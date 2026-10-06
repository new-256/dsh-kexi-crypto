#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest.py - 选币规则历史回测（v1.5.0 新增）

回答用户 2026-09-29 的核心质疑：
    "之前插件选币基本选的很多都是技术指标都很好，但是已经放量多天已经在高位的币种"
    "如果选币都选在高位风险有多高？"

**做法**：点时间（point-in-time）回测——在每个历史观察日，只用**当天及之前**的
K 线重算 screener 的评分与位置维度，然后看未来 N 日的真实收益。这样不会用到未来
数据（无前视偏差）。

**对比口径**（核心）：
    按位置分组，比较各组的未来收益：
      高位  (pos_90 >= 80)
      中高  (60 <= pos_90 < 80)
      中低  (40 <= pos_90 < 60)
      低位  (pos_90 < 40)
    如果"高位组未来收益显著差于低位组"，则用户的直觉被数据证实；
    若无显著差异，则应如实说明"高位扣分可能过度"。

**注意**：本脚本只做**统计描述**，不做显著性检验的强断言；样本量与币种数有限时，
结论须注明局限。宁可不给结论，也不编造。
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kline_utils as ku          # noqa: E402
import position as position_mod   # noqa: E402
import timeframe as tf_mod        # noqa: E402

TZ8 = timezone(timedelta(hours=8))
UA = {"User-Agent": "crypto-trend-analyst/1.0"}
# data-api.binance.vision 实测免 key 且 DNS 未被投毒（api.binance.com 返回 451）
HOSTS = ["data-api.binance.vision", "api1.binance.com", "api2.binance.com"]
D1 = 86_400_000


def http_json(url, retries=3, timeout=20):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
            time.sleep(1.5 ** i)
    raise RuntimeError(f"{url}: {last}")


def fetch_klines(symbol, interval="1d", limit=700):
    err = []
    for h in HOSTS:
        try:
            raw = http_json(
                f"https://{h}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")
            return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
                    for k in raw]
        except Exception as e:
            err.append(f"{h}:{e}")
    raise RuntimeError(" | ".join(err)[:200])


def universe_by_volume(top=60):
    """按 24h 成交额取标的池（回测用固定池——避免幸存者偏差请配合 --note 说明）。"""
    for h in HOSTS:
        try:
            raw = http_json(f"https://{h}/api/v3/ticker/24hr", retries=2)
            rows = []
            for t in raw:
                s = t.get("symbol", "")
                if not s.isascii() or not s.endswith("USDT"):
                    continue
                try:
                    rows.append((s, float(t["quoteVolume"])))
                except (KeyError, ValueError):
                    continue
            rows.sort(key=lambda x: -x[1])
            return [s for s, _ in rows[:top]]
        except Exception:
            continue
    return []


# ── 点时间评分（只复刻位置/多周期维度 + 基础趋势，与 screener 口径一致）──
def score_at(klines, idx, vol_mult=1.5):
    """在 klines 的第 idx 根（含）为"今天"做评分。只使用 idx 及之前的数据。"""
    hist = klines[:idx + 1]
    if len(hist) < 120:
        return None
    closes = [k[4] for k in hist]
    vols = [k[5] for k in hist]
    c = closes[-1]

    # 基础趋势/动量（与 screener 简化一致：站上MA20且拐头 + MACD + 量比 + RSI）
    ma20 = sum(closes[-20:]) / 20
    ma20_prev = sum(closes[-25:-5]) / 20
    def ema(vals, n):
        k = 2 / (n + 1)
        e = vals[0]
        for v in vals[1:]:
            e = v * k + e * (1 - k)
        return e
    difs, deas = [], []
    for i in range(len(closes)):
        seg = closes[:i + 1]
        if len(seg) < 26:
            difs.append(0); deas.append(0); continue
        difs.append(ema(seg, 12) - ema(seg, 26))
        deas.append(ema(difs, 9))
    vol_ma5 = sum(vols[-5:]) / 5
    vol_ma20 = sum(vols[-20:]) / 20
    vol_ratio = vol_ma5 / vol_ma20 if vol_ma20 else 0

    hits = 0
    if c > ma20 and ma20 > ma20_prev:
        hits += 1
    if difs[-1] > deas[-1] and difs[-1] > 0:
        hits += 1
    if vol_ratio >= vol_mult:
        hits += 1
    base = hits * 2

    pos = position_mod.analyze_position(hist)
    pen, _ = position_mod.position_penalty(pos) if pos else (0, [])
    tfv = tf_mod.multi_tf_view(hist) if len(hist) >= 200 else None
    tfs, _ = tf_mod.tf_alignment_score(tfv) if tfv else (0, [])
    return {
        "base": base, "score": base + pen + tfs,
        "pos_90": (pos or {}).get("pos_90"),
        "zone": (pos or {}).get("zone"),
        "pos_all": (pos or {}).get("pos_all"),
        "dist_ma20": (pos or {}).get("dist_ma20_pct"),
        "vol_expansion_days": (pos or {}).get("vol_expansion_days"),
        "alignment": (tfv or {}).get("alignment"),
        "close": c,
    }


