#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regime.py — 市场状态判定（上行期 / 下行期 / 横盘期）

背景（用户 2026-09-30 提问）：能否直接用大盘数据判断当前处于哪一期？

结论：**能，但只能给"概率标签"，不能给事实。** 任何状态分类器都有滞后和误判率，
所以本模块的输出必须同时携带：判据明细、置信度、**失效条件**——否则就是误导。
这与本项目既有的「证伪条件」纪律同源，不是新造框架。

## 为什么需要它

改版前，方向判定只有一处（assemble_report.py:176）：
    direction = MA_ALIGN_MAP.get(ind.get("ma_alignment"), "震荡")
它来自**单币**的均线排列——是"这个币现在什么形态"，不是"市场现在什么状态"。
两者在实盘里经常相反（大盘在跌、某个币走独立行情），拿单币方向当市场状态会误判。

## 四类判据（为什么是四类，不是只看均线）

| 判据 | 定什么 | 为什么必须有 |
|---|---|---|
| 趋势强度 | 方向 | 均线排列 + 多周期共振 |
| 市场广度 | 强度 | 全市场多少比例站上均线——**最稳健的单一指标**，且能识别"指数新高但大多数币在跌"的假上行 |
| 位置分位 | 过热/超跌 | 决定**入场时机**，不是方向——见下方"刻意不对称" |
| 拥挤度 | 情绪过热 | 资金费率极端是拥挤的先行信号 |

**刻意不对称——位置分位不给方向，只给时机。**
pos_90=95% 在上行趋势里是"健康"（趋势延续），在横盘里才是"危险"。
把位置直接折成方向分，会让判定器在牛市顶部和熊市底部同时给出错误方向。
所以它只做小幅调制（±0.4），且 verdict 明确写"位置·非方向"。

## 诚实性约束（这几条是本模块的要点，不是装饰）

1. **不可观测就不给分。** 任一判据取不到数据 → `available: false` + note，
   权重按比例重归一化，**并按覆盖率下调置信度**。绝不用默认值糊过去。
2. **置信度上限 0.85。** 这是概率标签，不存在"高置信度的状态判定"；
   敢给 0.95 的判定器一定在某处过度拟合了。
3. **失效条件永远非空。** 没有失效条件的结论无法被证伪，那不是结论是意见。
4. **regime 是市场级，position 是标的级。** 两者不混。

## 用法

    # 联网
    regime.py --symbol BTCUSDT --out kexi_out/regime.json

    # 离线（测试用；从目录读 klines，不联网）
    regime.py --fixtures <dir> --out regime.json
    #   <dir>/BTCUSDT.json      → fetch_klines 输出的 JSON
    #   <dir>/breadth.json      → {"A": [klines...], "B": [...]}  市场广度样本

零第三方依赖，与本包其余脚本一致。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kline_utils as ku  # noqa: E402

SCHEMA = "kexi.regime/1"

# 判据权重（会按可用性重归一化）
WEIGHTS = {"trend": 0.40, "breadth": 0.30, "position": 0.15, "crowding": 0.15}
# regime 判定阈值：|score| 低于此值一律记"横盘期"（不硬猜方向）
REGIME_EPS = 0.25
# 置信度硬上限——概率标签不该有"很高置信"这一说
CONFIDENCE_CAP = 0.85
# 广度样本数下限：低于此数广度判据直接判为不可用（样本太小，统计无意义）
BREADTH_MIN_SAMPLE = 20

UP, DOWN, FLAT = "上行期", "下行期", "横盘期"


