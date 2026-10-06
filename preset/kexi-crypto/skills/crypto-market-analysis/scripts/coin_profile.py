#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coin_profile.py - 币种行为画像（v1.5.1 新增）

背景（用户实测反馈，2026-09-29）：
    用户要求"关注每个币或某一机构的币种历史走势，是习惯性震荡拉盘还是
    快速拉盘震荡出货，或者缓慢拉盘快速出货，还是走特定趋势行情，顺大盘
    还是背离大盘，走以太行情还是大饼行情等等，以及大饼和以太的关系"。

本模块输出三层画像（纯函数、只吃 klines）：

  1. 拉盘/出货模式（pump_dump_pattern）
     从历史每轮"上涨段"的**斜率×量能×顶部形态**归纳该币的操盘习惯：
       · 震荡拉盘     : 涨速温和 + 涨后长横盘（波段市，适合高抛低吸）
       · 快速拉盘震荡出货 : 短时急拉 + 拉后放巨量滞涨/回落（一波流）
       · 缓慢拉盘快速出货 : 长期缓涨 + 顶部急跌放巨量（温水煮青蛙）
       · 趋势行情     : 涨速稳定 + 回撤浅 + 量能持续（趋势跟随市）

  2. 大盘关系（market_regime）
     · 顺大盘/背离大盘 : 与 BTC 近 90 日收益相关系数 r 与 beta
       - r ≥ 0.6        → 顺大盘（beta>1 放大器 / beta<1 缓涨跟随）
       - r ≤ 0.2        → 背离大盘（独立行情，仓位可不对冲 BTC）
       - 其间            → 弱相关
     · 走大饼还是以太行情（BTC-vs-ETH 归因）：分别算对 BTC/ETH 的相关，
       谁高跟谁；两者都高 → "大盘双驱"；都低 → 独立行情

  3. BTC-ETH 关系（btc_eth_linkage，用户点名"大饼和以太的关系"）
     · 90 日收益相关 r、ETH/BTC 比价走势（山寨季信号）
     · ETH/BTC 上升 → 山寨/ETH 系更强；下降 → BTC 主导（大饼行情）

设计原则：
  · 纯函数、只吃 klines（本币+BTC+ETH 可选），便于单测（合成数据可驱动）；
  · 相关用皮尔逊（对数收益），样本 ≥30 才出结论，不足如实标 insufficient；
  · **画像≠预测**：只归纳"这个币历史上习惯怎么走"，绝不外推为必然。
