#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
indicators.py - Compute technical indicators from kline JSON produced by fetch_klines.py.

v1.1-patched 相对专家包 v1.0.0 的修改（逐条对应审查报告中的缺陷编号）：
  [P0-4] 所有价格固定 round(x,2)，PEPE 等小价格币 close/MA/MACD 全部塌成 0.0
         —— 改为按数量级自适应精度。
  [P0-5] "整数心理关口" 用 10**(位数-2) 取步长，DOGE 产生 1.0 USDT（+923%）、
         低价币产生 -1.0 —— 改为按价格量级挑选 1/2/5×10^k 的合理步长。
  [P0-2] 末根未收盘 bar 污染 MA/MACD/RSI/量比 —— 默认剔除（--include-open-bar 保留）。
  [P1-6] volume_ratio = 末根量 / vol_ma5，而 vol_ma5 含末根自身 → 双重低估
         —— 改为与"剔除末根的 5 日均量"比较，并保留含末根口径以便对照。
  [P1-7] find_crosses 只返回 bar_index 不返回日期，且只认 MA5/MA20
         —— 补日期，并增加 MACD(DIF/DEA) 金叉死叉。
  [P1-8] hist 只给最后一根与上一根，"连续 N 日收敛"须人工数
         —— 新增 hist 正负号、扩张/收敛、连续步数、末 6 根序列。
  [P1-9] divergence_hint 是常量字符串（假字段） —— 改为真实扫描并明确标注"候选"。
  [P1-10] 守拙声明的年化波动率/最大回撤无任何实现 —— 新增 7/30 日年化波动率、30 日最大回撤。
  [P1-11] 交易计划需要的 ATR 缺失 —— 新增 ATR14 与 ATR 占价比。
  [P2-12] 关键位重复（同一价位挂两个依据） —— 去重并保留最强依据。

Usage:
  python indicators.py --input btc_1d.json --out indicators_result.json
  python indicators.py --input btc_1d.json --include-open-bar --out out.json

Output: 在 v1.0.0 原有字段基础上扩展（原字段名全部保留，便于下游兼容）。
Only stdlib is used.
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}


# ---------- 基础工具 ----------

def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def ema_series(vals, n):
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def decimals_for(x):
    """按数量级决定小数位，避免小价格币被 round(,2) 抹成 0。"""
    if x is None:
        return 2
    a = abs(x)
    if a >= 1000:
        return 2
    if a >= 1:
        return 4
    if a >= 0.01:
        return 5
    if a >= 0.0001:
        return 7
    if a >= 1e-6:
        return 9
    return 12


def rn(x):
    """自适应精度取整（替代固定 r2/r4）。"""
    if x is None:
        return None
    return round(x, decimals_for(x))


def r4(x):
    return None if x is None else round(x, 4)


# ---------- 指标 ----------

def macd(closes, fast=12, slow=26, signal=9):
    """Standard MACD. Returns (dif_series, dea_series, hist_series) or None."""
    if len(closes) < slow + signal:
        return None
    kf, ks = 2 / (fast + 1), 2 / (slow + 1)
    ef = sum(closes[:fast]) / fast
    es = sum(closes[:slow]) / slow
    dif_series = []
    for i, c in enumerate(closes):
        if i >= fast:
            ef = c * kf + ef * (1 - kf)
        if i >= slow:
            es = c * ks + es * (1 - ks)
        if i >= slow - 1:
            dif_series.append(ef - es)
    kd = 2 / (signal + 1)
    dea = sum(dif_series[:signal]) / signal
    dea_series = [dea]
    for d in dif_series[signal:]:
        dea = d * kd + dea * (1 - kd)
        dea_series.append(dea)
    dif_tail = dif_series[len(dif_series) - len(dea_series):]
    hist = [(d - e) * 2 for d, e in zip(dif_tail, dea_series)]
    return dif_tail, dea_series, hist


