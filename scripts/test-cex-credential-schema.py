#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CEX 凭据字段一致性（v1.8.1）。

起因（用户 2026-09-30 实测反馈）：**OKX 是三样凭据**
（密码短语 passphrase + API Key + Secret Key），而 Binance / Gate / MEXC 只有两样。
设置面板以前把 passphrase 无条件常驻显示，而且文案写着"仅 OKX/Gate 需要"
——**Gate 那半句是错的**：cex_keystore.py:189 的真实规则是**只有 OKX** 强制要求。

修完之后，"哪些交易所要 passphrase"这条规则有**两份拷贝**：
  ① cex_keystore.py（保存时强制校验，fail-closed）
  ② index.mjs 的 CEX_CREDENTIALS（下发给设置面板渲染表单）
它们会漂移。本测试就是防漂移的那道闸：**任一处改了而另一处没改，这里会红。**

同时钉死"表单按交易所条件渲染"这件事——因为它最初的形态就是
无条件常驻显示，而那正是用户报的问题。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
KEYSTORE = os.path.join(ROOT, 'preset', 'kexi-crypto', 'skills',
                        'crypto-market-analysis', 'scripts', 'cex_keystore.py')
INDEX = os.path.join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'lib', 'index.mjs')
CLIENT = os.path.join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'lib', 'client.js')

SCRIPTS = os.path.dirname(KEYSTORE)
checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


for p in (KEYSTORE, INDEX, CLIENT):
    if not os.path.exists(p):
        print('缺少文件: %s' % p)
        sys.exit(1)

ks = open(KEYSTORE, encoding='utf-8').read()
idx = open(INDEX, encoding='utf-8').read()
cl = open(CLIENT, encoding='utf-8').read()

# ══════════════ ① Python 侧：把真实规则抠出来 ══════════════
sup = re.search(r'SUPPORTED_EXCHANGES\s*=\s*\(([^)]*)\)', ks)
ck('① cex_keystore 有 SUPPORTED_EXCHANGES', sup is not None, '')
ex_list = re.findall(r'"([a-z0-9]+)"', sup.group(1)) if sup else []
ck('① 覆盖四家交易所', sorted(ex_list) == ['binance', 'gate', 'mexc', 'okx'], str(ex_list))

# 真正的强制规则形如： if ex == "okx" and not passphrase:
forced = re.findall(r'if ex == "([a-z0-9]+)" and not passphrase', ks)
ck('① Python 侧强制要 passphrase 的交易所恰好只有 okx',
   forced == ['okx'], '实际强制: %s' % forced)

# ══════════════ ② host 侧 CEX_CREDENTIALS 与之一致 ══════════════
block = re.search(r'const CEX_CREDENTIALS = \{(.*?)\n  \}', idx, re.S)
ck('② index.mjs 有 CEX_CREDENTIALS 常量', block is not None, '')
if block:
    b = block.group(1)
    schema = {}
    for m in re.finditer(r'(\w+):\s*\{([^}]*)\}', b):
        name, body = m.group(1), m.group(2)
        schema[name] = {
            'pp': bool(re.search(r'passphrase:\s*true', body)),
            'fields': re.findall(r"'([a-z_]+)'", body),
        }
    ck('② host schema 覆盖与 Python 相同的四家',
       sorted(schema) == sorted(ex_list), 'host=%s python=%s' % (sorted(schema), sorted(ex_list)))
    # **核心断言**：两份拷贝的 passphrase 规则必须完全一致
    host_pp = sorted([k for k, v in schema.items() if v['pp']])
    ck('② passphrase 规则 host 与 Python 完全一致（防漂移）',
       host_pp == sorted(forced), 'host 要 passphrase=%s，Python 要=%s' % (host_pp, forced))
    ck('② 明确 gate 不需要 passphrase（用户反馈点名的错误文案源头）',
       'gate' not in host_pp, 'host 竟把 gate 也标成需要：%s' % host_pp)
    for name in ex_list:
        f = schema.get(name, {}).get('fields', [])
        ck('② %s 的字段构成正确' % name,
           (f == ['api_key', 'api_secret', 'passphrase']) if name == 'okx'
           else (f == ['api_key', 'api_secret']), str(f))
    # 下发到 HTTP 响应里（不然客户端拿不到）
    ck('② schema 通过 /cex 下发（credential_schema 字段存在）',
       'credential_schema: CEX_CREDENTIALS' in idx, '')
    ck('② Python SUPPORTED_EXCHANGES 与 host registry 同源校验存在',
       'test-cex-credential-schema' in idx, 'host 未在注释里指向一致性测试')

# ══════════════ ③ 设置面板按交易所条件渲染 ══════════════
ck('③ 客户端读 credential_schema 决定是否显示密码短语',
   'credential_schema' in cl, '')
ck('③ passphrase 行是条件渲染而不是常驻',
   re.search(r'\?\s*cexRow\("密码短语', cl) is not None, '')
ck('③ 非 OKX 时给出"只需两样"的显式说明', '不需要密码短语' in cl, '')
ck('③ schema 未就绪时不谎报（宁可空着）',
   re.search(r'if\s*False:\s*null|:\s*null\)\)\s*,?\s*$', cl, re.M) is not None
   or ': null)' in cl, '')
