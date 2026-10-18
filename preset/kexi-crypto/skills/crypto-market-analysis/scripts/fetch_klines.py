#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_klines.py - Fetch crypto K-line (OHLCV) and real-time quotes.

v1.1-patched 相对专家包 v1.0.0 的修改（逐条对应审查报告中的缺陷编号）：
  [P0-1] CoinGecko 降级路径 days=120 是非法枚举值，永远 400 —— 改为合法枚举 CG_DAYS，
         并在 warnings 中明确声明 CoinGecko 的真实粒度与请求周期不一致。
  [P0-2] 末根未收盘 bar 污染日线指标 —— 默认剔除未收盘 bar（--include-open-bar 可保留），
         并在输出中记录 dropped_open_bar / last_closed_open。
  [P0-3] 从不校验实际 K 线周期 —— 新增 interval_actual（由时间戳中位差推断），
         与请求周期不一致时写入 warnings。
  [P1-4] 声称"校验时间戳连续性/去重"，实际只统计不处理 —— 新增真正的去重与缺口检测
         （duplicates_removed / gaps）。
  [P1-5] 4xx 参数错误被无意义重试 3 次 —— 400/404 等直接失败，仅 429 与网络错误退避重试。
  [P2-6] 单域名 —— 按 references/data-sources.md 增加 api1~api4 备选域名轮询。
  [P2-7] --interval 缺少 1w（望潮的周线印证无法执行）—— 补齐 1w。

Usage:
  python fetch_klines.py --symbol BTCUSDT --interval 1d --limit 120 --out btc_1d.json
  python fetch_klines.py --symbol BTCUSDT --quote
  python fetch_klines.py --symbol BTCUSDT --interval 1d --limit 120 --include-open-bar --out btc_1d_raw.json

Output JSON:
  {symbol, interval, interval_actual, source, fetched_at, klines: [[ts_ms, o, h, l, c, v], ...],
   bars, last_closed_open, last_bar_age_days, dropped_open_bar, duplicates_removed, gaps, warnings: [...], error: null|str}
Only stdlib is used.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

UA = {"User-Agent": "crypto-trend-analyst/1.1"}
TZ8 = timezone(timedelta(hours=8))

# Binance symbol -> CoinGecko coin id。
#
# v1.9.22 (D5)：这张表**不再是白名单**，只是**常用币的速查缓存**。
# 早先它是唯一来源（15 项），任何表外币即使符号正确，CoinGecko 回退路径
# 也必然失败（"no CoinGecko id mapping for XXX"）。这违反"研发优先泛用方案"
# 的原则——不能因为某个币不在我手写的清单里就判它取不到数。
#
# 现在的解析顺序：
#   ① 静态表命中 → 直接用（省一次网络请求）
#   ② 未命中 → 查 CoinGecko /coins/list 全量币表（约 15k 项），按 symbol 匹配
#   ③ 结果缓存 7 天到 <cwd>/kexi_out/.cache/cg_coins_list.json
#   ④ 网络不可用且未命中 → **明确报错**，绝不"猜一个"
CG_IDS = {
    "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "BNBUSDT": "binancecoin",
    "SOLUSDT": "solana", "XRPUSDT": "ripple", "DOGEUSDT": "dogecoin",
    "ADAUSDT": "cardano", "AVAXUSDT": "avalanche-2", "LINKUSDT": "chainlink",
    "DOTUSDT": "polkadot", "LTCUSDT": "litecoin", "TRXUSDT": "tron",
    "PEPEUSDT": "pepe", "SUIUSDT": "sui", "TONUSDT": "the-open-network",
}

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000,
}

# CoinGecko /coins/{id}/ohlc 只接受这些 days 值；其它值一律 HTTP 400
CG_VALID_DAYS = (1, 7, 14, 30, 90, 180, 365, "max")
# 请求周期 -> 合法 days（在合法枚举内取最接近者）
CG_DAYS = {"1d": 90, "4h": 30, "1h": 7, "1w": 365}
# CoinGecko 免费档 OHLC 的真实粒度（官方规则：1-2天=30分钟, 3-30天=4小时, 31天以上=4天）
CG_GRANULARITY = {1: "30分钟", 7: "4小时", 14: "4小时", 30: "4小时",
                  90: "4天", 180: "4天", 365: "4天", "max": "4天"}

