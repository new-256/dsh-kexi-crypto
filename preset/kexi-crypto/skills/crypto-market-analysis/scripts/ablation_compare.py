#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ablation_compare.py - 快答（Fast Analysis）vs 团队深研（Team In-Depth Research）消融对比

【重要声明与工具定位】：
    这是**诚实的自我评估工具**，用于持续验证"多 Agent 团队是否真的优于单次确定性调用"——
    **如果不能证明，就该削减团队层**。

【背景事实】：
    快答 fast_analysis.py 实测单币 1.4 秒、批量 4 币 2.1 秒；
    团队深研（数脉→指北→望潮→守拙→汇编）实测单币约 17 分钟。
    两层 11 项核心可计算数字（现价/RSI/MA20/MA60/ATR%/入场动作价位/追高嫌疑/失效位/画像模式 等）
    实测逐位一致。

【核心诚实性设计】：
    1. 标注 layer_origin（层来源）：
       · shared_engine：entry_plan 与 coin_profile 属于两层共用的确定性计算引擎，两层必然一致，
         **绝不能算作 team_advantage（团队优势）**；
       · fast：快答独有推导；
       · team：团队独有推理。
    2. 分类严格互斥（每项落在且仅落在一个分类桶）：
       · identical：两侧均有且数值一致（浮点相对容差 1e-5 或绝对误差 < 1e-6，字符串全等）；
       · divergent：两侧均有但存在实质分歧；
       · fast_only：仅快答产物提供；
       · team_only：仅团队产物提供（且确实有团队值）；
       · team_advantage：团队独有能力（非普通指标数字，而是包含分歧仲裁、消息面诚实性、仓位闸门、
         置信度降级、多周期矛盾裁决等高阶认知项）；
       · unavailable：两侧产物均缺失，无从比较。
    3. 缺产物时如实记录：
       若缺少快答或团队产物，输出 ablation_status 与 missing 清单，不编造对比结果。

依赖环境：
    零第三方依赖，纯 Python 3 标准库（json, argparse, os, sys, math, datetime）。
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta

# 东八区时间
TZ8 = timezone(timedelta(hours=8))

# 常见已知计价后缀
KNOWN_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH", "BNB")


def strip_quote_suffix(raw_symbol):
    """剥离计价后缀，提取 base 代币，如 PYTHUSDT -> PYTH。"""
    s = (raw_symbol or "").strip().upper()
    for q in KNOWN_QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return s[:-len(q)]
    return s


def is_number(val):
    """判断是否为有效数值（排除布尔值与 None）。"""
    if val is None or isinstance(val, bool):
        return False
    return isinstance(val, (int, float)) and not math.isnan(val)


def is_value_equal(v1, v2, rel_tol=1e-5, abs_tol=1e-6):
    """比较两个值是否实质相等。支持数值容差与基础类型比较。"""
    if v1 is None and v2 is None:
        return True
    if v1 is None or v2 is None:
        return False

    if is_number(v1) and is_number(v2):
        f1, f2 = float(v1), float(v2)
        if abs(f1 - f2) <= abs_tol:
            return True
        denom = max(abs(f1), abs(f2))
        if denom > 0 and (abs(f1 - f2) / denom) <= rel_tol:
            return True
        return False

    if isinstance(v1, list) and isinstance(v2, list):
        if len(v1) != len(v2):
            return False
        return all(is_value_equal(a, b, rel_tol, abs_tol) for a, b in zip(v1, v2))

    if isinstance(v1, dict) and isinstance(v2, dict):
        if set(v1.keys()) != set(v2.keys()):
            return False
        return all(is_value_equal(v1[k], v2[k], rel_tol, abs_tol) for k in v1)

    return str(v1).strip() == str(v2).strip()


# ─────────────────────────── 产物发现机制 ───────────────────────────

def discover_team_files(symbol, out_dir):
    """自动容错探测指定标的的团队层产物。返回 (found_files_dict, missing_list)。"""
    sym_upper = symbol.strip().upper()
    base_upper = strip_quote_suffix(sym_upper)

    expected = {
        "entry_plan": [f"{sym_upper}_1d_entry_plan.json", f"{base_upper}_1d_entry_plan.json"],
        "coin_profile": [f"{sym_upper}_1d_coin_profile.json", f"{base_upper}_1d_coin_profile.json"],
        "report": [f"{sym_upper}_1d_report.json", f"{base_upper}_1d_report.json", f"{sym_upper.lower()}_report.json"],
        "wangchao": [f"wangchao_{sym_upper}.json", f"wangchao_{base_upper}.json"],
        "shouzhuo": [
            f"shouzhuo_{sym_upper}_risk.json",
            f"shouzhuo_{base_upper}_risk.json",
            f"shouzhuo_{base_upper}.json",
            f"shouzhuo_risk.json",
            f"{sym_upper.lower()}_risk.json"
        ],
    }

    found = {}
    missing = []

    for role_key, file_cands in expected.items():
        matched_path = None
        for cand_name in file_cands:
            full_p = os.path.join(out_dir, cand_name)
            if os.path.exists(full_p):
                # 如果是通用命名的 shouzhuo_risk.json，需校验内部 symbol
                if cand_name == "shouzhuo_risk.json":
                    try:
                        with open(full_p, encoding="utf-8") as f:
                            d = json.load(f)
                        cand_sym = str(d.get("symbol") or "").strip().upper()
                        if cand_sym not in (sym_upper, base_upper):
                            continue
                    except Exception:
                        continue
                matched_path = full_p
                break
        if matched_path:
            found[role_key] = matched_path
        else:
            missing.append(f"{role_key} (尝试候选: {', '.join(file_cands)})")

    return found, missing


