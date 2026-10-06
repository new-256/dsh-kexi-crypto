#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
position_doctor.py — 仓位体检：实际持仓 × 市场状态 → 问题清单 + 调仓意见（v1.8.2）

## 它做什么

读你**交易所账户里的真实持仓**（v1.6.0 已打通的只读链路），
结合 `regime.py` 的市场级状态判定，逐条列出仓位问题并给调仓意见。

## 它不做什么（这几条是设计底线，不是能力缺失）

1. **它不下单，永远不下单。** 本模块只产出「诊断 + 意见 + 订单草案」；
   真正的执行一律走 `cex_adapter.py` 的自主级别闸门（fail-closed）。
   两道闸门分开放是有意的：诊断逻辑再复杂，也不该握有下单能力。
2. **它不预测涨跌。** 每条意见的依据必须是**可观测**的：权重多少、
   强平价多远、浮亏多少、市场状态是第几期。**不可观测的前提等于没有前提**
   （本项目既有的纪律）。
3. **它不把"市场下行"翻译成"该卖"。** 市场状态只用于**放大或抑制**已有问题，
   绝不单独构成一条减仓理由——否则就成了"因为我说要跌所以卖"的循环论证。

## 权重口径的诚实说明（容易被误读，先讲）

本模块算的是**持仓内部结构占比**（notional share），**不是占账户净值比例**。
原因：只读接口 `positions` **不返回账户余额与稳定币持仓**，
用它推净值会系统性低估（合约账户里 USDT 不在 positionAmt 中）。
所以：
  · 「BTC 占你持仓的 62%」是可靠的；
  · 「BTC 占你总资产 62%」是**不能说的**，本模块也不说。
杠杆会进一步放大这两者的差距，输出里会标注。

## 用法

    position_doctor.py --positions cex_positions.json --regime kexi_out/regime.json \
                       --max-weight 0.25 --top3 0.70 --out kexi_out/position_doctor.json
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

SCHEMA = "kexi.position_doctor/1"
TZ8 = timezone(timedelta(hours=8))

# 仓位性质分类——决定"市场下行时该拿它怎么办"
STABLES = {"USDT", "USDC", "DAI", "TUSD", "FDUSD", "BUSD", "USDE", "PYUSD"}
# 高 beta 山寨：市场走弱时它们跌得更多，不是"跌得少的大盘"
CORE = {"BTC", "ETH", "BNB"}

SEV = ("high", "medium", "low")


def _is_leveraged_token(sym):
    """UP/DOWN/BULL/BEAR 类杠杆代币：标的就带杠杆，不需要看账户杠杆。"""
    s = str(sym or "").upper()
    for pat in ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S"):
        if s.endswith("USDT") and s[:-4].endswith(pat):
            return True
    return False


def _base_asset(sym):
    s = str(sym or "").upper()
    for q in ("USDT", "USDC", "BUSD", "USD"):
        if s.endswith(q):
            return s[:-len(q)]
    return s


def _kind(sym):
    a = _base_asset(sym)
    if a in STABLES or str(sym or "").upper() in STABLES:
        return "stable"
    if a in CORE:
        return "core"
    if _is_leveraged_token(sym):
        return "leveraged_token"
    return "alt"


def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def normalize(positions):
    """统一成诊断内部形状。**任何字段缺失都记为 None，不编默认值。**"""
    out = []
    for p in (positions or []):
        if not isinstance(p, dict):
            continue
        sym = p.get("symbol")
        if not sym:
            continue
        size = abs(_num(p.get("size"), 0.0) or 0.0)
        mark = _num(p.get("mark_price"))
        entry = _num(p.get("entry_price"))
        # 名义额 = |size| × mark；mark 缺失时**不估**（宁可少一条诊断也不给假数字）
        notional = (size * mark) if (size and mark) else None
        lev = _num(p.get("leverage"), 1.0) or 1.0
        upnl = _num(p.get("unrealized_pnl"))
        margin = (notional / lev) if notional else None
        out.append({
            "symbol": sym,
            "side": (p.get("side") or "long").lower(),
            "size": size, "entry_price": entry, "mark_price": mark,
            "notional": notional, "margin": margin, "leverage": lev,
            "unrealized_pnl": upnl,
            "liq_price": _num(p.get("liq_price")),
            "kind": _kind(sym),
            "leverage_token": _is_leveraged_token(sym),
            "pnl_pct": (upnl / margin) if (upnl is not None and margin) else None,
        })
    total = sum(p["notional"] or 0.0 for p in out)
    for p in out:
        p["weight"] = ((p["notional"] / total) if (p["notional"] and total > 0) else None)
    return out, total


