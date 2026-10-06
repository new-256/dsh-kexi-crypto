#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
assemble_report.py - 把 fetch_klines + indicators 的量化输出组装成 kexi.report/1 契约报告

流水线第 3 步（取数→指标→**组装**→校验→看板）。设计要点：
  * 量化字段（indicators / data_quality / klines / symbol.price）全部来自真实脚本输出，
    绝不凭空生成数字；
  * 研判字段（verdict / trend / risk）先给一份**规则草稿**（direction 来自均线排列、
    三情景来自支撑阻力与 ATR、risk.level 来自波动率分档），供主理人 / 望潮 / 守拙覆写；
  * 若传 --verdict-json / --trend-json / --risk-json（成员研判结论落盘的 JSON 片段），
    则用其覆盖对应草稿块——实现「成员结论为权威、脚本不越权臆断」。

用法:
    python assemble_report.py --klines BTCUSDT_1d_klines.json \
        --indicators BTCUSDT_1d_indicators.json --out kexi_out/BTCUSDT_1d_report.json \
        [--trend-json kexi_out/wangchao_trend.json] [--risk-json kexi_out/shouzhuo_risk.json] \
        [--verdict-json kexi_out/lead_verdict.json]

输出单个紧凑 JSON: {"ok":true,"report":"<path>","draft":true|false,...}
失败输出 {"ok":false,"error":"..."} 且退出码 1。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

# 复用同目录 kline_utils.rn（自适应精度，绝不硬 round(x,2)）
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
try:
    import kline_utils as ku
except Exception:  # pragma: no cover - kline_utils 缺失时退化为本地实现
    def _rn(v):
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if f == 0:
            return 0.0
        import math
        d = min(6, max(2, 4 - int(math.floor(math.log10(abs(f))))))
        return round(f, d)
    class ku:  # noqa: N801
        @staticmethod
        def rn(v):
            return _rn(v)

TZ8 = timezone(timedelta(hours=8))
DISCLAIMER = ("本报告由 K析研判团自动生成，基于公开历史行情与技术指标，仅供研究参考，"
              "不构成任何投资建议或交易指引。加密资产波动极端，过往表现不代表未来，请自行风控、独立决策。")

MA_ALIGN_MAP = {"bullish": "多头", "bearish": "空头", "tangled": "震荡"}
_LEVEL_RANK = {"低": 1, "中": 2, "高": 3, "极高": 4}


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _risk_level_from_vol(atr_pct, max_dd_30d):
    """波动率分档给出风险草稿等级（宁高勿低由守拙覆写时把握）。"""
    a = atr_pct or 0
    d = abs(max_dd_30d or 0)
    score_high = a >= 8 or d >= 40
    score_mid = a >= 4 or d >= 20
    return "极高" if score_high else ("高" if score_mid else "中")


def build_draft_verdict(ind, direction, s1, r1, ma20, price):
    headline_map = {
        "多头": f"均线{direction}排列，价格位于 MA10 上方，短线偏多看待",
        "空头": f"均线{direction}排列，反弹承压，短线偏空谨慎",
        "震荡": "均线交织无明确方向，区间震荡对待",
    }
    return {
        "headline": headline_map.get(direction, "趋势待判，结合关键位观察")[:50],
        "rating": {"多头": "逢低布局", "空头": "观望/减仓", "震荡": "区间高抛低吸"}.get(direction, "观望"),
        "position_advice": "（草稿·待主理人结合仓位预算确认）轻仓试错，严格以 MA20/第一支撑为止损锚点",
        "key_levels": [
            {"label": "阻力R1", "price": ku.rn(r1), "action": "关注上方抛压，放量突破再看延伸"},
            {"label": "支撑S1", "price": ku.rn(s1), "action": "回踩企稳可试探，破位转防守"},
            {"label": "生命线", "price": ku.rn(ma20), "action": "MA20 破位则多头研判证伪"},
        ],
        "scenarios_summary": (f"（草稿）短线大概率在 [{ku.rn(s1)}, {ku.rn(r1)}] 区间运行，"
                              f"突破 {ku.rn(r1)} 或失守 {ku.rn(s1)} 决定方向"),
    }


def build_draft_scenarios(s1, s2, r1, r2, direction):
    base_low, base_high = ku.rn(s1), ku.rn(r1)
    if direction == "多头":
        probs = (0.50, 0.30, 0.20)
    elif direction == "空头":
        probs = (0.50, 0.20, 0.30)
    else:
        probs = (0.55, 0.25, 0.20)
    return [
        {"name": "基准", "probability": probs[0], "range_low": base_low, "range_high": base_high,
         "trigger": "在关键位区间内整理，量能平稳"},
        {"name": "乐观", "probability": probs[1], "range_low": base_high, "range_high": ku.rn(r2),
         "trigger": f"放量上破 {ku.rn(r1)} 打开上行空间"},
        {"name": "悲观", "probability": probs[2], "range_low": ku.rn(s2), "range_high": base_low,
         "trigger": f"击穿 {ku.rn(s1)} 回试 {ku.rn(s2)} 与 MA20"},
    ]