def rsi_series(closes, n):
    """Wilder RSI 序列，前 n 个为 None。"""
    out = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0)
        losses += max(-ch, 0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(ch, 0)) / n
        al = (al * (n - 1) + max(-ch, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def rsi(closes, n):
    s = rsi_series(closes, n)
    return s[-1] if s else None


def atr(klines, n=14):
    """Wilder ATR（绝对价格）。"""
    if len(klines) < n + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i][2], klines[i][3], klines[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def ann_vol(closes, n):
    """近 n 根日线的年化波动率（%，加密 7×24 按 365 天）。"""
    if len(closes) < n + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(365) * 100


def max_dd(closes, n):
    """近 n 根最大回撤（%，负值）。"""
    seg = closes[-n:] if len(closes) >= n else closes
    peak, mdd = seg[0], 0.0
    for c in seg:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    return mdd * 100


def pct_change(closes, n):
    if len(closes) < n + 1:
        return None
    return (closes[-1] / closes[-1 - n] - 1) * 100


def ma_alignment(ma5, ma10, ma20, ma60):
    vals = [ma5, ma10, ma20, ma60]
    if any(v is None for v in vals[:3]):
        return "insufficient"
    if ma60 is not None and ma5 > ma10 > ma20 > ma60:
        return "bullish"
    if ma60 is not None and ma5 < ma10 < ma20 < ma60:
        return "bearish"
    if ma5 > ma10 > ma20:
        return "bullish"
    if ma5 < ma10 < ma20:
        return "bearish"
    return "tangled"


def hist_stats(hist):
    """柱体正负号 / 扩张收敛 / 连续步数 / 末 6 根。"""
    if not hist:
        return {}
    mag = [abs(x) for x in hist]
    sign = "正" if hist[-1] >= 0 else "负"
    if len(mag) < 2:
        return {"hist_sign": sign, "hist_direction": "未知", "hist_direction_steps": 0,
                "hist_last6": [rn(x) for x in hist[-6:]]}
    direction = "扩张" if mag[-1] > mag[-2] else ("收敛" if mag[-1] < mag[-2] else "持平")
    steps = 0
    for k in range(len(mag) - 2, -1, -1):
        if direction == "扩张" and mag[k + 1] > mag[k]:
            steps += 1
        elif direction == "收敛" and mag[k + 1] < mag[k]:
            steps += 1
        else:
            break
    return {"hist_sign": sign, "hist_direction": direction,
            "hist_direction_steps": steps, "hist_last6": [rn(x) for x in hist[-6:]]}


def find_crosses(klines, closes, lookback=10):
    """近 lookback 根内的 MA5/MA20 与 MACD 金叉死叉，含日期。"""
    out = []
    n = len(closes)

    def d(i):
        return datetime.fromtimestamp(klines[i][0] / 1000, TZ8).strftime("%Y-%m-%d")

    if n >= 20 + lookback:
        for i in range(n - lookback, n):
            if i < 20:
                continue
            f_prev = sum(closes[i - 5:i]) / 5
            s_prev = sum(closes[i - 20:i]) / 20
            f_cur = sum(closes[i - 4:i + 1]) / 5
            s_cur = sum(closes[i - 19:i + 1]) / 20
            if f_prev <= s_prev and f_cur > s_cur:
                out.append({"type": "golden_cross", "indicator": "MA5/MA20",
                            "bar_index": i, "date": d(i)})
            elif f_prev >= s_prev and f_cur < s_cur:
                out.append({"type": "dead_cross", "indicator": "MA5/MA20",
                            "bar_index": i, "date": d(i)})

    m = macd(closes)
    if m:
        dif_s, dea_s, _ = m
        off = n - len(dif_s)
        for j in range(max(1, len(dif_s) - lookback), len(dif_s)):
            i = off + j
            if dif_s[j - 1] <= dea_s[j - 1] and dif_s[j] > dea_s[j]:
                out.append({"type": "golden_cross", "indicator": "MACD(DIF/DEA)",
                            "bar_index": i, "date": d(i)})
            elif dif_s[j - 1] >= dea_s[j - 1] and dif_s[j] < dea_s[j]:
                out.append({"type": "dead_cross", "indicator": "MACD(DIF/DEA)",
                            "bar_index": i, "date": d(i)})
    out.sort(key=lambda x: x["bar_index"])
    return out


def divergence_scan(klines, closes, rsi14_s, win=20):
    """基于相邻两个 win 周期摆动高/低点的背离候选（须人工确认）。"""
    n = len(closes)
    if n < win * 2 + 1:
        return {"bearish_hint": "样本不足", "bullish_hint": "样本不足"}
    hi1 = max(range(n - 2 * win, n - win), key=lambda x: klines[x][2])
    hi2 = max(range(n - win, n), key=lambda x: klines[x][2])
    lo1 = min(range(n - 2 * win, n - win), key=lambda x: klines[x][3])
    lo2 = min(range(n - win, n), key=lambda x: klines[x][3])
    res = {}
    if rsi14_s[hi1] is not None and rsi14_s[hi2] is not None and klines[hi2][2] > klines[hi1][2]:
        res["bearish_hint"] = ("顶背离候选：价格创高而 RSI14 未创高 "
                               f"({klines[hi1][2]}→{klines[hi2][2]}, RSI {rsi14_s[hi1]:.2f}→{rsi14_s[hi2]:.2f})"
                               if rsi14_s[hi2] < rsi14_s[hi1] else "无")
    else:
        res["bearish_hint"] = "无"
    if rsi14_s[lo1] is not None and rsi14_s[lo2] is not None and klines[lo2][3] < klines[lo1][3]:
        res["bullish_hint"] = ("底背离候选：价格创新低而 RSI14 未创新低 "
                               f"({klines[lo1][3]}→{klines[lo2][3]}, RSI {rsi14_s[lo1]:.2f}→{rsi14_s[lo2]:.2f})"
                               if rsi14_s[lo2] > rsi14_s[lo1] else "无")
    else:
        res["bullish_hint"] = "无"
    return res


def nice_step(price):
    """选取"整数心理关口"步长：1/2/5×10^k 中不超过价格 5% 的最大者。"""
    if price <= 0:
        return 1.0
    exp = math.floor(math.log10(price))
    target = price * 0.05
    best = None
    for k in range(exp - 3, exp + 2):
        for m in (1, 2, 5):
            s = m * 10 ** k
            if s <= target:
                best = s if best is None else max(best, s)
    return best if best else price * 0.01


def support_resistance(klines, closes, ma_vals):
    """Candidate S/R: swing highs/lows, MAs, 合理量级的整数关口。去重保留最强依据。"""
    n = len(klines)
    res, sup = [], []

    for lookback, label in [(20, "近20周期"), (60, "近60周期")]:
        seg = klines[-lookback:] if n >= lookback else klines
        if not seg:
            continue
        hi = max(k[2] for k in seg)
        lo = min(k[3] for k in seg)
        res.append({"price": rn(hi), "basis": f"{label}最高点",
                    "strength": "强" if lookback == 60 else "中"})
        sup.append({"price": rn(lo), "basis": f"{label}最低点",
                    "strength": "强" if lookback == 60 else "中"})

    for name, v in ma_vals.items():
        if v is None:
            continue
        entry = {"price": rn(v), "basis": f"{name.upper()} 均线位", "strength": "中"}
        (sup if v < closes[-1] else res).append(entry)

    c = closes[-1]
    step = nice_step(c)
    base = round(c / step)
    for k in (-2, -1, 1, 2):
        lvl = (base + k) * step
        if lvl <= 0:
            continue
        entry = {"price": rn(lvl), "basis": f"整数心理关口({step:g})", "strength": "弱"}
        (res if lvl > c else sup).append(entry)

    rank = {"强": 3, "中": 2, "弱": 1}

    def dedupe(lst, asc):
        best = {}
        for e in lst:
            key = e["price"]
            if key not in best or rank[e["strength"]] > rank[best[key]["strength"]]:
                best[key] = e
        out = sorted(best.values(), key=lambda x: x["price"], reverse=not asc)
        return out

    # 过滤噪声：距现价 <0.5% 的位不构成有效支撑/阻力
    res = [e for e in res if e["price"] > c * 1.005]
    sup = [e for e in sup if e["price"] < c * 0.995]
    return {"resistance": dedupe(res, True)[:4], "support": dedupe(sup, False)[:4],
            "round_step": rn(step)}


# ---------- 主流程 ----------

def drop_open_bar(klines, interval):
    step = INTERVAL_MS.get(interval)
    if not step or not klines:
        step = None
        if len(klines) > 3:
            ds = sorted(klines[i + 1][0] - klines[i][0] for i in range(len(klines) - 1))
            step = ds[len(ds) // 2]
    if not step:
        return klines, 0
    now_ms = int(time.time() * 1000)
    closed = [k for k in klines if k[0] + step <= now_ms]
    return closed, len(klines) - len(closed)


def volume_profile(klines, bins=24, lookback=120):
    """密集成交区 / 筹码峰识别（G4）：按价格分箱统计成交量分布。

    每根 bar 的成交量按 (H-L) 内覆盖到的价格箱均摊；H==L 时计入该价格箱。
    返回 point_of_control（最大筹码峰 POC）、value_area（70% 价值区 VAHigh/VALow）、
    HVN（高成交量节点，强支撑/阻力）、LVN（低成交量节点，易快速穿越的真空区）
    及 bins 全量序列（供看板横向直方图）。数据无成交量（如 CoinGecko）时 available=False。
    """
    sub = klines[-lookback:] if len(klines) > lookback else klines
    hi = max(k[2] for k in sub)
    lo = min(k[3] for k in sub)
    total_v = sum(k[5] for k in sub)
    if total_v <= 0 or hi <= lo:
        return {"available": False, "note": "数据无成交量，筹码峰不可用"}
    step = (hi - lo) / bins
    vols_b = [0.0] * bins
    for k in sub:
        o0, h0, l0, c0, v0 = k[1], k[2], k[3], k[4], k[5]
        if v0 <= 0:
            continue
        if h0 <= l0:
            idx = min(int((c0 - lo) / step), bins - 1)
            vols_b[idx] += v0
            continue
        i_lo = min(max(int((l0 - lo) / step), 0), bins - 1)
        i_hi = min(max(int((h0 - lo) / step), 0), bins - 1)
        n = i_hi - i_lo + 1
        for j in range(i_lo, i_hi + 1):
            vols_b[j] += v0 / n
    price_b = [rn(lo + (j + 0.5) * step) for j in range(bins)]
    poc_i = max(range(bins), key=lambda j: vols_b[j])
    poc_v = vols_b[poc_i]
    # 价值区：从 POC 向两侧累加至 70% 成交量
    target = 0.70 * total_v
    acc = poc_v
    up_i = dn_i = poc_i
    while acc < target and (up_i + 1 < bins or dn_i - 1 >= 0):
        vu = vols_b[up_i + 1] if up_i + 1 < bins else -1
        vd = vols_b[dn_i - 1] if dn_i - 1 >= 0 else -1
        if vu >= vd and vu >= 0:
            up_i += 1
            acc += vu
        elif vd >= 0:
            dn_i -= 1
            acc += vd
        else:
            break
    hvn, lvnn = [], []
    for j in range(bins):
        tag = "normal"
        if j == poc_i:
            tag = "POC"
        elif poc_v > 0 and vols_b[j] >= 0.6 * poc_v:
            tag = "HVN"
            hvn.append({"price": price_b[j], "vol": rn(vols_b[j]), "pct": r4(100 * vols_b[j] / total_v)})
        elif poc_v > 0 and vols_b[j] <= 0.2 * poc_v:
            tag = "LVN"
            lvnn.append({"price": price_b[j], "vol": rn(vols_b[j]), "pct": r4(100 * vols_b[j] / total_v)})
    return {
        "available": True,
        "lookback_bars": len(sub),
        "bins_count": bins,
        "point_of_control": {"price": price_b[poc_i], "vol": rn(poc_v), "pct": r4(100 * poc_v / total_v)},
        "value_area": {
            "va_high": price_b[up_i], "va_low": price_b[dn_i],
            "covers_pct": r4(100 * acc / total_v),
        },
        "hvn": hvn,
        "lvn": lvnn,
        "bins": [{"price": price_b[j], "vol": rn(vols_b[j]),
                  "pct": r4(100 * vols_b[j] / total_v), "tag": ("POC" if j == poc_i else "HVN" if (poc_v > 0 and vols_b[j] >= 0.6 * poc_v) else "LVN" if (poc_v > 0 and vols_b[j] <= 0.2 * poc_v) else "normal")}
                 for j in range(bins)],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--include-open-bar", action="store_true",
                    help="保留尚未收盘的最后一根 bar（默认剔除）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("error"):
        print(json.dumps({"error": f"input has error: {data['error']}"}, ensure_ascii=False))
        sys.exit(2)

    klines = data["klines"]
    warnings = list(data.get("warnings", []))
    dropped = 0
    if not args.include_open_bar:
        klines, dropped = drop_open_bar(klines, data.get("interval", "1d"))
        if dropped:
            warnings.append(f"已剔除 {dropped} 根未收盘 bar：所有指标基于最后一根已收盘 K 线")

    if len(klines) < 2:
        print(json.dumps({"error": "not enough klines"}, ensure_ascii=False))
        sys.exit(2)

    closes = [k[4] for k in klines]
    vols = [k[5] for k in klines]
    if len(klines) < 60:
        warnings.append("样本 <60 根：MA60、MACD 等长周期指标仅供参考")

    ma5, ma10, ma20, ma60 = sma(closes, 5), sma(closes, 10), sma(closes, 20), sma(closes, 60)
    ma_vals = {"ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60}

    m = macd(closes)
    macd_out = None
    if m:
        dif_s, dea_s, hist_s = m
        macd_out = {
            "dif": rn(dif_s[-1]), "dea": rn(dea_s[-1]),
            "hist": rn(hist_s[-1]),
            "hist_prev": rn(hist_s[-2] if len(hist_s) > 1 else None),
            "above_zero": dif_s[-1] > 0,
            "hist_expanding": (len(hist_s) > 1 and abs(hist_s[-1]) > abs(hist_s[-2])),
        }
        macd_out.update(hist_stats(hist_s))
    else:
        warnings.append("样本不足以计算 MACD（需 ≥35 根），macd 字段为 null")

    rsi6_s, rsi14_s = rsi_series(closes, 6), rsi_series(closes, 14)
    rsi6, rsi14 = rsi6_s[-1], rsi14_s[-1]

    vol_ma5_prior = sma(vols[:-1], 5)          # 剔除末根自身，避免双重低估
    vol_ma5_incl = sma(vols, 5)
    vol_ma20 = sma(vols, 20)
    has_volume = any(v > 0 for v in vols[-20:])
    volume_out = {"available": has_volume}
    if has_volume:
        ratio = (vols[-1] / vol_ma5_prior) if vol_ma5_prior else None
        volume_out.update({
            "vol_ma5": rn(vol_ma5_prior), "vol_ma20": rn(vol_ma20),
            "volume_ratio": r4(ratio),
            "volume_ratio_incl_last": r4((vols[-1] / vol_ma5_incl) if vol_ma5_incl else None),
            "volume_trend": ("放量" if ratio and ratio > 1.5 else
                             ("缩量" if ratio and ratio < 0.7 else "持平")),
            "ratio_basis": "末根量 / 剔除末根的5日均量",
        })
    else:
        volume_out["note"] = "数据源无成交量，量价分析不可用"

    a = atr(klines, 14)
    last_open = datetime.fromtimestamp(klines[-1][0] / 1000, TZ8)
    step_ms = None
    if len(klines) > 3:
        ds = sorted(klines[i + 1][0] - klines[i][0] for i in range(len(klines) - 1))
        step_ms = ds[len(ds) // 2]

    result = {
        "symbol": data.get("symbol"),
        "as_of": last_open.strftime("%Y-%m-%d %H:%M UTC+8"),
        "as_of_note": "as_of 为该 bar 的开盘时间；已剔除未收盘 bar",
        "last_bar_open": last_open.isoformat(),
        "last_bar_closed_at": (datetime.fromtimestamp((klines[-1][0] + step_ms) / 1000, TZ8).isoformat()
                               if step_ms else None),
        "dropped_open_bar": dropped,
        "close": rn(closes[-1]),
        "bars": len(klines),
        "ma": {k: rn(v) for k, v in ma_vals.items()},
        "ema": {"ema12": rn(ema_series(closes, 12)), "ema26": rn(ema_series(closes, 26))},
        "ma_alignment": ma_alignment(ma5, ma10, ma20, ma60),
        "price_vs_ma": {
            k: ("above" if v is not None and closes[-1] > v else
                ("below" if v is not None else None))
            for k, v in ma_vals.items()
        },
        "golden_dead_cross_recent": find_crosses(klines, closes),
        "macd": macd_out,
        "rsi": {
            "rsi6": rn(rsi6), "rsi14": rn(rsi14),
            "state": ("超买" if rsi14 and rsi14 > 70 else
                      ("超卖" if rsi14 and rsi14 < 30 else "中性")) if rsi14 else "unknown",
        },
        "divergence": divergence_scan(klines, closes, rsi14_s),
        "volatility": {
            "atr14": rn(a),
            "atr_pct": r4(a / closes[-1] * 100) if a else None,
            "ann_vol_7d_pct": r4(ann_vol(closes, 7)),
            "ann_vol_30d_pct": r4(ann_vol(closes, 30)),
            "max_dd_30d_pct": r4(max_dd(closes, 30)),
        },
        "performance": {"pct_7d": r4(pct_change(closes, 7)),
                        "pct_30d": r4(pct_change(closes, 30))},
        "volume": volume_out,
        "support_resistance": support_resistance(klines, closes, ma_vals),
        "volume_profile": volume_profile(klines),
        "warnings": warnings,
    }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        _od = os.path.dirname(os.path.abspath(args.out))
        if _od:
            os.makedirs(_od, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"indicators -> {args.out} (bars={result['bars']}, as_of={result['as_of']}, "
              f"dropped_open={dropped})")
    else:
        print(text)


if __name__ == "__main__":
    main()
