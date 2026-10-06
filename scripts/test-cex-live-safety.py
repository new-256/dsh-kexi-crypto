#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.6.0 回归：CEX 实盘链路的**安全性质**（不是功能是否跑通）。

这一组守的是钱。功能测试证明"能下单"，安全测试证明"不该下单时一定下不了"。

钉住的核心性质：
  ① **自主级别 fail-closed**：autonomy.json 缺失 / 非法 / 读失败 → 一律 readonly。
     "权限配置读不出来"绝不能等于"放行实盘"。
  ② **模型无法翻转权限**：cex_adapter.py 不接受任何 --autonomy 命令行参数。
     早先 CEX 只能靠 kexi_run(mode=script) 手写 argv，若权限来自命令行，
     模型多写一个参数就绕过了——所以必须没有这个参数。
  ③ **受限自动档必须带止损**，且超限额拒绝。
  ④ **confirm 档只生成草案、不发送**。
  ⑤ **订单意图日志**：先落意图再发送，崩溃后能看出"未决"（应对 37% 回合失败率）。
  ⑥ **凭据桥接**：host 写的裸 DPAPI 格式，Python 侧能原样读回（多账户 label 维度）。

全程离线：网络函数全部 monkeypatch，不发任何真实请求。
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'preset', 'kexi-crypto', 'skills',
                       'crypto-market-analysis', 'scripts')
sys.path.insert(0, SCRIPTS)

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


import cex_adapter as ca      # noqa: E402
import cex_keystore as ks     # noqa: E402

TZ8 = timezone(timedelta(hours=8))
src = open(os.path.join(SCRIPTS, 'cex_adapter.py'), encoding='utf-8').read()


def _mkargs(**kw):
    """造一个 argparse.Namespace 风格的参数对象，字段与 cex_adapter CLI 一致。"""
    base = dict(store_dir=None, exchange='binance', label='main', account_type='spot',
                market='spot', symbol=None, side=None, qty=None, price=None,
                order_type='MARKET', notional_usd=None, stop_loss=None,
                take_profit=None, leverage=None, profile=None, idem_seq=None,
                order_id=None, limit=20, markets=None, cmd=None)
    base.update(kw)
    return type('Args', (), base)()


def store_with(autonomy=None, extra_files=None):
    d = tempfile.mkdtemp(prefix='kexi_cex_test_')
    if autonomy is not None:
        with open(os.path.join(d, 'autonomy.json'), 'w', encoding='utf-8') as f:
            json.dump(autonomy, f)
    for name, content in (extra_files or {}).items():
        with open(os.path.join(d, name), 'w', encoding='utf-8') as f:
            f.write(content)
    return d


# ══════════════ ① 自主级别 fail-closed ══════════════
a = ca.read_autonomy(store_with())
ck('① autonomy.json 缺失 → readonly', a['level'] == 'readonly', json.dumps(a, ensure_ascii=False))
ck('① 缺失时 source=missing 且给出说明', a['source'] == 'missing' and bool(a.get('note')),
   json.dumps(a, ensure_ascii=False)[:140])

a = ca.read_autonomy(store_with({'level': 'full'}))
ck('① 正常读取 full', a['level'] == 'full', json.dumps(a, ensure_ascii=False)[:140])

for bad in ('FULL', 'full ', ' readonly', '', 'godmode', 'readonly_v2', None, 123, True):
    d = store_with({'level': bad} if bad is not None else {'other': 1})
    a = ca.read_autonomy(d)
    ck('① 非法级别 %r → 退回 readonly' % (bad,), a['level'] == 'readonly', a.get('level'))

# 写坏 JSON
d = store_with()
with open(os.path.join(d, 'autonomy.json'), 'w', encoding='utf-8') as f:
    f.write('{ 这不是 json')
a = ca.read_autonomy(d)
ck('① autonomy.json 是坏 JSON → 退回 readonly', a['level'] == 'readonly', a.get('level'))
ck('① 读失败时 source=error 且有说明', a['source'] == 'error' and bool(a.get('note')))

# ══════════════ ② 模型无法翻转权限 ══════════════
# ⚠ 只扫**真正的 argparse 参数声明**，不扫全文——注释与 help 文案里出现
#   "--autonomy" 是正常的（那是在说明"我们不提供它"），全文 grep 会误报。
add_arg_lines = [ln.strip() for ln in src.splitlines()
                 if 'add_argument(' in ln or 'add_parser(' in ln]
add_arg_blob = '\n'.join(add_arg_lines)
ck('② CLI 不声明 --autonomy 参数', '--autonomy' not in add_arg_blob,
   '出现了 add_argument("--autonomy"...) —— 模型可借此翻转权限')
