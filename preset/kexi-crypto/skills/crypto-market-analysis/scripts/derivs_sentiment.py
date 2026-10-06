#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
derivs_sentiment.py - 衍生品情绪采集（快研/团队研判的缺失维度）

为什么需要（2026-09-30）：
    快研（fast_analysis.py）与团队研判目前只有 OHLCV 技术面 + 少量新闻，
    缺一整块**衍生品情绪**：合约持仓在加还是在减、多头挤不挤、
    主动买盘有没有跟上。这一块不用任何付费数据就能免费拿到，
    是当前性价比最高的补齐项。

数据来源（全部免费、无需 API key、无需注册，实测响应 300ms 级）：

  ── 主源：Binance USDT 永续（维度最全，含账户多空比）──
    恐惧贪婪指数    https://api.alternative.me/fng/?limit=30
        全局指标，只覆盖比特币，对全市场生效（非按 symbol 分别计算）。
        返回 data[] 按时间**倒序**（index 0 = 最新），value 是字符串。
    资金费率(当前)  https://fapi.binance.com/fapi/v1/premiumIndex?symbol=<SYM>
        lastFundingRate 是当前/预估资金费率（8 小时结算一次）。
    资金费率(历史)  https://fapi.binance.com/fapi/v1/fundingRate?symbol=<SYM>&limit=30
        数组按 fundingTime**正序**（index 0 = 最老），用于算分位与均值。
    未平仓量(当前)  https://fapi.binance.com/fapi/v1/openInterest?symbol=<SYM>
        openInterest 是**合约张数（base units）**，不是美元金额。
    未平仓量(历史)  https://fapi.binance.com/futures/data/openInterestHist
                       ?symbol=<SYM>&period=1d&limit=8
        只有这个接口才有真实历史序列，open_interest.change_7d 由它算出。
        故意**不用**"连打两次 openInterest 取差值"的取巧做法——相隔几秒的
        两次快照差出来的是噪声，不是变化率，编不如不编。
    全市场账户多空比 https://fapi.binance.com/futures/data/globalLongShortAccountRatio
                       ?symbol=<SYM>&period=1d&limit=30
        倒序。注意是**账户数占比**（散户口径），不是持仓市值占比。
    主动买卖量比    https://fapi.binance.com/futures/data/takerlongshortRatio
                       ?symbol=<SYM>&period=1d&limit=30
        倒序，buySellRatio = 主动买量 / 主动卖量，>1 主动买占优。

  ── 备源：OKX USDT 永续（Binance 不可达时自动接管）──
    Binance fapi 对部分地区返回 **HTTP 451 "Service unavailable from a
    restricted location"**（合规地域限制，与 UA 无关，换 UA 无效）。
    此时自动降级到 OKX 公共接口，同样免费免 key：
        资金费率(当前)  https://www.okx.com/api/v5/public/funding-rate?instId=<BTC-USDT-SWAP>
        资金费率(历史)  https://www.okx.com/api/v5/public/funding-rate-history?instId=<...>&limit=30
        未平仓量(当前)  https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=<...>
                         （自带 oiUsd，比 Binance 那个多一个美元口径）
        主动买卖量      https://www.okx.com/api/v5/rubik/stat/taker-volume
                         ?ccy=<BTC>&instType=CONTRACTS&period=1D
                         （返回 [ts, sellVol, buyVol]，倒序）
    **OKX 没有公开的账户多空比接口**（rubik/stat/contracts-long-short-*
    系列已 404），因此降级时 long_short_ratio 恒为 null 并在 warnings
    说明原因——宁可空着也不拿别的口径顶替。
    每个维度都带 `provider` 字段标明数字来自哪家交易所，
    因为**不同交易所的费率结构与资金池不同，绝对值不可直接横向比较**。

纪律（这个脚本存在的意义就在这几行）：

    衍生品情绪指标只作 **过滤器（filter）**，不作 **alpha 来源**。
    它能回答的是"现在追多是不是已经很挤""这波涨是加仓还是撤杠杆"，
    它**不能**回答"该买哪个、什么时候买、目标价多少"。
    入场时机的依据仍然是 K 线结构与流动性（fast_analysis / indicators / entry_plan），
    本脚本的输出应当作为它们的**附加过滤条件**使用，而不是替代。

    因此：
    · 绝不用情绪指标反推价格方向或目标位；
    · 绝不当"贪婪"就断言"要跌"、"恐惧"就断言"要涨"（情绪可以是钝的）；
    · 绝不因为某个字段取不到就拿 0 或历史印象填充——取不到就是 null + warning。

已知局限（必须在结论里如实体现，不要装作没有）：

    · 恐惧贪婪指数只基于比特币，且本质是**反向**情绪指标：
      极端贪婪常出现在阶段高位、极端恐惧常出现在阶段低位，但钝化/背离都存在。
    · 多空比是**账户数**占比，散户口径；大资金完全可能站相反一边，
      账户多空比与持仓多空比经常背离。
    · 资金费率的极值常常出现在行情末端，属**反指标**（拥挤度警示）而非领先指标。
    · 不同交易所的费率结构与资金池不同，**不可直接横向比较**。
    · 跨源对比（--cross-source）只回答「两边是否指向同一方向」；即便发现分歧，
      也不得据此下「哪边更挤/更强」的绝对强弱结论。
    · 未平仓量增加既可能是多头进场也可能是空头进场，需配合价格方向才可解读。
    · 部分小币种没有 USDT 永续合约，此时全部字段为 null，属正常情况。
    · 降级到 OKX 后，多空比缺失、OI 无历史序列，覆盖面小于 Binance 模式。

可选 --cross-source（默认关闭，因为会额外耗时）：
    同时向 Binance 与 OKX 各取一遍**双方都有**的三个指标（资金费率 / 未平仓美元名义 /
    主动买卖比）并排对比，标出分歧。单边缺失一律 comparable=false，绝不拿单边数字编对比。

产出：
    kexi_out/derivs_sentiment.json   schema = "kexi.derivs_sentiment/1"