# 旧错误文案必须已经消失
ck('③ 旧错误文案「仅 OKX/Gate 需要」已消失',
   'OKX/Gate' not in cl and 'OKX/Gate 必填' not in cl, '')
ck('③ 「附加口令」这一含糊叫法已换成「密码短语」',
   '附加口令（OKX' not in cl, '')

# ══════════════ ④ 提交前拦一道（否则存得进去、到 Python 才炸） ══════════════
ck('④ 有凭据完整性自检函数', '_cxMissing' in cl, '')
ck('④ 界面明写缺哪一样', '缺 ' in cl and 'missing.join' in cl, '')
ck('④ 说明"留空=不改动"，不因此误报已配置的账户',
   '留空 = 不改动' in cl or '留空表示不改动' in cl, '')
# ⚠ 告警措辞不得写死"三样"——那会让只需两样的 Binance/Gate/MEXC 用户
#   以为自己还漏了一项。实测踩过：Gate 的告警写着"要三样凭据齐全"。
ck('④ 告警措辞里的样数跟着 schema 走，不写死',
   ('.fields || []).length' in cl), '')
# ⚠ 全文件匹配而不是取字符窗口——窗口定位对缩进/换行极其敏感，
#   实测注入「三样凭据齐全」后 26/26 全绿假通过过一次。
#   注释里只出现「三样」二字（如"写死三样"），不会命中「三样凭据齐全」，
#   所以全文件匹配既精确又不会误报。
ck('④ 告警里不再写死"三样凭据齐全"',
   '三样凭据齐全' not in cl,
   '命中: ' + cl[cl.find('三样凭据齐全') - 40:cl.find('三样凭据齐全') + 20]
   if '三样凭据齐全' in cl else '')

# ══════════════ ⑤ 客户端用值变量而非 state 数组（实测踩过）══════════════
# cxs = useState(null) → cxs 是 [值, setter] **数组**；值是 cexInfo = cxs[0]。
# 写成 cxs.credential_schema 恒为 undefined：字段条件渲染静默失效、
# 完整性告警永不触发，且 node --check 与任何静态检查都抓不到——
# 只有真浏览器渲染才暴露。
ck('⑤ 取 schema 用 cexInfo（值）而不是 cxs（state 数组）',
   'cxs.credential_schema' not in cl, '')
ck('⑤ _cxMissing 的第二参是 cexInfo',
   '_cxMissing(cxForm, cxs)' not in cl, '')

# ══════════════ ⑥ OKX 现货持仓接口口径（v1.9.3）══════════════════════════
# 实测踩到的：OKX 的 /api/v5/account/positions **没有 SPOT 这个 instType**
# （合法值只有 SWAP/FUTURES/OPTION/MARGIN），传 SPOT 直接
# `HTTP 400 {"code":"51000","msg":"Parameter instType error"}`。
# 现货持仓必须从 /account/balance 推导。
_adapter = open(os.path.join(SCRIPTS, 'cex_adapter.py'), encoding='utf-8').read()
ck('⑥ OKX positions 不再把 SPOT 当 instType 传（会 400）',
   not re.search(r'"spot":\s*"SPOT"', _adapter), '')
# ⚠ 只查 "没有 SPOT instType" **太窄**：把 spot 分支整段删掉后，dict 里查不到
#   "spot"，断言照样通过，而代码会把 spot 悄悄当成 SWAP 去查合约持仓
#   （不报错了，但返回的是**错的持仓**——更隐蔽）。
#   所以必须正面钉住「spot 走余额推导」这条路径本身。
ck('⑥ OKX get_positions 里 spot 明确分流到余额推导',
   re.search(r'if\s+market\s*==\s*["\']spot["\']\s*:\s*\n?\s*return\s+self\._spot_positions_from_balance\(',
             _adapter) is not None,
   'spot 必须 return self._spot_positions_from_balance(...)')
ck('⑥ OKX 现货持仓改由余额推导', '_spot_positions_from_balance' in _adapter, '')
ck('⑥ 现货 mark_price 是**单价**（eqUsd ÷ 数量），不是等值美元',
   re.search(r'mark\s*=\s*\(usd\s*/\s*total\)', _adapter) is not None,
   '否则下游 size × mark 会算出约 1000 倍的假名义额（实测 81.63 → 85444）')
ck('⑥ 现货成本价置 None 而不是编一个', re.search(r'None,\s*#\s*avgPx', _adapter) is not None, '')
# ══════════════ 汇总 ══════════════
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-cex-credential-schema: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)

# ══════════════ ⑥ OKX 现货持仓接口口径（v1.9.3）══════════════════════════
# 实测踩到的：OKX 的 /api/v5/account/positions **没有 SPOT 这个 instType**
# （合法值只有 SWAP/FUTURES/OPTION/MARGIN），传 SPOT 直接
# `HTTP 400 {"code":"51000","msg":"Parameter instType error"}`。
# 现货持仓必须从 /account/balance 推导。
ca = open(os.path.join(SCRIPTS, 'cex_keystore.py'), encoding='utf-8').read()
_adapter = open(os.path.join(os.path.dirname(KEYSTORE), 'cex_adapter.py'), encoding='utf-8').read()
