#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regime_log.py — 市场状态判定的**对账台账**（v1.7.0）

## 为什么需要它

regime.py 给出的是**概率标签**。概率标签必须能被检验，否则它和随机数没有区别。
所以每次判定都要留痕，事后用**当时说的话**去对**后来实际发生的事**。

这是全链条里最容易被跳过、也最不该跳过的一环：
没有它，regime 判定器就是个永远无法验证的黑箱。

## 与 prediction_log.py 的分工（不要合并）

| | prediction_log.py | regime_log.py |
|---|---|---|
| 对象 | **单币**（symbol） | **大盘**（BTC/市场） |
| 预测什么 | 三情景区间与概率 | 市场**状态**（上行/下行/横盘） |
| 判对与否 | 落进/没落进预测区间 | 事后方向与判定是否一致 |

两者口径不同：同一时刻 BTC 的单币研判可以是「震荡」而市场状态是「上行期」
（实测就是如此）。混在一张表里会导致对账结论自相矛盾。

## 诚实性设计（本工具的重点）

1. **只记当时说的，不重算**。record 只读 regime.json 的既有字段。
2. **record 时刻不知道结果**——这是对账成立的前提。
3. **reconcile 用固定的、可复现的口径**（见 `MATCH`），并在输出里写明该口径。
4. **不设"准确率"单一数字就下结论**：分 regime 统计样本数，
   样本 < 5 时显式标注"样本不足"。宁可显示"还不知道"，也不显示一个
   看起来有意义的百分比。

## 用法

    regime_log.py record   --regime kexi_out/regime.json --out kexi_out/regime_log.jsonl
    regime_log.py reconcile --log kexi_out/regime_log.jsonl --horizon 7 \
                            --btc-klines kexi_out/BTCUSDT_1d_klines.json \
                            --out kexi_out/regime_reconcile.json
