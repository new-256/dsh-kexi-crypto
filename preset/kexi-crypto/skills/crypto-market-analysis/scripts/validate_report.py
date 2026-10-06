#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_report.py - 报告 JSON 契约校验器（质量闸门）

CLI:
    python validate_report.py --input report.json [--fix] [--out out/report.json] [--quiet]

功能：
    - 校验研判报告 JSON 契约完整性与各字段合法性（严格遵循纯 stdlib、无异常抛栈）
    - 针对数据停更（D4 遗留问题）对 last_bar_age_days 进行强校验（日线 >3 天一律 error）
    - 校验数值字段类型，严禁字符串数字、NaN、Infinity
    - 支持 --fix 模式做安全归一（概率归一、异常值置 null、warnings 超长截断、字符串去首尾空白）
    - 输出紧凑 JSON: {ok, errors, warnings, changed}，通过/仅 warn 退出码 0，有 error 退出码 1

Stdlib only.
"""

import argparse
import copy
import json
import math
import os
import sys

SCHEMA_VERSION = "kexi.report/1"
TOP_LEVEL_KEYS = [
    "schema_version",
    "disclaimer",
    "generated_at",
    "as_of",
    "symbol",
    "verdict",
    "data_quality",
    "indicators",
    "trend",
    "risk",
    "report_text",
]

CREDIBILITY_ENUM = {"可用", "部分可用", "不可用"}
DIRECTION_ENUM = {"多头", "空头", "震荡"}
STRENGTH_ENUM = {"强", "中", "弱"}
CONFIDENCE_ENUM = {"高", "中", "低"}
RISK_LEVEL_ENUM = {"低", "中", "高", "极高"}
# ② 分歧度阈值：≥ 此值必须降级为"分歧观察（不入场）"或解释分歧。
# 0.5 = 收集到的结构化信号里有一半自相矛盾。取自 AlphaAgent R-20 的思路，
# 但阈值是我们自己的——没有跨市场实证前不冒充"经过验证"。
DISAGREEMENT_THRESHOLD = 0.5
SCENARIO_NAMES = {"基准", "乐观", "悲观"}


def is_number(val):
    if val is None or isinstance(val, bool):
        return False
    if isinstance(val, (int, float)):
        return not (math.isnan(val) or math.isinf(val))
    return False


def check_numeric_field(val, field_path, errors, warnings, allow_none=True):
    """数值字段校验：null 只 warn（数据不足），字符串/NaN/Infinity/其他类型一律 error"""
    if val is None:
        if allow_none:
            warnings.append(f"{field_path}: 为 null (数据不足)")
        else:
            errors.append(f"{field_path}: 为 null (不可为空)")
        return False
    if isinstance(val, bool):
        errors.append(f"{field_path}: 类型为布尔值，非数值")
        return False
    if isinstance(val, str):
        errors.append(f"{field_path}: 类型为字符串 '{val}'，严禁字符串数值")
        return False
    if isinstance(val, (int, float)):
        if math.isnan(val):
            errors.append(f"{field_path}: 数值为 NaN")
            return False
        if math.isinf(val):
            errors.append(f"{field_path}: 数值为 Infinity")
            return False
        return True
    errors.append(f"{field_path}: 类型非法 ({type(val).__name__})，非数值")
    return False


def validate_report(data):
    """对报告 dict 执行契约校验，返回 (errors, warnings) 两个列表"""
    errors = []
    warnings = []

    if not isinstance(data, dict):
        errors.append("根节点必须为 JSON 对象 (dict)")
        return errors, warnings

    # 1. 顶层键校验
    for k in TOP_LEVEL_KEYS:
        if k not in data:
            errors.append(f"缺少必需顶层键: {k}")

    # schema_version
    sv = data.get("schema_version")
    if sv != SCHEMA_VERSION:
        errors.append(f"schema_version 必须为 '{SCHEMA_VERSION}'，实际为 '{sv}'")

    for str_key in ["disclaimer", "generated_at", "as_of", "report_text"]:
        v = data.get(str_key)
        if str_key in data:
            if not isinstance(v, str) or not v.strip():
                warnings.append(f"{str_key}: 建议为非空字符串")

    # 2. symbol
    sym = data.get("symbol")
    if sym is None:
        pass  # 顶层键缺失已记录
    elif not isinstance(sym, dict):
        errors.append("symbol 必须为字典对象")
    else:
        for k in ["name", "code", "interval", "source"]:
            if k not in sym or sym[k] is None:
                warnings.append(f"symbol.{k}: 缺失或为 null")
            elif not isinstance(sym[k], str):
                errors.append(f"symbol.{k}: 必须为字符串")
        check_numeric_field(sym.get("price"), "symbol.price", errors, warnings)

    # 3. data_quality
    dq = data.get("data_quality")
    if dq is None:
        pass
    elif not isinstance(dq, dict):
        errors.append("data_quality 必须为字典对象")
    else:
        req_dq = ["interval_actual", "bars", "dropped_open_bar", "gaps", "duplicates_removed", "last_bar_age_days", "credibility", "notes"]
        for rk in req_dq:
            if rk not in dq:
                errors.append(f"data_quality 缺少必需字段: {rk}")

        cred = dq.get("credibility")
        if cred not in CREDIBILITY_ENUM:
            errors.append(f"data_quality.credibility 必须为 {sorted(list(CREDIBILITY_ENUM))} 之一，实际为 '{cred}'")

        # 周期一致性
        req_int = sym.get("interval") if isinstance(sym, dict) else None
        act_int = dq.get("interval_actual")
        if req_int and act_int and req_int != act_int:
            warnings.append(f"实际周期 (interval_actual='{act_int}') 与请求周期 (symbol.interval='{req_int}') 不一致")

        # 数据停更（D4 遗留问题）：日线 last_bar_age_days > 3 天一律报 error
        age = dq.get("last_bar_age_days")
        check_numeric_field(age, "data_quality.last_bar_age_days", errors, warnings, allow_none=True)
        is_daily = (req_int == "1d" or act_int == "1d")
        if is_daily and age is not None and isinstance(age, (int, float)) and not math.isnan(age) and age > 3:
            errors.append(f"数据停更 (D4)：日线数据停更天数 {age}d > 3d，标的数据不可用")

        for num_k in ["bars", "dropped_open_bar", "duplicates_removed"]:
            check_numeric_field(dq.get(num_k), f"data_quality.{num_k}", errors, warnings)

        if "gaps" in dq and not isinstance(dq.get("gaps"), list):
            errors.append("data_quality.gaps 必须为数组")

    # 4. indicators
    ind = data.get("indicators")
    if ind is None:
        pass
    elif not isinstance(ind, dict):
        errors.append("indicators 必须为字典对象")
    else:
        # ma
        ma = ind.get("ma")
        if not isinstance(ma, dict):
            errors.append("indicators.ma 必须为字典对象")
        else:
            for mak in ["ma5", "ma10", "ma20", "ma60"]:
                if mak not in ma:
                    errors.append(f"indicators.ma 缺少字段: {mak}")
                else:
                    check_numeric_field(ma.get(mak), f"indicators.ma.{mak}", errors, warnings)

        # ma_alignment
        if "ma_alignment" not in ind:
            errors.append("indicators 缺少字段: ma_alignment")

        # macd
        macd = ind.get("macd")
        if macd is not None and not isinstance(macd, dict):
            errors.append("indicators.macd 必须为字典或 null")
        elif isinstance(macd, dict):
            for mk in ["dif", "dea", "hist"]:
                if mk not in macd:
                    errors.append(f"indicators.macd 缺少字段: {mk}")
                else:
                    check_numeric_field(macd.get(mk), f"indicators.macd.{mk}", errors, warnings)
            if "state" not in macd:
                warnings.append("indicators.macd 缺少 state 字段")

        # rsi
        rsi = ind.get("rsi")
        if not isinstance(rsi, dict):
            errors.append("indicators.rsi 必须为字典对象")
        else:
            for rk in ["rsi6", "rsi14"]:
                if rk not in rsi:
                    errors.append(f"indicators.rsi 缺少字段: {rk}")
                else:
                    check_numeric_field(rsi.get(rk), f"indicators.rsi.{rk}", errors, warnings)
            if "state" not in rsi:
                warnings.append("indicators.rsi 缺少 state 字段")

        # volume_ratio
        if "volume_ratio" not in ind:
            errors.append("indicators 缺少字段: volume_ratio")
        else:
            check_numeric_field(ind.get("volume_ratio"), "indicators.volume_ratio", errors, warnings)

        # volatility
        vol = ind.get("volatility")
        if not isinstance(vol, dict):
            errors.append("indicators.volatility 必须为字典对象")
        else:
            for vk in ["atr_pct", "max_drawdown_30d"]:
                if vk not in vol:
                    errors.append(f"indicators.volatility 缺少字段: {vk}")
                else:
                    check_numeric_field(vol.get(vk), f"indicators.volatility.{vk}", errors, warnings)

        # support_resistance
        sr = ind.get("support_resistance")
        if not isinstance(sr, dict):
            errors.append("indicators.support_resistance 必须为字典对象")
        else:
            for side in ["support", "resistance"]:
                levels = sr.get(side)
                if not isinstance(levels, list):
                    errors.append(f"indicators.support_resistance.{side} 必须为数组")
                else:
                    for i, item in enumerate(levels):
                        if not isinstance(item, dict):
                            errors.append(f"indicators.support_resistance.{side}[{i}] 必须为字典")
                            continue
                        for fld in ["price", "strength", "basis"]:
                            if fld not in item:
                                errors.append(f"indicators.support_resistance.{side}[{i}] 缺少字段: {fld}")
                        check_numeric_field(item.get("price"), f"indicators.support_resistance.{side}[{i}].price", errors, warnings)

    # 5. trend
    trend = data.get("trend")
    if trend is None:
        pass
    elif not isinstance(trend, dict):
        errors.append("trend 必须为字典对象")
    else:
        # 枚举校验
        tdir = trend.get("direction")
        if tdir not in DIRECTION_ENUM:
            errors.append(f"trend.direction 必须为 {sorted(list(DIRECTION_ENUM))} 之一，实际为 '{tdir}'")

        tstr = trend.get("strength")
        if tstr not in STRENGTH_ENUM:
            errors.append(f"trend.strength 必须为 {sorted(list(STRENGTH_ENUM))} 之一，实际为 '{tstr}'")

        tconf = trend.get("confidence")
        if tconf not in CONFIDENCE_ENUM:
            errors.append(f"trend.confidence 必须为 {sorted(list(CONFIDENCE_ENUM))} 之一，实际为 '{tconf}'")

        for req_tk in ["stage", "falsification"]:
            if req_tk not in trend or not isinstance(trend.get(req_tk), str):
                warnings.append(f"trend.{req_tk}: 缺失或建议为非空字符串")

        # scenarios
        scenarios = trend.get("scenarios")
        if not isinstance(scenarios, list):
            errors.append("trend.scenarios 必须为数组")
        else:
            prob_sum = 0.0
            has_valid_prob = True
            for i, sc in enumerate(scenarios):
                if not isinstance(sc, dict):
                    errors.append(f"trend.scenarios[{i}] 必须为字典")
                    continue
                sname = sc.get("name")
                if sname not in SCENARIO_NAMES:
                    warnings.append(f"trend.scenarios[{i}].name 建议为 {sorted(list(SCENARIO_NAMES))} 之一，实际为 '{sname}'")
                p = sc.get("probability")
                p_valid = check_numeric_field(p, f"trend.scenarios[{i}].probability", errors, warnings, allow_none=False)
                if p_valid and isinstance(p, (int, float)):
                    if p < 0 or p > 1:
                        errors.append(f"trend.scenarios[{i}].probability 必须在 0 到 1 之间 (实际为 {p})")
                    prob_sum += p
                else:
                    has_valid_prob = False
                check_numeric_field(sc.get("range_low"), f"trend.scenarios[{i}].range_low", errors, warnings)
                check_numeric_field(sc.get("range_high"), f"trend.scenarios[{i}].range_high", errors, warnings)

            if has_valid_prob and len(scenarios) > 0:
                if prob_sum >= 2.0 or prob_sum <= 0:
                    errors.append(f"三情景 probability 之和为 {prob_sum:.4f}，数值严重异常 (和必须在 0 与 2 之间且接近 1.0)")
                elif abs(prob_sum - 1.0) > 0.15:
                    warnings.append(f"三情景 probability 之和为 {prob_sum:.4f}，偏离 1.0 超过 0.15")

    # 6. risk
    risk = data.get("risk")
    if risk is None:
        pass
    elif not isinstance(risk, dict):
        errors.append("risk 必须为字典对象")
    else:
        rlevel = risk.get("level")
        if rlevel not in RISK_LEVEL_ENUM:
            errors.append(f"risk.level 必须为 {sorted(list(RISK_LEVEL_ENUM))} 之一，实际为 '{rlevel}'")

        if "rationale" not in risk or not isinstance(risk.get("rationale"), str):
            warnings.append("risk.rationale: 缺失或建议为非空字符串")

        warns = risk.get("warnings")
        if not isinstance(warns, list):
            errors.append("risk.warnings 必须为数组")
        else:
            if len(warns) < 3 or len(warns) > 6:
                warnings.append(f"risk.warnings 数量为 {len(warns)}，建议在 3 到 6 条之间")

        for arr_k in ["extreme_events", "data_gaps"]:
            if arr_k not in risk or not isinstance(risk.get(arr_k), list):
                warnings.append(f"risk.{arr_k}: 建议为数组")

        # ── ① 硬风控一票否决（v1.5.5）────────────────────────────────────
        # 取自 AlphaAgent「硬风控一票否决、不参与投票」的设计。
        # 之前守拙的否决只是**散文**：risk.warnings 里写"不建议追高"，
        # 但契约层没有任何字段能阻止报告给出"可入场"评级——是否被遵守
        # 取决于主理人当时怎么写。现在把它变成机制：
        #   risk.veto = true → 评级必须不可交易、entry_plan 不得给 enter_now
        #   vetoed_symbols = [...] → 该标的在多币报告里被点名否决
        veto = risk.get("veto")
        if veto is not None and not isinstance(veto, (bool, dict)):
            errors.append("risk.veto 必须是 bool 或对象（{reason, rule, by}）")
        veto_on = bool(veto) if isinstance(veto, bool) else bool(isinstance(veto, dict) and veto.get("active"))
        if isinstance(veto, dict) and veto.get("active") and not veto.get("reason"):
            warnings.append("risk.veto 为真但缺 reason——否决必须给出可审计的理由")

        vs = risk.get("vetoed_symbols")
        if vs is not None and not isinstance(vs, list):
            errors.append("risk.vetoed_symbols 必须为数组")

    # 7. verdict
    verdict = data.get("verdict")
    if verdict is None:
        pass
    elif not isinstance(verdict, dict):
        errors.append("verdict 必须为字典对象")
    else:
        hl = verdict.get("headline")
        if not isinstance(hl, str):
            errors.append("verdict.headline 必须为字符串")
        else:
            if len(hl) > 50:
                warnings.append(f"verdict.headline 超过 50 字限制 (实际 {len(hl)} 字)")

        for vk in ["rating", "position_advice", "scenarios_summary"]:
            if vk not in verdict or not isinstance(verdict.get(vk), str):
                warnings.append(f"verdict.{vk}: 建议为非空字符串")

        kls = verdict.get("key_levels")
        if not isinstance(kls, list):
            errors.append("verdict.key_levels 必须为数组")
        else:
            for i, kl in enumerate(kls):
                if not isinstance(kl, dict):
                    errors.append(f"verdict.key_levels[{i}] 必须为字典")
                    continue
                for kl_req in ["label", "action"]:
                    if kl_req not in kl:
                        warnings.append(f"verdict.key_levels[{i}] 建议包含 {kl_req}")
                check_numeric_field(kl.get("price"), f"verdict.key_levels[{i}].price", errors, warnings)

    # ── ①（续）否决与评级的强制一致性 ──────────────────────────────────
    # 放在 verdict 校验之后：需要同时看到 risk 与 verdict 才能判定冲突。
    veto_active = bool(
        isinstance(data.get("risk"), dict) and (
            data["risk"].get("veto") is True
            or (isinstance(data["risk"].get("veto"), dict) and data["risk"].get("veto", {}).get("active"))
        )
    )
    if veto_active:
        v = data.get("verdict") or {}
        rating = str(v.get("rating") or "")
        entry = data.get("entry_plan") or {}
        chase = ((entry.get("chase_risk") or {}).get("flagged"))
        # 正面评级词表：一旦守拙否决，这些评级都自相矛盾
        POSITIVE = ("可关注", "可入场", "买入", "看多", "建仓", "推荐", "积极")
        if any(p in rating for p in POSITIVE):
            errors.append(
                f"硬风控一票否决冲突：risk.veto=true 但 verdict.rating='{rating}' 含正面评级词。"
                "守拙的否决不可被主理人调和——请把 rating 改为「否决/不建议入场」一类，"
                "或撤销 veto 并在 risk.rationale 说明理由。")
        if (entry.get("actions") or []) and any(
            str(a.get("type")) == "enter_now" for a in entry.get("actions") or []
        ):
            errors.append("硬风控一票否决冲突：risk.veto=true 但 entry_plan 仍给出 enter_now 动作")
        if chase is True and rating:
            warnings.append("追高嫌疑成立且风控否决：rating 应明确表达「不追高」，而不是任何形式的可执行建议")

    # ── ② 分歧度阈值（v1.5.5）──────────────────────────────────────────
    # 取自 AlphaAgent R-20「多 Agent 分歧度超阈值 → 降仓或不交易」。
    # 之前 member_disagreement 只是给主理人看的一段文字，**没有任何数值门槛**：
    # 分歧再大也不自动降级，全靠主理人自觉。这正是"分歧被记录但未被使用"。
    dis_score, dis_src = compute_disagreement(data)
    if dis_score is not None:
        v = data.get("verdict") or {}
        rating = str(v.get("rating") or "")
        if dis_score >= DISAGREEMENT_THRESHOLD:
            if not any(k in rating for k in ("分歧", "观察", "不入场", "否决", "回调", "反弹", "低置信")):
                errors.append(
                    f"成员分歧度 {dis_score:.2f} ≥ 阈值 {DISAGREEMENT_THRESHOLD}（{dis_src}），"
                    f"但 verdict.rating='{rating}' 未表达分歧/观察。按铁律应降级为"
                    "「分歧观察（不入场）」一类，或给出能解释分歧的证据。")
            warnings.append(f"成员分歧度 {dis_score:.2f} ≥ {DISAGREEMENT_THRESHOLD}（来源：{dis_src}）——已触发降仓门槛")
        elif dis_score >= DISAGREEMENT_THRESHOLD * 0.6:
            warnings.append(f"成员分歧度 {dis_score:.2f} 接近阈值 {DISAGREEMENT_THRESHOLD}（{dis_src}），建议在结论中明示分歧")

    return errors, warnings


# ── ② 分歧度计算（确定性，不用 LLM）────────────────────────────────────
def compute_disagreement(data):
    """从报告里已有的**结构化**信号推导 0~1 的成员分歧度。

    设计原则：**只读结构化字段，不解析自然语言**。
    之前 member_disagreement 是一段散文，无法机械判定；现在让成员把
    结论写成可比较的结构（方向/置信度/评级），由脚本机械求分歧。

    返回 (score, source)；无法判定时返回 (None, 原因)。
    """
    # ① 显式分数优先：成员若已给出数值，直接采用
    for path in (("verdict", "disagreement_score"), ("team", "disagreement_score"),
                 ("disagreement", "score"), ("disagreement",)):
        cur = data
        ok = True
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if ok and isinstance(cur, (int, float)) and not isinstance(cur, bool):
            return max(0.0, min(1.0, float(cur))), "成员显式给出的 disagreement_score"

    # ② 由结构化结论推导：收集每个成员的方向性判断
    stances = []
    tf = data.get("timeframe") or data.get("trend") or {}
    if isinstance(tf, dict):
        d = tf.get("daily_dir") or tf.get("daily")
        w = tf.get("weekly_dir") or tf.get("weekly")
        if isinstance(d, str) and isinstance(w, str):
            stances.append(("多周期", ("多头" in d or "站上" in d or "共振" in str(tf.get("alignment"))),
                            ("空头" in w or "跌破" in w)))
    tfl = data.get("timeframe") or {}
    if isinstance(tfl, dict):
        d = tfl.get("daily_dir"); w = tfl.get("weekly_dir")
        if isinstance(d, str) and isinstance(w, str):
            stances.append(("多周期(日vs周)", ("多头" in d or "站上" in d), ("空头" in w or "跌破" in w)))

    risk = data.get("risk") or {}
    if isinstance(risk, dict):
        lvl = str(risk.get("level") or "")
        rating = str((data.get("verdict") or {}).get("rating") or "")
        if lvl in ("极高", "高") and any(p in rating for p in ("可关注", "买入", "看多", "建仓", "推荐")):
            stances.append(("风控vs评级", True, True))   # 风控说高危、评级说正面 = 冲突

    # ③ 三情景基准区间不含现价 = 成员认为当前价不可持续
    close = (data.get("symbol") or {}).get("price")
    sc = (data.get("trend") or {}).get("scenarios")
    if isinstance(sc, list) and isinstance(close, (int, float)):
        for s in sc:
            if isinstance(s, dict) and str(s.get("name") or "") in ("基准", "base"):
                lo, hi = s.get("range_low", s.get("low")), s.get("range_high", s.get("high"))
                if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                    if not (lo <= close <= hi):
                        stances.append(("现价vs基准情景", True, True))
                        break

    if not stances:
        return None, "报告未提供可机械比较的结构化成员结论（需 verdict.disagreement_score 或 timeframe 日/周方向）"

    conflicts = sum(1 for _, a, b in stances if a and b)
    # 分歧度 = 冲突信号占比；同时并入 trend.alignment 内部的 4h/日线背离（若有）
    score = conflicts / len(stances)
    return max(0.0, min(1.0, score)), "由结构化结论推导：" + "、".join(x[0] for x in stances)


def apply_fixes(data):
    """
    --fix 安全归一处理：
    1. 丢弃 NaN/Infinity 置 null
    2. 递归去除字符串首尾空白
    3. probability 归一到和为 1（保留 2 位小数，误差吸收在最大项）
    4. risk.warnings 超长截断（>6 条截为前 6 条）
    返回 (fixed_data, changed_list)
    """
    fixed = copy.deepcopy(data)
    changed = []

    # 1. 递归清洗 NaN / Infinity / 字符串 strip
    def clean_obj(obj, path=""):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                p = f"{path}.{k}" if path else k
                if isinstance(v, (int, float)) and (math.isnan(v) or math.isinf(v)):
                    obj[k] = None
                    changed.append(f"{p}: NaN/Infinity -> null")
                elif isinstance(v, str):
                    stripped = v.strip()
                    if stripped != v:
                        obj[k] = stripped
                        changed.append(f"{p}: 去除首尾空白")
                elif isinstance(v, (dict, list)):
                    clean_obj(v, p)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                p = f"{path}[{i}]"
                if isinstance(item, (int, float)) and (math.isnan(item) or math.isinf(item)):
                    obj[i] = None
                    changed.append(f"{p}: NaN/Infinity -> null")
                elif isinstance(item, str):
                    stripped = item.strip()
                    if stripped != item:
                        obj[i] = stripped
                        changed.append(f"{p}: 去除首尾空白")
                elif isinstance(item, (dict, list)):
                    clean_obj(item, p)

    clean_obj(fixed)

    # 2. 归一化 probability
    trend = fixed.get("trend")
    if isinstance(trend, dict):
        scenarios = trend.get("scenarios")
        if isinstance(scenarios, list) and len(scenarios) > 0:
            probs = []
            all_num = True
            for sc in scenarios:
                p = sc.get("probability") if isinstance(sc, dict) else None
                if is_number(p):
                    probs.append(float(p))
                else:
                    all_num = False
                    break
            if all_num and len(probs) > 0:
                total = sum(probs)
                if total > 0 and abs(total - 1.0) > 1e-4:
                    scaled = [round(p / total, 2) for p in probs]
                    diff = round(1.0 - sum(scaled), 2)
                    max_idx = probs.index(max(probs))
                    scaled[max_idx] = round(scaled[max_idx] + diff, 2)
                    for i, (sc, old_p, new_p) in enumerate(zip(scenarios, probs, scaled)):
                        if old_p != new_p:
                            sc["probability"] = new_p
                            changed.append(f"trend.scenarios[{i}].probability: {old_p} -> {new_p}")

    # 3. risk.warnings 超长截断
    risk = fixed.get("risk")
    if isinstance(risk, dict):
        warns = risk.get("warnings")
        if isinstance(warns, list) and len(warns) > 6:
            risk["warnings"] = warns[:6]
            changed.append(f"risk.warnings: 数量 {len(warns)} 截断为 6 条")

    return fixed, changed


def main():
    parser = argparse.ArgumentParser(
        description="报告 JSON 契约校验器（质量闸门）：校验研判报告完整性与合法性，支持安全归一",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, help="输入的待校验 report.json 路径")
    parser.add_argument("--fix", action="store_true", help="是否执行安全归一修复")
    parser.add_argument("--out", default=None, help="归一修复后的输出 JSON 路径")
    parser.add_argument("--quiet", action="store_true", help="静默模式，仅输出紧凑校验 JSON")
    args = parser.parse_args()

    # 读取输入
    if not os.path.exists(args.input):
        result = {
            "ok": False,
            "error": f"输入文件不存在: {args.input}",
            "errors": [f"输入文件不存在: {args.input}"],
            "warnings": [],
            "changed": [],
        }
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        result = {
            "ok": False,
            "error": f"解析 JSON 失败: {str(e)}",
            "errors": [f"解析 JSON 失败: {str(e)}"],
            "warnings": [],
            "changed": [],
        }
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    changed_list = []
    if args.fix:
        data, changed_list = apply_fixes(data)
        if args.out:
            out_dir = os.path.dirname(os.path.abspath(args.out))
            os.makedirs(out_dir, exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    errors, warnings = validate_report(data)
    ok = (len(errors) == 0)

    summary = {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "changed": changed_list,
    }

    # 输出紧凑 JSON
    compact_json = json.dumps(summary, ensure_ascii=False)
    print(compact_json)

    if not args.quiet:
        # 人类可读详细诊断信息输出至 stderr，方便调试且不破坏 stdout 紧凑契约
        status_text = "✅ 校验通过" if ok else "❌ 校验失败"
        if ok and warnings:
            status_text += f" (含 {len(warnings)} 项警告)"
        sys.stderr.write(f"\n[{status_text}] 文件: {args.input}\n")
        if errors:
            sys.stderr.write("  错误清单 (errors):\n")
            for e in errors:
                sys.stderr.write(f"    - ❌ {e}\n")
        if warnings:
            sys.stderr.write("  警告清单 (warnings):\n")
            for w in warnings:
                sys.stderr.write(f"    - ⚠️ {w}\n")
        if changed_list:
            sys.stderr.write("  修复变更清单 (changed):\n")
            for c in changed_list:
                sys.stderr.write(f"    - 🔧 {c}\n")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
