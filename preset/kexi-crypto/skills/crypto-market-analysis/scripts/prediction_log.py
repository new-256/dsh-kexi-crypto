#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prediction_log.py — 决策留痕与事后对账（v1.5.9 新增）。

要闭环的缺口（见 HANDOFF §12.4 #2 / §12.5）
------------------------------------------------
`backtest.py` 能在**历史**数据上算策略表现，`track_report.py` 能复盘已成交的
台账，但两者**都没和"我们当时到底说了什么"串起来**：出结论时没有落盘
"当时预计 7 日走 [a, b]、评级是什么、置信度多少"，事后自然无法对账。
于是项目一直只能证明"脚本跑得动"，**证明不了结论准不准**。

本脚本补的就是这条链，两个子命令：

  record     读 fast_analysis.json（快答产物），把**当时**的预测快照
             追加进 kexi_out/prediction_log.jsonl。一行一个标的，含
             评级 / 置信度 / 三情景概率与价格区间 / 入场动作 / 失效位 /
             战术止损 / 数据来源。**只追加、不覆盖、不回填修改**——
             事后偷偷改预测等于没留痕。

  reconcile  对已过预测窗口（默认 7 日）的每条快照，取窗口内的真实 K 线，
             算出：实际走了哪一档、基准档是否守住、失效位/止损是否被触发、
             前瞻收益与最大不利偏移；再按评级、置信度、是否给过"立即入场"
             分组汇总（**校准表**）。

诚实性硬约束
------------------------------------------------
  · 窗口内 K 线不足（还没到预测期 / 停更 / 取数失败）→ status=pending/no_data，
    **不参与任何统计**，绝不拿"还没发生"当"没命中"。
  · 校准表每个分组都带 n。样本 < 5 的分组只标 `low_sample=True`，
    报告里必须照实显示 n，不得让 2 条样本的 100% 命中率冒充胜率。
  · 数字全部来自落盘快照与脚本 K 线，**不重算、不修改历史记录**。
  · 零第三方依赖（仅标准库 + 本包兄弟模块）。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TZ8 = timezone(timedelta(hours=8))
LOG_SCHEMA = "kexi.prediction/1"
DEFAULT_HORIZON = 7                  # 与三情景口径一致：7 日
SCEN_NAMES = ("基准", "乐观", "悲观")
LOW_SAMPLE_N = 5                     # 小于此样本量的分组必须显式标注不可信


# ─────────────────────────── 留痕 ───────────────────────────
def snapshot_of(row, horizon_days=DEFAULT_HORIZON, batch_id=None):
    """从 fast_analysis.json 的一行提取**当时**的预测快照。

    只取已算好的字段，不重新计算任何指标 —— 留痕要的是"当时说了什么"，
    不是"现在回头看该说什么"。取不到关键字段就返回 None（不留残缺记录）。
    """
    if not isinstance(row, dict):
        return None
    sym = row.get("symbol")
    close = row.get("close")
    sc = row.get("scenarios")
    if not sym or not isinstance(close, (int, float)) or not isinstance(sc, dict):
        return None
    scen = {}
    for k in SCEN_NAMES:
        s = sc.get(k)
        if not isinstance(s, dict):
            continue
        rng = s.get("range")
        if not (isinstance(rng, list) and len(rng) == 2
                and all(isinstance(x, (int, float)) for x in rng)):
            continue
        p = s.get("p")
        scen[k] = {"p": round(float(p), 4) if isinstance(p, (int, float)) else None,
                   "range": [float(rng[0]), float(rng[1])]}
    if not scen:
        return None                      # 没有可证伪区间 = 没有可对账的预测

    v = row.get("verdict") or {}
    cf = row.get("confidence") or {}
    ep = row.get("entry_plan") or {}
    inv = ep.get("invalidation") or {}
    sp = row.get("stop_plan") or {}
    now = datetime.now(TZ8)
    return {
        "schema": LOG_SCHEMA,
        "symbol": sym,
        "interval": row.get("interval") or "1d",
        "as_of": now.isoformat(),
        "ts_ms": int(now.timestamp() * 1000),
        "batch_id": batch_id,
        "close": float(close),
        "horizon_days": int(horizon_days),
        "rating": v.get("rating"),
        "data_usable": bool(v.get("data_usable")),
        "entry_now": bool(v.get("entry_now")),
        "confidence": v.get("confidence") or cf.get("level"),
        "confidence_score": cf.get("score"),
        "data_source": row.get("data_source"),
        "bars": row.get("bars"),
        "scenarios": scen,
        "entry_actions": [{"type": a.get("type"), "label": a.get("label"),
                           "price": a.get("price")}
                          for a in (ep.get("actions") or []) if isinstance(a, dict)],
        "chase_flagged": bool((ep.get("chase_risk") or {}).get("flagged")),
        "invalidation": inv.get("price") if isinstance(inv.get("price"), (int, float)) else None,
        "stop_tactical": sp.get("tactical") if isinstance(sp.get("tactical"), (int, float)) else None,
    }