def build_draft_risk(ind, direction, bars):
    vol = ind.get("volatility") or {}
    atr_pct = vol.get("atr_pct")
    max_dd = vol.get("max_dd_30d_pct")
    warns = []
    if direction == "多头":
        warns.append("追高需防获利回吐，回踩关键支撑再评估加仓")
    elif direction == "空头":
        warns.append("下行趋势中反弹多为减仓机会，勿盲目抄底")
    else:
        warns.append("方向未明，区间内控制仓位、避免过度交易")
    if atr_pct and atr_pct >= 6:
        warns.append(f"日均真实波幅 ATR% 约 {ku.rn(atr_pct)}，波动剧烈，务必收紧仓位与止损")
    if max_dd and abs(max_dd) >= 25:
        warns.append(f"近 30 日最大回撤约 {ku.rn(abs(max_dd))}%，需预留极端行情缓冲")
    vr = (ind.get("volume") or {}).get("volume_ratio")
    if vr is not None and vr < 0.7:
        warns.append(f"量比 {ku.rn(vr)} 明显缩量，突破有效性存疑，谨防假动作")
    rsi14 = (ind.get("rsi") or {}).get("rsi14")
    if rsi14 is not None and (rsi14 > 70 or rsi14 < 30):
        warns.append(f"RSI14 {ku.rn(rsi14)} 处于{'超买' if rsi14 > 70 else '超卖'}区，短线均值回归风险上升")
    if bars and bars < 60:
        warns.append(f"样本仅 {bars} 根，长周期指标（MA60/MACD）仅供参考")
    warns.append("加密市场 7×24 交易且无涨跌幅限制，严禁重仓博弈与无止损操作")
    return {
        "level": _risk_level_from_vol(atr_pct, max_dd),
        "rationale": "（草稿·风险等级由守拙核定，宁高勿低）依据 ATR%、30 日回撤与趋势结构给出",
        "warnings": warns[:6],
        "extreme_events": [],
        "data_gaps": [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--klines", required=True, help="fetch_klines 输出 JSON 路径")
    ap.add_argument("--indicators", required=True, help="indicators 输出 JSON 路径")
    ap.add_argument("--out", required=True, help="组装后的 report.json 输出路径")
    ap.add_argument("--trend-json", default=None, help="可选：望潮研判结论（覆盖 trend 草稿）")
    ap.add_argument("--risk-json", default=None, help="可选：守拙风险结论（覆盖 risk 草稿）")
    ap.add_argument("--verdict-json", default=None, help="可选：主理人结论（覆盖 verdict 草稿）")
    ap.add_argument("--unlock-json", default=None, help="可选：unlock_schedule 输出（G7 风险旗合并）")
    ap.add_argument("--regime", default=None,
                    help="可选：regime.py 输出（市场级状态判定，写入 market_regime 块）")
    args = ap.parse_args()

    try:
        k = _load(args.klines)
        if k.get("error"):
            raise ValueError(f"klines 输入含错误: {k['error']}")
        ind = _load(args.indicators)
        if ind.get("error"):
            raise ValueError(f"indicators 输入含错误: {ind['error']}")

        price = ind.get("close")
        ma = ind.get("ma") or {}
        ma20 = ma.get("ma20")
        sr = ind.get("support_resistance") or {}
        supports = sr.get("support") or []
        resistances = sr.get("resistance") or []
        s1 = supports[0]["price"] if supports and isinstance(supports[0], dict) and supports[0].get("price") else (price * 0.97 if price else None)
        s2 = supports[1]["price"] if len(supports) > 1 and isinstance(supports[1], dict) and supports[1].get("price") else (price * 0.94 if price else None)
        r1 = resistances[0]["price"] if resistances and isinstance(resistances[0], dict) and resistances[0].get("price") else (price * 1.03 if price else None)
        r2 = resistances[1]["price"] if len(resistances) > 1 and isinstance(resistances[1], dict) and resistances[1].get("price") else (price * 1.06 if price else None)

        direction = MA_ALIGN_MAP.get(ind.get("ma_alignment"), "震荡")
        trend_strength = "强" if (price and ma.get("ma5") and price >= ma.get("ma5")) else "中"

        def _norm_sr(lst):
            out = []
            for x in lst[:3]:
                if isinstance(x, dict) and x.get("price") is not None:
                    out.append({"price": ku.rn(x.get("price")),
                                "strength": x.get("strength", "中"),
                                "basis": x.get("basis", "技术位")})
            return out

        ma_raw = ind.get("macd") or {}
        report = {
            "schema_version": "kexi.report/1",
            "disclaimer": DISCLAIMER,
            "generated_at": datetime.now(TZ8).isoformat(),
            "as_of": ind.get("as_of"),
            "symbol": {
                "code": k.get("symbol") or ind.get("symbol") or "UNKNOWN",
                "name": (k.get("symbol") or ind.get("symbol") or "UNKNOWN"),
                "interval": k.get("interval", "1d"),
                "source": k.get("source", "binance"),
                "price": ku.rn(price),
            },
            "data_quality": {
                "interval_actual": k.get("interval_actual", k.get("interval", "1d")),
                "bars": ind.get("bars", len(k.get("klines", []))),
                "dropped_open_bar": ind.get("dropped_open_bar", k.get("dropped_open_bar", 0)),
                "gaps": k.get("gaps", []),
                "duplicates_removed": k.get("duplicates_removed", 0),
                "last_bar_age_days": ku.rn(k.get("last_bar_age_days", 1.0)),
                "credibility": "可用",
                "notes": "组装自 fetch_klines/indicators 真实输出；研判字段为草稿，需成员/主理人覆写",
            },
            "indicators": {
                "ma": {"ma5": ku.rn(ma.get("ma5")), "ma10": ku.rn(ma.get("ma10")),
                       "ma20": ku.rn(ma.get("ma20")), "ma60": ku.rn(ma.get("ma60"))},
                "ma_alignment": direction,
                "macd": ({
                    "dif": ku.rn(ma_raw.get("dif")), "dea": ku.rn(ma_raw.get("dea")),
                    "hist": ku.rn(ma_raw.get("hist")),
                    "state": ("零轴上" if ma_raw.get("above_zero") else "零轴下"),
                    "hist_direction": ma_raw.get("hist_direction"),
                    "hist_last6": ma_raw.get("hist_last6", []),
                } if ma_raw else None),
                "rsi": {"rsi6": ku.rn((ind.get("rsi") or {}).get("rsi6")),
                        "rsi14": ku.rn((ind.get("rsi") or {}).get("rsi14")),
                        "state": (ind.get("rsi") or {}).get("state", "中性")},
                "volume_ratio": ku.rn((ind.get("volume") or {}).get("volume_ratio")),
                "volatility": {"atr14": ku.rn((ind.get("volatility") or {}).get("atr14")),
                               "atr_pct": ku.rn((ind.get("volatility") or {}).get("atr_pct")),
                               "max_drawdown_30d": ku.rn((ind.get("volatility") or {}).get("max_dd_30d_pct"))},
                "support_resistance": {"support": _norm_sr(supports), "resistance": _norm_sr(resistances)},
                "volume_profile": ind.get("volume_profile", {"available": False}),
            },
            "trend": {
                "direction": direction,
                "strength": trend_strength,
                "stage": "待望潮研判",
                "scenarios": build_draft_scenarios(s1, s2, r1, r2, direction),
                "confidence": "低",
                "falsification": f"（草稿）若日线有效跌破 MA20 ({ku.rn(ma20)}) 则偏多研判证伪",
            },
            # v1.7.0：市场级状态。**与上面的 trend.direction 分开存放**——
            # direction 是本币均线排列，market_regime 是四判据合成的市场级判定。
            # 两者在实盘里经常相反，混在一处会被读成"市场看多=这个币能买"。
            "market_regime": {
                "available": False,
                "note": "未接入 regime.py 输出（assemble_report --regime）。"
                        "缺它不影响单币研判，但**缺少市场级状态这一层维度**。",
            },
            "risk": build_draft_risk(ind, direction, ind.get("bars")),
            "verdict": build_draft_verdict(ind, direction, s1, r1, ma20, price),
            "klines": k.get("klines", []),
        }
        draft = True

        # 成员结论覆写（存在即覆盖，实现「成员为权威」）
        for key, arg in (("trend", args.trend_json), ("risk", args.risk_json), ("verdict", args.verdict_json)):
            if arg and os.path.exists(arg):
                override = _load(arg)
                if isinstance(override, dict) and override:
                    report[key] = {**report[key], **override}
                    draft = False

        # G7：解锁时间表风险旗合并（非覆写块——追加到 risk.warnings 并按需上调等级）
        if args.unlock_json and os.path.exists(args.unlock_json):
            _us = _load(args.unlock_json)
            _flags = _us.get("risk_flags") or []
            if _flags:
                warns = list(report["risk"].get("warnings") or [])
                for fl in _flags:
                    line = "代币解锁：" + str(fl)
                    if line not in warns and len(warns) < 6:
                        warns.append(line)
                report["risk"]["warnings"] = warns[:6]
            _lvl = _us.get("risk_level_90d")
            if _lvl and _LEVEL_RANK.get(_lvl, 0) > _LEVEL_RANK.get(report["risk"].get("level"), 0):
                report["risk"]["level"] = _lvl

        # v1.7.0：接入 regime.py 的市场级状态判定
        if args.regime and os.path.exists(args.regime):
            _rg = _load(args.regime)
            if isinstance(_rg, dict) and _rg.get("regime"):
                # 只取必要字段——判据明细已在 regime.json 里全量保存，
                # 报告里再抄一遍会让报告体积翻倍且两处可能不同步。
                report["market_regime"] = {
                    "available": True,
                    "regime": _rg.get("regime"),
                    "score": _rg.get("score"),
                    "confidence": _rg.get("confidence"),
                    "coverage": _rg.get("coverage"),
                    "criteria": [{"label": c.get("label"),
                                  "value": c.get("value"),
                                  "note": c.get("note"),
                                  "available": c.get("available")}
                                 for c in (_rg.get("criteria") or [])],
                    "invalidation": _rg.get("invalidation") or [],
                    "degraded": _rg.get("degraded") or [],
                    "source": "regime.py",
                }
        report["report_text"] = _render_text(report)

        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"ok": True, "report": args.out, "draft": draft,
                          "next": "validate_report --fix 过契约后，成员可覆写 trend/risk/verdict 再 kexi_dashboard"},
                         ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"组装失败: {e}"}, ensure_ascii=False))
        sys.exit(1)


