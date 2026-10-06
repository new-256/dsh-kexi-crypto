#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
timeframe.py - 多周期（周线 / 月线）大周期研判（v1.5.0 新增）

背景（用户 2026-09-29 提问）：
    "这类是不是应该结合大盘和周线月线去看"——此前插件**只看日线**：
    screener 只取 interval=1d，`fetch_klines.py` 虽支持 --interval 1w 但
    screener 从不使用，月线完全不支持（INTERVAL_MS 里没有 1M）。

    后果：一个币"日线很漂亮"可能只是**月线级别下跌通道里的一次反弹**，
    系统无法分辨。本模块补上大周期视角。

提供两层能力：
  1. resample()      —— 日线 → 周线/月线（本地重采样，不依赖交易所是否支持该周期）
  2. analyze_tf()    —— 单周期结构：均线关系、MACD、位置、连涨/连跌
  3. multi_tf_view() —— 日/周/月三周期汇总 + **一致性判定**，输出"大周期是否支持"

设计原则：
  · 本地重采样（Binance 1M 支持不稳定、不同交易所口径不一，日线最可靠）；
  · 周线以周一 00:00 UTC+8 为起点、月线以自然月为起点，与常见看盘软件一致；
  · 重采样后的最后一根若不是完整周期，**同样要剔除**（复用 drop_open_bar 思路），
    否则"本周还没走完"的周线会污染 MA/MACD——这是日线已修过的同类 bug。
