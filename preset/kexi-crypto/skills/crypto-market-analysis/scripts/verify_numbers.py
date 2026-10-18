#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_numbers.py - 独立数值复核入口（v1.9.22 新增）

## 为什么需要这个脚本

现场取证（session-44da005c，2026-10-18）里，主理人在整轮会话中调用了
**25 次 pwsh，其中 14 次是内联 `python -c` 手写脚本**，直接调 Binance/OKX
接口自己复算 RSI、90 日位置、换手率。

而插件的人设规则明确写着「禁止手写 Python」。规则与行为直接冲突——
根因不是模型不守规矩，而是**确实没有正规的复核入口**：
当主理人需要交叉验证成员结论时，除了手写脚本别无他法。

本脚本把最高频的「独立复算」场景收进一个工具：
  · 复用主链同一批函数（indicators / position / kline_utils），
    保证口径一致，不会出现"我算的 RSI 和别人算的不一样"这种伪分歧；
  · 但从**原始 K 线**重新计算，不读任何已有的指标 JSON——
    这才是"复核"的意义：验证落盘产物是否与重算结果一致，而不是相信它；
  · 输出一份**可逐项对照**的报告，指出哪些字段对得上、哪些对不上。

## 两种用法

### 1) 复核落盘产物是否可信（主用途）

    python verify_numbers.py --symbol BTCUSDT --out-dir kexi_out

读取 kexi_out/BTCUSDT_1d_klines.json（原始 K 线），重新计算指标与位置，
再与 kexi_out/ 下的 indicators/position 产物逐项比对，输出差异。

### 2) 快速复核某个数值（成员报了数字，想独立验证）

    python verify_numbers.py --symbol BTCUSDT --check rsi=31.7
    python verify_numbers.py --symbol BTCUSDT --check pos_90=76.5

## 输出

JSON（末行）+ 人读摘要。`ok=false` 表示存在对不上的项——
**不会替你决定谁对**，只如实列出差异与容差。

Only stdlib is used.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import kline_utils as ku          # noqa: E402
import indicators as ind_mod      # noqa: E402
import position as pos_mod        # noqa: E402

TZ8 = timezone(timedelta(hours=8))

# 数值比对容差。为什么不设成 0：不同数据源的小数位/复权口径会有
# 极小差异（实测 <0.01%），设 0 会把"正常口径差"误报成"数据错误"，
# 反而让人不再信任复核结果。容差按**相对误差**算，绝对值兜底给极小值。
REL_TOL = 0.005          # 0.5% 相对误差
ABS_TOL = 1e-9