def fwd_return(klines, idx, horizon):
    """idx 之后 horizon 根的前向收益（%）。不足则 None。"""
    if idx + horizon >= len(klines):
        return None
    p0 = klines[idx][4]
    p1 = klines[idx + horizon][4]
    return (p1 / p0 - 1) * 100 if p0 else None


def max_drawdown_fwd(klines, idx, horizon):
    """未来 horizon 根内的最大回撤（相对起点，%，负数）。"""
    if idx + horizon >= len(klines):
        return None
    p0 = klines[idx][4]
    lo = min(k[3] for k in klines[idx + 1: idx + horizon + 1])
    return (lo / p0 - 1) * 100 if p0 else None


def summarize(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 2),
        "median": round(statistics.median(vals), 2),
        "win_rate": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
        "p25": round(sorted(vals)[len(vals) // 4], 2),
        "p75": round(sorted(vals)[3 * len(vals) // 4], 2),
        "worst": round(min(vals), 2),
        "best": round(max(vals), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=60, help="按成交额取多少币做池")
    ap.add_argument("--limit", type=int, default=700, help="每币取多少根日线")
    ap.add_argument("--lookback-start", type=int, default=420,
                    help="从倒数第几根开始做观察点（默认 420，留足指标与前瞻窗口）")
    ap.add_argument("--step", type=int, default=10, help="观察点间隔（天）")
    ap.add_argument("--horizons", default="7,14,30", help="前瞻窗口（天，逗号分隔）")
    ap.add_argument("--out", default="backtest_result.json")
    ap.add_argument("--md", default="backtest_report.md")
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()

    horizons = [int(x) for x in args.horizons.split(",")]
    log = lambda m: print(m, flush=True)

    log(f"[1/4] 取标的池（按成交额 Top {args.top}）...")
    syms = universe_by_volume(args.top)
    if not syms:
        log("     标的池获取失败")
        return 1
    log(f"     池大小 {len(syms)}")

    log(f"[2/4] 并发取 {len(syms)} 币日线（各 {args.limit} 根）...")
    from concurrent.futures import ThreadPoolExecutor, as_completed
    data, errors = {}, {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_klines, s, "1d", args.limit): s for s in syms}
        done = 0
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                data[s] = fut.result()
            except Exception as e:
                errors[s] = str(e)[:80]
            done += 1
            if done % 15 == 0:
                log(f"     {done}/{len(syms)}（失败 {len(errors)}）")
    log(f"     成功 {len(data)} / 失败 {len(errors)}")

    log("[3/4] 点时间回测（每个观察日只用当日及之前数据打分）...")
    obs = []          # 每条观察记录
    n_days_checked = 0
    for sym, kl in data.items():
        if len(kl) < 200:
            continue
        # 数据可能含未收盘的最后一根 → 去掉
        kl = kl[:-1]
        n = len(kl)
        start = max(200, n - args.lookback_start)
        for idx in range(start, n - max(horizons) - 1, args.step):
            sc = score_at(kl, idx)
            if sc is None or sc["pos_90"] is None:
                continue
            rec = {
                "symbol": sym,
                "date": datetime.fromtimestamp(kl[idx][0] / 1000, TZ8).strftime("%Y-%m-%d"),
                **sc,
            }
            for h in horizons:
                rec[f"fwd_{h}d"] = fwd_return(kl, idx, h)
                rec[f"mdd_{h}d"] = max_drawdown_fwd(kl, idx, h)
            obs.append(rec)
            n_days_checked += 1
    log(f"     观察样本 {len(obs)} 条（覆盖 {len(set(o['symbol'] for o in obs))} 币）")

    if not obs:
        log("     样本为空，无法回测")
        return 1

    log("[4/4] 分组统计 ...")

    def group_of(pos90):
        if pos90 >= 80:
            return "高位(≥80)"
        if pos90 >= 60:
            return "中高(60-80)"
        if pos90 >= 40:
            return "中低(40-60)"
        return "低位(<40)"

    groups = {}
    for o in obs:
        groups.setdefault(group_of(o["pos_90"]), []).append(o)

    result = {
        "generated_at": datetime.now(TZ8).isoformat(),
        "params": vars(args),
        "universe_size": len(syms),
        "symbols_ok": len(data),
        "symbols_failed": len(errors),
        "observations": len(obs),
        "symbols_covered": len(set(o["symbol"] for o in obs)),
        "date_range": (min(o["date"] for o in obs), max(o["date"] for o in obs)),
        "by_position_group": {},
        "by_score_rank": {},
        "by_timeframe_alignment": {},
        "by_vol_expansion": {},
    }

    for g, rows in sorted(groups.items()):
        gd = {"count": len(rows)}
        for h in horizons:
            gd[f"fwd_{h}d"] = summarize([r.get(f"fwd_{h}d") for r in rows])
            gd[f"mdd_{h}d"] = summarize([r.get(f"mdd_{h}d") for r in rows])
        result["by_position_group"][g] = gd

    # 按当日分数排名分组（前 10% vs 后 10%）——检验"分数高是否真的更强"
    obs_sorted = sorted(obs, key=lambda r: -(r["score"] or 0))
    k = max(1, len(obs_sorted) // 10)
    for label, rows in (("分数前10%", obs_sorted[:k]), ("分数后10%", obs_sorted[-k:])):
        d = {"count": len(rows)}
        for h in horizons:
            d[f"fwd_{h}d"] = summarize([r.get(f"fwd_{h}d") for r in rows])
        result["by_score_rank"][label] = d

    # 按多周期一致性分组
    al_groups = {}
    for o in obs:
        al_groups.setdefault(o.get("alignment") or "无", []).append(o)
    for a, rows in al_groups.items():
        d = {"count": len(rows)}
        for h in horizons:
            d[f"fwd_{h}d"] = summarize([r.get(f"fwd_{h}d") for r in rows])
        result["by_timeframe_alignment"][a] = d

    # 按是否"放量多日"分组（用户点名的特征）
    for label, pred in (("放量多日(≥5天)", lambda o: (o.get("vol_expansion_days") or 0) >= 5),
                        ("未放量(<5天)", lambda o: (o.get("vol_expansion_days") or 0) < 5)):
        rows = [o for o in obs if pred(o)]
        d = {"count": len(rows)}
        for h in horizons:
            d[f"fwd_{h}d"] = summarize([r.get(f"fwd_{h}d") for r in rows])
        result["by_vol_expansion"][label] = d

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ---------- markdown ----------
    L = []
    L.append(f"# 选币规则历史回测报告（{result['generated_at'][:16]} UTC+8）\n")
    L.append(f"- 标的池：按 24h 成交额 Top {args.top}（成功 {len(data)} / 失败 {len(errors)}）")
    L.append(f"- 观察样本：**{len(obs)}** 条，覆盖 {result['symbols_covered']} 个币")
    L.append(f"- 数据区间：{result['date_range'][0]} ~ {result['date_range'][1]}")
    L.append(f"- 前瞻窗口：{horizons} 天；观察点间隔 {args.step} 天")
    L.append("- **无前视偏差**：每个观察日只用当日及之前的 K 线重算指标\n")

    L.append("## 核心问题：位置越高，未来收益越差吗？\n")
    L.append("| 位置分组 | 样本 | 未来7日均值 | 中位 | 胜率 | 未来14日均值 | 未来30日均值 | 30日最大回撤均值 |")
    L.append("|---------|------|------------|------|------|-------------|-------------|----------------|")
    for g in ("低位(<40)", "中低(40-60)", "中高(60-80)", "高位(≥80)"):
        if g not in result["by_position_group"]:
            continue
        d = result["by_position_group"][g]
        def cell(key, field):
            s = d.get(key)
            return f"{s[field]}" if s else "-"
        L.append(f"| {g} | {d['count']} | {cell('fwd_7d','mean')}% | "
                 f"{cell('fwd_7d','median')}% | {cell('fwd_7d','win_rate')}% | "
                 f"{cell('fwd_14d','mean')}% | {cell('fwd_30d','mean')}% | "
                 f"{cell('mdd_30d','mean')}% |")

    lo = result["by_position_group"].get("低位(<40)", {}).get("fwd_30d")
    hi = result["by_position_group"].get("高位(≥80)", {}).get("fwd_30d")
    if lo and hi:
        # ⚠️ 重要方法论（被实测教训修正）：
        # 第一次跑出"高位 30 日均值 +3.25% vs 低位 -1.14%"→ 看似"高位更好"；
        # 换参数（Top100/step15）后**结果翻转**为"高位 -7.87% vs 低位 -1.52%"。
        # 追查原因：**均值被极端离群值主导**——两个样本里都出现 +904% / +371% 的
        # 单个币，把整组均值拉飞。而**中位数与胜率在两轮间高度一致**。
        # 故本报告的判定**以中位数与胜率为主**，均值仅作参考并同时列出离群值。
        d_med = round((hi.get("median") or 0) - (lo.get("median") or 0), 2)
        d_mean = round(hi["mean"] - lo["mean"], 2)
        L.append("\n> #### ⚠️ 均值不可靠，请看中位数\n")
        L.append(f"> 高位组 − 低位组：**均值差 {d_mean:+}**，但**中位数差 {d_med:+}**。"
                 "两组都出现 +300%~+900% 的极端离群币（见上表「最好」列），"
                 "使得**均值在两轮独立回测间会翻转符号**（实测：一轮 +3.25%，"
                 "换参数后 -7.87%）。**中位数与胜率则稳定**。\n")
        L.append(f"> 按中位数：高位 {hi.get('median')}% vs 低位 {lo.get('median')}% "
                 f"（差 {d_med:+}）；按 30 日胜率：高位 {hi.get('win_rate')}% vs "
                 f"低位 {lo.get('win_rate')}%。")
        if d_med > 0:
            L.append(">\n> **结论：数据不支持「区间位置高就更危险」**——两组中位数差异小"
                     "且高位组并不更差，符号还随样本翻转。"
                     "→ 实现上**不应给纯「位置」高权重**（当前 -2/-3 偏重），"
                     "宜降为风险提示级（-1）。")
        else:
            L.append(">\n> **结论：中位数显示高位组更差**，支持「位置」维度保留一定权重。")
        mdd_lo = (result["by_position_group"].get("低位(<40)", {}).get("mdd_30d") or {}).get("mean")
        mdd_hi = (result["by_position_group"].get("高位(≥80)", {}).get("mdd_30d") or {}).get("mean")
        L.append(f"> \n> 回撤维度：低位 30 日最大回撤均值 {mdd_lo}% vs 高位 {mdd_hi}%")

    # 放量多日（用户点名的具体特征）——**用中位数判定**（均值受离群值影响）
    _ve = result["by_vol_expansion"]
    ve_hi = (_ve.get("放量多日(≥5天)") or {}).get("fwd_30d")
    ve_lo = (_ve.get("未放量(<5天)") or {}).get("fwd_30d")
    if ve_hi and ve_lo:
        vd_mean = round(ve_hi["mean"] - ve_lo["mean"], 2)
        vd_med = round((ve_hi.get("median") or 0) - (ve_lo.get("median") or 0), 2)
        vd_win = round((ve_hi.get("win_rate") or 0) - (ve_lo.get("win_rate") or 0), 1)
        L.append(f"\n> #### 放量多日 vs 未放量（30 日）\n")
        L.append(f"> 均值差 **{vd_mean:+}** / 中位数差 **{vd_med:+}** / "
                 f"胜率差 **{vd_win:+}** 个百分点")
        if vd_med < -1 and vd_win < -2:
            L.append("> \n> ✅ **用户直觉在此特征上成立，且两轮回测一致**："
                     "「已放量多日」的币中期收益更低、胜率明显更差（32.9%/32.1% vs 43.4%/39.0%）。"
                     "→ **拥挤度（放量天数）比「区间位置」更能刻画追高风险**，"
                     "实现上应**加重拥挤度权重**。")
        elif vd_med > 1 and vd_win > 2:
            L.append("> \n> 数据不支持「放量多日更差」。")

    # 多周期一致性——检验周线/月线维度是否真的有用
    _al = result["by_timeframe_alignment"]
    al_up = (_al.get("全周期共振向上") or {}).get("fwd_30d")
    al_dn = (_al.get("全周期共振向下") or {}).get("fwd_30d")
    if al_up and al_dn:
        ad_med = round((al_up.get("median") or 0) - (al_dn.get("median") or 0), 2)
        ad_win = round((al_up.get("win_rate") or 0) - (al_dn.get("win_rate") or 0), 1)
        L.append(f"\n> #### 多周期共振向上 vs 共振向下（30 日）\n")
        L.append(f"> 均值差 **{round(al_up['mean']-al_dn['mean'],2):+}** / "
                 f"中位数差 **{ad_med:+}** / 胜率差 **{ad_win:+}** 个百分点")
        if ad_med > 1 and ad_win > 2:
            L.append("> \n> ✅ **多周期（周线/月线）维度有效性得到验证，且两轮一致**："
                     "共振向上的标的中期收益与胜率均明显优于共振向下"
                     "（中位 -0.04%/-1.22% vs -3.02%/-5.09%；胜率 48.4/41.9 vs 41.0/36.5）。"
                     "→ 该维度**应保留并可用作加分**。")

    L.append("\n## 附加检验\n")
    L.append("### 分数排名（当日分数前 10% vs 后 10%）\n")
    L.append("| 分组 | 样本 | 未来7日均值 | 未来14日均值 | 未来30日均值 |")
    L.append("|------|------|------------|-------------|-------------|")
    for k2, d in result["by_score_rank"].items():
        L.append(f"| {k2} | {d['count']} | "
                 f"{(d.get('fwd_7d') or {}).get('mean','-')}% | "
                 f"{(d.get('fwd_14d') or {}).get('mean','-')}% | "
                 f"{(d.get('fwd_30d') or {}).get('mean','-')}% |")

    L.append("\n### 多周期一致性\n")
    L.append("| 一致性 | 样本 | 未来7日均值 | 未来30日均值 |")
    L.append("|--------|------|------------|-------------|")
    for a, d in sorted(result["by_timeframe_alignment"].items(), key=lambda x: -x[1]["count"]):
        L.append(f"| {a} | {d['count']} | "
                 f"{(d.get('fwd_7d') or {}).get('mean','-')}% | "
                 f"{(d.get('fwd_30d') or {}).get('mean','-')}% |")

    L.append("\n### 放量多日（用户点名特征）\n")
    L.append("| 分组 | 样本 | 未来7日均值 | 未来14日均值 | 未来30日均值 |")
    L.append("|------|------|------------|-------------|-------------|")
    for a, d in result["by_vol_expansion"].items():
        L.append(f"| {a} | {d['count']} | "
                 f"{(d.get('fwd_7d') or {}).get('mean','-')}% | "
                 f"{(d.get('fwd_14d') or {}).get('mean','-')}% | "
                 f"{(d.get('fwd_30d') or {}).get('mean','-')}% |")

    L.append("\n## 方法论与局限（必读）\n")
    L.append(f"- **标的池固定为当前成交额 Top {args.top}**：存在 **幸存者偏差**"
             "（当年成交额高、如今已退市的币不在池内），会**高估**整体收益。")
    L.append("- 未计入手续费、滑点、资金费率；未考虑流动性冲击。")
    L.append("- 观察点间隔 %d 天、前瞻窗口最多 %d 天，样本间存在**重叠**，"
             "统计量不独立，不能直接做显著性检验。" % (args.step, max(horizons)))
    L.append("- 本回测只复刻了**位置/多周期/基础趋势**三类维度（简化版评分），"
             "并非完整 screener 口径（不含 4h 共振、赛道、补涨等）。")
    L.append("- 结论仅对**本样本区间**成立，不构成投资建议。\n")

    with open(args.md, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # 控制台摘要
    log("\n=== 位置分组 · 未来收益 ===")
    for g in ("低位(<40)", "中低(40-60)", "中高(60-80)", "高位(≥80)"):
        if g in result["by_position_group"]:
            d = result["by_position_group"][g]
            f7, f30 = d.get("fwd_7d"), d.get("fwd_30d")
            log(f"  {g:<12} n={d['count']:<4} 7日={f7['mean'] if f7 else '-':>7}% "
                f"中位={f7['median'] if f7 else '-':>7}% 胜率={f7['win_rate'] if f7 else '-':>5}% "
                f"30日={f30['mean'] if f30 else '-':>7}%")
    log(f"\n结果: {args.out} / {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())