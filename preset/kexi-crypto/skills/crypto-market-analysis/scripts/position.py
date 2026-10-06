#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
position.py - 位置 / 拥挤度分析（v1.5.0 新增）

背景（用户实测反馈，2026-09-29）：
    插件此前的初筛信号**全是趋势 / 动量类**（站上MA20、MACD金叉、RSI强势区、
    突破20日高点加分），**没有任何"价格在历史区间的什么位置"维度**。结果是
    "技术指标都很好、但已经放量多天涨到高位"的币天然占优——用户原话：
    "之前有推荐 pyth，一个大回调之前的盈利位现在全变成了大阻力位"。

    实测佐证（2026-09-29 20 币选币会话）：25 个入选币的 90 日区间位置中位数
    66.3%，其中 12 个 ≥80%（贴着高位）；而 pyth 那次报告里 RSI14=80.3、
    高于MA20 36.8%、风险=高 的信息**只出现在风控层**，无法反向影响入选名单。

本模块提供三类互相独立的度量：

  1. 位置（position）——价格在历史区间中的相对位置
       pos_90    : 90 日区间位置 0-100（0=区间最低，100=区间最高）
       dd_from_high_90 : 距 90 日高点的回撤%（负数表示在高点下方）
       gain_20d / gain_60d : 近 20 / 60 日涨幅
       at_180d_high : 是否处于 180 日新高

  2. 拥挤度（crowding）——"放量多天、涨了很久"的量化刻画
       up_days_streak   : 近 20 日里上涨天数
       consecutive_up   : 当前连续上涨天数
       vol_expansion_days : 近 20 日里成交量 > 20日均量×1.5 的天数（"放量几天了"）
       dist_ma20_pct    : 价格高于/低于 MA20 的百分比（超买拉伸度）

  3. 形态（pattern）——低位横盘 / 震荡调整识别（用户明确要求"有没有考虑"）
       consolidating    : 是否横盘（区间宽度窄 + 量能萎缩 + 均线缠绕）
       range_width_60   : 60 日区间宽度%（(高-低)/低）
       vol_shrink_ratio : 近10日均量 / 前30日均量（<1 表示缩量，横盘特征）
       base_days        : 已在窄幅区间内维持的天数

设计原则：
  · 纯函数、只吃 klines，不联网，便于单测（合成 K 线即可驱动）；
  · 阈值集中为常量并可被 score_symbol 覆盖；
  · **不做判断**，只输出度量；打分/否决策略放在 screener 里，便于调参与回测。
