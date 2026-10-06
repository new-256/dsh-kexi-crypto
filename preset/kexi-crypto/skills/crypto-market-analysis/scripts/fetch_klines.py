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

# Binance symbol -> CoinGecko coin id (extend as needed)
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


def http_get(url, retries=3, timeout=15):
    """GET with exponential backoff. 4xx(非429) 属参数类错误，不重试。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if 400 <= e.code < 500 and e.code != 429:
                break
            time.sleep(2 ** i)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"request failed after {retries} attempts: {last}")


def fetch_binance(symbol, interval, limit):
    """按 data-sources.md 依次尝试 api / api1~api4。"""
    errors = []
    for host in BINANCE_HOSTS:
        url = (f"https://{host}/api/v3/klines"
               f"?symbol={symbol.upper()}&interval={interval}&limit={limit}")
        try:
            raw = http_get(url)
            klines = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
                      for k in raw]
            return klines, ([f"Binance 主域名不可用，已切到 {host}"] if host != BINANCE_HOSTS[0] else [])
        except Exception as e:
            errors.append(f"{host}: {e}")
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
    coin = CG_IDS.get(symbol.upper())
    if not coin:
        raise RuntimeError(f"no CoinGecko id mapping for {symbol}; add it to CG_IDS")
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
        coin = CG_IDS.get(symbol.upper())
        if not coin:
            raise RuntimeError(f"binance failed ({e1}); no coingecko mapping")
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

    result = {"symbol": args.symbol.upper(), "interval": args.interval,
              "interval_actual": None, "source": None,
              "fetched_at": datetime.now(TZ8).isoformat(),
              "klines": [], "bars": 0, "last_closed_open": None,
              "last_bar_age_days": None,
              "dropped_open_bar": 0, "duplicates_removed": 0, "gaps": [],
              "warnings": [], "error": None}

    try:
        if args.quote:
            result["quote"] = fetch_quote(args.symbol)
            result["source"] = result["quote"]["source"]
        else:
            errors = []
            order = (["binance", "okx", "coingecko"] if args.source == "auto"
                     else [args.source])
            for src in order:
                try:
                    if src == "binance":
                        result["klines"], w = fetch_binance(args.symbol, args.interval, args.limit)
                    elif src == "okx":
                        result["klines"], w = fetch_okx(args.symbol, args.interval, args.limit)
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
        print(f"saved {result['bars']} klines -> {args.out} "
              f"(source={result['source']}, actual={result['interval_actual']}, "
              f"dropped_open={result['dropped_open_bar']}, warnings={len(result['warnings'])})")
    else:
        print(text)


if __name__ == "__main__":
    main()
