#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.py - 研判报告可视化渲染器（单文件离线可视化看板）

CLI:
    python dashboard.py --input report.json [--indicators indicators.json] [--klines btc_1d.json] [--portfolio portfolio.json] --out out/report.html [--title 自定义] [--no-candles]

功能与特性：
    - 零第三方依赖、零外部资源：仅使用 Python 标准库，纯内联 CSS + 手绘内联 SVG，严禁任何 http(s) 外链
    - 深色/浅色自适应：基于 @media (prefers-color-scheme: dark) 与 CSS 变量实现，无主题切换 JS
    - 现代化金融看板结构：
        1. 标题与核心结论栏（币种、周期、数据截至、数据源、可信度徽标、Headline 结论横幅）
        2. K线蜡烛图（最近 <= 120 根已收盘 bar，剔除未收盘 bar，叠加 MA5/10/20/60 折线与成交量柱）
        3. 指标卡片网格（均线排列状态、MACD 动能图、RSI 30/70 标尺图、量比趋势、波动画像）
        4. 支撑/阻力阶梯图（横向分层，现价居中，标注价位、强度与依据）
        5. 三情景概率条（横向条带 + 目标区间与触发条件卡片）
        6. 风险面板（风险等级色块、评定依据、风险警告列表、极端事件）
        7. 组合面板（若提供 --portfolio：相关性热力图、仓位预算表、假分散预警大红横幅）
        8. 页脚免责声明（不可省略，包含生成时间与合规提示）
    - 健壮性：缺失/为 null 字段渲染为优雅占位卡片，绝不抛栈；输出体积控制在 200KB 以内
    - stdout 首行输出紧凑 JSON: {ok, out, bytes, sections_rendered, warnings}