def _clamp(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


def _ma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / float(n)


def _closes(klines):
    return [float(k[4]) for k in klines]


# ── 判据 1：趋势强度 ──────────────────────────────────────────────────────
def _resample_weekly(daily):
    """日线 → 周线（按 ts 归自然周）。样本不足时返回空列表。"""
    if not daily:
        return []
    buckets = {}
    for k in daily:
        ts = int(k[0])
        # 以 UTC 周一为界：ts // WEEK 的偏移修正
        d = (ts // 86_400_000) + 4          # 1970-01-01 是周四，+4 让周一为 0
        wk = d // 7
        buckets.setdefault(wk, []).append(k)
    out = []
    for wk in sorted(buckets):
        ks = buckets[wk]
        out.append([
            ks[0][0], ks[0][1],
            max(float(x[2]) for x in ks), min(float(x[3]) for x in ks),
            float(ks[-1][4]), sum(float(x[5]) for x in ks),
        ])
    return out


def criterion_trend(klines):
    """均线排列 + 多周期共振。返回 (score, payload)。"""
    if not klines or len(klines) < 60:
        return None, {"available": False, "note": "日线样本不足 60 根（实得 %d），趋势判据不可用" % len(klines or [])}

    closes = _closes(klines)
    price = closes[-1]
    ma20, ma50, ma120 = _ma(closes, 20), _ma(closes, 50), _ma(closes, 120)

    parts, basis = [], []
    day_score = 0.0
    if ma20 and ma50 and ma120:
        if price > ma20 > ma50 > ma120:
            day_score, tag = 0.8, "多头排列（价>MA20>MA50>MA120）"
        elif price < ma20 < ma50 < ma120:
            day_score, tag = -0.8, "空头排列（价<MA20<MA50<MA120）"
        else:
            # 交织：按价格相对 MA20/MA50 的位置给一个较弱的分
            day_score = 0.25 if price > ma50 else (-0.25 if price < ma20 else 0.0)
            tag = "均线交织（无明确排列）"
        basis.append("日线 " + tag)
        parts.append(("day", day_score))
    else:
        basis.append("日线 样本不足以算 MA120，跳过日线排列")
        day_score = None

    weekly = _resample_weekly(klines)
    week_score = None
    if len(weekly) >= 20:
        wc = _closes(weekly)
        wma10, wma20 = _ma(wc, 10), _ma(wc, 20)
        if wma10 and wma20:
            if wc[-1] > wma10 > wma20:
                week_score = 0.7
            elif wc[-1] < wma10 < wma20:
                week_score = -0.7
            else:
                week_score = 0.0
            basis.append("周线 " + ("多头（价>MA10>MA20）" if week_score > 0
                                else ("空头（价<MA10<MA20）" if week_score < 0 else "周线均线纠缠")))
            parts.append(("week", week_score))
    else:
        basis.append("周线样本 %d 根（需 ≥20），周线判据缺失" % len(weekly))

    usable = [s for _, s in parts if s is not None]
    if not usable:
        return None, {"available": False, "note": "日线/周线均无法给出方向"}
    # 权重：日线 0.6 / 周线 0.4（若只有一项则占满）
    if day_score is not None and week_score is not None:
        score = 0.6 * day_score + 0.4 * week_score
    elif day_score is not None:
        score = day_score
    else:
        score = week_score

    ma_out = {"ma20": ku.rn(ma20) if ma20 else None,
              "ma50": ku.rn(ma50) if ma50 else None,
              "ma120": ku.rn(ma120) if ma120 else None}
    return _clamp(score), {
        "available": True, "score": round(_clamp(score), 3),
        "value": "日/周 %s" % ("共振向上" if score > 0.2 else ("共振向下" if score < -0.2 else "方向不明")),
        "basis": basis, "ma": ma_out,
        "note": "" if (day_score is not None and week_score is not None)
                else "仅单一周期可用，方向强度已相应降权",
    }


# ── 判据 2：市场广度 ──────────────────────────────────────────────────────
def fetch_breadth(top=50, limit=60, workers=6, delay=0.10):
    """联网取广度样本。返回 (universe, meta)。

    取数层**从 screener 借**而不是重写：`build_universe` 已经处理好了
    USDT 结尾、稳定币/包装币/商品/杠杆代币、非 ASCII 币种这些过滤。
    重写一遍必然与 screener 的宇宙定义漂移——两处筛出不同样本集，
    广度数字就没法互相印证了。

    广度只需 MA20/MA50，所以 limit 取 60 即可（screener 要 400 根是因为它
    还要算月线 MACD）。这一条让广度取数比全市场扫描快一个数量级。

    单样本失败**不中断整批**：记进 meta.failed，最后按有效样本数定判据可用性。
    """
    from concurrent.futures import ThreadPoolExecutor
    import screener as sc

    core, _, _ = sc.build_universe(top, top, delay)   # surge_rank_max=top → 不要新势候选
    meta = {"requested": top, "fetched": 0, "failed": {}, "limit": limit,
            "universe": [s for s in core[:top]]}

    def one(sym):
        raw = sc.http_get("/api/v3/klines?symbol=%s&interval=1d&limit=%d" % (sym, limit + 1))
        kl = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
              for k in raw]
        closed, _ = ku.drop_open_bar(kl, ku.D1)
        return sym, closed

    universe = {}
    # ex.map 按提交顺序返回，与 core 对齐；逐个取结果以免单个异常炸掉整批
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(one, s) for s in core]
        for s, fut in zip(core, futs):
            try:
                _, kl = fut.result()
                if kl:
                    universe[s] = kl
                    meta["fetched"] += 1
            except Exception as e:
                meta["failed"][s] = str(e)[:80]
    meta["universe_size"] = len(universe)
    return universe, meta