# ── 诊断规则 ──────────────────────────────────────────────────────────────
# 每条规则返回 finding dict 或 None。**找不到可观测依据就返回 None**——
# 宁可少报一条，不可观测的前提等于没有前提。
def _f_concentration(hold, total, max_weight):
    hits = []
    for p in hold:
        w = p.get("weight")
        if w is None or max_weight is None or w <= max_weight:
            continue
        over = w / max_weight
        hits.append({
            "rule": "单币集中度超限",
            "severity": "high" if over >= 2 else "medium",
            "symbol": p["symbol"],
            "observed": "占持仓 %.1f%%（上限 %.0f%%，超出 %.1f 倍）"
                        % (w * 100, max_weight * 100, over),
            "basis": ["notional=$%.0f / 持仓总额 $%.0f" % (p["notional"], total),
                      "上限来自设置 maxWeight=%.2f" % max_weight],
            "proposed": {"action": "reduce", "pct": 0.5,
                         "label": "减仓至上限以内（建议先砍一半）"},
            "invalidation": "若这笔仓位本就有独立的止损与止盈计划、"
                            "且你愿意承担超额集中风险，可维持不动。",
        })
    return hits


def _f_top3(hold, top3_limit):
    ws = sorted([p["weight"] for p in hold if p.get("weight") is not None], reverse=True)
    if len(ws) < 3 or top3_limit is None:
        return []
    s3 = sum(ws[:3])
    if s3 <= top3_limit:
        return []
    return [{
        "rule": "前三大持仓过度集中",
        "severity": "medium" if s3 < 0.85 else "high",
        "symbol": None,
        "observed": "前三大合计占 %.1f%%（警戒线 %.0f%%）" % (s3 * 100, top3_limit * 100),
        "basis": ["前三大权重：%s" % "、".join("%.1f%%" % (w * 100) for w in ws[:3])],
        "proposed": {"action": "review", "pct": None,
                     "label": "逐个复核前三大：是否每一笔都仍满足当初的买入理由"},
        "invalidation": "若这三条是同一个逻辑驱动的组合（如 BTC+ETH+一篮子），"
                        "分散度低是设计使然，不算问题。",
    }]


def _f_leverage(hold, max_lev_weight):
    hits = []
    for p in hold:
        risk_lev = p.get("leverage") or 1.0
        token = p.get("leverage_token")
        w = p.get("weight")
        if w is None:
            continue
        if risk_lev < 3 and not token:
            continue
        if w <= (max_lev_weight or 0.10):
            continue
        why = "标的自带杠杆（%s）" % p["symbol"] if token else "账户杠杆 %.0fx" % risk_lev
        hits.append({
            "rule": "高杠杆仓位集中",
            "severity": "high" if (risk_lev >= 5 or token) else "medium",
            "symbol": p["symbol"],
            "observed": "%s，占持仓 %.1f%%" % (why, w * 100),
            "basis": ["leverage=%s" % risk_lev,
                      ("杠杆代币在标的上再叠一层波动" if token else "杠杆放大回撤与强平风险")],
            "proposed": {"action": "reduce", "pct": 0.5,
                         "label": "降杠杆或减半——高杠杆 + 集中是同时踩两条线"},
            "invalidation": "若你已为这笔设了硬止损且能承受归零，则可维持。",
        })
    return hits


def _f_liquidation(hold, min_dist=0.20):
    hits = []
    for p in hold:
        liq, mark = p.get("liq_price"), p.get("mark_price")
        if not liq or not mark or mark <= 0:
            continue
        dist = abs(mark - liq) / mark
        if dist >= min_dist:
            continue
        hits.append({
            "rule": "强平价过近",
            "severity": "high" if dist < 0.10 else "medium",
            "symbol": p["symbol"],
            "observed": "距强平价仅 %.1f%%（警戒 %.0f%%）" % (dist * 100, min_dist * 100),
            "basis": ["标记价 $%.6g ｜ 强平价 $%.6g" % (mark, liq),
                      "下跌 %.1f%% 即触发强平" % (dist * 100)],
            "proposed": {"action": "review", "pct": None,
                         "label": "加保证金或降杠杆——不是减仓，是把强平线推远"},
            "invalidation": "若这笔是长期底仓且你不会被动平仓，可不管；"
                            "但仍需确认账户是否会触发自动强平。",
        })
    return hits


