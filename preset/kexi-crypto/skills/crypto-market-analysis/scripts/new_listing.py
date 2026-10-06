#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
new_listing.py - New-listing ("打新") evaluator with whale-market data.

Strategy (fixed 2026-09-27):
  Discovery : USDT pairs whose daily kline count <= --max-age-days => newly listed.
  Whale score (0-15, or 0-10 if holder-concentration unavailable):
    L1 top-trader position ratio (futures, latest 1d): >1.2 +3 | 1.0-1.2 +2 | 0.8-1.0 +1 | else 0
    L1 top-trader account ratio  (futures, latest 1d): >1.2 +2 | 1.0-1.2 +1 | else 0
    L2 large-trade net flow (spot aggTrades, last 1000, >$100k prints):
       net > $50k +3 | > 0 +2 | > -$50k +1 | else 0
    L3 holder concentration (explorer, needs ETHERSCAN_API_KEY env, best-effort):
       top10 <30% +5 | 30-50% +3 | 50-70% +1 | >=70% 0 | unavailable -> N/A
  "Second wave" (规则化第二波) entry conditions — ALL must hold:
    age >= 2 days (首日绝不开仓)
    price has not broken day-1 low (within 3%)
    volume contraction: avg vol of last 3d < 50% of day-1 vol (缩量企稳)
    last-3d range < 15% of close (企稳)
    whale score >= 9/15 (or >= 6/10 when L3 is N/A)
  Exit rules (hard-coded reminders): position <= 10%, time stop 72h, price stop -8%.

Usage:
  python new_listing.py --out new_listings.json --md new_listings_report.md
  ETHERSCAN_API_KEY=xxx python new_listing.py   # enables L3 concentration