def _render_regime(mr):
    """市场级状态段落。

    **必须与"本币趋势"分开写、且明确点出区别**——两者极易被混读：
      · trend.direction  = 本币均线排列（"这个币什么形态"）
      · market_regime    = 四判据合成的市场级状态（"市场现在什么状态"）
    实盘里两者经常相反（大盘在跌、某币走独立行情）。混读会让用户以为
    "市场看多"="这个币可以买"，这是危险的滑坡。
    """
    if not mr or not mr.get("available"):
        return ""
    crit = "；".join(
        ("%s %s" % (c.get("label"), c.get("value") or c.get("note") or "不可用"))
        for c in mr.get("criteria", []))
    inval = "\n".join("- " + x for x in mr.get("invalidation", []))
    return ("## 〇、市场状态（**市场级**，非本币）\n"
            "- 判定：**%s** ｜ 置信度 %.2f ｜ 判据覆盖 %.0f%%\n"
            "- 判据：%s\n"
            "- ⚠ 这是**概率标签不是事实**；与下方「本币趋势」不是一回事"
            "（后者只看本币均线排列）。两者相反时**以本币为准**——"
            "市场状态不替代单币判断。\n"
            "- 失效条件（看到即推翻上面判定）：\n%s\n\n"
            % (mr.get("regime"), mr.get("confidence", 0) or 0,
               (mr.get("coverage", 0) or 0) * 100, crit, inval))