def _f_loss(hold, loss_thresh=-0.25):
    hits = []
    for p in hold:
        pp = p.get("pnl_pct")
        if pp is None or pp > loss_thresh:
            continue
        hits.append({
            "rule": "浮亏较深且未见止损",
            "severity": "high" if pp <= loss_thresh * 2 else "medium",
            "symbol": p["symbol"],
            "observed": "浮亏 %.1f%%（占该笔保证金）" % (pp * 100),
            "basis": ["未实现盈亏 $%.2f" % p["unrealized_pnl"],
                      "入场价 $%.6g → 标记价 $%.6g" % (p["entry_price"], p["mark_price"])],
            "proposed": {"action": "review", "pct": None,
                         "label": "确认当初的止损位还在不在；不在就补上，已破位就执行"},
            "invalidation": "若这是按计划分批建仓的底仓、且仍在计划内，可不处理——"
                            "但要有明确的加仓/退出条件，而不是放着不动。",
        })
    return hits


def _f_regime_conflict(hold, regime):
    """市场状态**只用来放大已有问题，不单独构成减仓理由**（见模块 docstring 第 3 条）。"""
    r = (regime or {}).get("regime")
    conf = _num((regime or {}).get("confidence"), 0.0) or 0.0
    if not r or conf < 0.5:
        return []                     # 状态判定本身没把握 → 不用它说话
    hits = []
    if r == "下行期":
        alts = [p for p in hold
                if p["kind"] == "alt" and p["side"] == "long" and (p.get("weight") or 0) >= 0.10]
        if alts:
            hits.append({
                "rule": "下行期持有重仓山寨多头",
                "severity": "medium",
                "symbol": None,
                "observed": "市场判为下行期（置信度 %.2f），仍持有 %d 笔山寨多头，合计占 %.1f%%"
                            % (conf, len(alts), sum(p["weight"] for p in alts) * 100),
                "basis": ["涉及：%s" % "、".join("%s %.1f%%" % (p["symbol"], p["weight"] * 100)
                                                for p in alts),
                          "**市场状态本身不构成减仓理由**——本条只提示"
                          "「这些仓位正好是回撤时跌得最凶的一类」"],
                "proposed": {"action": "review", "pct": None,
                             "label": "逐笔复核：当初的买入理由在当前市场状态下还成立吗"},
                "invalidation": "若某笔有独立逻辑（独立行情/事件驱动）且已设止损，"
                                "市场状态不构成减它的理由。",
            })
        shorts = [p for p in hold if p["side"] == "short" and (p.get("weight") or 0) >= 0.10]
        if shorts:
            hits.append({
                "rule": "下行期持有空头（与市场状态一致，注意止盈）",
                "severity": "low",
                "symbol": None,
                "observed": "持有空头 %s，合计占 %.1f%%"
                            % ("、".join(p["symbol"] for p in shorts),
                               sum(p["weight"] for p in shorts) * 100),
                "basis": ["市场判为下行期（置信度 %.2f），空头方向与之一致" % conf],
                "proposed": {"action": "review", "pct": None,
                             "label": "方向对了，但检查止盈计划——空头最常见的亏法是坐了电梯"},
                "invalidation": "已设止盈/止损则无需处理。",
            })
    elif r == "上行期":
        shorts = [p for p in hold if p["side"] == "short" and (p.get("weight") or 0) >= 0.10]
        if shorts:
            hits.append({
                "rule": "上行期持有空头",
                "severity": "medium",
                "symbol": None,
                "observed": "市场判为上行期（置信度 %.2f），仍持有空头 %s，合计占 %.1f%%"
                            % (conf, "、".join(p["symbol"] for p in shorts),
                               sum(p["weight"] for p in shorts) * 100),
                "basis": ["上行期里空头承受的是时间成本与轧空风险"],
                "proposed": {"action": "review", "pct": None,
                             "label": "复核空头的持有理由是否已被市场证伪"},
                "invalidation": "若是对冲腿（本就要压住某笔多头），"
                                "**不该因市场转多就平掉对冲**——那会让风险敞口翻倍。",
            })
    return hits


