#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K析研判团 · 战绩评分器
=========================
从 track-record/ledger.jsonl 读取预测，拉取实时行情，重算战绩并输出 scoreboard。

设计原则：
  1. 只读 ledger（事实层），评分结果写到 score/（派生层），绝不改写历史预测。
  2. 行情源固定 data-api.binance.vision —— 本机 api.binance.com / fapi.binance.com 为 HTTP 451 封锁。
  3. 未满 horizon_days 的记录标 interim=True，禁止提前宣布胜负。
  4. 无三情景区间的记录只算收益，不算 Brier。

用法:
    python score_track_record.py                # 评分并打印
    python score_track_record.py --json         # 输出机读 JSON
    python score_track_record.py --only runC    # 只算某轮
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PRICE_BASE = "https://data-api.binance.vision"
# 本机实测被封锁的镜像（保留作注释，勿启用）
# BLOCKED = ["https://api.binance.com", "https://fapi.binance.com"]
FALLBACK_BASES = ["https://api2.binance.com", "https://api4.binance.com"]

HERE = Path(__file__).resolve().parent.parent
LEDGER = HERE / "ledger.jsonl"


def _get_json(url: str, timeout: int = 20):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def fetch_price(symbol: str) -> float | None:
    """取现价，主源失败则逐个降级镜像。"""
    for base in [PRICE_BASE, *FALLBACK_BASES]:
        try:
            return float(_get_json(f"{base}/api/v3/ticker/price?symbol={symbol}")["price"])
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, TimeoutError):
            continue
    return None


def in_range(v: float, rng) -> bool:
    if not rng or len(rng) != 2 or rng[0] is None or rng[1] is None:
        return False
    lo, hi = float(rng[0]), float(rng[1])
    return min(lo, hi) <= v <= max(lo, hi)


def brier(pbo, hit_idx: int | None) -> float | None:
    """三情景 one-vs-rest Brier。hit_idx: 0=悲观 1=基准 2=乐观。"""
    if not pbo or len(pbo) != 3 or hit_idx is None:
        return None
    ps = [p / 100.0 if p > 1 else float(p) for p in pbo]
    total = 0.0
    for i, p in enumerate(ps):
        o = 1.0 if i == hit_idx else 0.0
        total += (p - o) ** 2
    return round(total, 4)


def classify(rec: dict, price: float) -> dict:
    """判定实际落点属于哪个情景区间。"""
    for idx, key in ((2, "opt_range"), (1, "base_range"), (0, "pess_range")):
        if in_range(price, rec.get(key)):
            return {"hit_zone": ["pess", "base", "opt"][idx], "hit_idx": idx}
    if rec.get("opt_range") or rec.get("pess_range"):
        return {"hit_zone": "outside", "hit_idx": None}
    return {"hit_zone": "unknown", "hit_idx": None}


def horizon_elapsed(rec: dict) -> bool:
    ts = rec.get("baseline_ts_utc")
    if not ts:
        return False
    try:
        start = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    days = (datetime.now(timezone.utc) - start).total_seconds() / 86400.0
    return days >= float(rec.get("horizon_days", 7))


