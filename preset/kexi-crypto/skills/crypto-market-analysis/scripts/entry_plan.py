#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
entry_plan.py - 入场/出场时机引擎（v1.5.1 新增）

背景（用户实测反馈，2026-09-29 "20 币选币"会话）：
    团队综合评估后只给了"哪些币能买"，**没有给"怎么进、什么时候进"**——
    用户原话："团队给出综合评估后，应该出入场时机比如立即入场、下探到
    哪个位置开始反弹后入场、抄底点位、突破哪个位置开始补仓等等"。

本模块把"时机"量化成机器可判定的信号，输出四类动作建议：

  1. 立即入场（enter_now）        —— 趋势/量能/位置/大盘四门全过
  2. 回调反弹入场（pullback_buy） —— 下探到 X 企稳反弹后入场，X 来自
                                      支撑位（MA20/筹码峰POC/强支撑）
  3. 抄底点位（bottom_fish）      —— 深度回撤时的分批抄底价位（恐慌区）
  4. 突破补仓（breakout_add）     —— 放量突破阻力位 Y 后加仓，Y 来自
                                      阻力位（近高/MA60上方整数关/价值区上沿）

同时量化用户点名的入场环境条件（不满足时降级动作建议）：
  · 放量增长    : 近 3 日均量 / 20 日均量 ≥ 1.2（温和放量，非爆量出货）
  · 高换手      : 量比 ≥ 2.0 且未连续爆量 >5 天（爆量多天在高位=出货嫌疑）
  · 量升盘稳    : 本币量升 且 BTC 20 日波动 < 中位（大盘稳，个股启动才可信）
  · 资金流出减少: 稳定币 7 日净增发由负转正/降幅收窄（增量资金回场）
  · 追高嫌疑    : position.py 的 crowded/stretched/pos_90 ≥80（只能等回调，
                  禁止立即入场——回应用户"是不是会有追高嫌疑"）

设计原则（对齐 position.py）：
  · 纯函数、只吃 klines + 可选 context（BTC klines / 稳定币流），便于单测；
  · 阈值集中为常量；**只输出建议与依据，不输出仓位大小**（仓位属 cex_risk）；
  · 每条建议必带依据列表（可解释、可证伪），无依据的建议宁可不给。