def _f_symbol_conflict(hold, verdicts):
    """单币研判与持仓冲突。若传了 verdicts（本币 direction/评级）才启用。"""
    if not verdicts:
        return []
    hits = []
    for p in hold:
        v = verdicts.get(p["symbol"])
        if not isinstance(v, dict):
            continue
        d = str(v.get("direction") or "")
        r = str(v.get("rating") or "")
        bearish = d in ("空头", "震荡") or r in ("观望/减仓", "区间高抛低吸")
        if not bearish:
            continue
        w = p.get("weight")
        if w is None or w < 0.10:
            continue
        hits.append({
            "rule": "单币研判与持仓方向不一致",
            "severity": "medium",
            "symbol": p["symbol"],
            "observed": "该币研判为「%s／%s」，但仍持有占 %.1f%%"
                        % (d or "?", r or "?", w * 100),
            "basis": ["研判方向 %s，评级 %s" % (d or "?", r or "?")],
            "proposed": {"action": "review", "pct": None,
                         "label": "研判与持仓不一致时，先确认是「研判还没更新」还是「你改了主意」"},
            "invalidation": "研判可能已过期（数据时点早于你的建仓）——"
                            "以更新的研判与当前价为准。",
        })
    return hits


def _f_open_orders(hold, orders):
    """在途委托检查。

    起因（2026-09-30 真实账户实测）：用户 PYTH 挂着一笔**全仓限价卖单**，
    而体检完全不知道——它对这笔仓位建议「减仓至上限以内」，
    **而减仓已经在途了**。对正在执行的操作重复建议是噪声；
    更糟的是它**掩盖了真正该说的事**：那笔单挂价 $0.1、现价 $0.078，
    高出 28%，基本不会成交。

    ⚠ 市价从**持仓的 mark_price** 取（不额外联网）：体检本来就有它，
    凭它判断挂价远近不需要多一次网络往返。
    """
    hits = []
    if not orders:
        return hits
    by_sym = {p["symbol"]: p for p in hold}

    by_base = {}
    for p in hold:
        by_base.setdefault(base_asset(p["symbol"]), p)

    def match(inst):
        s = str(inst or "").upper()
        if s in by_sym:
            return by_sym[s]
        b = base_asset(inst)
        if b in by_base:
            return by_base[b]
        for k, p in by_sym.items():
            if s.startswith(k.upper()) or k.upper().startswith(s):
                return p
        return None

    for o in orders:
        if not isinstance(o, dict):
            continue
        inst = o.get("instId") or o.get("symbol")
        side = str(o.get("side") or "").lower()
        state = str(o.get("state") or o.get("status") or "live").lower()
        if state not in ("live", "open", "partially_filled", "partiallyfilled", ""):
            continue                       # 已成交/已撤销的不算在途
        p = match(inst)
        sz = _num(o.get("sz") or o.get("qty") or o.get("size"))
        px = _num(o.get("px") or o.get("price"))
        filled = _num(o.get("accFillSz") or o.get("filled") or 0) or 0.0
        oid = o.get("ordId") or o.get("order_id") or "—"

        # ① 减仓建议与在途委托重复
        if p and side == "sell" and p["side"] == "long":
            pend = max((sz or 0) - filled, 0)
            hold_sz = p.get("size") or 0
            if pend > 0 and hold_sz > 0 and pend >= hold_sz * 0.9:
                hits.append({
                    "rule": "减仓建议与在途委托重复",
                    "severity": "medium",
                    "symbol": p["symbol"],
                    "observed": "已有全仓卖单在途（%s，余量 %.4g / 持仓 %.4g）"
                                % (inst, pend, hold_sz),
                    "basis": ["单号 %s｜%s %s｜状态 %s" % (oid, side, inst, state)],
                    "proposed": {"action": "review", "pct": None,
                                 "label": "不必再减——已经在卖了。改为确认这笔单本身是否合理"},
                    "invalidation": "若委托已被交易所撤销或已成交，本条自动失效。",
                })

        # ② 挂单价远离市价 → 可能永不成交（这才是真正该提醒的事）
        mk = (p or {}).get("mark_price")
        if px and mk and mk > 0:
            gap = (px - mk) / mk
            if side == "sell" and gap > 0.05:
                hits.append({
                    "rule": "挂单价高于市价，很可能永不成交",
                    "severity": "high" if gap > 0.15 else "medium",
                    "symbol": p["symbol"] if p else inst,
                    "observed": "卖单挂 $%.6g，现价 $%.6g，挂高 %.1f%%" % (px, mk, gap * 100),
                    "basis": ["单号 %s｜数量 %.4g" % (oid, sz or 0),
                              "挂单价高于市价 %.1f%%，需涨上去才成交" % (gap * 100)],
                    "proposed": {"action": "review", "pct": None,
                                 "label": "确认这是不是想要的成交价；若是则改价或重挂"},
                    "invalidation": "若你就是等某个更高的波段目标价，那它是合理的——"
                                    "本条只提醒它当前挂不出去。",
                })
            elif side == "buy" and gap < -0.05:
                hits.append({
                    "rule": "买单挂价高于市价，会立刻成交",
                    "severity": "medium",
                    "symbol": p["symbol"] if p else inst,
                    "observed": "买单挂 $%.6g，现价 $%.6g，挂高 %.1f%%" % (px, mk, abs(gap) * 100),
                    "basis": ["单号 %s｜数量 %.4g" % (oid, sz or 0)],
                    "proposed": {"action": "review", "pct": None,
                                 "label": "限价买单高于市价会按市价成交（滑价）——确认有意的吗"},
                    "invalidation": "若市价快速变动、以致这个价只是「看起来」高于市价，可忽略。",
                })
    return hits


