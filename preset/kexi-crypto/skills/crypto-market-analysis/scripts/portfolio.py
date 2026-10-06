#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio.py - 组合层量化分析：相关性矩阵、等风险仓位预算与 Amihud 流动性冲击

补齐分析层三大能力空白：
  [G2] 资产间相关性评估（假分散预警，防多标的实为同一单边 Beta 敞口）
  [G3] 等风险预算仓位管理（基于 ATR14 波动率动态分配名义本金）
  [G6] Amihud 非流动性冲击评估（度量单位成交金额引起的价格位移）

Usage:
  python portfolio.py --input scan_result.json --klines-dir klines/ --out portfolio.json --md portfolio.md
  python portfolio.py --input scan_result.json --klines-dir klines/ --capital 10000 --risk-per-trade 0.005 --max-weight 0.2

Stdlib only.
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import kline_utils as ku

TZ8 = timezone(timedelta(hours=8))
CORR_THRESHOLD = 0.85
DISCLAIMER = "不构成投资建议"


def load_picks(input_path):
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("picked") or data.get("picks") or []
    return data if isinstance(data, list) else []


def find_kline_file(klines_dir, symbol):
    names = [f"{symbol}_1d.json", f"{symbol.upper()}_1d.json", f"{symbol}.json"]
    if not symbol.upper().endswith("USDT"):
        names.extend([f"{symbol.upper()}USDT_1d.json", f"{symbol.upper()}USDT.json"])
    for name in names:
        p = os.path.join(klines_dir, name)
        if os.path.isfile(p):
            return p
    return None


def load_closed_klines(klines_dir, symbol):
    path = find_kline_file(klines_dir, symbol)
    if not path:
        return None, f"缺失K线文件 ({symbol}_1d.json)"
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f).get("klines", [])
        if not raw:
            return None, "K线数据为空"
        closed, _ = ku.drop_open_bar(raw, ku.D1)
        if len(closed) < 15:
            return None, f"已收盘K线不足15根(仅{len(closed)})"
        return closed, None
    except Exception as e:
        return None, f"读取K线异常: {e}"


def calc_amihud(closed, lookback=60):
    bars = closed[-lookback:] if len(closed) > lookback else closed
    vals = []
    for i in range(1, len(bars)):
        pc, c, v = bars[i - 1][4], bars[i][4], bars[i][5]
        if pc > 0 and (c * v) > 0:
            vals.append((abs(c - pc) / pc) / (c * v))
    return (sum(vals) / len(vals) * 1e12) if vals else None


def calc_pearson_corr(series_a, series_b, min_bars=10):
    common_ts = sorted(set(series_a.keys()) & set(series_b.keys()))[-60:]
    if len(common_ts) < min_bars:
        return None, len(common_ts)
    x = [series_a[t] for t in common_ts]
    y = [series_b[t] for t in common_ts]
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    vx, vy = sum((xi - mx) ** 2 for xi in x), sum((yi - my) ** 2 for yi in y)
    if vx > 1e-16 and vy > 1e-16:
        return round(max(-1.0, min(1.0, cov / math.sqrt(vx * vy))), 3), n
    return 0.0, n


def analyze(picks, klines_dir, capital, risk_per_trade, max_weight):
    positions, ret_map = [], {}
    for p in picks:
        sym = (p.get("symbol") if isinstance(p, dict) else str(p)).upper()
        closed, err = load_closed_klines(klines_dir, sym)
        row = {"symbol": sym, "close": None, "atr14": None, "atr_pct": None,
               "weight": None, "notional": None, "amihud": None, "error": err}
        if closed:
            c = float(p.get("close") if isinstance(p, dict) and p.get("close") else closed[-1][4])
            a14 = ku.atr(closed, 14)
            row["close"] = ku.rn(c)
            row["atr14"] = ku.rn(a14)
            if a14 and c > 0:
                atr_pct = a14 / c
                row["atr_pct"] = round(atr_pct * 100, 2)
                w = min(risk_per_trade / atr_pct, max_weight)
                row["weight"] = round(w, 4)
                row["notional"] = round(capital * w, 2)
            row["amihud"] = ku.rn(calc_amihud(closed))
            ret_map[sym] = {closed[i][0]: (closed[i][4] - closed[i - 1][4]) / closed[i - 1][4]
                            for i in range(1, len(closed)) if closed[i - 1][4] > 0}
        positions.append(row)

    symbols = [p["symbol"] for p in positions]
    matrix = {s: {} for s in symbols}
    high_corr_pairs = []
    for i, s1 in enumerate(symbols):
        matrix[s1][s1] = 1.0
        for s2 in symbols[i + 1:]:
            if s1 in ret_map and s2 in ret_map:
                r, n = calc_pearson_corr(ret_map[s1], ret_map[s2])
                matrix[s1][s2] = matrix[s2][s1] = r
                if r is not None and r > CORR_THRESHOLD:
                    high_corr_pairs.append({
                        "pair": f"{s1}/{s2}", "corr": r, "bars": n,
                        "warning": f"相关系数 {r:.2f} (>{CORR_THRESHOLD})，存在假分散风险",
                    })
            else:
                matrix[s1][s2] = matrix[s2][s1] = None

    tot_notional = sum(p["notional"] for p in positions if p["notional"] is not None)
    tot_weight = sum(p["weight"] for p in positions if p["weight"] is not None)
    cash = round(capital - tot_notional, 2)
    return {
        "disclaimer": DISCLAIMER,
        "generated_at": datetime.now(TZ8).isoformat(),
        "capital": capital,
        "risk_per_trade": risk_per_trade,
        "max_weight": max_weight,
        "total_allocated_notional": round(tot_notional, 2),
        "total_allocated_weight": round(tot_weight, 4),
        "cash_residual": cash,
        "cash_weight": round(cash / capital, 4) if capital > 0 else 0.0,
        "fake_diversification": len(high_corr_pairs) > 0,
        "high_corr_pairs": high_corr_pairs,
        "positions": positions,
        "correlation_matrix": matrix,
    }