"""

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

TZ8 = timezone(timedelta(hours=8))

HTTP_TIMEOUT = 8              # 单请求超时（秒）——任何源失败都不拖垮整体
FNG_LIMIT = 30
FUNDING_LIMIT = 30
RATIO_LIMIT = 30
OI_HIST_LIMIT = 8             # 1d x 8 根 → 7 日变化 + 当日
CHANGE_7D_IDX = 7             # 1d 粒度下，index 7 即 7 日前

CROWD_LONG_PCT = 80.0         # 分位 ≥ 80 → 多头拥挤
CROWD_SHORT_PCT = 20.0        # 分位 ≤ 20 → 空头拥挤
FUNDINGS_PER_DAY = 3          # 永续合约 8 小时结算一次 → 每日 3 次
DAYS_PER_YEAR = 365

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
HTTP_HEADERS = {
    "User-Agent": USER_AGENT,      # 缺这个 Binance fapi 可能返回 451
    "Accept": "application/json",
}

FNG_URL = "https://api.alternative.me/fng/?limit=%d" % FNG_LIMIT
BINANCE = "binance_fapi"
OKX = "okx"
FAPI = "https://fapi.binance.com"
OKX_API = "https://www.okx.com/api/v5"

DISCIPLINE = ("衍生品情绪只作过滤器不作 alpha 来源：用于判断拥挤度与增/减仓，"
              "入场时机仍以 K 线结构与流动性为准；任何缺失字段一律 null + warning，绝不编造。")

# ── 跨源对比（--cross-source，默认关闭）──
# 为什么补这一段：旧流程是**二选一**（Binance 可用就用 Binance，否则降级 OKX）。
# 消融对比已经发现「换数据源后 funding / open_interest 的结论会明显分歧」，
# 而二选一把这个分歧**静默吞掉**了——工具给了一个数，却从不说这个数有多稳。
# 下面这组常量只服务于 compare_sources，不影响任何既有取数路径。
XSR_ATTEMPTS = 2            # 每接口最多 2 次（首次 + 1 次重试）
XSR_BACKOFF = 0.7           # 退避基数（秒）；第 n 次失败后等 XSR_BACKOFF * n
XSR_FUNDING_RATIO = 0.5     # 资金费率：相对差 > 50% 判分歧
XSR_FUNDING_ABS = 0.0001    # 资金费率：绝对差 > 0.0001（分数口径）= 0.01 个百分点，也判分歧
XSR_OI_RATIO = 0.25         # 未平仓量（美元名义）：相对差 > 25% 判分歧
XSR_TAKER_RATIO = 0.05      # 主动买卖比：相对差 > 5% 判分歧

CROSS_DISCIPLINE = (
    "跨所绝对值不可直接横比：合约张数/单位定义、费率结构与参与者结构各家不同，"
    "本对比只用于判断两边是否指向同一方向，不得据此下绝对强弱结论。")

SOURCES = {
    "fear_greed": FNG_URL,
    "binance.funding_current": FAPI + "/fapi/v1/premiumIndex?symbol=<SYM>",
    "binance.funding_history": FAPI + "/fapi/v1/fundingRate?symbol=<SYM>&limit=30",
    "binance.open_interest_current": FAPI + "/fapi/v1/openInterest?symbol=<SYM>",
    "binance.open_interest_history": FAPI + "/futures/data/openInterestHist?symbol=<SYM>&period=1d&limit=8",
    "binance.long_short_ratio": FAPI + "/futures/data/globalLongShortAccountRatio?symbol=<SYM>&period=1d&limit=30",
    "binance.taker_buy_sell": FAPI + "/futures/data/takerlongshortRatio?symbol=<SYM>&period=1d&limit=30",
    "okx.funding_current": OKX_API + "/public/funding-rate?instId=<CCY-USDT-SWAP>",
    "okx.funding_history": OKX_API + "/public/funding-rate-history?instId=<CCY-USDT-SWAP>&limit=30",
    "okx.open_interest_current": OKX_API + "/public/open-interest?instType=SWAP&instId=<CCY-USDT-SWAP>",
    "okx.taker_buy_sell": OKX_API + "/rubik/stat/taker-volume?ccy=<CCY>&instType=CONTRACTS&period=1D",
    "okx.long_short_ratio": "（不存在：OKX 未公开账户多空比接口，该维度降级为 null）",
}


# ───────────────────────────── 取数 ─────────────────────────────
def http_get_json(url, timeout=HTTP_TIMEOUT):
    """GET 一个 JSON 接口。返回 (data, err)；err 非 None 时 data 必为 None。"""
    try:
        req = Request(url, headers=HTTP_HEADERS, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw), None
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:120]
        except Exception:
            pass
        geo = "（合规地域限制 Binance 451）" if e.code == 451 and "restricted location" in body else ""
        return None, "HTTPError %s%s" % (e.code, geo)
    except URLError as e:
        return None, "URLError %s" % (getattr(e, "reason", e),)
    except json.JSONDecodeError:
        return None, "JSONDecodeError(响应非 JSON，通常是网关拦截页)"
    except Exception as e:  # 超时/连接重置/DNS 等，一律降级
        return None, "%s: %s" % (type(e).__name__, str(e)[:80])


def fetch_with_retry(url, timeout=HTTP_TIMEOUT, attempts=XSR_ATTEMPTS):
    """http_get_json 的**重试包装**，刻意不去改 http_get_json 本身。

    为什么需要：本机走 Clash 代理且**出口节点在轮换**。2026-09-30 实测 6 轮重复探测，
    binance_spot / binance_fapi 各只有 3/6 成功、bybit 4/6、OKX 对 Python 稳定 403。
    也就是说「单次取数失败」往往只是**节点问题**而不是接口下线，所以给跨源对比
    一次短退避重试；用尽仍失败才记 null + warning（失败绝不静默吞掉）。
    返回 (data, err, attempts_used)；err 非 None 时 data 必为 None。
    """
    n = max(1, int(attempts or 1))
    err = "未发起请求"
    for i in range(n):
        data, e = http_get_json(url, timeout=timeout)
        if e is None:
            return data, None, i + 1
        err = e
        if i < n - 1:
            time.sleep(XSR_BACKOFF * (i + 1))
    return None, err, n


def _binance_err(data):
    """Binance 出错时会返回 {"code":-1121,"msg":"Invalid symbol."} 而非 HTTP 错误码。"""
    if isinstance(data, dict) and "code" in data and "msg" in data:
        return "Binance code=%s msg=%s" % (data.get("code"), data.get("msg"))
    return None


def _okx_err(data):
    """OKX 出错时返回 {"code":"1","msg":...}。"""
    if isinstance(data, dict) and str(data.get("code")) not in ("0", "None"):
        return "OKX code=%s msg=%s" % (data.get("code"), data.get("msg"))
    return None


def _fnum(v):
    """字符串/数字 → float，失败返回 None（不返回 0，不编造）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf 视为无效
        return None
    return f


def _to_int(v):
    f = _fnum(v)
    return None if f is None else int(f)


def to_okx_inst(symbol):
    """BTCUSDT → BTC-USDT-SWAP；无法映射的币种返回 None（该币无 OKX 永续）。"""
    s = (symbol or "").upper()
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return "%s-%s-SWAP" % (s[:-len(quote)], quote)
    return None


def detect_provider(probe_symbol, timeout=HTTP_TIMEOUT):
    """探一次 Binance；不可达则降级 OKX。返回 (provider, note)。"""
    data, err = http_get_json("%s/fapi/v1/premiumIndex?symbol=%s" % (FAPI, probe_symbol),
                              timeout=timeout)
    if err is None and _binance_err(data) is None:
        return BINANCE, "Binance fapi 可用（数据源 = Binance USDT 永续）"
    reason = err or "Binance 返回错误体"
    return OKX, "Binance fapi 不可用：%s → 已降级到 OKX 公共接口（数据源 = OKX USDT 永续，" \
                "维度少于 Binance：无账户多空比、无 OI 历史）" % reason


# ───────────────────────────── 统计 ─────────────────────────────
def percentile_rank(value, samples):
    """value 在 samples 中的分位（0-100，inclusive 计数法）。样本不足返回 None。"""
    if value is None or not samples:
        return None
    le = sum(1 for s in samples if s <= value)
    return round(le * 100.0 / len(samples), 1)


def change_vs_7d(newest, series, idx=CHANGE_7D_IDX):
    """倒序序列里 newest - series[idx]。样本不够则 None。"""
    if newest is None or series is None or len(series) <= idx:
        return None
    old = series[idx]
    if old is None:
        return None
    return round(newest - old, 6)


# ─────────────────────────── 恐惧贪婪（全局） ───────────────────────────
def parse_fear_greed(timeout=HTTP_TIMEOUT):
    """恐惧贪婪指数——全局指标，不按 symbol 区分，故只取一次。"""
    out = {"value": None, "classification": None, "change_7d": None,
           "scope": "global(bitcoin-based, 全市场共用，非按 symbol 单独计算)",
           "as_of_date": None, "samples": 0, "source": "alternative.me/fng"}
    data, err = http_get_json(FNG_URL, timeout=timeout)
    if err:
        out["error"] = err
        return out
    meta_err = (data or {}).get("metadata", {}).get("error")
    if meta_err:
        out["error"] = "API metadata.error: %s" % meta_err
        return out
    rows = (data or {}).get("data") or []
    vals = [v for v in (_fnum(r.get("value")) for r in rows) if v is not None]
    if not vals:
        out["error"] = "响应无有效 data 点"
        return out
    out["samples"] = len(vals)
    out["value"] = int(vals[0])
    out["classification"] = rows[0].get("value_classification")
    ts = _to_int(rows[0].get("timestamp"))
    if ts is not None:
        out["as_of_date"] = datetime.fromtimestamp(ts, TZ8).strftime("%Y-%m-%d")
    if len(vals) > CHANGE_7D_IDX:
        out["change_7d"] = int(vals[0]) - int(vals[CHANGE_7D_IDX])
    else:
        out["error_note"] = "样本 %d 条 < 8，change_7d 置 null" % len(vals)
    return out


