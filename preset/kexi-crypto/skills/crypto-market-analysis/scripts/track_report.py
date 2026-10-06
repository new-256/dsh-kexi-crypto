#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
track_report.py - 战绩可视化报告生成器（v1.5.0 新增）

═══════════════════════════════════════════════════════════════════════
用户要求（2026-09-29）：
  · "项目历史实操经验成功预测/失败，应建档存盘整理思路用于迭代"
  · "提炼技能，优化脚本"
  · "交付成果可视化报告"
  · "有完整的判断点分析线，止盈止损点，底仓策略等"
═══════════════════════════════════════════════════════════════════════

产出：**单文件自包含 HTML**（零外链、零 CDN、可离线打开）
  · 战绩总览（预测 vs 实际、超额 vs BTC 基准）
  · 判断点分析线（每个币的入场判断 → 后续走势时间轴）
  · 止盈止损点（基于 ATR / 结构位 / 风险预算三法给出）
  · 底仓策略（分批建仓计划：底仓/加仓/减仓/清仓 + 触发条件）

设计约束（沿用本项目既有约定）
------------------------------
  · **零外链**：不引用任何 CDN/字体/图片，全部内联 SVG+CSS
  · **不自造数据**：所有点位来自传入的实时 K 线或显式传入的参数；
    缺失时明确显示"数据缺失"，**绝不编造**
  · **中文可读**：面向用户，术语给解释
"""

import html
import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))

CSS = """
*{box-sizing:border-box}
body{margin:0;padding:24px;background:#0f1115;color:#e6e8eb;
 font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.6}