def to_markdown(res):
    L = [
        f"# 投资组合风险预算与相关性分析报告（{res['generated_at'][:16]} UTC+8）\n",
        f"- **总资本**：{res['capital']:,.2f} USDT | **单笔风险预算**：{res['risk_per_trade']*100:.2f}% | "
        f"**单标的权重上限**：{res['max_weight']*100:.1f}%",
        f"- **已配名义本金**：{res['total_allocated_notional']:,.2f} USDT ({res['total_allocated_weight']*100:.2f}%) | "
        f"**留存现金**：{res['cash_residual']:,.2f} USDT ({res['cash_weight']*100:.2f}%)",
        f"- **假分散检测**：{'⚠️ 警惕：存在高度相关标的对，警惕同向联动风险！' if res['fake_diversification'] else '✅ 良好：无标的对相关系数超限，分散度合格'}\n",
        "## 1. 标的仓位预算与 Amihud 冲击度量",
        "| # | 标的 | 收盘价 | ATR14 | ATR% | 建议权重 | 建议金额(U) | Amihud冲击 | 状态/异常 |",
        "|---|------|--------|-------|------|----------|-------------|------------|-----------|",
    ]
    for i, p in enumerate(res["positions"], 1):
        err = p["error"] if p["error"] else "正常"
        w_str = f"{p['weight']*100:.2f}%" if p["weight"] is not None else "-"
        notional_str = f"{p['notional']:,.2f}" if p["notional"] is not None else "-"
        c_str = str(p["close"]) if p["close"] is not None else "-"
        atr_str = str(p["atr14"]) if p["atr14"] is not None else "-"
        atrp_str = f"{p['atr_pct']:.2f}%" if p["atr_pct"] is not None else "-"
        amihud_str = f"{p['amihud']:.2f}" if p["amihud"] is not None else "-"
        L.append(f"| {i} | {p['symbol']} | {c_str} | {atr_str} | {atrp_str} | {w_str} | {notional_str} | {amihud_str} | {err} |")

    symbols = [p["symbol"] for p in res["positions"]]
    if len(symbols) > 1:
        L.extend(["\n## 2. 日线收益率相关性矩阵（近60根对齐收盘K线）",
                  "| 标的 | " + " | ".join(symbols) + " |",
                  "|------|" + "|".join(["------"] * len(symbols)) + "|"])
        for s1 in symbols:
            row = [f"| {s1} "]
            for s2 in symbols:
                val = res["correlation_matrix"].get(s1, {}).get(s2)
                row.append("-" if val is None else (f"**{val:.3f}** ★" if s1 != s2 and val > CORR_THRESHOLD else f"{val:.3f}"))
            L.append(" | ".join(row) + " |")
        L.append("\n> 注：★ 标记表示相关系数 > 0.85，提示同向共振风险。")

    if res["high_corr_pairs"]:
        L.append("\n## 3. 假分散预警明细")
        for h in res["high_corr_pairs"]:
            L.append(f"- **{h['pair']}**：{h['warning']}（样本对齐 {h['bars']} 根K线）")

    L.append(f"\n---\n**免责声明**：{res['disclaimer']}。加密资产波动剧烈，请严格执行风控纪律。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Portfolio risk budgeting & correlation analysis")
    ap.add_argument("--input", required=True, help="screener result JSON path")
    ap.add_argument("--klines-dir", default="klines", help="directory of <SYMBOL>_1d.json files")
    ap.add_argument("--capital", type=float, default=10000.0, help="total capital in USDT")
    ap.add_argument("--risk-per-trade", type=float, default=0.005, help="risk budget fraction per trade (default 0.005 = 0.5%%)")
    ap.add_argument("--max-weight", type=float, default=0.2, help="max weight per asset (default 0.2 = 20%%)")
    ap.add_argument("--out", default="portfolio.json", help="output JSON path")
    ap.add_argument("--md", default="portfolio.md", help="output Markdown path")
    args = ap.parse_args()

    picks = load_picks(args.input)
    res = analyze(picks, args.klines_dir, args.capital, args.risk_per_trade, args.max_weight)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    with open(args.md, "w", encoding="utf-8") as f:
        f.write(to_markdown(res))

    print(f"saved portfolio -> {args.out} and {args.md} (positions={len(res['positions'])}, "
          f"allocated={res['total_allocated_notional']}U, fake_div={res['fake_diversification']})")


if __name__ == "__main__":
    main()