def diagnose(positions, regime=None, verdicts=None, max_weight=0.25,
             top3_limit=0.70, max_lev_weight=0.10, autonomy="readonly",
             open_orders=None, costs=None):
    """主入口。autonomy 只影响**是否附订单草案**，不影响诊断本身。"""
    hold, total = normalize(positions)
    findings = []
    findings += _f_concentration(hold, total, max_weight)
    findings += _f_top3(hold, top3_limit)
    findings += _f_leverage(hold, max_lev_weight)
    findings += _f_liquidation(hold)
    findings += _f_loss(hold)
    findings += _f_regime_conflict(hold, regime)
    findings += _f_symbol_conflict(hold, verdicts)
    findings += _f_open_orders(hold, open_orders)
    findings += _f_cost_basis(hold, costs)
    order = {s: i for i, s in enumerate(SEV)}
    findings.sort(key=lambda f: (order.get(f.get("severity"), 9), f.get("rule", "")))

    # 自主级别：readonly → **只给建议**；其余档才附订单草案，且仍须由
    # cex_adapter 在真正执行时再过一遍它自己的闸门。本模块不下单。
    advice_only = (str(autonomy or "readonly").lower() == "readonly")
    for f in findings:
        f["advice_only"] = advice_only
        f["order_draft"] = None
        if not advice_only and f.get("proposed", {}).get("action") in ("reduce", "exit"):
            p = next((h for h in hold if h["symbol"] == f.get("symbol")), None)
            if p and p.get("notional"):
                pct = float(f["proposed"].get("pct") or 0.5)
                qty = (p["size"] * pct)
                f["order_draft"] = {
                    "symbol": p["symbol"], "side": "sell" if p["side"] == "long" else "buy",
                    "reduce_only": True, "qty": round(qty, 8),
                    "notional_usd": round(p["notional"] * pct, 2),
                    "note": "**草案**。真正执行仍须 cex_adapter 过自主级别闸门；"
                            "reduce_only 不得反向开仓。",
                }

    missing = []
    if not positions:
        missing.append("未取得持仓（positions 为空）")
    if any(p.get("notional") is None for p in hold):
        missing.append("部分仓位缺标记价，名义额与占比未计入")
    if not regime:
        missing.append("未接入 regime 市场状态，下行/上行期冲突检查未启用")
    elif (_num((regime or {}).get("confidence"), 0) or 0) < 0.5:
        missing.append("regime 置信度 %.2f < 0.50，不足以支撑状态相关判断"
                       % ((regime or {}).get("confidence") or 0))
    if not verdicts:
        missing.append("未接入单币研判，单币冲突检查未启用")
    if not open_orders:
        missing.append("未接入在途委托——**减仓建议可能与已挂单重复**，"
                       "且查不出「挂价远离市价、成交不了」这类问题")

    n_high = sum(1 for f in findings if f["severity"] == "high")
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(TZ8).isoformat(),
        "summary": {
            "positions": len(hold),
            "total_notional": round(total, 2) if total else 0.0,
            "findings": len(findings),
            "high": n_high,
            "verdict": ("无问题" if not findings else
                        ("%d 项高危" % n_high if n_high else "%d 项待复核" % len(findings))),
        },
        "autonomy": autonomy,
        "advice_only": advice_only,
        "holdings": hold,
        "findings": findings,
        "data_gaps": missing,
        "weight_basis": ("weight = 该币名义额 ÷ 全部持仓名义额，"
                         "即**持仓内部结构占比**，非占账户净值比例——"
                         "只读接口不返回余额与稳定币持仓，杠杆会进一步放大两者差距。"),
        "note": ("本模块只产出诊断与建议，**从不下单**。执行一律走 cex_adapter 的"
                 "自主级别闸门。每条意见的 in_validation 即它的证伪条件。"),
    }


