#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""regime_log.py 回归（v1.7.0）：regime 判定的对账台账。

对账工具本身是最容易自欺的一类代码——它能"证明"任何结论，
只要口径是看完结果再挑的。所以本测试的重点不是"能不能算命中率"，
而是**口径是否先定后看、坏数据是否被如实标记、前视偏差是否被排除**。

全程离线：用合成 K 线构造"后来实际怎么走了"，直接检验判定判对没判对。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts')
RLOG = os.path.join(SCRIPTS, 'regime_log.py')
PY = os.environ.get('KEXI_PYTHON') or 'python'

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location('regime_log', RLOG)
rl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl)

DAY = 86_400_000
T0 = 1_700_000_000_000


def klines_from_returns(rets, start_ts=T0):
    """把一串日收益率变成 K 线（收盘价序列）。"""
    px, out = 100.0, []
    for i, r in enumerate(rets):
        px *= (1 + r)
        out.append([start_ts + i * DAY, px, px * 1.01, px * 0.99, px, 1000.0])
    return out


# ══════════════ ① 对账口径：先定后看，且写进输出 ══════════════
ck('① 匹配口径是显式常量表（不是散落在代码里）',
   isinstance(rl.MATCH, dict) and set(rl.MATCH) == {'上行期', '下行期', '横盘期'}, str(rl.MATCH))
ck('① 上下行/横盘三种口径互补而非重叠',
   rl._match('上行期', 0.05, 0.03) is True and rl._match('下行期', 0.05, 0.03) is False
   and rl._match('横盘期', 0.01, 0.03) is True and rl._match('横盘期', 0.05, 0.03) is False, '')
ck('① 无实际收益时不判对错（返回 None 而非 False）',
   rl._match('上行期', None, 0.03) is None, str(rl._match('上行期', None, 0.03)))
ck('① 最小样本数存在（防止 1 条记录就报 100%）', rl.MIN_SAMPLE >= 3, str(rl.MIN_SAMPLE))

# ══════════════ ② 前视偏差：起点必须取 at_ts 之后那根 ══════════════
# _forward_return 的口径是 **close[T] → close[T+h]**（收盘价对收盘价）。
# 夹具 klines_from_returns 里 close[i] 已含第 i 根的涨跌，所以"那次暴涨"
# 要用 returns 数组里**下一位**的位置来表达，否则根本测不到想测的东西。
# 这里：0.10 放在第 3 根 → close[3] 相对 close[2] 跳了 10%。
kl = klines_from_returns([0.0, 0.0, 0.10] + [0.0] * 8)
r_across = rl._forward_return(kl, T0, 3)          # 起点 index0 → 跨 3 天 → 应吃到那 10%
ck('② at_ts 在暴涨之前时，窗口内应吃到那次涨幅',
   r_across is not None and abs(r_across - 0.10) < 1e-6, str(r_across))
r_after = rl._forward_return(kl, T0 + 4 * DAY, 1)  # 起点在暴涨之后
ck('② at_ts 在暴涨之后时，不该把那 10% 再算一次（无前视偏差）',
   r_after is not None and abs(r_after) < 1e-9, str(r_after))
ck('② 窗口不足时返回 None（不用不足的窗口凑一个数）',
   rl._forward_return(klines_from_returns([0.0] * 3), T0, 7) is None, '')
ck('② 完全无未来数据时返回 None', rl._forward_return([], T0, 7) is None, '')

# ══════════════ ③ record：只记当时说的，且残缺不记 ══════════════
tmp = tempfile.mkdtemp(prefix='kexi-rlog-')
log = os.path.join(tmp, 'log.jsonl')
rg_ok = os.path.join(tmp, 'regime.json')
with open(rg_ok, 'w', encoding='utf-8') as f:
    json.dump({"regime": "上行期", "score": 0.86, "confidence": 0.7, "coverage": 0.7,
               "degraded": ["位置分位"],
               "criteria": [{"label": "趋势强度", "value": "共振向上", "available": True}],
               "invalidation": ["跌破 MA50"],
               "data_quality": {"price": 81234.5}}, f, ensure_ascii=False)