# ─────────────────────────── 资金费率 ───────────────────────────
def _finalize_funding(out, series, warn, sym):
    """共用尾部：均值 / 分位 / 年化 / 拥挤度。series 为升序历史。"""
    if series:
        out["samples"] = len(series)
        out["mean_30"] = round(statistics.fmean(series), 8)
        out["last_settled"] = series[-1]      # 最近一笔已结算费率
        if out["last"] is None:               # 实时接口失败时退回已结算值
            out["last"] = series[-1]
            out["last_source"] = out["last_source"] or "历史末条(已结算)"
    if out["last"] is not None:
        p = percentile_rank(out["last"], series)
        out["percentile_30d"] = p
        out["annualized_last"] = round(
            out["last"] * FUNDINGS_PER_DAY * DAYS_PER_YEAR * 100.0, 2)
        if p is not None:
            if p >= CROWD_LONG_PCT:
                out["crowded"], out["crowd_side"] = True, "long"
            elif p <= CROWD_SHORT_PCT:
                out["crowded"], out["crowd_side"] = True, "short"
            else:
                out["crowded"], out["crowd_side"] = False, None
    else:
        warn.append("%s: 资金费率全部取不到 → last/分位/年化置 null" % sym)
    return out


def parse_funding_binance(symbol, timeout):
    out = {"last": None, "last_source": None, "last_settled": None,
           "mean_30": None, "percentile_30d": None, "annualized_last": None,
           "crowded": None, "crowd_side": None, "samples": 0,
           "next_funding_time": None, "mark_price": None, "provider": BINANCE}
    warn = []
    cur, err = http_get_json("%s/fapi/v1/premiumIndex?symbol=%s" % (FAPI, symbol), timeout)
    if err:
        warn.append("premiumIndex 取不到 (%s)" % err)
    else:
        berr = _binance_err(cur)
        if berr:
            warn.append("premiumIndex: %s" % berr)
        else:
            out["last"] = _fnum((cur or {}).get("lastFundingRate"))
            out["last_source"] = "premiumIndex.lastFundingRate(当前/预估)"
            out["mark_price"] = _fnum((cur or {}).get("markPrice"))
            out["next_funding_time"] = _to_int((cur or {}).get("nextFundingTime"))
            if out["last"] is None:
                warn.append("premiumIndex.lastFundingRate 无法解析为数字")

    hist, err = http_get_json("%s/fapi/v1/fundingRate?symbol=%s&limit=%d"
                              % (FAPI, symbol, FUNDING_LIMIT), timeout)
    series = []
    if err:
        warn.append("fundingRate 历史不可用 (%s) → mean_30/分位/拥挤度置 null" % err)
    elif not isinstance(hist, list) or not hist:
        warn.append("fundingRate 历史为空 → 分位置 null")
    else:
        series = sorted(v for v in (_fnum(r.get("fundingRate")) for r in hist) if v is not None)
        if not series:
            warn.append("fundingRate 历史无可解析样本 → 分位置 null")
    return _finalize_funding(out, series, warn, symbol), warn


def parse_funding_okx(symbol, timeout):
    inst = to_okx_inst(symbol)
    out = {"last": None, "last_source": None, "last_settled": None,
           "mean_30": None, "percentile_30d": None, "annualized_last": None,
           "crowded": None, "crowd_side": None, "samples": 0,
           "next_funding_time": None, "mark_price": None, "provider": OKX}
    warn = []
    if not inst:
        warn.append("%s: 无法映射为 OKX 永续合约（无 USDT/USDC 永续）→ 资金费率置 null" % symbol)
        return out, warn

    cur, err = http_get_json("%s/public/funding-rate?instId=%s" % (OKX_API, inst), timeout)
    if err:
        warn.append("OKX funding-rate 取不到 (%s)" % err)
    else:
        oerr = _okx_err(cur)
        d0 = ((cur or {}).get("data") or [{}])[0] if isinstance(cur, dict) else {}
        if oerr and not d0.get("fundingRate"):
            warn.append("OKX funding-rate: %s" % oerr)
        else:
            out["last"] = _fnum(d0.get("fundingRate"))
            out["last_source"] = "OKX public/funding-rate(当期/预估)"
            out["next_funding_time"] = _to_int(d0.get("fundingTime"))
            if out["last"] is None:
                warn.append("OKX fundingRate 无法解析为数字")

    hist, err = http_get_json("%s/public/funding-rate-history?instId=%s&limit=%d"
                              % (OKX_API, inst, FUNDING_LIMIT), timeout)
    series = []
    if err:
        warn.append("OKX funding-rate-history 不可用 (%s) → mean_30/分位/拥挤度置 null" % err)
    else:
        # OKX 统一包一层 {"code":"0","data":[...]}，必须先剥掉再判断
        hrows = hist.get("data") if isinstance(hist, dict) else None
        if _okx_err(hist) or not hrows:
            warn.append("OKX funding-rate-history: %s" % (_okx_err(hist) or "空 data"))
        else:
            series = sorted(v for v in (_fnum(r.get("fundingRate")) for r in hrows) if v is not None)
            if not series:
                warn.append("OKX funding-rate-history 无可解析样本 → 分位置 null")
    return _finalize_funding(out, series, warn, symbol), warn


# ─────────────────────────── 未平仓量 ───────────────────────────
def parse_open_interest_binance(symbol, timeout):
    out = {"last": None, "unit": "contracts(base units，非美元)",
           "last_value_usd": None, "change_7d": None, "change_7d_pct": None,
           "change_7d_source": "unavailable", "sample_days": None, "provider": BINANCE}
    warn = []
    cur, err = http_get_json("%s/fapi/v1/openInterest?symbol=%s" % (FAPI, symbol), timeout)
    if err:
        warn.append("openInterest 取不到 (%s) → last 置 null" % err)
    else:
        berr = _binance_err(cur)
        if berr:
            warn.append("openInterest: %s" % berr)
        else:
            out["last"] = _fnum((cur or {}).get("openInterest"))

    hist, err = http_get_json("%s/futures/data/openInterestHist?symbol=%s&period=1d&limit=%d"
                              % (FAPI, symbol, OI_HIST_LIMIT), timeout)
    series = []
    if err:
        warn.append("openInterestHist 不可用 (%s) → change_7d 置 null"
                    "（不用两次即时快照取差：相隔数秒的差是噪声，不是变化率）" % err)
    else:
        rows = hist if isinstance(hist, list) else []
        series = [v for v in (_fnum(r.get("sumOpenInterest")) for r in rows) if v is not None]
        if len(series) >= 2:
            out["sample_days"] = len(series)
            newest, oldest = series[-1], series[0]
            out["change_7d"] = round(newest - oldest, 4)
            if oldest > 0:
                out["change_7d_pct"] = round((newest - oldest) / oldest * 100.0, 2)
            out["change_7d_source"] = "openInterestHist(period=1d, %d 根, 首尾差)" % len(series)
        else:
            warn.append("openInterestHist 样本不足（%d 根）→ change_7d 置 null" % len(series))
        if rows:
            usd = _fnum(rows[-1].get("sumOpenInterestValue"))
            if usd is not None:
                out["last_value_usd"] = usd
    return out, warn


