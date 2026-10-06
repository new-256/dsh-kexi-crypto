#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compact_report.py - 研判报告 Token 压缩器（省 token 核心利器）

CLI:
    python compact_report.py --input report.json [--out out/report.compact.json] [--max-chars 1200]

功能：
    - 将包含详细文本、多维指标与完整图表数据的完整研判报告 JSON 压缩成极简结论块
    - 输出分两部分：
        1. stdout: 纯文本摘要块（默认 <= 1200 字符，按重要性分级保障），末尾附带 [compact sha1=... chars=...] 机器头
        2. --out: 结构化紧凑 JSON，包含 summary_text、精炼数值 key_numbers 与被裁剪字段清单 dropped_fields
    - 严格遵循第一性原则：绝不编造，缺失字段统一记为 "—"，数值展示完全复用 kline_utils.rn 自适应精度

Stdlib only.
"""

import argparse
import hashlib
import json
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import kline_utils as ku


def safe_rn(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return ku.rn(v)
    return v


def fmt_val(v, suffix=""):
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{ku.rn(v)}{suffix}"
    return f"{v}{suffix}"


def get_first_sentence(text):
    if not text or not isinstance(text, str):
        return "本报告仅供参考，不构成任何投资建议。"
    for sep in ["。", "！", "!", "\n"]:
        if sep in text:
            first = text.split(sep)[0].strip()
            if first:
                return first + "。"
    return text[:60].strip() + "..."


def build_summary(data, max_chars=1200):
    sym = data.get("symbol") or {}
    verdict = data.get("verdict") or {}
    trend = data.get("trend") or {}
    ind = data.get("indicators") or {}
    risk = data.get("risk") or {}
    disclaimer = data.get("disclaimer") or ""

    code = sym.get("code") or sym.get("name") or "BTCUSDT"
    interval = sym.get("interval") or "1d"
    price_str = fmt_val(sym.get("price"))

    # 1. 核心结论
    headline = verdict.get("headline") or "—"
    rating = verdict.get("rating") or "—"
    pos_adv = verdict.get("position_advice") or "—"
    line_verdict = f"【结论】{code}({interval}) 现价:{price_str} | 评级:{rating} | 仓位:{pos_adv} | 研判:{headline}"

    # 2. 关键位 (key_levels 或 support_resistance)
    kl_list = verdict.get("key_levels") or []
    if kl_list:
        kl_strs = [f"{kl.get('label','')} {fmt_val(kl.get('price'))}({kl.get('action','')})" for kl in kl_list[:4]]
        line_levels = f"【关键位】" + " | ".join(kl_strs)
    else:
        sr = ind.get("support_resistance") or {}
        res_list = [f"R{i+1}:{fmt_val(x.get('price'))}" for i, x in enumerate((sr.get("resistance") or [])[:2])]
        sup_list = [f"S{i+1}:{fmt_val(x.get('price'))}" for i, x in enumerate((sr.get("support") or [])[:2])]
        line_levels = f"【关键位】阻力:" + (",".join(res_list) or "—") + " | 支撑:" + (",".join(sup_list) or "—")

    # 3. 三情景概率
    scenarios = trend.get("scenarios") or []
    sc_parts = []
    for sc in scenarios:
        sname = sc.get("name", "")
        sprob = sc.get("probability")
        prob_str = f"{int(round(sprob * 100))}%" if sprob is not None else "—"
        rlow = fmt_val(sc.get("range_low"))
        rhigh = fmt_val(sc.get("range_high"))
        trig = sc.get("trigger", "")
        trig_str = f"({trig})" if trig else ""
        sc_parts.append(f"{sname}({prob_str}):{rlow}~{rhigh}{trig_str}")
    falsification = trend.get("falsification") or ""
    fals_str = f" | 证伪:{falsification}" if falsification else ""
    line_scenarios = f"【三情景】" + (" | ".join(sc_parts) if sc_parts else "—") + fals_str

    # 4. 风控等级
    rlevel = risk.get("level") or "—"
    rationale = risk.get("rationale") or "—"
    warns = risk.get("warnings") or []
    warn_text = "; ".join(warns[:2]) if warns else "—"
    line_risk = f"【风控】等级:{rlevel} | 依据:{rationale} | 警示:{warn_text}"

    # 5. 免责首句
    line_disclaimer = f"【免责】{get_first_sentence(disclaimer)}"

    # 6. 技术指标详情 (重要性相对次要)
    ma = ind.get("ma") or {}
    ma_align = ind.get("ma_alignment") or "—"
    ma20_val = fmt_val(ma.get("ma20"))
    macd = ind.get("macd") or {}
    macd_hist = fmt_val(macd.get("hist"))
    macd_state = macd.get("state") or macd.get("hist_direction") or "—"
    rsi = ind.get("rsi") or {}
    rsi14 = fmt_val(rsi.get("rsi14"))
    rsi_state = rsi.get("state") or "—"
    vol_ratio = fmt_val(ind.get("volume_ratio"))
    volatility = ind.get("volatility") or {}
    atr_pct = fmt_val(volatility.get("atr_pct"), "%")
    mdd = fmt_val(volatility.get("max_drawdown_30d"), "%")
    line_tech = f"【技术面】趋势:{trend.get('direction','—')}({trend.get('strength','—')}) | MA:{ma_align}(MA20:{ma20_val}) | MACD:柱{macd_hist}({macd_state}) | RSI14:{rsi14}({rsi_state}) | 量比:{vol_ratio} | ATR%:{atr_pct} | 回撤:{mdd}"

    # 按重要性组合并检查长度
    # 优先级: 结论 > 关键位 > 三情景 > 风险等级 > 免责首句 > 技术面
    sections = [line_verdict, line_levels, line_scenarios, line_tech, line_risk, line_disclaimer]
    full_text = "\n".join(sections)

    if len(full_text) <= max_chars:
        return full_text

    # 超过限制，自底向上压缩
    # 策略 1: 简化技术面
    line_tech_short = f"【技术面】MA20:{ma20_val} | MACD柱:{macd_hist} | RSI14:{rsi14} | ATR%:{atr_pct}"
    sections = [line_verdict, line_levels, line_scenarios, line_tech_short, line_risk, line_disclaimer]
    if len("\n".join(sections)) <= max_chars:
        return "\n".join(sections)

    # 策略 2: 略去技术面
    sections = [line_verdict, line_levels, line_scenarios, line_risk, line_disclaimer]
    if len("\n".join(sections)) <= max_chars:
        return "\n".join(sections)

    # 策略 3: 进一步压缩风险与情景
    line_risk_short = f"【风控】等级:{rlevel} | 依据:{rationale}"
    sections = [line_verdict, line_levels, line_scenarios, line_risk_short, line_disclaimer]
    if len("\n".join(sections)) <= max_chars:
        return "\n".join(sections)

    # 策略 4: 硬截断保证在 max_chars 以内
    clipped = "\n".join(sections)[:max_chars - 3].rstrip() + "..."
    return clipped


def extract_key_numbers(data):
    sym = data.get("symbol") or {}
    ind = data.get("indicators") or {}
    ma = ind.get("ma") or {}
    rsi = ind.get("rsi") or {}
    vol = ind.get("volatility") or {}
    trend = data.get("trend") or {}
    risk = data.get("risk") or {}

    scenarios = trend.get("scenarios") or []
    prob_base = None
    prob_up = None
    prob_down = None
    for sc in scenarios:
        sname = sc.get("name")
        p = safe_rn(sc.get("probability"))
        if sname == "基准":
            prob_base = p
        elif sname == "乐观":
            prob_up = p
        elif sname == "悲观":
            prob_down = p

    return {
        "price": safe_rn(sym.get("price")),
        "ma20": safe_rn(ma.get("ma20")),
        "rsi14": safe_rn(rsi.get("rsi14")),
        "atr_pct": safe_rn(vol.get("atr_pct")),
        "direction": trend.get("direction"),
        "confidence": trend.get("confidence"),
        "risk_level": risk.get("level"),
        "prob_base": prob_base,
        "prob_up": prob_up,
        "prob_down": prob_down,
    }


def main():
    parser = argparse.ArgumentParser(
        description="研判报告 Token 压缩器：将完整 report.json 压缩为供 LLM 高效引用的摘要块",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, help="输入的 report.json 路径")
    parser.add_argument("--out", default=None, help="输出的 report.compact.json 路径（可选）")
    parser.add_argument("--max-chars", type=int, default=1200, help="摘要纯文本最大字符数（默认 1200）")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(json.dumps({"error": f"输入文件不存在: {args.input}"}, ensure_ascii=False))
        sys.exit(1)

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(json.dumps({"error": f"读取或解析 JSON 失败: {str(e)}"}, ensure_ascii=False))
        sys.exit(1)

    summary_text = build_summary(data, max_chars=args.max_chars)
    sha1_val = hashlib.sha1(summary_text.encode("utf-8")).hexdigest()[:10]
    machine_line = f"[compact sha1={sha1_val} chars={len(summary_text)}]"

    # stdout 输出纯文本摘要块 + 机器行
    print(summary_text)
    print(machine_line)

    # 若指定 --out，写入结构化 JSON
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)
        compact_payload = {
            "summary_text": summary_text,
            "machine_meta": {
                "sha1": sha1_val,
                "chars": len(summary_text),
            },
            "key_numbers": extract_key_numbers(data),
            "dropped_fields": [
                "report_text",
                "data_quality.gaps",
                "data_quality.notes",
                "indicators.divergence",
                "indicators.performance",
                "indicators.golden_dead_cross_recent",
                "risk.extreme_events",
                "risk.data_gaps",
            ],
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(compact_payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
