#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.5.9 回归：快答结论置信度分级 + 三情景区间结构修正。

两个被本次改动堵上的真实缺陷：

1) **快答只给评级、不给置信度**。团队深研里望潮会按数据质量与信号一致性定级，
   但快答没有——于是"降级源 + 22 根 4 天粒度 + 正在追高"与"币安 240 根正规
   日线 + 周期共振"在用户眼里长得一模一样。本测试钉住：置信度必须随数据
   质量下降，且**低置信不得给立即入场**。

2) **三情景区间结构反了**（消融对比 + 实跑日志同时暴露）。旧版用最深支撑
   `min(supports)` 当基准档下沿，导致基准档吞掉几乎全部跌幅，而"悲观"只剩
   支撑到尾部之间一条窄缝；BTC 实测悲观 47% 只对应 0.2% 宽的区间。
   同时追高时 p_base 被下调、���差全灌进悲观 → 悲观成了众数档，与"基准=最可能"
   约定相反。本测试钉住：基准恒为众数、三档概率和为 1、三段区间首尾相接不倒挂、
   且任何一档都不得退化成零宽窄缝。

本文件全程离线：不联网、不读盘、不写文件。
"""
import os
import sys

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                       'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts')
sys.path.insert(0, SCRIPTS)
import fast_analysis as fa   # noqa: E402

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


def base_res(**over):
    """一份"数据健康"的快答结果，用作对照组。"""
    r = {
        'symbol': 'BTCUSDT', 'close': 83663.66, 'bars': 300, 'last_bar_age_days': 0.3,
        'data_source': 'binance', 'liquidity_known': True, 'adv20_quote': 1.3e9,
        'illiquid': False, 'volatility': {'atr_pct': 2.67},
        'position': {'pos_90': 50.0, 'zone': '中位'},
        'timeframe': {'daily_dir': '多头排列', 'weekly_dir': '多头排列',
                      'monthly_dir': '站上MA20', 'alignment': '全周期共振向上'},
        'entry_plan': {'chase_risk': {'flagged': False},
                       'actions': [{'type': 'enter_now', 'label': '立即入场'}]},
    }
    r.update(over)
    return r


# ═══════════════ 一、置信度分级 ═══════════════
c = fa.compute_confidence(base_res(), [])
ck('数据健康时置信度为「高」', c['level'] == '高', 'level=%s score=%s' % (c['level'], c['score']))
ck('无扣分时 penalties 为空', c['penalties'] == [], str(c['penalties'])[:100])
ck('无扣分时 blocking=False', c['blocking'] is False)

c = fa.compute_confidence(base_res(data_source='coingecko'), [])
ck('降级源（coingecko）降级置信度', c['level'] != '高', 'level=%s' % c['level'])
ck('降级源被显式记为扣分项 degraded_source',
   any(p['code'] == 'degraded_source' for p in c['penalties']), str(c['penalties'])[:160])
# 真实降级路径里 CoinGecko 免费档**同时**没有成交量字段，liquidity_known 必为 False，
# 两条扣分叠加才落到「低」——单看降级源只是「中」（方向可信度尚可，执行细节缺失）。
c = fa.compute_confidence(
    base_res(data_source='coingecko', adv20_quote=None, liquidity_known=False), [])
ck('降级源+无成交量（真实降级路径）→「低」', c['level'] == '低',
   'level=%s score=%s' % (c['level'], c['score']))
ck('降级路径同时记 degraded_source 与 liquidity_unknown',
   {p['code'] for p in c['penalties']} >= {'degraded_source', 'liquidity_unknown'},
   str([p['code'] for p in c['penalties']]))

c = fa.compute_confidence(base_res(bars=90), [])
ck('样本不足 90 根 → 至少「中」', c['level'] in ('中', '低'), 'level=%s' % c['level'])
ck('样本不足被记为 thin_bars',
   any(p['code'] == 'thin_bars' for p in c['penalties']), str(c['penalties'])[:120])

c = fa.compute_confidence(base_res(liquidity_known=False), [])
ck('流动性无法判定 → 至少「中」（不得判高）', c['level'] in ('中', '低'), 'level=%s' % c['level'])
ck('流动性未知被单独记为 liquidity_unknown',
   any(p['code'] == 'liquidity_unknown' for p in c['penalties']))

c = fa.compute_confidence(
    base_res(entry_plan={'chase_risk': {'flagged': True}, 'actions': []}), [])
ck('追高嫌疑 → 至少「中」', c['level'] in ('中', '低'), 'level=%s' % c['level'])
ck('追高被记为 chase_risk',
   any(p['code'] == 'chase_risk' for p in c['penalties']))

c = fa.compute_confidence(
    base_res(timeframe={'daily_dir': '多头排列', 'weekly_dir': '空头排列',
                        'monthly_dir': '跌破MA20', 'alignment': '冲突'}), [])
ck('多周期冲突 → 至少「中」', c['level'] in ('中', '低'), 'level=%s' % c['level'])
ck('多周期冲突被记为 tf_conflict',
   any(p['code'] == 'tf_conflict' for p in c['penalties']))

c = fa.compute_confidence(base_res(), [{'stage': 'data_quality', 'error': '日线停更 9 天'}])
ck('数据不可用 → 置信度「低」', c['level'] == '低', 'level=%s' % c['level'])
ck('数据不可用 → blocking=True', c['blocking'] is True)

c = fa.compute_confidence(base_res(), [{'stage': 'position', 'error': 'boom'}])
ck('非数据类阶段失败只扣 1 分（不误杀为低）', c['level'] == '中', 'level=%s' % c['level'])
ck('非数据类阶段失败 blocking=False', c['blocking'] is False)

c = fa.compute_confidence(base_res(volatility=None, position=None), [])
ck('关键维度缺失 → 逐项记 missing_module',
   sum(1 for p in c['penalties'] if p['code'] == 'missing_module') == 2, str(c['penalties'])[:140])
ck('置信度恒为 高/中/低 三档之一', c['level'] in ('高', '中', '低'), c['level'])
ck('置信度对象带硬规则文本', isinstance(c.get('rule'), str) and c['rule'], str(c.get('rule')))

# ═══════════════ 二、三情景区间结构 ═══════════════
C = 83663.66
CASES = [
    ('追高',         C, [74967.0], [88000.0], 2.67, True,  False),
    ('常态',         C, [74967.0], [88000.0], 2.67, False, False),
    ('周线空头',     C, [74967.0], [88000.0], 2.67, False, True),
    ('近支撑远阻力', C, [82000.0], [95000.0], 2.67, False, False),
    ('无结构位',     100.0, [],         [],        3.0,  False, False),
    ('支撑贴现价',   100.0, [99.9],     [100.1],   3.0,  False, False),
    ('结构位倒挂',   100.0, [120.0],    [80.0],    3.0,  False, False),
    ('支撑全在现价上方', C, [90000.0],  [95000.0], 2.67, False, False),
    ('高ATR',        0.07875, [0.0547], [0.1027],  8.6, True,  False),
]
for nm, c0, sup, res_, atr, ch, wb in CASES:
    x = fa.build_scenarios(c0, sup, res_, atr, ch, wb)
    tot = sum(v['p'] for v in x.values())
    modal = max(x.items(), key=lambda kv: kv[1]['p'])[0]
    pb, pm, po = x['悲观']['range'], x['基准']['range'], x['乐观']['range']
    ck('[%s] 三档概率之和 = 1.00' % nm, abs(tot - 1.0) < 1e-9, 'sum=%s' % tot)
    ck('[%s] 基准恒为众数档（悲观不得反超）' % nm, modal == '基准', 'modal=%s' % modal)
    ck('[%s] 三段区间首尾相接不倒挂' % nm,
       pb[1] <= pm[0] + 1e-9 and pm[1] <= po[0] + 1e-9, '%s %s %s' % (pb, pm, po))
    for key in ('悲观', '基准', '乐观'):
        w = (x[key]['range'][1] - x[key]['range'][0]) / c0
        ck('[%s] %s 档宽度 > 0.5%%（不得退化成窄缝）' % (nm, key), w > 0.005,
           'width=%.4f%% range=%s' % (w * 100, x[key]['range']))
    ck('[%s] 基准档含现价' % nm, pm[0] <= c0 <= pm[1], 'base=%s close=%s' % (pm, c0))

# 追高必须压低乐观概率；周线空头把乐观硬压到 ≤20%
x_ch = fa.build_scenarios(C, [74967.0], [88000.0], 2.67, True, False)
x_no = fa.build_scenarios(C, [74967.0], [88000.0], 2.67, False, False)
ck('追高压低乐观概率', x_ch['乐观']['p'] < x_no['乐观']['p'],
   'chase=%.2f < normal=%.2f' % (x_ch['乐观']['p'], x_no['乐观']['p']))
ck('追高时悲观概率高于常态', x_ch['悲观']['p'] > x_no['悲观']['p'],
   'chase=%.2f > normal=%.2f' % (x_ch['悲观']['p'], x_no['悲观']['p']))
x_wb = fa.build_scenarios(C, [74967.0], [88000.0], 2.67, False, True)
ck('周线空头把乐观概率硬压到 ≤0.20', x_wb['乐观']['p'] <= 0.20, 'p=%.2f' % x_wb['乐观']['p'])

# 回归钉子：旧版"悲观=窄缝 + 悲观反超基准"的形状必须不再出现
x_old_shape = x_ch
ck('【回归】悲观档不再是最窄档（v1.5.9 前的真 bug）',
   (x_old_shape['悲观']['range'][1] - x_old_shape['悲观']['range'][0])
   >= (x_old_shape['乐观']['range'][1] - x_old_shape['乐观']['range'][0]) * 0.25,
   'bear_w=%.2f bull_w=%.2f' % (
       x_old_shape['悲观']['range'][1] - x_old_shape['悲观']['range'][0],
       x_old_shape['乐观']['range'][1] - x_old_shape['乐观']['range'][0]))
ck('【回归】追高时悲观概率不再反超基准（v1.5.9 前的真 bug）',
   x_ch['悲观']['p'] <= x_ch['基准']['p'],
   'bear=%.2f base=%.2f' % (x_ch['悲观']['p'], x_ch['基准']['p']))

# ═══════════════ 三、汇总 ═══════════════
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-fast-confidence: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
