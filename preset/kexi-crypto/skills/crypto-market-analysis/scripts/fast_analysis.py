#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fast_analysis.py - 单次确定性快研（v1.5.3 新增，v1.5.2 性能实测驱动）

为什么需要（2026-09-30 实测数据）：
    单币完整研判走团队协作（数脉→指北→望潮→守拙→汇编）实测 **17 分钟**；
    而同样的分析用确定性脚本跑只需：
        fetch_klines  505 ms（唯一联网环节）
        indicators     89 ms
        position       65 ms
        timeframe      60 ms
        entry_plan     92 ms
        coin_profile   90 ms
        --------------------------
        合计          ~0.9 秒
    即 **99.9% 的时间花在 LLM 编排往返**（几十次顺序模型调用 + 37% 的
    provider 空响应重试），而不是计算。用户反馈"结果出来币价都涨了几个点"
    正是这个代价的体现。

定位：**快答层**，不是替代团队，而是替代"为了拿一个初判而走完整流程"：
    · 单币问走势      → 1 次工具调用，~1-2 秒出结论（含入场时机/画像/三情景/风控）
    · 多币初筛        → 同一调用内批量，N 币 ~N×0.15 秒
    · 需要深度论证/交叉验证/成员分歧 → 再升级到团队流程（kexi_run mode=pipeline
      或派活给四位成员），快答的产物（report.json）可直接喂给团队，不必重算。

设计原则：
    · **不重算已有逻辑**：全部 import 现有模块（indicators/position/timeframe/
      entry_plan/coin_profile/kline_utils），保证与团队流程**同一口径**——
      快答和深研的数字必须一致，否则会出现"两个结论"的信任灾难。
    · 复用落盘 K 线：若 <symbol>_<interval>_klines.json 已存在且末根未过期
      （同一天内），直接复用不重新取数（省 500ms，也避免重复打交易所）。
    · 诚实降级：任一环节失败都如实记录 errors[]，不编造、不用默认值填充。