def parse_open_interest_okx(symbol, timeout):
    inst = to_okx_inst(symbol)
    out = {"last": None, "unit": "contracts(币本位张数，非美元)",
           "last_value_usd": None, "change_7d": None, "change_7d_pct": None,
           "change_7d_source": "unavailable(OKX 未公开 OI 历史接口，需鉴权)",
           "sample_days": None, "provider": OKX}
    warn = []
    if not inst:
        warn.append("%s: 无法映射为 OKX 永续合约 → 未平仓量置 null" % symbol)
        return out, warn
    data, err = http_get_json("%s/public/open-interest?instType=SWAP&instId=%s"
                              % (OKX_API, inst), timeout)
    if err:
        warn.append("OKX open-interest 取不到 (%s) → last 置 null" % err)
        return out, warn
    oerr = _okx_err(data)
    d0 = ((data or {}).get("data") or [{}])[0] if isinstance(data, dict) else {}
    if oerr and not d0.get("oi"):
        warn.append("OKX open-interest: %s" % oerr)
        return out, warn
    out["last"] = _fnum(d0.get("oiCcy")) or _fnum(d0.get("oi"))
    out["last_value_usd"] = _fnum(d0.get("oiUsd"))
    warn.append("%s: OKX 未公开未平仓量历史（open-interest-history 需 API key）"
                " → open_interest.change_7d 置 null（不拿两次即时快照取差凑数）" % symbol)
    return out, warn


# ─────────────────────────── 账户多空比 ───────────────────────────
def parse_long_short(symbol, provider, timeout):
    out = {"last": None, "change_7d": None, "long_account": None,
           "short_account": None, "samples": 0, "provider": provider,
           "caveat": "账户数占比口径，非持仓市值占比"}
    if provider != BINANCE:
        return out, ["%s: %s 模式无公开的账户多空比接口（仅 Binance futures/data 提供）"
                     " → long_short_ratio 置 null，不用其他口径顶替" % (symbol, provider)]

    data, err = http_get_json(
        "%s/futures/data/globalLongShortAccountRatio?symbol=%s&period=1d&limit=%d"
        % (FAPI, symbol, RATIO_LIMIT), timeout)
    warn = []
    if err:
        warn.append("globalLongShortAccountRatio 不可用 (%s) → last/change_7d 置 null" % err)
        return out, warn
    rows = data if isinstance(data, list) else []
    if not rows:
        warn.append("globalLongShortAccountRatio 返回空数组 → 置 null")
        return out, warn
    out["samples"] = len(rows)
    out["last"] = _fnum(rows[0].get("longShortRatio"))
    out["long_account"] = _fnum(rows[0].get("longAccount"))
    out["short_account"] = _fnum(rows[0].get("shortAccount"))
    series = [_fnum(r.get("longShortRatio")) for r in rows]
    out["change_7d"] = change_vs_7d(out["last"], series)
    if out["change_7d"] is None and out["last"] is not None:
        warn.append("多空比 7 日前样本缺失（%d 根）→ change_7d 置 null" % len(rows))
    return out, warn


# ─────────────────────────── 主动买卖比 ───────────────────────────
def parse_taker_binance(symbol, timeout):
    out = {"last": None, "buy_vol": None, "sell_vol": None, "net_side": None,
           "net_buy_vol": None, "change_7d": None, "samples": 0, "provider": BINANCE}
    warn = []
    data, err = http_get_json(
        "%s/futures/data/takerlongshortRatio?symbol=%s&period=1d&limit=%d"
        % (FAPI, symbol, RATIO_LIMIT), timeout)
    if err:
        warn.append("takerlongshortRatio 不可用 (%s) → last/net_side 置 null" % err)
        return out, warn
    rows = data if isinstance(data, list) else []
    if not rows:
        warn.append("takerlongshortRatio 返回空数组 → 置 null")
        return out, warn
    out["samples"] = len(rows)
    out["last"] = _fnum(rows[0].get("buySellRatio"))
    out["buy_vol"] = _fnum(rows[0].get("buyVol"))
    out["sell_vol"] = _fnum(rows[0].get("sellVol"))
    if out["last"] is not None:
        out["net_side"] = "buy" if out["last"] > 1.0 else ("sell" if out["last"] < 1.0 else None)
    if out["buy_vol"] is not None and out["sell_vol"] is not None:
        out["net_buy_vol"] = round(out["buy_vol"] - out["sell_vol"], 4)
    out["change_7d"] = change_vs_7d(
        out["last"], [_fnum(r.get("buySellRatio")) for r in rows])
    return out, warn


def parse_taker_okx(symbol, timeout):
    inst = to_okx_inst(symbol)
    ccy = inst.split("-")[0] if inst else None
    out = {"last": None, "buy_vol": None, "sell_vol": None, "net_side": None,
           "net_buy_vol": None, "change_7d": None, "samples": 0, "provider": OKX}
    if not ccy:
        return out, ["%s: 无法映射为 OKX 永续合约 → 主动买卖量置 null" % symbol]
    data, err = http_get_json(
        "%s/rubik/stat/taker-volume?ccy=%s&instType=CONTRACTS&period=1D" % (OKX_API, ccy),
        timeout)
    warn = []
    if err:
        warn.append("OKX taker-volume 不可用 (%s) → last/net_side 置 null" % err)
        return out, warn
    rows = data.get("data") if isinstance(data, dict) else None
    if _okx_err(data) or not rows:
        warn.append("OKX taker-volume: %s" % (_okx_err(data) or "空数组"))
        return out, warn
    # OKX 行格式固定为 [ts, sellVol, buyVol]，倒序
    parsed = []
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 3:
            parsed.append((_to_int(row[0]), _fnum(row[1]), _fnum(row[2])))
    parsed = [p for p in parsed if p[1] is not None and p[2] is not None]
    if not parsed:
        warn.append("OKX taker-volume 无可解析样本 → 置 null")
        return out, warn
    out["samples"] = len(parsed)
    _, sell, buy = parsed[0]
    out["sell_vol"], out["buy_vol"] = sell, buy
    if sell > 0:
        out["last"] = round(buy / sell, 4)
        out["net_side"] = "buy" if buy > sell else ("sell" if buy < sell else None)
    out["net_buy_vol"] = round(buy - sell, 4)
    if len(parsed) > CHANGE_7D_IDX and parsed[0][1] > 0:
        s0, b0 = parsed[CHANGE_7D_IDX][1], parsed[CHANGE_7D_IDX][2]
        if s0 > 0:
            out["change_7d"] = round(b0 / s0 - out["last"], 4)
    return out, warn