"""

import math

import kline_utils as ku

# ---------- 阈值常量 ----------
R_FOLLOW = 0.6          # r ≥ 此值 → 顺大盘
R_DIVERGE = 0.2         # r ≤ 此值 → 背离大盘
MIN_CORR_BARS = 30      # 相关计算最少样本
PUMP_FAST_GAIN = 25.0   # 单段涨幅 ≥ 此值（10 日内）算"快速拉盘"
PUMP_SLOW_DAYS = 20     # 上涨段 ≥ 此天数算"缓慢拉盘"
SHALLOW_DD = -12.0      # 趋势行情判定：段内回撤浅于此值
TOP_DUMP_FAST = -15.0   # 顶部 5 日内跌超此值算"快速出货"


def _pct(a, b):
    if not b:
        return None
    return (a / b - 1) * 100


def _safe(v, nd=2):
    return round(v, nd) if isinstance(v, (int, float)) else None


def _log_returns(closes):
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            out.append(math.log(closes[i] / closes[i - 1]))
    return out


def pearson(xs, ys):
    """皮尔逊相关（长度取交集，样本不足返回 None）。"""
    n = min(len(xs), len(ys))
    if n < MIN_CORR_BARS:
        return None
    xs2, ys2 = xs[-n:], ys[-n:]
    mx, my = sum(xs2) / n, sum(ys2) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs2, ys2))
    vx = sum((x - mx) ** 2 for x in xs2)
    vy = sum((y - my) ** 2 for y in ys2)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def beta(xs, ys):
    """ys 对 xs 的 beta（ys = a + b·xs 的 b）。"""
    n = min(len(xs), len(ys))
    if n < MIN_CORR_BARS:
        return None
    xs2, ys2 = xs[-n:], ys[-n:]
    mx, my = sum(xs2) / n, sum(ys2) / n
    vx = sum((x - mx) ** 2 for x in xs2)
    if vx <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs2, ys2))
    return cov / vx


def _split_swings(klines, min_gain=10.0, min_days=3):
    """把历史切成"上涨段"（局部低点到随后高点，涨幅 ≥ min_gain%）。

    返回 [{start_i, end_i, gain, days, top_dd_after, vol_ratio_top}]
    top_dd_after = 段顶后 5 日内最大回撤（出货速度的代理）。
    """
    closes = [float(k[4]) for k in klines]
    vols = [float(k[5]) for k in klines]
    swings = []
    n = len(closes)
    i = 0
    while i < n - 2:
        # 局部低点：低于前后各 1 根
        if closes[i] <= closes[i + 1]:
            j = i + 1
            top_j = j
            while j < n - 1 and closes[j + 1] >= closes[j] * 0.995:
                j += 1
                if closes[j] > closes[top_j]:
                    top_j = j
            gain = _pct(closes[top_j], closes[i]) if closes[i] else None
            days = top_j - i
            if gain is not None and gain >= min_gain and days >= min_days:
                # 段顶后 5 日回撤
                seg_end = min(n, top_j + 6)
                top_after_dd = 0.0
                if top_j + 1 < seg_end:
                    lo_after = min(closes[top_j + 1:seg_end])
                    top_after_dd = _pct(lo_after, closes[top_j]) or 0.0
                # 拉升期量能 / 前期基准
                vol_ma20 = (sum(vols[max(0, i - 20):i]) / 20) if i >= 20 else None
                vol_seg = (sum(vols[i:top_j + 1]) / (top_j - i + 1)) if top_j > i else None
                vol_ratio = (vol_seg / vol_ma20) if (vol_ma20 and vol_seg) else None
                swings.append({
                    "start_i": i, "end_i": top_j, "gain": _safe(gain, 1),
                    "days": days, "top_dd_after": _safe(top_after_dd, 1),
                    "vol_ratio": _safe(vol_ratio),
                })
            i = max(j, i + 1)
        else:
            i += 1
    return swings


def classify_pattern(swings):
    """对一组上涨段做模式判定。返回 (pattern, stats, why)。

    v1.5.5：抽成独立函数，好让**同一套判据**分别跑全历史与最近窗口——
    这是"信号衰减"能成立的前提（两处各写一套判据的话，比对毫无意义）。
    """
    n = len(swings)
    fast_pumps = slow_pumps = fast_dumps = shallow_dd_cnt = 0
    for s in swings:
        if (s["gain"] or 0) >= PUMP_FAST_GAIN and s["days"] <= 10:
            fast_pumps += 1
        elif s["days"] >= PUMP_SLOW_DAYS:
            slow_pumps += 1
        if (s["top_dd_after"] or 0) <= TOP_DUMP_FAST:
            fast_dumps += 1
        if (s["top_dd_after"] or 0) >= SHALLOW_DD:
            shallow_dd_cnt += 1

    stats = {"n": n, "fast_pumps": fast_pumps, "slow_pumps": slow_pumps,
             "fast_dumps": fast_dumps, "shallow_dd": shallow_dd_cnt}

    if fast_pumps * 2 >= n and fast_dumps * 2 >= n:
        return "快速拉盘震荡出货", stats, "多数段短时急拉且顶部急跌放巨量——一波流拉高出货，追高危险"
    if slow_pumps * 2 >= n and fast_dumps * 2 >= n:
        return "缓慢拉盘快速出货", stats, "多数段缓慢拉升但顶部快速崩跌——温水煮青蛙，涨时舒服跌时利落"
    if shallow_dd_cnt * 2 >= n:
        return "趋势行情", stats, "多数段回撤浅、涨速稳定——适合趋势跟随，回踩均线是加仓点而非离场点"
    return "震荡拉盘", stats, "涨速温和且段间长横盘——波段市，适合支撑买/阻力卖的高抛低吸"


def pattern_decay(klines, window=90):
    """信号衰减检测（v1.5.5，借鉴"滚动窗口 vs 全历史"的思想）。

    动机：原实现只用**全部历史**归纳操盘习惯并输出一个模式，但操盘模式
    是会变的——历史成立不代表现在还成立。用**同一判据**跑最近窗口，
    看它是否已经换了玩法。

    诚实边界：窗口内样本不足时返回 available=false，
    **绝不用 2~3 段 swings 硬凑一个结论**。
    """
    out = {"available": False, "window_bars": window,
           "pattern_full": None, "pattern_recent": None, "stable": None,
           "confidence": "低", "recent_dump_rate": None, "recent_swings": 0, "note": ""}
    if not klines or len(klines) < 120:
        out["note"] = "样本 <120 根，滚动窗口不足以判定衰减"
        return out

    full_sw = _split_swings(klines)
    recent_sw = _split_swings(klines[-window:])
    if not full_sw:
        out["note"] = "全历史无 ≥10% 上涨段，无模式可比"
        return out
    if len(recent_sw) < 3:
        out["note"] = (f"最近 {window} 根仅 {len(recent_sw)} 轮上涨段（<3），"
                       "样本不足——不下结论，不用 2 段硬凑")
        return out

    p_full, s_full, _ = classify_pattern(full_sw)
    p_recent, s_recent, _ = classify_pattern(recent_sw)
    dump_cnt = sum(1 for s in recent_sw if (s["top_dd_after"] or 0) <= TOP_DUMP_FAST)

    stable = (p_full == p_recent)
    notes = []
    if stable:
        notes.append(f"模式未变：全历史「{p_full}」与最近 {window} 根一致")
    else:
        notes.append(f"**模式已切换**：全历史「{p_full}」→ 最近 {window} 根「{p_recent}」，应按近期行为解读")
    notes.append(f"最近 {len(recent_sw)} 轮上涨段中，段顶急跌(≥{abs(TOP_DUMP_FAST):.0f}%) 占 "
                 f"{dump_cnt}/{len(recent_sw)}（{int(dump_cnt/len(recent_sw)*100)}%）")

    out.update({"available": True, "pattern_full": p_full, "pattern_recent": p_recent,
                "stable": stable, "confidence": "中" if (stable and s_recent["n"] >= 4) else "低",
                "recent_dump_rate": round(dump_cnt / len(recent_sw), 2),
                "recent_swings": len(recent_sw),
                "stats_full": s_full, "stats_recent": s_recent,
                "note": "；".join(notes)})
    return out


def pump_dump_pattern(klines):
    """归纳拉盘/出货模式。返回 {pattern, evidence, swings}。"""
    if not klines or len(klines) < 60:
        return {"pattern": "insufficient", "evidence": ["样本 <60 根，无法归纳操盘模式"],
                "swings": []}
    swings = _split_swings(klines)
    if not swings:
        return {"pattern": "无显著上涨段", "evidence": ["历史中无 ≥10% 的上涨段（长期阴跌/横盘）"],
                "swings": []}

    # 每段：急拉？缓拉？顶部快出？（v1.5.5：判据统一走 classify_pattern，
    # 保证与 pattern_decay 的滚动窗口用同一套标准）
    n = len(swings)
    pattern, s, why = classify_pattern(swings)
    evidence = [
        f"历史 {n} 轮上涨段：急拉(≤10日涨≥{PUMP_FAST_GAIN:.0f}%) {s['fast_pumps']} 轮 / "
        f"缓涨(≥{PUMP_SLOW_DAYS}日) {s['slow_pumps']} 轮",
        f"段顶后5日内急跌(≥{abs(TOP_DUMP_FAST):.0f}%) {s['fast_dumps']} 轮 / "
        f"回撤浅(>{abs(SHALLOW_DD):.0f}%) {s['shallow_dd']} 轮",
        f"最近一轮：{swings[-1]['gain']}% / {swings[-1]['days']} 天 / "
        f"顶后回撤 {swings[-1]['top_dd_after']}%",
        why,
    ]

    return {"pattern": pattern, "evidence": evidence,
            "swings": swings[-5:]}       # 最近 5 轮明细


def market_regime(klines, btc_klines=None, eth_klines=None):
    """顺/逆大盘 + 走大饼还是以太行情。"""
    out = {
        "btc_r": None, "btc_beta": None, "regime_vs_btc": "insufficient",
        "eth_r": None, "eth_beta": None, "regime_vs_eth": "insufficient",
        "follows": None, "note": None,
    }
    closes = [float(k[4]) for k in klines] if klines else []
    if len(closes) < MIN_CORR_BARS:
        out["note"] = f"样本 <{MIN_CORR_BARS}，无法做相关分析"
        return out
    rets = _log_returns(closes)

    def _pair(other):
        if not other or len(other) < MIN_CORR_BARS:
            return None, None
        orets = _log_returns([float(k[4]) for k in other])
        return pearson(rets, orets), beta(orets, rets)

    out["btc_r"], out["btc_beta"] = _pair(btc_klines)
    out["eth_r"], out["eth_beta"] = _pair(eth_klines)

    # vs BTC 判定
    if out["btc_r"] is not None:
        r = out["btc_r"]
        if r >= R_FOLLOW:
            out["regime_vs_btc"] = "顺大盘"
            b = out["btc_beta"]
            out["note"] = (f"与 BTC 相关 r={r:.2f}、beta={b:.2f}——"
                           f"{'高beta放大器（BTC跌时跌更狠）' if b and b > 1.2 else '跟随大盘'}")
        elif r <= R_DIVERGE:
            out["regime_vs_btc"] = "背离大盘"
            out["note"] = f"与 BTC 相关 r={r:.2f}——独立行情，BTC 大跌时未必跟随"
        else:
            out["regime_vs_btc"] = "弱相关"
            out["note"] = f"与 BTC 相关 r={r:.2f}——弱相关，部分独立"

    # 走大饼还是以太（用户点名）
    if out["btc_r"] is not None and out["eth_r"] is not None:
        rb, re_ = out["btc_r"], out["eth_r"]
        both = min(rb, re_) >= R_FOLLOW
        if both:
            out["follows"] = "大盘双驱（BTC/ETH 都高度相关）"
        elif re_ - rb >= 0.15:
            out["follows"] = "走以太行情（对 ETH 相关性明显高于 BTC）"
        elif rb - re_ >= 0.15:
            out["follows"] = "走大饼行情（对 BTC 相关性明显高于 ETH）"
        else:
            out["follows"] = "大饼/以太不分（两者相关接近）"
    return out


def btc_eth_linkage(btc_klines, eth_klines):
    """BTC-ETH 关系（用户点名"大饼和以太的关系"）。"""
    if not btc_klines or not eth_klines:
        return {"available": False, "note": "需要 BTC 与 ETH 两份 K 线"}
    bc = [float(k[4]) for k in btc_klines]
    ec = [float(k[4]) for k in eth_klines]
    if len(bc) < MIN_CORR_BARS or len(ec) < MIN_CORR_BARS:
        return {"available": False, "note": "样本不足（<30）"}
    n = min(len(bc), len(ec))
    bc2, ec2 = bc[-n:], ec[-n:]
    r = pearson(_log_returns(bc2), _log_returns(ec2))
    # ETH/BTC 比价走势（近 30 日 vs 前 30 日）
    ratio_now = ec2[-1] / bc2[-1] if bc2[-1] else None
    ratio_30ago = (ec2[-31] / bc2[-31]) if n >= 31 and bc2[-31] else None
    ratio_chg = _pct(ratio_now, ratio_30ago) if (ratio_now and ratio_30ago) else None
    season = None
    if ratio_chg is not None:
        if ratio_chg >= 3:
            season = "ETH 相对 BTC 走强（ETH/BTC↑）——山寨/ETH 系行情窗口"
        elif ratio_chg <= -3:
            season = "BTC 主导（ETH/BTC↓）——大饼行情，山寨普遍跑输"
        else:
            season = "ETH/BTC 比价平稳（±3% 内）——无明确风格切换"
    return {
        "available": True,
        "corr_r": _safe(r),
        "eth_btc_ratio": _safe(ratio_now, 6),
        "eth_btc_chg_30d_pct": _safe(ratio_chg),
        "season": season,
        "note": "ETH/BTC 比价是风格判别核心：升=山寨季窗口，降=大饼主导",
    }


def build_profile(klines, btc_klines=None, eth_klines=None):
    """汇总画像。"""
    pattern = pump_dump_pattern(klines)
    decay = pattern_decay(klines)          # v1.5.5：信号衰减
    regime = market_regime(klines, btc_klines, eth_klines)
    linkage = btc_eth_linkage(btc_klines, eth_klines) if (btc_klines and eth_klines) \
        else {"available": False, "note": "未提供 BTC/ETH K 线（可选）"}
    return {
        "ok": True,
        "close": ku.rn(float(klines[-1][4])) if klines else None,
        "bars": len(klines) if klines else 0,
        "pump_dump_pattern": pattern,
        "pattern_decay": decay,          # v1.5.5 信号衰减
        "market_regime": regime,
        "btc_eth_linkage": linkage,
        "how_to_use": [
            "画像≠预测：只归纳该币历史操盘习惯，不外推为必然",
            "**信号衰减优先于历史画像**：pattern_decay.stable=false 时以近期模式为准",
            "快速拉盘震荡出货的币：只做右侧突破，不做左侧埋伏，止损必须紧",
            "震荡拉盘的币：支撑买/阻力卖的波段思路优于追突破",
            "趋势行情的币：回踩均线加仓优于高抛低吸",
            f"顺大盘(r≥{R_FOLLOW})的币：仓位须考虑 BTC 大盘环境；背离币可作为组合分散项",
            "走以太行情的币在 ETH/BTC 下行期会双重承压（beta×风格）",
        ],
    }


def main():
    import argparse
    import json
    import os

    ap = argparse.ArgumentParser(description="币种行为画像（拉盘/出货模式 + 顺逆大盘 + 大饼/以太归属）")
    ap.add_argument("--klines", required=True, help="fetch_klines 输出 JSON（本币日线）")
    ap.add_argument("--btc-klines", default=None, help="可选：BTC 日线 JSON")
    ap.add_argument("--eth-klines", default=None, help="可选：ETH 日线 JSON")
    ap.add_argument("--out", default=None, help="输出 JSON 路径（默认 kexi_out/coin_profile.json）")
    args = ap.parse_args()

    def _load(p):
        if not p or not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("klines") or d.get("data") or (d if isinstance(d, list) else None)

    klines = _load(args.klines)
    if not klines:
        print(json.dumps({"ok": False, "error": f"找不到或无 K 线：{args.klines}"}))
        return 1
    profile = build_profile(klines, _load(args.btc_klines), _load(args.eth_klines))

    out = args.out or os.path.join("kexi_out", "coin_profile.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    summary = {
        "ok": True,
        "pattern": profile["pump_dump_pattern"]["pattern"],
        "pattern_recent": profile["pattern_decay"].get("pattern_recent"),
        "pattern_stable": profile["pattern_decay"].get("stable"),
        "decay_note": profile["pattern_decay"].get("note"),
        "regime_vs_btc": profile["market_regime"]["regime_vs_btc"],
        "follows": profile["market_regime"].get("follows"),
        "eth_btc_season": (profile["btc_eth_linkage"] or {}).get("season"),
        "out": out,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