def criterion_breadth(universe):
    """universe: {symbol: klines}。返回 (score, payload)。

    广度是**横截面**指标——单看 BTC 永远得不到它。
    判据：站上 MA20 / MA50 的样本比例。50% 为中性，75%/25% 为强多/强空。
    """
    """universe: {symbol: klines}。返回 (score, payload)。

    广度是**横截面**指标——单看 BTC 永远得不到它。
    判据：站上 MA20 / MA50 的样本比例。50% 为中性，75%/25% 为强多/强空。
    """
    if not universe:
        return None, {"available": False, "note": "未提供广度样本（--fixtures 缺 breadth.json / 未启用 --fetch-breadth），广度判据不可用"}
    above20, above50, used = 0, 0, []
    for sym, kl in universe.items():
        if not kl or len(kl) < 21:
            continue
        c = _closes(kl)
        m20, m50 = _ma(c, 20), _ma(c, 50)
        if m20 is None:
            continue
        above20 += 1 if c[-1] > m20 else 0
        if m50 is not None:
            above50 += 1 if c[-1] > m50 else 0
        used.append(sym)
    n = len(used)
    if n < BREADTH_MIN_SAMPLE:
        return None, {"available": False,
                      "note": "广度有效样本仅 %d 个（下限 %d），样本太小不作判据"
                              % (n, BREADTH_MIN_SAMPLE)}
    p20 = above20 / float(n) * 100
    p50 = above50 / float(n) * 100
    # 50% → 0；75% → +1；25% → -1（线性，超出截断）
    score = _clamp((p20 - 50.0) / 25.0)
    return score, {
        "available": True, "score": round(score, 3),
        "value": "%.0f%% 站上 MA20 / %.0f%% 站上 MA50" % (p20, p50),
        "basis": ["有效样本 %d / 请求 %d" % (n, len(universe)),
                  "站上 MA20：%d/%d；站上 MA50：%d/%d" % (above20, n, above50, n)],
        "pct_above_ma20": round(p20, 1), "pct_above_ma50": round(p50, 1),
        # ⚠ 两条**实测得出的**局限，必须随输出一起给用户看，不能只写在文档里：
        #   ① 样本是"按成交额前 N"，会**系统性偏向赢家**——实测同一时点
        #      N=30 得 97%、N=100 得 90%。所以它测的是"龙头是否领涨"，
        #      **不是**"全市场是否参与"；识别不出"指数新高但多数币在跌"。
        #   ② 折算为 (p20-50)/25 后在两端**饱和**：90% 与 97% 都折成 1.0，
        #      强趋势中该判据不再提供额外信息（不致过度自信，因为置信度另有
        #      coverage 与 0.85 上限把关，但粒度确实丢了）。
        # 要真正度量全市场参与度，需要去掉成交额排序的样本——那是另一个
        # 取数策略（更慢、且会引入流动性偏差），本版未做。
        "note": ("广度用 MA20 折算方向，MA50 仅作展示。**局限**：样本按成交额前 N 取，"
                 "系统性偏向赢家（实测 N=30→97% vs N=100→90%），"
                 "故它反映「龙头领涨」而非「全市场参与」；且两端饱和，强趋势中无额外分辨力。"),
    }