# ─────────────────────────── 判读 ───────────────────────────
def build_verdict(fg, funding, oi, lsr, taker):
    """把可用字段拼成一句中文判读。取不到的部分直接不写，绝不用占位数字凑句。"""
    parts = []

    last = funding.get("last")
    if last is not None:
        seg = "资金费率 %.4f%%" % (last * 100.0)
        if funding.get("annualized_last") is not None:
            seg += "（年化 %.1f%%）" % funding["annualized_last"]
        p = funding.get("percentile_30d")
        if p is not None:
            seg += " 处 %g 分位" % p
            if p >= CROWD_LONG_PCT:
                seg += "，多头拥挤，追多需谨慎"
            elif p <= CROWD_SHORT_PCT:
                seg += "，空头拥挤，逼空/反弹风险上升"
            else:
                seg += "，未见极端拥挤"
        else:
            seg += "（历史样本不足，无法判断分位）"
        parts.append(seg)

    lsr_last = lsr.get("last")
    if lsr_last is not None:
        seg = "全市场账户多空比 %.3f" % lsr_last
        if lsr.get("long_account") is not None:
            seg += "（多头账户 %.1f%%）" % (lsr["long_account"] * 100.0)
        if lsr.get("change_7d") is not None:
            seg += "，7 日 %+.3f" % lsr["change_7d"]
        seg += "（账户数口径）"
        parts.append(seg)

    t = taker.get("last")
    if t is not None:
        seg = "主动买卖比 %.3f" % t
        if taker.get("net_side") == "buy":
            seg += "，主动买盘占优"
        elif taker.get("net_side") == "sell":
            seg += "，主动卖盘占优"
        else:
            seg += "，买卖力量持平"
        parts.append(seg)

    oi_pct = oi.get("change_7d_pct")
    if oi_pct is not None:
        parts.append("未平仓 7 日 %+.1f%%（%s）" % (oi_pct, "增仓" if oi_pct > 0 else "减仓"))
    elif oi.get("last") is not None:
        parts.append("未平仓 7 日变化不可得")

    if fg and fg.get("value") is not None:
        seg = "全市场恐惧贪婪 %d（%s）" % (fg["value"], fg.get("classification") or "未分类")
        if fg.get("change_7d") is not None:
            seg += "，7 日 %+d" % fg["change_7d"]
        parts.append(seg)

    if not parts:
        return "衍生品数据全部不可用，无法给出情绪判断（见 warnings）"
    return "；".join(parts) + "。"


# ─────────────────── 跨所/跨源矛盾检测（--cross-source） ───────────────────
# 只挑**双方都有、口径一致**的三个指标：资金费率 / 未平仓美元名义 / 主动买卖比。
# 账户多空比（Binance 独有）、30 日分位/拥挤度（各家历史口径不同）**一律不参与**跨源比较：
# 它们衡量的是各自样本内的相对位置，跨所横比没有意义。
XSR_METRICS = ("funding", "open_interest_usd", "taker_ratio")

XSR_SPECS = {
    "funding": {
        "label": "资金费率(8h)",
        "unit": "percent(每 8 小时费率百分比；两家都取「当前/预估」口径，量纲一致)",
        "ratio": XSR_FUNDING_RATIO,
        "abs": XSR_FUNDING_ABS,
        "rule": "相对差 > %g%% 或绝对差 > %.2f 个百分点" % (XSR_FUNDING_RATIO * 100.0,
                                                          XSR_FUNDING_ABS * 100.0),
        "why": "典型资金费率只有 0.005%~0.05% 量级，两家差到 50% 的相对幅度才说明「指向」不同；"
               "同时给 0.01 个百分点（1bp）的绝对下限，避免费率整体极低时（都在 0.001% 附近）"
               "把微小相对噪声误判成分歧。",
        "note": "资金费率跨所差异大（%s）→ 两边对「多头/空头谁在付费」的指向不一致，"
                "该结论不可单源采信",
    },
    "open_interest_usd": {
        "label": "未平仓(美元名义)",
        "unit": "usd(未平仓美元名义；Binance sumOpenInterestValue vs OKX oiUsd)",
        "ratio": XSR_OI_RATIO,
        "abs": None,
        # ⚠ v1.5.9 修正：本项**只作参考，不进入可行动分歧清单**。
        # 实测 Binance $7.73B vs OKX $2.37B（+69.3%）——两所客户体量差异是**结构性**的，
        # 25% 阈值几乎每次必然命中，于是 divergences 永远非空、失去筛选力，
        # 用户会养成"看到分歧就忽略"的习惯，那才是真正的信息损失。
        # 关键在于：比的是**存量水位**（由各所体量决定，几乎必然不同），
        # 有决策价值的是**变化方向**——而 OKX 未公开 OI 历史，根本没法比变化率。
        # 故：照常算出并展示，但 actionable=False，只进 informational 列表。
        "actionable": False,
        "rule": "相对差 > %g%%（**仅供参考，不计入分歧清单**）" % (XSR_OI_RATIO * 100.0),
        "why": "持仓量的跨所绝对值主要由各所客户体量决定，天然差很多，所以阈值放宽到 25%；"
               "但要记住：这比的是**存量水位**而不是**变化方向**（OKX 未公开 OI 历史，"
               "根本没法比变化率），命中分歧只说明「两所杠杆量级不同」。"
               "因此本项标 actionable=False —— 几乎必然超阈值，若计入分歧清单会让"
               "真正的资金费率/主动买卖分歧被噪声淹没。",
        "note": "未平仓美元名义跨所差异大（%s）→ 两所杠杆总量本就存在量级差"
                "（Binance 通常远大于 OKX，且 OKX 未公开 OI 历史、无法比变化率），"
                "该差异只说明「资金池体量不同」，推不出「哪边在加仓、哪边在减仓」；"
                "**本项不计入分歧清单**。增/减仓方向仍以单源 open_interest.change_7d 为准",
    },
    "taker_ratio": {
        "label": "主动买卖比",
        "unit": "ratio(主动买量/主动卖量，无量纲，两家同口径)",
        "ratio": XSR_TAKER_RATIO,
        "abs": None,
        "rule": "相对差 > %g%%" % (XSR_TAKER_RATIO * 100.0),
        "why": "这是三个指标里**唯一无量纲**的，比值天然接近 1，5% 的相对差已经足够改变"
               "「主动买占优还是主动卖占优」的判断，故阈值取最严的 5%。",
        "note": "主动买卖比跨所差异大（%s）→ 两边对「主动买盘还是主动卖盘占优」的指向不一致，"
                "该结论不可单源采信",
    },
}


def _xsrc_metric(key, a, b):
    """构造单项跨源对比记录。

    铁律：任一侧为 None → comparable=False，**绝不判分歧**。
    单边数字不编对比——比不出来就说比不出来。
    """
    spec = XSR_SPECS[key]
    rec = {"label": spec["label"], "unit": spec["unit"], "rule": spec["rule"],
           "actionable": spec.get("actionable", True),
           "binance": a, "okx": b, "delta_abs": None, "delta_ratio": None,
           "delta_ratio_pct": None, "comparable": False, "divergent": False,
           "reason": None}
    if a is None or b is None:
        miss = "、".join(n for n, v in (("Binance", a), ("OKX", b)) if v is None)
        rec["reason"] = "%s：%s 无值 → 不做跨源比较，不判分歧" % (spec["label"], miss)
        return rec

    diff = a - b
    base = max(abs(a), abs(b))
    ratio = (abs(diff) / base) if base > 0 else None
    rec["delta_abs"] = round(diff, 8)
    if ratio is not None:
        rec["delta_ratio"] = round(ratio, 6)          # 展示值
        rec["delta_ratio_pct"] = round(ratio * 100.0, 2)
    rec["comparable"] = True
    if ratio is None:
        rec["reason"] = "%s：两边都是 0（或绝对值相同）→ 差值无意义，不判分歧" % spec["label"]
        return rec

    # 判定一律用**未取整**的原值，展示才取整：否则 0.4999996 被四舍五入成 0.5 会误判
    hits = []
    if ratio > spec["ratio"]:
        hits.append("相对差 %.1f%% > 阈值 %.0f%%" % (ratio * 100.0, spec["ratio"] * 100.0))
    if spec["abs"] is not None and abs(diff) > spec["abs"]:
        hits.append("绝对差 %.4f 个百分点 > 阈值 %.4f 个百分点" % (abs(diff) * 100.0,
                                                                 spec["abs"] * 100.0))
    rec["divergent"] = bool(hits)
    rec["reason"] = ("分歧：" + "；".join(hits)) if hits else ("未超阈值（%s）" % spec["rule"])
    return rec