"""

import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta

LOG_SCHEMA = "kexi.regime_log/1"
RECON_SCHEMA = "kexi.regime_reconcile/1"
TZ8 = timezone(timedelta(hours=8))

# 对账口径：固定、可复现、**先定后看**（看到结果再挑口径是对账的经典自欺）
MATCH = {
    "上行期": "forward_return > +BAND",
    "下行期": "forward_return < -BAND",
    "横盘期": "abs(forward_return) <= BAND",
}
DEFAULT_BAND = 0.03      # ±3%（7 日）
MIN_SAMPLE = 5           # 少于这个数不报命中率


def _now():
    return datetime.now(TZ8)


def _ts_ms(dt):
    return int(dt.timestamp() * 1000)


# ── record ────────────────────────────────────────────────────────────────
def cmd_record(args):
    if not os.path.exists(args.regime):
        print(json.dumps({"ok": False, "error": "找不到 %s" % args.regime}, ensure_ascii=False))
        return 1
    with open(args.regime, encoding="utf-8") as f:
        rg = json.load(f)
    regime = rg.get("regime")
    score = rg.get("score")
    if not regime or not isinstance(score, (int, float)):
        # 没有可检验的判定就不留痕——留一条残缺记录只会污染对账
        print(json.dumps({"ok": False, "error": "regime.json 缺 regime 或 score，无法留痕"},
                         ensure_ascii=False))
        return 1

    now = _now()
    ent = {
        "schema": LOG_SCHEMA,
        "ts_ms": _ts_ms(now),
        "as_of": now.isoformat(),
        "regime": regime,
        "score": float(score),
        "confidence": rg.get("confidence"),
        "coverage": rg.get("coverage"),
        "close": (rg.get("data_quality") or {}).get("price"),
        # 只抄判据的标签与取值，判据全文仍在 regime.json 里
        "criteria": [{"label": c.get("label"),
                      "value": c.get("value"),
                      "available": c.get("available")}
                     for c in (rg.get("criteria") or [])],
        "degraded": rg.get("degraded") or [],
        "invalidation": rg.get("invalidation") or [],
        "horizon_days": int(args.horizon),
        "status": "pending",
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "a", encoding="utf-8") as f:
        f.write(json.dumps(ent, ensure_ascii=False) + "\n")
    print(json.dumps({
        "ok": True, "recorded": ent["ts_ms"], "regime": regime,
        "horizon_days": ent["horizon_days"],
        "digest": "已留痕：%s（score %.3f，%d 天后对账）" % (regime, float(score), ent["horizon_days"]),
        "out_files": [args.out],
    }, ensure_ascii=False))
    return 0


# ── reconcile ─────────────────────────────────────────────────────────────
def _load_klines(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        return d.get("klines") or d.get("data") or []
    return []


def _forward_return(klines, at_ts_ms, horizon_days):
    """从 at_ts_ms 起 horizon_days 的涨跌幅。

    ⚠ 取**时间戳 ≥ at_ts** 的第一根作为起点。取错一根（用了 at_ts 之前那根）
    会凭空多算/少算一整天涨跌——这类偏差在看结果时才被发现，已经污染结论了。
    数据不够 horizon_days 时返回 None（**不用不足的窗口凑一个数**）。
    """
    fut = [k for k in klines if int(k[0]) >= at_ts_ms]
    if len(fut) < horizon_days + 1:
        return None
    a = float(fut[0][4])
    b = float(fut[horizon_days][4])
    if a <= 0:
        return None
    return (b - a) / a


def _match(regime, r, band):
    if r is None:
        return None
    if regime == "上行期":
        return r > band
    if regime == "下行期":
        return r < -band
    return abs(r) <= band


def cmd_reconcile(args):
    log_path = args.log
    if not os.path.exists(log_path):
        print(json.dumps({"ok": False, "error": "台账为空或不存在：%s（先跑 record）" % log_path},
                         ensure_ascii=False))
        return 1
    band = float(args.band)
    horizon = int(args.horizon) if args.horizon else None

    entries = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue                    # 坏行跳过，不让整份台账作废

    klines = _load_klines(args.btc_klines) if (args.btc_klines and
                                               os.path.exists(args.btc_klines)) else []
    now_ms = _ts_ms(_now())
    items, pending = [], 0
    for e in entries:
        h = horizon or int(e.get("horizon_days") or 7)
        due = int(e.get("ts_ms", 0)) + h * 86_400_000
        if due > now_ms:
            pending += 1
            items.append({"ts": e.get("as_of"), "regime": e.get("regime"),
                          "status": "pending", "due": due})
            continue
        r = _forward_return(klines, int(e.get("ts_ms", 0)), h) if klines else None
        hit = _match(e.get("regime"), r, band)
        items.append({
            "ts": e.get("as_of"), "regime": e.get("regime"),
            "score": e.get("score"), "confidence": e.get("confidence"),
            "horizon_days": h,
            "forward_return": round(r, 4) if r is not None else None,
            "hit": hit,
            "status": "scored" if hit is not None else "no_data",
        })

    scored = [i for i in items if i.get("status") == "scored"]
    by_regime = {}
    for i in scored:
        b = by_regime.setdefault(i["regime"], {"n": 0, "hit": 0, "returns": []})
        b["n"] += 1
        b["hit"] += 1 if i["hit"] else 0
        if i["forward_return"] is not None:
            b["returns"].append(i["forward_return"])
    summary = {}
    for k, v in by_regime.items():
        rs = v["returns"]
        summary[k] = {
            "n": v["n"],
            "hit_rate": round(v["hit"] / v["n"], 3) if v["n"] else None,
            "mean_return": round(sum(rs) / len(rs), 4) if rs else None,
            "verdict": ("样本不足（n<%d），尚不能判断准不准" % MIN_SAMPLE) if v["n"] < MIN_SAMPLE
                       else ("命中率 %d%%" % round(v["hit"] / v["n"] * 100)),
        }

    out = {
        "schema": RECON_SCHEMA,
        "generated_at": _now().isoformat(),
        "total": len(entries),
        "scored": len(scored),
        "pending": pending,
        "no_data": sum(1 for i in items if i.get("status") == "no_data"),
        "match_rule": {k: v.replace("BAND", "%.4f" % band) for k, v in MATCH.items()},
        "match_note": ("口径**先定后看**：看到结果再挑口径是对账的经典自欺。"
                       "band=±%.1f%%，horizon=%s 天。样本少于 %d 条时不给命中率结论。"
                       % (band * 100, horizon or "各自记录值", MIN_SAMPLE)),
        "summary_by_regime": summary,
        "items": items,
    }
    res_out = args.out or os.path.join("kexi_out", "regime_reconcile.json")
    os.makedirs(os.path.dirname(os.path.abspath(res_out)) or ".", exist_ok=True)
    with open(res_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    bits = []
    for k, v in summary.items():
        bits.append("%s %s（n=%d）" % (k, v["verdict"], v["n"]))
    print(json.dumps({
        "ok": True, "total": out["total"], "scored": out["scored"],
        "pending": out["pending"], "no_data": out["no_data"],
        "summary": summary,
        "digest": ("regime 对账：可评分 %d/%d，待定 %d，无数据 %d。%s"
                   % (out["scored"], out["total"], pending, out["no_data"],
                      ("；".join(bits)) if bits else "尚无可评分记录")),
        "out_files": [res_out],
    }, ensure_ascii=False))
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="regime 判定的对账台账")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("record", help="把一次 regime 判定追加进台账")
    p1.add_argument("--regime", required=True, help="regime.py 输出 JSON")
    p1.add_argument("--horizon", type=int, default=7, help="对账周期（天，默认 7）")
    p1.add_argument("--out", default=os.path.join("kexi_out", "regime_log.jsonl"))
    p1.set_defaults(fn=cmd_record)

    p2 = sub.add_parser("reconcile", help="用实际走势给历史判定打分")
    p2.add_argument("--log", default=os.path.join("kexi_out", "regime_log.jsonl"))
    p2.add_argument("--horizon", type=int, default=None, help="统一对账周期（不给则用各自记录值）")
    p2.add_argument("--band", type=float, default=DEFAULT_BAND, help="判定为对的涨跌带（默认 ±3%%）")
    p2.add_argument("--btc-klines", default=None, help="BTC 日线 JSON（不给则全部 no_data）")
    p2.add_argument("--out", default=None)
    p2.set_defaults(fn=cmd_reconcile)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