"""

from datetime import datetime, timezone, timedelta

import kline_utils as ku

TZ8 = timezone(timedelta(hours=8))

W1 = ku.W1
D1 = ku.D1


def _period_key(ts_ms, period):
    """把开仓时间戳归到所属周期起点（毫秒）。周=周一00:00 UTC+8，月=自然月1日00:00。"""
    dt = datetime.fromtimestamp(ts_ms / 1000, TZ8)
    if period == "1w":
        start = dt - timedelta(days=dt.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "1M":
        start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"unsupported period: {period}")
    return int(start.timestamp() * 1000)


def resample(klines, period):
    """日线 → 周线/月线聚合。

    Args:
        klines: [[ts_ms, o, h, l, c, v], ...] 升序（应已剔除未收盘日线）
        period: "1w" | "1M"

    Returns:
        (agg, dropped) —— agg 为聚合后的 K 线；dropped=True 表示**最后一根被剔除**
        （因为它所属周期尚未结束，属于"未走完的周/月线"）。
    """
    if not klines:
        return [], False
    buckets = {}
    order = []
    for k in klines:
        key = _period_key(int(k[0]), period)
        if key not in buckets:
            buckets[key] = [key, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
            order.append(key)
        else:
            b = buckets[key]
            b[2] = max(b[2], float(k[2]))      # high
            b[3] = min(b[3], float(k[3]))      # low
            b[4] = float(k[4])                 # close = 最后一根
            b[5] += float(k[5])                # volume 累加
    order.sort()
    agg = [buckets[k] for k in order]

    if not agg:
        return [], False

    # 剔除"未走完"的最后一根：只有当最后一根的周期起点 == 当前时刻所属周期起点时，
    # 说明它仍在进行中。（无法取系统时间做稳定测试时，退化为按周期长度推断。）
    dropped = False
    last_key = agg[-1][0]
    now_key = _period_key(int(datetime.now(TZ8).timestamp() * 1000), period)
    if last_key == now_key:
        agg = agg[:-1]
        dropped = True
    else:
        # 兜底：最后一根跨度不足一个完整周期也剔除
        span = int(klines[-1][0]) - last_key
        if span < (W1 if period == "1w" else 28 * D1):
            agg = agg[:-1]
            dropped = True
    return agg, dropped


def _sma(vals, n, end=None):
    seg = vals[:end] if end else vals
    if len(seg) < n:
        return None
    return sum(seg[-n:]) / n


def _macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None
    ef = es = closes[0]
    kf, ks = 2 / (fast + 1), 2 / (slow + 1)
    difs = []
    for c in closes:
        ef = c * kf + ef * (1 - kf)
        es = c * ks + es * (1 - ks)
        difs.append(ef - es)
    dea, deas = difs[0], []
    ka = 2 / (signal + 1)
    for d in difs:
        dea = d * ka + dea * (1 - ka)
        deas.append(dea)
    return difs, deas, [d - a for d, a in zip(difs, deas)]


def analyze_tf(klines, period, min_bars=6):
    """单周期结构分析。样本不足返回带 insufficient 标记的字典（不抛异常）。"""
    if not klines or len(klines) < min_bars:
        return {"period": period, "insufficient": True, "bars": len(klines or [])}
    closes = [float(k[4]) for k in klines]
    c = closes[-1]
    ma20 = _sma(closes, 20)
    ma50 = _sma(closes, 50)
    ma20_prev = _sma(closes, 20, end=len(closes) - 1)
    m = _macd(closes)

    trend = "unknown"
    ma_stack = "unknown"          # 均线自身排列——比"价格在MA20上还是下"更本质
    if ma20 and ma50:
        if ma20 > ma50:
            ma_stack = "短期均线在长期之上"
        else:
            ma_stack = "短期均线在长期之下"
    if ma20:
        if ma50:
            if c > ma20 > ma50:
                trend = "多头排列"
            elif c < ma20 < ma50:
                trend = "空头排列"
            elif c > ma20:
                # 价格在 MA20 上，但均线排列是否支持要看 ma_stack
                trend = ("站上MA20(均线多头)" if ma20 > ma50
                         else "站上MA20但MA20<MA50(反弹)")
            else:
                trend = ("MA20下方(均线多头)" if ma20 > ma50
                         else "MA20下方(均线空头)")
        else:
            trend = "站上MA20" if c > ma20 else "MA20下方"

    macd_state = "unknown"
    if m:
        dif, dea, hist = m
        if dif[-1] > dea[-1] and dif[-1] > 0:
            macd_state = "零上多头"
        elif dif[-1] > dea[-1]:
            macd_state = "零下金叉(反弹)"
        elif dif[-1] < dea[-1] and dif[-1] < 0:
            macd_state = "零下空头"
        else:
            macd_state = "零上死叉(回调)"

    # 区间位置（该周期自身的视角）
    hi = max(float(k[2]) for k in klines)
    lo = min(float(k[3]) for k in klines)
    pos = round((c - lo) / (hi - lo) * 100, 1) if hi > lo else 50.0

    # 连涨/连跌（该周期的连续根数）
    consec = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            consec += 1 if consec >= 0 else 0
        elif closes[i] < closes[i - 1]:
            consec -= 1 if consec <= 0 else 0
        else:
            break

    return {
        "period": period,
        "insufficient": False,
        "bars": len(klines),
        "close": ku.rn(c),
        "trend": trend,
        "ma20": ku.rn(ma20) if ma20 else None,
        "ma50": ku.rn(ma50) if ma50 else None,
        "ma_stack": ma_stack,
        "ma20_rising": bool(ma20 and ma20_prev and ma20 > ma20_prev),
        "above_ma20": bool(ma20 and c > ma20),
        "macd_state": macd_state,
        "range_pos_pct": pos,
        "range_high": ku.rn(hi),
        "range_low": ku.rn(lo),
        "consecutive": consec,
        # 「多头/空头」必须同时看**价格位置**与**均线排列**：
        # 实测缺陷：只判 c>MA20 会把"月线价格在MA20下方但短期反抽"错判为多头；
        # 更要命的是漏掉"MA20 < MA50"这种中期结构仍是下跌的反弹
        # （实测 SEI/ALGO/BTC 周线均为 MA20<MA50，日线却多头排列）。
        "bullish": bool(ma20 and c > ma20 and (ma50 is None or ma20 > ma50)
                        and (m and m[0][-1] > m[1][-1])),
        "bearish": bool(ma20 and (c < ma20 or (ma50 is not None and ma20 < ma50))
                        and (m and m[0][-1] < m[1][-1])),
        # 单独的"价格在MA20下"或"均线未多头排列"标记，便于上层做更细判断
        "price_below_ma20": bool(ma20 and c < ma20),
        "ma_bear_stack": bool(ma20 and ma50 and ma20 < ma50),
        # MACD 样本不足时的**纯均线方向**兜底判断（月线常见：32 根 < 26+9）
        "ma_direction": ("多头" if (ma20 and c > ma20 and (ma50 is None or ma20 > ma50))
                         else "空头" if (ma20 and (c < ma20 or (ma50 is not None and ma20 < ma50)))
                         else "中性"),
    }


def multi_tf_view(daily_klines):
    """日 / 周 / 月三周期汇总 + 大周期一致性判定。

    Returns:
        dict —— 含 daily/weekly/monthly 三段 + alignment 判定与人类可读说明
    """
    weekly, w_dropped = resample(daily_klines, "1w")
    monthly, m_dropped = resample(daily_klines, "1M")
    d = analyze_tf(daily_klines, "1d", min_bars=60)
    w = analyze_tf(weekly, "1w", min_bars=6)
    mo = analyze_tf(monthly, "1M", min_bars=6)

    notes = []
    # 用「均线方向」而非「MACD 多头」做主判据：月线常因样本不足（<35根）拿不到 MACD，
    # 若强依赖 MACD 会让月线永远判不出方向，多周期研判形同虚设。
    def _dir(x):
        if x.get("insufficient"):
            return None
        d = x.get("ma_direction")
        return d if d in ("多头", "空头") else None

    dirs = {p: _dir(x) for p, x in (("日", d), ("周", w), ("月", mo))}
    avail_dirs = {p: v for p, v in dirs.items() if v}
    bull = sum(1 for v in avail_dirs.values() if v == "多头")
    bear = sum(1 for v in avail_dirs.values() if v == "空头")
    avail = len(avail_dirs)

    if avail == 0:
        alignment = "unknown"
        notes.append("三周期均样本不足，无法做多周期研判")
    elif bull == avail and avail >= 2:
        alignment = "全周期共振向上"
        notes.append(f"日/周/月 {avail} 个可用周期全部多头，大周期支持")
    elif bear == avail and avail >= 2:
        alignment = "全周期共振向下"
        notes.append(f"日/周/月 {avail} 个可用周期全部空头，**日线走强时须警惕只是下跌中继**")
    elif bear >= 1 and bull >= 1:
        alignment = "周期冲突"
        notes.append(
            f"{bull} 个周期多头 / {bear} 个周期空头：大周期与日线不一致，"
            "须降低仓位或只做短线")
    else:
        alignment = "中性"
        notes.append("周期未形成一致方向")

    # 关键提示：日线多头但大周期空头 = 逆大势反弹（用户关心的风险）
    risk_note = None
    d_bull = dirs.get("日") == "多头"
    if d_bull:
        if dirs.get("月") == "空头":
            risk_note = ("⚠️ 日线多头但**月线空头**：属逆大周期反弹，"
                         "历史上失败率高，须严格止损")
        elif dirs.get("周") == "空头":
            risk_note = "⚠️ 日线多头但**周线空头**：中期趋势未转，反弹性质，须警惕"
        elif mo.get("ma_bear_stack") or w.get("ma_bear_stack"):
            # 周/月虽因 MACD 未确认而标"中性"，但均线已是空头排列——同样危险
            which = "月线" if mo.get("ma_bear_stack") else "周线"
            risk_note = (f"⚠️ 日线多头但**{which} MA20<MA50**（中期均线仍空头排列）："
                         "本轮上涨属反弹而非趋势反转，须警惕")
    if risk_note:
        notes.append(risk_note)

    if w_dropped:
        notes.append("已剔除未走完的本周周线（避免未收盘 bar 污染）")
    if m_dropped:
        notes.append("已剔除未走完的本月月线")

    return {
        "daily": d, "weekly": w, "monthly": mo,
        "weekly_bars": len(weekly), "monthly_bars": len(monthly),
        "alignment": alignment,
        "bullish_tf": bull, "bearish_tf": bear, "available_tf": avail,
        "risk_note": risk_note,
        "notes": notes,
    }


def tf_alignment_score(tf):
    """把多周期一致性折算成**加减分**（返回 (分, 理由列表)）。

    与 position.py 的扣分互补：位置看"涨到哪了"，多周期看"大方向支不支持"。
      · 全周期共振向上            +2
      · 日线多头但月线空头        -3（逆大势反弹，最危险）
      · 日线多头但周线空头        -2
      · 全周期共振向下            -3
      · 周期冲突                  -1
    """
    if not tf:
        return 0, []
    score, reasons = 0, []
    a = tf.get("alignment")
    d, w, mo = tf.get("daily", {}), tf.get("weekly", {}), tf.get("monthly", {})
    if a == "全周期共振向上":
        score += 2
        reasons.append("多周期: 日/周/月共振向上(+2)")
    elif a == "全周期共振向下":
        score -= 3
        reasons.append("多周期: 日/周/月共振向下(-3)")
    elif a == "周期冲突":
        if d.get("ma_direction") == "多头" and mo.get("ma_direction") == "空头":
            score -= 3
            reasons.append("多周期: 日线多头/月线空头——逆大周期反弹(-3)")
        elif d.get("ma_direction") == "多头" and w.get("ma_direction") == "空头":
            score -= 2
            reasons.append("多周期: 日线多头/周线空头——中期未转(-2)")
        else:
            score -= 1
            reasons.append("多周期: 周期方向冲突(-1)")

    # 即便未整段判为"冲突"，中期均线空头排列本身就该扣分（实测常见形态）
    if a not in ("全周期共振向下", "周期冲突"):
        if mo.get("ma_bear_stack"):
            score -= 2
            reasons.append("多周期: 月线MA20<MA50（长期均线空头排列）(-2)")
        elif w.get("ma_bear_stack"):
            score -= 1
            reasons.append("多周期: 周线MA20<MA50（中期均线空头排列）(-1)")
    return score, reasons