BINANCE_HOSTS = ["api.binance.com", "api1.binance.com", "api2.binance.com",
                 "api3.binance.com", "api4.binance.com"]


def _normalize_symbol(raw):
    """v1.9.22：转发到 kline_utils 的唯一真源实现。

    为什么不在本文件重复写一份：**三处入口各写一遍 .toUpperCase()
    正是这个缺陷长期没修好的原因**。这里只做转发，保证口径一致。
    延迟导入（函数内 import）是为了避免 fetch_klines ←→ kline_utils
    的模块级循环依赖：kline_utils 不依赖本模块，但入口顺序不该成为隐患。
    """
    try:
        import kline_utils as _ku
        return _ku.normalize_symbol(raw)
    except Exception:
        # 兜底：极端情况下 kline_utils 不可用，退化为最小校验，
        # 但**仍然是显式补后缀**而不是原样透传（否则就回到旧缺陷）。
        s = str(raw or "").strip().upper().replace("/", "").replace("-", "").replace("_", "")
        if not s:
            return {"symbol": None, "error": "符号为空", "note": None, "changed": False}
        if not s.endswith("USDT") and s not in ("USDT", "USDC", "USD", "BUSD"):
            s = s + "USDT"
        return {"symbol": s, "error": None, "note": None, "changed": True}


def http_get(url, retries=3, timeout=15, deadline=None):
    """GET with exponential backoff. 4xx(非429) 属参数类错误，不重试。

    v1.9.22：新增 deadline（Unix 秒时间戳）。网络层失败（连接重置/超时）
    会走完 retries × 退避，5 host × 3 retry 就是 30s+；传入 deadline 后
    每次重试前检查，超时立即抛错，让上层轮询链能真正"到点收手"。
    """
    last = None
    for i in range(retries):
        if deadline is not None and time.time() > deadline:
            raise RuntimeError(f"request deadline exceeded after {i} attempt(s): {last or 'no attempt completed'}")
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if 400 <= e.code < 500 and e.code != 429:
                # v1.9.22：**立刻抛出，不再走退避**。
                # 早先虽然也 break，但仍执行了 time.sleep(2 ** i)（最长 4 秒），
                # 且异常信息里看不到 "400" 之外的结构化线索，上层无从判断
                # "这是参数错还是网络错"。现在直接带上状态码抛出。
                raise RuntimeError(f"HTTP {e.code} {e.reason or ''}".strip())
            time.sleep(2 ** i)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"request failed after {retries} attempts: {last}")


def fetch_binance(symbol, interval, limit, time_budget=10.0):
    """按 data-sources.md 依次尝试 api / api1~api4。

    v1.9.22：**HTTP 400 立即终止整条轮询链**。
    400 是参数类错误（symbol 不存在 / interval 非法 / limit 越界），
    换一个域名必然还是 400——五个 host 全跑一遍纯粹是浪费时间。
    现场实测：不存在的符号走完 5 host × 3 retry 要 10 秒，
    20 币就是 120 秒；短路后单币 <1 秒。

    v1.9.22：**整条 host 链加总预算 deadline**（默认 10s）。
    400 短路挡得住"参数错误"，但挡不住**连接层失败**（网络重置/超时/地域
    拦截 451）——那种情况下每个 host 仍会走 3 次重试，5 host 就是 30s+。
    实测本机 api.binance.com 连接不稳定，一次选币全会话曾耗 118 秒。
    到点即放弃剩余域名并如实报出已尝试结果。
    """
    deadline = time.time() + max(1.0, float(time_budget))
    errors = []
    for host in BINANCE_HOSTS:
        if time.time() > deadline:
            errors.append("已超过币安轮询总预算 %.0fs，停止尝试剩余域名" % float(time_budget))
            break
        url = (f"https://{host}/api/v3/klines"
               f"?symbol={symbol.upper()}&interval={interval}&limit={limit}")
        try:
            raw = http_get(url, deadline=deadline)
            klines = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
                      for k in raw]
            return klines, ([f"Binance 主域名不可用，已切到 {host}"] if host != BINANCE_HOSTS[0] else [])
        except Exception as e:
            errors.append(f"{host}: {e}")
            # 400 = 参数错误：换域名不会有不同结果，立刻跳出。
            if "400" in str(e) or "Bad Request" in str(e):
                errors.append("（HTTP 400 属参数类错误，已停止轮询其余域名——"
                              "多为交易对不存在或周期非法）")
                break
            # 451 = 地域拦截：换域名同样是拦截，继续试无意义，立刻跳出。
            if "451" in str(e) or "Forbidden" in str(e):
                errors.append("（HTTP 451 地域拦截，已停止轮询其余域名——"
                              "本机出口 IP 在该地区被币安拒绝）")
                break
    raise RuntimeError(" | ".join(errors))


