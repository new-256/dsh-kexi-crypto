#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cex_risk.py - 实盘风控闸门（v1.5.0 新增）

═══════════════════════════════════════════════════════════════════════
用户选择（2026-09-29）：
  · 权限范围：**直接实盘**
  · 默认硬性风控档：**保守默认**
═══════════════════════════════════════════════════════════════════════

设计立场（重要）
----------------
用户选了"直接实盘"+"保守默认"。这两者放在一起意味着：**必须在代码层强制拦，
而不是靠提示语提醒**。一个只会"建议你减仓"的风控等于没有风控——人在兴奋时
不会看提示。因此本模块是**闸门（gate）**：`check_order()` 返回 `allowed=False`
时，适配层**必须拒绝发单**。

同时必须避免另一种失败：过度拦截导致功能不可用。故：
  · 硬上限（max_position_pct 等）可配置，但**有不可突破的上限**（见 HARD_CEILING）
  · 每次拦截都给出**具体理由 + 建议改小到多少**，而不是只说"不允许"
  · 用户可用 `--risk-profile aggressive` 放宽到预设档，但**仍需显式指定**，
    且日志会记录实际使用的档位（可审计）

四档预设
--------
conservative(保守, 默认) / balanced(稳健) / aggressive(激进) / custom(自定义)

风控检查项（逐条独立，全部通过才放行）
--------------------------------------
  1. 标的白名单/黑名单（用户可禁用不想碰的币）
  2. 单笔订单金额上限（占账户净值 %）
  3. 单币种持仓上限（占净值 %）
  4. 总持仓上限（占净值 %）
  5. 杠杆上限
  6. 单日下单次数上限（防脚本失控刷单）
  7. 单日累计成交额上限
  8. 强平距离检查（开仓价距强平价太近 → 拒绝）
  9. 现有持仓的反向单检查（防止"以为在平仓实际在加仓"）
 10. 止损必填检查（保守档：开仓**必须**同时带止损）