def base_asset(sym):
    """把各种写法归到标的资产：PYTH-USDT / PYTH-SPOT / PYTH -> PYTH。

    ⚠ 不归一会漏判：现货持仓命名成 `{ccy}-SPOT`，而 OKX 的 instId 是
      `{ccy}-USDT`——字符串比不上，新规则会静默失效（实测踩过）。
    """
    t = str(sym or "").upper()
    for q in ("-USDT", "-USDC", "-BUSD", "-USD", "-SPOT", "USDT", "USDC", "BUSD", "USD"):
        if t.endswith(q):
            return t[:-len(q)]
    return t


def _f_cost_basis(hold, costs):
    """成本价检查：有了成本才有"赚还是亏"的基准。

    ⚠ v1.9.15 为什么这很重要：现货持仓从**余额**接口推导，
      而余额接口**不返回成本字段**——所以 entry_price 恒为 0。
      之前诊断只能说"占持仓 100%"，却**说不出是赚是亏**，
      于是"止盈还是止损"这类问题只能回避。
      成本价改由 `cost-basis`（历史订单推导）提供后，基准才存在。

    ⚠ 同样要紧的是**局限标注**：交易所历史订单有时间窗口，
      算出来的成本可能偏低。**必须把 caveat 一起传出去**——
      一个没标注的偏低成本，会让人误以为"其实还赚着"。
    """
    hits = []
    if not costs:
        return hits
    for c in costs:
        sym = c.get("instId")
        if not sym or not c.get("available"):
            continue
        p = None
        for q in hold:
            if base_asset(sym) == base_asset(q["symbol"]):
                p = q
                break
        if not p:
            continue
        cost = c.get("avg_cost")
        mk = p.get("mark_price") or c.get("mark_price")
        if not cost or not mk:
            continue
        pnl = (mk - cost) / cost * 100.0
        tag = "成本价（已覆盖全部可见买入）" if c.get("complete") else "成本价（**覆盖可能不完整**）"
        if pnl <= -15:
            sev, act = "high", "reduce"
        elif pnl <= -8:
            sev, act = "medium", "reduce"
        elif pnl >= 25:
            sev, act = "medium", "review"
        else:
            sev, act = "low", "hold"
        hits.append({
            "rule": "成本盈亏基准",
            "severity": sev,
            "symbol": p["symbol"],
            "observed": "%s $%.6g，现价 $%.6g，%+.2f%%"
                        % (tag, cost, mk, pnl),
            "basis": [c.get("caveat") or "",
                      "总投入 $%s / 数量 %s" % (c.get("total_spent"), c.get("total_qty")),
                      "最早可见成交 %s" % (c.get("earliest_fill") or "未知")],
            "proposed": {"action": act, "pct": None,
                         "label": ("浮亏 %+.1f%%：先确认这笔仓位的**离场条件**（跌破什么价位就走），"
                                   "再谈减仓比例" % pnl) if pnl < 0 else
                                  ("浮盈 %+.1f%%：可考虑分批兑现一部分，或上移止损保护利润" % pnl)},
            "invalidation": "若成本价覆盖不完整（见 basis），盈亏比例会失真——"
                            "以交易所 App 的持仓成本为准。",
        })
    return hits