def _f(v):
    """安全转数字：None / 非数值一律返回 None（绝不变成 0）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _klines_from(doc):
    """从各种可能的落盘结构里取出 K 线数组。"""
    if not isinstance(doc, dict):
        return []
    for key in ("klines", "data", "bars"):
        v = doc.get(key)
        if isinstance(v, list) and v and isinstance(v[0], list):
            return v
    return []


def recompute(symbol, interval, out_dir, limit):
    """从**原始 K 线**重算全部可复核数值。

    刻意不读任何已有的 indicators.json / position.json ——
    读了就不叫复核了，那只是把同一个结果复制一遍。
    """
    path = os.path.join(out_dir, "%s_%s_klines.json" % (symbol, interval))
    doc = _load_json(path)
    if doc is None:
        return None, "找不到或读不了 %s —— 请先跑 kexi_run(mode=fast) 或 fetch_klines.py 取数" % path

    kl = _klines_from(doc)
    if not kl:
        return None, "%s 里没有 K 线数组（klines 为空）" % path
    kl = kl[-limit:] if limit and len(kl) > limit else kl

    step = ku.detect_step_ms(kl)
    closed, dropped = ku.drop_open_bar(kl, step)

    closes = [float(k[4]) for k in closed]
    highs = [float(k[2]) for k in closed]
    lows = [float(k[3]) for k in closed]
    vols = [float(k[5]) for k in closed]

    res = {
        "symbol": symbol,
        "interval": interval,
        "source_file": path,
        "bars": len(closed),
        "dropped_open_bar": dropped,
        "interval_actual": ku.humanize_ms(step),
        "last_closed_open": (datetime.fromtimestamp(closed[-1][0] / 1000.0, TZ8).isoformat()
                             if closed else None),
        "close": ku.rn(closes[-1]) if closes else None,
    }

    # ── 指标（复用 indicators.py 的同一批函数，保证口径一致）──
    # 注意各自返回类型不同：sma/ema_series/rsi 返回最新值(float)，
    # macd 返回元组 (dif, dea, hist)，atr 返回 float。这里按实际签名取值。
    ind = {}
    for n in (5, 10, 20, 60):
        if len(closes) >= n:
            ind["ma%d" % n] = ku.rn(ind_mod.sma(closes, n))
    for n in (12, 26):
        if len(closes) >= n:
            ind["ema%d" % n] = ku.rn(ind_mod.ema_series(closes, n))
    if len(closes) >= 35:
        try:
            dif, dea, hist = ind_mod.macd(closes)
            ind["macd_dif"] = ku.rn(dif)
            ind["macd_dea"] = ku.rn(dea)
            ind["macd_hist"] = ku.rn(hist)
        except Exception:
            pass
    for n in (6, 14):
        if len(closes) > n:
            try:
                ind["rsi%d" % n] = round(float(ind_mod.rsi(closes, n)), 2)
            except Exception:
                pass
    if len(closed) >= 15:
        try:
            ind["atr14"] = ku.rn(ind_mod.atr(closed, 14))
        except Exception:
            pass

    # 量能：5 日均量 / 20 日均量
    if len(vols) >= 20:
        v5 = sum(vols[-5:]) / 5.0
        v20 = sum(vols[-20:]) / 20.0
        ind["vol5"] = round(v5, 4)
        ind["vol20"] = round(v20, 4)
        ind["vol_ratio_5_20"] = round(v5 / v20, 4) if v20 else None
    res["indicators"] = ind

    # ── 位置（复用 position.py，含 90 日位置/回撤等）──
    try:
        pos = pos_mod.analyze_position(closed)
        if isinstance(pos, dict):
            res["position"] = {k: (ku.rn(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                               for k, v in pos.items()
                               if isinstance(v, (int, float, str, type(None)))}
    except Exception as e:
        res["position"] = {"error": "position.py 计算失败：%s" % str(e)[:120]}

    # ── 自检：重算结果与落盘自检信息比对（D11）──
    sc = doc.get("self_check") if isinstance(doc, dict) else None
    if isinstance(sc, dict):
        res["source_self_check"] = sc
        diffs = []
        if sc.get("ok") is False:
            diffs.append("源文件 self_check 标记为失败：%s" % str(sc.get("reason"))[:120])
        if sc.get("bars") is not None and int(sc["bars"]) != len(_klines_from(doc)):
            diffs.append("self_check.bars=%s 与文件实际 %d 根不符"
                         % (sc.get("bars"), len(_klines_from(doc))))
        res["self_check_diffs"] = diffs

    return res, None


def compare_with_artifacts(symbol, interval, out_dir, recomputed):
    """把重算值与落盘的 indicators / position 产物逐项比对。"""
    report = []
    targets = [
        ("indicators", os.path.join(out_dir, "%s_%s_indicators.json" % (symbol, interval))),
        ("position", os.path.join(out_dir, "%s_%s_position.json" % (symbol, interval))),
    ]
    for kind, path in targets:
        if not os.path.exists(path):
            report.append({"artifact": kind, "path": path, "status": "missing",
                           "note": "该产物不存在，无从比对"})
            continue
        doc = _load_json(path)
        if not isinstance(doc, dict):
            report.append({"artifact": kind, "path": path, "status": "unreadable",
                           "note": "产物无法解析为 JSON"})
            continue

        # 语义检查：文件名叫 position，内容却是指标 —— 现场真实事故
        has_pos = any(k in doc for k in ("pos_90", "drawdown", "range_90"))
        has_ind = any(k in doc for k in ("ma", "ema", "macd", "rsi", "ma20"))
        if kind == "position" and has_ind and not has_pos:
            report.append({"artifact": kind, "path": path, "status": "semantic_mismatch",
                           "note": "文件名是 position，内容却是指标字段（无 pos_90/drawdown）"
                                   "—— 该产物不可当作位置数据消费"})
            continue
        if kind == "indicators" and has_pos and not has_ind:
            report.append({"artifact": kind, "path": path, "status": "semantic_mismatch",
                           "note": "文件名是 indicators，内容却是位置字段"})
            continue

        # 数值比对
        # 产物是**嵌套结构**（indicators 里是 ma.ma20 / ema.ema12 / macd.dif），
        # 而重算结果是扁平键。这里做一次显式映射——不写映射就得靠猜，
        # 猜错了会把"字段不存在"误报成"数值对不上"。
        FIELD_MAP = {
            "indicators": {
                "ma5": ("ma", "ma5"), "ma10": ("ma", "ma10"),
                "ma20": ("ma", "ma20"), "ma60": ("ma", "ma60"),
                "ema12": ("ema", "ema12"), "ema26": ("ema", "ema26"),
                "macd_dif": ("macd", "dif"), "macd_dea": ("macd", "dea"),
                "macd_hist": ("macd", "hist"),
                "rsi6": ("rsi", "rsi6"), "rsi14": ("rsi", "rsi14"),
                "atr14": ("atr", "atr14"),
            },
            "position": {},
        }
        checked, mismatched = [], []
        src = recomputed.get("position") if kind == "position" else recomputed.get("indicators")
        if isinstance(src, dict):
            for key, exp in src.items():
                if exp is None:
                    continue
                got = None
                path = (FIELD_MAP.get(kind) or {}).get(key)
                if path:
                    node = doc
                    for seg in path:
                        node = node.get(seg) if isinstance(node, dict) else None
                    got = node
                elif key in doc:
                    got = doc.get(key)
                if got is None or isinstance(got, (dict, list)):
                    continue
                gf = _f(got)
                ef = _f(exp)
                if gf is None or ef is None:
                    continue
                checked.append(key)
                denom = max(abs(ef), ABS_TOL)
                if abs(gf - ef) / denom > REL_TOL:
                    mismatched.append({"field": key, "artifact": got, "recomputed": exp,
                                       "rel_diff_pct": round(abs(gf - ef) / denom * 100, 3)})
        report.append({
            "artifact": kind, "path": path,
            "status": "mismatch" if mismatched else ("checked" if checked else "no_overlap"),
            "checked_fields": checked,
            "mismatches": mismatched[:10],
            "note": ("" if checked else "产物字段与重算字段无交集，未做数值比对"),
        })
    return report


def check_expressions(exprs, recomputed):
    """--check name=value 形式的直接数值核对。"""
    out = []
    flat = {}
    for sec in ("indicators", "position"):
        d = recomputed.get(sec) or {}
        if isinstance(d, dict):
            for k, v in d.items():
                flat[k] = v
    flat["close"] = recomputed.get("close")
    flat["bars"] = recomputed.get("bars")

    for e in exprs or []:
        if "=" not in e:
            out.append({"expr": e, "status": "bad_syntax", "note": "需写成 name=value"})
            continue
        name, val = e.split("=", 1)
        name = name.strip()
        claimed = _f(val.strip())
        actual = _f(flat.get(name))
        if actual is None:
            out.append({"expr": e, "status": "unknown_field", "note": "重算结果里没有该字段",
                        "available": sorted(flat.keys())[:20]})
            continue
        if claimed is None:
            out.append({"expr": e, "status": "bad_value", "note": "声称值不是数字"})
            continue
        denom = max(abs(actual), ABS_TOL)
        rel = abs(claimed - actual) / denom
        out.append({
            "expr": e, "field": name,
            "claimed": claimed, "recomputed": actual,
            "rel_diff_pct": round(rel * 100, 3),
            "status": "match" if rel <= REL_TOL else "mismatch",
        })
    return out


def render(res, artifact_report, checks):
    """人读摘要（放 digest 里）。"""
    L = []
    L.append("[独立复核] %s %s · %d 根 · 实周期 %s · 末根 %s"
             % (res["symbol"], res["interval"], res["bars"],
                res.get("interval_actual"), res.get("last_closed_open")))
    L.append("  现价 %s" % res.get("close"))

    ind = res.get("indicators") or {}
    keys = [k for k in ("ma20", "ma60", "rsi6", "rsi14", "atr14", "vol_ratio_5_20") if k in ind]
    if keys:
        L.append("  " + " | ".join("%s=%s" % (k, ind[k]) for k in keys))

    pos = res.get("position") or {}
    pkeys = [k for k in ("pos_90", "drawdown_from_high", "range_90") if k in pos]
    if pkeys:
        L.append("  " + " | ".join("%s=%s" % (k, pos[k]) for k in pkeys))

    for d in res.get("self_check_diffs") or []:
        L.append("  ⚠ 源文件自检异常：%s" % d)

    if artifact_report:
        L.append("  ── 与落盘产物比对 ──")
        for a in artifact_report:
            st = a.get("status")
            if st == "missing":
                L.append("    · %s：产物不存在" % a["artifact"])
            elif st == "unreadable":
                L.append("    ⚠ %s：产物无法解析" % a["artifact"])
            elif st == "semantic_mismatch":
                L.append("    ✗ %s：%s" % (a["artifact"], a.get("note")))
            elif st == "mismatch":
                L.append("    ✗ %s：%d 项对不上（已核 %d 项）"
                         % (a["artifact"], len(a["mismatches"]), len(a["checked_fields"])))
                for m in a.get("mismatches", [])[:5]:
                    L.append("        %s: 产物 %s vs 重算 %s（差 %.2f%%）"
                             % (m["field"], m["artifact"], m["recomputed"], m["rel_diff_pct"]))
            elif st == "checked":
                L.append("    ✓ %s：%d 项全部一致（相对容差 %.1f%%）"
                         % (a["artifact"], len(a["checked_fields"]), REL_TOL * 100))
            else:
                L.append("    · %s：无可比对字段" % a["artifact"])

    if checks:
        L.append("  ── 数值核对 ──")
        for c in checks:
            if c["status"] == "match":
                L.append("    ✓ %s：重算 %s（差 %.2f%%）"
                         % (c["field"], c["recomputed"], c["rel_diff_pct"]))
            elif c["status"] == "mismatch":
                L.append("    ✗ %s：声称 %s vs 重算 %s（差 %.2f%%）"
                         % (c["field"], c["claimed"], c["recomputed"], c["rel_diff_pct"]))
            else:
                L.append("    ⚠ %s：%s" % (c.get("expr"), c.get("note")))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="独立数值复核：从原始 K 线重算指标/位置，与落盘产物逐项比对")
    ap.add_argument("--symbol", required=True, help="如 BTCUSDT（裸符号会自动补 USDT）")
    ap.add_argument("--interval", default="1d", choices=["1h", "4h", "1d", "1w"])
    ap.add_argument("--out-dir", default=None, help="默认 <cwd>/kexi_out")
    ap.add_argument("--limit", type=int, default=240)
    ap.add_argument("--check", action="append", default=[],
                    metavar="NAME=VALUE",
                    help="直接核对某个数值，如 --check rsi14=31.7（可重复）")
    ap.add_argument("--out", default=None, help="复核报告 JSON 输出路径")
    args = ap.parse_args()

    n = ku.normalize_symbol(args.symbol)
    if n["error"]:
        print(json.dumps({"ok": False, "error": "符号非法：" + n["error"]}, ensure_ascii=False))
        return 2
    symbol = n["symbol"]

    out_dir = args.out_dir or os.path.join(os.getcwd(), "kexi_out")
    res, err = recompute(symbol, args.interval, out_dir, args.limit)
    if err:
        print(json.dumps({"ok": False, "error": err, "symbol": symbol}, ensure_ascii=False))
        return 2

    artifact_report = compare_with_artifacts(symbol, args.interval, out_dir, res)
    checks = check_expressions(args.check, res)

    bad = [a for a in artifact_report if a.get("status") in ("mismatch", "semantic_mismatch", "unreadable")]
    bad += [c for c in checks if c["status"] in ("mismatch", "bad_syntax", "bad_value", "unknown_field")]

    payload = {
        "schema": "kexi.verify_numbers/1",
        "generated_at": datetime.now(TZ8).isoformat(),
        "ok": not bad,
        "tolerance_rel": REL_TOL,
        "recomputed": res,
        "artifacts": artifact_report,
        "checks": checks,
        "note": ("这是**独立重算**：只读原始 K 线，不读任何已有指标/位置产物。"
                 "对不上时不替你裁决谁对，只如实列出差异——"
                 "请据差异定位是数据源问题还是产物被污染。"),
    }

    if args.out:
        try:
            d = os.path.dirname(os.path.abspath(args.out))
            if d:
                os.makedirs(d, exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    print(render(res, artifact_report, checks))
    print(json.dumps({"ok": payload["ok"], "symbol": symbol,
                      "artifacts": len(artifact_report),
                      "bad": len(bad), "out": args.out}, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