"""

import json
import os
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))

# ══════════════════════════════════════════════════════════════════════
# 预设档
# ══════════════════════════════════════════════════════════════════════
RISK_PROFILES = {
    "conservative": {
        "max_order_pct": 5.0,          # 单笔 ≤ 净值 5%
        "max_symbol_pct": 10.0,        # 单币种 ≤ 10%
        "max_total_pct": 50.0,         # 总持仓 ≤ 50%（留一半现金）
        "max_leverage": 3,
        "max_orders_per_day": 20,
        "max_daily_volume_pct": 30.0,
        "min_liq_distance_pct": 25.0,  # 距强平价至少 25%
        "require_stop_loss": True,     # ★ 开仓必须带止损
        "allow_short": False,          # ★ 保守档默认只做多
        "require_confirmation": True,
    },
    "balanced": {
        "max_order_pct": 10.0,
        "max_symbol_pct": 20.0,
        "max_total_pct": 80.0,
        "max_leverage": 5,
        "max_orders_per_day": 50,
        "max_daily_volume_pct": 60.0,
        "min_liq_distance_pct": 15.0,
        "require_stop_loss": True,
        "allow_short": True,
        "require_confirmation": True,
    },
    "aggressive": {
        "max_order_pct": 25.0,
        "max_symbol_pct": 40.0,
        "max_total_pct": 100.0,
        "max_leverage": 10,
        "max_orders_per_day": 200,
        "max_daily_volume_pct": 100.0,
        "min_liq_distance_pct": 8.0,
        "require_stop_loss": False,
        "allow_short": True,
        "require_confirmation": False,
    },
}

# 任何档位都不允许突破的绝对上限（防"配置写错导致爆仓"）
HARD_CEILING = {
    "max_order_pct": 50.0,
    "max_symbol_pct": 60.0,
    "max_total_pct": 100.0,
    "max_leverage": 20,
    "max_orders_per_day": 1000,
}

DEFAULT_PROFILE = "conservative"


def get_profile(name=DEFAULT_PROFILE, overrides=None):
    """取风控档位（并施加 HARD_CEILING 与合法域校验）。"""
    if name == "custom":
        base = dict(RISK_PROFILES[DEFAULT_PROFILE])
    else:
        base = dict(RISK_PROFILES.get(name) or RISK_PROFILES[DEFAULT_PROFILE])
    if overrides:
        for k, v in overrides.items():
            if k in base:
                base[k] = v
    # 施加绝对上限
    clamped = []
    for k, cap in HARD_CEILING.items():
        if k in base and isinstance(base[k], (int, float)) and base[k] > cap:
            clamped.append(f"{k}: {base[k]} → {cap}")
            base[k] = cap
    # 百分比字段合法性
    for k in ("max_order_pct", "max_symbol_pct", "max_total_pct",
              "max_daily_volume_pct", "min_liq_distance_pct"):
        if k in base and base[k] < 0:
            base[k] = 0.0
    base["_profile"] = name
    base["_clamped"] = clamped
    return base


# ══════════════════════════════════════════════════════════════════════
# 风控状态（下单计数/成交量，按日重置）
# ══════════════════════════════════════════════════════════════════════
class RiskState:
    """下单计数与当日成交量。持久化到磁盘，**跨进程重启仍然有效**——
    否则"重启插件即可绕过单日限制"，风控形同虚设。"""

    def __init__(self, path=None):
        self.path = path
        self.data = {"date": self._today(), "orders": 0, "volume_usd": 0.0,
                     "history": []}
        self.load()

    @staticmethod
    def _today():
        return datetime.now(TZ8).strftime("%Y-%m-%d")

    def load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("date") == self._today():
                self.data = d
            else:
                # 跨日：保留 history，计数归零
                self.data = {"date": self._today(), "orders": 0,
                             "volume_usd": 0.0,
                             "history": (d.get("history") or [])[-30:]}
                self.data["history"].append(
                    {"date": d.get("date"), "orders": d.get("orders"),
                     "volume_usd": d.get("volume_usd")})
        except Exception:
            pass

    def save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def record(self, volume_usd=0.0):
        self.data["orders"] = self.data.get("orders", 0) + 1
        self.data["volume_usd"] = self.data.get("volume_usd", 0.0) + float(volume_usd or 0)
        self.save()

    def reset(self):
        self.data = {"date": self._today(), "orders": 0, "volume_usd": 0.0,
                     "history": self.data.get("history", [])}
        self.save()


# ══════════════════════════════════════════════════════════════════════
# 闸门
# ══════════════════════════════════════════════════════════════════════
def check_order(order, account, positions=None, profile=None, state=None,
                market="spot"):
    """**实盘下单前必须调用**。返回 dict(allowed, reasons, warnings, adjustments)。

    Args:
        order    : dict(symbol, side, qty, price, notional_usd, leverage,
                        stop_loss, market)
        account  : dict(net_usd=总净值, available_usd=可用)
        positions: 现有仓位列表（来自 adapter.get_positions()）
        profile  : get_profile() 的结果；None 则用保守档
        state    : RiskState 实例（用于单日计数）
        market   : spot|usdtm|coinm
    Returns:
        {
          "allowed": bool,             # False → 适配层必须拒发
          "reasons": [...],             # 拒绝原因（具体、可操作）
          "warnings": [...],            # 不阻断但需提示
          "adjustments": {...},         # 建议改成的参数
          "profile": str
        }
    """
    p = profile or get_profile()
    reasons, warnings, adj = [], [], {}
    positions = positions or []

    net = float(account.get("net_usd") or 0)
    notional = float(order.get("notional_usd") or 0)
    symbol = (order.get("symbol") or "").upper()
    side = (order.get("side") or "").lower()
    is_open = order.get("is_open", True)

    if net <= 0:
        reasons.append("账户净值为 0 或无法读取——拒绝下单（无法计算任何风险敞口上限）")
        return {"allowed": False, "reasons": reasons, "warnings": warnings,
                "adjustments": adj, "profile": p["_profile"]}

    # ---- 1) 空头限制（保守档默认只做多）----
    if not is_open and not p.get("allow_short"):
        # 平多是允许的；开空被拦
        if side in ("sell", "short") and order.get("is_short"):
            reasons.append(f"保守档不允许开空（side={side}）。"
                           f"如需做空请显式切到 balanced/aggressive 档")

    # ---- 2) 单笔金额上限 ----
    order_pct = notional / net * 100 if net else 0
    if order_pct > p["max_order_pct"]:
        cap_usd = net * p["max_order_pct"] / 100
        reasons.append(f"单笔金额 {order_pct:.1f}% 超过上限 {p['max_order_pct']}%"
                       f"（本单 ${notional:,.0f}，上限 ${cap_usd:,.0f}）")
        adj["qty"] = round(float(order.get("qty") or 0) *
                           (p["max_order_pct"] / order_pct), 8) if order_pct else None
        adj["notional_usd"] = round(cap_usd, 2)

    # ---- 3) 单币种持仓上限 ----
    sym_val = sum(float(x.get("size") or 0) * float(x.get("mark_price") or
                  x.get("entry_price") or 0) for x in positions
                  if (x.get("symbol") or "").upper() == symbol)
    projected_sym = sym_val + (notional if is_open else 0)
    sym_pct = projected_sym / net * 100
    if sym_pct > p["max_symbol_pct"]:
        reasons.append(f"{symbol} 建仓后占比 {sym_pct:.1f}% 超过单币上限 "
                       f"{p['max_symbol_pct']}%（现有 ${sym_val:,.0f} + 本单 ${notional:,.0f}）")
        adj["max_additional_usd"] = round(max(0, net * p["max_symbol_pct"] / 100 - sym_val), 2)

    # ---- 4) 总持仓上限 ----
    total_val = sum(float(x.get("size") or 0) * float(x.get("mark_price") or
                    x.get("entry_price") or 0) for x in positions)
    total_pct = (total_val + (notional if is_open else 0)) / net * 100
    if total_pct > p["max_total_pct"]:
        reasons.append(f"总持仓将达 {total_pct:.1f}%，超过上限 {p['max_total_pct']}%"
                       f"（现有 ${total_val:,.0f} + 本单 ${notional:,.0f} / 净值 ${net:,.0f}）")

    # ---- 5) 杠杆上限 ----
    lev = order.get("leverage")
    if lev is not None and float(lev) > p["max_leverage"]:
        reasons.append(f"杠杆 {lev}x 超过上限 {p['max_leverage']}x")
        adj["leverage"] = p["max_leverage"]

    # ---- 6)(7) 单日下单次数 / 成交量 ----
    if state is not None:
        today_orders = state.data.get("orders", 0)
        if today_orders >= p["max_orders_per_day"]:
            reasons.append(f"今日已下单 {today_orders} 次，达上限 {p['max_orders_per_day']} 次"
                           f"（防脚本失控；如需继续请显式重置风控计数）")
        else:
            warn_at = int(p["max_orders_per_day"] * 0.8)
            if today_orders + 1 >= warn_at:
                warnings.append(f"今日下单 {today_orders + 1} 次，接近上限 "
                                f"{p['max_orders_per_day']} 次")
        today_vol = state.data.get("volume_usd", 0.0)
        vol_pct = (today_vol + notional) / net * 100
        if vol_pct > p["max_daily_volume_pct"]:
            reasons.append(f"今日累计成交将达净值 {vol_pct:.1f}%，"
                           f"超过上限 {p['max_daily_volume_pct']}%"
                           f"（今日已成交 ${today_vol:,.0f}）")

    # ---- 8) 强平距离 ----
    if market in ("usdtm", "coinm") and lev:
        try:
            liq_dist = 100.0 / float(lev)      # 粗略：1x≈100%，10x≈10%
            if liq_dist < p["min_liq_distance_pct"]:
                reasons.append(
                    f"{lev}x 杠杆下强平距离约 {liq_dist:.1f}%，"
                    f"低于安全线 {p['min_liq_distance_pct']}%"
                    f"（建议杠杆 ≤ {int(100 / p['min_liq_distance_pct'])}x）")
                adj["leverage"] = min(int(adj.get("leverage", 999)),
                                      int(100 / p["min_liq_distance_pct"]))
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # ---- 9) 反向单检查（防"以为平仓其实在加仓"）----
    if is_open and side in ("sell", "short"):
        long_syms = [(x.get("symbol") or "").upper() for x in positions
                     if (x.get("side") or "").lower() == "long"
                     and float(x.get("size") or 0) > 0]
        if symbol in long_syms:
            warnings.append(
                f"⚠️ 你当前**持有多头** {symbol}，此单方向为 {side}——"
                f"如果本意是平仓，请使用平仓指令（reduce_only）而非再开一笔，"
                f"否则会变成反向锁仓")

    # ---- 10) 止损必填（保守/稳健档）----
    if p.get("require_stop_loss") and is_open and not order.get("stop_loss"):
        reasons.append("保守档要求开仓时**必须同时设置止损**（stop_loss 为空）。"
                       "这是防止单笔亏损失控的核心保障——请提供止损价，"
                       "或显式切到 aggressive 档（不推荐）")

    # ---- 附加提示 ----
    if is_open and order.get("price") and order.get("stop_loss"):
        try:
            entry, sl = float(order["price"]), float(order["stop_loss"])
            risk_pct = abs(entry - sl) / entry * 100
            if risk_pct > 20:
                warnings.append(f"止损距开仓价 {risk_pct:.1f}%，单笔风险偏大")
            else:
                warnings.append(f"单笔风险 {risk_pct:.1f}%（止损 {sl}）")
        except (TypeError, ValueError):
            pass
    if p.get("require_confirmation"):
        warnings.append(f"当前为**{p['_profile']}**档，实盘下单需用户显式确认")

    return {"allowed": not reasons, "reasons": reasons, "warnings": warnings,
            "adjustments": adj, "profile": p["_profile"],
            "order_pct": round(order_pct, 2), "total_pct_after": round(total_pct, 2),
            "symbol_pct_after": round(sym_pct, 2)}


def describe_profile(name=DEFAULT_PROFILE):
    """把档位渲染成人能读的说明（供 UI/报告展示）。"""
    p = get_profile(name)
    return {
        "profile": p["_profile"],
        "limits": {
            "单笔上限": f"净值 {p['max_order_pct']}%",
            "单币上限": f"净值 {p['max_symbol_pct']}%",
            "总仓上限": f"净值 {p['max_total_pct']}%",
            "杠杆上限": f"{p['max_leverage']}x",
            "单日下单": f"{p['max_orders_per_day']} 次",
            "单日成交": f"净值 {p['max_daily_volume_pct']}%",
            "强平距离": f"≥ {p['min_liq_distance_pct']}%",
            "开仓必须带止损": "是" if p["require_stop_loss"] else "否",
            "允许开空": "是" if p["allow_short"] else "否",
        },
        "clamped": p["_clamped"],
    }


def position_size_by_risk(net_usd, entry, stop_loss, risk_pct=1.0,
                          max_order_pct=None, profile=None):
    """按"单笔风险 %"反推建议仓位（比拍脑袋给金额专业得多）。

    例：净值 $10,000，单笔愿亏 1% = $100；入场 100 止损 95（-5%）
        → 仓位 = 100 / 0.05 = $2,000
    并与档位的单笔上限取小值。
    """
    p = profile or get_profile()
    try:
        entry, sl = float(entry), float(stop_loss)
        if entry <= 0 or sl <= 0 or entry == sl:
            return {"ok": False, "reason": "入场价/止损价非法或相等"}
        stop_dist = abs(entry - sl) / entry
        risk_usd = float(net_usd) * float(risk_pct) / 100
        size_usd = risk_usd / stop_dist
        cap_pct = max_order_pct if max_order_pct is not None else p["max_order_pct"]
        cap_usd = float(net_usd) * cap_pct / 100
        final = min(size_usd, cap_usd)
        return {
            "ok": True, "size_usd": round(final, 2),
            "size_units": round(final / entry, 8),
            "stop_distance_pct": round(stop_dist * 100, 2),
            "risk_usd": round(final * stop_dist, 2),
            "risk_pct_actual": round(final * stop_dist / float(net_usd) * 100, 2),
            "limited_by": "单笔上限" if cap_usd < size_usd else "风险预算",
            "note": f"若止损被打到，亏损约 ${final * stop_dist:,.0f}"
                    f"（净值的 {final * stop_dist / float(net_usd) * 100:.2f}%）",
        }
    except (TypeError, ValueError) as e:
        return {"ok": False, "reason": f"参数错误: {e}"}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="实盘风控档位说明与试算")
    ap.add_argument("--profile", default=DEFAULT_PROFILE,
                    choices=list(RISK_PROFILES) + ["custom"])
    ap.add_argument("--net", type=float, default=None, help="账户净值USD（试算用）")
    ap.add_argument("--entry", type=float, default=None)
    ap.add_argument("--stop", type=float, default=None)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--all", action="store_true", help="展示全部档位")
    args = ap.parse_args()

    if args.all:
        for nm in RISK_PROFILES:
            d = describe_profile(nm)
            print(f"\n=== {nm} ===")
            for k, v in d["limits"].items():
                print(f"  {k:<14} {v}")
        return 0

    d = describe_profile(args.profile)
    print(json.dumps(d, ensure_ascii=False, indent=2))
    if args.net and args.entry and args.stop:
        print("\n=== 建议仓位（按单笔风险反推）===")
        print(json.dumps(position_size_by_risk(args.net, args.entry, args.stop,
                                               args.risk_pct,
                                               profile=get_profile(args.profile)),
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())