def _render_text(r):
    v = r["verdict"]; t = r["trend"]; rk = r["risk"]; sym = r["symbol"]
    sc = "; ".join(f"{s['name']} {int((s.get('probability') or 0)*100)}%"
                   f"[{s.get('range_low')},{s.get('range_high')}]" for s in t.get("scenarios", []))
    kl = "\n".join(f"- {x['label']}: {x['price']} — {x['action']}" for x in v.get("key_levels", []))
    warn = "\n".join(f"- {w}" for w in rk.get("warnings", []))
    return (f"# {sym.get('code')} 走势研判（{r.get('as_of')}）\n\n"
            f"> 数据源 {sym.get('source')} ｜ 周期 {sym.get('interval')} ｜ 最新 {sym.get('price')}\n\n"
            f"{_render_regime(r.get('market_regime'))}"
            f"## 一、核心结论\n- {v.get('headline')}\n- 评级：{v.get('rating')} ｜ 本币趋势：{t.get('direction')}（{t.get('strength')}）\n"
            f"- {v.get('position_advice')}\n\n## 二、关键技术位\n{kl}\n\n"
            f"## 三、三情景推演\n{sc}\n\n## 四、风险提示（等级：{rk.get('level')}）\n{warn}\n\n"
            f"---\n*{r.get('disclaimer')}*\n")


if __name__ == "__main__":
    main()