def score_record(rec: dict) -> dict:
    price = fetch_price(rec["symbol"])
    out = dict(rec)
    if price is None:
        out.update(score_error="price_unavailable", verdict="pending")
        return out
    entry = rec.get("entry_price")
    out["current_price"] = price
    out["score_ts_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if entry:
        out["ret_pct"] = round((price / float(entry) - 1) * 100, 2)
    cls = classify(rec, price)
    out.update(cls)
    out["brier"] = brier(rec.get("pbo"), cls["hit_idx"])
    # 证伪线：跌破即判错（无论收益正负）
    fa = rec.get("falsification")
    if fa:
        out["falsified"] = price < float(fa)
    matured = horizon_elapsed(rec)
    out["interim"] = not matured
    if not matured:
        out["verdict"] = "pending"
    elif out.get("falsified"):
        out["verdict"] = "wrong"
    elif cls["hit_idx"] == 2:
        out["verdict"] = "correct"
    elif cls["hit_idx"] == 1:
        out["verdict"] = "partial"
    elif cls["hit_idx"] == 0:
        out["verdict"] = "wrong"
    else:
        out["verdict"] = "outside"
    return out


def load_ledger(only: str | None = None) -> list[dict]:
    if not LEDGER.exists():
        print(f"[ERR] ledger not found: {LEDGER}", file=sys.stderr)
        sys.exit(1)
    recs = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if only and only not in r.get("run_id", ""):
            continue
        recs.append(r)
    return recs


def benchmark_baselines(scored: list[dict]) -> dict:
    """
    BTC 基准：用于区分「选币 alpha」与「市场 beta」。
    每轮运行按其 baseline_ts_utc 取当日 BTC 收盘作为基准，与组合收益对比。
    """
    out = {}
    runs = sorted({r.get("run_id") for r in scored if r.get("run_id")})
    for rid in runs:
        recs = [r for r in scored if r.get("run_id") == rid]
        btc_now = fetch_price("BTCUSDT")
        btc_start = fetch_daily_close("BTCUSDT", recs[0].get("baseline_ts_utc"))
        if btc_now is None or btc_start is None:
            out[rid] = {"error": "btc_price_unavailable"}
            continue
        btc_ret = (btc_now / btc_start - 1) * 100
        tiers = {}
        for tier in sorted({r.get("tier") for r in recs if r.get("tier")}):
            rets = [r["ret_pct"] for r in recs
                    if r.get("tier") == tier and isinstance(r.get("ret_pct"), (int, float))]
            if not rets:
                continue
            avg = sum(rets) / len(rets)
            tiers[tier] = {
                "n": len(rets),
                "avg_ret_pct": round(avg, 2),
                "excess_vs_btc_pct": round(avg - btc_ret, 2),
            }
        out[rid] = {"btc_start": round(btc_start, 2), "btc_now": btc_now,
                    "btc_ret_pct": round(btc_ret, 2), "tiers": tiers}
    return out


def fetch_daily_close(symbol: str, iso_ts: str | None) -> float | None:
    """取基准日的日线收盘价（用于 BTC 基准）。"""
    if not iso_ts:
        return None
    try:
        start = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    ms = int(start.timestamp() * 1000)
    for base in [PRICE_BASE, *FALLBACK_BASES]:
        try:
            kl = _get_json(
                f"{base}/api/v3/klines?symbol={symbol}&interval=1d&startTime={ms}&limit=1"
            )
            if kl:
                return float(kl[0][4])  # close
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, TimeoutError):
            continue
    return None


def summarize(scored: list[dict]) -> dict:
    """分层统计。绝不把 watch/avoid 混进 formal。"""
    summary = {}
    for tier in sorted({r.get("tier", "?") for r in scored}):
        sub = [r for r in scored if r.get("tier") == tier]
        rets = [r["ret_pct"] for r in sub if isinstance(r.get("ret_pct"), (int, float))]
        briers = [r["brier"] for r in sub if isinstance(r.get("brier"), (int, float))]
        zones = {}
        for r in sub:
            z = r.get("hit_zone", "unknown")
            zones[z] = zones.get(z, 0) + 1
        summary[tier] = {
            "n": len(sub),
            "avg_ret_pct": round(sum(rets) / len(rets), 2) if rets else None,
            "winners": sum(1 for x in rets if x > 0),
            "win_rate_pct": round(100 * sum(1 for x in rets if x > 0) / len(rets), 1) if rets else None,
            "avg_brier": round(sum(briers) / len(briers), 4) if briers else None,
            "zone_distribution": zones,
            "falsified": sum(1 for r in sub if r.get("falsified")),
        }
    return summary