"""

import kline_utils as ku

# ---------- 阈值常量（可在 screener 里覆盖，便于回测调参） ----------
HIGH_ZONE_POS = 80.0          # 区间位置 ≥ 此值 = 高位区
MID_ZONE_POS = 60.0
LOW_ZONE_POS = 40.0           # 区间位置 ≤ 此值 = 低位区
NEAR_HIGH_PCT = 3.0           # 距 90 日高点 ≤ 此值 = 贴着高点
STRETCH_MA20_PCT = 25.0       # 高于 MA20 超过此值 = 拉伸过度（pyth 当时 36.8%）
VOL_EXPANSION_MULT = 1.5      # 成交量相对 20 日均量的"放量"倍数
VOL_EXPANSION_DAYS = 5        # 近 20 日放量天数 ≥ 此值 = 已放量多天
CONSOLIDATION_MAX_WIDTH = 18.0  # 60 日区间宽度 ≤ 此值（%）算窄幅
CONSOLIDATION_VOL_SHRINK = 1.0  # 近端量能 / 远端量能 ≤ 此值 = 缩量
MIN_BARS_FOR_POSITION = 60    # 位置分析最少需要的日线根数
# 区间位置「有没有信息量」的门槛（v1.5.0）：
#   价格在 52~53 元的窄幅里来回蹭，90 日区间位置只取决于最后一根恰好落在哪，
#   是噪声而非信号——实测低位横盘币 pos_90=78.8% 被判"中高位"而误扣分。
#   故：90 日区间宽度 < 此值(%) 时，**区间位置类**扣分一律不适用
#   （拥挤度/拉伸度与区间宽度无关，仍然照常生效）。
MIN_INFORMATIVE_RANGE_PCT = 12.0

# ---------- 扣分权重（v1.5.0 回测校准，2026-09-29） ----------
# 两轮独立点时间回测（backtest.py，1534 / 2057 条观察样本）给出的可操作结论：
#   · 「纯区间位置」对未来收益区分度不稳定（均值差两轮符号翻转，被离群币主导）
#     → 由 -3 / -2 / -1 下调为 -1 / -1（贴顶再 -1）
#   · 「已放量多日」（用户点名的特征）两轮一致偏负、胜率稳定更低
#     → 由 -2 上调为 -3
# 即：**挤掉的是追高拥挤度，而不是机械地惩罚"涨得多"。**
# 显式暴露成常量并支持 position_penalty(weights=...) 覆盖，便于回测对比不同档位。
PENALTY_WEIGHTS = {
    "near_high": -1,      # 位置≥80 且贴 90 日高点
    "high_zone": -1,      # 位置≥80
    "mid_high_zone": -1,  # 位置 60~80
    "at_180d_high": -1,   # 创 180 日新高（仅高位区叠加）
    "crowded": -3,        # 放量多日且已明显上涨 ★最重
    "stretched": -2,      # 远离 MA20
}


def _pct(a, b):
    """(a/b - 1) * 100，b 为 0 时返回 None。"""
    if not b:
        return None
    return (a / b - 1) * 100


def _safe(v, nd=2):
    return round(v, nd) if isinstance(v, (int, float)) else None


def analyze_position(klines, min_bars=MIN_BARS_FOR_POSITION):
    """计算位置 / 拥挤度 / 形态度量。

    Args:
        klines: [[ts_ms, o, h, l, c, v], ...] 按时间升序（应已 drop_open_bar）
        min_bars: 最少根数，不足返回 None

    Returns:
        dict | None —— 度量字典；数据不足返回 None（调用方应视为"位置未知"）
    """
    if not klines or len(klines) < min_bars:
        return None

    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    vols = [float(k[5]) for k in klines]
    c = closes[-1]

    # ---------- 1. 位置 ----------
    w90h = highs[-90:] if len(highs) >= 90 else highs
    w90l = lows[-90:] if len(lows) >= 90 else lows
    hi90, lo90 = max(w90h), min(w90l)
    span = hi90 - lo90
    pos90 = ((c - lo90) / span * 100) if span > 0 else 50.0
    # 区间太窄时，区间位置只是"最后一根恰好落在哪"的噪声，标记为无信息量
    range_pct_90 = (span / lo90 * 100) if lo90 > 0 else None
    position_informative = bool(
        range_pct_90 is not None and range_pct_90 >= MIN_INFORMATIVE_RANGE_PCT)
    dd_high_90 = _pct(c, hi90)                      # ≤0，0 表示正在高点
    up_from_low_90 = _pct(c, lo90)                  # ≥0

    # 长窗口位置（v1.5.0 实测补强）：
    # 90 日窗口会**严重误导**——实测 ALGO 在 90 日区间位置 93.6%（"高位"），
    # 但在 2.7 年区间位置仅 1.8%（近乎历史最低），日线"高位"只是从深跌里反弹。
    # 因此同时给出全样本（最多 2 年）位置，供上层区分"真高位"与"深跌反弹"。
    hi_all, lo_all = max(highs), min(lows)
    span_all = hi_all - lo_all
    pos_all = ((c - lo_all) / span_all * 100) if span_all > 0 else 50.0
    # 距历史最高点的回撤（负数）
    dd_from_ath = _pct(c, hi_all)
    # 是否处于"深跌后反弹"形态：长期位置低但短期位置高
    rebound_from_deep = bool(pos_all < 35 and pos90 >= 70)

    # 180 日新高（样本不足时退化为全样本）
    w180h = highs[-180:] if len(highs) >= 180 else highs
    hi180 = max(w180h)
    at_180d_high = c >= hi180 * 0.995               # 容差 0.5%

    gain_20d = _pct(c, closes[-21]) if len(closes) > 21 else None
    gain_60d = _pct(c, closes[-61]) if len(closes) > 61 else None

    # ---------- 2. 拥挤度 ----------
    # 连续上涨天数
    consec_up = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            consec_up += 1
        else:
            break
    # 近 20 日上涨天数
    recent = closes[-21:]
    up_days_20 = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])

    # 放量天数：近 20 日里 volume > vol_ma20(前推) × MULT
    vol_ma20_prior = (sum(vols[-40:-20]) / 20) if len(vols) >= 40 else (
        sum(vols[:-20]) / max(1, len(vols) - 20) if len(vols) > 20 else 0)
    vol_expansion_days = 0
    if vol_ma20_prior > 0:
        vol_expansion_days = sum(
            1 for v in vols[-20:] if v > vol_ma20_prior * VOL_EXPANSION_MULT)

    ma20 = (sum(closes[-20:]) / 20) if len(closes) >= 20 else None
    dist_ma20 = _pct(c, ma20) if ma20 else None

    # ---------- 3. 形态：低位横盘 / 震荡调整 ----------
    w60h = highs[-60:] if len(highs) >= 60 else highs
    w60l = lows[-60:] if len(lows) >= 60 else lows
    hi60, lo60 = max(w60h), min(w60l)
    range_width_60 = ((hi60 - lo60) / lo60 * 100) if lo60 > 0 else None

    # 量能萎缩：近 10 日均量 / 更早 30 日均量
    vol_shrink = None
    if len(vols) >= 40:
        near = sum(vols[-10:]) / 10
        far = sum(vols[-40:-10]) / 30
        vol_shrink = (near / far) if far > 0 else None

    # 均线缠绕（横盘特征）：MA5/MA10/MA20 互相差距都很小
    ma5 = (sum(closes[-5:]) / 5) if len(closes) >= 5 else None
    ma10 = (sum(closes[-10:]) / 10) if len(closes) >= 10 else None
    ma_tangle = None
    if ma5 and ma10 and ma20:
        mas = [ma5, ma10, ma20]
        ma_tangle = (max(mas) - min(mas)) / ma20 * 100

    # 已在窄幅区间维持多少天：
    # 注意——原实现用 max() 跨所有窗口取最大宽度合格值，会把"曾经横盘过、后来
    # 已经突破"的币也算成长横盘（实测 ALGO 这类已拉升币被误判 base=120d）。
    # 正确口径：**从最新一根往前连续**满足窄幅的窗口长度才算"已维持天数"。
    base_days = 0
    if len(closes) >= 30:
        max_lb = min(len(closes), 120)
        for lookback in range(20, max_lb + 1):
            seg_h = max(highs[-lookback:])
            seg_l = min(lows[-lookback:])
            if seg_l <= 0:
                break
            width = (seg_h - seg_l) / seg_l * 100
            if width <= CONSOLIDATION_MAX_WIDTH:
                base_days = lookback          # 连续合格则持续抬高
            else:
                break                          # 一旦不合格就停（连续性）
        if base_days < 20:
            base_days = 0

    consolidating = bool(
        range_width_60 is not None and range_width_60 <= CONSOLIDATION_MAX_WIDTH
        and vol_shrink is not None and vol_shrink <= CONSOLIDATION_VOL_SHRINK
        and ma_tangle is not None and ma_tangle <= 6.0
    )

    # ---------- 派生标签 ----------
    if not position_informative:
        # 区间太窄：位置标签误导性强，标为"窄幅区间"而非"高位/中位"
        zone = "窄幅"
    elif rebound_from_deep and pos90 >= HIGH_ZONE_POS:
        # 短期高、长期低 → 准确表述是"深跌反弹"而非"高位"
        zone = "深跌反弹"
    elif pos90 >= HIGH_ZONE_POS:
        zone = "高位"
    elif pos90 >= MID_ZONE_POS:
        zone = "中高位"
    elif pos90 >= LOW_ZONE_POS:
        zone = "中位"
    else:
        zone = "低位"

    crowded = bool(
        vol_expansion_days >= VOL_EXPANSION_DAYS
        and (gain_20d or 0) > 15
    )

    stretched = bool(dist_ma20 is not None and dist_ma20 >= STRETCH_MA20_PCT)

    return {
        # 位置
        "pos_90": _safe(pos90, 1),
        "zone": zone,
        "range_pct_90": _safe(range_pct_90, 1),
        "position_informative": position_informative,
        "dd_from_high_90": _safe(dd_high_90, 1),
        "up_from_low_90": _safe(up_from_low_90, 1),
        "hi_90": ku.rn(hi90), "lo_90": ku.rn(lo90),
        # 长窗口（全样本，最多约2年）位置——区分"真高位"与"深跌后反弹"
        "pos_all": _safe(pos_all, 1),
        "hi_all": ku.rn(hi_all), "lo_all": ku.rn(lo_all),
        "dd_from_ath": _safe(dd_from_ath, 1),
        "sample_bars": len(klines),
        "rebound_from_deep": rebound_from_deep,
        "at_180d_high": bool(at_180d_high),
        "gain_20d": _safe(gain_20d, 1),
        "gain_60d": _safe(gain_60d, 1),
        # 拥挤度
        "consecutive_up": consec_up,
        "up_days_20": up_days_20,
        "vol_expansion_days": vol_expansion_days,
        "dist_ma20_pct": _safe(dist_ma20, 1),
        "crowded": crowded,
        "stretched": stretched,
        # 形态
        "range_width_60": _safe(range_width_60, 1),
        "vol_shrink_ratio": _safe(vol_shrink, 2),
        "ma_tangle_pct": _safe(ma_tangle, 2),
        "base_days": base_days,
        "consolidating": consolidating,
    }


def position_penalty(pos, high_zone_pos=HIGH_ZONE_POS, near_high_pct=NEAR_HIGH_PCT,
                     stretch_pct=STRETCH_MA20_PCT, crowded_days=VOL_EXPANSION_DAYS,
                     weights=None):
    """把位置度量折算成**扣分**（返回 (扣分, 理由列表)）。

    这是直接回应用户痛点的核心：让"已在高位 + 放量多天 + 拉伸过度"的币
    在初筛阶段就被压下去，而不是等到风控层才提示。

    ══════════════════════════════════════════════════════════════════════
    权重来源：**两轮独立历史回测**（backtest.py，Top50/step10 与 Top100/step15，
    样本 1534 / 2057 条，2025-05 ~ 2026-08）。三组结论：

      ① 「90日区间位置」高低对未来的**区分度不稳定**——均值差在两轮间
         **符号翻转**（高位组 30 日均值 一轮 +3.25%、另一轮 -7.87%），
         原因是均值被 +904%/+371% 的极少数离群币主导；中位数差也很小。
         → 纯位置只能作**风险提示**，**不该重罚**。故权重从 -3/-2/-1
           统一下调到 -1（P_HIGH / P_MID_HIGH），贴顶额外 -1（P_NEAR_HIGH）。
      ② 用户点名的「已放量多日」特征**两轮一致偏负**且胜率稳定偏低
         （30 日胜率 32.9% / 32.1%，vs 未放量 43.4% / 39.0%）。
         → **拥挤度比区间位置更能刻画追高风险**，权重从 -2 **上调到 -3**。
      ③ 「多周期共振向上 vs 共振向下」两轮一致（见 timeframe.py），保留。

    结论：**加重拥挤度、减轻纯位置**——这正是回测给出的可操作结论。
    ══════════════════════════════════════════════════════════════════════

    扣分规则（weights 可覆盖，便于回测对比不同策略）：
      · 区间位置 ≥80 且贴着 90 日高点（回撤 > -3%）   -1  「高位贴顶」
      · 另加：创 180 日新高（且位于高位区）            -1  「历史新高追高」
      · 区间位置 ≥80（未贴顶）                         -1  「高位区」
      · 中高区（60-80）                                -1  「中高区」
      · 近 20 日放量天数 ≥5 且涨 >15%                  -3  「放量多日已拉伸」★加重
      · 高于 MA20 ≥25%                                 -2  「远离均线超买」
    低位 / 中位不加不减。

    重要（v1.5.0）：**区间位置类**扣分只在 `position_informative` 为真时生效。
    若 90 日区间宽度 < MIN_INFORMATIVE_RANGE_PCT，说明价格一直在窄幅里蹭，
    "位置 78%" 只是最后一根恰好落在上沿的噪声——此时不扣位置分
    （否则会把真正低位横盘蓄势的币误判为中高位而扣分，实测已复现）。
    拥挤度（放量多日）与拉伸度（距 MA20）与区间宽度无关，照常生效。
    """
    if not pos:
        return 0, []
    w = dict(PENALTY_WEIGHTS)
    if weights:
        w.update(weights)
    pen, reasons = 0, []
    p90 = pos.get("pos_90")
    dd = pos.get("dd_from_high_90")
    if p90 is None:
        return 0, []

    # 区间无信息量 → 跳过所有"区间位置类"扣分
    informative = pos.get("position_informative", True)
    if not informative:
        reasons.append(
            f"位置说明: 90日区间仅{pos.get('range_pct_90')}%宽（<{MIN_INFORMATIVE_RANGE_PCT}%），"
            f"区间位置{p90:.0f}%无判别力，不计位置扣分")
    else:
        # 「深跌后反弹」降档（v1.5.0 实测补强）：
        # 实测 ALGO/SEI/BTC 等 90 日位置 92%+ 看似"高位贴顶"，但放在 2.7 年维度
        # 仅 1.8%/3.7%（近乎历史最低）——它们不是"涨到高位"，而是"从深跌里反弹"。
        # 两者风险性质不同：真高位是获利盘堆积，深跌反弹是套牢盘解套压力。
        # 故对 rebound_from_deep 的标的，位置类扣分减半（仍扣，因为短期确实涨多了）。
        deep_rebound = pos.get("rebound_from_deep", False)
        factor = 0.5 if deep_rebound else 1.0
        if deep_rebound:
            reasons.append(
                f"位置说明: 90日位置{p90:.0f}%高、但全样本位置仅{pos.get('pos_all')}%"
                f"（距历史高点{pos.get('dd_from_ath')}%）→ 属**深跌后反弹**、非真高位，"
                f"位置扣分减半")

        near_high = dd is not None and dd > -near_high_pct
        tag = "×0.5" if deep_rebound else ""
        if p90 >= high_zone_pos and near_high:
            pen += int(round(w["near_high"] * factor))
            reasons.append(
                f"位置扣分: 90日区间{p90:.0f}%且距高点仅{dd:.1f}%（高位贴顶 "
                f"{w['near_high']}{tag}）")
        elif p90 >= high_zone_pos:
            pen += int(round(w["high_zone"] * factor))
            reasons.append(
                f"位置扣分: 90日区间高位{p90:.0f}%（{w['high_zone']}{tag}）")
        elif p90 >= MID_ZONE_POS:
            pen += int(round(w["mid_high_zone"] * factor))
            reasons.append(
                f"位置扣分: 90日区间中高位{p90:.0f}%（{w['mid_high_zone']}{tag}）")
        else:
            # 显式说明低位/中位为何不扣分（避免"静默无输出"被误读为漏判）
            reasons.append(f"位置说明: 90日区间{p90:.0f}%属低位/中位，不计位置扣分")

        if pos.get("at_180d_high") and p90 >= high_zone_pos:
            pen += w["at_180d_high"]
            reasons.append(f"位置扣分: 创180日新高（追高 {w['at_180d_high']}）")

    if pos.get("vol_expansion_days", 0) >= crowded_days and (pos.get("gain_20d") or 0) > 15:
        pen += w["crowded"]
        reasons.append(
            f"位置扣分: 近20日放量{pos['vol_expansion_days']}天且涨{pos['gain_20d']:.0f}%"
            f"（放量多日已拉伸 {w['crowded']}，回测验证权重最重）")

    dm = pos.get("dist_ma20_pct")
    if dm is not None and dm >= stretch_pct:
        pen += w["stretched"]
        reasons.append(f"位置扣分: 高于MA20 {dm:.1f}%（拉伸过度 {w['stretched']}）")

    return pen, reasons


def position_vetoes(pos, hard_pos=None, hard_stretch=None):
    """硬性否决（默认关闭，返回空列表）。

    设计取向：**默认不启用硬否决**——高位不等于必跌（趋势可延续），一票否决
    会错杀强势主升浪。默认只用扣分（position_penalty）。需要更严格时可显式
    传 hard_pos / hard_stretch 开启（供回测对比不同策略）。

    Args:
        hard_pos    : 区间位置 ≥ 此值时否决（如 95）
        hard_stretch: 高于 MA20 ≥ 此值时否决（如 40）
    """
    if not pos:
        return []
    v = []
    if hard_pos is not None and pos.get("pos_90") is not None and pos["pos_90"] >= hard_pos:
        v.append(f"位置≥{hard_pos:.0f}%（{pos['pos_90']:.0f}%）")
    if hard_stretch is not None and pos.get("dist_ma20_pct") is not None \
            and pos["dist_ma20_pct"] >= hard_stretch:
        v.append(f"高于MA20 {pos['dist_ma20_pct']:.1f}%（≥{hard_stretch:.0f}%）")
    return v