Only stdlib is used. All external failures degrade gracefully.
"""

import argparse
import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

import kline_utils as ku

UA = {"User-Agent": "crypto-trend-analyst/1.0"}
SPOT = "https://api.binance.com"
FAPI = "https://fapi.binance.com"
CG = "https://api.coingecko.com/api/v3"
TZ8 = timezone(timedelta(hours=8))

STABLES = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT",
           "USDEUSDT", "USD1USDT", "EURUSDT", "AEURUSDT", "BFUSDUSDT"}
WRAPPED = {"WBTCUSDT", "WBETHUSDT", "WETHUSDT", "STETHUSDT", "BNSOLUSDT"}
LARGE_TRADE_USD = 100_000


def get(base, path, timeout=10, retries=2):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(base + path, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5 ** i)
    raise RuntimeError(str(last))


def discover_new_candicates(top_n, max_age_days, delay, log):
    tickers = get(SPOT, "/api/v3/ticker/24hr")
    rows = []
    for t in tickers:
        s = t["symbol"]
        if not s.isascii() or not s.endswith("USDT") or s in STABLES or s in WRAPPED:
            continue
        try:
            rows.append((s, float(t["quoteVolume"])))
        except (KeyError, ValueError):
            continue
    rows.sort(key=lambda x: -x[1])
    found = []
    for i, (s, _) in enumerate(rows[:top_n]):
        time.sleep(delay)
        try:
            raw = get(SPOT, f"/api/v3/klines?symbol={s}&interval=1d&limit={max_age_days + 6}")
            kl = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
                  for k in raw]
            # 口径统一：根数 = 已收盘日线数，"≤30 根即上市不足 30 天"才成立
            kl, _ = ku.drop_open_bar(kl, ku.D1)
            if len(kl) <= max_age_days:
                found.append((s, kl))
        except Exception as e:
            log(f"      [skip] {s}: {e}")
        if (i + 1) % 50 == 0:
            log(f"      新币探测 {i + 1}/{min(top_n, len(rows))}（已发现 {len(found)}）...")
    return found


def futures_whale(symbol):
    """L1: top trader long/short ratios. Returns (pos_ratio, acc_ratio) or (None, None)."""
    pos = acc = None
    try:
        d = get(FAPI, f"/futures/data/topLongShortPositionRatio?symbol={symbol}&period=1d&limit=3")
        if d:
            pos = float(d[-1]["longShortRatio"])
    except Exception:
        pass
    try:
        d = get(FAPI, f"/futures/data/topLongShortAccountRatio?symbol={symbol}&period=1d&limit=3")
        if d:
            acc = float(d[-1]["longShortRatio"])
    except Exception:
        pass
    return pos, acc


def large_trade_flow(symbol):
    """L2: net flow of >$100k prints in last 1000 spot trades."""
    trades = get(SPOT, f"/api/v3/aggTrades?symbol={symbol}&limit=1000")
    buy = sell = 0.0
    for t in trades:
        usd = float(t["p"]) * float(t["q"])
        if usd < LARGE_TRADE_USD:
            continue
        if t["m"]:      # buyer is maker => taker sold
            sell += usd
        else:
            buy += usd
    return {"large_buy": round(buy), "large_sell": round(sell),
            "net": round(buy - sell), "prints": int((buy + sell) > 0)}


def holder_concentration(symbol, cg_cache):
    """L3: top-10 holder share via Etherscan (EVM only, needs API key). Best-effort."""
    key = os.environ.get("ETHERSCAN_API_KEY")
    if not key:
        return None
    base = symbol.replace("USDT", "")
    try:
        if base not in cg_cache:
            res = get(CG, f"/search?query={urllib.parse.quote(base)}")
            coin_id = next((c["id"] for c in res.get("coins", [])
                            if c.get("symbol", "").upper() == base), None)
            cg_cache[base] = coin_id
        coin_id = cg_cache[base]
        if not coin_id:
            return None
        info = get(CG, f"/coins/{coin_id}?localization=false&tickers=false"
                       f"&market_data=false&community_data=false&developer_data=false")
        contract = (info.get("platforms") or {}).get("ethereum")
        if not contract:
            return None
        # Etherscan: total supply + top holders are pro-only; use tokenholdercount as弱代理
        d = get("https://api.etherscan.io",
                f"/api?module=token&action=tokenholdercount&contractaddress={contract}"
                f"&apikey={key}")
        n = int(d.get("result", 0) or 0)
        return {"holder_count": n, "note": "免费API无法获取Top10占比，仅持币地址数"}
    except Exception:
        return None


def supply_info(symbol, cg_cache):
    base = symbol.replace("USDT", "")
    try:
        if base not in cg_cache:
            res = get(CG, f"/search?query={urllib.parse.quote(base)}")
            cg_cache[base] = next((c["id"] for c in res.get("coins", [])
                                   if c.get("symbol", "").upper() == base), None)
        if not cg_cache[base]:
            return None
        m = get(CG, f"/coins/markets?vs_currency=usd&ids={cg_cache[base]}")
        if not m:
            return None
        m = m[0]
        ratio = m["market_cap"] / m["fully_diluted_valuation"] if m.get("fully_diluted_valuation") else None
        return {"market_cap": m.get("market_cap"), "fdv": m.get("fully_diluted_valuation"),
                "float_ratio": round(ratio, 3) if ratio else None}
    except Exception:
        return None


def whale_score(symbol, delay, log, cg_cache):
    """Returns (score, max_score, detail)."""
    score, detail = 0, {}

    pos, acc = futures_whale(symbol)
    detail["top_trader_position_ratio"] = pos
    detail["top_trader_account_ratio"] = acc
    if pos is not None:
        score += 3 if pos > 1.2 else (2 if pos >= 1.0 else (1 if pos >= 0.8 else 0))
    if acc is not None:
        score += 2 if acc > 1.2 else (1 if acc >= 1.0 else 0)

    time.sleep(delay)
    try:
        flow = large_trade_flow(symbol)
        detail["large_flow"] = flow
        net = flow["net"]
        score += 3 if net > 50_000 else (2 if net > 0 else (1 if net > -50_000 else 0))
    except Exception as e:
        detail["large_flow"] = {"error": str(e)}

    time.sleep(delay)
    conc = holder_concentration(symbol, cg_cache)
    detail["concentration"] = conc
    max_score = 15 if conc and "top10_share" in (conc or {}) else 10
    # L3 以持币地址数为弱代理时不得分，仅展示
    return score, max_score, detail


def evaluate_second_wave(symbol, kl, wscore, wmax):
    d1 = kl[0]
    d1_low, d1_vol = float(d1[3]), float(d1[5])
    rest = kl[1:]
    close = float(kl[-1][4])
    age = len(kl) - 1  # days since listing day

    conds = []
    conds.append(("上市满2天（首日不开仓）", age >= 2))
    if rest:
        lowest_after = min(float(k[3]) for k in rest)
        conds.append(("未破首日低点", lowest_after >= d1_low * 0.97))
    else:
        conds.append(("未破首日低点", False))
    if len(rest) >= 3:
        v3 = sum(float(k[5]) for k in rest[-3:]) / 3
        conds.append(("缩量（近3日均量<首日50%）", d1_vol > 0 and v3 < d1_vol * 0.5))
        hi = max(float(k[2]) for k in rest[-3:])
        lo = min(float(k[3]) for k in rest[-3:])
        conds.append(("企稳（近3日振幅<15%）", close > 0 and (hi - lo) / close < 0.15))
    else:
        conds.append(("缩量（近3日均量<首日50%）", False))
        conds.append(("企稳（近3日振幅<15%）", False))
    threshold = 9 if wmax == 15 else 6
    conds.append((f"鲸鱼行为分≥{threshold}/{wmax}", wscore >= threshold))

    passed = all(ok for _, ok in conds)
    return {
        "age_days": age, "close": close,
        "day1": {"open": float(d1[1]), "high": float(d1[2]), "low": d1_low, "volume": d1_vol},
        "vs_day1_open_pct": round((close / float(d1[1]) - 1) * 100, 1) if float(d1[1]) else None,
        "conditions": [{"rule": r, "ok": ok} for r, ok in conds],
        "second_wave_ready": passed,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=300, help="检查成交额前N的USDT对")
    ap.add_argument("--max-age-days", type=int, default=30)
    ap.add_argument("--delay", type=float, default=0.25)
    ap.add_argument("--out", default="new_listings.json")
    ap.add_argument("--md", default="new_listings_report.md")
    args = ap.parse_args()

    log = lambda m: print(m, flush=True)
    t0 = time.time()
    log(f"[1/3] 探测上市 ≤{args.max_age_days} 天的新币（检查成交额前 {args.top}）...")
    found = discover_new_candicates(args.top, args.max_age_days, args.delay, log)
    log(f"      发现新币 {len(found)} 个: {', '.join(s for s, _ in found) or '无'}")

    log("[2/3] 鲸鱼数据与第二波条件评估 ...")
    cg_cache = {}
    cards = []
    for s, kl in found:
        log(f"      评估 {s} ...")
        time.sleep(args.delay)
        wscore, wmax, wdetail = whale_score(s, args.delay, log, cg_cache)
        ev = evaluate_second_wave(s, kl, wscore, wmax)
        supply = supply_info(s, cg_cache)
        cards.append({"symbol": s, "whale_score": wscore, "whale_max": wmax,
                      "whale_detail": wdetail, "evaluation": ev, "supply": supply})

    cards.sort(key=lambda c: (not c["evaluation"]["second_wave_ready"], -c["whale_score"]))
    out = {"generated_at": datetime.now(TZ8).isoformat(),
           "params": vars(args), "new_coins": len(cards), "cards": cards,
           "exit_rules": {"position_cap": "≤10% 总仓位", "time_stop": "72小时",
                          "price_stop": "-8%", "day1_rule": "上市首日绝不开仓"}}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    log("[3/3] 生成报告 ...")
    L = [f"# 新币打新评估报告（{out['generated_at'][:16]} UTC+8）\n"]
    L.append(f"- 探测范围：成交额前 {args.top} 的 USDT 对，上市 ≤{args.max_age_days} 天")
    L.append(f"- 发现新币 {len(cards)} 个；满足第二波条件 "
             f"{sum(1 for c in cards if c['evaluation']['second_wave_ready'])} 个")
    L.append(f"- 铁律：首日不开仓 ｜ 仓位 ≤10% ｜ 时间止损 72h ｜ 价格止损 -8%\n")
    for c in cards:
        e, s = c["evaluation"], c["symbol"]
        mark = "✅ 第二波条件满足" if e["second_wave_ready"] else "⏳ 未满足"
        L.append(f"## {s} — {mark}（鲸鱼分 {c['whale_score']}/{c['whale_max']}）")
        L.append(f"- 上市 {e['age_days']} 天 ｜ 现价 {e['close']} ｜ 较首日开盘 {e['vs_day1_open_pct']}%")
        L.append(f"- 首日：开 {e['day1']['open']} 高 {e['day1']['high']} 低 {e['day1']['low']}")
        for cd in e["conditions"]:
            L.append(f"  - {'✅' if cd['ok'] else '❌'} {cd['rule']}")
        wd = c["whale_detail"]
        L.append(f"- 顶级账户持仓多空比 {wd.get('top_trader_position_ratio')} / 账户多空比 {wd.get('top_trader_account_ratio')}")
        if isinstance(wd.get("large_flow"), dict) and "net" in wd["large_flow"]:
            f0 = wd["large_flow"]
            L.append(f"- 大单流向（近1000笔，>$100k）：买 {f0['large_buy']:,}U / 卖 {f0['large_sell']:,}U / 净 {f0['net']:,}U")
        if c.get("supply") and c["supply"].get("float_ratio"):
            L.append(f"- 流通占比 {c['supply']['float_ratio']*100:.0f}%（MC/FDV，低流通高FDV警惕解锁抛压）")
        if wd.get("concentration"):
            L.append(f"- 链上持币：{wd['concentration'].get('note', wd['concentration'])}")
        L.append("")
    L.append("---\n**免责声明**：新币上市波动极端剧烈，本评估仅为数据参考，不构成投资建议。"
             "第二波规则不保证盈利，时间/价格止损必须无条件执行。")
    with open(args.md, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    log(f"\n完成，耗时 {round(time.time()-t0,1)}s -> {args.out} / {args.md}")


if __name__ == "__main__":
    main()