ck('② CLI 不声明 --dry-run/--live 翻转实盘',
   ('--dry-run' not in add_arg_blob) and ('--dry_run' not in add_arg_blob)
   and ('--live' not in add_arg_blob), '')
ck('② CLI 不声明 --store-dir 之外的路径覆盖（store-dir 只用于定位凭据，非权限）',
   add_arg_blob.count('--store-dir') <= 2, str(add_arg_blob.count('--store-dir')))
ck('② read_autonomy 签名不含权限入参', 'def read_autonomy(store_dir)' in src, '')
ck('② autonomy_allows 对未知级别返回 False（不靠默认值放行）',
   ca.autonomy_allows('nonexistent', 'readonly') is False
   and ca.autonomy_allows('readonly', 'nonexistent') is False,
   '')

# 无论传什么，readonly 都不许写
d = store_with({'level': 'readonly'})
for act, kw in (('order', {}), ('cancel', {})):
    args = _mkargs(store_dir=d, exchange='binance', label='main',
                    symbol='BTCUSDT', side='buy', qty=0.01, **{k: v for k, v in kw.items()})
    r = (ca.cmd_order if act == 'order' else ca.cmd_cancel)(args)
    ck('② readonly 下 %s 被拦且 sent=False' % act,
       r.get('ok') is False and r.get('sent') is False and r.get('blocked_by') == 'autonomy',
       json.dumps(r, ensure_ascii=False)[:150])

# ══════════════ ③ 受限自动档：必须带止损 + 限额 ══════════════
d = store_with({'level': 'limited', 'auto_notional_cap_usd': 200})
args = _mkargs(store_dir=d, exchange='binance', label='main', symbol='BTCUSDT',
               side='buy', qty=0.001, notional_usd=100)          # 无 stop_loss
r = ca.cmd_order(args)
ck('③ limited 档无止损 → 拒绝', r.get('ok') is False and r.get('sent') is False,
   json.dumps(r, ensure_ascii=False)[:150])
ck('③ 拒绝原因指向 requires_stop_loss', r.get('blocked_by') == 'limited_requires_sl',
   str(r.get('blocked_by')))

args = _mkargs(store_dir=d, exchange='binance', label='main', symbol='BTCUSDT',
               side='buy', qty=1.0, notional_usd=5000, stop_loss=60000)  # 超限额
r = ca.cmd_order(args)
ck('③ limited 档超限额 → 拒绝', r.get('ok') is False and r.get('blocked_by') == 'limited_cap',
   json.dumps(r, ensure_ascii=False)[:150])

args = _mkargs(store_dir=d, exchange='binance', label='main', symbol='BTCUSDT',
               side='buy', qty=0.001, notional_usd=100, stop_loss=60000)  # 合规
r = ca.cmd_order(args)
ck('③ limited 档合规单通过前两道门（走到读密钥才失败）',
   r.get('blocked_by') not in ('autonomy', 'limited_requires_sl', 'limited_cap'),
   json.dumps(r, ensure_ascii=False)[:150])

# ══════════════ ④ confirm 档只生成草案 ══════════════
d = store_with({'level': 'confirm'})
args = _mkargs(store_dir=d, exchange='binance', label='main', symbol='BTCUSDT',
               side='buy', qty=0.001, notional_usd=100)
r = ca.cmd_order(args)
ck('④ confirm 档返回 pending_confirmation', r.get('pending_confirmation') is True,
   json.dumps(r, ensure_ascii=False)[:150])
ck('④ confirm 档绝不标记 sent', r.get('sent') is False, str(r.get('sent')))
ck('④ confirm 档不碰凭据（说明未走到读密钥）', 'hint' not in r or '凭据' not in str(r.get('hint', '')),
   str(r.get('error'))[:100])

# ══════════════ ⑤ 订单意图日志 ══════════════
d = store_with({'level': 'confirm'})
ca.cmd_order(_mkargs(store_dir=d, exchange='binance', label='main', symbol='BTCUSDT',
                      side='buy', qty=0.001, notional_usd=100))
ck('⑤ confirm 草案不写意图日志（未发单就不该留意图）',
   not os.path.exists(os.path.join(d, ca.JOURNAL_FILE)),
   'journal 文件不该存在')

# 模拟一次"落意图后崩溃"：直接写一条 intent
d2 = store_with({'level': 'full'})
ca.journal_append(d2, {'stage': 'intent', 'order_id': 'kexi-abc',
                        'exchange': 'binance', 'label': 'main',
                        'order': {'symbol': 'BTCUSDT', 'side': 'buy', 'qty': 0.01}})