def _xs_binance_side(symbol, timeout):
    """Binance 侧三个跨源可比指标。任一失败只置 None + warning，不抛异常。"""
    out = {"funding": None, "open_interest_usd": None, "taker_ratio": None}
    warn = []

    data, err, tries = fetch_with_retry(
        "%s/fapi/v1/premiumIndex?symbol=%s" % (FAPI, symbol), timeout)
    if err:
        warn.append("跨源/Binance premiumIndex 取不到（%s，已试 %d 次）" % (err, tries))
    else:
        berr = _binance_err(data)
        if berr:
            warn.append("跨源/Binance premiumIndex: %s" % berr)
        else:
            out["funding"] = _fnum((data or {}).get("lastFundingRate"))
            if out["funding"] is None:
                warn.append("跨源/Binance premiumIndex.lastFundingRate 无法解析为数字 → 置 null")

    data, err, tries = fetch_with_retry(
        "%s/futures/data/openInterestHist?symbol=%s&period=1d&limit=%d"
        % (FAPI, symbol, OI_HIST_LIMIT), timeout)
    if err:
        warn.append("跨源/Binance openInterestHist 取不到（%s，已试 %d 次）→ 美元名义置 null"
                    % (err, tries))
    else:
        rows = data if isinstance(data, list) else []
        out["open_interest_usd"] = _fnum(rows[-1].get("sumOpenInterestValue")) if rows else None
        if out["open_interest_usd"] is None:
            warn.append("跨源/Binance openInterestHist 无 sumOpenInterestValue → 美元名义置 null")

    data, err, tries = fetch_with_retry(
        "%s/futures/data/takerlongshortRatio?symbol=%s&period=1d&limit=%d"
        % (FAPI, symbol, RATIO_LIMIT), timeout)
    if err:
        warn.append("跨源/Binance takerlongshortRatio 取不到（%s，已试 %d 次）" % (err, tries))
    else:
        rows = data if isinstance(data, list) else []
        out["taker_ratio"] = _fnum(rows[0].get("buySellRatio")) if rows else None
        if out["taker_ratio"] is None:
            warn.append("跨源/Binance takerlongshortRatio 无 buySellRatio → 置 null")
    return out, warn


def _xs_okx_side(symbol, timeout):
    """OKX 侧三个跨源可比指标。任一失败只置 None + warning，不抛异常。"""
    out = {"funding": None, "open_interest_usd": None, "taker_ratio": None}
    inst = to_okx_inst(symbol)
    ccy = inst.split("-")[0] if inst else None
    if not inst:
        return out, ["跨源/OKX：%s 无法映射为 OKX 永续合约 → OKX 侧三项全部置 null" % symbol]
    warn = []

    data, err, tries = fetch_with_retry(
        "%s/public/funding-rate?instId=%s" % (OKX_API, inst), timeout)
    if err:
        warn.append("跨源/OKX funding-rate 取不到（%s，已试 %d 次）" % (err, tries))
    else:
        oerr = _okx_err(data)
        d0 = ((data or {}).get("data") or [{}])[0] if isinstance(data, dict) else {}
        out["funding"] = _fnum(d0.get("fundingRate"))
        if out["funding"] is None:
            warn.append("跨源/OKX funding-rate: %s" % (oerr or "fundingRate 无法解析为数字"))

    data, err, tries = fetch_with_retry(
        "%s/public/open-interest?instType=SWAP&instId=%s" % (OKX_API, inst), timeout)
    if err:
        warn.append("跨源/OKX open-interest 取不到（%s，已试 %d 次）→ 美元名义置 null" % (err, tries))
    else:
        oerr = _okx_err(data)
        d0 = ((data or {}).get("data") or [{}])[0] if isinstance(data, dict) else {}
        out["open_interest_usd"] = _fnum(d0.get("oiUsd"))
        if out["open_interest_usd"] is None:
            warn.append("跨源/OKX open-interest: %s" % (oerr or "无 oiUsd 字段"))

    data, err, tries = fetch_with_retry(
        "%s/rubik/stat/taker-volume?ccy=%s&instType=CONTRACTS&period=1D" % (OKX_API, ccy), timeout)
    if err:
        warn.append("跨源/OKX taker-volume 取不到（%s，已试 %d 次）" % (err, tries))
    else:
        rows = data.get("data") if isinstance(data, dict) else None
        first = rows[0] if isinstance(rows, list) and rows else None
        if _okx_err(data) or not isinstance(first, (list, tuple)) or len(first) < 3:
            warn.append("跨源/OKX taker-volume: %s" % (_okx_err(data) or "首行不可解析"))
        else:
            sell, buy = _fnum(first[1]), _fnum(first[2])
            if sell is None or sell <= 0 or buy is None:
                warn.append("跨源/OKX taker-volume 卖量不可用 → 置 null")
            else:
                out["taker_ratio"] = buy / sell    # 不取整：阈值判定要精确
    return out, warn


def _xsrc_verdict(metrics, divergences, informational=None, symbol=None):
    """一句中文结论。取不到就说取不到，绝不用占位话术糊过去。

    `informational` 里的项（跨所绝对额）**不进结论**——只作为附注，
    否则结构性体量差会让每一轮都判"分歧"，用户随即学会无视它。
    """
    comparable = [k for k in XSR_METRICS if metrics[k]["comparable"]]
    if symbol is None:                       # 兼容旧调用签名
        symbol, informational = divergences, informational
    tail = ""
    if informational:
        tail = ("；另 %s 跨所绝对额差异较大，但属体量结构性差异（%s），"
                "**不计入分歧结论**——它推不出加仓/减仓方向"
                % ("、".join(d["label"] for d in informational),
                   "、".join(d["label"] for d in informational)))
    if not comparable:
        return ("%s 跨源对比无法进行：%d 个可比指标全部缺失或只有单边 → "
                "本轮不给出任何跨源一致性结论（宁可不判，也不编）%s"
                % (symbol, len(XSR_METRICS), tail))
    if not divergences:
        return ("%s 跨源一致性可接受：%d/%d 项可比指标均未超阈值（%s）→ "
                "单源结论可照用；注意这只说明两边同向，不说明哪边更强%s"
                % (symbol, len(comparable), len(XSR_METRICS),
                   "、".join(XSR_SPECS[k]["label"] for k in comparable), tail))
    return ("%s 跨源存在分歧：%s（%d/%d 项可比指标）→ 相关结论应标注「单源不确定」或降级处理；"
            "分歧不必然说明某一方错，很可能只是参与者结构不同%s"
            % (symbol, "、".join(d["label"] for d in divergences),
               len(divergences), len(comparable), tail))


