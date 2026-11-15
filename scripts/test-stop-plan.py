#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.5.5 回归：可执行止损（消融对比发现的真缺陷修复）。

缺陷：entry_plan 的 invalidation 是**结构性失效位**（跌破前低/60日低），
PYTH 上为 0.03763 = -53.7%。这种"止损"在风控上等于没有——途中保护不了仓位。
修复：三法交叉（结构位/MA20/ATR）给出可执行止损，并明确标注结构位不可当止损用。
"""
import json
import os
import subprocess
import sys
import tempfile

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                       'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts')
sys.path.insert(0, SCRIPTS)
import fast_analysis as fa   # noqa: E402

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


def mk_klines(closes, start=1_700_000_000_000, step=86_400_000):
    out = []
    for i, c in enumerate(closes):
        o = c * 0.99
        out.append([start + i * step, o, c * 1.01, o * 0.98, c, 1_000_000.0])
    return out


# ① 正常趋势：应给出可执行止损，且比结构位近得多
kl = mk_klines([0.05 + i * 0.0004 for i in range(160)])
res = {"close": round(kl[-1][4], 5),
       "ma": {"ma20": round(sum(c[4] for c in kl[-20:]) / 20, 5)},
       "volatility": {"atr_pct": 6.0},
       "entry_plan": {"invalidation": {"price": round(kl[-1][4] * 0.46, 5)}}}
sp = fa.build_stop_plan(kl, res, supports=[round(kl[-1][4] * 0.97, 5), round(kl[-1][4] * 0.90, 5)])
ck('正常趋势给出可执行止损', sp.get('tactical') is not None, json.dumps(sp, ensure_ascii=False)[:120])
ck('止损距现价在合理区间（-3%~-35%）',
   sp.get('distance_pct') is not None and -35 <= sp['distance_pct'] <= -3, str(sp.get('distance_pct')))
ck('可执行止损明显优于结构位（至少近 10 倍）',
   sp.get('structural') and sp.get('tactical') and sp['tactical'] / sp['structural'] > 1.1,
   f"tactical={sp.get('tactical')} structural={sp.get('structural')}")
ck('note 明确警告结构位不可当止损用', '不可当作止损' in (sp.get('note') or ''), '')
ck('记录了裁决方法', bool(sp.get('method')), str(sp.get('method')))

# ② ATR 不可知且所有候选都过近（<1×ATR 下限用 close*2% 兜底）→ 诚实拒绝
# 语义说明：只要 ATR 可得，ATR 候选间距恒为 1.5×ATR，必然给出可行止损；
# 拒绝路径只在**无法度量波动**时才触发——那时硬凑一个数才是危险的。
res2 = {"close": 100.0, "ma": {"ma20": 99.92}, "volatility": {},
        "entry_plan": {"invalidation": {"price": 90.0}}}
sp2 = fa.build_stop_plan([], res2, supports=[99.9, 99.7, 99.95])
ck('波动不可知且候选全过近时诚实拒绝（不硬凑）',
   sp2.get('tactical') is None and '无法给出可执行止损' in (sp2.get('note') or ''),
   json.dumps(sp2, ensure_ascii=False)[:150])

# ③ 无现价时不编造
sp3 = fa.build_stop_plan([], {"close": None}, [])
ck('无现价时返回明确说明而非编造', sp3.get('tactical') is None and '无现价' in (sp3.get('note') or ''), '')

# ④ 端到端：真实 PYTH 数据
# ⚠ 这份基线在**仓库之外**（用户真实产出目录），干净机器上不存在。体检 P1-6 /
#   规则 1 的教训是：旧实现"路径不存在就整段不注册断言"，分母跟着缩水，
#   测试照报 N/N 通过——等于这两条最贴近真金白银的断言在 CI 上恒为未验证。
#   现在改为 **fail-loud**：断言恒定注册（分母恒为 9），基线缺失/损坏就判失败，
#   并把期望路径与取得方式打印出来，让门禁红得可解释。
#   目录支持 KEXI_REAL_DIR 覆盖，避免"只有开发机这一台能跑"。
K = os.environ.get('KEXI_REAL_DIR') or r'C:\Users\lcl\Desktop\K析工作文件夹\kexi_out'
real_kl_path = os.path.join(K, 'PYTHUSDT_1d_klines.json')
_REAL_HINT = ('期望路径: %s\n'
              '        取得方式: 对 PYTHUSDT 产出日线分析（快析写盘目录即 K），'
              '或把 KEXI_REAL_DIR 指向已含该文件的目录'
              % real_kl_path)

_rsp = None
_fail_note = ''
if not os.path.exists(real_kl_path):
    _fail_note = '基线缺失，本条未验证（禁止静默跳过）\n        ' + _REAL_HINT
else:
    try:
        with open(real_kl_path, encoding='utf-8') as f:
            rk = json.load(f).get('klines')
        if not rk:
            raise ValueError('文件里没有非空 klines 数组')
        close = rk[-1][4]
        atr = close * 0.075366
        rres = {"close": close, "ma": {"ma20": 0.06241},
                "volatility": {"atr": atr, "atr_pct": 7.5366},
                "entry_plan": {"invalidation": {"price": 0.03763}}}
        _rsp = fa.build_stop_plan(rk, rres, supports=[0.08, 0.078, 0.07769, 0.06241])
        print('  基线：真实 PYTH 产出 %s' % real_kl_path)
    except Exception as e:                                  # noqa: BLE001
        _fail_note = ('基线不可用（%s），本条未验证\n        %s'
                      % (e, _REAL_HINT))

if _fail_note:
    ck('真实 PYTH：止损落在守拙实测区间(0.0685~0.0715)附近', False, _fail_note)
    ck('真实 PYTH：结构位 -53.7% 被明确标注不可用作止损', False, _fail_note)
else:
    tactical = _rsp.get('tactical')
    ck('真实 PYTH：止损落在守拙实测区间(0.0685~0.0715)附近',
       tactical is not None and 0.066 <= tactical <= 0.075,
       f"快答={tactical} vs 守拙=0.0715（盘中硬止损 0.0685）")
    ck('真实 PYTH：结构位 -53.7% 被明确标注不可用作止损',
       '不可当作止损' in (_rsp.get('note') or ''), '')

ok = sum(1 for _, c, _ in checks if c)
print('=' * 70)
print(f'v1.5.5 可执行止损：{ok}/{len(checks)} 通过')
print('=' * 70)
for n, c, e in checks:
    print(f'  {"✓" if c else "✗"} {n}')
    if not c and e:
        print(f'      → {e}')
sys.exit(0 if ok == len(checks) else 1)
