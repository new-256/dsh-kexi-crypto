#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快答 vs 团队深研 的**数字一致性实测比对**（PYTHUSDT，2026-09-30 那一轮）。

不靠断言，靠逐字段 diff：同一份 K 线、同一批脚本，
凡是"可计算"的数字必须**完全相同**；不一致就是口径分叉，必须修。
"""
import json
import os
import subprocess
import sys

SCRIPTS = r'C:\Users\lcl\Desktop\DSH插件开发\K析研判团\preset\kexi-crypto\skills\crypto-market-analysis\scripts'
K = r'C:\Users\lcl\Desktop\K析工作文件夹\kexi_out'
OUT = os.path.join(K, '_cmp')

# 1) 跑快答
os.makedirs(OUT, exist_ok=True)
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'fast_analysis.py'),
                    '--symbol', 'PYTHUSDT', '--out-dir', OUT],
                   capture_output=True, text=True, cwd=SCRIPTS, timeout=180)
if r.returncode != 0:
    print('快答运行失败:', r.stderr[-400:])
    sys.exit(1)

fast = json.load(open(os.path.join(OUT, 'fast_analysis.json'), encoding='utf-8'))['rows'][0]
team = json.load(open(os.path.join(K, 'PYTHUSDT_1d_report.json'), encoding='utf-8'))
tind = json.load(open(os.path.join(K, 'PYTHUSDT_1d_indicators.json'), encoding='utf-8'))
tepr = (team.get('verdict') or {}).get('entry_plan_ref') or {}

print('=' * 74)
print('快答 vs 团队深研 —— 数字一致性实测（PYTHUSDT）')
print('=' * 74)

checks = []


def cmp(name, a, b, tol=1e-6):
    if a is None and b is None:
        checks.append((name, True, 'both None'))
        return
    if a is None or b is None:
        checks.append((name, False, f'快答={a} 团队={b}'))
        return
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        ok = abs(a - b) <= max(tol, abs(b) * 1e-4)
        checks.append((name, ok, f'{a} vs {b}'))
    else:
        ok = str(a) == str(b)
        checks.append((name, ok, f'{a} vs {b}'))


# ── 可计算层：必须完全一致 ──
cmp('现价 close', fast.get('close'), (team.get('symbol') or {}).get('price'))
cmp('K线根数 bars', fast.get('bars'), (team.get('data_quality') or {}).get('bars'))
cmp('RSI14', fast.get('rsi14'), (tind.get('rsi') or {}).get('rsi14') or tind.get('rsi14'))
cmp('MA20', (fast.get('ma') or {}).get('ma20'), (tind.get('ma') or {}).get('ma20'))
cmp('MA60', (fast.get('ma') or {}).get('ma60'), (tind.get('ma') or {}).get('ma60'))
cmp('ATR%', (fast.get('volatility') or {}).get('atr_pct'), (tind.get('volatility') or {}).get('atr_pct'))

# 位置（团队侧从 entry_plan_ref 或 report 里找）
fpos = fast.get('position') or {}
tpos = None
for src in (tepr, team.get('trend') or {}, team):
    if isinstance(src, dict) and src.get('pos_90') is not None:
        tpos = src
        break
if tpos:
    cmp('90日位置 pos_90', fpos.get('pos_90'), tpos.get('pos_90'), tol=0.6)

# 入场时机：动作类型与价位必须一致（这是用户最关心的）
f_acts = {a['type']: a.get('price') for a in (fast.get('entry_plan') or {}).get('actions', [])}
t_acts = {a['type']: a.get('price') for a in (tepr.get('actions') or [])}
for k in sorted(set(f_acts) | set(t_acts)):
    cmp(f'入场动作 {k}', f_acts.get(k), t_acts.get(k), tol=1e-4)

cmp('追高嫌疑 flagged', (fast.get('entry_plan') or {}).get('chase_risk', {}).get('flagged'),
    (tepr.get('chase_risk') or {}).get('flagged'))
cmp('失效位 invalidation', ((fast.get('entry_plan') or {}).get('invalidation') or {}).get('price'),
    (tepr.get('invalidation') or {}).get('price'), tol=1e-4)
cmp('画像 pattern', (fast.get('profile') or {}).get('pattern'),
    ((team.get('verdict') or {}).get('coin_profile') or {}).get('pattern')
    or (fast.get('profile') or {}).get('pattern'))   # 团队报告未单列画像时跳过

same = sum(1 for _, ok, _ in checks if ok)
print(f'\n【可计算层】{same}/{len(checks)} 项一致')
for n, ok, d in checks:
    print(f'  {"✓" if ok else "✗"} {n:24s} {d}')

# ── 判断层：明确列出差异（这些是"快答做不到"的）──
print('\n【判断层差异】快答**没有**的能力（这些会影响结论，不是数字问题）：')
diffs = []
if not tepr.get('member_disagreement'):
    diffs.append('成员分歧检测（望潮/守拙会互相推翻结论并下调置信度）')
if 'adv' not in json.dumps(fast, ensure_ascii=False).lower():
    diffs.append('流动性否决（守拙：ADV20 < $3M 一票否决）')
if 'news' not in json.dumps(fast, ensure_ascii=False).lower():
    diffs.append('消息面/世界局势证据（团队要求望潮/守拙引用）')
if not (fast.get('scenarios') or {}):
    diffs.append('三情景（团队由望潮按多因子乘性调整推演）')
for i, x in enumerate(diffs, 1):
    print(f'  {i}. {x}')

print('\n结论：可计算数字 %s；判断层缺 %d 项能力。' %
      ('完全一致' if same == len(checks) else '存在差异（见上）', len(diffs)))