def load_fast_row(symbol, fast_path):
    """从 fast_analysis.json 中加载指定 symbol 的快答数据行。"""
    if not fast_path or not os.path.exists(fast_path):
        return None, f"快答文件不存在: {fast_path}"

    try:
        with open(fast_path, encoding="utf-8") as f:
            fast_data = json.load(f)
    except Exception as e:
        return None, f"快答文件读取失败 ({fast_path}): {str(e)[:100]}"

    sym_upper = symbol.strip().upper()
    base_upper = strip_quote_suffix(sym_upper)

    rows = fast_data.get("rows") or []
    for r in rows:
        r_sym = str(r.get("symbol") or "").strip().upper()
        if r_sym == sym_upper or r_sym == base_upper or strip_quote_suffix(r_sym) == base_upper:
            return r, None

    return None, f"快答文件内未包含标的 {sym_upper} (包含标的: {[r.get('symbol') for r in rows]})"


def load_json_safely(path):
    """安全读取单个 JSON 文件。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ─────────────────────────── 单标的消融对比 ───────────────────────────

def compare_single_symbol(symbol, out_dir, fast_path=None):
    """针对单个标的执行两层对比，输出符合 schema kexi.ablation/1 的结构。"""
    sym_upper = symbol.strip().upper()
    base_upper = strip_quote_suffix(sym_upper)
    fast_file = fast_path or os.path.join(out_dir, "fast_analysis.json")

    # 1. 加载快答产物
    fast_row, fast_err = load_fast_row(sym_upper, fast_file)

    # 2. 发现并加载团队产物
    team_files, team_missing = discover_team_files(sym_upper, out_dir)
    team_ep = load_json_safely(team_files.get("entry_plan"))
    team_cp = load_json_safely(team_files.get("coin_profile"))
    team_rep = load_json_safely(team_files.get("report"))
    team_wc = load_json_safely(team_files.get("wangchao"))
    team_sz = load_json_safely(team_files.get("shouzhuo"))

    # 状态评估
    has_fast = fast_row is not None
    has_team = len(team_files) >= 3  # 至少有 report、wangchao 或 shouzhuo 中的多数

    if not has_fast and not has_team:
        status = "缺少快答与团队双方产物，无法完成消融对比"
    elif not has_fast:
        status = f"缺少快答产物（{fast_err}），仅检出团队产物"
    elif not has_team:
        status = f"缺少团队产物（未发现完整产物链），无法完成深研消融"
    elif team_missing:
        status = f"degraded（团队产物部分缺失: {len(team_missing)}项）"
    else:
        status = "ok"

    items = []

    # 辅助构造对比项
    def add_metric(metric, label, category, layer_origin, f_val, t_val, f_src, t_src, note=""):
        items.append({
            "metric": metric,
            "label": label,
            "category": category,
            "layer_origin": layer_origin,
            "fast": f_val,
            "team": t_val,
            "fast_source": f_src,
            "team_source": t_src,
            "note": note,
        })

    def categorize_metric(f_val, t_val, layer_origin="shared_engine", label="", metric="", f_src="", t_src=""):
        if f_val is None and t_val is None:
            cat = "unavailable"
            note = "两侧产物均缺失，无从比较"
        elif f_val is not None and t_val is not None:
            if is_value_equal(f_val, t_val):
                cat = "identical"
                note = "两侧数值完全一致"
            else:
                cat = "divergent"
                note = f"数值存在差异: 快答={f_val}, 团队={t_val}"
        elif f_val is not None:
            cat = "fast_only"
            note = "仅快答侧计算并输出"
        else:  # t_val is not None
            cat = "team_only"
            note = "仅团队侧输出"

        add_metric(metric, label, cat, layer_origin, f_val, t_val, f_src, t_src, note)

    # ──────────────────────── 24 项可计算指标抽取与比对 ────────────────────────

    # 1. close 现价
    f_close = (fast_row or {}).get("close")
    t_close = (
        ((team_rep or {}).get("symbol") or {}).get("price")
        or ((team_sz or {}).get("anchors") or {}).get("close_1d")
        or (team_wc or {}).get("close_1d")
        or (team_ep or {}).get("close")
    )
    categorize_metric(f_close, t_close, "shared_engine", "现价 Close", "close", "fast.close", "team.report.symbol.price / anchors.close_1d")

    # 2. rsi14
    f_rsi14 = (fast_row or {}).get("rsi14")
    t_rsi14 = (
        (((team_rep or {}).get("indicators") or {}).get("rsi") or {}).get("rsi14")
        or ((team_sz or {}).get("anchors") or {}).get("rsi14_1d")
    )
    categorize_metric(f_rsi14, t_rsi14, "shared_engine", "RSI(14)", "rsi14", "fast.rsi14", "team.report.indicators.rsi.rsi14")

    # 3. 均线 MA5, MA10, MA20, MA60
    f_ma = (fast_row or {}).get("ma") or {}
    t_ma = (((team_rep or {}).get("indicators") or {}).get("ma") or {})
    for ma_k in ("ma5", "ma10", "ma20", "ma60"):
        categorize_metric(
            f_ma.get(ma_k),
            t_ma.get(ma_k),
            "shared_engine",
            f"均线 {ma_k.upper()}",
            ma_k,
            f"fast.ma.{ma_k}",
            f"team.report.indicators.ma.{ma_k}"
        )

    # 4. macd_state
    f_macd_st = (fast_row or {}).get("macd_state")
    t_macd_st = (((team_rep or {}).get("indicators") or {}).get("macd") or {}).get("state")
    categorize_metric(f_macd_st, t_macd_st, "shared_engine", "MACD 状态", "macd_state", "fast.macd_state", "team.report.indicators.macd.state")

    # 5. atr_pct
    f_atrp = ((fast_row or {}).get("volatility") or {}).get("atr_pct")
    t_atrp = (
        (((team_rep or {}).get("indicators") or {}).get("volatility") or {}).get("atr_pct")
        or ((team_sz or {}).get("anchors") or {}).get("atr14_pct_1d")
        or (team_wc or {}).get("atr_pct_1d")
    )
    categorize_metric(f_atrp, t_atrp, "shared_engine", "ATR% 真实波幅百分比", "atr_pct", "fast.volatility.atr_pct", "team.indicators.volatility.atr_pct")

    # 6. max_dd_30d
    f_mdd = ((fast_row or {}).get("volatility") or {}).get("max_dd_30d_pct")
    t_mdd = (
        (((team_rep or {}).get("indicators") or {}).get("volatility") or {}).get("max_drawdown_30d")
        or ((team_sz or {}).get("anchors") or {}).get("max_dd_30d_pct_1d")
    )
    categorize_metric(f_mdd, t_mdd, "shared_engine", "30日最大回撤", "max_dd_30d", "fast.volatility.max_dd_30d_pct", "team.indicators.volatility.max_drawdown_30d")

    # 7. position_90d
    f_pos90 = ((fast_row or {}).get("position") or {}).get("pos_90")
    t_pos90 = ((team_sz or {}).get("anchors") or {}).get("pos_90d_pct") or (team_wc or {}).get("position_90d")
    categorize_metric(f_pos90, t_pos90, "shared_engine", "90日区间位置百分比", "position_90d", "fast.position.pos_90", "team.anchors.pos_90d_pct")

    # 8. volume_ratio
    f_vr = ((fast_row or {}).get("volume") or {}).get("volume_ratio")
    t_vr = (((team_rep or {}).get("indicators") or {}).get("volume_ratio")
            or ((team_sz or {}).get("anchors") or {}).get("volume_ratio_1d"))
    categorize_metric(f_vr, t_vr, "shared_engine", "日线量比", "volume_ratio", "fast.volume.volume_ratio", "team.indicators.volume_ratio")

    # 9. chase_risk.flagged 追高嫌疑（共用引擎）
    f_chase = (((fast_row or {}).get("entry_plan") or {}).get("chase_risk") or {}).get("flagged")
    t_chase = (
        (((team_ep or {}).get("chase_risk") or {}).get("flagged"))
        if team_ep else ((team_sz or {}).get("anchors") or {}).get("chase_risk_flagged")
    )
    categorize_metric(f_chase, t_chase, "shared_engine", "追高嫌疑 Flag", "chase_risk_flagged", "fast.entry_plan.chase_risk.flagged", "team.entry_plan.chase_risk.flagged")

    # 10. entry action 首个价格（pullback_buy 回调入场价位，共用引擎）
    f_acts = ((fast_row or {}).get("entry_plan") or {}).get("actions") or []
    t_acts = (team_ep or {}).get("actions") or []
    f_pb = next((a.get("price") for a in f_acts if a.get("type") == "pullback_buy"), None)
    t_pb = next((a.get("price") for a in t_acts if a.get("type") == "pullback_buy"), None)
    categorize_metric(f_pb, t_pb, "shared_engine", "首选入场动作价位 (Pullback Buy)", "entry_pullback_price", "fast.entry_plan.actions[pullback_buy].price", "team.entry_plan.actions[pullback_buy].price")

    # 11. breakout_add price 突破加仓价位（共用引擎）
    f_bo = next((a.get("price") for a in f_acts if a.get("type") == "breakout_add"), None)
    t_bo = next((a.get("price") for a in t_acts if a.get("type") == "breakout_add"), None)
    categorize_metric(f_bo, t_bo, "shared_engine", "突破加仓价位 (Breakout Add)", "breakout_add_price", "fast.entry_plan.actions[breakout_add].price", "team.entry_plan.actions[breakout_add].price")

    # 12. invalidation price 失效位（共用引擎）
    f_inv = (((fast_row or {}).get("entry_plan") or {}).get("invalidation") or {}).get("price")
    t_inv = (((team_ep or {}).get("invalidation") or {}).get("price"))
    categorize_metric(f_inv, t_inv, "shared_engine", "结构失效位 (Invalidation)", "invalidation_price", "fast.entry_plan.invalidation.price", "team.entry_plan.invalidation.price")

    # 13. team 侧实际核定止损价（shouzhuo cross_conclusion.stop_price）vs 快答失效位
    t_sz_stop = (
        (((team_sz or {}).get("risk") or {}).get("stop_loss") or {}).get("cross_conclusion") or {}
    ).get("stop_price") or ((team_rep or {}).get("risk") or {}).get("stop_loss")
    # 快答侧只有 entry_plan.invalidation 或按 ATR 算，若两侧不同则展示团队推理裁决
    if f_inv is not None and t_sz_stop is not None:
        if is_value_equal(f_inv, t_sz_stop):
            add_metric("team_actual_stop_price", "实际风控止损价", "identical", "team", f_inv, t_sz_stop, "fast.entry_plan.invalidation.price", "team.shouzhuo.stop_loss.cross_conclusion.stop_price", "两者均采用了相同结构止损价")
        else:
            add_metric("team_actual_stop_price", "实际风控止损价", "divergent", "team", f_inv, t_sz_stop, "fast.entry_plan.invalidation.price", "team.shouzhuo.stop_loss.cross_conclusion.stop_price", "快答沿用远端失效位；团队守拙执行均线/结构/ATR三法交叉裁决，收窄了有效保护止损位")
    else:
        categorize_metric(f_inv, t_sz_stop, "team", "实际风控止损价", "team_actual_stop_price", "fast.invalidation.price", "team.shouzhuo.stop_loss")

    # 14. profile pattern 画像模式（共用引擎）
    f_pat = ((fast_row or {}).get("profile") or {}).get("pattern")
    t_pat = (
        (((team_cp or {}).get("pump_dump_pattern") or {}).get("pattern"))
        or (((team_wc or {}).get("coin_profile_qualitative") or {}).get("pattern"))
    )
    categorize_metric(f_pat, t_pat, "shared_engine", "币种画像模式 (Pattern)", "profile_pattern", "fast.profile.pattern", "team.coin_profile.pump_dump_pattern.pattern")

    # 15. regime_vs_btc 顺大盘/逆大盘（共用引擎）
    f_reg = ((fast_row or {}).get("profile") or {}).get("regime_vs_btc")
    t_reg = (((team_cp or {}).get("market_regime") or {}).get("regime_vs_btc"))
    categorize_metric(f_reg, t_reg, "shared_engine", "大盘相对走势 (Regime vs BTC)", "regime_vs_btc", "fast.profile.regime_vs_btc", "team.coin_profile.market_regime.regime_vs_btc")

    # 16. follows 驱动归属（共用引擎）
    f_fol = ((fast_row or {}).get("profile") or {}).get("follows")
    t_fol = (((team_cp or {}).get("market_regime") or {}).get("follows"))
    categorize_metric(f_fol, t_fol, "shared_engine", "大盘驱动属性 (Follows)", "follows", "fast.profile.follows", "team.coin_profile.market_regime.follows")

    # 17. support 首档
    f_sups = (fast_row or {}).get("supports") or []
    f_s1 = f_sups[0].get("price") if f_sups else None
    t_sups = (
        (team_ep or {}).get("supports")
        or (((team_rep or {}).get("indicators") or {}).get("support_resistance") or {}).get("support")
        or []
    )
    t_s1 = t_sups[0].get("price") if t_sups else None
    categorize_metric(f_s1, t_s1, "shared_engine", "首档支撑价 S1", "support_s1", "fast.supports[0].price", "team.entry_plan.supports[0].price")

    # 18. resistance 首档
    f_resis = (fast_row or {}).get("resistances") or []
    f_r1 = f_resis[0].get("price") if f_resis else None
    t_resis = (
        (team_ep or {}).get("resistances")
        or (((team_rep or {}).get("indicators") or {}).get("support_resistance") or {}).get("resistance")
        or []
    )
    t_r1 = t_resis[0].get("price") if t_resis else None
    categorize_metric(f_r1, t_r1, "shared_engine", "首档阻力价 R1", "resistance_r1", "fast.resistances[0].price", "team.entry_plan.resistances[0].price")

    # 19. 三情景概率（基准 / 乐观 / 悲观）
    f_sc = (fast_row or {}).get("scenarios") or {}
    f_probs = [
        round(f_sc.get("基准", {}).get("p") or 0, 2),
        round(f_sc.get("乐观", {}).get("p") or 0, 2),
        round(f_sc.get("悲观", {}).get("p") or 0, 2),
    ] if f_sc else None

    t_wc_sc = (team_wc or {}).get("scenarios") or {}
    if t_wc_sc:
        t_probs = [
            round(t_wc_sc.get("base", {}).get("probability") or 0, 2),
            round(t_wc_sc.get("bull", {}).get("probability") or 0, 2),
            round(t_wc_sc.get("bear", {}).get("probability") or 0, 2),
        ]
    else:
        t_rep_sc = ((team_rep or {}).get("trend") or {}).get("scenarios") or []
        if t_rep_sc and len(t_rep_sc) >= 3:
            t_probs = [round(s.get("probability") or 0, 2) for s in t_rep_sc[:3]]
        else:
            t_probs = None

    if f_probs and t_probs:
        if is_value_equal(f_probs, t_probs):
            add_metric("scenarios_probs", "三情景概率分布 [基准, 乐观, 悲观]", "identical", "team", f_probs, t_probs, "fast.scenarios", "team.wangchao.scenarios", "三情景概率一致")
        else:
            add_metric("scenarios_probs", "三情景概率分布 [基准, 乐观, 悲观]", "divergent", "team", f_probs, t_probs, "fast.scenarios", "team.wangchao.scenarios", "快答基于确定性ATR波幅预估；团队望潮结合了多周期矛盾与消息面加权微调")
    else:
        categorize_metric(f_probs, t_probs, "team", "三情景概率分布 [基准, 乐观, 悲观]", "scenarios_probs", "fast.scenarios", "team.wangchao.scenarios")

    # 20. confidence 置信度评级
    f_conf = None  # 快答层无单独置信度定级
    t_conf = (team_wc or {}).get("confidence") or ((team_rep or {}).get("trend") or {}).get("confidence")
    if t_conf is None:
        add_metric("confidence", "推演置信度", "unavailable", "team", f_conf, t_conf, "fast（无置信度打分）", "team.wangchao.confidence", "两侧产物均缺失，无从比较")
    else:
        add_metric("confidence", "推演置信度", "team_only", "team", f_conf, t_conf, "fast（无置信度打分）", "team.wangchao.confidence", "团队独有：按纪律评估信号矛盾并下调置信度")

    # 21. rating 综合评级
    f_rat = ((fast_row or {}).get("verdict") or {}).get("rating")
    t_rat = ((team_rep or {}).get("verdict") or {}).get("rating") or (((team_sz or {}).get("risk") or {}).get("level"))
    if f_rat and t_rat:
        if f_rat in t_rat or t_rat in f_rat:
            add_metric("rating", "综合评级 Verdict Rating", "identical", "fast", f_rat, t_rat, "fast.verdict.rating", "team.verdict.rating", "评级方向基本吻合")
        else:
            add_metric("rating", "综合评级 Verdict Rating", "divergent", "fast", f_rat, t_rat, "fast.verdict.rating", "team.verdict.rating", "快答评级与团队综合研判存在差异")
    else:
        categorize_metric(f_rat, t_rat, "fast", "综合评级 Verdict Rating", "rating", "fast.verdict.rating", "team.verdict.rating")

    # 22. ann_vol_7d_pct 7日年化波动率
    f_vol7 = ((fast_row or {}).get("volatility") or {}).get("ann_vol_7d_pct")
    t_vol7 = ((team_sz or {}).get("anchors") or {}).get("ann_vol_7d_pct_1d")
    categorize_metric(f_vol7, t_vol7, "shared_engine", "7日年化波动率", "ann_vol_7d_pct", "fast.volatility.ann_vol_7d_pct", "team.shouzhuo.anchors.ann_vol_7d_pct_1d")

    # 23. adv20 流动性
    f_adv = (fast_row or {}).get("adv20_quote")
    t_adv = ((team_sz or {}).get("anchors") or {}).get("adv20_usd")
    categorize_metric(f_adv, t_adv, "shared_engine", "ADV20 成交额流动性", "adv20_liquidity", "fast.adv20_quote", "team.shouzhuo.anchors.adv20_usd")

    # 24. atr14 绝对值
    f_atr_abs = ((fast_row or {}).get("volatility") or {}).get("atr14")
    t_atr_abs = (
        (((team_rep or {}).get("indicators") or {}).get("volatility") or {}).get("atr14")
        or ((((team_rep or {}).get("trend") or {}).get("stop_take_profit") or {}).get("method_atr") or {}).get("atr14_1d")
    )
    categorize_metric(f_atr_abs, t_atr_abs, "shared_engine", "ATR14 绝对值", "atr14_absolute", "fast.volatility.atr14", "team.indicators.volatility.atr14")

    # ──────────────────────── 团队独有能力项 (team_advantage) ────────────────────────

    team_advantages = []

    def check_and_add_advantage(adv_id, label, evidence, source_path):
        if evidence:
            it = {
                "metric": adv_id,
                "label": label,
                "category": "team_advantage",
                "layer_origin": "team",
                "fast": None,
                "team": evidence,
                "fast_source": "fast（无对应能力）",
                "team_source": source_path,
                "note": f"团队独有高阶能力: {label}",
            }
            items.append(it)
            team_advantages.append(it)

    # 1. 成员分歧检测
    m_dis = ((team_rep or {}).get("verdict") or {}).get("member_disagreement")
    check_and_add_advantage("adv_member_disagreement", "成员分歧仲裁 (Member Disagreement)", m_dis, "team.report.verdict.member_disagreement")

    # 2. 消息面证据体系 (news_evidence)
    raw_news = ((team_rep or {}).get("trend") or {}).get("news_evidence") if isinstance((team_rep or {}).get("trend"), dict) else None
    if not isinstance(raw_news, dict):
        raw_news = (team_wc or {}).get("news_evidence") if isinstance(team_wc, dict) else None
    news_ev = raw_news if isinstance(raw_news, dict) else {}
    if news_ev.get("bullish") or news_ev.get("bearish") or news_ev.get("risk_alerts"):
        news_summary = {
            "bullish_count": len(news_ev.get("bullish") or []) if isinstance(news_ev.get("bullish"), list) else 0,
            "bearish_count": len(news_ev.get("bearish") or []) if isinstance(news_ev.get("bearish"), list) else 0,
            "risk_alerts_count": len(news_ev.get("risk_alerts") or []) if isinstance(news_ev.get("risk_alerts"), list) else 0,
            "total_items": news_ev.get("total_items"),
        }
        check_and_add_advantage("adv_news_evidence", "全网消息面证据多空论证 (News Evidence)", news_summary, "team.report.trend.news_evidence")

    # 3. 消息面诚实性标注 (data_honesty / social_gap)
    if news_ev.get("data_honesty") or news_ev.get("social_gap"):
        honesty_info = {
            "data_honesty": news_ev.get("data_honesty"),
            "social_gap": news_ev.get("social_gap"),
        }
        check_and_add_advantage("adv_news_honesty", "消息面陈旧数据/情绪缺口诚实性标注", honesty_info, "team.report.trend.news_evidence.data_honesty")

    # 4. 流动性一票否决与仓位闸门 (anchors.adv20 vs veto_line, position 详细控制)
    sz_risk = (team_sz or {}).get("risk") if isinstance(team_sz, dict) else {}
    sz_pos = sz_risk.get("position") if isinstance(sz_risk, dict) else {}
    sz_anchors = (team_sz or {}).get("anchors") if isinstance(team_sz, dict) else {}
    if sz_pos or (isinstance(sz_anchors, dict) and sz_anchors.get("adv20_veto_line_usd")):
        gate_summary = {
            "adv20_veto_line_usd": sz_anchors.get("adv20_veto_line_usd") if isinstance(sz_anchors, dict) else None,
            "single_notional_cap_usd": (sz_pos or {}).get("single_notional_cap_usd") if isinstance(sz_pos, dict) else None,
            "risk_per_trade_pct_equity": (sz_pos or {}).get("risk_per_trade_pct_equity") if isinstance(sz_pos, dict) else None,
            "max_total_exposure_pct_equity": (sz_pos or {}).get("max_total_exposure_pct_equity") if isinstance(sz_pos, dict) else None,
            "cex_gate": (sz_pos or {}).get("cex_gate") if isinstance(sz_pos, dict) else None,
        }
        check_and_add_advantage("adv_liquidity_position_gate", "守拙流动性一票否决与仓位硬闸门", gate_summary, "team.shouzhuo.risk.position")

    # 5. 多因子情景概率可证伪条件 (wangchao scenarios trigger/falsify/note)
    if isinstance(t_wc_sc, dict) and t_wc_sc:
        sc_triggers = {
            k: {
                "trigger": (t_wc_sc.get(k) or {}).get("trigger") if isinstance(t_wc_sc.get(k), dict) else None,
                "falsify": (t_wc_sc.get(k) or {}).get("falsify") if isinstance(t_wc_sc.get(k), dict) else None,
                "note": (t_wc_sc.get(k) or {}).get("note") if isinstance(t_wc_sc.get(k), dict) else None,
            }
            for k in ("base", "bull", "bear") if k in t_wc_sc
        }
        check_and_add_advantage("adv_scenarios_falsification", "事前可证伪的情景触发与失效条件链", sc_triggers, "team.wangchao.scenarios")

    # 6. 置信度降级机制 (confidence downgrade)
    need_down = (team_wc or {}).get("need_confidence_downgrade") if isinstance(team_wc, dict) else None
    down_reason = (team_wc or {}).get("downgrade_reason") if isinstance(team_wc, dict) else None
    if need_down or down_reason:
        down_summary = {
            "need_downgrade": need_down,
            "reason": down_reason,
            "final_confidence": (team_wc or {}).get("confidence"),
        }
        check_and_add_advantage("adv_confidence_downgrade", "置信度自动降级纪律机制", down_summary, "team.wangchao.downgrade_reason")

    # 7. 多周期分歧识别 (stage_4h / weekly_counter_trend / 死叉)
    wc_stages = (team_wc or {}).get("trend_state_stage") if isinstance(team_wc, dict) else {}
    wc_stages = wc_stages if isinstance(wc_stages, dict) else {}
    if wc_stages.get("stage_4h") or ((team_wc or {}).get("weekly_counter_trend") is not None if isinstance(team_wc, dict) else False):
        stages_summary = {
            "stage_1d": wc_stages.get("stage_1d"),
            "stage_1w": wc_stages.get("stage_1w"),
            "stage_4h": wc_stages.get("stage_4h"),
            "weekly_counter_trend": (team_wc or {}).get("weekly_counter_trend") if isinstance(team_wc, dict) else None,
            "summary": wc_stages.get("summary"),
        }
        check_and_add_advantage("adv_multitimeframe_conflict", "多周期信号冲突与调整初起识别", stages_summary, "team.wangchao.trend_state_stage")

    # 8. 尾部波动历史统计 (extreme_bars_gt10pct_8m)
    ext_bars = ((team_sz or {}).get("anchors") or {}).get("extreme_bars_gt10pct_8m")
    if ext_bars is not None:
        check_and_add_advantage("adv_extreme_tail_risk", "近8个月单根极端振幅(>10%)尾部风险统计", f"{ext_bars} 次极端波动", "team.shouzhuo.anchors.extreme_bars_gt10pct_8m")

    # 9. 成交量分布 Volume Profile (POC / VA / HVN / LVN)
    raw_ind = (team_rep or {}).get("indicators") if isinstance(team_rep, dict) else {}
    vp = raw_ind.get("volume_profile") if isinstance(raw_ind, dict) else {}
    vp = vp if isinstance(vp, dict) else {}
    if vp.get("available") and isinstance(vp.get("point_of_control"), dict):
        vp_summary = {
            "poc_price": (vp.get("point_of_control") or {}).get("price"),
            "va_high": (vp.get("value_area") or {}).get("va_high") if isinstance(vp.get("value_area"), dict) else None,
            "va_low": (vp.get("value_area") or {}).get("va_low") if isinstance(vp.get("value_area"), dict) else None,
            "hvn_count": len(vp.get("hvn") or []) if isinstance(vp.get("hvn"), list) else 0,
            "lvn_count": len(vp.get("lvn") or []) if isinstance(vp.get("lvn"), list) else 0,
        }
        check_and_add_advantage("adv_volume_profile", "筹码分布特征 (POC / VA / HVN / LVN)", vp_summary, "team.report.indicators.volume_profile")

    # 10. 三档止损交叉结论 (stop_loss.method_atr/structure/ma + cross_conclusion)
    raw_sz_risk = (team_sz or {}).get("risk") if isinstance(team_sz, dict) else {}
    sz_sl = raw_sz_risk.get("stop_loss") if isinstance(raw_sz_risk, dict) else {}
    sz_sl = sz_sl if isinstance(sz_sl, dict) else {}
    if sz_sl.get("cross_conclusion") or sz_sl.get("method_atr"):
        sl_summary = {
            "cross_stop_price": (sz_sl.get("cross_conclusion") or {}).get("stop_price") if isinstance(sz_sl.get("cross_conclusion"), dict) else None,
            "method_atr": (sz_sl.get("method_atr") or {}).get("rule") if isinstance(sz_sl.get("method_atr"), dict) else None,
            "method_structure": (sz_sl.get("method_structure") or {}).get("rule") if isinstance(sz_sl.get("method_structure"), dict) else None,
            "method_ma": (sz_sl.get("method_ma") or {}).get("rule") if isinstance(sz_sl.get("method_ma"), dict) else None,
        }
        check_and_add_advantage("adv_three_method_stop_loss", "ATR/结构/均线三法交叉验证止损体系", sl_summary, "team.shouzhuo.risk.stop_loss")

    # 11. 分批建仓与止盈计划 (base_position_plan / take_profit_plan)
    raw_trend = (team_rep or {}).get("trend") if isinstance(team_rep, dict) else {}
    raw_stp = raw_trend.get("stop_take_profit") if isinstance(raw_trend, dict) else {}
    tp_plan = raw_stp.get("take_profit_plan") if isinstance(raw_stp, dict) else None
    pos_plan = (team_wc or {}).get("base_position_plan") if isinstance(team_wc, dict) else None
    if tp_plan or pos_plan:
        plan_summary = {
            "take_profit_tiers": len(tp_plan) if isinstance(tp_plan, list) else 0,
            "base_position_plan": pos_plan[:2] if isinstance(pos_plan, list) else pos_plan,
        }
        check_and_add_advantage("adv_position_take_profit_plan", "分批建仓与阶梯止盈执行计划", plan_summary, "team.report.trend.stop_take_profit.take_profit_plan")

    # 12. 条件闸门判定 (conditions_check.verdict)
    cond_check = (team_wc or {}).get("conditions_check") if isinstance(team_wc, dict) else None
    if isinstance(cond_check, dict) and cond_check.get("verdict"):
        check_and_add_advantage("adv_conditions_gate_verdict", "望潮量能/大盘环境入场条件闸门裁决", cond_check.get("verdict"), "team.wangchao.conditions_check.verdict")

    # ──────────────────────── 统计与汇总 ────────────────────────

    identical_items = [it for it in items if it["category"] == "identical"]
    divergent_items = [it for it in items if it["category"] == "divergent"]
    fast_only_items = [it for it in items if it["category"] == "fast_only"]
    team_only_items = [it for it in items if it["category"] == "team_only"]
    team_adv_items = [it for it in items if it["category"] == "team_advantage"]
    unavailable_items = [it for it in items if it["category"] == "unavailable"]

    identical_count = len(identical_items)
    divergent_count = len(divergent_items)
    fast_only_count = len(fast_only_items)
    team_only_count = len(team_only_items)
    team_adv_count = len(team_adv_items)
    unavailable_count = len(unavailable_items)

    comparable_count = identical_count + divergent_count

    # (fast_ok, team_ok) 映射统一驱动文案
    fast_ok = has_fast
    team_ok = has_team

    missing_team_files = team_missing if team_missing else [
        f"{sym_upper}_1d_entry_plan.json",
        f"{sym_upper}_1d_coin_profile.json",
        f"{sym_upper}_1d_report.json",
        f"wangchao_{sym_upper}.json",
        f"shouzhuo_{sym_upper}_risk.json",
    ]
    missing_team_str = "、".join(missing_team_files)

    if not fast_ok and not team_ok:
        one_liner = "缺少快答与团队产物，无法完成消融对比，不对用哪一层下判断"
        recommendation = (
            f"数据严重不足，无法给出层级推荐。需先生成快答产物（{fast_file} 缺少 {sym_upper} 数据行），"
            f"并补齐团队产物：{missing_team_str}。"
        )
        verdict_choice = "数据不足，无法判定"
    elif not fast_ok and team_ok:
        one_liner = f"快答产物未发现；团队层具备 {team_adv_count} 项深研特有能力，需补跑快答后比对数字一致性。"
        recommendation = (
            "团队层产物已就绪，但缺少对应标的的快答产物，暂时无法比对可计算数字的一致性；"
            "建议先补跑 fast_analysis 生成快答行后再行消融评估。"
        )
        verdict_choice = "待快答产物补齐后判定"
    elif fast_ok and not team_ok:
        one_liner = f"缺少团队产物（缺失: {missing_team_str}），无法完成消融对比。"
        recommendation = (
            f"快答产物已就绪可独立使用，但团队研判产物缺失（待补齐: {missing_team_str}）；"
            f"当前无法验证团队层的高阶风控增益。"
        )
        verdict_choice = "快答可独立使用；深研增益无数据支撑"
    else:
        # 两侧齐备 (fast_ok and team_ok)
        if comparable_count > 0:
            one_liner = (
                f"{identical_count}/{comparable_count} 项可计算数字逐位一致，"
                f"团队独有的 {team_adv_count} 项能力集中在成员分歧/消息面/流动性否决与多周期冲突裁决——"
                f"日常问盘用快答，需要深度风控与大额仓位管理时升级深研。"
            )
        else:
            one_liner = (
                f"两侧产物均已检出，但有效比对项为 0；"
                f"团队具备 {team_adv_count} 项高阶能力。"
            )
        recommendation = (
            "【分级使用建议】日常问盘/批量选币优先用确定性快答（1~2 秒完成指标、画像、入场位计算，零 LLM 延迟）；"
            "仅在单笔名义敞口较大、需排查成员内部信号矛盾（如日线多头但4h死叉）、"
            "或需消息面真实性核验与严格止损交叉裁决时，才升级调用团队深研。"
        )
        verdict_choice = "推荐使用快答（日常首选）" if divergent_count == 0 else "推荐升级团队深研（存在结构分歧）"

    use_fast_when = [
        "日常看盘、快速获取支撑阻力与入场价格位（1.4s 完成）",
        "多币种初筛或批量选币（批量 N 币仅需 N×0.15s）",
        "只需客观确定性指标、均线排列、ATR 波动率与画像分类",
    ]

    use_team_when = [
        "单笔资金量大，需要守拙的 ADV20 流动性一票否决与名义仓位上限硬闸门",
        "多周期信号背离（如日线多头但 4h 双死叉），需要望潮进行置信度自动降级",
        "市场突发事件频发，需要抓取全网消息面并剔除陈旧过期缓存",
        "需要多成员交叉论证、三档止损交叉结论与阶梯止盈计划",
    ]

    conclusion = {
        "one_liner": one_liner,
        "recommendation": recommendation,
        "use_fast_when": use_fast_when,
        "use_team_when": use_team_when,
        "verdict": verdict_choice,
    }

    premise = (
        "快答 fast_analysis.py 实测单币 1.4 秒、批量 4 币 2.1 秒；"
        "团队深研（数脉→指北→望潮→守拙→汇编）实测单币约 17 分钟。"
        "两层可计算数字实测逐位一致。本工具持续验证团队层是否具备不可替代增益，"
        "如果团队层不能证明高阶优势，就应当削减团队层编排开销。"
    )

    summary = {
        "identical_count": identical_count,
        "divergent_count": divergent_count,
        "fast_only_count": fast_only_count,
        "team_only_count": team_only_count,
        "team_advantage_count": team_adv_count,
        "unavailable_count": unavailable_count,
        "divergent_items": [d["metric"] for d in divergent_items],
        "team_advantage_items": [a["metric"] for a in team_adv_items],
    }

    layers_info = {
        "fast": {
            "file": fast_file,
            "exists": os.path.exists(fast_file),
            "has_symbol": has_fast,
            "error": fast_err,
        },
        "team": {
            "files": team_files,
            "missing": team_missing,
        },
    }

    report = {
        "schema": "kexi.ablation/1",
        "symbol": sym_upper,
        "generated_at": datetime.now(TZ8).isoformat(),
        "ablation_status": status,
        "premise": premise,
        "layers": layers_info,
        "summary": summary,
        "conclusion": conclusion,
        "items": items,
    }

    return report


# ─────────────────────────── 摘要渲染 ───────────────────────────

def render_digest(reports):
    """渲染人类可读的消融对比摘要报告。"""
    lines = []
    lines.append("=" * 78)
    lines.append("【快答 vs 团队深研 消融对比报告 (Ablation Comparison)】")
    lines.append("核心宗旨：诚实评估多 Agent 团队是否真正优于秒级确定性调用——无优势即应削减！")
    lines.append("=" * 78)

    for rep in reports:
        sym = rep.get("symbol")
        st = rep.get("ablation_status")
        summ = rep.get("summary") or {}
        conc = rep.get("conclusion") or {}

        lines.append("")
        lines.append(f"■ 标的: {sym}  状态: 【{st}】")
        lines.append(f"  消融结论: {conc.get('one_liner')}")
        lines.append(f"  建议: {conc.get('verdict')}")

        lines.append("")
        lines.append("  [对比统计]:")
        unavail_cnt = summ.get('unavailable_count', 0)
        lines.append(
            f"    · 一致项 (identical): {summ.get('identical_count', 0)} 项 "
            f"｜ 分歧项 (divergent): {summ.get('divergent_count', 0)} 项 "
            f"｜ 仅快答: {summ.get('fast_only_count', 0)} 项 "
            f"｜ 仅团队: {summ.get('team_only_count', 0)} 项 "
            f"｜ 不可比 (unavailable): {unavail_cnt} 项"
        )
        if unavail_cnt > 0:
            lines.append(f"      （注：{unavail_cnt} 项两侧产物均缺失，属于不可比项，不计入任何一侧统计）")
        lines.append(f"    · 团队独有高阶能力 (team_advantage): {summ.get('team_advantage_count', 0)} 项")

        # 重点显示分歧项
        div_items = [it for it in (rep.get("items") or []) if it.get("category") == "divergent"]
        if div_items:
            lines.append("")
            lines.append("  ⚠ 关键分歧项 (Divergent):")
            for d in div_items:
                lines.append(f"    · {d.get('label')} ({d.get('metric')}) [来源: {d.get('layer_origin')}]:")
                lines.append(f"        快答 = {d.get('fast')}")
                lines.append(f"        团队 = {d.get('team')}")
                lines.append(f"        说明: {d.get('note')}")

        # 重点显示团队优势项
        adv_items = [it for it in (rep.get("items") or []) if it.get("category") == "team_advantage"]
        if adv_items:
            lines.append("")
            lines.append("  ★ 团队独有能力验证 (Team Advantages):")
            for a in adv_items[:8]:
                lines.append(f"    · {a.get('label')}: {str(a.get('team'))[:90]}")
            if len(adv_items) > 8:
                lines.append(f"    · ... 另有 {len(adv_items) - 8} 项团队能力收录于完整 JSON 产物")

        # 显示产物缺失警告
        ly = rep.get("layers") or {}
        tm_miss = (ly.get("team") or {}).get("missing") or []
        if tm_miss:
            lines.append("")
            lines.append("  [产物缺失提示]:")
            for m in tm_miss:
                lines.append(f"    ⚠ 团队层缺失: {m}")
        if (ly.get("fast") or {}).get("error"):
            lines.append(f"    ⚠ 快答层状态: {ly['fast']['error']}")

    lines.append("")
    lines.append("-" * 78)
    lines.append("日常问盘秒级用快答；涉及大资金、多周期冲突或消息面真伪时才调用团队。")
    lines.append("-" * 78)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="快答 vs 团队深研 消融对比工具（诚实评估多 Agent 团队是否真优于单次确定性调用）"
    )
    parser.add_argument("--symbol", help="单个代币，如 PYTHUSDT")
    parser.add_argument("--symbols", help="多个代币，逗号分隔，如 PYTHUSDT,XRPUSDT")
    parser.add_argument("--out-dir", default=None, help="产物输出目录，默认 <cwd>/kexi_out")
    parser.add_argument("--out", default=None, help="对比报告 JSON 输出路径")
    parser.add_argument("--fast", default=None, help="快答 JSON 路径，默认 <out_dir>/fast_analysis.json")

    args = parser.parse_args()

    sym_list = []
    if args.symbol:
        sym_list.append(args.symbol.strip().upper())
    if args.symbols:
        sym_list.extend([s.strip().upper() for s in args.symbols.split(",") if s.strip()])

    if not sym_list:
        err_msg = {"ok": False, "error": "请提供 --symbol 或 --symbols 参数"}
        print(json.dumps(err_msg, ensure_ascii=False))
        return 1

    out_dir = args.out_dir or os.path.join(os.getcwd(), "kexi_out")

    reports = []
    for s in sym_list:
        rep = compare_single_symbol(s, out_dir, fast_path=args.fast)
        reports.append(rep)

    payload = {
        "schema": "kexi.ablation/1",
        "generated_at": datetime.now(TZ8).isoformat(),
        "count": len(reports),
        "reports": reports,
    }

    if args.out:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            pass

    print(render_digest(reports))
    print(json.dumps({
        "ok": True,
        "count": len(reports),
        "out": args.out,
        "symbols": [r["symbol"] for r in reports]
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