def _autoload_cost_basis(symbols=None, exchange=None, label=None):
    """自动读成本价（只读）。返回 ({instId: {...}}, 说明)。读不到就说读不到。"""
    import json as _json
    import os as _os
    import subprocess as _sp
    import sys as _sys
    here = _os.path.dirname(_os.path.abspath(__file__))
    adapter = _os.path.join(here, "cex_adapter.py")
    if not _os.path.exists(adapter):
        return None, "找不到 cex_adapter.py"
    home = _os.environ.get("DSH_HOME") or _os.path.join(_os.path.expanduser("~"), ".dsh")
    store = _os.environ.get("KEXI_CEX_STORE") or _os.path.join(home, "kexi-cex")
    if not _os.path.isdir(store):
        return None, "凭据目录不存在"
    try:
        # ⚠ 必须带 --exchange：适配器据此定位账户，缺了会直接退出 1。
        _argv = [_sys.executable, adapter, "--store-dir", store, "cost-basis"]
        if exchange:
            _argv += ["--exchange", str(exchange)]
        if label:
            _argv += ["--label", str(label)]
        _argv += ["--symbols"] + list(symbols or [])
        r = _sp.run(_argv, capture_output=True, text=True, timeout=60)
    except Exception as e:                                  # noqa: BLE001
        return None, "调用适配器失败：%s" % str(e)[:120]
    if r.returncode != 0:
        return None, "退出码 %d：%s" % (
            r.returncode, (r.stderr or "").strip().splitlines()[-1][:120]
            if r.stderr else "无 stderr")
    try:
        doc = _json.loads([l for l in (r.stdout or "").strip().splitlines() if l.strip()][-1])
    except Exception as e:                                  # noqa: BLE001
        return None, "解析失败：%s" % str(e)[:100]
    if not doc.get("ok"):
        return None, doc.get("error") or "适配器未成功"
    return {c["instId"]: c for c in (doc.get("costs") or []) if c.get("instId")}, None


def _autoload_open_orders(store_dir=None):
    """自动读在途委托。返回 (orders, 出错原因)；读不到时 orders=None。

    **只读**，且严格按自主级别：任何写操作都不碰。
    拿不到就交出原因，绝不假装"没有挂单"——那会让诊断漏掉最该说的话。
    """
    import json as _json
    import os as _os
    import subprocess as _sp
    import sys as _sys
    here = _os.path.dirname(_os.path.abspath(__file__))
    adapter = _os.path.join(here, "cex_adapter.py")
    if not _os.path.exists(adapter):
        return None, "找不到 cex_adapter.py"
    home = _os.environ.get("DSH_HOME") or _os.path.join(
        _os.path.expanduser("~"), ".dsh")
    store = _os.environ.get("KEXI_CEX_STORE") or _os.path.join(home, "kexi-cex")
    if not _os.path.isdir(store):
        return None, "凭据目录不存在（%s）" % store
    try:
        r = _sp.run([_sys.executable, adapter, "--store-dir", store,
                     "open-orders", "--markets", "spot", "usdtm"],
                    capture_output=True, text=True, timeout=45)
    except Exception as e:                                  # noqa: BLE001
        return None, "调用适配器失败：%s" % str(e)[:120]
    if r.returncode != 0:
        return None, (r.stderr or "").strip().splitlines()[-1][:140] if r.stderr else "退出码 %d" % r.returncode
    try:
        doc = _json.loads([l for l in (r.stdout or "").strip().splitlines() if l.strip()][-1])
    except Exception as e:                                  # noqa: BLE001
        return None, "解析适配器输出失败：%s" % str(e)[:120]
    if not doc.get("ok"):
        return None, doc.get("error") or "适配器返回未成功"
    return (doc.get("orders") or []), None