"""

import kline_utils as ku

# ---------- 阈值常量 ----------
VOL_WARM_MULT = 1.2        # 温和放量：近3日均量 / 20日均量
VOL_HOT_MULT = 2.0         # 高换手：单日量 / 20日均量
VOL_SPIKE_MAX_DAYS = 5     # 连续爆量超过此天数 = 出货嫌疑（不给立即入场）
MA20_REBOUND_TOL = 0.6     # 回调反弹：收盘价从 MA20 下方的反弹幅度 ≥0.6%
BOTTOM_FISH_DD_PCT = -25.0  # 抄底：距 90 日高点回撤 ≤ -25% 才启用分批抄底
BOTTOM_TRANCHE_DD = [-30.0, -45.0, -60.0]   # 抄底三档回撤位（距高点）
BREAKOUT_VOL_MULT = 1.2    # 突破补仓：突破当日量 ≥ 20日均量 × 1.2
CHASE_POS_BLOCK = 80.0     # 追高拦截：90日位置 ≥80 禁止立即入场
BTC_CALM_ATR_PCT = 5.0     # 大盘稳：BTC ATR% 低于此值算"盘稳"


def _pct(a, b):
    if not b:
        return None
    return (a / b - 1) * 100


def _safe(v, nd=2):
    return round(v, nd) if isinstance(v, (int, float)) else None


def _ma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _prior_ma(vals, n, back=1):
    """n 日均线（回退 back 根，避免用未来数据：判断'今天相对昨天均线'）。"""
    if len(vals) < n + back:
        return None
    return sum(vals[-(n + back):-back]) / n


def analyze_volume_context(klines):
    """量能环境：温和放量 / 高换手 / 爆量出货嫌疑。"""
    if not klines or len(klines) < 25:
        return {"available": False}
    vols = [float(k[5]) for k in klines]
    vol_ma20 = _ma(vols, 20)
    if not vol_ma20:
        return {"available": False}
    v_last = vols[-1]
    vol3 = sum(vols[-3:]) / 3
    ratio_last = v_last / vol_ma20 if vol_ma20 else None
    ratio_3d = vol3 / vol_ma20 if vol_ma20 else None
    # 连续爆量天数（量 > 2×20日均量）
    spike_days = 0
    for v in reversed(vols[-20:]):
        if vol_ma20 and v > vol_ma20 * VOL_HOT_MULT:
            spike_days += 1
        else:
            break
    return {
        "available": True,
        "vol_ratio_last": _safe(ratio_last),
        "vol_ratio_3d": _safe(ratio_3d),
        "vol_ma20": vol_ma20,
        "spike_streak": spike_days,
        "warm_volume": bool(ratio_3d and ratio_3d >= VOL_WARM_MULT
                            and (ratio_3d or 0) < VOL_HOT_MULT),
        "high_turnover": bool(ratio_last and ratio_last >= VOL_HOT_MULT
                              and spike_days <= VOL_SPIKE_MAX_DAYS),
        "dump_suspect": bool(spike_days > VOL_SPIKE_MAX_DAYS),
    }


def analyze_btc_context(btc_klines):
    """大盘环境：BTC 波动 / 趋势（决定'量升盘稳'是否成立）。"""
    if not btc_klines or len(btc_klines) < 25:
        return {"available": False}
    closes = [float(k[4]) for k in btc_klines]
    # ATR%（简化：近 14 根真实波幅均值 / 现价）
    trs = []
    for i in range(max(1, len(btc_klines) - 14), len(btc_klines)):
        h, l, pc = float(btc_klines[i][2]), float(btc_klines[i][3]), closes[i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs) / len(trs) if trs else None
    atr_pct = (atr / closes[-1] * 100) if atr and closes[-1] else None
    ma20 = _ma(closes, 20)
    ma20_prev = _prior_ma(closes, 20)
    return {
        "available": True,
        "atr_pct": _safe(atr_pct, 2),
        "calm": bool(atr_pct is not None and atr_pct < BTC_CALM_ATR_PCT),
        "above_ma20": bool(ma20 and closes[-1] > ma20),
        "ma20_rising": bool(ma20 and ma20_prev and ma20 > ma20_prev),
    }


def build_entry_plan(klines, position=None, indicators=None,
                     btc_klines=None, stablecoin_flow=None):
    """生成入场/出场时机建议。

    Args:
        klines           : 本币已收盘 K 线 [[ts,o,h,l,c,v],...]（日线）
        position         : position.analyze_position() 输出（可选；缺则现算拥挤度）
        indicators       : indicators.py 输出 dict（可选；缺则只用 klines 算）
        btc_klines       : BTC 日线（可选；给'大盘稳'判定）
        stablecoin_flow  : datasources.fetch_stablecoin_flow() 输出（可选）

    Returns:
        dict —— {
          symbol_note, close, actions: [...], conditions: {...},
          chase_risk: {...}, invalidation, notes: [...]
        }
        actions 每条: {type, price, basis:[...], priority}
        type ∈ enter_now | pullback_buy | bottom_fish | breakout_add | wait
    """
    import position as pos_mod

    if not klines or len(klines) < 30:
        return {"ok": False, "error": "K 线样本不足（<30）——时机引擎无法工作"}

    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    vols = [float(k[5]) for k in klines]
    c = closes[-1]

    # 位置/拥挤度（外部给了就用，没给现算）
    pos = position or pos_mod.analyze_position(klines)

    # 量能环境
    volctx = analyze_volume_context(klines)
    # 大盘环境
    btcctx = analyze_btc_context(btc_klines) if btc_klines else {"available": False}
    # 资金环境（稳定币 7 日净增发）
    flow_ok = False
    flow_note = None
    if isinstance(stablecoin_flow, dict) and stablecoin_flow.get("available"):
        net7 = stablecoin_flow.get("net_mint_7d_usd")
        if net7 is not None:
            flow_ok = net7 > 0
            flow_note = (f"稳定币7日净{'增发' if net7 > 0 else '销毁'} "
                         f"{stablecoin_flow.get('net_mint_7d_display', net7)}")
        else:
            flow_note = "稳定币数据无 7 日净额字段"

    # ---------- 关键位 ----------
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    hi60 = max(highs[-60:]) if len(highs) >= 60 else max(highs)
    lo60 = min(lows[-60:]) if len(lows) >= 60 else min(lows)
    hi90 = max(highs[-90:]) if len(highs) >= 90 else max(highs)

    # 支撑/阻力（无 indicators 时用简化集；有则取其 support/resistance）
    supports, resistances = [], []
    if indicators and isinstance(indicators.get("support_resistance"), dict):
        sr = indicators["support_resistance"]
        for s in (sr.get("support") or [])[:3]:
            supports.append((float(s["price"]), s.get("basis", "支撑")))
        for r in (sr.get("resistance") or [])[:3]:
            resistances.append((float(r["price"]), r.get("basis", "阻力")))
    if ma20 and ma20 < c:
        supports.append((ma20, "MA20 均线"))
    supports.append((lo60, "近60日低点"))
    if ma60 and ma60 < c and (ma20 is None or ma60 != ma20):
        supports.append((ma60, "MA60 均线"))
    resistances.append((hi60, "近60日高点"))
    resistances.append((hi90, "近90日高点"))

    # 去重排序（支撑降序=最近的在前；阻力升序）
    supports = sorted({(round(p, 8), b) for p, b in supports if p and p < c * 0.995},
                      key=lambda x: -x[0])
    resistances = sorted({(round(p, 8), b) for p, b in resistances if p and p > c * 1.005},
                         key=lambda x: x[0])

    # ---------- 追高嫌疑（用户点名：直接看技术指标位置是不是追高） ----------
    chase = {
        "flagged": False,
        "reasons": [],
    }
    if pos:
        p90 = pos.get("pos_90")
        if p90 is not None and p90 >= CHASE_POS_BLOCK and pos.get("position_informative", True):
            chase["flagged"] = True
            chase["reasons"].append(f"90日区间位置 {p90:.0f}%（≥{CHASE_POS_BLOCK:.0f}% 高位区）")
        if pos.get("crowded"):
            chase["flagged"] = True
            chase["reasons"].append(
                f"放量 {pos.get('vol_expansion_days')} 天且近20日涨 "
                f"{pos.get('gain_20d')}%（高位放量多日=出货嫌疑）")
        if pos.get("stretched"):
            chase["flagged"] = True
            chase["reasons"].append(f"高于 MA20 {pos.get('dist_ma20_pct')}%（拉伸过度）")

    # ---------- 入场环境条件（用户点名的四类） ----------
    cond = {
        "warm_volume": bool(volctx.get("warm_volume")),
        "high_turnover": bool(volctx.get("high_turnover")),
        "vol_up_market_calm": bool(volctx.get("available") and volctx.get("warm_volume")
                                   and btcctx.get("available") and btcctx.get("calm")),
        "fund_outflow_easing": flow_ok if stablecoin_flow else None,
    }
    cond_notes = []
    if volctx.get("available"):
        cond_notes.append(
            f"量能：近3日/20日均量 {volctx.get('vol_ratio_3d')}x，"
            f"昨量 {volctx.get('vol_ratio_last')}x，连续爆量 {volctx.get('spike_streak')} 天")
    if btcctx.get("available"):
        cond_notes.append(
            f"大盘：BTC ATR% {btcctx.get('atr_pct')}（{'盘稳' if btcctx.get('calm') else '波动大'}），"
            f"{'站上' if btcctx.get('above_ma20') else '跌破'}MA20")
    if flow_note:
        cond_notes.append(f"资金：{flow_note}")

    # ---------- 动作建议 ----------
    actions = []

    # ① 立即入场：趋势+量能+位置+大盘 四门全过（追高嫌疑直接拦截）
    # 趋势门两种形态都算完好：a) 站上 MA20 且 MA20>MA60（多头排列）；
    # b) 站上 MA20 且 MA20 向上（回调后刚金叉/回升，MA20 尚未追上 MA60——
    #    回调-回升结构里 b 形态很常见，只认 a 会漏掉全部回调买点）。
    ma20_rising = None
    if ma20 and len(closes) >= 41:
        ma20_prev = sum(closes[-41:-21]) / 20
        ma20_rising = ma20 > ma20_prev
    trend_ok = bool(ma20 and c > ma20 and (
        (ma60 and ma20 > ma60) or ma20_rising))
    vol_ok = cond["warm_volume"] or cond["high_turnover"]
    market_ok = (not btcctx.get("available")) or btcctx.get("calm") or btcctx.get("above_ma20")
    if trend_ok and vol_ok and market_ok and not chase["flagged"]:
        basis = [
            f"收盘 > MA20（{ku.rn(ma20)}）且 " + (
                f"MA20 > MA60（{ku.rn(ma60)}，多头排列）" if (ma60 and ma20 > ma60)
                else "MA20 向上（回调后回升形态）"),
        ]
        basis.append(f"量能确认：{'温和放量' if cond['warm_volume'] else '高换手'}"
                     f"（3日量比 {volctx.get('vol_ratio_3d')}x）")
        if btcctx.get("available"):
            basis.append("大盘配合：" + ("BTC 盘稳" if btcctx.get("calm")
                                       else "BTC 站上 MA20"))
        if cond["vol_up_market_calm"]:
            basis.append("量升盘稳：本币放量且 BTC 低波动（用户点名条件成立）")
        if pos and pos.get("pos_90") is not None:
            basis.append(f"位置可控：90日区间 {pos['pos_90']:.0f}%")
        actions.append({
            "type": "enter_now", "label": "立即入场（小仓试探）",
            "price": ku.rn(c), "basis": basis, "priority": 1,
        })

    # ② 回调反弹入场：下探支撑位企稳反弹后买
    if supports:
        sp = supports[0]
        # 回调到支撑的幅度
        pull_pct = _pct(sp[0], c)
        rebound_ok = None
        # 近 5 根内是否发生过"下探 MA20/支撑后收复"（企稳反弹证据）
        if ma20:
            for i in range(max(1, len(closes) - 5), len(closes)):
                if lows[i] <= ma20 * 1.005 and closes[i] > ma20:
                    rebound_ok = i
                    break
        basis = [f"下探 {ku.rn(sp[0])}（{sp[1]}）后企稳反弹再入场",
                 f"距现价 {pull_pct:+.1f}%"]
        if rebound_ok is not None:
            basis.append(f"近 5 根内已现「下探 MA20 后收复」形态（第 {len(closes) - rebound_ok} 根）")
        basis.append("确认信号：回调不破支撑 + 收盘重新站上 MA20 + 缩量回踩")
        actions.append({
            "type": "pullback_buy", "label": f"回调至 {ku.rn(sp[0])} 企稳反弹后入场",
            "price": ku.rn(sp[0]), "basis": basis, "priority": 2,
        })

    # ③ 抄底点位：深度回撤时分批
    dd = (pos or {}).get("dd_from_high_90")
    if dd is not None and dd <= BOTTOM_FISH_DD_PCT:
        tranches = []
        for i, ddp in enumerate(BOTTOM_TRANCHE_DD):
            if dd <= ddp:
                tranches.append({"tranche": i + 1, "dd_pct": ddp,
                                 "price": ku.rn(hi90 * (1 + ddp / 100)),
                                 "note": f"距90日高点回撤 {ddp:.0f}% 档"})
        if tranches:
            actions.append({
                "type": "bottom_fish", "label": f"深度回撤分批抄底（当前回撤 {dd:.1f}%）",
                "price": ku.rn(tranches[0]["price"]),
                "tranches": tranches,
                "basis": [f"距 90 日高点已回撤 {dd:.1f}%（≤{BOTTOM_FISH_DD_PCT:.0f}% 启用抄底层）",
                          "三档分批回撤位：-30% / -45% / -60%（每档等额，不一次抄满）",
                          "前提：逆大周期反弹勿重仓——月线空头时抄底仓位减半"],
                "priority": 3,
            })

    # ④ 突破补仓：放量突破最近阻力
    if resistances:
        rs = resistances[0]
        breakout_today = bool(volctx.get("available") and volctx.get("vol_ratio_last")
                              and volctx["vol_ratio_last"] >= BREAKOUT_VOL_MULT
                              and c > rs[0])
        basis = [f"放量（量比 ≥{BREAKOUT_VOL_MULT}x）收盘突破 {ku.rn(rs[0])}（{rs[1]}）后补仓",
                 "确认：突破日收盘站稳阻力上方 ≥1%，次日不回吞"]
        if breakout_today:
            basis.append("✅ 今日已满足突破条件（当前价已在阻力上方）")
        actions.append({
            "type": "breakout_add", "label": f"放量突破 {ku.rn(rs[0])} 后补仓",
            "price": ku.rn(rs[0]), "basis": basis, "priority": 4,
        })

    # 没有任何动作可给（数据不足/全部门槛未过）→ 显式 wait，不硬凑
    if not actions:
        why = []
        if chase["flagged"]:
            why.append("追高嫌疑：" + "；".join(chase["reasons"]))
        if not vol_ok:
            why.append("量能未确认（无温和放量/高换手）")
        if not market_ok:
            why.append("大盘环境不支持（BTC 高波动且跌破 MA20）")
        actions.append({
            "type": "wait", "label": "暂不入场（等待条件）",
            "price": None, "basis": why or ["样本不足"], "priority": 9,
        })

    # ---------- 失效位 ----------
    invalidation = None
    if supports:
        invalidation = {
            "price": ku.rn(supports[-1][0]),
            "note": f"日线收盘跌破 {ku.rn(supports[-1][0])}（{supports[-1][1]}）→ 看涨逻辑失效，离场",
        }
    elif ma20:
        invalidation = {
            "price": ku.rn(ma20),
            "note": f"日线收盘跌破 MA20（{ku.rn(ma20)}）→ 离场",
        }

    notes = [
        "本引擎只出'时机与依据'，仓位大小由 cex_risk 风控闸门决定",
        "追高嫌疑 flagged=true 时永远不给立即入场——先等回调",
    ]
    notes.extend(cond_notes)

    return {
        "ok": True,
        "close": ku.rn(c),
        "chase_risk": chase,
        "conditions": cond,
        "conditions_note": "；".join(cond_notes),
        "supports": [{"price": ku.rn(p), "basis": b} for p, b in supports[:4]],
        "resistances": [{"price": ku.rn(p), "basis": b} for p, b in resistances[:4]],
        "actions": actions,
        "invalidation": invalidation,
        "vol_context": volctx,
        "btc_context": btcctx,
        "notes": notes,
    }


def main():
    import argparse
    import json
    import os
    import sys

    ap = argparse.ArgumentParser(description="入场/出场时机引擎（立即入场/回调反弹/抄底/突破补仓）")
    ap.add_argument("--klines", required=True, help="fetch_klines 输出 JSON（本币日线）")
    ap.add_argument("--btc-klines", default=None, help="可选：BTC 日线 JSON（大盘环境）")
    ap.add_argument("--indicators", default=None, help="可选：indicators 输出 JSON（用其支撑阻力）")
    ap.add_argument("--flow", default=None, help="可选：datasources 稳定币流 JSON（资金环境）")
    ap.add_argument("--out", default=None, help="输出 JSON 路径（默认 kexi_out/entry_plan.json）")
    args = ap.parse_args()

    def _load(p, key="klines"):
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d.get(key) or d.get("data") or (d if isinstance(d, list) else None)

    if not os.path.exists(args.klines):
        print(json.dumps({"ok": False, "error": f"找不到 {args.klines}"}))
        return 1
    klines = _load(args.klines)
    btc_klines = _load(args.btc_klines) if args.btc_klines and os.path.exists(args.btc_klines) else None
    indicators = None
    if args.indicators and os.path.exists(args.indicators):
        with open(args.indicators, encoding="utf-8") as f:
            indicators = json.load(f)
    flow = None
    if args.flow and os.path.exists(args.flow):
        with open(args.flow, encoding="utf-8") as f:
            flow = json.load(f)

    plan = build_entry_plan(klines, indicators=indicators,
                            btc_klines=btc_klines, stablecoin_flow=flow)

    out = args.out or os.path.join("kexi_out", "entry_plan.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    # 摘要（stdout 最后一行紧凑 JSON，供 lastJsonLine 解析）
    summary = {
        "ok": plan.get("ok", False),
        "close": plan.get("close"),
        "chase": plan.get("chase_risk", {}).get("flagged"),
        "actions": [{"type": a["type"], "label": a["label"],
                     "price": a.get("price")} for a in plan.get("actions", [])],
        "invalidation": (plan.get("invalidation") or {}).get("price"),
        "out": out,
    }
    if not plan.get("ok"):
        summary["error"] = plan.get("error")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if plan.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