def compare_sources(symbol, timeout=HTTP_TIMEOUT):
    """同时向 Binance 与 OKX 取**双方都有**的三个指标并排对比。

    · 任一源失败不抛异常：该项置 null + warning，绝不编数字；
    · 任一侧为 null → comparable=False，**绝不判分歧**（单边数字不编对比）；
    · 跨所绝对值不可横比（合约单位/参与者结构不同），只判断「是否指向同一方向」。
    """
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=2) as ex:      # 两侧并发，省一半墙钟时间
        fb = ex.submit(_xs_binance_side, symbol, timeout)
        fo = ex.submit(_xs_okx_side, symbol, timeout)
        b_vals, b_warn = fb.result()
        o_vals, o_warn = fo.result()
    warn = list(b_warn) + list(o_warn)                 # 固定顺序，便于回归比对

    metrics = {}
    for key in XSR_METRICS:
        metrics[key] = _xsrc_metric(key, b_vals.get(key), o_vals.get(key))

    divergences = []
    informational = []
    for key in XSR_METRICS:
        m = metrics[key]
        if not m["divergent"]:
            continue
        item = {
            "metric": key, "label": m["label"], "unit": m["unit"],
            "binance": m["binance"], "okx": m["okx"],
            "delta_abs": m["delta_abs"], "delta_ratio": m["delta_ratio"],
            "threshold": m["rule"],
            "note": XSR_SPECS[key]["note"] % m["reason"].replace("分歧：", ""),
        }
        # actionable=False 的项照常展示，但只进 informational——
        # 见 XSR_SPECS["open_interest_usd"]["why"]：跨所绝对额几乎必然超阈值，
        # 计入分歧清单会让真正的分歧被结构性噪声淹没。
        (divergences if m.get("actionable", True) else informational).append(item)

    compared = [k for k in XSR_METRICS if metrics[k]["comparable"]]
    if len(compared) < len(XSR_METRICS):
        warn.append("%s: 跨源对比只有 %d/%d 项可比较，其余为单边缺失（comparable=false，"
                    "未参与分歧判定）" % (symbol, len(compared), len(XSR_METRICS)))

    return {
        "symbol": symbol,
        "as_of": datetime.now(TZ8).isoformat(),
        "enabled": True,
        "metrics": metrics,
        "compared": compared,
        "divergences": divergences,
        "informational": informational,
        "informational_note": "informational=跨所绝对额差异（如未平仓美元名义），"
                               "由各所体量结构性决定，**不计入分歧结论**，仅供体量参考",
        "verdict": _xsrc_verdict(metrics, divergences, informational, symbol),
        "discipline": CROSS_DISCIPLINE,
        "warnings": warn,
        "elapsed_ms": int((time.time() - t0) * 1000),
    }


def _compare_one(symbol, timeout=HTTP_TIMEOUT):
    """单标的跨源对比的兜底包装：跨源异常绝不允许拖垮主流程。"""
    try:
        return compare_sources(symbol, timeout)
    except Exception as e:
        return {"symbol": symbol, "enabled": True, "metrics": {}, "compared": [],
                "divergences": [],
                "verdict": "跨源对比未预期异常 → 本轮不给出任何跨源结论",
                "discipline": CROSS_DISCIPLINE,
                "warnings": ["%s: 跨源对比未预期异常 %s: %s"
                             % (symbol, type(e).__name__, str(e)[:80])]}


def _compare_many(symbols, timeout=HTTP_TIMEOUT):
    """逐标的跨源对比；多标的并发，返回顺序与入参一致。"""
    syms = list(symbols)
    if len(syms) <= 1:
        return [_compare_one(syms[0], timeout)] if syms else []
    with ThreadPoolExecutor(max_workers=min(4, len(syms))) as ex:
        return list(ex.map(lambda s: _compare_one(s, timeout), syms))


# ─────────────────────────── 渲染 ───────────────────────────
def _f(v, spec="%.4f", dash="—"):
    return dash if v is None else (spec % v)


def _xsrc_fmt(key, v):
    """跨源表格的数值格式化：None 一律显示 —，绝不显示 0（0 是有含义的数）。"""
    if v is None:
        return "—"
    if key == "funding":
        return "%.4f%%" % (v * 100.0)
    if key == "open_interest_usd":
        return "$%.2fB" % (v / 1e9)
    return "%.4f" % v


def _xsrc_delta(key, m):
    if not m.get("comparable") or m.get("delta_ratio_pct") is None:
        return "—"
    if key == "funding":
        return "%+.4fpp / %+.1f%%" % (m["delta_abs"] * 100.0, m["delta_ratio_pct"])
    if key == "open_interest_usd":
        return "%+.1f%%" % m["delta_ratio_pct"]
    return "%+.4f / %+.1f%%" % (m["delta_abs"], m["delta_ratio_pct"])


def render_table(payload):
    rows = payload.get("rows") or []
    fg = payload.get("fear_greed") or {}
    L = []
    bar = "═" * 64
    L.append(bar)
    L.append(" 衍生品情绪 · %d 个标的 · schema %s · 耗时 %sms"
             % (len(rows), payload.get("schema"), payload.get("elapsed_ms")))
    L.append(" 数据源   %s" % (payload.get("provider_note") or "—"))
    if fg.get("value") is not None:
        L.append(" 全局恐惧贪婪 %d · %s · 7日 %s · 口径：比特币口径，全市场共用"
                 % (fg["value"], fg.get("classification") or "—",
                    ("%+d" % fg["change_7d"]) if fg.get("change_7d") is not None else "—"))
    else:
        L.append(" 全局恐惧贪婪 不可用（%s）" % (fg.get("error") or "未知原因"))
    L.append(bar)

    for r in rows:
        if r is None:
            continue
        fd, oi, lsr, tk = r["funding"], r["open_interest"], r["long_short_ratio"], r["taker_buy_sell"]
        last = fd.get("last")
        crowd = "未知（无历史分位）"
        if fd.get("crowd_side") == "long":
            crowd = "多头拥挤（≥%g 分位）" % CROWD_LONG_PCT
        elif fd.get("crowd_side") == "short":
            crowd = "空头拥挤（≤%g 分位）" % CROWD_SHORT_PCT
        elif fd.get("crowded") is False:
            crowd = "正常（未见极端拥挤）"
        L.append("")
        L.append(" ■ %s  [数据源 %s]" % (r.get("symbol"), r.get("provider")))
        L.append("   资金费率  %s   年化 %s   分位 %s   样本 %s"
                 % (_f(None if last is None else last * 100.0, "%.4f%%"),
                    _f(fd.get("annualized_last"), "%.2f%%"),
                    _f(fd.get("percentile_30d"), "%g"),
                    _f(fd.get("samples"), "%d")))
        L.append("   拥挤度    %s" % crowd)
        L.append("   未平仓    %s   名义 %s   7日 %s（来源 %s）"
                 % (_f(oi.get("last"), "%.2f"),
                    ("$%.2fB" % (oi["last_value_usd"] / 1e9)) if oi.get("last_value_usd") else "—",
                    _f(oi.get("change_7d_pct"), "%+.2f%%"),
                    oi.get("change_7d_source")))
        L.append("   账户多空  %s（多 %s / 空 %s） 7日 %s"
                 % (_f(lsr.get("last"), "%.4f"),
                    _f(None if lsr.get("long_account") is None else lsr["long_account"] * 100.0, "%.2f%%"),
                    _f(None if lsr.get("short_account") is None else lsr["short_account"] * 100.0, "%.2f%%"),
                    _f(lsr.get("change_7d"), "%+.4f")))
        L.append("   主动买卖  %s（%s） 净主动买入 %s"
                 % (_f(tk.get("last"), "%.4f"),
                    {"buy": "主动买占优", "sell": "主动卖占优", None: "持平/未知"}.get(tk.get("net_side"), "—"),
                    _f(tk.get("net_buy_vol"), "%.2f")))
        L.append("   判读      %s" % r.get("verdict"))
        for w in (r.get("warnings") or []):
            L.append("   [!] %s" % w)

    all_w = payload.get("warnings") or []
    if all_w:
        L.append("")
        L.append("─" * 64)
        L.append(" 降级/局限（%d 条，均为如实记录，无编造数字）：" % len(all_w))
        for w in all_w:
            L.append("   [!] %s" % w)

    xs = payload.get("cross_source")
    if xs:
        L.append("")
        L.append("─" * 64)
        L.append(" 跨源一致性 · Binance ↔ OKX（旁路校验，%d 个标的）"
                 "  单源结论仍来自 %s" % (len(xs), payload.get("provider") or "—"))
        L.append(" ⚠ %s" % CROSS_DISCIPLINE)
        for x in xs:
            if not x:
                continue
            L.append("")
            L.append(" ■ %s  可比 %d/%d"
                     % (x.get("symbol"), len(x.get("compared") or []), len(XSR_METRICS)))
            for key in XSR_METRICS:
                m = (x.get("metrics") or {}).get(key) or {}
                flag = ("分歧:是" if m.get("divergent")
                        else ("分歧:否" if m.get("comparable") else "不可比（单边缺失）"))
                L.append("   · %s：Binance %s | OKX %s | 差异 %s | %s"
                         % (m.get("label", key), _xsrc_fmt(key, m.get("binance")),
                            _xsrc_fmt(key, m.get("okx")), _xsrc_delta(key, m), flag))
                if m.get("reason"):
                    L.append("     依据  %s" % m["reason"])
            L.append("   结论  %s" % x.get("verdict"))
            for d in (x.get("divergences") or []):
                L.append("   [分歧] %s" % d.get("note"))
            for w in (x.get("warnings") or []):
                L.append("   [!] %s" % w)
    L.append("")
    L.append(" 纪律：情绪指标只作过滤器不作 alpha 来源——它说明「现在挤不挤」，"
             "不说明「该买什么、什么时候买」；入场仍以 K 线结构与流动性为准。")
    return "\n".join(L)