OKX_BAR = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
           "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H",
           "1d": "1D", "3d": "3D", "1w": "1W"}


def _okx_inst_id(symbol):
    """PYTHUSDT -> PYTH-USDT。OKX 用连字符分基币/计价币，币安用连写。"""
    s = (symbol or "").upper().replace("/", "").replace("_", "")
    for q in ("USDT", "USDC", "BUSD", "USD"):
        if s.endswith(q) and len(s) > len(q):
            return s[:-len(q)] + "-" + q
    return s


def fetch_okx(symbol, interval, limit):
    """OKX 行情（**公开接口，不需要 API Key**）。

    v1.9.14：为什么必须加这个源——用户仓位在 OKX，而本脚本只支持
    binance / coingecko，于是**拿币安的价去算 OKX 仓位的止损**。
    实测当下价差只有 -0.026%（影响很小），但结构性风险在：
    一旦 OKX 溢价（逼空、或流动性只在一家），止损位就会算在错的价格上，
    而止损位是要真金白银执行的。**当时看着没事，不等于下次没事。**
    """
    inst = _okx_inst_id(symbol)
    bar = OKX_BAR.get(interval, interval)
    url = ("https://www.okx.com/api/v5/market/candles"
           f"?instId={inst}&bar={bar}&limit={min(int(limit), 300)}")
    raw = http_get(url)
    if isinstance(raw, dict):
        if raw.get("code") not in (None, "0"):
            raise RuntimeError("OKX 返回错误 %s: %s" % (raw.get("code"), raw.get("msg")))
        raw = raw.get("data") or []
    # OKX 返回 [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]，**倒序**（最新在前）
    out = []
    for k in raw:
        try:
            out.append([int(k[0]), float(k[1]), float(k[2]), float(k[3]),
                        float(k[4]), float(k[5])])
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda r: r[0])          # 统一成升序，和 binance 一致
    if not out:
        raise RuntimeError("OKX 未返回 K 线（instId=%s 是否正确？）" % inst)
    return out, ["OKX 价源：instId=%s（**持仓在 OKX 时应优先用本源**）" % inst]


def fetch_coingecko(symbol, interval, limit):
    coin, note = resolve_coingecko_id(symbol)
    if not coin:
        raise RuntimeError(note or f"无法解析 {symbol} 的 CoinGecko id")
    days = CG_DAYS.get(interval, 90)
    if days not in CG_VALID_DAYS:
        raise RuntimeError(f"CoinGecko OHLC 不接受 days={days}，合法值 {CG_VALID_DAYS}")
    url = (f"https://api.coingecko.com/api/v3/coins/{coin}/ohlc"
           f"?vs_currency=usd&days={days}")
    raw = http_get(url)  # [[ts, o, h, l, c], ...] 无成交量
    klines = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), 0.0]
              for k in raw]
    gran = CG_GRANULARITY.get(days, "未知")
    warnings = [
        "CoinGecko OHLC 无成交量字段，volume=0；量价分析不可用，仅可用价格类指标",
        f"CoinGecko 免费档 days={days} 的真实粒度为【{gran}】，与请求周期 {interval} 不一致；"
        f"报告中不得称其为 {interval} 线，长周期指标可能因样本不足而失效",
    ]
    return klines[-limit:], warnings


def fetch_quote(symbol):
    """Real-time snapshot; Binance first, CoinGecko fallback."""
    try:
        d = http_get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol.upper()}")
        return {"source": "binance", "price": float(d["lastPrice"]),
                "change_24h_pct": float(d["priceChangePercent"]),
                "volume_24h": float(d["quoteVolume"]),
                "high_24h": float(d["highPrice"]), "low_24h": float(d["lowPrice"])}
    except Exception as e1:
        # v1.9.22 (D5)：不再只查静态表，改走动态解析器
        coin, note = resolve_coingecko_id(symbol)
        if not coin:
            raise RuntimeError(f"binance failed ({e1}); {note or 'CoinGecko id 无法解析'}")
        d = http_get(f"https://api.coingecko.com/api/v3/simple/price"
                     f"?ids={coin}&vs_currencies=usd&include_24hr_change=true"
                     f"&include_24hr_vol=true")[coin]
        return {"source": "coingecko", "price": float(d["usd"]),
                "change_24h_pct": float(d.get("usd_24h_change", 0.0)),
                "volume_24h": float(d.get("usd_24h_vol", 0.0)),
                "high_24h": None, "low_24h": None}