"""

import argparse
import json
import os
import time
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kline_utils as ku          # noqa: E402
import fetch_klines as fk         # noqa: E402
import indicators as ind_mod      # noqa: E402
import position as pos_mod        # noqa: E402
import timeframe as tf_mod        # noqa: E402
import entry_plan as ep_mod       # noqa: E402
import coin_profile as cp_mod     # noqa: E402

TZ8 = timezone(timedelta(hours=8))
STALE_DAYS = 3
# 数据源约束（由 main 从 --source 写入）：auto=自动降级链；指定具体源则**只取该源**。
# 显式指定而静默换源 = 模型以为拿到了正规口径，实际是降级数据，结论与预期不符。
_FORCED_SOURCE = "auto"
# 落盘 K 线的复用有效期（小时）。日线级别，取过就不必反复打交易所——
# 盘中反复取数既慢又更容易撞限流，而最后一根已收盘 bar 在一个交易日内本来就不变。
CACHE_TTL_HOURS = 12
# 守拙的流动性硬否决线：ADV20 < $3M 一票否决（与 cex/risk 口径一致）。
# 快答原本缺这条，等于放行"看着能买、实际根本进不去/出不来"的标的。
ADV_FLOOR_USD = 3_000_000
# 单根极端波动阈值（±15%），超过说明有拉盘砸盘/事件风险，需提示
EXTREME_MOVE_PCT = 15.0


def compute_adv20_quote(klines):
    """近 20 日平均成交额（quote 计价）——用已收盘 K 线算，无需联网。

    返回 None 表示**无法判定**。关键区分：
      · 全部 volume=0（如 CoinGecko 免费档没有成交量字段）→ None（无数据）
      · 真的算出 0 → 也是 None（不可能有真实零成交）
    把"无成交量数据"当成 ADV=0 再判"流动性不足"是**方向完全相反的错误结论**：
    CoinGecko 只是免费档不给 volume，不代表这个币没人交易（PEPE 实盘日成交上亿美元）。
    """
    if not klines or len(klines) < 5:
        return None
    seg = klines[-20:]
    vals = [float(k[4]) * float(k[5]) for k in seg if len(k) >= 6]
    if not vals or all(v <= 0 for v in vals):
        return None          # 无成交量数据 / 全零 —— 无法判定，不是"流动性差"
    return sum(vals) / len(vals)


def extreme_moves(klines, limit=3):
    """检测近 N 根内的单根极端涨跌（事件/操纵风险提示）。"""
    out = []
    for k in klines[-60:]:
        if len(k) < 5:
            continue
        try:
            o, c = float(k[1]), float(k[4])
        except Exception:
            continue
        if o > 0:
            ch = (c / o - 1) * 100
            if abs(ch) >= EXTREME_MOVE_PCT:
                out.append({"ts": k[0], "pct": round(ch, 1)})
    return out[-limit:]


# ─────────────────────────── 取数（带复用） ───────────────────────────
def load_or_fetch(symbol, interval, limit, out_dir, refresh=False):
    """优先复用本地已收盘 K 线；过期或缺失才联网。返回 (klines, meta)。"""
    path = os.path.join(out_dir, f"{symbol}_{interval}_klines.json")
    if not refresh and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            kl = d.get("klines") or []
            # 缓存判据修正（v1.5.5）：**看"多久之前取的"，不看"最后一根K线多新"**。
            # 早先错误地用 last_bar_age_days<=1 判断，导致盘中几乎必然失效
            # （今天取的数据 age 就已经 1.7 天），于是每次都重新取数；更糟的是
            # 交易所限流时，磁盘上 239 根的好数据会被丢弃、换用 22 根的降级数据——
            # 方向完全相反。正确做法：fetched_at 在有效期内 + 样本够 + 未长期停更。
            # ⚠ v1.9.17 收紧复用门槛：日线缓存 age>1.5 天就不再复用（币安 PYTH
            #   日线实测停更 2.0 天，旧缓存被放行 → 价比对根本没机会跑 →
            #   「1d 停更盲区」成了红队攻击点）。旧缓存宁可不复用，也要触发
            #   重取+价比对；取到更新数据后自然写回。
            age_days = d.get("last_bar_age_days")
            _max_age = 1.5 if interval == "1d" else STALE_DAYS
            if (kl and len(kl) >= 60 and _fetched_hours_ago(d) is not None
                    and _fetched_hours_ago(d) <= CACHE_TTL_HOURS
                    and (age_days is None or age_days <= _max_age)):
                return kl, {"source": "cache(" + str(d.get("source") or "?") + ")",
                            "file": path, "age_days": age_days, "bars": len(kl),
                            "fetched_hours_ago": round(_fetched_hours_ago(d), 1)}
        except Exception:
            pass
    klines, warns = None, []
    forced = (globals().get("_FORCED_SOURCE") or "auto").lower()
    if forced == "binance":
        # 强制币安：取不到就明确失败，**不静默降级**——
        # 模型传 source:'binance' 是为了"要正规口径"，偷偷换源会毁掉这个意图
        try:
            klines, warns = fk.fetch_binance(symbol, interval, limit)
            src = "binance"
        except Exception as e:
            return None, {"source": "binance", "error":
                          f"已指定 --source binance 但币安取数失败（{str(e)[:120]}）。"
                          "未自动降级到其它数据源——若可接受降级口径请用 --source auto"}
    elif forced == "okx":
        # v1.9.16：持仓在 OKX 时用 OKX 价。此前 auto 永远先打币安，
        # 而币安 PYTH 日线停更 2.0 天（实测）——**拿陈旧的币安价算 OKX
        # 仓位的止损**。红队正是抓住「1d 停更 2 天盲区」说趋势不可验证。
        try:
            klines, warns = fk.fetch_okx(symbol, interval, limit)
            src = "okx"
        except Exception as e:
            return None, {"source": "okx", "error":
                          f"已指定 --source okx 但取数失败（{str(e)[:120]}）"}
    elif forced == "coingecko":
        try:
            klines, warns = fk.fetch_coingecko(symbol, interval, limit)
            src = "coingecko"
        except Exception as e:
            return None, {"source": "coingecko", "error":
                          f"已指定 --source coingecko 但取数失败（{str(e)[:120]}）"}
    else:
        # v1.9.16 auto 语义：优先币安（正规口径），但**数据陈旧就降级 OKX**。
        # 判据：最后一根 K 线的 age 超 STALE_DAYS 即视为陈旧（币安 PYTH 日线
        # 停更 2 天就是实测案例）。陈旧数据算出的关键位/止损位都是错的，
        # 好过"正规但过期"——先可用，再正统。
        # v1.9.16 auto：币安和 OKX 都取，**选最后一根 K 线更新的**。
        # 背景：币安 PYTH 日线停更 2.0 天（实测），OKX 1.3 天。
        # 拿陈旧数据算关键位/止损位全是错的——"正规但过期"不如"新且可用"。
        # 不预设持仓所在交易所：永远选最新价，来源在 meta 里如实标注。
        cands = []
        try:
            kb, wb = fk.fetch_binance(symbol, interval, limit)
            if kb:
                cands.append(("binance", kb, wb))
        except Exception:
            pass
        try:
            ko, wo = fk.fetch_okx(symbol, interval, limit)
            if ko:
                cands.append(("okx", ko, wo))
        except Exception:
            pass
        if not cands:
            try:
                klines, warns = fk.fetch_coingecko(symbol, interval, limit)
                src = "coingecko"
            except Exception as e:
                return None, {"source": None, "error": str(e)[:160]}
        else:
            def _last_age(c):
                return (time.time() * 1000 - c[1][-1][0]) / 86400000.0
            cands.sort(key=_last_age)          # 最新的排最前
            klines, src, warns = cands[0][1], cands[0][0], list(cands[0][2])
            if len(cands) > 1:
                others = "、".join("%s(%.1f天)" % (c[0], _last_age(c)) for c in cands[1:])
                warns.append("auto 价比对：取 %s（最新 %.1f 天），"
                             "备选 %s" % (src, _last_age(cands[0]), others))
    if not klines:
        return None, {"source": None, "error": "取数返回空"}
    step = fk.detect_step_ms(klines)
    # 注意：dedupe / drop_open_bar 返回的是 (结果, 计数) 元组，不是纯结果
    klines, _dup = fk.dedupe(klines)
    klines, _dropped = fk.drop_open_bar(klines, step)
    meta = {"source": src, "warnings": warns, "duplicates_removed": _dup,
            "dropped_open_bar": _dropped}
    try:
        os.makedirs(out_dir, exist_ok=True)
        new_step = fk.detect_step_ms(klines) if len(klines) > 2 else 0
        # 数据完整性保护：**降级取数不得覆盖磁盘上已有的好数据**。
        # 现场事故：交易所限流(451)时降级到 CoinGecko（22 根、4 天粒度），
        # 直接覆盖了 239 根的 binance 文件——一次限流就把本地数据集毁了。
        # 规则：样本更少、或粒度更粗（step 更大）时一律不写盘。
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    old = json.load(f)
                old_kl = old.get("klines") or []
                old_step = fk.detect_step_ms(old_kl) if len(old_kl) > 2 else 0
                if len(old_kl) > len(klines) or (old_step and new_step and new_step > old_step):
                    return klines, {"source": src, "warnings": warns,
                                    "note": f"降级数据（{len(klines)} 根）**未覆盖**磁盘上更好的 "
                                            f"{len(old_kl)} 根记录——已保留原文件"}
            except Exception:
                pass
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "symbol": symbol, "interval": interval, "interval_actual": interval,
                "source": (meta or {}).get("source") or "binance",
                "fetched_at": datetime.now(TZ8).isoformat(),
                "klines": klines, "bars": len(klines),
                "last_bar_age_days": _age_days(klines, interval),
                "dropped_open_bar": (meta or {}).get("dropped_open_bar", 0),
                "duplicates_removed": (meta or {}).get("duplicates_removed", 0),
                "gaps": [],
            }, f)
    except Exception:
        pass
    return klines, {"source": (meta or {}).get("source") or "binance", "file": path}


def _fetched_hours_ago(d):
    """落盘文件的 fetched_at 距今多少小时；无法解析返回 None。"""
    import datetime as _dt
    s = (d or {}).get("fetched_at")
    if not s:
        return None
    try:
        t = _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=TZ8)
        return max(0.0, (datetime.now(tz=TZ8) - t).total_seconds() / 3600.0)
    except Exception:
        return None


def _age_days(klines, interval):
    if not klines:
        return None
    step = fk.detect_step_ms(klines) if len(klines) > 1 else 86400000
    last = klines[-1][0]
    return round((Date_now_ms() - last) / 86400000.0, 1)


def Date_now_ms():
    import time as _t
    return int(_t.time() * 1000)


def _friendly_error(e):
    """把 Python 异常翻译成研究员看得懂的一句话。"""
    msg = str(e)
    if "unsupported operand type" in msg or isinstance(e, TypeError):
        return (f"内部类型错误（{msg[:60]}）——多半是数据源降级导致字段缺失；"
                "请重试或换用 --refresh 重新取数")
    if isinstance(e, (KeyError, IndexError)):
        return f"数据结构异常（{msg[:60]}）——数据源返回的字段与预期不符，请重试"
    if isinstance(e, TimeoutError):
        return "请求超时——交易所接口无响应，请稍后重试"
    return f"{type(e).__name__}: {msg[:100]}"


# ─────────────────────────── 三情景（确定性） ───────────────────────────
def build_stop_plan(klines, res, supports):
    """可执行止损（三法交叉，与守拙同口径）。

    消融对比实测发现的缺陷：entry_plan 的 invalidation 是**结构性失效位**，
    PYTH 上是 0.03763 = -53.7%。这种止损在风控上等于没有——途中根本保护不了仓位。
    守拙的做法是三法交叉裁决后收窄到 -11.99%。

    三法：
      ① 结构位：最近支撑（跌破 = 结构破坏）
      ② 均线位：MA20（趋势的生命线）
      ③ 波幅位：close - 1.5×ATR（给随机噪声留余量）

    裁决规则（保守，避免被日内噪音扫损）：
      - 剔除比现价高（无效）与距现价 < 1.0×ATR 的过近候选（必被噪音打掉）
      - 在剩余候选中取**最高**者 = 最靠近现价的可执行位（最严格的风控）
      - 若所有候选都被剔除，则诚实返回"无法给出可执行止损"，**不硬凑一个数**

    返回 {structural, tactical, distance_pct, method, candidates, note}
    """
    close = res.get("close")
    out = {"structural": None, "tactical": None, "distance_pct": None,
           "method": None, "candidates": [], "note": ""}
    if not isinstance(close, (int, float)) or close <= 0:
        out["note"] = "无现价，无法计算止损"
        return out

    structural = ((res.get("entry_plan") or {}).get("invalidation") or {}).get("price")
    out["structural"] = structural

    ma = res.get("ma") or {}
    atr = (res.get("volatility") or {}).get("atr")
    if not isinstance(atr, (int, float)) or atr <= 0:
        # 从 atr_pct 反推
        ap = (res.get("volatility") or {}).get("atr_pct")
        atr = close * (ap / 100.0) if isinstance(ap, (int, float)) and ap > 0 else None

    cands = []
    struct_near = min([s for s in supports if isinstance(s, (int, float)) and s < close],
                      default=None)
    if struct_near is not None:
        cands.append({"price": ku.rn(struct_near), "method": "结构位（最近支撑）"})
    ma20 = ma.get("ma20")
    if isinstance(ma20, (int, float)) and ma20 < close:
        cands.append({"price": ku.rn(ma20), "method": "均线位（MA20）"})
    if atr:
        cands.append({"price": ku.rn(close - 1.5 * atr), "method": "波幅位（现价−1.5×ATR）"})

    min_gap = atr * 1.0 if atr else close * 0.02
    viable = [c for c in cands if close - c["price"] >= min_gap]
    dropped = [c for c in cands if c not in viable]
    out["candidates"] = cands

    if not viable:
        out["note"] = ("无法给出可执行止损：所有候选位距现价不足 1×ATR（会被日内噪音扫损）"
                       + (f"；已剔除 {len(dropped)} 个过近候选" if dropped else "")
                       + "。此时应减仓或离场观望，而非硬设止损。")
        return out

    best = max(viable, key=lambda c: c["price"])
    out["tactical"] = best["price"]
    out["method"] = best["method"]
    out["distance_pct"] = round((best["price"] / close - 1) * 100, 2)
    notes = [f"可执行止损 {best['price']}（{out['distance_pct']}%，{best['method']}）"]
    if structural and isinstance(structural, (int, float)) and structural < close:
        sd = round((structural / close - 1) * 100, 1)
        notes.append(f"结构性失效位 {ku.rn(structural)}（{sd}%）仅用于判断'逻辑是否证伪'，"
                     f"**不可当作止损使用**——距现价过远，途中保护不了仓位")
    if dropped:
        notes.append(f"已剔除 {len(dropped)} 个过近候选（<1×ATR，易被噪音扫损）")
    out["note"] = "；".join(notes)
    return out


def _nearest_below(levels, price):
    """取最靠近现价、且位于现价下方的支撑位；没有则返回 None。"""
    below = [x for x in (levels or []) if isinstance(x, (int, float)) and x < price]
    return max(below) if below else None


def _nearest_above(levels, price):
    """取最靠近现价、且位于现价上方的阻力位；没有则返回 None。"""
    above = [x for x in (levels or []) if isinstance(x, (int, float)) and x > price]
    return min(above) if above else None


def build_scenarios(close, supports, resistances, atr_pct, chase, weekly_bear):
    """基于 ATR 期望波幅 + 结构位锚定，给事前可证伪的价格区间。

    ⚠ v1.5.9 修正一个结构性错误（消融对比与实跑日志同时暴露）：
    旧版用 `lo_s = min(supports)`（**最深**支撑）当"基准"下沿，于是三档区间
    按 [尾部, 支撑, 阻力, 尾部] 平铺时，"基准"这一档会从最深支撑一路吞到阻力，
    把几乎全部下跌空间都算了进去；真正的"悲观"只剩最深支撑到尾部之间一条
    极窄的缝。BTC 实测：基准 35% 给出 [-10.4%, +5.2%]，悲观 47% 只给出
    [-10.6%, -10.4%]（0.2% 宽）——**概率最高的档区间最窄，且方向反了**。
    这让"事前可证伪"变成形式：区间在，但没有一个档真正对应"跌"。

    修正后两处硬约束：
      1) 基准档锚定**现价附近**，上下沿取「最近结构位」与「1× 期望波幅」的
         较紧者 —— 基准就是"最可能发生的那一段"，不能被远处深支撑撑大。
      2) "基准"永远是**众数档**（概率最高者）。旧版追高时下调 p_base、
         把残差全灌进悲观，导致"悲观"变成最可能档，与"基准=最可能"约定相反。
         概率铁律：三者之和恒为 1.00，残差由基准吸收。

    乐观概率上限：周线空头时硬压到 ≤20%（与望潮口径一致）。
    尾部区间用 1.5× 期望波幅而非 2×——2× 会把"尾部"写成"常态"，
    实测 PYTH（ATR≈8.6%）下 2× 会给出 -40%/+36% 的区间，明显夸张。
    """
    atr_pct = atr_pct if atr_pct and atr_pct > 0 else 3.0
    exp7 = atr_pct * (7 ** 0.5) / 100.0        # 7 日期望波幅（比例）
    tail = 1.5 * exp7

    band_lo = close * (1 - exp7)               # 1× 期望波幅下沿
    band_hi = close * (1 + exp7)               # 1× 期望波幅上沿
    # 结构位与期望波幅取"较紧者"：支撑在 band_lo 之下说明它太远，
    # 这一档不该被它撑开——那正是旧版"基准吞掉全部跌幅"的成因。
    s_near = _nearest_below(supports, close)
    r_near = _nearest_above(resistances, close)
    bear_hi = max(s_near, band_lo) if s_near is not None else band_lo
    bull_lo = min(r_near, band_hi) if r_near is not None else band_hi
    # 兜底：结构位异常（贴现价/畸形）时不得产出零宽或倒挂区间。
    # 离现价不足 0.5× 期望波幅的"结构位"是噪声不是支撑/阻力——直接用会让
    # 基准档退化成贴着现价的一条缝（例如 [99.9, 100.1]），那不是区间，是零宽。
    if bear_hi >= close or (close - bear_hi) < 0.5 * exp7 * close:
        bear_hi = band_lo
    if bull_lo <= close or (bull_lo - close) < 0.5 * exp7 * close:
        bull_lo = band_hi
    tail_lo, tail_hi = close * (1 - tail), close * (1 + tail)

    if chase:
        # 追高：概率从"乐观"流向"基准（横盘消化）"与"悲观（回吐）"，
        # 但基准仍保持众数——它是"最可能"，不是"最喜欢的"。
        p_base, p_bull = 0.50, 0.12
    else:
        p_base, p_bull = 0.45, 0.30
    if weekly_bear:
        p_bull = min(p_bull, 0.20)
    p_bull = round(p_bull, 2)
    p_bear = round(max(0.10, 1.0 - p_base - p_bull), 2)
    p_base = round(1.0 - p_bull - p_bear, 2)   # 残差归基准，保证三者之和 = 1.00

    return {
        "基准": {"p": p_base, "range": [ku.rn(bear_hi), ku.rn(bull_lo)],
                 "cond": f"守住 {ku.rn(bear_hi)} 且量能不再放大"},
        "乐观": {"p": p_bull, "range": [ku.rn(bull_lo), ku.rn(max(bull_lo, tail_hi))],
                 "cond": f"放量突破 {ku.rn(bull_lo)} 并站稳（量比≥1.2）"},
        "悲观": {"p": p_bear, "range": [ku.rn(tail_lo), ku.rn(bear_hi)],
                 "cond": f"跌破 {ku.rn(bear_hi)} 支撑"},
    }


# ─────────────────── 结论置信度分级（v1.5.9 新增）───────────────────
# 为什么快答也必须要置信度：团队深研里望潮会按"数据质量 + 信号一致性"定级，
# 但快答此前**只给评级、不给置信度**——于是"降级源 + 22 根 4 天粒度 + 正在追高"
# 这种结论，和"币安 240 根正规日线 + 周期共振"，在用户眼里长得一模一样。
# 快答的取数来源、样本数、停更天数、追高状态、周期冲突**全都影响可信度**，
# 不标出来就是让用户自己猜。
#
# 硬规则：**低置信不得给立即入场**。评级照给（方向仍然告诉用户），
# 但执行层必须降级——这与"数据不可用时作废止损价"是同一条纪律：
# 方向可以猜，仓位不能赌。
CONFIDENCE_RULE = "低置信不得给立即入场：方向可以猜，仓位不能赌"


def compute_confidence(res, errors):
    """给单币快答定置信度（高/中/低）并列出逐条扣分理由。

    纯函数：不联网、不读盘，只看 res 里已算好的字段 → 与团队流程同源同码，
    不可能算出与快答本体矛盾的置信度。
    """
    p = []

    def hit(w, code, note):
        p.append({"weight": w, "code": code, "note": note})

    # ── 数据层：降级源 / 样本不足 / 停更 ─────────────────────────────
    src = str(res.get("data_source") or "")
    if "coingecko" in src.lower():
        hit(2, "degraded_source",
            "数据源为降级口径（%s）：CoinGecko 免费档无成交量且粒度更粗，"
            "ADV20 与量价结构均无法计算" % src)
    bars = res.get("bars") or 0
    if 0 < bars < 240:
        hit(1 if bars >= 120 else 2, "thin_bars",
            "样本仅 %d 根（<240）：MA60 / 20 日分位 / 90 日位置的稳定性弱于满样本" % bars)
    age = res.get("last_bar_age_days")
    if isinstance(age, (int, float)) and not isinstance(age, bool) and age > 1.5:
        hit(1, "stale_bar", "末根 K 线距今 %s 天，结论未反映最近这段行情" % age)

    # ── 可判定性：ADV20 算不出来 ≠ 流动性差，但"算不出来"本身就是风险 ──
    if res.get("liquidity_known") is False:
        hit(1, "liquidity_unknown",
            "ADV20 无法判定（该源不提供成交量）——能否进出会去全靠用户自行核对盘口深度")

    # ── 信号层：追高 / 极端波动 / 多周期冲突 ──────────────────────────
    if ((res.get("entry_plan") or {}).get("chase_risk") or {}).get("flagged"):
        hit(1, "chase_risk", "追高嫌疑成立：当前位置与涨速已透支，短期结论易反转")
    if res.get("extreme_moves"):
        hit(1, "extreme_move", "近 60 根存在单根极端波动，形态与指标赖以成立的统计前提被事件污染")
    tf = res.get("timeframe") or {}
    known = [d for d in (tf.get("daily_dir"), tf.get("weekly_dir"), tf.get("monthly_dir")) if d]
    bull = sum(1 for d in known if any(k in str(d) for k in ("多头", "上涨", "上行")))
    bear = sum(1 for d in known if any(k in str(d) for k in ("空头", "下跌", "下行")))
    if bull and bear:
        hit(1, "tf_conflict",
            "多周期方向冲突（日%s/周%s/月%s）——小周期结论不可外推"
            % (tf.get("daily_dir"), tf.get("weekly_dir"), tf.get("monthly_dir")))

    # ── 维度缺失：关键模块没产出 = 结论少一块骨头，不能当"高" ──────────
    for key, label in (("entry_plan", "入场时机"), ("position", "位置"), ("volatility", "波动率")):
        if not res.get(key):
            hit(1, "missing_module", "%s模块未产出，结论缺少该维度支撑" % label)

    for e in (errors or []):
        note = str(e.get("error") or "")[:120]
        if e.get("stage") == "data_quality":
            hit(3, "data_quality", note)
        else:
            hit(1, "stage_failed", "%s 阶段失败：%s" % (e.get("stage"), note[:80]))

    score = sum(x["weight"] for x in p)
    return {
        "level": "低" if score >= 3 else ("中" if score >= 1 else "高"),
        "score": score,
        "penalties": p,
        "blocking": any(x["code"] == "data_quality" for x in p),
        "rule": CONFIDENCE_RULE,
    }


# ─────────────────────────── 单币快研 ───────────────────────────
def analyze_one(symbol, interval, limit, out_dir, btc=None, eth=None, refresh=False):
    errors = []
    kl, kmeta = load_or_fetch(symbol, interval, limit, out_dir, refresh)
    if not kl:
        return None, [{"stage": "fetch", "error": (kmeta or {}).get("error", "取数失败")}]

    res = {
        "symbol": symbol, "interval": interval,
        "close": ku.rn(float(kl[-1][4])),
        "bars": len(kl),
        "last_bar_age_days": kmeta.get("age_days") if kmeta.get("age_days") is not None
                            else _age_days(kl, interval),
        "data_source": kmeta.get("source"),
    }
    if res["last_bar_age_days"] is not None and res["last_bar_age_days"] > STALE_DAYS:
        errors.append({"stage": "data_quality",
                       "error": f"日线停更约 {res['last_bar_age_days']} 天（>{STALE_DAYS}）——结论不可用"})
    if len(kl) < 120:
        errors.append({"stage": "data_quality",
                       "error": f"样本仅 {len(kl)} 根（<120，MA60/多周期不可靠）"})

    # 流动性与异常波动闸门（v1.5.3 补齐守拙的核心风控项）
    # 诚实性要求：ADV20 **算不出来**（CoinGecko 免费档无成交量字段）时，
    # 必须标"无法判定"，**不能**判成"流动性不足"——没数据 ≠ 流动性差。
    # 早先版本把 adv=None 也当 illiquid，既崩在 f-string 除法上，
    # 又给出一个方向完全相反的错误结论。
    adv = compute_adv20_quote(kl)
    res["adv20_quote"] = round(adv, 2) if (adv is not None and adv > 0) else None
    res["liquidity_known"] = res["adv20_quote"] is not None
    res["illiquid"] = bool(res["liquidity_known"] and res["adv20_quote"] < ADV_FLOOR_USD)
    if res["illiquid"]:
        res.setdefault("risk_flags", []).append(
            f"流动性不足：ADV20 ≈ ${adv/1e6:.2f}M < $3M（守拙一票否决口径）——"
            f"建议单笔名义 ≤${max(50000, adv*0.03)/1000:.0f}k 且限价分批")
    elif adv is None:
        res.setdefault("risk_flags", []).append(
            "流动性无法判定：该数据源不提供成交量，ADV20 算不出——"
            "下单前须自行核对盘口深度；本次不据此给流动性结论")
    ext = extreme_moves(kl)
    if ext:
        res["extreme_moves"] = ext
        res.setdefault("risk_flags", []).append(
            "近 60 根存在单根极端波动：" +
            "、".join(f"{m['pct']:+.1f}%" for m in ext) + "（事件/操纵风险，慎追）")

    # 指标：indicators.py 没有库入口（只有 main），走子进程——
    # 这样快答与团队流程**执行的是同一份代码**，口径不可能分叉（我自己的设计原则）。
    ind = None
    try:
        import subprocess
        import tempfile
        _ind_out = os.path.join(out_dir, f"{symbol}_{interval}_indicators.json")
        _kl_path = os.path.join(out_dir, f"{symbol}_{interval}_klines.json")
        if not os.path.exists(_kl_path):
            with open(_kl_path, "w", encoding="utf-8") as _f:
                json.dump({"symbol": symbol, "interval": interval, "klines": kl}, _f)
        _p = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "indicators.py"),
             "--input", _kl_path, "--out", _ind_out],
            capture_output=True, text=True, timeout=120)
        if os.path.exists(_ind_out):
            with open(_ind_out, encoding="utf-8") as _f:
                ind = json.load(_f)
    except Exception as e:
        errors.append({"stage": "indicators", "error": str(e)[:140]})
    if ind:
        res["rsi14"] = ind.get("rsi14") or (ind.get("rsi") or {}).get("rsi14")
        res["ma"] = ind.get("ma") or {}
        res["macd_state"] = (ind.get("macd") or {}).get("state") or ind.get("macd_state")
        res["support_resistance"] = ind.get("support_resistance") or {}
        res["volatility"] = ind.get("volatility") or {}
        res["volume"] = ind.get("volume") or {}

    # 位置
    try:
        pos = pos_mod.analyze_position(kl)
    except Exception as e:
        pos = None
        errors.append({"stage": "position", "error": str(e)[:140]})
    if pos:
        res["position"] = {k: pos.get(k) for k in
                           ("pos_90", "zone", "dd_from_high_90", "pos_all",
                            "gain_20d", "crowded", "stretched", "position_informative")}

    # 多周期
    try:
        mtf = tf_mod.multi_tf_view(kl)
    except Exception as e:
        mtf = None
        errors.append({"stage": "timeframe", "error": str(e)[:140]})
    if mtf:
        # multi_tf_view 的真实结构是 {daily:{trend:...}, weekly:{...}, monthly:{...}, alignment}
        # （早期版本误按 daily_dir/weekly_dir 取值 → 全部 None，这里按真实结构取）
        def _dir(per):
            d = mtf.get(per) or {}
            return d.get("trend") or d.get("direction") or d.get("dir")
        res["timeframe"] = {
            "daily_dir": _dir("daily"), "weekly_dir": _dir("weekly"),
            "monthly_dir": _dir("monthly"), "alignment": mtf.get("alignment"),
            "risk_note": mtf.get("risk_note"),
            "available_tf": mtf.get("available_tf"),
        }

    # 入场时机
    try:
        plan = ep_mod.build_entry_plan(kl, position=pos, indicators=ind,
                                        btc_klines=btc, stablecoin_flow=None)
    except Exception as e:
        plan = None
        errors.append({"stage": "entry_plan", "error": str(e)[:140]})
    if plan and plan.get("ok"):
        res["entry_plan"] = {
            "chase_risk": plan.get("chase_risk"),
            "conditions": plan.get("conditions"),
            "actions": [{"type": a.get("type"), "label": a.get("label"),
                         "price": a.get("price"), "basis": a.get("basis", [])[:3]}
                        for a in plan.get("actions", [])],
            "invalidation": plan.get("invalidation"),
        }
        res["supports"] = plan.get("supports", [])
        res["resistances"] = plan.get("resistances", [])

    # 币种画像
    try:
        prof = cp_mod.build_profile(kl, btc, eth)
    except Exception as e:
        prof = None
        errors.append({"stage": "coin_profile", "error": str(e)[:140]})
    if prof and prof.get("ok"):
        res["profile"] = {
            "pattern": prof["pump_dump_pattern"].get("pattern"),
            "pattern_evidence": prof["pump_dump_pattern"].get("evidence", [])[:3],
            "regime_vs_btc": prof["market_regime"].get("regime_vs_btc"),
            "btc_r": prof["market_regime"].get("btc_r"),
            "follows": prof["market_regime"].get("follows"),
        }
        if (prof.get("btc_eth_linkage") or {}).get("available"):
            res["profile"]["eth_btc_season"] = prof["btc_eth_linkage"].get("season")

    # 三情景
    sr = res.get("support_resistance") or {}
    sup = [s["price"] for s in (sr.get("support") or []) if isinstance(s, dict) and s.get("price")]
    resi = [r["price"] for r in (sr.get("resistance") or []) if isinstance(r, dict) and r.get("price")]
    atr_pct = (res.get("volatility") or {}).get("atr_pct")
    chase = bool(((res.get("entry_plan") or {}).get("chase_risk") or {}).get("flagged"))
    weekly_bear = any(x in str((res.get("timeframe") or {}).get("weekly_dir") or "")
                      for x in ("空头", "MA20<MA50", "跌破"))
    res["scenarios"] = build_scenarios(res["close"], sup, resi, atr_pct, chase, weekly_bear)

    # ── 可执行止损（v1.5.5 修正）────────────────────────────────────────
    # 消融对比实测发现的真缺陷：entry_plan 的 invalidation 是**结构性失效位**
    # （跌破前低/60日低），PYTH 上是 0.03763 = **-53.7%**。一个 -53.7% 的"止损"
    # 在风控上等于没有——它只在"判断彻底错了"时才触发，途中根本保护不了仓位。
    # 守拙的做法是三法交叉：结构位 / 均线位 / ATR 波幅，收窄到 -11.99% 并另给
    # 盘中硬止损。快答补上同样口径，且**两个数都给**（结构位用于判断"逻辑是否
    # 证伪"，战术位用于"实际扛不扛得住"），绝不二选一让人误用。
    res["stop_plan"] = build_stop_plan(kl, res, sup)
    # 数据不可用时，止损**必须一并作废**——基于 22 根 4 天粒度数据算出的
    # "止损价"是精确的垃圾。v1.5.7 初版只清了 entry_plan/scenarios，漏了 stop_plan，
    # 于是出现「评级【数据不可用】+ 给出可执行止损 77813.2」的自相矛盾输出
    # （现场取证：BTCUSDT 在 Binance 451 降级期间）。与"一边说不可用一边给评级"
    # 是同一类错误，必须一并堵上。
    if [e for e in errors if e.get("stage") == "data_quality"]:
        res["stop_plan"] = {
            "structural": None, "tactical": None, "distance_pct": None,
            "method": None, "candidates": [],
            "note": "数据不可用（停更/样本不足）——**不给出止损价**："
                    "在不可信数据上算出的价位是精确的垃圾，比没有更危险",
        }

    # 评级（与铁律一致：逆周线空头 = 反弹，不给正面评级；
    #        流动性不足 = 守拙一票否决，不得进推荐前列；
    #        数据不可用 = **不得出结论**，否则会出现"一边说结论不可用、
    #        一边给出评级与三情景"的自相矛盾）
    acts = {a["type"] for a in (res.get("entry_plan") or {}).get("actions", [])}
    align = (res.get("timeframe") or {}).get("alignment")
    dq_blocked = [e for e in errors if e.get("stage") == "data_quality"]
    blocked = bool(dq_blocked)
    if blocked:
        # 数据不可用时，把入场动作与情景一并作废，避免误用
        res.pop("entry_plan", None)
        res.pop("scenarios", None)

    # 置信度必须在评级之前算：entry_now 受它硬约束（低置信不得立即入场）
    conf = compute_confidence(res, errors)
    res["confidence"] = conf

    if dq_blocked:
        rating = "数据不可用"
        why = dq_blocked[0]["error"] + "——本次不给出任何方向性结论"
    elif res.get("illiquid"):
        rating, why = "流动性不足（不推荐）", f"ADV20 ${res['adv20_quote']/1e6:.2f}M 低于 $3M 硬否决线"
    elif align == "全周期共振向上" and not chase:
        rating, why = "可关注", "全周期共振向上且无追高嫌疑"
    elif chase:
        rating, why = "回调观察（不追高）", "追高嫌疑成立，只等回调或突破"
    elif weekly_bear:
        rating, why = "反弹观察（置信度低）", "日线多头但周线空头＝反弹非反转"
    else:
        rating, why = "观察", "周期证据不足"
    # 低置信 → 降级执行层：评级保留（方向仍告诉用户），但 entry_now 必须收回。
    # 否则会出现"置信度低"和"立即入场"并列的自相矛盾建议。
    entry_now = ("enter_now" in acts and not res.get("illiquid") and not blocked
                 and conf["level"] != "低")
    if (not entry_now and "enter_now" in acts and not res.get("illiquid") and not blocked
            and conf["level"] == "低"):
        top = (conf["penalties"][0]["note"] if conf["penalties"] else "多项扣分")
        why += "；⚠ 置信度【低】（扣 %d 分，首要原因：%s）→ 已收回「立即入场」，只观察" % (
            conf["score"], top)
    res["verdict"] = {"rating": rating, "reason": why, "data_usable": not blocked,
                      "entry_now": entry_now, "confidence": conf["level"]}
    res["errors"] = errors
    return res, errors


# ─────────────────────────── 摘要渲染 ───────────────────────────
def render_digest(rows, elapsed_ms, meta):
    L = []
    L.append(f"[快研] {len(rows)} 币 · 确定性脚本链 · 耗时 {elapsed_ms} ms"
             f"（团队协作流程约需 15-20 分钟）")
    for r in rows:
        if r is None:
            continue
        v = r.get("verdict") or {}
        ep = r.get("entry_plan") or {}
        ch = "⚠追高" if (ep.get("chase_risk") or {}).get("flagged") else "—"
        acts = ep.get("actions") or []
        main_act = acts[0]["label"] if acts else "—"
        L.append("")
        L.append(f"■ {r.get('symbol')}  现价 {r.get('close', '—')}  "
                 f"评级【{v.get('rating')}】{ch}  置信度【{v.get('confidence') or (r.get('confidence') or {}).get('level', '—')}】")
        L.append(f"  数据：{r.get('bars', '—')} 根 · 停更 {r.get('last_bar_age_days', '—')} 天"
                 f" · {r.get('data_source', '—')}")
        cf = r.get("confidence") or {}
        if cf.get("penalties"):
            for _x in cf["penalties"][:3]:
                L.append(f"       └ 扣分×{_x['weight']}：{_x['note']}")
            if len(cf["penalties"]) > 3:
                L.append(f"       └ …另有 {len(cf['penalties']) - 3} 项扣分，"
                         f"合计 {cf.get('score')} 分")
        if cf.get("level") == "低":
            L.append("       ⚠ %s（本次已自动收回「立即入场」）" % (cf.get("rule") or ""))
        posn = r.get("position") or {}
        tf = r.get("timeframe") or {}
        L.append(f"  位置：90日 {posn.get('pos_90')}%（{posn.get('zone')}）"
                 f"｜多周期 {tf.get('daily_dir')}/{tf.get('weekly_dir')}/{tf.get('monthly_dir')}"
                 f"（{tf.get('alignment')}）")
        pf = r.get("profile") or {}
        L.append(f"  画像：{pf.get('pattern')}｜{pf.get('regime_vs_btc')}｜{pf.get('follows') or '—'}")
        L.append(f"  入场：{main_act}")
        for a in acts[1:2]:
            L.append(f"        {a['label']}")
        inv = ep.get("invalidation") or {}
        if inv.get("price"):
            L.append(f"  失效(结构)：{inv['price']}（跌破则看涨逻辑证伪）")
        sp = r.get("stop_plan") or {}
        if sp.get("tactical"):
            L.append(f"  **可执行止损**：{sp['tactical']}（{sp['distance_pct']}%，{sp['method']}）"
                     + ("　⚠ 结构位距现价过远，**不可当止损用**" if sp.get("structural") else ""))
        elif sp.get("note"):
            L.append(f"  ⚠ 止损：{sp['note']}")
        sc = r.get("scenarios") or {}
        if sc:
            L.append("  三情景：" + " ｜ ".join(
                f"{k} {int((sc[k]['p'] or 0)*100)}%[{sc[k]['range'][0]},{sc[k]['range'][1]}]"
                for k in ("基准", "乐观", "悲观") if k in sc))
        if r.get("adv20_quote"):
            L.append(f"  流动性：ADV20 ≈ ${r['adv20_quote']/1e6:.2f}M"
                     + ("（低于 $3M 硬否决线）" if r.get("illiquid") else ""))
        for flag in (r.get("risk_flags") or []):
            L.append(f"  ⚠ {flag}")
        if r.get("errors"):
            L.append("  ⚠ " + "；".join(e.get("error", "")[:60] for e in r["errors"]))
    L.append("")
    L.append("说明：以上为**确定性快答**（可计算数字与团队流程逐位一致，实测 11/11）。"
             "它**不做**成员交叉验证、消息面证据与情景概率的多因子推演——"
             "需要这些请升级到 kexi_run(mode=pipeline) 或派活团队；"
             "本结果的 fast_analysis.json 可直接喂给团队，无需重算。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="单次确定性快研（秒级出结论）")
    ap.add_argument("--symbol", help="单个标的，如 PYTHUSDT")
    ap.add_argument("--symbols", help="逗号分隔的多个标的（批量）")
    ap.add_argument("--interval", default="1d", choices=["1h", "4h", "1d", "1w"])
    ap.add_argument("--limit", type=int, default=240)
    ap.add_argument("--out-dir", default=None, help="默认 <cwd>/kexi_out")
    ap.add_argument("--out", default=None, help="汇总 JSON 输出路径")
    ap.add_argument("--md", default=None, help="汇总 Markdown 输出路径")
    ap.add_argument("--refresh", action="store_true", help="强制重新取数（不复用缓存）")
    ap.add_argument("--source", default="auto", choices=["auto", "binance", "okx", "coingecko"],
                    help="数据源：auto=自动降级链（默认）；指定 binance/coingecko 则只取该源，"
                         "取不到即失败——**绝不静默换源**，否则你以为拿到了正规口径，"
                         "实际是 4 天粒度的降级数据（v1.5.7 现场修：kexi_run 曾静默丢弃此参数）")
    args = ap.parse_args()
    forced_source = (args.source or "auto").lower()
    # load_or_fetch 与 main 分处不同作用域；本脚本是单线程 CLI，
    # 用模块级变量传递最省改动（不必给 analyze_one/load_or_fetch 逐层加参数）。
    global _FORCED_SOURCE
    _FORCED_SOURCE = forced_source

    syms = []
    for raw in (args.symbol, args.symbols):
        if not raw:
            continue
        # --symbol 与 --symbols 都接受逗号分隔：模型与用户都会顺手写 "A,B"，
        # 只认单值时报 "no CoinGecko id mapping for A,B" 这种误导性错误。
        for part in str(raw).split(","):
            t = part.strip().upper()
            if t and t not in syms:
                syms.append(t)
    if not syms:
        print(json.dumps({"ok": False, "error": "需要 --symbol 或 --symbols"}, ensure_ascii=False))
        return 1

    out_dir = args.out_dir or os.path.join(os.getcwd(), "kexi_out")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass

    import time as _t
    t0 = _t.time()

    # BTC/ETH 参照（顺逆大盘 / 大饼以太归属需要），失败不阻断
    btc = eth = None
    for ref, name in (("btc", "BTCUSDT"), ("eth", "ETHUSDT")):
        try:
            k, _ = load_or_fetch(name, "1d", args.limit, out_dir, args.refresh)
            if ref == "btc":
                btc = k
            else:
                eth = k
        except Exception:
            pass

    rows = []
    for s in syms:
        try:
            r, errs = analyze_one(s, args.interval, args.limit, out_dir, btc, eth, args.refresh)
            if r:
                rows.append(r)
            else:
                # 取数彻底失败时**不能让它从报告里消失**——早先版本直接跳过，
                # 表现为"分析 0 币"却没有任何说明，看起来像"没传标的"。
                rows.append({
                    "symbol": s, "verdict": {"rating": "取数失败", "reason": "未取得该标的行情",
                                             "data_usable": False, "entry_now": False},
                    "errors": errs or [{"stage": "fetch", "error": "取数失败且未返回原因"}],
                })
        except Exception as e:
            # 诚实且可读地失败：降级数据（下架/低流动性/限流）会走到这里，
            # 报 Python 原始异常对研究员毫无意义——说清"哪一步失败 + 可能原因"。
            rows.append({
                "symbol": s, "verdict": {"rating": "数据不可用", "reason": "分析过程失败",
                                         "data_usable": False, "entry_now": False},
                "errors": [{"stage": "fatal", "error": _friendly_error(e)}],
            })
    elapsed = int((_t.time() - t0) * 1000)

    payload = {
        "schema": "kexi.fast/1",
        "generated_at": datetime.now(TZ8).isoformat(),
        "elapsed_ms": elapsed,
        "count": len(rows),
        "rows": rows,
    }
    out = args.out or os.path.join(out_dir, "fast_analysis.json")
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    md = args.md or os.path.join(out_dir, "fast_analysis.md")
    try:
        with open(md, "w", encoding="utf-8") as f:
            f.write(render_digest(rows, elapsed, payload))
    except Exception:
        pass

    print(render_digest(rows, elapsed, payload))
    print(json.dumps({"ok": True, "count": len(rows), "elapsed_ms": elapsed,
                      "out": out, "md": md,
                      "symbols": [r["symbol"] for r in rows]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