# ─────────────────────────── 主流程 ───────────────────────────
def analyze_one(symbol, fg, provider, timeout=HTTP_TIMEOUT):
    """取单个标的的全部衍生品情绪维度。任一源失败只记 warning，不抛异常。"""
    warnings = []
    t0 = time.time()
    if provider == OKX:
        funding, w1 = parse_funding_okx(symbol, timeout)
        oi, w2 = parse_open_interest_okx(symbol, timeout)
        taker, w4 = parse_taker_okx(symbol, timeout)
    else:
        funding, w1 = parse_funding_binance(symbol, timeout)
        oi, w2 = parse_open_interest_binance(symbol, timeout)
        taker, w4 = parse_taker_binance(symbol, timeout)
    lsr, w3 = parse_long_short(symbol, provider, timeout)
    for w in (w1, w2, w3, w4):
        warnings.extend(w)

    fg_row = {
        "value": fg.get("value"), "classification": fg.get("classification"),
        "change_7d": fg.get("change_7d"), "scope": fg.get("scope"),
    }
    if fg.get("value") is None:
        warnings.append("%s: 恐惧贪婪指数不可用 (%s) → fear_greed 三项置 null"
                        % (symbol, fg.get("error") or "未知原因"))

    return {
        "symbol": symbol,
        "as_of": datetime.now(TZ8).isoformat(),
        "provider": provider,
        "fear_greed": fg_row,
        "funding": funding,
        "open_interest": oi,
        "long_short_ratio": lsr,
        "taker_buy_sell": taker,
        "verdict": build_verdict(fg_row, funding, oi, lsr, taker),
        "warnings": warnings,
        "elapsed_ms": int((time.time() - t0) * 1000),
    }


def main():
    ap = argparse.ArgumentParser(
        description="衍生品情绪采集（恐惧贪婪/资金费率/未平仓/多空比/主动买卖，免费免 key）")
    ap.add_argument("--symbol", help="单个标的，如 BTCUSDT")
    ap.add_argument("--symbols", help="逗号分隔的多个标的，如 BTCUSDT,ETHUSDT")
    ap.add_argument("--out", default=None, help="JSON 输出路径（默认 <cwd>/kexi_out/derivs_sentiment.json）")
    ap.add_argument("--out-dir", default=None, help="产出目录（默认 <cwd>/kexi_out）")
    ap.add_argument("--timeout", type=int, default=HTTP_TIMEOUT, help="单请求超时秒数（默认 8）")
    ap.add_argument("--provider", choices=["auto", BINANCE, OKX], default="auto",
                    help="强制数据源；默认 auto=Binance 优先，不可达自动降级 OKX")
    ap.add_argument("--cross-source", action="store_true",
                    help="额外向 Binance 与 OKX 各取一遍三个可比指标并排对比并标出分歧"
                         "（默认关闭：会额外多打 6 个请求）")
    # 刻意**不**提供 --cross-source-only：下游卡片/报告直接消费 rows[].funding 等字段，
    # 只做跨源而不产出单源行会破坏既有契约；要改契约就得动 kexi-plugin.mjs，不在本轮范围。
    args = ap.parse_args()

    syms = []
    for chunk in (args.symbol, args.symbols):
        if chunk:
            syms += [s.strip().upper() for s in chunk.split(",") if s.strip()]
    syms = list(dict.fromkeys(syms))          # 去重且保序
    if not syms:
        print(json.dumps({"ok": False, "error": "需要 --symbol 或 --symbols"},
                         ensure_ascii=False), flush=True)
        return 1

    out_dir = args.out_dir or os.path.join(os.getcwd(), "kexi_out")
    out_path = args.out or os.path.join(out_dir, "derivs_sentiment.json")

    t0 = time.time()
    if args.provider == "auto":
        provider, provider_note = detect_provider(syms[0], timeout=args.timeout)
    else:
        provider = args.provider
        provider_note = "由 --provider 强制指定：%s" % provider
    if provider == OKX:
        provider_note += "（降级：维度少于 Binance，无账户多空比、无 OI 历史序列）"

    # 恐惧贪婪是全局指标，N 个 symbol 也只取一次
    fg = parse_fear_greed(timeout=args.timeout)
    rows = []
    for s in syms:
        try:
            rows.append(analyze_one(s, fg, provider, timeout=args.timeout))
        except Exception as e:   # 兜底：单币失败不拖垮整批
            rows.append({"symbol": s, "provider": provider,
                         "as_of": datetime.now(TZ8).isoformat(),
                         "verdict": "该标的取数异常，无法判断",
                         "warnings": ["%s: 未预期异常 %s: %s" % (s, type(e).__name__, str(e)[:80])]})
    elapsed = int((time.time() - t0) * 1000)

    xs_rows = None
    if args.cross_source:
        xs_rows = _compare_many(syms, args.timeout)

    all_w = []
    if provider_note:
        all_w.append("数据源：%s" % provider_note)
    if fg.get("value") is None:
        all_w.append("恐惧贪婪指数不可用 (%s) → 所有 symbol 的 fear_greed 置 null"
                     % (fg.get("error") or "未知原因"))
    for r in rows:
        all_w.extend(r.get("warnings") or [])
    for x in (xs_rows or []):
        all_w.extend(x.get("warnings") or [])

    payload = {
        "schema": "kexi.derivs_sentiment/1",
        "as_of": datetime.now(TZ8).isoformat(),
        "elapsed_ms": elapsed,
        "count": len(rows),
        "provider": provider,
        "provider_note": provider_note,
        "fear_greed": fg,
        "rows": rows,
        "warnings": all_w,
        "sources": SOURCES,
        "discipline": DISCIPLINE,
    }
    # 关闭跨源时不写这个键，保证产出与旧版本逐字段一致
    if xs_rows is not None:
        payload["cross_source"] = xs_rows
        payload["cross_source_discipline"] = CROSS_DISCIPLINE

    try:
        parent = os.path.dirname(os.path.abspath(out_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(json.dumps({"ok": False, "error": "写入失败: %s" % str(e)[:120]},
                         ensure_ascii=False), flush=True)
        return 1

    # 人类可读表格在前，最后一行是紧凑 JSON（供上层 lastJsonLine 解析）
    print(render_table(payload), flush=True)
    summary = {"ok": True, "count": len(rows), "elapsed_ms": elapsed,
               "provider": provider, "out": out_path,
               "warnings": len(all_w),
               "symbols": [r["symbol"] for r in rows]}
    if xs_rows is not None:          # 同样只在开启时出现
        summary["cross_source"] = len(xs_rows)
        summary["cross_source_divergences"] = sum(
            len(x.get("divergences") or []) for x in xs_rows)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