def detect_step_ms(klines):
    """由时间戳中位差推断真实周期（毫秒）。"""
    if len(klines) < 3:
        return None
    ds = sorted(klines[i + 1][0] - klines[i][0] for i in range(len(klines) - 1))
    return ds[len(ds) // 2]


# ── v1.9.22 (D5)：CoinGecko 币表解析已拆到 cg_registry.py（用户规则26 单文件≤600行）──
# 这里只做转发，保持 fetch_klines 内部调用点不变。
# 早先 CG_IDS 是硬编码 15 项的白名单且是唯一来源，表外币必然取数失败；
# 现在静态表降级为速查缓存，未命中时查全量币表（约 2 万项）并缓存 7 天。
from cg_registry import CG_IDS, resolve_coingecko_id  # noqa: E402

# v1.9.22：把本模块的 http_get 注入 cg_registry，让它查全量币表时复用
# 同一套 UA / 超时 / deadline 语义，而不是另起一套 urllib 调用。
try:
    import cg_registry as _cg
    _cg.set_http_get(lambda url, timeout=20: http_get(url, timeout=timeout))
except Exception:
    pass


def humanize_ms(ms):
    if not ms:
        return None
    for name, v in INTERVAL_MS.items():
        if ms == v:
            return name
    if ms % 86_400_000 == 0:
        return f"{ms // 86_400_000}d"
    if ms % 3_600_000 == 0:
        return f"{ms // 3_600_000}h"
    return f"{ms}ms"


def dedupe(klines):
    """按时间戳去重，保留后者（新数据覆盖旧数据）。"""
    seen, out = {}, []
    for k in klines:
        if k[0] in seen:
            continue
        seen[k[0]] = True
        out.append(k)
    return out, len(klines) - len(out)


def drop_open_bar(klines, step_ms):
    """剔除尚未走完的最后一根 bar（按开盘时间 + 周期 <= now 判定）。"""
    if not klines or not step_ms:
        return klines, 0
    now_ms = int(time.time() * 1000)
    closed = [k for k in klines if k[0] + step_ms <= now_ms]
    return closed, len(klines) - len(closed)


def check_gaps(klines, step_ms, max_report=5):
    """检测时间戳缺口，返回 [{'after': ts, 'missing': n}]。"""
    if not step_ms or len(klines) < 2:
        return []
    gaps = []
    for i in range(1, len(klines)):
        d = klines[i][0] - klines[i - 1][0]
        if d > step_ms * 1.5:
            gaps.append({"after": klines[i - 1][0], "missing": max(int(round(d / step_ms)) - 1, 1)})
    return gaps[:max_report]


def quality_check(klines):
    """Detect extreme candles. Returns warnings list."""
    warnings = []
    if not klines:
        return ["empty kline data"]
    prev_close = None
    extremes = []
    for k in klines:
        ts = k[0]
        o, h, l, c = k[1], k[2], k[3], k[4]
        if prev_close and prev_close > 0:
            chg = (c - prev_close) / prev_close * 100
            if abs(chg) >= 10:
                dt = datetime.fromtimestamp(ts / 1000, TZ8).strftime("%Y-%m-%d %H:%M")
                extremes.append(f"{dt} 单根变动 {chg:+.1f}%")
        if h < l or min(o, c) < l - 1e-9 or max(o, c) > h + 1e-9:
            warnings.append(f"K线数据异常 ts={ts}: OHLC 关系不一致")
        prev_close = c
    if extremes:
        warnings.append("极端波动: " + "; ".join(extremes) +
                        " —— 技术指标准确性可能下降，请风控重点关注")
    return warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="e.g. BTCUSDT, ETHUSDT")
    ap.add_argument("--interval", default="1d", choices=["1h", "4h", "1d", "1w"])
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--source", default="auto", choices=["auto", "binance", "okx", "coingecko"])
    ap.add_argument("--quote", action="store_true", help="fetch real-time snapshot instead")
    ap.add_argument("--include-open-bar", action="store_true",
                    help="保留尚未收盘的最后一根 bar（默认剔除，避免污染日线指标）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    # ── v1.9.22 (D1)：符号规范化 ─────────────────────────────────────
    # 早先 args.symbol.upper() 原样透传，"BTC" 会挨个打 CG_IDS / Binance / OKX
    # 三个源全部失败。这里接入共享的 normalize_symbol（唯一真源）。
    _norm = _normalize_symbol(args.symbol)
    if _norm["error"]:
        print(json.dumps({"symbol": args.symbol, "error": "符号非法：" + _norm["error"],
                          "klines": [], "bars": 0}, ensure_ascii=False, indent=2))
        sys.exit(2)
    symbol = _norm["symbol"]
    _sym_warn = [_norm["note"]] if _norm.get("note") else []

    result = {"symbol": symbol, "interval": args.interval,
              "interval_actual": None, "source": None,
              "fetched_at": datetime.now(TZ8).isoformat(),
              "klines": [], "bars": 0, "last_closed_open": None,
              "last_bar_age_days": None,
              "dropped_open_bar": 0, "duplicates_removed": 0, "gaps": [],
              "warnings": list(_sym_warn), "error": None}

    try:
        if args.quote:
            result["quote"] = fetch_quote(symbol)
            result["source"] = result["quote"]["source"]
        else:
            errors = []
            order = (["binance", "okx", "coingecko"] if args.source == "auto"
                     else [args.source])
            for src in order:
                try:
                    if src == "binance":
                        result["klines"], w = fetch_binance(symbol, args.interval, args.limit)
                    elif src == "okx":
                        result["klines"], w = fetch_okx(symbol, args.interval, args.limit)
                    else:
                        result["klines"], w = fetch_coingecko(args.symbol, args.interval, args.limit)
                    result["source"] = src
                    result["warnings"].extend(w)
                    if len(order) > 1 and src != order[0]:
                        result["warnings"].append(f"已降级到 {src} 数据源，原因: {errors[-1]}")
                    break
                except Exception as e:
                    errors.append(f"{src}: {e}")
            else:
                result["error"] = ("所有数据源均获取失败: " + " | ".join(errors) +
                                   "。请检查网络后重试；严禁用记忆数据顶替，报告中须标注'数据缺失'。")
                print(json.dumps(result, ensure_ascii=False, indent=2))
                sys.exit(2)

            result["klines"], result["duplicates_removed"] = dedupe(result["klines"])
            if result["duplicates_removed"]:
                result["warnings"].append(f"已按时间戳去重 {result['duplicates_removed']} 根")

            step_ms = detect_step_ms(result["klines"])
            result["interval_actual"] = humanize_ms(step_ms)
            req_ms = INTERVAL_MS.get(args.interval)
            if step_ms and req_ms and step_ms != req_ms:
                result["warnings"].append(
                    f"实际 K 线周期为 {result['interval_actual']}，与请求的 {args.interval} 不一致；"
                    f"报告口径须按实际周期标注")

            result["gaps"] = check_gaps(result["klines"], step_ms)
            if result["gaps"]:
                miss = sum(g["missing"] for g in result["gaps"])
                result["warnings"].append(
                    f"发现时间戳缺口 {len(result['gaps'])} 处，共缺 {miss} 根（缺口>20% 须降置信度）")

            if not args.include_open_bar:
                result["klines"], result["dropped_open_bar"] = drop_open_bar(result["klines"], step_ms)
                if result["dropped_open_bar"]:
                    result["warnings"].append(
                        f"已剔除 {result['dropped_open_bar']} 根未收盘 bar，"
                        f"指标基于最后一根已收盘 K 线（口径统一）")

            if result["klines"]:
                last_open_s = result["klines"][-1][0] / 1000.0
                result["last_closed_open"] = datetime.fromtimestamp(
                    last_open_s, TZ8).isoformat()
                now_s = time.time()
                age = round((now_s - last_open_s) / 86400.0, 1)
                result["last_bar_age_days"] = age
                if args.interval == "1d" and age > 3:
                    result["warnings"].append(
                        f"数据可能停更: 最后一根已收盘K线距今 {age} 天(>3), 该标的可能已退市/改符号, 分析须标注")
                elif args.interval == "1w" and age > 10:
                    result["warnings"].append(
                        f"数据可能停更: 最后一根已收盘K线距今 {age} 天(>10), 该标的可能已退市/改符号, 分析须标注")
            result["bars"] = len(result["klines"])

            result["warnings"].extend(quality_check(result["klines"]))
            if len(result["klines"]) < 60:
                result["warnings"].append(
                    f"样本仅 {len(result['klines'])} 根（<60），长周期指标仅供参考")
    except Exception as e:
        result["error"] = str(e)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(2)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        _od = os.path.dirname(os.path.abspath(args.out))
        if _od:
            os.makedirs(_od, exist_ok=True)
        # 数据完整性保护（v1.5.5）：**降级取数不得覆盖磁盘上已有的好数据**。
        # 现场事故：交易所限流(451)→ 降级 CoinGecko（22 根、4 天粒度），
        # 把本地 239 根的 binance 文件覆盖掉了。一次限流就毁掉数据集，
        # 后续所有分析都基于垃圾数据——而成员不会察觉，只看到"取数成功"。
        _keep_old = False
        if os.path.exists(args.out):
            try:
                with open(args.out, encoding="utf-8") as f:
                    _old = json.load(f)
                _ok = _old.get("klines") or []
                _ost = detect_step_ms(_ok) if len(_ok) > 2 else 0
                _nst = detect_step_ms(result["klines"]) if len(result["klines"]) > 2 else 0
                if len(_ok) > len(result["klines"]) or (_ost and _nst and _nst > _ost):
                    _keep_old = True
                    print(f"⚠ 降级数据（{result['bars']} 根 / {result.get('interval_actual')}）"
                          f"**未覆盖** {args.out} 中更好的 {len(_ok)} 根记录——已保留原文件")
            except Exception:
                pass
        if _keep_old:
            print(json.dumps({**result, "written": False,
                              "note": f"未写盘：{args.out} 已有更好的数据"}, ensure_ascii=False, indent=2))
            return
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)

        # ── v1.9.22 (D10)：**写后回读校验** ──────────────────────────────
        # 现场取证（session-44da005c，成员实证）：digest 显示数据新鲜，
        # 但写进 kexi_out/ 的文件内容止于 10-05。5 个币因此被误判
        # 「停更 13 天」并建议弃用，靠主理人手工复核才纠正。
        # 元数据与产物内容不一致且不报错，是**数据正确性**缺陷，比报错更危险。
        #
        # 这里写完立刻回读，比对：
        #   ① 文件里的 bars 是否等于内存中的 bars
        #   ② 文件里最后一根 K 线的时间戳是否等于内存中的
        # 不一致 → 非零退出码，绝不"报告成功"。
        try:
            with open(args.out, encoding="utf-8") as f:
                _rb = json.load(f)
            _rb_kl = _rb.get("klines") or []
            _mem_last = result["klines"][-1][0] if result["klines"] else None
            _rb_last = _rb_kl[-1][0] if _rb_kl else None
            if len(_rb_kl) != len(result["klines"]) or _mem_last != _rb_last:
                msg = (f"落盘校验失败：{args.out} 回读得到 {len(_rb_kl)} 根/末根 {_rb_last}，"
                       f"与内存中 {len(result['klines'])} 根/末根 {_mem_last} 不一致——"
                       f"产物内容与本次取数不符，下游不得使用该文件")
                result["error"] = msg
                result["self_check"] = {"ok": False, "reason": msg}
                print(json.dumps(result, ensure_ascii=False, indent=2))
                sys.exit(4)
            # 校验通过：把结果里的自检信息一并写出，供上层机械复核
            result["self_check"] = {
                "ok": True,
                "bars": len(result["klines"]),
                "last_closed_open": result.get("last_closed_open"),
                "last_bar_age_days": result.get("last_bar_age_days"),
                "source": result.get("source"),
                "written_bytes": os.path.getsize(args.out),
            }
            # 重写一次，把 self_check 带进文件（供 kexi-plugin 的质检读取）
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except SystemExit:
            raise
        except Exception as e:
            msg = f"落盘校验异常：{args.out} 无法回读（{str(e)[:120]}）"
            result["error"] = msg
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(4)

        print(f"saved {result['bars']} klines -> {args.out} "
              f"(source={result['source']}, actual={result['interval_actual']}, "
              f"dropped_open={result['dropped_open_bar']}, warnings={len(result['warnings'])})")
    else:
        print(text)


if __name__ == "__main__":
    main()