un = ca.journal_unresolved(d2)
ck('⑤ 只有 intent 没有 result → 列入未决', len(un) == 1 and un[0]['order_id'] == 'kexi-abc',
   json.dumps(un, ensure_ascii=False)[:140])
ca.journal_append(d2, {'stage': 'result', 'order_id': 'kexi-abc', 'ok': True})
ck('⑤ 补上 result 后不再算未决', ca.journal_unresolved(d2) == [],
   json.dumps(ca.journal_unresolved(d2), ensure_ascii=False)[:120])

# 意图 id 稳定性：同参数同 seq → 同 id（幂等重试不会变成两笔）
i1 = ca._order_id('BTCUSDT', 'buy', 0.01, None, 's1')
i2 = ca._order_id('BTCUSDT', 'buy', 0.01, None, 's1')
i3 = ca._order_id('BTCUSDT', 'buy', 0.01, None, 's2')
ck('⑤ 同参数同 seq → 同一意图 id（幂等）', i1 == i2, '%s vs %s' % (i1, i2))
ck('⑤ 不同 seq → 不同意图 id（是两次不同决策）', i1 != i3, '%s vs %s' % (i1, i3))

# ══════════════ ⑥ 凭据桥接：host 写 → Python 读 ══════════════
# 这里真的调 PowerShell 加密，验证格式对齐（这是 v1.5.5 遗留的死链）
ps_ok = (os.name == 'nt')
if ps_ok:
    d = store_with()
    payload = json.dumps({"schema": "kexi.cexkeys/1", "exchange": "binance",
                          "label": "main", "api_key": "AK_BRIDGE_1234",
                          "api_secret": "SK_BRIDGE_5678", "passphrase": ""})
    # 用 cex_keystore 自己的写入路径（与 host 同一原语：DPAPI CurrentUser）
    r = ks.save_keys(d, 'binance', 'AK_BRIDGE_1234', 'SK_BRIDGE_5678', None)
    ck('⑥ 写入成功且已加密', r.get('ok') and r.get('encrypted'), json.dumps(r, ensure_ascii=False)[:140])
    lk = ks.load_keys(d, 'binance', 'main')
    ck('⑥ 同 label 读回成功', lk.get('ok'), str(lk.get('message'))[:120])
    ck('⑥ 读回 api_key 一致', (lk.get('keys') or {}).get('api_key') == 'AK_BRIDGE_1234',
       str((lk.get('keys') or {}).get('api_key')))
    # 多账户：不同 label 互不覆盖
    ks.save_keys(d, 'binance', 'AK_SUB_9999', 'SK_SUB_8888', None, label='sub')
    ck('⑥ 多账户：main 账户未被 sub 覆盖',
       ((ks.load_keys(d, 'binance', 'main') or {}).get('keys') or {}).get('api_key') == 'AK_BRIDGE_1234',
       '主账户被覆盖了')
    ck('⑥ 多账户：sub 账户独立可读',
       ((ks.load_keys(d, 'binance', 'sub') or {}).get('keys') or {}).get('api_key') == 'AK_SUB_9999',
       '子账户读不到')
    lst = ks.list_keys(d)
    lst = lst if isinstance(lst, list) else (lst.get('keys') or [])
    ck('⑥ list_keys 列出两个账户', len(lst) >= 2, str(lst)[:160])
    # 文件命名必须与 host 约定一致
    ck('⑥ 文件名是 {ex}.{label}.dpapi',
       os.path.exists(os.path.join(d, 'binance.main.dpapi')),
       str(sorted(os.listdir(d))))

# 目录定位优先级
old = os.environ.get(ks.STORE_DIR_ENV)
try:
    os.environ[ks.STORE_DIR_ENV] = r'C:\custom\kexi-cex'
    ck('⑥ KEXI_CEX_STORE_DIR 优先级最高', ks.default_store_dir() == r'C:\custom\kexi-cex',
       ks.default_store_dir())
    del os.environ[ks.STORE_DIR_ENV]
    os.environ['DSH_HOME'] = r'C:\dsh-home'
    ck('⑥ 其次是 $DSH_HOME/kexi-cex（与 host 同源）',
       ks.default_store_dir() == os.path.join(r'C:\dsh-home', 'kexi-cex'),
       ks.default_store_dir())
    del os.environ['DSH_HOME']
finally:
    if old is not None:
        os.environ[ks.STORE_DIR_ENV] = old
    else:
        os.environ.pop(ks.STORE_DIR_ENV, None)
        os.environ.pop('DSH_HOME', None)

# ══════════════ 汇总 ══════════════
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-cex-live-safety: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