Stdlib only.
"""

import argparse
import html
from datetime import datetime, timezone, timedelta
import json
import math
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import kline_utils as ku

TZ8 = timezone(timedelta(hours=8))


def safe_escape(val):
    if val is None:
        return "—"
    return html.escape(str(val))


def fmt_p(val, decimals=None):
    if val is None:
        return "—"
    if isinstance(val, (int, float)):
        if decimals is not None:
            return f"{val:.{decimals}f}"
        return f"{ku.rn(val):,}"
    return html.escape(str(val))


def pct_diff(curr, target):
    if curr is None or target is None or curr <= 0:
        return "—"
    diff = (target - curr) / curr * 100
    sign = "+" if diff > 0 else ""
    return f"{sign}{diff:.2f}%"


# ---------- SVG 绘制纯函数 ----------

def render_candle_chart_svg(klines, width=1000, height=460):
    """
    手绘内联 SVG K线蜡烛图纯函数：
    - 最近 <= 120 根已收盘 K 线
    - 坐标 y 轴自然翻转（价格越高 y 越小）
    - 叠加 MA5/10/20/60 折线
    - 下方成交量柱状图
    """
    if not klines or len(klines) < 2:
        return '<div class="empty-card">暂无充足 K 线数据以绘制蜡烛图</div>'

    # 取最近 <= 120 根
    closed_klines = klines[-120:]
    n = len(closed_klines)

    pad_left = 65
    pad_right = 75
    pad_top = 25
    pad_bottom = 35
    gap_y = 20

    chart_w = width - pad_left - pad_right
    price_h = 280
    vol_h = 100

    closes = [k[4] for k in closed_klines]
    highs = [k[2] for k in closed_klines]
    lows = [k[3] for k in closed_klines]
    volumes = [k[5] for k in closed_klines]

    min_p, max_p = min(lows), max(highs)
    p_span = (max_p - min_p) if max_p > min_p else 1.0
    min_p -= p_span * 0.03
    max_p += p_span * 0.03
    p_span = max_p - min_p

    max_v = max(volumes) if any(v > 0 for v in volumes) else 1.0

    def price_to_y(p):
        return pad_top + (max_p - p) / p_span * price_h

    vol_base_y = pad_top + price_h + gap_y + vol_h

    def vol_to_y(v):
        return vol_base_y - (v / max_v * vol_h if max_v > 0 else 0)

    step_x = chart_w / n
    bar_w = max(2.5, min(step_x * 0.72, 10.0))

    # 计算移动平均线
    def calc_ma_series(period):
        res = [None] * n
        for i in range(period - 1, n):
            sub = closes[i - period + 1 : i + 1]
            res[i] = sum(sub) / period
        return res

    ma5_s = calc_ma_series(5)
    ma10_s = calc_ma_series(10)
    ma20_s = calc_ma_series(20)
    ma60_s = calc_ma_series(60)

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" class="candle-svg" xmlns="http://www.w3.org/2000/svg">'
    ]

    # 背景参考网格线与价格刻度
    grid_steps = 5
    for i in range(grid_steps + 1):
        price_val = min_p + p_span * (i / grid_steps)
        gy = price_to_y(price_val)
        svg_parts.append(
            f'<line x1="{pad_left}" y1="{gy:.1f}" x2="{width - pad_right}" y2="{gy:.1f}" '
            f'stroke="var(--grid-line)" stroke-dasharray="3,3" stroke-width="0.8"/>'
        )
        svg_parts.append(
            f'<text x="{width - pad_right + 6}" y="{gy + 4:.1f}" fill="var(--text-muted)" '
            f'font-size="10" font-family="sans-serif">{ku.rn(price_val):,}</text>'
        )

    # 绘制蜡烛与成交量
    for i, k in enumerate(closed_klines):
        ts, o, h, l, c, v = k[0], k[1], k[2], k[3], k[4], k[5]
        cx = pad_left + i * step_x + step_x / 2.0
        y_o = price_to_y(o)
        y_c = price_to_y(c)
        y_h = price_to_y(h)
        y_l = price_to_y(l)

        is_up = (c >= o)
        bar_color = "var(--candle-up)" if is_up else "var(--candle-down)"

        # 影线
        svg_parts.append(
            f'<line x1="{cx:.1f}" y1="{y_h:.1f}" x2="{cx:.1f}" y2="{y_l:.1f}" '
            f'stroke="{bar_color}" stroke-width="1.2"/>'
        )

        # 实体
        top_y = min(y_o, y_c)
        body_h = max(1.5, abs(y_o - y_c))
        svg_parts.append(
            f'<rect x="{cx - bar_w/2.0:.1f}" y="{top_y:.1f}" width="{bar_w:.1f}" height="{body_h:.1f}" '
            f'fill="{bar_color}"/>'
        )

        # 成交量柱
        y_vol_top = vol_to_y(v)
        v_h = max(1.0, vol_base_y - y_vol_top)
        svg_parts.append(
            f'<rect x="{cx - bar_w/2.0:.1f}" y="{y_vol_top:.1f}" width="{bar_w:.1f}" height="{v_h:.1f}" '
            f'fill="{bar_color}" opacity="0.6"/>'
        )

    # 绘制 MA 折线
    def make_ma_polyline(series, stroke_color):
        pts = []
        for i, val in enumerate(series):
            if val is not None:
                cx = pad_left + i * step_x + step_x / 2.0
                cy = price_to_y(val)
                pts.append(f"{cx:.1f},{cy:.1f}")
        if pts:
            return f'<polyline points="{" ".join(pts)}" fill="none" stroke="{stroke_color}" stroke-width="1.4"/>'
        return ""

    svg_parts.append(make_ma_polyline(ma5_s, "#eab308"))   # 黄色
    svg_parts.append(make_ma_polyline(ma10_s, "#f97316"))  # 橙色
    svg_parts.append(make_ma_polyline(ma20_s, "#a855f7"))  # 紫色
    svg_parts.append(make_ma_polyline(ma60_s, "#3b82f6"))  # 蓝色

    # X 轴日期标注（均匀分布 5 个标签）
    date_step = max(1, n // 5)
    for i in range(0, n, date_step):
        cx = pad_left + i * step_x + step_x / 2.0
        dt = datetime.fromtimestamp(closed_klines[i][0] / 1000, TZ8)
        d_str = dt.strftime("%m-%d")
        svg_parts.append(
            f'<text x="{cx:.1f}" y="{vol_base_y + 18}" fill="var(--text-muted)" font-size="10" '
            f'text-anchor="middle" font-family="sans-serif">{d_str}</text>'
        )

    # 图例 Legend
    svg_parts.append(
        f'<g transform="translate({pad_left + 10}, 16)" font-size="11" font-family="sans-serif">'
        f'<rect x="0" y="-8" width="8" height="8" fill="#eab308"/>'
        f'<text x="12" y="0" fill="var(--text-muted)">MA5 {ku.rn(ma5_s[-1]) if ma5_s[-1] else "—"}</text>'
        f'<rect x="110" y="-8" width="8" height="8" fill="#f97316"/>'
        f'<text x="122" y="0" fill="var(--text-muted)">MA10 {ku.rn(ma10_s[-1]) if ma10_s[-1] else "—"}</text>'
        f'<rect x="220" y="-8" width="8" height="8" fill="#a855f7"/>'
        f'<text x="232" y="0" fill="var(--text-muted)">MA20 {ku.rn(ma20_s[-1]) if ma20_s[-1] else "—"}</text>'
        f'<rect x="330" y="-8" width="8" height="8" fill="#3b82f6"/>'
        f'<text x="342" y="0" fill="var(--text-muted)">MA60 {ku.rn(ma60_s[-1]) if ma60_s[-1] else "—"}</text>'
        f'</g>'
    )

    svg_parts.append('</svg>')
    return "\n".join(svg_parts)


def render_rsi_svg(rsi14_val):
    """绘制 RSI 30/70 参考线与指针微型 SVG"""
    if rsi14_val is None:
        return ""
    val = max(0.0, min(100.0, float(rsi14_val)))
    w, h = 220, 36
    px = val / 100.0 * w
    status_color = "var(--accent)"
    if val >= 70:
        status_color = "var(--candle-down)"
    elif val <= 30:
        status_color = "var(--candle-up)"

    return f'''
    <svg viewBox="0 0 {w} {h}" class="mini-svg" xmlns="http://www.w3.org/2000/svg">
        <!-- 标尺背景 -->
        <rect x="0" y="14" width="{w}" height="8" rx="4" fill="var(--bg-subtle)" />
        <!-- 超卖区 (0-30) -->
        <rect x="0" y="14" width="{w*0.3:.1f}" height="8" rx="4" fill="var(--candle-up)" opacity="0.25"/>
        <!-- 超买区 (70-100) -->
        <rect x="{w*0.7:.1f}" y="14" width="{w*0.3:.1f}" height="8" rx="4" fill="var(--candle-down)" opacity="0.25"/>
        <!-- 30 与 70 参考线 -->
        <line x1="{w*0.3:.1f}" y1="10" x2="{w*0.3:.1f}" y2="26" stroke="var(--text-muted)" stroke-width="1" stroke-dasharray="2,2"/>
        <line x1="{w*0.7:.1f}" y1="10" x2="{w*0.7:.1f}" y2="26" stroke="var(--text-muted)" stroke-width="1" stroke-dasharray="2,2"/>
        <text x="{w*0.3:.1f}" y="34" fill="var(--text-light)" font-size="8" text-anchor="middle">30</text>
        <text x="{w*0.7:.1f}" y="34" fill="var(--text-light)" font-size="8" text-anchor="middle">70</text>
        <!-- 当前值指针 -->
        <circle cx="{px:.1f}" cy="18" r="5" fill="{status_color}" stroke="var(--bg-card)" stroke-width="2"/>
    </svg>
    '''


def render_macd_svg(hist_val, hist_last6):
    """绘制 MACD 柱状走势微型 SVG"""
    w, h = 180, 40
    mid_y = 20.0
    if not hist_last6 or len(hist_last6) == 0:
        if hist_val is None:
            return ""
        hist_last6 = [hist_val]

    max_mag = max(abs(x) for x in hist_last6) if hist_last6 else 1.0
    if max_mag == 0:
        max_mag = 1.0

    bar_step = w / max(1, len(hist_last6))
    bar_w = min(18.0, bar_step * 0.7)

    parts = [
        f'<svg viewBox="0 0 {w} {h}" class="mini-svg" xmlns="http://www.w3.org/2000/svg">',
        f'<line x1="0" y1="{mid_y}" x2="{w}" y2="{mid_y}" stroke="var(--border-color)" stroke-width="1"/>'
    ]

    for i, v in enumerate(hist_last6):
        cx = i * bar_step + bar_step / 2.0
        norm_h = (abs(v) / max_mag) * (mid_y - 4)
        is_pos = v >= 0
        fill_c = "var(--candle-up)" if is_pos else "var(--candle-down)"
        rect_y = (mid_y - norm_h) if is_pos else mid_y
        parts.append(
            f'<rect x="{cx - bar_w/2.0:.1f}" y="{rect_y:.1f}" width="{bar_w:.1f}" height="{max(1.0, norm_h):.1f}" '
            f'fill="{fill_c}" opacity="0.85" rx="1"/>'
        )

    parts.append('</svg>')
    return "".join(parts)


# ---------- 核心页面渲染 ----------

def render_dashboard_html(report_data, klines_data=None, portfolio_data=None, custom_title=None, show_candles=True):
    sym = report_data.get("symbol") or {}
    dq = report_data.get("data_quality") or {}
    ind = report_data.get("indicators") or {}
    trend = report_data.get("trend") or {}
    risk = report_data.get("risk") or {}
    verdict = report_data.get("verdict") or {}
    disclaimer = report_data.get("disclaimer") or "本报告基于公开数据，不构成任何投资建议。"

    code = sym.get("code") or sym.get("name") or "BTCUSDT"
    interval = sym.get("interval") or dq.get("interval_actual") or "1d"
    price = sym.get("price")
    as_of = report_data.get("as_of") or "—"
    source = sym.get("source") or "binance"
    credibility = dq.get("credibility") or "可用"

    title = custom_title or f"【K析研判】{code} {interval} 行情分析看板"

    # 可信度与风险颜色映射
    cred_badge_cls = {
        "可用": "badge-success",
        "部分可用": "badge-warning",
        "不可用": "badge-danger",
    }.get(credibility, "badge-neutral")

    rlevel = risk.get("level") or "中"
    risk_badge_cls = {
        "低": "risk-low",
        "中": "risk-mid",
        "高": "risk-high",
        "极高": "risk-extreme",
    }.get(rlevel, "risk-mid")

    sections_rendered = ["header", "verdict"]

    # 1. 均线数据
    ma = ind.get("ma") or {}
    ma5 = ma.get("ma5")
    ma10 = ma.get("ma10")
    ma20 = ma.get("ma20")
    ma60 = ma.get("ma60")
    ma_align = ind.get("ma_alignment") or "—"

    # 2. MACD 数据
    macd = ind.get("macd") or {}
    dif = macd.get("dif")
    dea = macd.get("dea")
    hist = macd.get("hist")
    macd_last6 = macd.get("hist_last6") or []
    macd_state = macd.get("state") or macd.get("hist_direction") or "—"

    # 3. RSI 数据
    rsi = ind.get("rsi") or {}
    rsi6 = rsi.get("rsi6")
    rsi14 = rsi.get("rsi14")
    rsi_state = rsi.get("state") or "中性"

    # 4. 量价
    vol_ratio = ind.get("volume_ratio")
    vol_trend = "持平"
    if vol_ratio is not None:
        if vol_ratio > 1.5:
            vol_trend = "放量"
        elif vol_ratio < 0.7:
            vol_trend = "缩量"

    # 5. 波动画像
    volatility = ind.get("volatility") or {}
    atr14 = volatility.get("atr14")
    atr_pct = volatility.get("atr_pct")
    mdd30 = volatility.get("max_drawdown_30d") or volatility.get("max_dd_30d_pct")

    # 6. K线蜡烛图
    candles_html = ""
    raw_klines = None
    if show_candles:
        if klines_data and isinstance(klines_data, dict) and "klines" in klines_data:
            raw_klines = klines_data.get("klines")
        elif isinstance(report_data.get("klines"), list):
            raw_klines = report_data.get("klines")
        elif isinstance(ind.get("klines"), list):
            raw_klines = ind.get("klines")

        if raw_klines:
            closed_k, _ = ku.drop_open_bar(raw_klines, ku.INTERVAL_MS.get(interval, ku.D1))
            candles_html = render_candle_chart_svg(closed_k)
            sections_rendered.append("candle_chart")
        else:
            candles_html = '<div class="empty-card">未提供蜡烛图原始 K 线数据（可通过 --klines 传入）</div>'
    else:
        candles_html = '<div class="empty-card">蜡烛图渲染已按 --no-candles 参数禁用</div>'

    sections_rendered.append("indicators")

    # 7. 支撑与阻力阶梯
    sr = ind.get("support_resistance") or {}
    res_list = sr.get("resistance") or []
    sup_list = sr.get("support") or []
    sections_rendered.append("support_resistance")

    # 8. 三情景概率条
    scenarios = trend.get("scenarios") or []
    sections_rendered.append("scenarios")

    # 9. 风险面板
    warnings_list = risk.get("warnings") or []
    extreme_events = risk.get("extreme_events") or []
    data_gaps = dq.get("gaps") or risk.get("data_gaps") or []
    sections_rendered.append("risk_panel")

    # 10. 组合面板
    portfolio_html = ""
    if portfolio_data:
        sections_rendered.append("portfolio")
        fake_div = portfolio_data.get("fake_diversification", False)
        positions = portfolio_data.get("positions") or []
        corr_matrix = portfolio_data.get("correlation_matrix") or {}
        symbols = [p.get("symbol") for p in positions if p.get("symbol")]

        fake_banner = ""
        if fake_div:
            fake_banner = '''
            <div class="alert-box alert-danger">
                <strong>⚠️ 假分散高危预警：</strong> 组合内检测到多标的高度相关（相关系数 &gt; 0.85），实际承担单边同向风险共振，无法达到风险对冲目的！
            </div>
            '''

        # 仓位预算表
        pos_rows = []
        for p in positions:
            sym_name = p.get("symbol", "—")
            c_val = fmt_p(p.get("close"))
            a14 = fmt_p(p.get("atr14"))
            ap = fmt_p(p.get("atr_pct"), decimals=2)
            w = f"{p.get('weight') * 100:.2f}%" if p.get("weight") is not None else "—"
            notional = f"{p.get('notional'):,.2f} U" if p.get("notional") is not None else "—"
            amihud = fmt_p(p.get("amihud"), decimals=2)
            err = p.get("error") or "正常"
            pos_rows.append(f'''
            <tr>
                <td><strong>{sym_name}</strong></td>
                <td>{c_val}</td>
                <td>{a14}</td>
                <td>{ap}%</td>
                <td><span class="weight-tag">{w}</span></td>
                <td>{notional}</td>
                <td>{amihud}</td>
                <td><span class="badge {'' if err=='正常' else 'badge-warning'}">{err}</span></td>
            </tr>
            ''')

        # 相关性热力图表格
        corr_headers = "".join([f"<th>{s}</th>" for s in symbols])
        corr_rows = []
        for s1 in symbols:
            cells = [f"<td><strong>{s1}</strong></td>"]
            for s2 in symbols:
                val = corr_matrix.get(s1, {}).get(s2)
                if val is None:
                    cells.append("<td>—</td>")
                else:
                    is_high = (s1 != s2 and val > 0.85)
                    style = "background-color: var(--candle-down-subtle); color: var(--candle-down); font-weight: bold;" if is_high else ""
                    star = " ★" if is_high else ""
                    cells.append(f'<td style="{style}">{val:.2f}{star}</td>')
            corr_rows.append(f"<tr>{''.join(cells)}</tr>")

        portfolio_html = f'''
        <div class="card card-portfolio">
            <div class="card-header">
                <h3>投资组合量化画像与相关性检验</h3>
                <span class="badge badge-accent">等风险预算</span>
            </div>
            {fake_banner}
            <div class="table-responsive">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>标的</th><th>收盘价</th><th>ATR14</th><th>ATR%</th>
                            <th>建议权重</th><th>分配本金</th><th>Amihud冲击</th><th>状态</th>
                        </tr>
                    </thead>
                    <tbody>
                        {"".join(pos_rows)}
                    </tbody>
                </table>
            </div>

            <h4 style="margin-top: 20px; margin-bottom: 10px; font-size: 14px;">近 60 根对齐日线收益率 Pearson 相关系数矩阵</h4>
            <div class="table-responsive">
                <table class="data-table corr-table">
                    <thead>
                        <tr><th>标的</th>{corr_headers}</tr>
                    </thead>
                    <tbody>
                        {"".join(corr_rows)}
                    </tbody>
                </table>
            </div>
            <div class="footnote">★ 标记表示两两相关系数 &gt; 0.85，提示表面分散实则同一风险敞口。</div>
        </div>
        '''

    # 11. 密集成交区 / 筹码峰面板（G4）
    vp = ind.get("volume_profile") or {}
    vp_html = ""
    if vp.get("available"):
        sections_rendered.append("volume_profile")
        poc = vp.get("point_of_control") or {}
        va = vp.get("value_area") or {}
        maxpct = max((b.get("pct") or 0) for b in vp.get("bins", [])) or 1
        tag_cls = {"POC": "vp-poc", "HVN": "vp-hvn", "LVN": "vp-lvn", "normal": ""}
        rows_html = []
        for b in reversed(vp.get("bins", [])):
            w = 100 * (b.get("pct") or 0) / maxpct
            tag = b.get("tag", "normal")
            rows_html.append(f'''
            <div class="vp-row">
                <div class="vp-price">{fmt_p(b.get("price"))}</div>
                <div class="vp-bar-wrap"><div class="vp-bar {tag_cls.get(tag, '')}" style="width:{w:.1f}%"></div></div>
                <div class="vp-pct">{(b.get("pct") or 0):.2f}%{('<span class="vp-tag">' + tag + '</span>') if tag != 'normal' else ''}</div>
            </div>
            ''')
        vp_html = f'''
        <section class="card vp-card">
            <div class="card-header">
                <h3>密集成交区 / 筹码分布（近 {vp.get('lookback_bars')} 根 · {vp.get('bins_count')} 价格箱）</h3>
                <span class="badge badge-neutral">POC {fmt_p(poc.get('price'))}</span>
            </div>
            <div class="vp-summary">
                <span class="badge badge-accent">最大筹码峰 POC: {fmt_p(poc.get('price'))}（{poc.get('pct')}%）</span>
                <span class="badge badge-success">70% 价值区: {fmt_p(va.get('va_low'))} ~ {fmt_p(va.get('va_high'))}（{va.get('covers_pct')}%）</span>
                <span class="badge badge-warning">HVN 高量节点 {len(vp.get('hvn', []))} 处（强支撑/阻力）</span>
                <span class="badge badge-neutral">LVN 低量节点 {len(vp.get('lvn', []))} 处（真空区，易快速穿越）</span>
            </div>
            <div class="vp-hist">
                {"".join(rows_html)}
            </div>
            <div class="footnote">POC=成交量控制点（历史成交最密集，强支撑/阻力）；HVN=高成交量节点；LVN=低成交量真空区。现价在价值区之上=已突破。</div>
        </section>
        '''
    else:
        vp_html = f'''
        <section class="card vp-card">
            <div class="card-header"><h3>密集成交区 / 筹码分布</h3></div>
            <div class="empty-card">{safe_escape(vp.get("note", "筹码峰不可用"))}</div>
        </section>
        '''

    # 构建 HTML 内容
    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_escape(title)}</title>
    <style>
        :root {{
            --bg-page: #f8fafc;
            --bg-card: #ffffff;
            --bg-subtle: #f1f5f9;
            --border-color: #e2e8f0;
            --text-main: #0f172a;
            --text-muted: #64748b;
            --text-light: #94a3b8;
            --candle-up: #10b981;
            --candle-up-subtle: rgba(16, 185, 129, 0.12);
            --candle-down: #ef4444;
            --candle-down-subtle: rgba(239, 68, 68, 0.12);
            --accent: #2563eb;
            --accent-subtle: rgba(37, 99, 235, 0.08);
            --warning: #f59e0b;
            --warning-subtle: rgba(245, 158, 11, 0.12);
            --grid-line: #e2e8f0;
            --shadow-sm: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
            --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.07), 0 2px 4px -1px rgba(0,0,0,0.04);
            --radius: 8px;
            --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
        }}

        @media (prefers-color-scheme: dark) {{
            :root {{
                --bg-page: #0b0f19;
                --bg-card: #111827;
                --bg-subtle: #1f2937;
                --border-color: #374151;
                --text-main: #f9fafb;
                --text-muted: #9ca3af;
                --text-light: #6b7280;
                --candle-up: #10b981;
                --candle-up-subtle: rgba(16, 185, 129, 0.18);
                --candle-down: #f87171;
                --candle-down-subtle: rgba(248, 113, 113, 0.18);
                --accent: #3b82f6;
                --accent-subtle: rgba(59, 130, 246, 0.15);
                --warning: #fbbf24;
                --warning-subtle: rgba(251, 191, 36, 0.18);
                --grid-line: #1f2937;
                --shadow-sm: 0 1px 3px rgba(0,0,0,0.4);
                --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.4);
            }}
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-page);
            color: var(--text-main);
            font-family: var(--font-sans);
            line-height: 1.5;
            padding: 24px 16px;
        }}
        .container {{
            max-width: 1160px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }}

        /* 顶部导航与元信息 */
        .header-bar {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius);
            padding: 20px 24px;
            box-shadow: var(--shadow-sm);
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
        }}
        .header-left {{
            display: flex;
            align-items: baseline;
            gap: 12px;
        }}
        .symbol-title {{
            font-size: 26px;
            font-weight: 800;
            letter-spacing: -0.5px;
        }}
        .symbol-interval {{
            font-size: 13px;
            background: var(--bg-subtle);
            color: var(--text-muted);
            padding: 2px 8px;
            border-radius: 4px;
            font-weight: 600;
        }}
        .header-meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 14px;
            font-size: 13px;
            color: var(--text-muted);
        }}
        .price-badge {{
            font-size: 24px;
            font-weight: 700;
            color: var(--text-main);
            font-variant-numeric: tabular-nums;
        }}

        /* 徽标体系 */
        .badge {{
            display: inline-block;
            font-size: 12px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 4px;
        }}
        .badge-success {{ background: var(--candle-up-subtle); color: var(--candle-up); }}
        .badge-warning {{ background: var(--warning-subtle); color: var(--warning); }}
        .badge-danger  {{ background: var(--candle-down-subtle); color: var(--candle-down); }}
        .badge-accent  {{ background: var(--accent-subtle); color: var(--accent); }}
        .badge-neutral {{ background: var(--bg-subtle); color: var(--text-muted); }}

        /* 结论横幅 */
        .verdict-banner {{
            background: var(--bg-card);
            border-left: 4px solid var(--accent);
            border: 1px solid var(--border-color);
            border-left-width: 5px;
            border-radius: var(--radius);
            padding: 16px 20px;
            box-shadow: var(--shadow-sm);
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .verdict-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .verdict-headline {{
            font-size: 18px;
            font-weight: 700;
            color: var(--text-main);
        }}
        .verdict-props {{
            display: flex;
            gap: 16px;
            font-size: 13px;
            color: var(--text-muted);
        }}

        /* 通用卡片容器 */
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius);
            padding: 20px;
            box-shadow: var(--shadow-sm);
        }}
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }}
        .card-header h3 {{
            font-size: 16px;
            font-weight: 700;
        }}

        /* SVG K线图表 */
        .candle-box {{
            width: 100%;
            overflow-x: auto;
        }}
        .candle-svg {{
            width: 100%;
            height: auto;
            display: block;
        }}

        /* 指标网格 */
        .grid-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
        }}
        .stat-card {{
            background: var(--bg-subtle);
            border-radius: 6px;
            padding: 14px 16px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        .stat-label {{
            font-size: 12px;
            color: var(--text-muted);
            font-weight: 600;
            display: flex;
            justify-content: space-between;
        }}
        .stat-val {{
            font-size: 20px;
            font-weight: 700;
            color: var(--text-main);
            font-variant-numeric: tabular-nums;
        }}
        .stat-sub {{
            font-size: 12px;
            color: var(--text-muted);
        }}

        /* 支撑阻力阶梯图 */
        .sr-ladder {{
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-top: 10px;
        }}
        .sr-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 14px;
            border-radius: 6px;
            font-size: 13px;
        }}
        .sr-res {{
            background: var(--candle-down-subtle);
            border-left: 3px solid var(--candle-down);
        }}
        .sr-sup {{
            background: var(--candle-up-subtle);
            border-left: 3px solid var(--candle-up);
        }}
        .sr-curr {{
            background: var(--accent-subtle);
            border: 2px dashed var(--accent);
            font-weight: 700;
            font-size: 14px;
        }}
        .sr-diff {{
            font-weight: 600;
            font-variant-numeric: tabular-nums;
        }}

        /* 三情景卡片 */
        .scenarios-bar {{
            height: 12px;
            border-radius: 6px;
            display: flex;
            overflow: hidden;
            margin: 10px 0 16px 0;
        }}
        .sc-seg {{ height: 100%; }}
        .sc-seg-base {{ background: var(--accent); }}
        .sc-seg-up   {{ background: var(--candle-up); }}
        .sc-seg-down {{ background: var(--candle-down); }}

        .scenarios-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 16px;
        }}
        .scenario-box {{
            background: var(--bg-subtle);
            border-radius: 6px;
            padding: 14px 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            font-size: 13px;
        }}
        .scenario-name {{
            font-weight: 700;
            font-size: 14px;
            display: flex;
            justify-content: space-between;
        }}

        /* 密集成交区 / 筹码分布面板（G4） */
        .vp-summary {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 14px;
        }}
        .vp-hist {{
            max-height: 360px;
            overflow-y: auto;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 8px 10px;
            background: var(--bg-subtle);
        }}
        .vp-row {{
            display: grid;
            grid-template-columns: 92px 1fr 110px;
            align-items: center;
            gap: 10px;
            padding: 1px 0;
        }}
        .vp-price {{ font-variant-numeric: tabular-nums; font-size: 12px; text-align: right; }}
        .vp-bar-wrap {{ height: 14px; background: transparent; }}
        .vp-bar {{ height: 100%; border-radius: 3px; background: var(--accent); min-width: 2px; }}
        .vp-bar.vp-poc {{ background: #7c3aed; }}
        .vp-bar.vp-hvn {{ background: var(--candle-up); }}
        .vp-bar.vp-lvn {{ background: var(--text-light); }}
        .vp-pct {{ font-size: 11px; color: var(--text-muted); font-variant-numeric: tabular-nums; }}
        .vp-tag {{
            margin-left: 6px;
            font-size: 10px;
            font-weight: 700;
            padding: 1px 5px;
            border-radius: 3px;
            background: var(--accent-subtle);
            color: var(--accent);
        }}

        /* 风险面板 */
        .risk-badge-large {{
            font-size: 14px;
            font-weight: 800;
            padding: 4px 12px;
            border-radius: 4px;
            text-transform: uppercase;
        }}
        .risk-low {{ background: var(--candle-up-subtle); color: var(--candle-up); }}
        .risk-mid {{ background: var(--warning-subtle); color: var(--warning); }}
        .risk-high {{ background: var(--candle-down-subtle); color: var(--candle-down); }}
        .risk-extreme {{ background: #dc2626; color: #ffffff; }}

        .risk-list {{
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-top: 12px;
            font-size: 13px;
        }}
        .risk-list li {{
            padding-left: 20px;
            position: relative;
        }}
        .risk-list li::before {{
            content: "•";
            position: absolute;
            left: 6px;
            color: var(--candle-down);
            font-weight: bold;
        }}

        /* 表格体系 */
        .table-responsive {{
            overflow-x: auto;
            margin-top: 10px;
        }}
        .data-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }}
        .data-table th, .data-table td {{
            padding: 10px 12px;
            border-bottom: 1px solid var(--border-color);
        }}
        .data-table th {{
            background: var(--bg-subtle);
            color: var(--text-muted);
            font-weight: 600;
        }}
        .corr-table td {{
            font-variant-numeric: tabular-nums;
            text-align: center;
        }}
        .corr-table th {{
            text-align: center;
        }}

        .alert-box {{
            padding: 12px 16px;
            border-radius: 6px;
            font-size: 13px;
            margin-bottom: 16px;
        }}
        .alert-danger {{
            background: var(--candle-down-subtle);
            color: var(--candle-down);
            border: 1px solid var(--candle-down);
        }}

        /* 占位空状态 */
        .empty-card {{
            padding: 24px;
            text-align: center;
            color: var(--text-muted);
            font-size: 13px;
            background: var(--bg-subtle);
            border-radius: 6px;
        }}

        /* 页脚免责 */
        .footer-card {{
            background: var(--bg-subtle);
            border-radius: var(--radius);
            padding: 16px 20px;
            font-size: 12px;
            color: var(--text-muted);
            line-height: 1.6;
        }}
        .footnote {{
            font-size: 11px;
            color: var(--text-light);
            margin-top: 8px;
        }}
        .weight-tag {{
            font-weight: 700;
            color: var(--accent);
        }}
    </style>
</head>
<body>
<div class="container">

    <!-- 1. 标题与行情状态栏 -->
    <header class="header-bar">
        <div class="header-left">
            <h1 class="symbol-title">{safe_escape(code)}</h1>
            <span class="symbol-interval">{safe_escape(interval)}</span>
            <span class="badge {cred_badge_cls}">数据可信度: {safe_escape(credibility)}</span>
        </div>
        <div class="header-meta">
            <div>最新价: <span class="price-badge">{fmt_p(price)}</span> <small>USDT</small></div>
            <div>数据源: <strong>{safe_escape(source)}</strong></div>
            <div>截至: <strong>{safe_escape(as_of)}</strong></div>
        </div>
    </header>

    <!-- 2. 一句话研判横幅 -->
    <section class="verdict-banner">
        <div class="verdict-header">
            <span class="verdict-headline">{safe_escape(verdict.get("headline", "—"))}</span>
            <span class="badge badge-accent">评级: {safe_escape(verdict.get("rating", "—"))}</span>
        </div>
        <div class="verdict-props">
            <div>建议仓位: <strong>{safe_escape(verdict.get("position_advice", "—"))}</strong></div>
            <div>趋势判定: <strong>{safe_escape(trend.get("direction", "—"))} ({safe_escape(trend.get("strength", "—"))})</strong></div>
            <div>阶段: <strong>{safe_escape(trend.get("stage", "—"))}</strong></div>
            <div>置信度: <strong>{safe_escape(trend.get("confidence", "—"))}</strong></div>
        </div>
    </section>

    <!-- 3. K线蜡烛图 -->
    <section class="card">
        <div class="card-header">
            <h3>K 线走势与均线系统 (最近 ≤120 根已收盘 Bar)</h3>
            <span class="badge badge-neutral">已剔除未收盘 Bar: {dq.get("dropped_open_bar", 0)}</span>
        </div>
        <div class="candle-box">
            {candles_html}
        </div>
    </section>

    <!-- 4. 技术指标明细卡片 -->
    <section class="card">
        <div class="card-header">
            <h3>核心技术指标矩阵</h3>
            <span class="badge badge-neutral">MA 排列: {safe_escape(ma_align)}</span>
        </div>
        <div class="grid-cards">
            <!-- 均线卡片 -->
            <div class="stat-card">
                <div class="stat-label"><span>MA 系统</span><span>{safe_escape(ma_align)}</span></div>
                <div class="stat-val">{fmt_p(ma20)}</div>
                <div class="stat-sub">MA5: {fmt_p(ma5)} | MA10: {fmt_p(ma10)}<br>MA20: {fmt_p(ma20)} | MA60: {fmt_p(ma60)}</div>
            </div>
            <!-- MACD 卡片 -->
            <div class="stat-card">
                <div class="stat-label"><span>MACD 动能</span><span>{safe_escape(macd_state)}</span></div>
                <div class="stat-val">{fmt_p(hist)}</div>
                <div class="stat-sub">DIF: {fmt_p(dif)} | DEA: {fmt_p(dea)}</div>
                {render_macd_svg(hist, macd_last6)}
            </div>
            <!-- RSI 卡片 -->
            <div class="stat-card">
                <div class="stat-label"><span>RSI14 强弱</span><span>{safe_escape(rsi_state)}</span></div>
                <div class="stat-val">{fmt_p(rsi14, decimals=1)}</div>
                <div class="stat-sub">RSI6: {fmt_p(rsi6, decimals=1)}</div>
                {render_rsi_svg(rsi14)}
            </div>
            <!-- 量价卡片 -->
            <div class="stat-card">
                <div class="stat-label"><span>量价配合</span><span>{safe_escape(vol_trend)}</span></div>
                <div class="stat-val">{fmt_p(vol_ratio, decimals=2)} <small style="font-size:12px;font-weight:normal;">量比</small></div>
                <div class="stat-sub">5日均量对比 (剔除末根自身)</div>
            </div>
            <!-- 波动画像卡片 -->
            <div class="stat-card">
                <div class="stat-label"><span>波动画像</span><span>ATR14: {fmt_p(atr14)}</span></div>
                <div class="stat-val">{fmt_p(atr_pct, decimals=2)}% <small style="font-size:12px;font-weight:normal;">ATR占现价</small></div>
                <div class="stat-sub">近30日最大回撤: {fmt_p(mdd30, decimals=2)}%</div>
            </div>
        </div>
    </section>

    <!-- 5. 支撑与阻力阶梯图 -->
    <section class="card">
        <div class="card-header">
            <h3>关键位阶梯分布 (自适应心理关口与摆动极值)</h3>
            <span class="badge badge-neutral">现价: {fmt_p(price)}</span>
        </div>
        <div class="sr-ladder">
            <!-- 阻力位 -->
            {"".join([f'''
            <div class="sr-item sr-res">
                <div><strong>阻力 R{i+1}:</strong> {fmt_p(r.get("price"))} <span class="badge badge-danger">{r.get("strength","中")}</span> ({safe_escape(r.get("basis",""))})</div>
                <div class="sr-diff" style="color:var(--candle-down)">{pct_diff(price, r.get("price"))}</div>
            </div>
            ''' for i, r in enumerate(reversed(res_list))]) if res_list else '<div class="empty-card">暂无阻力位数据</div>'}

            <!-- 现价水平线 -->
            <div class="sr-item sr-curr">
                <div>★ 当前基准价格 (Current Price): {fmt_p(price)} USDT</div>
                <div>0.00%</div>
            </div>

            <!-- 支撑位 -->
            {"".join([f'''
            <div class="sr-item sr-sup">
                <div><strong>支撑 S{i+1}:</strong> {fmt_p(s.get("price"))} <span class="badge badge-success">{s.get("strength","中")}</span> ({safe_escape(s.get("basis",""))})</div>
                <div class="sr-diff" style="color:var(--candle-up)">{pct_diff(price, s.get("price"))}</div>
            </div>
            ''' for i, s in enumerate(sup_list)]) if sup_list else '<div class="empty-card">暂无支撑位数据</div>'}
        </div>
    </section>

    {vp_html}

    <!-- 6. 三情景概率与推演 -->
    <section class="card">
        <div class="card-header">
            <h3>7~30 日三情景走势预判</h3>
            <span class="badge badge-accent">证伪点: {safe_escape(trend.get("falsification", "无明确证伪点"))}</span>
        </div>
        <!-- 概率分割进度条 -->
        <div class="scenarios-bar">
            {"".join([f'<div class="sc-seg sc-seg-{"base" if sc.get("name")=="基准" else ("up" if sc.get("name")=="乐观" else "down")}" style="width: {max(5, (sc.get("probability") or 0.33)*100)}%;"></div>' for sc in scenarios])}
        </div>
        <div class="scenarios-grid">
            {"".join([f'''
            <div class="scenario-box">
                <div class="scenario-name">
                    <span>{safe_escape(sc.get("name","情景"))}</span>
                    <span class="badge {("badge-accent" if sc.get("name")=="基准" else ("badge-success" if sc.get("name")=="乐观" else "badge-danger"))}">
                        {int(round((sc.get("probability") or 0) * 100))}% 概率
                    </span>
                </div>
                <div>目标区间: <strong>{fmt_p(sc.get("range_low"))} ~ {fmt_p(sc.get("range_high"))}</strong></div>
                <div style="color:var(--text-muted);">触发条件: {safe_escape(sc.get("trigger","—"))}</div>
            </div>
            ''' for sc in scenarios]) if scenarios else '<div class="empty-card">暂无情景预判数据</div>'}
        </div>
    </section>

    <!-- 7. 风险评估与预警清单 -->
    <section class="card">
        <div class="card-header">
            <h3>风险定级与异常监测</h3>
            <span class="risk-badge-large {risk_badge_cls}">风险等级: {safe_escape(rlevel)}</span>
        </div>
        <div><strong>评定依据：</strong>{safe_escape(risk.get("rationale", "—"))}</div>
        <ul class="risk-list">
            {"".join([f"<li>{safe_escape(w)}</li>" for w in warnings_list]) if warnings_list else "<li>暂无特定风险告警</li>"}
        </ul>
        {f'<div style="margin-top:12px;font-size:12px;color:var(--text-muted);"><strong>极端历史波动事件：</strong>{safe_escape(", ".join([str(e) for e in extreme_events]))}</div>' if extreme_events else ''}
        {f'<div style="margin-top:4px;font-size:12px;color:var(--warning);"><strong>数据缺口记录：</strong>{safe_escape(str(data_gaps))}</div>' if data_gaps else ''}
    </section>

    <!-- 8. 组合风险画像（可选） -->
    {portfolio_html}

    <!-- 9. 页脚免责声明 -->
    <footer class="footer-card">
        <strong>免责声明：</strong>{safe_escape(disclaimer)}<br>
        <div class="footnote">生成时间：{safe_escape(report_data.get("generated_at", "—"))} ｜ 数据口径：全部指标严控基于已收盘 Bar ｜ K析研判团 DSH 插件 ｜ 本报告不构成任何投资建议</div>
    </footer>

</div>
</body>
</html>
'''
    return html_content, sections_rendered


def main():
    parser = argparse.ArgumentParser(
        description="研判报告可视化渲染器：单文件离线可视化看板，内联 SVG 手绘图表，零第三方依赖",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, help="输入的 report.json 路径")
    parser.add_argument("--indicators", default=None, help="可选：补充 indicators.json 路径")
    parser.add_argument("--klines", default=None, help="可选：补充 K 线原始数据路径 (如 btc_1d.json)")
    parser.add_argument("--portfolio", default=None, help="可选：补充投资组合量化分析结果 portfolio.json")
    parser.add_argument("--out", default=None, help="输出的 HTML 文件路径（默认 <数据目录>/kexi_out/report.html）")
    parser.add_argument("--title", default=None, help="自定义看板标题")
    parser.add_argument("--no-candles", action="store_true", help="禁用 K 线蜡烛图绘制")
    args = parser.parse_args()

    # 1. 读取 report.json
    if not os.path.exists(args.input):
        print(json.dumps({"ok": False, "error": f"输入报告不存在: {args.input}"}, ensure_ascii=False))
        sys.exit(1)

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            report_data = json.load(f)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"读取或解析 report.json 失败: {str(e)}"}, ensure_ascii=False))
        sys.exit(1)

    # 2. 读取可选数据
    warnings = []
    ind_data = None
    if args.indicators:
        if os.path.exists(args.indicators):
            try:
                with open(args.indicators, "r", encoding="utf-8") as f:
                    ind_data = json.load(f)
                # 合并/补充指标数据
                if "indicators" not in report_data and isinstance(ind_data, dict):
                    report_data["indicators"] = ind_data
            except Exception as e:
                warnings.append(f"读取 indicators.json 失败: {e}")
        else:
            warnings.append(f"指定的 indicators 文件不存在: {args.indicators}")

    klines_data = None
    if args.klines:
        if os.path.exists(args.klines):
            try:
                with open(args.klines, "r", encoding="utf-8") as f:
                    klines_data = json.load(f)
            except Exception as e:
                warnings.append(f"读取 klines 文件失败: {e}")
        else:
            warnings.append(f"指定的 klines 文件不存在: {args.klines}")

    portfolio_data = None
    if args.portfolio:
        if os.path.exists(args.portfolio):
            try:
                with open(args.portfolio, "r", encoding="utf-8") as f:
                    portfolio_data = json.load(f)
            except Exception as e:
                warnings.append(f"读取 portfolio.json 失败: {e}")
        else:
            warnings.append(f"指定的 portfolio 文件不存在: {args.portfolio}")

    # 3. 确定输出路径（统一约定为 <数据目录>/kexi_out/）
    out_path = args.out
    if not out_path:
        base_dir = os.path.dirname(os.path.abspath(args.input)) or "."
        out_path = os.path.join(base_dir, "kexi_out", "report.html")

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)

    # 4. 执行渲染
    try:
        html_text, sections_rendered = render_dashboard_html(
            report_data=report_data,
            klines_data=klines_data,
            portfolio_data=portfolio_data,
            custom_title=args.title,
            show_candles=(not args.no_candles),
        )
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"渲染 HTML 异常: {str(e)}"}, ensure_ascii=False))
        sys.exit(1)

    # 5. 写入输出文件
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html_text)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"写入输出文件失败: {str(e)}"}, ensure_ascii=False))
        sys.exit(1)

    byte_size = os.path.getsize(out_path)

    # 6. 输出紧凑 JSON
    status_out = {
        "ok": True,
        "out": out_path,
        "bytes": byte_size,
        "sections_rendered": sections_rendered,
        "warnings": warnings,
    }
    print(json.dumps(status_out, ensure_ascii=False))


if __name__ == "__main__":
    main()