def render_markdown(scored: list[dict], summary: dict, bench: dict | None = None) -> str:
    L = ["# K析研判团 · 战绩评分表（scoreboard）", ""]
    L.append(f"> 评分时刻（UTC）：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    L.append("> 行情源：`data-api.binance.vision`（api.binance.com 451 封锁）")
    L.append("")
    if bench:
        L.append("## ⭐ BTC 基准对照（区分 alpha / beta 的关键）")
        L.append("")
        L.append("| run_id | BTC 基准价 | BTC 现价 | BTC 收益% | tier | 组合均值% | **超额%** |")
        L.append("|---|---|---|---|---|---|---|")
        for rid, b in bench.items():
            if "error" in b:
                L.append(f"| {rid} | — | — | — | — | — | {b['error']} |")
                continue
            for tier, t in b["tiers"].items():
                L.append(
                    f"| {rid} | {b['btc_start']} | {b['btc_now']} | {b['btc_ret_pct']} | "
                    f"{tier} | {t['avg_ret_pct']} | **{t['excess_vs_btc_pct']}** |"
                )
        L.append("")
        L.append("> **超额为负 = 组合跑不过只持有 BTC**。此时「选币能力」不成立，")
        L.append("> 收益完全由市场 beta 解释。这是本档案最重要的判据，优先于胜率。")
        L.append("")
    L.append("## 分层汇总")
    L.append("")
    L.append("| tier | 样本 | 均值收益% | 胜 | 胜率% | 均值Brier | 证伪数 | 情景落点分布 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for tier, s in summary.items():
        L.append(
            f"| {tier} | {s['n']} | {s['avg_ret_pct']} | {s['winners']} | "
            f"{s['win_rate_pct']} | {s['avg_brier']} | {s['falsified']} | {s['zone_distribution']} |"
        )
    L.append("")
    L.append("## 明细")
    L.append("")
    L.append("| run_id | 币 | tier | 入场价 | 现价 | 收益% | 落点区间 | Brier | 已证伪 | 判定 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(scored, key=lambda x: (x.get("run_id", ""), -(x.get("ret_pct") or 0))):
        L.append(
            f"| {r.get('run_id')} | {r.get('symbol')} | {r.get('tier')} | {r.get('entry_price')} | "
            f"{r.get('current_price')} | {r.get('ret_pct')} | {r.get('hit_zone')} | "
            f"{r.get('brier')} | {r.get('falsified')} | {r.get('verdict')} |"
        )
    L.append("")
    L.append("## 解读须知（勿跳过）")
    L.append("")
    L.append("1. 全部样本 `horizon_days=7` 未满，**verdict 一律 pending**，当前数值仅为中期快照。")
    L.append("2. Run A 与 Run C 基准相同、标的重叠 10 个，**不是独立样本**，不可叠加当 2 轮。")
    L.append("3. 尚未加入「随机等权 N 币」对照组 → 只能对比 BTC，**不能证明选币优于随机**。")
    L.append("4. `hit_zone=unknown` 表示该项原始预测未落盘三情景区间（报告只给了部分币），非 0 命中。")
    L.append("5. `outside` 表示实际价超出三情景区间全部范围 = 模型尾部盲区，须单独计数观察。")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出机读 JSON")
    ap.add_argument("--only", default=None, help="只算匹配 run_id 子串的运行")
    ap.add_argument("--write", action="store_true", help="写 score/ 目录")
    args = ap.parse_args()

    recs = load_ledger(args.only)
    scored = [score_record(r) for r in recs]
    summary = summarize(scored)
    bench = benchmark_baselines(scored)

    if args.write:
        outdir = HERE / "score"
        outdir.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        (outdir / f"score-{stamp}.json").write_text(
            json.dumps({"summary": summary, "benchmark": bench, "records": scored},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        (outdir / "scoreboard.md").write_text(render_markdown(scored, summary, bench), encoding="utf-8")
        print(f"[ok] wrote {outdir/'scoreboard.md'}")

    if args.json:
        print(json.dumps({"summary": summary, "benchmark": bench, "records": scored},
                         ensure_ascii=False, indent=1))
    else:
        print(render_markdown(scored, summary, bench))


if __name__ == "__main__":
    main()