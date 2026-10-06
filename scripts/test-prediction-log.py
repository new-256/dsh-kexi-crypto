#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.5.9 回归：决策留痕与事后对账（prediction_log.py）。

补的是 HANDOFF §12.4 #2 / §12.5 的空缺：此前 backtest.py 只算历史策略表现、
track_report.py 只复盘已成交台账，**两者都没和"当时到底说了什么"串起来**，
于是项目只能证明"脚本跑得动"，证明不了结论准不准。

本测试钉住最容易被做假的三处：
  1) 窗口没走完 / 窗口内无 K 线 → **必须排除**，不得计入命中率
     （把"还没发生"当"没命中"是这类对账最常见的自欺）；
  2) 实际落档必须按**区间重叠长度**归因，跑出包络外要显式标 envelope_miss；
  3) 小样本分组必须带 low_sample 标记，不许 2 条样本的 100% 冒充胜率。

全程离线：不联网、不写工作区（append_log 写临时目录）。
"""
import json
import os
import sys
import tempfile

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                       'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts')
sys.path.insert(0, SCRIPTS)
import prediction_log as pl   # noqa: E402

checks = []
DAY = 86400000
T0 = 1759000000000 // DAY * DAY      # 对齐到 UTC 整日，供 assess 的日线过滤用
NOW = T0 + 30 * DAY                  # 模拟 30 天后


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


def mk_klines(start_day, closes, lows=None, highs=None):
    """构造日线 [openTime, open, high, low, close, volume]。"""
    out = []
    n = len(closes)
    lows = lows or [c * 0.98 for c in closes]
    highs = highs or [c * 1.02 for c in closes]
    for i, c in enumerate(closes):
        out.append([start_day + i * DAY, c, highs[i], lows[i], c, 1e6])
    return out


def mk_entry(**over):
    e = {
        "schema": pl.LOG_SCHEMA, "symbol": "BTCUSDT", "interval": "1d",
        "as_of": "2026-09-30T00:00:00+08:00", "ts_ms": T0, "batch_id": "b1",
        "close": 100.0, "horizon_days": 7,
        "rating": "可关注", "data_usable": True, "entry_now": True,
        "confidence": "高", "confidence_score": 0, "data_source": "binance", "bars": 300,
        "scenarios": {
            "基准": {"p": 0.5, "range": [95.0, 108.0]},
            "乐观": {"p": 0.3, "range": [108.0, 115.0]},
            "悲观": {"p": 0.2, "range": [88.0, 95.0]},
        },
        "entry_actions": [{"type": "enter_now", "label": "立即入场", "price": 100.0}],
        "chase_flagged": False, "invalidation": 88.0, "stop_tactical": 95.0,
    }
    e.update(over)
    return e


# ═══════════ 一、留痕：只记"当时说了什么"，缺关键字段就不记 ═══════════
row = {"symbol": "BTCUSDT", "interval": "1d", "close": 100.0, "bars": 300,
       "data_source": "binance",
       "verdict": {"rating": "可关注", "data_usable": True, "entry_now": True,
                   "confidence": "高"},
       "confidence": {"level": "高", "score": 0, "penalties": []},
       "scenarios": {"基准": {"p": 0.5, "range": [95.0, 108.0]},
                     "乐观": {"p": 0.3, "range": [108.0, 115.0]},
                     "悲观": {"p": 0.2, "range": [88.0, 95.0]}},
       "entry_plan": {"chase_risk": {"flagged": False},
                      "actions": [{"type": "enter_now", "label": "立即入场", "price": 100.0}],
                      "invalidation": {"price": 88.0}},
       "stop_plan": {"tactical": 95.0}}
s = pl.snapshot_of(row)
ck('快照提取出标的与现价', s and s['symbol'] == 'BTCUSDT' and s['close'] == 100.0)
ck('快照保留当时评级与置信度', s['rating'] == '可关注' and s['confidence'] == '高')
ck('快照保留三情景概率与区间', set(s['scenarios']) == {'基准', '乐观', '悲观'}
   and s['scenarios']['基准']['range'] == [95.0, 108.0])
ck('快照保留失效位与战术止损', s['invalidation'] == 88.0 and s['stop_tactical'] == 95.0)
ck('快照带 schema 与预测期', s['schema'] == pl.LOG_SCHEMA and s['horizon_days'] == pl.DEFAULT_HORIZON)
ck('缺三情景 → 不留痕（没有可证伪区间就没有可对账的预测）',
   pl.snapshot_of({k: v for k, v in row.items() if k != 'scenarios'}) is None)
ck('缺 close → 不留痕', pl.snapshot_of({k: v for k, v in row.items() if k != 'close'}) is None)
ck('close 非数值 → 不留痕', pl.snapshot_of(dict(row, close="一百")) is None)
ck('单档区间含非数值 → 只丢该档，其余照留', len(pl.snapshot_of(dict(row, scenarios={
    '基准': {'p': 0.5, 'range': [95.0, 'x']},
    '乐观': {'p': 0.3, 'range': [108.0, 115.0]},
    '悲观': {'p': 0.2, 'range': [88.0, 95.0]}}))['scenarios']) == 2)
ck('三档全部不可用 → 整条不留痕', pl.snapshot_of(
    dict(row, scenarios={'基准': {'p': 0.5, 'range': [95.0, 'x']}})) is None)

# ═══════════ 二、留痕：同日同标的不稀释样本 ═══════════
with tempfile.TemporaryDirectory() as td:
    lp = os.path.join(td, 'p.jsonl')
    a1, sk1 = pl.append_log(lp, [mk_entry()])
    a2, sk2 = pl.append_log(lp, [mk_entry()])
    ck('首次留痕写入 1 条', a1 == 1 and sk1 == 0, 'added=%s skipped=%s' % (a1, sk1))
    ck('同日同标的重复快照不重复写入', a2 == 0 and sk2 == 1, 'added=%s skipped=%s' % (a2, sk2))
    ck('台账只留 1 条', len(pl.read_log(lp)) == 1, str(len(pl.read_log(lp))))
    # 置信度更低（更保守）的一条应当替换当日记录（+1 小时仍属同一自然日）
    pl.append_log(lp, [mk_entry(confidence="低", confidence_score=4, ts_ms=T0 + 3600000)])
    got = pl.read_log(lp)
    ck('同日更保守的快照被采纳', len(got) == 1 and got[0]['confidence'] == '低', str(got))
    ck('台账文件本身只追加、不改写旧行（留痕不可回填）',
       len([x for x in open(lp, encoding='utf-8') if x.strip()]) == 2,
       str(len([x for x in open(lp, encoding='utf-8') if x.strip()])))
    # 坏行不得毁掉整个台账
    with open(lp, 'a', encoding='utf-8') as f:
        f.write('{ 这不是 json\n\n')
    ck('坏行被跳过、其余记录完好', len(pl.read_log(lp)) == 1, str(len(pl.read_log(lp))))

# ═══════════ 三、对账：窗口未走完必须 pending，绝不计入命中 ═══════════
kl = mk_klines(T0 + DAY, [101, 102, 103, 104, 105, 106, 107], lows=[99] * 7, highs=[108] * 7)
o = pl.assess(mk_entry(), kl, now_ms=T0 + 3 * DAY)
ck('窗口未走完 → pending', o['status'] == 'pending', str(o)[:100])
ck('pending 带剩余天数', o.get('days_left', 0) > 0, str(o.get('days_left')))
ck('pending 时不得给出任何命中率字段', 'brier' not in o and 'fwd_return_pct' not in o)

o = pl.assess(mk_entry(), [], now_ms=NOW)
ck('窗口内无 K 线 → no_data', o['status'] == 'no_data', str(o)[:100])
ck('no_data 必须说明原因', bool(o.get('why')), str(o.get('why')))
o = pl.assess(mk_entry(), mk_klines(T0 + DAY, [101, 102]), now_ms=NOW)
ck('窗口内 K 线过稀（<3 根）→ no_data', o['status'] == 'no_data', str(o)[:100])

# ═══════════ 四、对账：落档归因 / 守住 / 触发 ═══════════
# 实际全在基准档 [95,108] 内横盘
kl = mk_klines(T0 + DAY, [100] * 7, lows=[97] * 7, highs=[106] * 7)
o = pl.assess(mk_entry(), kl, now_ms=NOW)
ck('横盘 → 实际落在基准档', o['status'] == 'ok' and o['realized_scenario'] == '基准',
   str(o.get('realized_scenario')))
ck('基准档守住 = True', o['base_band_held'] is True)
ck('Brier = (0.5-1)^2+0.3^2+0.2^2 = 0.38',
   abs(o['brier'] - 0.38) < 1e-6, str(o.get('brier')))
ck('前瞻收益按窗口末根收盘计算', o['fwd_return_pct'] == 0.0, str(o.get('fwd_return_pct')))
ck('最大不利偏移为负数', o['max_dd_pct'] < 0, str(o.get('max_dd_pct')))
ck('止损 95 未被触发（最低 97）', o['stop_hit'] is False)
ck('失效位 88 未被触发', o['invalidation_hit'] is False)

# 冲进乐观档
kl = mk_klines(T0 + DAY, [110] * 7, lows=[108.5] * 7, highs=[114] * 7)
o = pl.assess(mk_entry(), kl, now_ms=NOW)
ck('上涨 → 实际落在乐观档', o['realized_scenario'] == '乐观', str(o.get('realized_scenario')))
ck('冲破乐观档后基准档不再算守住', o['base_band_held'] is False)
ck('乐观命中时 Brier = 0.5²+(0.3−1)²+0.2² = 0.78',
   abs(o['brier'] - 0.78) < 1e-6, str(o.get('brier')))

# 暴跌穿止损
kl = mk_klines(T0 + DAY, [90] * 7, lows=[86] * 7, highs=[92] * 7)
o = pl.assess(mk_entry(), kl, now_ms=NOW)
ck('暴跌 → 实际落在悲观档', o['realized_scenario'] == '悲观', str(o.get('realized_scenario')))
ck('跌破战术止损 → stop_hit', o['stop_hit'] is True)
ck('跌破失效位 → invalidation_hit', o['invalidation_hit'] is True)
ck('最大回撤显著为负', o['max_dd_pct'] <= -14, str(o.get('max_dd_pct')))

# 整段跑出包络外（远超乐观上沿 115）
kl = mk_klines(T0 + DAY, [200] * 7, lows=[190] * 7, highs=[210] * 7)
o = pl.assess(mk_entry(), kl, now_ms=NOW)
ck('跑出全部情景区间 → envelope_miss（不得硬塞进某一档）',
   o['realized_scenario'] == 'envelope_miss', str(o.get('realized_scenario')))
ck('envelope_miss 时 Brier 置空（无归因就不打分）', o['brier'] is None, str(o.get('brier')))

# ═══════════ 五、校准：pending/no_data 一律排除，小样本必须标记 ═══════════
outcomes = [
    pl.assess(mk_entry(), kl, now_ms=NOW),                       # ok / envelope_miss
    pl.assess(mk_entry(), [], now_ms=NOW),                       # no_data
    pl.assess(mk_entry(), kl, now_ms=T0 + DAY),                  # pending
    pl.assess(mk_entry(confidence="低", rating="回调观察（不追高）"),
              mk_klines(T0 + DAY, [100] * 7, lows=[97] * 7, highs=[106] * 7), now_ms=NOW),
    pl.assess(mk_entry(), mk_klines(T0 + DAY, [110] * 7, lows=[108.5] * 7, highs=[114] * 7),
              now_ms=NOW),
]
cal = pl.calibration(outcomes)
ck('总数正确', cal['total'] == 5, str(cal['total']))
ck('可对账数正确', cal['reconciled'] == 3, str(cal['reconciled']))
ck('pending 计数正确且被排除', cal['pending'] == 1 and cal['reconciled'] == 3)
ck('no_data 计数正确且被排除', cal['excluded_no_data'] == 1 and cal['reconciled'] == 3)
ck('排除口径有明文说明', 'pending' in cal['exclusion_note'] and 'no_data' in cal['exclusion_note'])
ck('Brier 参考基准同时给出最优与瞎猜', cal['brier_reference']['perfect'] == 0.0
   and cal['brier_reference']['uniform_guess'] == 0.667)
byc = {r['key']: r for r in cal['by_confidence']}
ck('按置信度分组存在', set(byc) >= {'高', '低'}, str(list(byc)))
ck('n<5 的分组必须标 low_sample', all(r['low_sample'] for r in cal['by_confidence']))
byr = {r['key']: r for r in cal['by_rating']}
ck('按评级分组存在', '回调观察（不追高）' in byr, str(list(byr)))
cke = {str(r['key']): r for r in cal['by_entry_now']}
ck('按是否给过立即入场分组存在', 'True' in cke, str(list(cke)))
ck('止损触发率只按可对账样本算', cal['stop_hit_rate_pct'] in (0.0, 33.3, 66.7),
   str(cal['stop_hit_rate_pct']))
ck('落档分布只统计可对账样本', sum(r['n'] for r in cal['realized_scenario_mix']) == 3,
   str(cal['realized_scenario_mix']))

# 样本充足时 low_sample 必须转 False
many = [pl.assess(mk_entry(), mk_klines(T0 + DAY, [100] * 7, lows=[97] * 7, highs=[106] * 7),
                  now_ms=NOW) for _ in range(6)]
cal2 = pl.calibration(many)
ck('n≥5 的分组 low_sample=False',
   not cal2['by_confidence'][0]['low_sample'], str(cal2['by_confidence'][0]))
ck('样本充足时基准档守住率为 100%', cal2['base_band_hold_rate_pct'] == 100.0,
   str(cal2['base_band_hold_rate_pct']))

# Markdown 报告必须自带免责与样本不足提示
with tempfile.TemporaryDirectory() as td:
    md = pl.render_md({"generated_at": "2026-09-30T00:00:00+08:00",
                       "calibration": pl.calibration(outcomes), "outcomes": outcomes})
    ck('报告写明排除口径', 'pending' in md and 'no_data' in md)
    ck('样本不足时报告显式警告', '不足以谈' in md or '打通链路' in md)
    ck('报告标出低样本分组', '⚠' in md)
    ck('报告声明未回填历史记录', '未回填' in md)

# ═══════════ 汇总 ═══════════
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-prediction-log: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