r = subprocess.run([PY, RLOG, 'record', '--regime', rg_ok, '--horizon', '7', '--out', log],
                   capture_output=True, text=True, timeout=60)
ck('③ record 成功', r.returncode == 0, r.stderr[-200:])
ent = json.loads(open(log, encoding='utf-8').readline())
ck('③ 留痕含判定与打分', ent['regime'] == '上行期' and abs(ent['score'] - 0.86) < 1e-9, '')
ck('③ 留痕含当时的价格（对账起点）', ent['close'] == 81234.5, str(ent.get('close')))
ck('③ 留痕初始为 pending（record 时刻还不知道结果）', ent['status'] == 'pending', '')
ck('③ 留痕含失效条件（供事后核对判据是否被触发）',
   len(ent['invalidation']) >= 1, str(ent.get('invalidation')))
# 残缺输入不得留痕
bad = os.path.join(tmp, 'bad.json')
with open(bad, 'w', encoding='utf-8') as f:
    json.dump({"regime": None}, f)
log2 = os.path.join(tmp, 'log2.jsonl')
r2 = subprocess.run([PY, RLOG, 'record', '--regime', bad, '--out', log2],
                    capture_output=True, text=True, timeout=60)
ck('③ 缺 regime/score 时拒绝留痕（不记残缺行污染对账）',
   r2.returncode != 0 and not os.path.exists(log2), r2.stdout[:120])

# ══════════════ ④ reconcile：判定对错 ══════════════
def build_case(tag, past_returns, future_returns, regime, band=0.03):
    """写一份台账（用**过去**的时间戳）+ 一份**未来**K 线。"""
    lg = os.path.join(tmp, 'c_%s.jsonl' % tag)
    # 时间戳设在 10 根之前，其后接 future
    allk = klines_from_returns(past_returns + future_returns)
    at = T0 + len(past_returns) * DAY
    with open(lg, 'w', encoding='utf-8') as f:
        f.write(json.dumps({"schema": rl.LOG_SCHEMA,
                            "ts_ms": at, "as_of": "t", "regime": regime,
                            "score": 0.8, "confidence": 0.7, "coverage": 0.7,
                            "close": 100.0, "criteria": [], "degraded": [],
                            "invalidation": [], "horizon_days": 7,
                            "status": "pending"}, ensure_ascii=False) + "\n")
    kp = os.path.join(tmp, 'k_%s.json' % tag)
    with open(kp, 'w', encoding='utf-8') as f:
        json.dump(allk, f)
    outp = os.path.join(tmp, 'o_%s.json' % tag)
    rr = subprocess.run([PY, RLOG, 'reconcile', '--log', lg, '--band', str(band),
                         '--btc-klines', kp, '--out', outp],
                        capture_output=True, text=True, timeout=60)
    doc = json.load(open(outp, encoding='utf-8')) if os.path.exists(outp) else {}
    return doc, rr


doc, _ = build_case('up_hit', [0.0] * 10, [0.05] * 8, '上行期')
ck('④ 判上行期 + 后来真涨 → hit=true',
   doc.get('items', [{}])[0].get('hit') is True, str(doc.get('items'))[:160])
doc, _ = build_case('up_miss', [0.0] * 10, [-0.05] * 8, '上行期')
ck('④ 判上行期 + 后来跌了 → hit=false（错判要被如实记下）',
   doc.get('items', [{}])[0].get('hit') is False, str(doc.get('items'))[:160])
doc, _ = build_case('dn_hit', [0.0] * 10, [-0.06] * 8, '下行期')
ck('④ 判下行期 + 后来真跌 → hit=true', doc.get('items', [{}])[0].get('hit') is True, '')
doc, _ = build_case('flat_hit', [0.0] * 10, [0.001] * 8, '横盘期')
ck('④ 判横盘期 + 后来窄幅波动 → hit=true', doc.get('items', [{}])[0].get('hit') is True, '')

# 样本不足时不给命中率结论
ck('④ 单条记录时明说「样本不足」而非 0% 或 100%',
   '样本不足' in (doc.get('summary_by_regime', {}).get('横盘期', {}).get('verdict') or ''),
   str(doc.get('summary_by_regime')))