def load_fast(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def append_log(log_path, snaps, now_ms=None):
    """追加快照到 jsonl。**同日同标的只留最保守的一条**（一天反复问同一币不该
    稀释样本；且事后回填一条更乐观的结论等于自己给自己开后门）。

    文件本身**永远只追加**，不改写、不删除已落盘的行；去重发生在读取侧
    （`read_log` 按 (标的, 自然日) 取最保守的一条）。这样台账是原始证据，
    任何"事后改预测"的操作都会在文件里留下痕迹。
    保守度：数据不可用 > 扣分多（置信度低）> 缺战术止损位。返回 (新增数, 跳过数)。
    """
    os.makedirs(os.path.dirname(os.path.abspath(log_path)) or ".", exist_ok=True)
    seen = {}
    for e in read_log(log_path):
        day = datetime.fromtimestamp(e.get("ts_ms", 0) / 1000, TZ8).strftime("%Y-%m-%d")
        seen[(e.get("symbol"), day)] = e
    added = skipped = 0
    with open(log_path, "a", encoding="utf-8") as f:
        for s in snaps:
            day = datetime.fromtimestamp(s["ts_ms"] / 1000, TZ8).strftime("%Y-%m-%d")
            old = seen.get((s["symbol"], day))
            # 已有同日记录且它不比新的保守 → 跳过，保留台账里更保守的那条
            if old is not None and _conservativeness(s) <= _conservativeness(old):
                skipped += 1
                continue
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
            seen[(s["symbol"], day)] = s
            added += 1
    return added, skipped


def _conservativeness(snap):
    """排序键，**值越大越保守**：数据不可用(+1000) > 扣分多(+10×分) > 缺止损位(+1)。"""
    return ((0 if snap.get("data_usable") else 1000)
            + (snap.get("confidence_score") or 0) * 10
            + (0 if snap.get("stop_tactical") else 1))


def read_log(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue                      # 坏行跳过，不让一行脏数据毁掉整个台账
            if isinstance(d, dict) and d.get("symbol"):
                out.append(d)
    # 同日同标的后写覆盖先写（与 append_log 的保留策略一致）
    latest = {}
    for d in out:
        day = datetime.fromtimestamp(d.get("ts_ms", 0) / 1000, TZ8).strftime("%Y-%m-%d")
        latest[(d.get("symbol"), day)] = d
    return list(latest.values())


# ─────────────────────────── 对账 ───────────────────────────
def assess(entry, klines, now_ms=None):
    """拿一条预测快照去和窗口内的真实 K 线对账。**纯函数**，不联网。

    返回 status:
      pending    还没到预测期结束（窗口未走完）
      no_data    到期了但取不到窗口内 K 线（停更/取数失败）→ 不参与统计
      ok         可对账
    """
    ts = int(entry.get("ts_ms") or 0)
    if not ts:
        return {"status": "no_data", "why": "快照缺 ts_ms"}
    hz = int(entry.get("horizon_days") or DEFAULT_HORIZON)
    end_ms = ts + hz * 86400000
    now_ms = now_ms if now_ms is not None else int(datetime.now(TZ8).timestamp() * 1000)
    if now_ms < end_ms:
        return {"status": "pending", "days_left": int((end_ms - now_ms) / 86400000)}

    step = 86400000
    future = [k for k in (klines or [])
              if isinstance(k, list) and len(k) >= 5
              and ts < int(k[0]) < end_ms and int(k[0]) % step == 0]
    if not future:
        return {"status": "no_data", "why": "预测窗口内无 K 线（停更或取数失败）"}
    # 窗口内至少要有 3 根日线才谈得上"7 日走势"，否则样本太稀
    if len(future) < 3:
        return {"status": "no_data", "why": "预测窗口内仅 %d 根 K 线，样本过稀" % len(future)}

    lows = [float(k[3]) for k in future]
    highs = [float(k[2]) for k in future]
    closes = [float(k[4]) for k in future]
    a_lo, a_hi, a_end = min(lows), max(highs), closes[-1]
    base_close = float(entry.get("close") or 0)

    # ── 实际落在哪一档：按区间重叠长度归因 ──────────────────────────
    overlap, realized = {}, None
    for name in SCEN_NAMES:
        s = (entry.get("scenarios") or {}).get(name)
        if not s:
            continue
        lo, hi = s["range"]
        ov = max(0.0, min(a_hi, hi) - max(a_lo, lo))
        overlap[name] = round(ov, 8)
    if overlap:
        realized = max(overlap.items(), key=lambda kv: kv[1])[0]
        if overlap[realized] <= 0:
            realized = "envelope_miss"     # 整段都跑出预期包络外
    else:
        realized = "no_bands"

    # Brier 分数：三档概率对"实际命中哪档"的均方误差，越低越好。
    # 均匀瞎猜（各 1/3）≈ 0.667；完美预测 = 0。
    brier = None
    if realized in SCEN_NAMES:
        brier = round(sum(
            (float((entry.get("scenarios") or {}).get(n, {}).get("p") or 0.0)
             - (1.0 if n == realized else 0.0)) ** 2
            for n in SCEN_NAMES
            if (entry.get("scenarios") or {}).get(n)), 4)

    bs = (entry.get("scenarios") or {}).get("基准") or {}
    b_rng = bs.get("range") or [None, None]
    out = {
        "status": "ok",
        "symbol": entry.get("symbol"),
        "as_of": entry.get("as_of"),
        "horizon_days": hz,
        "bars_in_window": len(future),
        "base_close": base_close,
        "actual_low": round(a_lo, 8),
        "actual_high": round(a_hi, 8),
        "actual_end": round(a_end, 8),
        "fwd_return_pct": round((a_end / base_close - 1) * 100, 2) if base_close else None,
        "max_up_pct": round((a_hi / base_close - 1) * 100, 2) if base_close else None,
        "max_dd_pct": round((a_lo / base_close - 1) * 100, 2) if base_close else None,
        "realized_scenario": realized,
        "band_overlap": overlap,
        "brier": brier,
        "base_band_held": (b_rng[0] is not None and a_lo >= b_rng[0] and a_hi <= b_rng[1]),
        "invalidation_hit": (entry.get("invalidation") is not None
                             and a_lo <= float(entry["invalidation"])),
        "stop_hit": (entry.get("stop_tactical") is not None
                     and a_lo <= float(entry["stop_tactical"])),
        "rating": entry.get("rating"),
        "confidence": entry.get("confidence"),
        "entry_now": bool(entry.get("entry_now")),
        "data_usable": bool(entry.get("data_usable")),
    }
    return out


def _agg(items, key):
    buckets = {}
    for it in items:
        buckets.setdefault(it.get(key), []).append(it)
    out = []
    for k, v in buckets.items():
        rets = [x["fwd_return_pct"] for x in v if x.get("fwd_return_pct") is not None]
        dds = [x["max_dd_pct"] for x in v if x.get("max_dd_pct") is not None]
        briers = [x["brier"] for x in v if x.get("brier") is not None]
        out.append({
            "key": k if k is not None else "（未标）",
            "n": len(v),
            "low_sample": len(v) < LOW_SAMPLE_N,
            "avg_fwd_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
            "median_fwd_return_pct": round(sorted(rets)[len(rets) // 2], 2) if rets else None,
            "avg_max_dd_pct": round(sum(dds) / len(dds), 2) if dds else None,
            "worst_dd_pct": round(min(dds), 2) if dds else None,
            "win_rate_pct": round(100.0 * sum(1 for x in rets if x > 0) / len(rets), 1) if rets else None,
            "avg_brier": round(sum(briers) / len(briers), 4) if briers else None,
        })
    out.sort(key=lambda x: (x["key"] is None, str(x["key"])))
    return out


def calibration(outcomes):
    """把已对账的结果汇总成校准表。**只统计 status=ok 的**，pending/no_data
    单独计数并明示排除，绝不混进命中率。"""
    ok = [o for o in outcomes if o.get("status") == "ok"]
    pending = sum(1 for o in outcomes if o.get("status") == "pending")
    no_data = sum(1 for o in outcomes if o.get("status") == "no_data")
    briers = [o["brier"] for o in ok if o.get("brier") is not None]
    return {
        "total": len(outcomes),
        "reconciled": len(ok),
        "pending": pending,
        "excluded_no_data": no_data,
        "exclusion_note": "pending=预测期未走完；no_data=窗口内无足够 K 线。两者均不计入命中率。",
        "brier_avg": round(sum(briers) / len(briers), 4) if briers else None,
        "brier_reference": {"uniform_guess": 0.667, "perfect": 0.0,
                            "note": "三档均匀瞎猜≈0.667，完美预测=0；越低越好"},
        "base_band_hold_rate_pct": (round(100.0 * sum(1 for o in ok if o["base_band_held"]) / len(ok), 1)
                                    if ok else None),
        "invalidation_hit_rate_pct": (round(100.0 * sum(1 for o in ok if o["invalidation_hit"]) / len(ok), 1)
                                      if ok else None),
        "stop_hit_rate_pct": (round(100.0 * sum(1 for o in ok if o["stop_hit"]) / len(ok), 1)
                              if ok else None),
        "realized_scenario_mix": _mix(ok, "realized_scenario"),
        "by_rating": _agg(ok, "rating"),
        "by_confidence": _agg(ok, "confidence"),
        "by_entry_now": _agg(ok, "entry_now"),
    }


def _mix(items, key):
    c = {}
    for it in items:
        c[it.get(key) if it.get(key) is not None else "（未标）"] = \
            c.get(it.get(key) if it.get(key) is not None else "（未标）", 0) + 1
    return [{"key": k, "n": v, "pct": round(100.0 * v / len(items), 1) if items else None}
            for k, v in sorted(c.items(), key=lambda kv: -kv[1])]


# ─────────────────────────── K 线取数 ───────────────────────────
def load_klines(symbol, interval, limit=400, out_dir=None):
    """对账取数：优先本地已有 K 线文件，不足再联网。失败返回 (None, 原因)。"""
    import fetch_klines as fk
    out_dir = out_dir or os.path.join(os.getcwd(), "kexi_out")
    path = os.path.join(out_dir, "%s_%s_klines.json" % (symbol, interval))
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                kl = json.load(f).get("klines") or []
            if kl:
                return kl, "local:%s" % os.path.basename(path)
        except Exception:
            pass
    last_err = None
    for fn, name in ((fk.fetch_binance, "binance"), (fk.fetch_coingecko, "coingecko")):
        try:
            kl, _w = fn(symbol, interval, limit)
            if kl:
                return kl, "fetched:%s" % name
        except Exception as e:
            last_err = "%s: %s" % (name, str(e)[:80])
    return None, last_err or "取数失败"


# ─────────────────────────── 渲染 ───────────────────────────
def render_md(payload):
    cal = payload["calibration"]
    L = ["# 预测对账报告（决策留痕闭环）", "",
         "生成时间：%s" % payload["generated_at"], "",
         "## 一、样本状态", "",
         "| 项 | 值 |", "|---|---|",
         "| 台账总条数 | %d |" % cal["total"],
         "| 可对账 | %d |" % cal["reconciled"],
         "| 预测期未走完（pending） | %d |" % cal["pending"],
         "| 窗口内无足够 K 线（排除） | %d |" % cal["excluded_no_data"],
         "| Brier 均分 | %s |" % cal["brier_avg"],
         "| 基准档守住率 | %s%% |" % cal["base_band_hold_rate_pct"],
         "| 失效位被触发率 | %s%% |" % cal["invalidation_hit_rate_pct"],
         "| 战术止损被触发率 | %s%% |" % cal["stop_hit_rate_pct"],
         "", "> %s" % cal["exclusion_note"],
         "> Brier 参考：%s" % cal["brier_reference"]["note"], ""]
    if cal["reconciled"] < LOW_SAMPLE_N:
        L += ["> ⚠ **可对账样本仅 %d 条，远不足以谈「准不准」**。"
              "本报告此刻的价值是**打通链路**，不是证明有效性。" % cal["reconciled"], ""]
    L += ["## 二、实际落在哪一档", "", "| 档位 | 次数 | 占比 |", "|---|---|---|"]
    for r in cal["realized_scenario_mix"]:
        L.append("| %s | %d | %s%% |" % (r["key"], r["n"], r["pct"]))
    for title, key in (("三、按评级分组", "by_rating"),
                       ("四、按置信度分组", "by_confidence"),
                       ("五、按是否给过「立即入场」分组", "by_entry_now")):
        L += ["", "## %s" % title, "",
              "| 分组 | n | 低样本 | 平均前瞻收益 | 中位前瞻 | 平均最大回撤 | 最差回撤 | 胜率 | Brier |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in cal[key]:
            L.append("| %s%s | %d | %s | %s%% | %s%% | %s%% | %s%% | %s%% | %s |" % (
                r["key"], " ⚠" if r["low_sample"] else "", r["n"],
                "是" if r["low_sample"] else "否",
                r["avg_fwd_return_pct"], r["median_fwd_return_pct"],
                r["avg_max_dd_pct"], r["worst_dd_pct"], r["win_rate_pct"], r["avg_brier"]))
    L += ["", "> ⚠ 标「低样本」的分组 n<%d，命中率不具备统计意义，仅作观察。" % LOW_SAMPLE_N, "",
          "## 六、逐条明细（最近 30 条）", "",
          "| 标的 | 记录时间 | 评级 | 置信度 | 实际落档 | 前瞻收益 | 最大回撤 | 基准守住 | 止损触发 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for o in payload["outcomes"][-30:]:
        if o.get("status") != "ok":
            continue
        L.append("| %s | %s | %s | %s | %s | %s%% | %s%% | %s | %s |" % (
            o.get("symbol"), (o.get("as_of") or "")[:10], o.get("rating"),
            o.get("confidence"), o.get("realized_scenario"),
            o.get("fwd_return_pct"), o.get("max_dd_pct"),
            "是" if o.get("base_band_held") else "否",
            "是" if o.get("stop_hit") else "否"))
    L += ["", "---", "",
          "口径声明：所有数字来自落盘预测快照与脚本 K 线，**未回填、未修改历史记录**；"
          "pending 与 no_data 样本一律不计入命中率。",
          ""]
    return "\n".join(L)


# ─────────────────────────── 主流程 ───────────────────────────
def cmd_record(args):
    src = args.fast or os.path.join(args.out_dir or os.path.join(os.getcwd(), "kexi_out"),
                                    "fast_analysis.json")
    if not os.path.exists(src):
        print(json.dumps({"ok": False, "error": "找不到 %s（先跑 kexi_run mode=fast）" % src},
                         ensure_ascii=False))
        return 2
    payload = load_fast(src)
    batch = payload.get("generated_at") or datetime.now(TZ8).isoformat()
    snaps = []
    for row in payload.get("rows") or []:
        s = snapshot_of(row, args.horizon, batch_id=batch)
        if s:
            snaps.append(s)
        else:
            print("[!] %s 无可证伪三情景，跳过留痕"
                  % (row.get("symbol") if isinstance(row, dict) else "?"),
                  file=sys.stderr)
    if not snaps:
        print(json.dumps({"ok": False,
                          "error": "没有可留痕的预测（三情景缺失或数据不可用）"},
                         ensure_ascii=False))
        return 2
    log_path = args.log or os.path.join(args.out_dir or os.path.join(os.getcwd(), "kexi_out"),
                                        "prediction_log.jsonl")
    added, skipped = append_log(log_path, snaps)
    print(json.dumps({"ok": True, "added": added, "skipped_same_day": skipped,
                      "log": log_path, "symbols": [s["symbol"] for s in snaps]},
                     ensure_ascii=False))
    return 0


def cmd_reconcile(args):
    log_path = args.log or os.path.join(args.out_dir or os.path.join(os.getcwd(), "kexi_out"),
                                        "prediction_log.jsonl")
    entries = read_log(log_path)
    if not entries:
        print(json.dumps({"ok": False, "error": "台账为空：%s（先跑 --record）" % log_path},
                         ensure_ascii=False))
        return 2
    out_dir = args.out_dir or os.path.join(os.getcwd(), "kexi_out")
    now_ms = int(datetime.now(TZ8).timestamp() * 1000)
    cache = {}
    outcomes = []
    for e in sorted(entries, key=lambda x: x.get("ts_ms") or 0):
        sym = e.get("symbol")
        iv = e.get("interval") or "1d"
        if (sym, iv) not in cache:
            cache[(sym, iv)] = load_klines(sym, iv, args.limit, out_dir)
        kl, srcnote = cache[(sym, iv)]
        if not kl:
            o = {"status": "no_data", "symbol": sym, "why": "K 线取数失败：%s" % srcnote}
        else:
            o = assess(e, kl, now_ms)
        o["_kl_source"] = srcnote
        outcomes.append(o)
    payload = {
        "schema": "kexi.prediction_reconcile/1",
        "generated_at": datetime.now(TZ8).isoformat(),
        "log": log_path,
        "horizon_days": args.horizon,
        "calibration": calibration(outcomes),
        "outcomes": outcomes,
    }
    out = args.out or os.path.join(out_dir, "prediction_reconcile.json")
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(json.dumps({"ok": False, "error": "写入失败: %s" % str(e)[:120]},
                         ensure_ascii=False))
        return 1
    md = args.md or os.path.join(out_dir, "prediction_reconcile.md")
    try:
        with open(md, "w", encoding="utf-8") as f:
            f.write(render_md(payload))
    except Exception:
        pass
    c = payload["calibration"]
    print(json.dumps({"ok": True, "total": c["total"], "reconciled": c["reconciled"],
                      "pending": c["pending"], "excluded_no_data": c["excluded_no_data"],
                      "brier_avg": c["brier_avg"], "out": out, "md": md},
                     ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description="决策留痕与事后对账")
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("record", help="把快答预测快照追加进台账")
    p1.add_argument("--fast", help="fast_analysis.json 路径（默认 kexi_out/fast_analysis.json）")
    p1.add_argument("--log", help="台账路径（默认 kexi_out/prediction_log.jsonl）")
    p1.add_argument("--out-dir", default=None)
    p1.add_argument("--horizon", type=int, default=DEFAULT_HORIZON, help="预测期天数（默认 7）")
    p2 = sub.add_parser("reconcile", help="对已过预测期的快照做事后对账")
    p2.add_argument("--log", help="台账路径（默认 kexi_out/prediction_log.jsonl）")
    p2.add_argument("--out", default=None)
    p2.add_argument("--md", default=None)
    p2.add_argument("--out-dir", default=None)
    p2.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    p2.add_argument("--limit", type=int, default=400, help="取数根数（默认 400）")
    args = ap.parse_args()
    if args.cmd == "record":
        return cmd_record(args)
    if args.cmd == "reconcile":
        return cmd_reconcile(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
