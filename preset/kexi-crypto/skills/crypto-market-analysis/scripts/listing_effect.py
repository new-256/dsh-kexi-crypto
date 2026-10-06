#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
listing_effect.py - Exchange listing effect & reputation analysis ("上币分析").

Modes:
  1) Single coin:  python listing_effect.py --symbol JTO
     For each CEX: find listing date (first daily candle), compute D1/D7/D30
     post-listing returns; plus DEX venue info via Dexscreener.
  2) Reputation:   python listing_effect.py --reputation --sample 12
     Sample recent listings (coins whose Binance listing is < 365d old),
     aggregate per-exchange stats: median D1/D7/D30 returns, 30d break-even rate.
     Output = 交易所打新风评表.

Pre-market notes (agent-side, not scripted):
  - whales.market: no public free API -> agent fetches via WebFetch for specific
    pre-TGE tokens during Workflow E (best-effort, cite as "盘前场外价").
  - Binance/Bybit pre-market perpetuals: queried via futures API when present.

Usage:
  python listing_effect.py --symbol JTO --out le_JTO.json
  python listing_effect.py --reputation --sample 12 --out reputation.json --md reputation.md
"""

import argparse
import json
import statistics
import time
from datetime import datetime, timezone, timedelta

import exchanges

TZ8 = timezone(timedelta(hours=8))


def listing_info(exchange, base, limit=None):
    """Listing date + post-listing returns on one exchange. None if not listed.
    Window-capped per exchange; when result hits the cap, date is an approximation."""
    max_lim = exchanges.MAX_LIMITS.get(exchange, 300)
    req = min(limit or max_lim, max_lim)
    try:
        kl = exchanges.get_klines(exchange, base, limit=req)
    except Exception:
        return None
    if len(kl) < 2:
        return None
    first, last = kl[0], kl[-1]
    d1_open, d1_close = first[1], first[4]
    ret = lambda n: round((kl[n][4] / d1_open - 1) * 100, 1) if len(kl) > n else None
    window_limited = len(kl) >= req
    return {
        "listed": True,
        "listing_date": datetime.fromtimestamp(first[0] / 1000, TZ8).strftime("%Y-%m-%d"),
        "window_limited": window_limited,
        "days_listed": len(kl) - 1,
        "d1_open": d1_open,
        "ret_d1_pct": round((d1_close / d1_open - 1) * 100, 1),
        "ret_d7_pct": ret(7),
        "ret_d30_pct": ret(30),
        "current": last[4],
        "since_open_pct": round((last[4] / d1_open - 1) * 100, 1),
    }


def analyze_symbol(base, delay=0.3):
    out = {"symbol": base, "cex": {}, "dex": []}
    for ex in exchanges.CEX_LIST:
        time.sleep(delay)
        info = listing_info(ex, base)
        if info:
            out["cex"][ex] = info
    time.sleep(delay)
    try:
        out["dex"] = exchanges.dex_search(base)[:3]
    except Exception as e:
        out["dex_error"] = str(e)
    # earliest known listing across venues
    dates = [v["listing_date"] for v in out["cex"].values()]
    for p in out["dex"]:
        if p.get("created_at_ms"):
            dates.append(datetime.fromtimestamp(p["created_at_ms"] / 1000, TZ8).strftime("%Y-%m-%d"))
    out["first_seen"] = min(dates) if dates else None
    return out


def reputation(sample, delay, log):
    """Build exchange listing-effect stats from recent Binance listings."""
    tickers = exchanges._get(f"{exchanges.BASES['binance']}/api/v3/ticker/24hr")
    rows = sorted(
        (t for t in tickers
         if t["symbol"].isascii() and t["symbol"].endswith("USDT")
         and t["symbol"] not in ("BTCUSDT", "ETHUSDT")),
        key=lambda t: -float(t["quoteVolume"]))
    recent = []
    for t in rows[:120]:
        base = t["symbol"].replace("USDT", "")
        time.sleep(delay)
        try:
            kl = exchanges.get_klines("binance", base, limit=400)
        except Exception:
            continue
        if len(kl) >= 31 and len(kl) < 400:  # listed within ~1y, has 30d history
            recent.append(base)
        if len(recent) >= sample:
            break
    log(f"样本币: {', '.join(recent)}")

    stats = {ex: {"d1": [], "d7": [], "d30": [], "n": 0} for ex in exchanges.CEX_LIST}
    for base in recent:
        for ex in exchanges.CEX_LIST:
            time.sleep(delay)
            info = listing_info(ex, base)
            if not info or info["ret_d30_pct"] is None:
                continue
            stats[ex]["d1"].append(info["ret_d1_pct"])
            stats[ex]["d7"].append(info["ret_d7_pct"])
            stats[ex]["d30"].append(info["ret_d30_pct"])
            stats[ex]["n"] += 1

    table = []
    for ex, s in stats.items():
        if s["n"] < 2:
            table.append({"exchange": ex, "n": s["n"], "note": "样本不足"})
            continue
        table.append({
            "exchange": ex, "n": s["n"],
            "d1_median_pct": round(statistics.median(s["d1"]), 1),
            "d7_median_pct": round(statistics.median(s["d7"]), 1),
            "d30_median_pct": round(statistics.median(s["d30"]), 1),
            "d30_below_open_rate": round(sum(1 for x in s["d30"] if x < 0) / s["n"] * 100),
        })
    return {"sample": recent, "table": table}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", help="单币上币分析, e.g. JTO")
    ap.add_argument("--reputation", action="store_true", help="交易所风评批量模式")
    ap.add_argument("--sample", type=int, default=12)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--out", default="listing_effect.json")
    ap.add_argument("--md", default="listing_effect.md")
    args = ap.parse_args()

    log = lambda m: print(m, flush=True)

    if args.reputation:
        log("[风评模式] 采样近期上市币种 ...")
        rep = reputation(args.sample, args.delay, log)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        L = [f"# 交易所打新风评（样本 {len(rep['sample'])} 个近一年新币）\n"]
        L.append("| 交易所 | 样本 | 首日中位 | 7日中位 | 30日中位 | 30日破发率 |")
        L.append("|--------|------|---------|---------|----------|-----------|")
        for r in rep["table"]:
            if "note" in r:
                L.append(f"| {r['exchange']} | {r['n']} | {r['note']} | | | |")
            else:
                L.append(f"| {r['exchange']} | {r['n']} | {r['d1_median_pct']}% | "
                         f"{r['d7_median_pct']}% | {r['d30_median_pct']}% | {r['d30_below_open_rate']}% |")
        L.append("\n> 破发率 = 上市 30 日后价格低于首日开盘价的比例；仅含样本内在该所已满 30 天的币")
        with open(args.md, "w", encoding="utf-8") as f:
            f.write("\n".join(L))
        log(f"-> {args.out} / {args.md}")
        return

    if not args.symbol:
        ap.error("--symbol or --reputation required")
    base = args.symbol.upper().replace("USDT", "")
    log(f"[单币模式] {base} 跨所上币分析 ...")
    res = analyze_symbol(base, args.delay)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    L = [f"# {base} 上币走势分析\n", f"- 最早可见上市：{res.get('first_seen') or '未知'}\n",
         "| 交易所 | 上市日期 | 首日 | 7日 | 30日 | 至今 |", "|--------|----------|------|-----|------|------|"]
    for ex, v in res["cex"].items():
        date_str = v["listing_date"] + ("*" if v.get("window_limited") else "")
        L.append(f"| {ex} | {date_str} | {v['ret_d1_pct']}% | "
                 f"{v['ret_d7_pct']}% | {v['ret_d30_pct']}% | {v['since_open_pct']}% |")
    L.append("\n> \\* = 达到该所数据窗口上限，实际上市日期可能更早")
    if res.get("dex"):
        L.append("\n## DEX 主要交易对")
        for p in res["dex"]:
            L.append(f"- {p['dex']}（{p['chain']}）：流动性 ${p['liquidity_usd']:,}，"
                     f"24h 量 ${p['volume_24h']:,}" if p["liquidity_usd"] else f"- {p['dex']}（{p['chain']}）")
    with open(args.md, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    log(f"-> {args.out} / {args.md}")


if __name__ == "__main__":
    main()