# ══════════════ ⑤ 缺数据必须如实标 no_data，不得算成"错" ══════════════
lg = os.path.join(tmp, 'c_nodata.jsonl')
at = T0 + 10 * DAY
with open(lg, 'w', encoding='utf-8') as f:
    f.write(json.dumps({"schema": rl.LOG_SCHEMA, "ts_ms": at, "as_of": "t",
                        "regime": "上行期", "score": 0.8, "confidence": 0.7,
                        "coverage": 0.7, "close": 100.0, "criteria": [],
                        "degraded": [], "invalidation": [], "horizon_days": 7,
                        "status": "pending"}, ensure_ascii=False) + "\n")
outp = os.path.join(tmp, 'o_nodata.json')
rr = subprocess.run([PY, RLOG, 'reconcile', '--log', lg, '--out', outp],
                    capture_output=True, text=True, timeout=60)
doc = json.load(open(outp, encoding='utf-8'))
ck('⑤ 无 K 线数据时标 no_data 而不是判错',
   doc['items'][0].get('status') == 'no_data' and doc['items'][0].get('hit') is None,
   str(doc['items'])[:140])
ck('⑤ no_data 不计入命中率', doc['scored'] == 0, str(doc.get('scored')))

# ══════════════ ⑥ 口径写进输出（供人复核，不藏在代码里） ══════════════
ck('⑥ 输出含完整匹配口径', isinstance(doc.get('match_rule'), dict)
   and '上行期' in doc['match_rule'], str(doc.get('match_rule')))
ck('⑥ 输出明说「口径先定后看」', '先定后看' in (doc.get('match_note') or ''), '')

# ══════════════ ⑦ 待定记录不得被提前评分 ══════════════
import time  # noqa: E402
lg3 = os.path.join(tmp, 'c_pend.jsonl')
future_ts = int(time.time() * 1000) + 40 * DAY
with open(lg3, 'w', encoding='utf-8') as f:
    f.write(json.dumps({"schema": rl.LOG_SCHEMA, "ts_ms": future_ts, "as_of": "未来",
                        "regime": "上行期", "score": 0.8, "confidence": 0.7,
                        "coverage": 0.7, "close": 100.0, "criteria": [],
                        "degraded": [], "invalidation": [], "horizon_days": 7,
                        "status": "pending"}, ensure_ascii=False) + "\n")
kp3 = os.path.join(tmp, 'k_pend.json')
with open(kp3, 'w', encoding='utf-8') as f:
    json.dump(klines_from_returns([0.05] * 30), f)
outp3 = os.path.join(tmp, 'o_pend.json')
subprocess.run([PY, RLOG, 'reconcile', '--log', lg3, '--btc-klines', kp3, '--out', outp3],
               capture_output=True, text=True, timeout=60)
doc3 = json.load(open(outp3, encoding='utf-8'))
ck('⑦ 到期前的记录保持 pending（不许提前评分）',
   doc3['items'][0].get('status') == 'pending' and doc3['pending'] == 1,
   str(doc3['items'])[:140])

# ══════════════ ⑧ 坏行不让整份台账作废 ══════════════
lg4 = os.path.join(tmp, 'c_bad.jsonl')
with open(lg4, 'w', encoding='utf-8') as f:
    f.write(json.dumps({"ts_ms": at, "regime": "上行期", "score": 0.8, "horizon_days": 7},
                       ensure_ascii=False) + "\n")
    f.write("{ 这不是 JSON\n")
    f.write(json.dumps({"ts_ms": at, "regime": "下行期", "score": -0.7, "horizon_days": 7},
                       ensure_ascii=False) + "\n")
outp4 = os.path.join(tmp, 'o_bad.json')
rr4 = subprocess.run([PY, RLOG, 'reconcile', '--log', lg4, '--out', outp4],
                     capture_output=True, text=True, timeout=60)
ck('⑧ 台账里有坏行时仍能处理其余记录',
   rr4.returncode == 0 and json.load(open(outp4, encoding='utf-8'))['total'] == 2,
   rr4.stderr[-160:])

# ══════════════ 汇总 ══════════════
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-regime-log: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