# ── 判据 3：位置分位（刻意不给方向）────────────────────────────────────────
def criterion_position(pos):
    """pos: position.analyze_position 的输出。**只表位置，不表方向。**

    为什么不给方向：pos_90=95% 在上行趋势里是健康的（趋势延续），
    在横盘里才是危险的。折成方向分会让判定器在牛市顶部与熊市底部同时出错。
    这里只做 ±0.4 的时机调制，并在 verdict 里写明"位置·非方向"。
    """
    if not pos or pos.get("pos_90") is None:
        return None, {"available": False, "note": "缺少位置分位（pos_90），位置判据不可用"}
    p = float(pos["pos_90"])
    if p >= 90:
        score, tag = -0.4, "极高位（≥90%）：追高风险大，非方向信号"
    elif p <= 10:
        score, tag = 0.4, "极低位（≤10%）：超跌区，非方向信号"
    else:
        score, tag = 0.0, "中位区间（10%~90%）：位置无极端提示"
    return score, {
        "available": True, "score": round(score, 3),
        "value": "90日位置 %.0f%%" % p,
        "basis": [tag,
                  "区间宽度 %.1f%%" % pos.get("range_pct_90", float("nan"))
                  if pos.get("range_pct_90") is not None else "区间宽度未知"],
        "note": "**位置不是方向**：高位≠该跌、低位≠该涨，只影响入场时机。",
    }


# ── 判据 4：拥挤度（默认关闭，联网依赖）──────────────────────────────────
def criterion_crowding(funding):
    """funding: {"percentile": 0-100, "source": "..."}。未提供即不可用。"""
    if not funding or funding.get("percentile") is None:
        return None, {"available": False, "note": "未提供资金费率分位（--funding），拥挤度判据不可用"}
    pct = float(funding["percentile"])
    score = _clamp((pct - 50.0) / 45.0)
    tag = "拥挤（分位≥80，多头情绪过热）" if pct >= 80 else (
        "清淡（分位≤20，情绪低迷）" if pct <= 20 else "中性")
    return score, {
        "available": True, "score": round(score, 3),
        "value": "资金费率分位 %.0f%%" % pct,
        "basis": [tag] + ([("来源：" + funding["source"])] if funding.get("source") else []),
        "note": "拥挤度是**情绪**指标，与趋势可背离；不单独决定方向。",
    }


# ── 汇总 ──────────────────────────────────────────────────────────────────
_LABELS = {"trend": "趋势强度", "breadth": "市场广度",
           "position": "位置分位", "crowding": "拥挤度"}


def _invalidation(results, closes, ma):
    """由**实际可用的**判据生成失效条件。永远返回非空。

    ⚠ results 的值是 (score, payload) **元组**，不是 payload 本身——
    这里曾按 payload 访问而崩（单测抓到）。解包时要和 criterion_* 的返回形状对齐。
    """
    out = []
    # 只有 available 的判据才配失效条件；不可用的判据不该产生"看起来很具体的"条件
    payloads = {k: (v[1] if isinstance(v, tuple) else v) for k, v in (results or {}).items()}
    m50 = ma.get("ma50")
    m20 = ma.get("ma20")
    price = closes[-1] if closes else None
    if m50 and price:
        out.append("BTC 日线收盘跌破 MA50 %s（趋势判据失效）" % ku.rn(m50))
    if m20 and price:
        out.append("BTC 日线收盘跌破 MA20 %s 且周线同转空（趋势判据失效）" % ku.rn(m20))
    b = payloads.get("breadth")
    if b and b.get("available"):
        out.append("广度（站上MA20比例）跌破 35%%（由 %.0f%% 下行，强度判据失效）" % b.get("pct_above_ma20", 0))
    p = payloads.get("position")
    if p and p.get("available") and p.get("score", 0) != 0:
        out.append("90 日位置回到 10%~90% 中位区间（极端位置提示消失）")
    if not out:
        out.append("可用判据不足——本判定不具备可证伪基础，请补数据后重判")
    return out