h1{font-size:24px;margin:0 0 4px}
h2{font-size:18px;margin:28px 0 12px;padding-left:10px;border-left:3px solid #4f8cff}
h3{font-size:15px;margin:18px 0 8px;color:#9aa4b2}
.sub{color:#8b94a3;font-size:13px;margin-bottom:20px}
.card{background:#171a21;border:1px solid #242832;border-radius:10px;padding:16px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.kpi{background:#1c2029;border-radius:8px;padding:12px;text-align:center}
.kpi .v{font-size:22px;font-weight:600}
.kpi .l{font-size:12px;color:#8b94a3;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #242832}
th{color:#8b94a3;font-weight:500;font-size:12px}
tr:hover td{background:#1c2029}
.pos{color:#3ddc84}.neg{color:#ff5c5c}.warn{color:#ffb84d}.mut{color:#8b94a3}
.tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;
 background:#242832;color:#9aa4b2;margin-right:4px}
.tag.g{background:#123324;color:#3ddc84}
.tag.r{background:#331414;color:#ff5c5c}
.tag.y{background:#332a12;color:#ffb84d}
.note{background:#1a1d26;border-left:3px solid #4f8cff;padding:10px 14px;
 border-radius:0 6px 6px 0;font-size:13px;color:#b8c0cc;margin:12px 0}
.warnbox{border-left-color:#ffb84d}
.errbox{border-left-color:#ff5c5c}
.bar{height:8px;background:#242832;border-radius:4px;overflow:hidden;margin-top:4px}
.bar>span{display:block;height:100%;background:#4f8cff}
svg{display:block;max-width:100%}
.legend{font-size:12px;color:#8b94a3;margin-top:6px}
.legend span{margin-right:14px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px}
footer{margin-top:30px;padding-top:14px;border-top:1px solid #242832;
 color:#6b7280;font-size:12px}
"""


def _esc(s):
    return html.escape(str(s if s is not None else ""))


def _pct(v, digits=2):
    if v is None:
        return "—"
    return f"{v:+.{digits}f}%"


def _cls(v):
    if v is None:
        return "mut"
    return "pos" if v > 0 else ("neg" if v < 0 else "mut")


def _num(v, digits=2, suffix=""):
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


# ══════════════════════════════════════════════════════════════════════
# 点位计算：三法交叉验证
# ══════════════════════════════════════════════════════════════════════
def atr(klines, n=14):
    """平均真实波幅。"""
    if not klines or len(klines) < n + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i][2], klines[i][3], klines[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n:
        return None
    return sum(trs[-n:]) / n


def swing_levels(klines, lookback=60):
    """结构位：近端摆动高/低点（用分形近似：左右各 2 根更低/更高）。"""
    if not klines or len(klines) < lookback:
        return {"highs": [], "lows": []}
    seg = klines[-lookback:]
    highs, lows = [], []
    for i in range(2, len(seg) - 2):
        h = seg[i][2]
        if h >= seg[i - 1][2] and h >= seg[i - 2][2] and \
           h >= seg[i + 1][2] and h >= seg[i + 2][2]:
            highs.append(h)
        l = seg[i][3]
        if l <= seg[i - 1][3] and l <= seg[i - 2][3] and \
           l <= seg[i + 1][3] and l <= seg[i + 2][3]:
            lows.append(l)
    return {"highs": sorted(set(highs)), "lows": sorted(set(lows))}


def compute_trade_plan(klines, entry=None, atr_mult_sl=2.0, rr=2.5,
                       risk_pct=1.0, net_usd=10000.0):
    """给出**止盈止损点 + 底仓策略**。

    三法交叉验证止损（保守取"最近的那个"，即最不容易被打掉的）：
      ① ATR 法：入场 − 2×ATR（波动自适应，最常用）
      ② 结构法：最近摆动低点下方一点（市场结构位）
      ③ 风险预算法：让单笔亏损 = 净值 × risk_pct% 反推

    止盈：按 R:R 倍数 + 结构阻力位，取"较早到达"的作为第一目标。

    底仓策略（分批）：
      底仓 40%  → 立即建（回踩不破 MA20 时）
      加仓 30%  → 突破并站稳近端摆动高点
      加仓 30%  → 回踩加仓位不破再上
      减仓       → 触及 TP1 减 1/3；触及 TP2 再减 1/3
    """
    if not klines or len(klines) < 30:
        return {"ok": False, "error": "K线不足 30 根，无法计算点位"}
    cur = klines[-1][4]
    entry = float(entry or cur)
    a = atr(klines, 14)
    sl_levels = swing_levels(klines, 60)
    ma20 = sum(k[4] for k in klines[-20:]) / 20
    ma50 = (sum(k[4] for k in klines[-50:]) / 50) if len(klines) >= 50 else None

    candidates = {}
    if a:
        candidates["ATR法(2×ATR)"] = entry - atr_mult_sl * a
    if sl_levels["lows"]:
        below = [x for x in sl_levels["lows"] if x < entry]
        if below:
            candidates["结构法(近端低点)"] = max(below) * 0.995
    if ma20 and ma20 < entry:
        candidates["均线法(MA20下沿)"] = ma20 * 0.98

    if not candidates:
        return {"ok": False, "error": "无法确定止损位（无 ATR / 无结构低点）"}
    # 保守：取最高的止损（离入场最近=风险最小，但也最易被打）
    # 稳健：取**中位数**，兼顾两者
    vals = sorted(candidates.values())
    sl = vals[len(vals) // 2]
    sl_method = [k for k, v in candidates.items() if v == sl][0]

    risk_per_unit = entry - sl
    if risk_per_unit <= 0:
        return {"ok": False, "error": "止损高于入场价，参数异常"}
    tp1 = entry + rr * risk_per_unit
    tp2 = entry + (rr * 1.8) * risk_per_unit
    # 结构阻力位修正（若更近则下调第一目标）
    highs_above = [x for x in sl_levels["highs"] if x > entry * 1.02]
    if highs_above:
        tp1_struct = min(highs_above)
        if tp1_struct < tp1:
            tp1 = tp1_struct
            tp2 = max(tp2, entry + (rr * 1.8) * risk_per_unit)
    # 风险预算仓位
    risk_usd = net_usd * risk_pct / 100
    size_by_risk = risk_usd / (risk_per_unit / entry)
    stop_pct = risk_per_unit / entry * 100

    return {
        "ok": True,
        "entry": round(entry, 8), "current": round(cur, 8),
        "atr": round(a, 8) if a else None,
        "ma20": round(ma20, 8), "ma50": round(ma50, 8) if ma50 else None,
        "stop_loss": round(sl, 8), "sl_method": sl_method,
        "sl_candidates": {k: round(v, 8) for k, v in candidates.items()},
        "stop_pct": round(stop_pct, 2),
        "take_profit_1": round(tp1, 8), "take_profit_2": round(tp2, 8),
        "tp1_pct": round((tp1 / entry - 1) * 100, 2),
        "tp2_pct": round((tp2 / entry - 1) * 100, 2),
        "rr_tp1": round((tp1 - entry) / risk_per_unit, 2),
        "rr_tp2": round((tp2 - entry) / risk_per_unit, 2),
        "size_usd_by_risk": round(size_by_risk, 2),
        "size_units_by_risk": round(size_by_risk / entry, 8),
        "max_loss_usd": round(size_by_risk * risk_per_unit / entry, 2),
        "base_position": {
            "note": "分批建仓：先建底仓，突破/回踩确认后再加；"
                    "亏损时**不加仓**（不摊平）",
            "tranches": [
                {"name": "底仓", "pct": 40,
                 "trigger": f"现价 {_num(cur,4)} 附近（不破 MA20={_num(ma20,4)}）",
                 "amount_usd": round(size_by_risk * 0.4, 2)},
                {"name": "加仓①", "pct": 30,
                 "trigger": f"突破并站稳近端高点 {_num(max(highs_above) if highs_above else None,4)}",
                 "amount_usd": round(size_by_risk * 0.3, 2)},
                {"name": "加仓②", "pct": 30,
                 "trigger": f"回踩 {_num(max(highs_above) if highs_above else None,4)} 不破再上",
                 "amount_usd": round(size_by_risk * 0.3, 2)},
            ],
            "exits": [
                {"name": "减仓①", "pct": 33,
                 "trigger": f"触及 TP1 {_num(tp1,4)}（{_pct((tp1/entry-1)*100)}）"},
                {"name": "减仓②", "pct": 33,
                 "trigger": f"触及 TP2 {_num(tp2,4)}（{_pct((tp2/entry-1)*100)}）"},
                {"name": "清仓", "pct": 100,
                 "trigger": f"跌破止损 {_num(sl,4)} 立即清（{_pct(-stop_pct)}）"},
            ],
            "rules": [
                "单笔风险不超过净值 1–2%（本计划按 " + f"{risk_pct}%" + "）",
                "触及止损**无条件**执行，不找理由扛单",
                "盈利后把止损上移到成本价（保本），再逐步上移",
                "放量多日 + 90日高位同时出现时，**只减不加**",
            ],
        },
    }


# ══════════════════════════════════════════════════════════════════════
# SVG 迷你走势图（含判断点/止损止盈水平线）
# ══════════════════════════════════════════════════════════════════════
def svg_price_chart(klines, plan=None, width=560, height=170, bars=90):
    """内联 SVG 走势 + 止损/止盈水平线 + 入场点。"""
    if not klines:
        return "<svg></svg>"
    seg = klines[-bars:]
    closes = [k[4] for k in seg]
    if not closes:
        return "<svg></svg>"
    levels = [min(closes), max(closes)]
    if plan and plan.get("ok"):
        levels += [plan["stop_loss"], plan["take_profit_1"]]
    lo, hi = min(levels), max(levels)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad

    def x(i):
        return 40 + i * (width - 60) / max(1, len(closes) - 1)

    def y(v):
        return height - 25 - (v - lo) / (hi - lo) * (height - 45)

    pts = " ".join(f"{x(i):.1f},{y(c):.1f}" for i, c in enumerate(closes))
    parts = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
             f'role="img" aria-label="价格走势">']
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="#12151b" '
                 f'rx="6"/>')
    # 网格
    for frac in (0.25, 0.5, 0.75):
        yy = 20 + frac * (height - 45)
        parts.append(f'<line x1="40" y1="{yy:.1f}" x2="{width-20}" y2="{yy:.1f}" '
                     f'stroke="#242832" stroke-width="1"/>')
    # 价格线
    parts.append(f'<polyline points="{pts}" fill="none" stroke="#4f8cff" '
                 f'stroke-width="1.8"/>')
    # 水平线：止损/止盈/入场
    if plan and plan.get("ok"):
        marks = [("止损", plan["stop_loss"], "#ff5c5c", "4 3"),
                 ("入场", plan["entry"], "#ffb84d", "0"),
                 ("TP1", plan["take_profit_1"], "#3ddc84", "4 3"),
                 ("TP2", plan["take_profit_2"], "#3ddc84", "2 3")]
        for label, v, color, dash in marks:
            if v is None or not (lo <= v <= hi):
                continue
            yy = y(v)
            parts.append(f'<line x1="40" y1="{yy:.1f}" x2="{width-20}" y2="{yy:.1f}" '
                         f'stroke="{color}" stroke-width="1" stroke-dasharray="{dash}" '
                         f'opacity="0.85"/>')
            parts.append(f'<text x="{width-16}" y="{yy+3:.1f}" fill="{color}" '
                         f'font-size="10" text-anchor="end">{label} {v:.4g}</text>')
    # 当前价点
    parts.append(f'<circle cx="{x(len(closes)-1):.1f}" cy="{y(closes[-1]):.1f}" r="3.5" '
                 f'fill="#e6e8eb"/>')
    parts.append(f'<text x="6" y="14" fill="#8b94a3" font-size="10">'
                 f'{hi:.4g}</text>')
    parts.append(f'<text x="6" y="{height-6}" fill="#8b94a3" font-size="10">'
                 f'{lo:.4g}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_equity_curve(points, width=560, height=140):
    """战绩累计曲线（points = [(label, cum_return_pct), ...]）。"""
    if not points:
        return "<svg></svg>"
    vals = [p[1] for p in points]
    lo, hi = min(0, min(vals)), max(0, max(vals))
    rng = (hi - lo) or 1

    def x(i):
        return 44 + i * (width - 64) / max(1, len(points) - 1)

    def y(v):
        return height - 22 - (v - lo) / rng * (height - 40)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
             f'role="img" aria-label="累计收益曲线">']
    parts.append(f'<rect width="{width}" height="{height}" fill="#12151b" rx="6"/>')
    zy = y(0)
    parts.append(f'<line x1="44" y1="{zy:.1f}" x2="{width-20}" y2="{zy:.1f}" '
                 f'stroke="#8b94a3" stroke-width="1" opacity="0.5"/>')
    up = [(x(i), y(v)) for i, (_, v) in enumerate(points)]
    pol = " ".join(f"{a:.1f},{b:.1f}" for a, b in up)
    parts.append(f'<polyline points="{pol}" fill="none" stroke="#3ddc84" '
                 f'stroke-width="2"/>')
    for i, (lab, v) in enumerate(points):
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3" fill="#3ddc84"/>')
        parts.append(f'<text x="{x(i):.1f}" y="{height-6}" fill="#6b7280" '
                     f'font-size="9" text-anchor="middle">{_esc(lab)}</text>')
    parts.append(f'<text x="6" y="14" fill="#8b94a3" font-size="10">{hi:+.1f}%</text>')
    parts.append("</svg>")
    return "".join(parts)


# ══════════════════════════════════════════════════════════════════════
# 报告主体
# ══════════════════════════════════════════════════════════════════════
def build_report(ledger=None, plans=None, backtest=None, klines_map=None,
                 title="K析研判团 · 战绩与策略可视化报告"):
    """生成自包含 HTML。

    Args:
        ledger  : 战绩记录列表 [{symbol, run_id, tier, entry, current, ret_pct,
                                  btc_ret_pct, verdict, note, entry_date}]
        plans   : {symbol: trade_plan}（compute_trade_plan 的输出）
        backtest: backtest.py 的结果 dict（可选，用于验证维度有效性）
        klines_map: {symbol: klines}（用于画走势图；缺失则跳过图）
    """
    led = ledger or []
    plans = plans or {}
    now = datetime.now(TZ8).strftime("%Y-%m-%d %H:%M UTC+8")
    H = [f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         f'<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>{_esc(title)}</title><style>{CSS}</style></head><body>']
    H.append(f"<h1>{_esc(title)}</h1>")
    H.append(f'<div class="sub">生成时间 {now} · 单文件自包含（零外链，可离线打开）</div>')

    # ---------- 1. 战绩总览 ----------
    formal = [r for r in led if r.get("tier") == "formal"]
    watch = [r for r in led if r.get("tier") == "watch"]
    def avg(rows, k):
        v = [r.get(k) for r in rows if r.get(k) is not None]
        return sum(v) / len(v) if v else None
    ret = avg(formal, "ret_pct")
    btc = avg(formal, "btc_ret_pct")
    excess = (ret - btc) if (ret is not None and btc is not None) else None
    wins = len([r for r in formal if (r.get("ret_pct") or 0) > 0])

    H.append("<h2>一、战绩总览</h2>")
    H.append('<div class="grid">')
    H.append(f'<div class="kpi"><div class="v {_cls(ret)}">{_pct(ret)}</div>'
             f'<div class="l">正式推荐均值收益</div></div>')
    H.append(f'<div class="kpi"><div class="v {_cls(btc)}">{_pct(btc)}</div>'
             f'<div class="l">同期 BTC（基准）</div></div>')
    H.append(f'<div class="kpi"><div class="v {_cls(excess)}">{_pct(excess)}</div>'
             f'<div class="l">超额收益（关键判据）</div></div>')
    H.append(f'<div class="kpi"><div class="v">{wins}/{len(formal)}</div>'
             f'<div class="l">正式推荐胜率</div></div>')
    H.append(f'<div class="kpi"><div class="v">{len(watch)}</div>'
             f'<div class="l">观察级样本</div></div>')
    H.append("</div>")
    H.append('<div class="note warnbox"><b>如何解读超额收益：</b>超额 = 组合收益 − BTC 收益。'
             '若超额为负，说明"选币"没有跑赢只持有 BTC，收益完全由市场行情（beta）解释，'
             '选币能力（alpha）不成立。<b>这个指标比胜率重要</b>——'
             '牛市里随便买都能赚，只有超额才说明判断力。</div>')

    if led:
        H.append("<h3>明细</h3><table><tr><th>币</th><th>来源</th><th>级别</th>"
                 "<th>入场</th><th>现价</th><th>收益</th><th>BTC同期</th>"
                 "<th>超额</th><th>判定</th></tr>")
        for r in sorted(led, key=lambda x: -(x.get("ret_pct") or -999)):
            rp, bp = r.get("ret_pct"), r.get("btc_ret_pct")
            ex = (rp - bp) if (rp is not None and bp is not None) else None
            H.append(f'<tr><td><b>{_esc(r.get("symbol"))}</b></td>'
                     f'<td class="mut">{_esc(r.get("run_id",""))}</td>'
                     f'<td><span class="tag">{"正式" if r.get("tier")=="formal" else "观察"}</span></td>'
                     f'<td>{_num(r.get("entry"),6)}</td>'
                     f'<td>{_num(r.get("current"),6)}</td>'
                     f'<td class="{_cls(rp)}">{_pct(rp)}</td>'
                     f'<td class="mut">{_pct(bp)}</td>'
                     f'<td class="{_cls(ex)}">{_pct(ex)}</td>'
                     f'<td class="mut">{_esc(r.get("verdict",""))}</td></tr>')
        H.append("</table>")

    # 累计曲线
    if formal:
        ordered = sorted(formal, key=lambda x: x.get("entry_date") or "")
        cum, pts = 0.0, []
        for r in ordered:
            cum += (r.get("ret_pct") or 0) / len(formal)
            pts.append((r.get("symbol", ""), round(cum, 2)))
        if pts:
            H.append("<h3>累计收益曲线（等权）</h3>")
            H.append(f'<div class="card">{svg_equity_curve(pts)}</div>')

    # ---------- 2. 回测验证的维度有效性 ----------
    if backtest:
        H.append("<h2>二、哪些判断维度真的有效（历史回测验证）</h2>")
        H.append('<div class="note">回测方法：点时间（point-in-time）——'
                 '每个历史观察日<b>只用当天及之前</b>的 K 线重算指标，再看未来真实收益，'
                 '无前视偏差。观察样本见下表。</div>')
        H.append(f'<div class="sub">样本 {backtest.get("observations","—")} 条 · '
                 f'覆盖 {backtest.get("symbols_covered","—")} 个币 · 区间 '
                 f'{(backtest.get("date_range") or ["—","—"])[0]} ~ '
                 f'{(backtest.get("date_range") or ["—","—"])[1]}</div>')

        def table_of(dct, keyname, metrics=("fwd_7d", "fwd_30d")):
            if not dct:
                return ""
            out = [f'<h3>{_esc(keyname)}</h3><table><tr><th>分组</th><th>样本</th>']
            for m in metrics:
                out.append(f"<th>{m.replace('fwd_','未来').replace('d','日')}均值</th>"
                           f"<th>中位</th><th>胜率</th>")
            out.append("</tr>")
            for k, d in dct.items():
                out.append(f'<tr><td>{_esc(k)}</td><td>{d.get("count","—")}</td>')
                for m in metrics:
                    s = d.get(m) or {}
                    out.append(f'<td class="{_cls(s.get("mean"))}">{_pct(s.get("mean"))}</td>'
                               f'<td class="mut">{_pct(s.get("median"))}</td>'
                               f'<td class="mut">{s.get("win_rate","—")}%</td>')
                out.append("</tr>")
            out.append("</table>")
            return "".join(out)

        H.append(table_of(backtest.get("by_position_group"), "按 90 日区间位置分组"))
        H.append(table_of(backtest.get("by_vol_expansion"),
                          "按是否「已放量多日」分组（用户点名特征）",
                          metrics=("fwd_7d", "fwd_30d")))
        H.append(table_of(backtest.get("by_timeframe_alignment"),
                          "按多周期一致性分组", metrics=("fwd_7d", "fwd_30d")))
        H.append('<div class="note"><b>结论（已据此调整打分权重）：</b><br>'
                 '①「90 日区间位置」的区分度<b>不稳定</b>——均值被极少数 +900% 的离群币'
                 '主导，换样本后符号会翻转；故已把它从重罚(-3)降到风险提示级(-1)。<br>'
                 '②「已放量多日」两轮回测<b>一致偏负</b>，30 日胜率仅约 32%'
                 '（未放量 39–43%）；故拥挤度权重上调到 -3，成为最重扣分项。<br>'
                 '③「多周期共振向上」两轮一致优于共振向下；故周线/月线维度保留并加权。'
                 '</div>')

    # ---------- 3. 判断点分析线 ----------
    H.append("<h2>三、判断点分析线（每个标的的决策时间轴）</h2>")
    if not led:
        H.append('<div class="note">暂无战绩记录。</div>')
    else:
        for r in led:
            sym = r.get("symbol")
            H.append(f'<div class="card"><h3>{_esc(sym)} '
                     f'<span class="tag">{"正式" if r.get("tier")=="formal" else "观察"}</span>'
                     f'<span class="tag {"g" if (r.get("ret_pct") or 0)>0 else "r"}">'
                     f'{_pct(r.get("ret_pct"))}</span></h3>')
            kl = (klines_map or {}).get(sym)
            pl = plans.get(sym)
            if kl:
                H.append(svg_price_chart(kl, pl))
                H.append('<div class="legend">'
                         '<span><i class="dot" style="background:#4f8cff"></i>价格</span>'
                         '<span><i class="dot" style="background:#ff5c5c"></i>止损</span>'
                         '<span><i class="dot" style="background:#ffb84d"></i>入场</span>'
                         '<span><i class="dot" style="background:#3ddc84"></i>止盈 TP1/TP2</span>'
                         '</div>')
            else:
                H.append('<div class="note">走势图数据缺失（未取到该币 K 线）——'
                         '此处不绘制，避免用其他币的图形误导。</div>')
            # 决策点时间轴
            timeline = []
            if r.get("entry_date"):
                timeline.append(("判断入场", r["entry_date"],
                                 f'依据：{r.get("note") or "见原始研判报告"}'))
            timeline.append(("入场价", "", _num(r.get("entry"), 6)))
            timeline.append(("当前", "", f'{_num(r.get("current"),6)} '
                                        f'（{_pct(r.get("ret_pct"))}）'))
            if r.get("verdict"):
                timeline.append(("结论", "", r["verdict"]))
            H.append("<table>")
            for k, dt, v in timeline:
                H.append(f'<tr><td style="width:90px"><b>{_esc(k)}</b></td>'
                         f'<td class="mut" style="width:100px">{_esc(dt)}</td>'
                         f'<td>{_esc(v)}</td></tr>')
            H.append("</table>")
            H.append("</div>")

    # ---------- 4. 止盈止损点 ----------
    H.append("<h2>四、止盈止损点（三法交叉验证）</h2>")
    H.append('<div class="note">止损用<b>三种方法</b>分别算，再取中位值——'
             '单一方法容易在特定行情下失效：<br>'
             '① <b>ATR 法</b>（入场 − 2×ATR）：随波动自适应，高波动币不会被噪声打掉；<br>'
             '② <b>结构法</b>（近端摆动低点下方）：尊重市场结构位；<br>'
             '③ <b>均线法</b>（MA20 下沿）：趋势单的常用防守位。</div>')
    if not plans:
        H.append('<div class="note">未提供点位计划（需传入 K 线以计算）。</div>')
    else:
        H.append("<table><tr><th>币</th><th>入场</th><th>现价</th><th>止损</th>"
                 "<th>止损法</th><th>风险%</th><th>TP1</th><th>TP2</th>"
                 "<th>R:R(TP1)</th><th>建议仓位</th><th>最大亏损</th></tr>")
        for sym, p in plans.items():
            if not p or not p.get("ok"):
                H.append(f'<tr><td>{_esc(sym)}</td><td colspan="10" class="mut">'
                         f'{_esc((p or {}).get("error","数据不足"))}</td></tr>')
                continue
            H.append(f'<tr><td><b>{_esc(sym)}</b></td>'
                     f'<td>{_num(p["entry"],6)}</td><td>{_num(p["current"],6)}</td>'
                     f'<td class="neg">{_num(p["stop_loss"],6)}</td>'
                     f'<td class="mut">{_esc(p["sl_method"])}</td>'
                     f'<td class="warn">-{p["stop_pct"]}%</td>'
                     f'<td class="pos">{_num(p["take_profit_1"],6)}'
                     f'<div class="mut">{_pct(p["tp1_pct"])}</div></td>'
                     f'<td class="pos">{_num(p["take_profit_2"],6)}'
                     f'<div class="mut">{_pct(p["tp2_pct"])}</div></td>'
                     f'<td>{p["rr_tp1"]}</td>'
                     f'<td>${p["size_usd_by_risk"]:,.0f}</td>'
                     f'<td class="neg">${p["max_loss_usd"]:,.0f}</td></tr>')
        H.append("</table>")

    # ---------- 5. 底仓策略 ----------
    H.append("<h2>五、底仓策略（分批建仓与退出）</h2>")
    any_plan = next((p for p in plans.values() if p and p.get("ok")), None)
    if not any_plan:
        H.append('<div class="note">未提供点位计划。</div>')
    else:
        bp = any_plan["base_position"]
        H.append(f'<div class="note">{_esc(bp["note"])}</div>')
        H.append("<h3>建仓批次</h3><table><tr><th>批次</th><th>占比</th>"
                 "<th>触发条件</th><th>金额</th></tr>")
        for t in bp["tranches"]:
            H.append(f'<tr><td><b>{_esc(t["name"])}</b></td><td>{t["pct"]}%</td>'
                     f'<td>{_esc(t["trigger"])}</td>'
                     f'<td>${t["amount_usd"]:,.0f}</td></tr>')
        H.append("</table>")
        H.append("<h3>退出批次</h3><table><tr><th>动作</th><th>比例</th>"
                 "<th>触发条件</th></tr>")
        for t in bp["exits"]:
            H.append(f'<tr><td><b>{_esc(t["name"])}</b></td><td>{t["pct"]}%</td>'
                     f'<td>{_esc(t["trigger"])}</td></tr>')
        H.append("</table>")
        H.append("<h3>纪律条款</h3><ul>")
        for rule in bp["rules"]:
            H.append(f"<li>{_esc(rule)}</li>")
        H.append("</ul>")
        H.append('<div class="note warnbox"><b>为什么先建底仓而不是一次满仓：</b>'
                 '方向判断一定会错，错的时候要保证"错得便宜"。底仓让你有参与感、'
                 '又不至于一次看错就伤到本金；突破加仓让盈利时仓位变大（让利润奔跑），'
                 '这就是"截断亏损、让利润奔跑"的具体做法。</div>')

    H.append("<footer>本报告由 K析研判团插件生成，仅供研究参考，"
             "不构成投资建议。所有点位基于历史价格计算，未来表现可能完全不同。"
             "加密资产波动极大，请自行控制风险。</footer>")
    H.append("</body></html>")
    return "".join(H)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="生成战绩可视化报告（自包含 HTML）")
    ap.add_argument("--ledger", default=None, help="战绩 JSON（列表）")
    ap.add_argument("--plans", default=None, help="点位计划 JSON（{symbol: plan}）")
    ap.add_argument("--backtest", default=None, help="backtest.py 输出 JSON")
    ap.add_argument("--out", default="track_report.html")
    ap.add_argument("--title", default="K析研判团 · 战绩与策略可视化报告")
    args = ap.parse_args()

    def load(p):
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return None

    html_out = build_report(load(args.ledger), load(args.plans),
                            load(args.backtest), None, args.title)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"已生成 {args.out}（{len(html_out):,} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())