def main():
    import argparse
    ap = argparse.ArgumentParser(description="仓位体检：实际持仓 × 市场状态 → 调仓意见")
    ap.add_argument("--positions", required=True, help="cex_adapter positions 输出的 JSON")
    ap.add_argument("--regime", default=None, help="regime.py 输出（市场状态）")
    ap.add_argument("--verdicts", default=None,
                    help='单币研判映射 {"BTCUSDT":{"direction":"空头","rating":"观望/减仓"}}')
    ap.add_argument("--max-weight", type=float, default=0.25, help="单币占比上限（默认 0.25）")
    ap.add_argument("--top3", type=float, default=0.70, help="前三大合计警戒线（默认 0.70）")
    ap.add_argument("--max-lev-weight", type=float, default=0.10,
                    help="高杠杆仓占比上限（默认 0.10）")
    ap.add_argument("--autonomy", default="readonly",
                    help="自主级别（只影响是否附订单草案，不影响诊断）")
    ap.add_argument("--no-cost-basis", action="store_true",
                    help="跳过成本价自动读取（默认会读；现货余额接口不给成本，"
                         "只能从历史订单推导）")
    ap.add_argument("--no-open-orders", action="store_true",
                    help="跳过自动读取在途委托（默认会自动读；显式关掉才跳过）")
    ap.add_argument("--open-orders", default=None,
                    help="可选：cex_adapter open_orders 输出（在途委托；缺它就查不出重复建议）")
    ap.add_argument("--out", default=None, help="输出（默认 kexi_out/position_doctor.json）")
    args = ap.parse_args()

    if not os.path.exists(args.positions):
        print(json.dumps({"ok": False, "error": "找不到 %s" % args.positions}, ensure_ascii=False))
        return 1

    def _load(p):
        if not p or not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        # cex_adapter 的输出是 {ok, positions:[...]}；也容忍裸数组
        if isinstance(d, dict):
            return d.get("positions") if "positions" in d else d
        return d

    positions = _load(args.positions)
    if isinstance(positions, dict):
        positions = positions.get("positions") or []
    regime = _load(args.regime) if args.regime else None
    verdicts = _load(args.verdicts) if args.verdicts else None

    orders = _load(args.open_orders) if args.open_orders else None
    # v1.9.14：**没显式给在途委托时，自己去读**（若凭据可用）。
    #   起因：用户会话 session-324ef002 里诊断给出了「零止损单」的真实结论，
    #   但**不知道账户里已经挂着一笔全仓卖单**——那笔单挂价高于市价 29%，
    #   正是最该说的事。手动传 --open-orders 没人会记得，
    #   而这不是可选增强，是**诊断的完整性前提**。
    #   读不到就如实报数据缺口，绝不静默当成"没有挂单"。
    if orders is None and not args.no_open_orders:
        orders, _why = _autoload_open_orders()
        if orders is None:
            orders = []
            missing.append("未能读取在途委托（%s）——减仓建议可能与已挂单重复，"
                           "且查不出「挂价远离市价」这类问题" % _why)

    # v1.9.15：成本价（现货余额接口不给成本，必须从历史订单推导）
    costs = None
    if not getattr(args, "no_cost_basis", False):
        _syms = []
        for _r in (positions if isinstance(positions, list) else positions.get("positions") or []):
            _i = _r.get("instId") or _r.get("symbol")
            if _i:
                _syms.append(str(_i).replace("-SPOT", "-USDT"))
        _ex = next((str(_r.get("exchange")) for _r in (
            positions if isinstance(positions, list) else positions.get("positions") or [])
            if _r.get("exchange")), None)
        _lb = next((str(_r.get("label")) for _r in (
            positions if isinstance(positions, list) else positions.get("positions") or [])
            if _r.get("label")), None)
        _map, _why2 = _autoload_cost_basis(sorted(set(_syms)), _ex, _lb)
        if _map:
            costs = list(_map.values())
    if isinstance(orders, dict):
        orders = orders.get("orders") or orders.get("open_orders") or []

    res = diagnose(positions, regime=regime, verdicts=verdicts,
                   max_weight=args.max_weight, top3_limit=args.top3,
                   max_lev_weight=args.max_lev_weight, autonomy=args.autonomy,
                   open_orders=orders, costs=costs)

    out = args.out or os.path.join("kexi_out", "position_doctor.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    d = res["data_gaps"]
    print(json.dumps({
        "ok": True, "positions": res["summary"]["positions"],
        "findings": res["summary"]["findings"], "high": res["summary"]["high"],
        "advice_only": res["advice_only"],
        "digest": ("仓位体检：%d 笔持仓，%s。%s"
                   % (res["summary"]["positions"], res["summary"]["verdict"],
                      ("数据缺口：" + "；".join(d)) if d else "")),
        "out_files": [out],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