def judge(klines, universe=None, pos=None, funding=None, breadth_meta=None):
    """主入口。返回 regime 判定结果 dict。"""
    closes = _closes(klines) if klines else []
    results = {
        "trend": criterion_trend(klines),
        "breadth": criterion_breadth(universe),
        "position": criterion_position(pos),
        "crowding": criterion_crowding(funding),
    }

    criteria, wsum, wused, avail_w = [], 0.0, 0.0, 0.0
    for key in ("trend", "breadth", "position", "crowding"):
        score, payload = results[key]
        w = WEIGHTS[key]
        wsum += w
        payload = dict(payload)
        payload["key"] = key
        payload["label"] = _LABELS[key]
        if payload.get("available"):
            wused += w
            avail_w += w
        criteria.append(payload)

    # 权重按可用性重归一化
    num, den = 0.0, 0.0
    for c in criteria:
        if c.get("available"):
            c["weight_used"] = round(WEIGHTS[c["key"]] * wsum / avail_w, 4) if avail_w else 0.0
            num += c["score"] * c["weight_used"]
            den += c["weight_used"]
        else:
            c["weight_used"] = 0.0
            c["score"] = None
    score = round(num / den, 3) if den else 0.0

    coverage = (avail_w / wsum) if wsum else 0.0
    if score >= REGIME_EPS:
        regime = UP
    elif score <= -REGIME_EPS:
        regime = DOWN
    else:
        regime = FLAT
    # 置信度 = 覆盖率 × 方向强度，且硬封顶 CONFIDENCE_CAP
    agree = min(1.0, abs(score) / 0.6)
    confidence = round(min(CONFIDENCE_CAP, coverage * (0.55 + 0.45 * agree)), 2)

    ma = {}
    t = results["trend"][1]
    if t.get("available"):
        ma = t.get("ma", {})

    degraded = [c["label"] for c in criteria if not c.get("available")]
    return {
        "schema": SCHEMA,
        "regime": regime,
        "score": score,
        "confidence": confidence,
        "coverage": round(coverage, 2),
        "criteria": criteria,
        "invalidation": _invalidation(results, closes, ma),
        "degraded": degraded,
        "data_quality": {
            "bars": len(klines or []),
            "breadth_universe": len(universe or {}),
            "breadth_fetch": breadth_meta or None,
            "price": ku.rn(closes[-1]) if closes else None,
        },
        "note": ("regime 是**概率标签不是事实**。可用判据 %d/%d 类，权重覆盖 %.0f%%；"
                 "置信度封顶 %.2f 是刻意为之——不存在高置信度的市场状态判定。"
                 % (len(criteria) - len(degraded), len(criteria), coverage * 100, CONFIDENCE_CAP)),
    }


# ── CLI ───────────────────────────────────────────────────────────────────
def _load(p, key="klines"):
    """读 K 线 JSON。**必须容忍裸数组**。

    本包其它脚本的 _load 写的是 `d.get(key) or d.get("data") or (d if isinstance(d, list) else None)`，
    对 dict 输入没问题，但**遇到裸数组会在 d.get 上抛 AttributeError**——
    用户直接传一个 klines 数组、或离线夹具写成数组时就崩（单测实测抓到）。
    排列判断必须放在调用 .get 之前。
    """
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, list):
        return d                      # 裸 K 线数组
    if isinstance(d, dict):
        return d.get(key) or d.get("data") or None
    return None


