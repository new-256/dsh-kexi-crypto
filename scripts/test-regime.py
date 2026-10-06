#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""regime.py 回归（v1.7.0）：市场状态判定上行/下行/横盘。

用**合成 K 线**做夹具，完全离线、可复现。这是刻意的：
判定器一旦依赖网络，它的回归测试就会变成"看交易所当时心情"。

## 测什么

不是"某天判对了"（那是运气），而是**判据是否真的有区分力**：
  ① 造明确的上行/下行/横盘形态，判定器必须给出对应状态；
  ② 单条判据的方向必须对（趋势/广度各自可独立验证）；
  ③ **诚实性约束**——这是本模块的要点，测得比功能更多：
     - 判据不可用时不给分、权重重归一化、置信度按覆盖率下调
     - 置信度**永远不超过 0.85**（概率标签不该有高置信）
     - 失效条件**永远非空**
     - 位置分位**不给方向**（这是刻意设计：高位≠该跌）
"""
import json
import math
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts')
REGIME = os.path.join(SCRIPTS, 'regime.py')
PY = os.environ.get('KEXI_PYTHON') or 'python'

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


if not os.path.exists(REGIME):
    print('缺少 regime.py')
    sys.exit(1)


# ── 合成 K 线生成 ─────────────────────────────────────────────────────────
def synth(n=300, start=100.0, drift=0.004, noise=0.012, seed=7):
    """生成日线 [[ts, o, h, l, c, v], ...]，ts 为开盘时间、按周分桶可用的自然周。"""
    rnd = random.Random(seed)
    px, out = start, []
    t0 = 1_700_000_000_000
    for i in range(n):
        px *= (1.0 + drift + rnd.gauss(0, noise))
        o = px
        h = px * (1 + abs(rnd.gauss(0, 0.004)))
        l = px * (1 - abs(rnd.gauss(0, 0.004)))
        out.append([t0 + i * 86_400_000, o, h, l, px, 1000.0])
    return out


def run_py(code):
    r = subprocess.run([PY, '-c', code], capture_output=True, text=True, timeout=60,
                       cwd=SCRIPTS)
    return r


# 直接 import 模块做函数级测试（更快、更细）
sys.path.insert(0, SCRIPTS)
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location('regime', REGIME)
rg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rg)

# ══════════════ ① 形态 → 状态 ══════════════
up = synth(drift=+0.006, noise=0.004, seed=11)
down = synth(drift=-0.006, noise=0.004, seed=12)
flat = synth(drift=0.0, noise=0.004, seed=13)

r_up = rg.judge(up)
r_dn = rg.judge(down)
r_fl = rg.judge(flat)
ck('① 明确上行形态 → 上行期', r_up['regime'] == rg.UP, r_up['regime'] + ' score=' + str(r_up['score']))
ck('① 明确下行形态 → 下行期', r_dn['regime'] == rg.DOWN, r_dn['regime'] + ' score=' + str(r_dn['score']))
ck('① 横盘形态 → 非方向（横盘期或明确不是上/下）',
   r_fl['regime'] == rg.FLAT, r_fl['regime'] + ' score=' + str(r_fl['score']))
ck('① 上行 score 为正、下行为负',
   r_up['score'] > 0 > r_dn['score'], '%s / %s' % (r_up['score'], r_dn['score']))

# ══════════════ ② 单判据方向 ══════════════
s_t, p_t = rg.criterion_trend(up)
ck('② 趋势判据在上行形态给正分', s_t is not None and s_t > 0.3, str(s_t))
s_t2, _ = rg.criterion_trend(down)
ck('② 趋势判据在下行形态给负分', s_t2 is not None and s_t2 < -0.3, str(s_t2))
# 广度：手工构造 —— 20 个币里 18 个站上 MA20
uni_up = {'S%02d' % i: synth(60, start=100 + i, drift=+0.005, noise=0.002, seed=100 + i)
          for i in range(20)}
uni_dn = {'D%02d' % i: synth(60, start=100 + i, drift=-0.005, noise=0.002, seed=200 + i)
          for i in range(20)}
s_b, p_b = rg.criterion_breadth(uni_up)
ck('② 广度判据在全涨样本给正分', s_b is not None and s_b > 0.5, str(s_b))
s_b2, p_b2 = rg.criterion_breadth(uni_dn)
ck('② 广度判据在全跌样本给负分', s_b2 is not None and s_b2 < -0.5, str(s_b2))
ck('② 广度样本太小 → 判为不可用（不硬算）',
   rg.criterion_breadth({'X': synth(60)})[0] is None, '')
ck('② 无广度数据 → 判为不可用', rg.criterion_breadth(None)[0] is None, '')

# ══════════════ ③ 诚实性约束（重点） ══════════════
# ③-1 位置分位**不给方向**：高位是"时机"提示不是方向判定
s_p, p_p = rg.criterion_position({"pos_90": 96.0, "range_pct_90": 80.0})
ck('③ 位置极高 → 小幅负分（时机提示），不是强方向',
   s_p is not None and -0.45 <= s_p < 0, str(s_p))
ck('③ 位置判据明写「位置不是方向」', '不是方向' in p_p.get('note', ''), p_p.get('note', ''))
s_p2, _ = rg.criterion_position({"pos_90": 5.0, "range_pct_90": 90.0})
ck('③ 位置极低 → 小幅正分，且幅度同样受限',
   s_p2 is not None and 0 < s_p2 <= 0.45, str(s_p2))
s_p3, _ = rg.criterion_position({"pos_90": 50.0})
ck('③ 位置居中 → 0 分', s_p3 == 0.0, str(s_p3))
ck('③ 位置数据缺失 → 不可用', rg.criterion_position({})[0] is None, '')
ck('③ 位置分位幅度不超过 0.45（刻意限制它对方向的影��）',
   abs(rg.WEIGHTS['position']) <= 0.20, str(rg.WEIGHTS['position']))

# ③-2 判据不可用 → 权重重归一化 + 覆盖率下降 + 置信度下调
full = rg.judge(up, universe=uni_up, pos={"pos_90": 50.0}, funding={"percentile": 50.0})
part = rg.judge(up, universe=None, pos={"pos_90": 50.0}, funding=None)
ck('③ 全判据可用时 coverage=1.0', abs(full['coverage'] - 1.0) < 1e-6, str(full['coverage']))
ck('③ 缺 2 类判据时 coverage<1', part['coverage'] < 1.0, str(part['coverage']))
ck('③ 缺判据会被列入 degraded', len(part['degraded']) == 2, str(part['degraded']))
ck('③ 缺判据的 score 置 null（不编造分数）',
   all(c['score'] is None for c in part['criteria'] if not c['available']), '')
ck('③ 缺判据的 weight_used 置 0（不占权重）',
   all(c['weight_used'] == 0.0 for c in part['criteria'] if not c['available']), '')
ck('③ 可用判据的权重被放大以补足（重归一化）',
   abs(sum(c['weight_used'] for c in part['criteria']) - 1.0) < 0.02,
   str(sum(c['weight_used'] for c in part['criteria'])))
ck('③ 覆盖率越低，置信度越低', part['confidence'] < full['confidence'],
   '%s < %s' % (part['confidence'], full['confidence']))

# ③-3 置信度硬封顶
ck('③ 置信度上限常量为 0.85', abs(rg.CONFIDENCE_CAP - 0.85) < 1e-9, str(rg.CONFIDENCE_CAP))
ck('③ 极端有利的输入也不能超过上限',
   rg.judge(up, universe=uni_up, pos={"pos_90": 5.0}, funding={"percentile": 5.0})['confidence'] <= rg.CONFIDENCE_CAP,
   str(rg.judge(up, universe=uni_up, pos={"pos_90": 5.0}, funding={"percentile": 5.0})['confidence']))

# ③-4 失效条件永远非空
for nm, r in (('上行', r_up), ('下行', r_dn), ('无任何辅助数据', rg.judge(up))):
    ck('③ %s 场景失效条件非空' % nm, isinstance(r['invalidation'], list) and len(r['invalidation']) > 0,
       str(r.get('invalidation')))
ck('③ 无任何辅助数据时明说"不具备可证伪基础"',
   any('可证伪' in x for x in rg.judge([])['invalidation']), str(rg.judge([])['invalidation']))
ck('③ 连 K 线都没有时判据全不可用（不给编造的结论）',
   all(not c['available'] for c in rg.judge([])['criteria']), '')
ck('③ 连 K 线都没有时仍给出有限定的结果而非崩溃',
   rg.judge([])['regime'] in (rg.UP, rg.DOWN, rg.FLAT), rg.judge([])['regime'])
ck('③ 有 K 线但无广度/位置时，仍能给出基于趋势的失效条件',
   len(rg.judge(up)['invalidation']) > 0
   and not any('可证伪' in x for x in rg.judge(up)['invalidation']),
   str(rg.judge(up)['invalidation']))

# ⚠ 只断言"非空"是不够的：失效条件写死一句假话也能通过（注入实测 43/43 全绿）。
# 必须验证它**由本次实算的均线值生成**——否则用户拿着一个具体价位去核对却对不上。
_cl = [float(k[4]) for k in up]
_m50 = rg.ku.rn(sum(_cl[-50:]) / 50.0)     # 用 ku.rn 自己的精度——判定里也是它格式化的
_m20 = rg.ku.rn(sum(_cl[-20:]) / 20.0)
_inv = rg.judge(up)['invalidation']
ck('③ 失效条件引用**实算的** MA50 值（写死常量会被抓）',
   any(('MA50' in x and str(_m50) in x) for x in _inv),
   '实算 MA50=%s，判定给：%s' % (_m50, _inv))
ck('③ 失效条件引用实算的 MA20 值',
   any(('MA20' in x and str(_m20) in x) for x in _inv),
   '实算 MA20=%s' % _m20)

# ③-5 样本不足不许硬判
ck('③ 趋势：日线不足 60 根 → 不可用',
   rg.criterion_trend(synth(40))[0] is None, '')
ck('③ 趋势：空输入 → 不可用', rg.criterion_trend([])[0] is None, '')
ck('③ 拥挤度未提供 → 不可用', rg.criterion_crowding(None)[0] is None, '')
ck('③ 拥挤度：分位 95（极端拥挤）→ 强正分',
   rg.criterion_crowding({"percentile": 95})[0] > 0.9, '')

# ══════════════ ④ 离线可复现（CLI 走 --fixtures，全程不联网） ══════════════
tmp = tempfile.mkdtemp(prefix='kexi-regime-')
with open(os.path.join(tmp, 'BTCUSDT.json'), 'w', encoding='utf-8') as f:
    json.dump(up, f)
with open(os.path.join(tmp, 'breadth.json'), 'w', encoding='utf-8') as f:
    json.dump(uni_up, f)
outp = os.path.join(tmp, 'regime.json')
r = subprocess.run([PY, os.path.join(SCRIPTS, 'regime.py'),
                    '--fixtures', tmp, '--out', outp],
                   capture_output=True, text=True, timeout=120, cwd=tmp)
ck('④ CLI --fixtures 跑通（不联网）', r.returncode == 0, r.stderr[-300:])
if r.returncode == 0 and os.path.exists(outp):
    doc = json.load(open(outp, encoding='utf-8'))
    ck('④ 输出 schema 正确', doc.get('schema') == 'kexi.regime/1', str(doc.get('schema')))
    ck('④ CLI 结果与函数级一致（同数据同结论）',
       doc['regime'] == rg.judge(up, universe=uni_up)['regime'],
       '%s vs %s' % (doc['regime'], rg.judge(up, universe=uni_up)['regime']))
    ck('④ 输出含 data_quality 供追溯',
       isinstance(doc.get('data_quality'), dict) and 'bars' in doc['data_quality'], '')
    last = [l for l in r.stdout.strip().splitlines() if l.startswith('{')][-1]
    summ = json.loads(last)
    ck('④ stdout 末行是紧凑 JSON（供 lastJsonLine）',
       summ.get('ok') is True and 'regime' in summ and 'digest' in summ, last[:120])
    # 复现性：同夹具再跑一次结论必须一致
    r2 = subprocess.run([PY, os.path.join(SCRIPTS, 'regime.py'),
                         '--fixtures', tmp, '--out', os.path.join(tmp, 'r2.json')],
                        capture_output=True, text=True, timeout=120, cwd=tmp)
    d2 = json.load(open(os.path.join(tmp, 'r2.json'), encoding='utf-8'))
    ck('④ 同夹具两次运行结论一致（无隐藏随机性）',
       d2['regime'] == doc['regime'] and d2['score'] == doc['score'],
       '%s/%s vs %s/%s' % (d2['regime'], d2['score'], doc['regime'], doc['score']))

# ══════════════ ⑤ 区分力（不只"能出结果"，还要能区分） ══════════════
def _breadth_score(drift, tag):
    uni = {('%s%02d' % (tag, i)): synth(60, start=100 + i, drift=drift, noise=0.002, seed=300 + i)
           for i in range(20)}
    return rg.criterion_breadth(uni)[0] or 0.0


s_up, s_mid, s_dn = _breadth_score(+0.005, 'U'), _breadth_score(0.0, 'M'), _breadth_score(-0.005, 'D')
ck('⑤ 广度得分随样本涨跌单调变化', s_up > s_mid > s_dn, '%s / %s / %s' % (s_up, s_mid, s_dn))

# ══════════════ ⑥ 联网取数通路（假 screener 顶替，不真联网） ══════════════
# 测的是**接线**：fetch_breadth 是否真借用了 screener 的取数层、
# 单样本失败是否不拖垮整批、失败会不会被如实记账。
# 真联网会让回归测试变成"看交易所当时心情"，所以这里 monkeypatch。
import screener as _sc_mod  # noqa: E402

_orig_build = _sc_mod.build_universe
_orig_http = _sc_mod.http_get
_log = {'universe': 0, 'klines': []}


def _walk(drift, n):
    rnd = random.Random(5)
    px, out = 100.0, []
    for _ in range(n):
        px *= (1 + drift + rnd.gauss(0, 0.003))
        out.append(px)
    return out


def _fake_build(top, surge_rank_max, delay):
    _log['universe'] += 1
    _log['top'] = top
    return (['SYM%02d' % i for i in range(top)], [], {})


def _fake_http(path):
    _log['klines'].append(path)
    if 'symbol=SYM00' in path:
        raise RuntimeError('模拟该币取数失败')          # 单点失败
    sym = path.split('symbol=')[1].split('&')[0]
    drift = 0.005 if int(sym[-2:]) < 15 else -0.005     # 一半涨一半跌
    rows = _walk(drift, 70)
    return [[i * 86_400_000, c, c * 1.01, c * 0.99, c, 1000.0] for i, c in enumerate(rows)]


_sc_mod.build_universe = _fake_build
_sc_mod.http_get = _fake_http
try:
    uni, meta = rg.fetch_breadth(top=30, workers=4)
    ck('⑥ 广度取数借用了 screener.build_universe（宇宙定义不漂移）',
       _log['universe'] == 1 and _log.get('top') == 30, str(_log))
    ck('⑥ 逐币走 screener.http_get', len(_log['klines']) == 30, str(len(_log['klines'])))
    ck('⑥ 单样本失败不拖垮整批（仍取到 29 个）',
       meta['universe_size'] == 29, str(meta['universe_size']))
    ck('⑥ 失败样本被如实记账',
       len(meta['failed']) == 1 and 'SYM00' in meta['failed'], str(meta['failed']))
    s_f, p_f = rg.criterion_breadth(uni)
    ck('⑥ 部分失败时广度判据仍可用（有效样本 >20）',
       s_f is not None and p_f['available'], str(p_f.get('note')))
    r_f = rg.judge(up, universe=uni, breadth_meta=meta)
    ck('⑥ data_quality 带回取数元信息（取了多少、错了什么可追溯）',
       (r_f['data_quality'].get('breadth_fetch') or {}).get('universe_size') == 29,
       str(r_f['data_quality'].get('breadth_fetch')))
finally:
    _sc_mod.build_universe = _orig_build
    _sc_mod.http_get = _orig_http

# 全网失败 → 广度判据必须判不可用（而不是拿空宇宙算个 0 分）
_sc_mod.build_universe = lambda top, surge_rank_max, delay: (['A', 'B'], [], {})


def _always_fail(path):
    raise RuntimeError('全网失败')


_sc_mod.http_get = _always_fail
try:
    uni2, meta2 = rg.fetch_breadth(top=30, workers=4)
    ck('⑥ 全网失败时返回空宇宙且全部记账',
       uni2 == {} and len(meta2['failed']) == 2, str(meta2)[:120])
    ck('⑥ 全网失败时广度判据判不可用（不拿空宇宙凑分）',
       rg.criterion_breadth(uni2)[0] is None, '')
    ck('⑥ 全网失败时整体降级而不是崩溃',
       rg.judge(up, universe=uni2)['regime'] in (rg.UP, rg.DOWN, rg.FLAT), '')
finally:
    _sc_mod.build_universe = _orig_build
    _sc_mod.http_get = _orig_http

# ══════════════ 汇总 ══════════════
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-regime: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
