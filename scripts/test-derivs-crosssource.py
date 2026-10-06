#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.5.7 回归：跨所/跨源矛盾检测（derivs_sentiment.py --cross-source）。

背景：消融对比发现换数据源后 funding / open_interest 的结论会明显分歧，
而旧流程是「二选一」（Binance 可用就用 Binance，否则降级 OKX）——分歧被静默吞掉，
工具给了一个数却从不说这个数有多稳。本测试守住新增的 compare_sources。

**完全离线**：monkeypatch 掉 ds.http_get_json（唯一网络出口）与 ds.urlopen，
任何未覆盖的请求都会被记进 LEAK 并直接判红；ds.XSR_BACKBACK 置 0 所以不会真睡。

覆盖六类场景（外加重试语义 / CLI 开关 / 渲染失败可见 / 纪律文案）：
  ① 双方一致 → 无分歧
  ② 资金费率差异大 → 判分歧且说明正确
  ③ 一侧取数失败 → comparable=false 且不判分歧、不崩
  ④ 两侧都为 null
  ⑤ 阈值边界（刚好在阈值上 / 刚过阈值）
  ⑥ 分位/拥挤度字段不参与跨源比较（只比三个指定指标）
"""
import contextlib
import inspect
import io
import json
import os
import sys
import tempfile

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                       'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts')
sys.path.insert(0, SCRIPTS)
import derivs_sentiment as ds   # noqa: E402

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


# ─────────────────────────── 离线护栏 + fixture ───────────────────────────
LEAK = []       # 未被 fixture 覆盖的请求（= 差点真联网）
HITS = {}       # "bn_funding" → 被请求次数（验证重试）

DEFAULTS = {
    'bn_funding': 0.0001, 'okx_funding': 0.0001,
    'bn_oi_usd': 1.0e9, 'okx_oi_usd': 1.0e9,
    'bn_taker': 1.20, 'okx_taker': 1.20,
    'bn_fail': (), 'okx_fail': (),      # 强制 HTTP 失败的 tag
    'bn_flaky': (), 'okx_flaky': (),    # 第 1 次失败、第 2 次成功的 tag（验证重试）
}
STATE = dict(DEFAULTS)


def reset(**kw):
    STATE.clear()
    STATE.update(DEFAULTS)
    STATE.update(kw)
    HITS.clear()
    LEAK[:] = []


def _forbidden(*a, **k):
    LEAK.append(repr(a[:1]))
    raise AssertionError('测试禁止真实联网')


ds.urlopen = _forbidden        # 万一将来有人绕过 http_get_json 直接调 urlopen，立刻炸
ds.XSR_BACKOFF = 0             # 离线测试不睡觉
ds.http_get_json = None        # 下面替换


def _resp(side, tag, build):
    """(data, err) 形态与真实 http_get_json 完全一致。"""
    key = '%s_%s' % (side, tag)
    HITS[key] = HITS.get(key, 0) + 1
    if tag in STATE.get('%s_fail' % side, ()):
        return None, 'HTTPError 451（合规地域限制 Binance 451）'
    if tag in STATE.get('%s_flaky' % side, ()) and HITS[key] == 1:
        return None, 'URLError <urlopen error timed out>'
    return build(STATE.get(key)), None


# ── 响应构造器（数值缺失时返回「没有该字段」，模拟 200 但解析不出数）──
def _bn_funding(v):
    d = {"symbol": "BTCUSDT", "markPrice": "60000.0", "nextFundingTime": 1700000000000}
    if v is not None:
        d["lastFundingRate"] = str(v)
    return d


def _bn_funding_hist(v):
    base = 0.0001 if v is None else v
    return [{"fundingTime": 1700000000000 + i * 28800000, "fundingRate": str(base)}
            for i in range(30)]


def _bn_oi_now(v):
    return {"symbol": "BTCUSDT", "openInterest": "1000.0"}


def _bn_oi(v):
    rows = []
    for _ in range(8):
        r = {"symbol": "BTCUSDT", "sumOpenInterest": "1000.0"}
        if v is not None:
            r["sumOpenInterestValue"] = str(v)
        rows.append(r)
    return rows


def _bn_lsr(_v):
    return [{"timestamp": 1700000000000 - i * 86400000, "longShortRatio": "1.5",
             "longAccount": "0.6", "shortAccount": "0.4"} for i in range(30)]


def _bn_taker(v):
    rows = []
    for _ in range(30):
        r = {"buyVol": "100.0", "sellVol": "100.0"}
        if v is not None:
            r["buySellRatio"] = str(v)
        rows.append(r)
    return rows


def _okx_funding(v):
    d0 = {"instId": "BTC-USDT-SWAP", "fundingTime": "1700000000000"}
    if v is not None:
        d0["fundingRate"] = str(v)
    return {"code": "0", "msg": "", "data": [d0]}


def _okx_funding_hist(v):
    base = 0.0001 if v is None else v
    return {"code": "0", "msg": "",
            "data": [{"fundingRate": str(base), "fundingTime": str(1700000000000 + i * 28800000)}
                     for i in range(30)]}


def _okx_oi(v):
    d0 = {"instId": "BTC-USDT-SWAP", "oi": "1000", "oiCcy": "1000"}
    if v is not None:
        d0["oiUsd"] = str(v)
    return {"code": "0", "msg": "", "data": [d0]}


def _okx_taker(v):
    # OKX 行格式固定 [ts, sellVol, buyVol]，倒序；卖量固定 1 → buy/sell 精确等于目标值
    if v is None:
        return {"code": "0", "msg": "", "data": []}
    return {"code": "0", "msg": "", "data": [["1700000000000", "1.0", str(v)]]}


def _http(url, timeout=8):
    """唯一出口：所有 fixture 都在这里返回。"""
    if 'api.alternative.me/fng' in url:
        return {"data": [{"value": "62", "value_classification": "Greed",
                          "timestamp": "1700000000"}]}, None
    if '/fapi/v1/premiumIndex' in url:
        return _resp('bn', 'funding', _bn_funding)
    if '/fapi/v1/fundingRate' in url:
        return _resp('bn', 'funding_hist', _bn_funding_hist)
    if 'openInterestHist' in url:
        return _resp('bn', 'oi_usd', _bn_oi)
    if '/fapi/v1/openInterest?' in url:
        return _resp('bn', 'oi', _bn_oi_now)
    if 'globalLongShortAccountRatio' in url:
        return _resp('bn', 'lsr', _bn_lsr)
    if 'takerlongshortRatio' in url:
        return _resp('bn', 'taker', _bn_taker)
    if '/public/funding-rate-history' in url:
        return _resp('okx', 'funding_hist', _okx_funding_hist)
    if '/public/funding-rate?' in url:
        return _resp('okx', 'funding', _okx_funding)
    if '/public/open-interest' in url:
        return _resp('okx', 'oi_usd', _okx_oi)
    if 'rubik/stat/taker-volume' in url:
        return _resp('okx', 'taker', _okx_taker)
    LEAK.append(url)
    return None, 'URLError 未被 fixture 覆盖的请求'


ds.http_get_json = _http


def run_case(**kw):
    reset(**kw)
    return ds.compare_sources('BTCUSDT', 8)


def metric(fx, key):
    return run_case(**fx)['metrics'][key]


# ─────────────────────────── ① 双方一致 → 无分歧 ───────────────────────────
x = run_case()
ck('① 双方一致 → divergences 为空', x['divergences'] == [],
   json.dumps(x['divergences'], ensure_ascii=False)[:140])
ck('① 三个指标全部 comparable',
   x['compared'] == ['funding', 'open_interest_usd', 'taker_ratio'], str(x['compared']))
ck('① 一致时各项 divergent=False',
   all(m['divergent'] is False for m in x['metrics'].values()), str(x['metrics'])[:120])
ck('① 一致时 delta_ratio 全为 0',
   all(m['delta_ratio'] == 0.0 for m in x['metrics'].values()),
   str({k: m['delta_ratio'] for k, m in x['metrics'].items()}))
ck('① verdict 明确说未超阈值', '未超阈值' in x['verdict'], x['verdict'])
ck('① verdict 仍声明不下绝对强弱结论', '不说明哪边更强' in x['verdict'], x['verdict'])

# ─────────────────── ② 资金费率差异大 → 判分歧且说明正确 ───────────────────
x = run_case(okx_funding=0.0003)
m = x['metrics']['funding']
ck('② 资金费率相对差 67% → 判分歧', m['divergent'] is True
   and m['comparable'] is True, str(m))
ck('② delta_ratio 取「以较大者为分母」= 0.666667',
   m['delta_ratio'] == 0.666667, str(m['delta_ratio']))
ck('② divergences 只列 funding 一项',
   [d['metric'] for d in x['divergences']] == ['funding'],
   str([d['metric'] for d in x['divergences']]))
ck('② 分歧说明点名资金费率',
   '资金费率' in x['divergences'][0]['note'], x['divergences'][0]['note'])
ck('② 分歧说明写明不可单源采信',
   '不可单源采信' in x['divergences'][0]['note'], x['divergences'][0]['note'])
ck('② 分歧说明带出命中的阈值', '相对差' in x['divergences'][0]['note']
   and '50' in x['divergences'][0]['note'], x['divergences'][0]['note'])
ck('② verdict 出现「跨源存在分歧」', '跨源存在分歧' in x['verdict'], x['verdict'])
ck('② verdict 提示分歧不等于某一方错',
   '不必然说明某一方错' in x['verdict'], x['verdict'])
ck('② 另两项不判分歧',
   not x['metrics']['open_interest_usd']['divergent']
   and not x['metrics']['taker_ratio']['divergent'], str(x['metrics'])[:120])

# ────────────── ③ 一侧取数失败 → comparable=false 且不判分歧、不崩 ──────────────
x = run_case(okx_fail=('funding', 'oi_usd', 'taker'))
ck('③ OKX 侧全失败：返回结构完整不崩',
   x.get('enabled') is True and len(x.get('metrics')) == 3, str(sorted(x))[:120])
ck('③ OKX 侧全失败 → 三项全部 comparable=false',
   all(m['comparable'] is False for m in x['metrics'].values()),
   str({k: m['comparable'] for k, m in x['metrics'].items()}))
ck('③ OKX 侧全失败 → 一项都不判分歧',
   x['divergences'] == [] and all(m['divergent'] is False for m in x['metrics'].values()),
   str(x['divergences'])[:120])
ck('③ OKX 侧全失败 → 不拿单边数字编对比（delta 全为 null）',
   all(m['delta_abs'] is None and m['delta_ratio'] is None for m in x['metrics'].values()),
   str({k: (m['delta_abs'], m['delta_ratio']) for k, m in x['metrics'].items()}))
ck('③ OKX 侧全失败 → 仍保留 Binance 单边的真实值',
   x['metrics']['funding']['binance'] == 0.0001
   and x['metrics']['taker_ratio']['okx'] is None,
   json.dumps(x['metrics']['funding'], ensure_ascii=False)[:120])
ck('③ OKX 侧全失败 → warnings 如实记录失败原因',
   any('跨源/OKX' in w for w in x['warnings']), str(x['warnings'])[:150])
ck('③ OKX 侧全失败 → verdict 明说无法比较',
   '无法进行' in x['verdict'], x['verdict'])
ck('③ OKX 侧全失败 → 追加「未参与分歧判定」说明',
   any('未参与分歧判定' in w for w in x['warnings']), str(x['warnings'])[:150])

x = run_case(okx_fail=('taker',))          # 只坏一项
ck('③ 只坏一项 → 其余两项仍可比',
   x['compared'] == ['funding', 'open_interest_usd'], str(x['compared']))
ck('③ 只坏一项 → 坏的那项不可比且不判分歧',
   x['metrics']['taker_ratio']['comparable'] is False
   and x['metrics']['taker_ratio']['divergent'] is False,
   json.dumps(x['metrics']['taker_ratio'], ensure_ascii=False)[:120])
ck('③ 只坏一项 → divergences 仍为空', x['divergences'] == [], str(x['divergences'])[:120])

# ─────────────────────────── ④ 两侧都为 null ───────────────────────────
x = run_case(bn_fail=('funding', 'oi_usd', 'taker'), okx_fail=('funding', 'oi_usd', 'taker'))
ck('④ 双侧全失败 → 不崩且双方均为 null',
   all(m['binance'] is None and m['okx'] is None for m in x['metrics'].values()),
   str({k: (m['binance'], m['okx']) for k, m in x['metrics'].items()}))
ck('④ 双侧全失败 → 零分歧零可比',
   x['divergences'] == [] and x['compared'] == [], str(x['divergences'])[:100])
ck('④ 双侧全失败 → 两边 warning 都记了', len(x['warnings']) >= 4, str(x['warnings'])[:150])

x = run_case(bn_funding=None, okx_funding=None)      # HTTP 200 但字段缺失
m = x['metrics']['funding']
ck('④ 字段缺失（200 但无该值）→ 也算 null 且不可比',
   m['binance'] is None and m['okx'] is None and m['comparable'] is False,
   json.dumps(m, ensure_ascii=False)[:120])
ck('④ 字段缺失 → 不判分歧', m['divergent'] is False, str(m))

reset()
x = ds.compare_sources('FOOBARQUOTE', 8)             # 无 OKX 永续的币种
ck('④ 无 OKX 合约的币种 → OKX 侧全 null 且不崩',
   all(m['okx'] is None for m in x['metrics'].values())
   and x['divergences'] == [], json.dumps(x, ensure_ascii=False)[:140])
ck('④ 无 OKX 合约 → warnings 说明无法映射',
   any('无法映射' in w for w in x['warnings']), str(x['warnings'])[:120])

# ─────────────────────── ⑤ 阈值边界（在阈值上 / 刚过阈值） ───────────────────────
m = metric({'bn_funding': 0.00002, 'okx_funding': 0.00001}, 'funding')
ck('⑤ 资金费率相对差恰为 0.50（阈值上）→ 不判分歧',
   m['comparable'] is True and m['divergent'] is False, json.dumps(m, ensure_ascii=False)[:130])
ck('⑤ 阈值上 delta_ratio 精确等于 0.5', m['delta_ratio'] == 0.5, str(m['delta_ratio']))

m = metric({'bn_funding': 0.0000201, 'okx_funding': 0.00001}, 'funding')
ck('⑤ 资金费率相对差 0.5025（刚过 0.50）→ 判分歧', m['divergent'] is True,
   json.dumps(m, ensure_ascii=False)[:130])

m = metric({'bn_funding': 0.0002, 'okx_funding': 0.0001}, 'funding')
ck('⑤ 资金费率相对(50%)与绝对(1bp)双阈值同时踩线 → 不判分歧',
   m['comparable'] is True and m['divergent'] is False, json.dumps(m, ensure_ascii=False)[:130])

m = metric({'bn_funding': 0.0004, 'okx_funding': 0.00025}, 'funding')
ck('⑤ 绝对差 1.5bp > 1bp 且相对差 37.5% < 50% → 按绝对规则判分歧',
   m['divergent'] is True, json.dumps(m, ensure_ascii=False)[:130])
ck('⑤ 绝对规则命中原因写进 reason', '绝对差' in m['reason'], m['reason'])

m = metric({'bn_oi_usd': 4.0e9, 'okx_oi_usd': 3.0e9}, 'open_interest_usd')
ck('⑤ OI 相对差恰为 0.25（阈值上）→ 不判分歧',
   m['comparable'] is True and m['divergent'] is False and m['delta_ratio'] == 0.25,
   json.dumps(m, ensure_ascii=False)[:130])
m = metric({'bn_oi_usd': 4.4e9, 'okx_oi_usd': 3.0e9}, 'open_interest_usd')
ck('⑤ OI 相对差 0.318（刚过 0.25）→ 判分歧', m['divergent'] is True,
   json.dumps(m, ensure_ascii=False)[:130])

m = metric({'bn_taker': 1.25, 'okx_taker': 1.1875}, 'taker_ratio')
ck('⑤ 主动买卖比相对差恰为 0.05（阈值上）→ 不判分歧',
   m['comparable'] is True and m['divergent'] is False, json.dumps(m, ensure_ascii=False)[:130])
m = metric({'bn_taker': 1.25, 'okx_taker': 1.18}, 'taker_ratio')
ck('⑤ 主动买卖比相对差 0.056（刚过 0.05）→ 判分歧', m['divergent'] is True,
   json.dumps(m, ensure_ascii=False)[:130])

# ──────── ⑥ 分位/拥挤度字段不参与跨源比较（只比三个指定指标） ────────
x = run_case()
ck('⑥ 只比三个指定指标', set(x['metrics']) == {'funding', 'open_interest_usd', 'taker_ratio'},
   str(sorted(x['metrics'])))
FORBIDDEN = ('percentile', 'crowd', 'annualized', 'mean_30', 'change_7d', '分位', '拥挤')
leaked_keys = [k for m in x['metrics'].values() for k in m
               if any(f in k for f in FORBIDDEN)]
ck('⑥ 指标记录里不含分位/拥挤度字段', leaked_keys == [], str(leaked_keys))
src = ''.join(inspect.getsource(getattr(ds, fn)) for fn in
              ('fetch_with_retry', '_xs_binance_side', '_xs_okx_side',
               '_xsrc_metric', 'compare_sources'))
ck('⑥ 跨源实现源码不触碰分位/拥挤度',
   ('percentile' not in src) and ('crowd' not in src)
   and ('分位' not in src) and ('拥挤' not in src),
   [w for w in ('percentile', 'crowd', '分位', '拥挤') if w in src])
ck('⑥ 跨源实现不依赖单源主流程（不共享同一份数据）',
   ('analyze_one' not in src) and ('build_verdict' not in src), '')
ck('⑥ 阈值配置只覆盖这三个指标', set(ds.XSR_SPECS) == set(ds.XSR_METRICS),
   str(sorted(ds.XSR_SPECS)))
# 分位/拥挤度就算两所天差地别也不该产生分歧：单源流程里它们仍照常产出，只是不进对比
ck('⑥ 跨源输出里不含单源的 percentile_30d / crowded 键',
   'percentile_30d' not in json.dumps(x, ensure_ascii=False)
   and 'crowd_side' not in json.dumps(x, ensure_ascii=False), '')

# ───────────────────── ⑦ CLI 开关（默认关闭 = 行为不变） ─────────────────────
def run_cli(argv, **fx):
    reset(**fx)
    out = os.path.join(tempfile.mkdtemp(), 'derivs.json')
    old = sys.argv
    sys.argv = ['derivs_sentiment.py'] + argv + ['--out', out]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = ds.main()
    finally:
        sys.argv = old
    data = {}
    if os.path.exists(out):
        with open(out, encoding='utf-8') as f:
            data = json.load(f)
    return code, data, buf.getvalue()


code, data, txt = run_cli(['--symbol', 'BTCUSDT'])
ck('⑦ 默认不带 --cross-source → payload 无 cross_source 键',
   'cross_source' not in data, str(sorted(data))[:140])
ck('⑦ 默认不带 --cross-source → 渲染里没有跨源表', '跨源一致性' not in txt, '')
ck('⑦ 默认不带 --cross-source → 单源字段照常产出',
   data['rows'][0]['funding']['last'] == 0.0001
   and data['rows'][0]['taker_buy_sell']['last'] == 1.2,
   json.dumps(data['rows'][0]['funding'], ensure_ascii=False)[:110])
ck('⑦ 默认路径退出码 0', code == 0, str(code))

code, data, txt = run_cli(['--symbol', 'BTCUSDT', '--cross-source'])
ck('⑦ --cross-source 退出码 0', code == 0, str(code))
ck('⑦ --cross-source → payload 含 cross_source 列表',
   isinstance(data.get('cross_source'), list) and len(data['cross_source']) == 1,
   str(type(data.get('cross_source'))))
ck('⑦ --cross-source → 渲染出现跨源一致性表', '跨源一致性' in txt, txt[-160:])
ck('⑦ --cross-source → 表里同时给出双方数值与分歧列',
   ('Binance 0.0100%' in txt) and ('OKX 0.0100%' in txt) and ('分歧:否' in txt), '')
ck('⑦ --cross-source → 带上跨所不可横比的纪律警告',
   '跨所绝对值不可直接横比' in txt and '不得据此下绝对强弱结论' in txt, '')
ck('⑦ --cross-source → 逐项给出分歧清单', '"divergences": []' in
   json.dumps(data, ensure_ascii=False), '')

code, data, txt = run_cli(['--symbols', 'BTCUSDT,ETHUSDT', '--cross-source'])
ck('⑦ 多标的 → cross_source 数量与 symbols 一致',
   [x['symbol'] for x in data.get('cross_source', [])] == ['BTCUSDT', 'ETHUSDT'],
   str([x.get('symbol') for x in data.get('cross_source', [])]))

code, data, txt = run_cli(['--symbol', 'BTCUSDT', '--cross-source'],
                          okx_fail=('funding', 'oi_usd', 'taker'))
ck('⑦ 跨源单侧失败在渲染里可见（不可比 + [!] 警告）',
   '不可比（单边缺失）' in txt and '[!] 跨源/OKX' in txt, txt[-220:])
ck('⑦ 跨源失败进全局 warnings', any('跨源/OKX' in w for w in data['warnings']),
   str(data['warnings'])[:140])
ck('⑦ 跨源单侧失败不崩、退出码仍为 0', code == 0, str(code))

# ───────────────────── ⑧ 重试语义（节点轮换导致的偶发失败） ─────────────────────
x = run_case(okx_flaky=('funding',))
ck('⑧ 第 1 次失败、第 2 次成功 → 仍取到数（节点轮换场景）',
   x['metrics']['funding']['comparable'] is True,
   json.dumps(x['metrics']['funding'], ensure_ascii=False)[:120])
ck('⑧ 确实重试过（该接口被打了 2 次）', HITS.get('okx_funding') == 2, str(HITS.get('okx_funding')))
ck('⑧ 重试后成功不产生失败 warning',
   not any('已试' in w for w in x['warnings']), str(x['warnings'])[:120])

x = run_case(okx_fail=('funding',))
ck('⑧ 永久失败 → 重试用尽后置 null + warning（失败绝不静默）',
   x['metrics']['funding']['comparable'] is False
   and any('已试 2 次' in w for w in x['warnings']), str(x['warnings'])[:160])
ck('⑧ 永久失败 → 确实打满 2 次', HITS.get('okx_funding') == 2, str(HITS.get('okx_funding')))

ck('⑧ http_get_json 本身未被改成带重试（保持既有行为）',
   ('attempts' not in inspect.getsource(ds.http_get_json))
   and ('XSR_BACKOFF' not in inspect.getsource(ds.http_get_json)), '')
ck('⑧ fetch_with_retry 默认 attempts=2', ds.XSR_ATTEMPTS == 2, str(ds.XSR_ATTEMPTS))

# ─────────────────────────── ⑨ 全程离线 + 纪律文案 ───────────────────────────
ck('⑨ 全程零未 fixture 覆盖的请求（测试没有联网）', not LEAK, str(LEAK)[:150])
ck('⑨ 纪律文案含「不可直接横比」', '跨所绝对值不可直接横比' in ds.CROSS_DISCIPLINE,
   ds.CROSS_DISCIPLINE[:60])
ck('⑨ 纪律文案含「不得据此下绝对强弱结论」',
   '不得据此下绝对强弱结论' in ds.CROSS_DISCIPLINE, ds.CROSS_DISCIPLINE[:60])
ck('⑨ 纪律文案含「是否指向同一方向」',
   '是否指向同一方向' in ds.CROSS_DISCIPLINE, ds.CROSS_DISCIPLINE[:60])
ck('⑨ 三个指标的 why 都写明了阈值理由',
   all(ds.XSR_SPECS[k].get('why') for k in ds.XSR_METRICS), str(sorted(ds.XSR_SPECS)))

# ────── ⑩ 跨所绝对额（未平仓美元名义）只作参考，不进可行动分歧清单（v1.5.9 修正）──────
# 实测 Binance $7.73B vs OKX $2.37B（+69.3%）。两所客户体量差异是**结构性**的，
# 25% 阈值几乎必然命中 → divergences 永远非空 → 用户学会无视它，真正的资金费率/
# 主动买卖分歧被噪声淹没。结构性噪声淹没有效信号，比没有这个功能更糟。
ck('⑩ open_interest_usd 标为 actionable=False',
   ds.XSR_SPECS['open_interest_usd'].get('actionable') is False,
   str(ds.XSR_SPECS['open_interest_usd'].get('actionable')))
ck('⑩ 资金费率与主动买卖比仍 actionable',
   ds.XSR_SPECS['funding'].get('actionable', True) is True
   and ds.XSR_SPECS['taker_ratio'].get('actionable', True) is True)
ck('⑩ 指标记录带 actionable 字段',
   all('actionable' in ds._xsrc_metric(k, 1.0, 2.0) for k in ds.XSR_METRICS))

# 构造「只有 OI 超阈值」的场景：funding/taker 一致，OI 差 3 倍（真实量级差）
_m = {
    'funding': ds._xsrc_metric('funding', 0.0003, 0.0003),
    'open_interest_usd': ds._xsrc_metric('open_interest_usd', 7.73e9, 2.37e9),
    'taker_ratio': ds._xsrc_metric('taker_ratio', 1.0, 1.0),
}
_ck_div = [k for k in ds.XSR_METRICS
           if _m[k]['divergent'] and _m[k].get('actionable', True)]
_ck_inf = [k for k in ds.XSR_METRICS
           if _m[k]['divergent'] and not _m[k].get('actionable', True)]
ck('⑩ OI 确实超阈值（相对差 > 25%，说明该场景真实存在）',
   _m['open_interest_usd']['divergent'] is True,
   str(_m['open_interest_usd']['delta_ratio_pct']))
ck('⑩ 但 OI 不进可行动分歧清单（否则清单恒非空、失去筛选力）', _ck_div == [], str(_ck_div))
ck('⑩ OI 改进 informational 列表', _ck_inf == ['open_interest_usd'], str(_ck_inf))
_vd = ds._xsrc_verdict(_m, [], [{'metric': 'open_interest_usd',
                                  'label': '未平仓(美元名义)'}], 'BTCUSDT')
ck('⑩ 结论判为「一致性可接受」而非「存在分歧」', '跨源一致性可接受' in _vd, _vd[:100])
ck('⑩ 结论附注说明 OI 不计入分歧', '不计入分歧结论' in _vd, _vd[-100:])
ck('⑩ 附注点明推不出加仓/减仓方向', '推不出加仓/减仓方向' in _vd, _vd[-100:])

# 端到端：只有 OI 分歧时，divergences 必须为空、informational 必须有一项
reset()
x = ds.compare_sources('BTCUSDT', 8)
ck('⑩ 端到端返回体带 informational 列表', isinstance(x.get('informational'), list),
   str(type(x.get('informational'))))
ck('⑩ 端到端返回体带 informational_note 说明',
   '不计入分歧结论' in (x.get('informational_note') or ''), str(x.get('informational_note')))
ck('⑩ 端到端 divergences 只含 actionable 项',
   all(i['metric'] in ('funding', 'taker_ratio') for i in x.get('divergences') or []),
   str([i.get('metric') for i in x.get('divergences') or []]))
ck('⑩ 端到端 informational 不含 actionable 项',
   all(i['metric'] == 'open_interest_usd' for i in x.get('informational') or []),
   str([i.get('metric') for i in x.get('informational') or []]))

ok = sum(1 for _, c, _ in checks if c)
print('=' * 74)
print(f'v1.5.7 跨源/跨源矛盾检测（--cross-source）：{ok}/{len(checks)} 通过')
print('=' * 74)
for n, c, e in checks:
    print(f'  {"✓" if c else "✗"} {n}')
    if not c and e:
        print(f'      → {e}')
sys.exit(0 if ok == len(checks) else 1)