def main():
    import argparse
    ap = argparse.ArgumentParser(description="市场状态判定（上行期/下行期/横盘期）")
    ap.add_argument("--klines", default=None, help="BTC 日线 JSON（fetch_klines 输出）")
    ap.add_argument("--breadth", default=None, help="市场广度样本 JSON：{symbol: klines}")
    ap.add_argument("--position", default=None, help="position.py 输出 JSON（取 pos_90）")
    ap.add_argument("--funding", default=None, help="资金费率分位 JSON：{percentile, source}")
    ap.add_argument("--fixtures", default=None,
                    help="离线夹具目录：<BTCUSDT.json> + [breadth.json]，不联网（供测试）")
    ap.add_argument("--fetch-breadth", type=int, default=0, metavar="N",
                    help="联网抓取广度样本：按成交额取前 N 个 USDT 交易对（建议 30-80；0=不抓）")
    ap.add_argument("--fetch-workers", type=int, default=6, help="广度并发取数线程数（默认 6）")
    ap.add_argument("--fetch-btc", default=None,
                    help="联网抓 BTC 日线到该路径（不给则必须用 --klines/--fixtures）")
    ap.add_argument("--out", default=None, help="输出 JSON 路径（默认 kexi_out/regime.json）")
    args = ap.parse_args()

    klines, universe, pos, funding = None, None, None, None
    breadth_meta = None
    if args.fixtures:
        d = args.fixtures
        p1 = os.path.join(d, "BTCUSDT.json")
        if not os.path.exists(p1):
            print(json.dumps({"ok": False, "error": "夹具缺少 %s" % p1}, ensure_ascii=False))
            return 1
        klines = _load(p1)
        p2 = os.path.join(d, "breadth.json")
        if os.path.exists(p2):
            with open(p2, encoding="utf-8") as f:
                universe = json.load(f)
    else:
        # 联网模式：BTC 也要抓（除非已给了本地 --klines）
        if args.klines and os.path.exists(args.klines):
            klines = _load(args.klines)
        elif args.fetch_btc:
            import screener as sc
            raw = sc.http_get("/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=301")
            klines = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]),
                       float(k[4]), float(k[5])] for k in raw]
        else:
            print(json.dumps({"ok": False,
                              "error": "需要 --klines / --fetch-btc / --fixtures 之一"},
                             ensure_ascii=False))
            return 1
        if args.breadth and os.path.exists(args.breadth):
            with open(args.breadth, encoding="utf-8") as f:
                universe = json.load(f)
        elif args.fetch_breadth and args.fetch_breadth > 0:
            universe, breadth_meta = fetch_breadth(args.fetch_breadth,
                                                   workers=args.fetch_workers)
        if args.position and os.path.exists(args.position):
            with open(args.position, encoding="utf-8") as f:
                pos = json.load(f)
        if args.funding and os.path.exists(args.funding):
            with open(args.funding, encoding="utf-8") as f:
                funding = json.load(f)

    if klines:
        klines, _ = ku.drop_open_bar(klines)

    res = judge(klines, universe=universe, pos=pos, funding=funding,
                breadth_meta=breadth_meta)
    res["generated_at"] = None  # 由调用方/文件时间决定，保持可复现（测试断言用）

    out = args.out or os.path.join("kexi_out", "regime.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": True,
        "regime": res["regime"],
        "confidence": res["confidence"],
        "score": res["score"],
        "coverage": res["coverage"],
        "degraded": res["degraded"],
        "breadth": (breadth_meta or {}).get("universe_size"),
        "digest": "市场状态判定：%s（置信度 %.2f，判据覆盖 %.0f%%）%s"
                  % (res["regime"], res["confidence"], res["coverage"] * 100,
                     ("；缺 " + "、".join(res["degraded"])) if res["degraded"] else ""),
        "out_files": [out],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
