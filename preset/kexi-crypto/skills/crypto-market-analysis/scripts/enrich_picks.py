#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enrich_picks.py - Post-scan enrichment for screener picks (strategy v2, 2026-09-27).

Reads scan_result.json, annotates each pick with:
  - TP1 / TP2 / SL (from ATR14 + swing highs; SL = existing invalidation)
  - Order-book depth within +/-2% (Binance /depth) -> shallow-depth flag (< $300k)
  - Futures funding rate (Binance fapi premiumIndex) -> overheated flag (> 0.1%)
  - Circulating supply ratio (CoinGecko /coins/markets, best-effort) -> low-float flag (< 30%)
  - Fear & Greed index gate (rule from 999-day backtest):
      >= 80  -> downgrade overall confidence (overheat)
      <= 20  -> mark contrarian opportunity window
      20-80  -> neutral

Usage:
  python enrich_picks.py --input scan_result.json --out scan_enriched.json --md final_report.md
Only stdlib is used. All external failures degrade gracefully (field = None).
"""

import argparse
import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error

import kline_utils as ku

UA = {"User-Agent": "crypto-trend-analyst/1.0"}
SPOT = "https://api.binance.com"
FAPI = "https://fapi.binance.com"
CG = "https://api.coingecko.com/api/v3"

DEPTH_MIN_USD = 300_000
FUNDING_HOT = 0.001   # 0.1%
FLOAT_LOW = 0.30


def get(base, path, timeout=10, retries=2):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(base + path, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # graceful degradation everywhere
            last = e
            time.sleep(1.5 ** i)
    raise RuntimeError(str(last))


def atr14(klines):
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i][2], klines[i][3], klines[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < 14:
        return None
    a = sum(trs[:14]) / 14
    for tr in trs[14:]:
        a = (a * 13 + tr) / 14
    return a


def price_levels(symbol, close, invalidation):
    """TP1/TP2/SL from daily klines + ATR."""
    kl = get(SPOT, f"/api/v3/klines?symbol={symbol}&interval=1d&limit=91")
    kl = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in kl]
    kl, _ = ku.drop_open_bar(kl, ku.D1)   # 口径统一：ATR 与高低点只用已收盘日线
    a = atr14(kl)
    high20 = max(k[2] for k in kl[-21:-1])
    high60 = max(k[2] for k in kl[-61:-1]) if len(kl) > 61 else high20
    if a is None:
        return {"tp1": None, "tp2": None, "sl": invalidation, "note": "ATR样本不足"}
    tp1 = high20 if high20 > close * 1.005 else close + 1.5 * a
    tp2 = max(high60, close + 2.5 * a)
    return {"tp1": round(tp1, 6), "tp2": round(tp2, 6), "sl": invalidation,
            "atr14": round(a, 6), "note": None}


def depth_check(symbol):
    d = get(SPOT, f"/api/v3/depth?symbol={symbol}&limit=100")
    bids = [(float(p), float(q)) for p, q in d["bids"]]
    asks = [(float(p), float(q)) for p, q in d["asks"]]
    if not bids or not asks:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2
    lo, hi = mid * 0.98, mid * 1.02
    bid_depth = sum(p * q for p, q in bids if p >= lo)
    ask_depth = sum(p * q for p, q in asks if p <= hi)
    return {"bid_2pct": round(bid_depth), "ask_2pct": round(ask_depth),
            "shallow": min(bid_depth, ask_depth) < DEPTH_MIN_USD}


def funding_check(symbol):
    d = get(FAPI, f"/fapi/v1/premiumIndex?symbol={symbol}")
    rate = float(d.get("lastFundingRate", 0))
    return {"funding_rate": rate, "funding_pct": round(rate * 100, 4),
            "overheated": rate > FUNDING_HOT}


def supply_check(symbol):
    base = symbol.replace("USDT", "")
    q = urllib.parse.quote(base)
    res = get(CG, f"/search?query={q}")
    coin_id = None
    for c in res.get("coins", []):
        if c.get("symbol", "").upper() == base:
            coin_id = c["id"]
            break
    if not coin_id:
        return None
    m = get(CG, f"/coins/markets?vs_currency=usd&ids={coin_id}")
    if not m:
        return None
    m = m[0]
    mc, fdv = m.get("market_cap"), m.get("fully_diluted_valuation")
    circ, tot = m.get("circulating_supply"), m.get("total_supply")
    ratio = None
    if mc and fdv:
        ratio = mc / fdv
    elif circ and tot:
        ratio = circ / tot
    if ratio is None:
        return None
    return {"circ_ratio": round(ratio, 3), "low_float": ratio < FLOAT_LOW,
            "market_cap": mc, "fdv": fdv, "coingecko_id": coin_id}


def fng_gate():
    d = get("https://api.alternative.me", "/fng/?limit=1")
    v = int(d["data"][0]["value"])
    cls = d["data"][0]["value_classification"]
    if v >= 80:
        gate = "overheated"
        note = (f"F&G={v}({cls})：999日回测显示极度贪婪区30日中位收益为负(-1.04%)、胜率49%，"
                "整体置信度降一级")
    elif v <= 20:
        gate = "contrarian"
        note = (f"F&G={v}({cls})：极度恐惧区历史30日胜率71%、中位+4.15%，"
                "属逆向机会窗口，名单置信度不降，但需警惕下跌惯性中的假突破")
    else:
        gate = "neutral"
        note = f"F&G={v}({cls})：情绪中性区，不调整置信度"
    return {"value": v, "class": cls, "gate": gate, "note": note}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="scan_result.json")
    ap.add_argument("--out", default="scan_enriched.json")
    ap.add_argument("--md", default="final_report.md")
    ap.add_argument("--delay", type=float, default=0.3)
    args = ap.parse_args()

    log = lambda m: print(m, flush=True)
    # ⚠ v1.9.7：缺 --input 时这里原本直接 open() 抛 FileNotFoundError 堆栈。
    #   模型漏传参数拿到的是 traceback——它没法据此自我纠正，只能瞎试。
    #   改成干净的可操作报错：说清缺什么、期望什么文件。
    if not args.input or not os.path.exists(args.input):
        print(json.dumps({
            "ok": False,
            "error": ("缺少输入文件：%s" % (args.input or "(未传 --input)")),
            "hint": ("先跑 screener.py 生成 scan_result.json，"
                     "再 enrich_picks.py --input kexi_out/scan_result.json"),
        }, ensure_ascii=False), flush=True)
        return 1
    with open(args.input, encoding="utf-8") as f:
        scan = json.load(f)

    try:
        fng = fng_gate()
    except Exception as e:
        fng = {"value": None, "gate": "unknown", "note": f"F&G获取失败: {e}"}
    log(f"F&G 闸门: {fng['gate']} — {fng['note']}")

    picks = scan.get("picked", [])
    enriched = []
    for p in picks:
        s = p["symbol"]
        log(f"增强 {s} ...")
        time.sleep(args.delay)
        try:
            p["levels"] = price_levels(s, p["close"], p["invalidation"])
        except Exception as e:
            p["levels"] = {"tp1": None, "tp2": None, "sl": p["invalidation"], "note": str(e)}
        time.sleep(args.delay)
        try:
            p["depth"] = depth_check(s)
        except Exception as e:
            p["depth"] = {"error": str(e)}
        time.sleep(args.delay)
        try:
            p["funding"] = funding_check(s)
        except Exception:
            p["funding"] = None  # no futures market for this coin
        time.sleep(args.delay)
        try:
            p["supply"] = supply_check(s)
        except Exception as e:
            p["supply"] = {"error": str(e)}

        flags = []
        if p["depth"] and p["depth"].get("shallow"):
            flags.append("盘口深度不足(±2%双边<30万U)，插针风险高")
        if p["funding"] and p["funding"].get("overheated"):
            flags.append(f"合约资金费率{p['funding']['funding_pct']}%>0.1%，多头拥挤")
        if p["supply"] and p["supply"].get("low_float"):
            flags.append(f"流通占比仅{p['supply']['circ_ratio']*100:.0f}%，解锁抛压风险")
        p["risk_flags"] = flags
        enriched.append(p)

    confidence = scan.get("overall_confidence", "中")
    if fng.get("gate") == "overheated" and confidence in ("中高", "高"):
        confidence = "中"
        log("F&G 过热：整体置信度 中高 -> 中")

    out = dict(scan)
    out["picked"] = enriched
    out["fng"] = fng
    out["overall_confidence"] = confidence
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    L = [f"# 技术筛选最终名单（增强版，{out['generated_at'][:16]} UTC+8）\n"]
    L.append(f"- 大盘环境：{scan['environment']['status']}；F&G 情绪闸门：{fng['gate']}")
    L.append(f"- {fng['note']}")
    L.append(f"- 整体置信度：**{confidence}**")
    L.append(f"- 漏斗：标的池 {scan['universe_size']} → 初筛 {scan['passed_screen']} → "
             f"4h复核 {scan['resonant_count']} → 最终 {len(enriched)}\n")
    if len(enriched) < scan["params"]["final"]:
        L.append(f"> ⚠️ 宁缺毋滥：仅 {len(enriched)} 个通过全部关卡。\n")
    L.append("| # | 币种 | 总分 | 现价 | 止损(失效位) | TP1 | TP2 | 风险标记 |")
    L.append("|---|------|------|------|------------|-----|-----|---------|")
    for i, p in enumerate(enriched, 1):
        lv = p["levels"]
        L.append(f"| {i} | {p['symbol']} | {p['score']} | {p['close']} | {lv['sl']} | "
                 f"{lv.get('tp1')} | {lv.get('tp2')} | {'；'.join(p['risk_flags']) or '无'} |")
    L.append("\n## 明细\n")
    for i, p in enumerate(enriched, 1):
        lv = p["levels"]
        L.append(f"### {i}. {p['symbol']}（截至 {p['last_date']}）")
        L.append(f"- 命中信号：" + "；".join(p["reasons"] + p["bonus_reasons"]))
        L.append(f"- 4h复核：{p['resonance_note']}")
        L.append(f"- 价位：现价 {p['close']} ｜ 止损 {lv['sl']} ｜ TP1 {lv.get('tp1')} ｜ TP2 {lv.get('tp2')}")
        if p.get("depth") and not p["depth"].get("error"):
            L.append(f"- 盘口深度(±2%)：买 {p['depth']['bid_2pct']:,}U / 卖 {p['depth']['ask_2pct']:,}U")
        if p.get("funding"):
            L.append(f"- 合约资金费率：{p['funding']['funding_pct']}%")
        if p.get("supply") and p["supply"].get("circ_ratio"):
            L.append(f"- 流通占比：{p['supply']['circ_ratio']*100:.0f}%（市值/FDV）")
        if p["risk_flags"]:
            L.append(f"- ⚠️ 风险标记：" + "；".join(p["risk_flags"]))
        L.append("")
    L.append("---\n**免责声明**：本名单由量化规则筛选并经多维数据增强，仅为技术信号参考，不构成投资建议。"
             "虚拟币波动剧烈，止损位仅供参考，请独立决策并严格控制仓位。")
    with open(args.md, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    log(f"\n完成。增强名单 {len(enriched)} 个 -> {args.out} / {args.md}")


if __name__ == "__main__":
    main()
