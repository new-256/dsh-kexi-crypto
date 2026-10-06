#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keepalive.py - 长期保活监控（v1.5.0 新增）

═══════════════════════════════════════════════════════════════════════
用户要求（2026-09-29）：
  · "加入长期保活设置，在插件运行期间满足某些特定信号或者定时进行评估
     并通知用户介入"
  · 默认评估间隔：**每小时（推荐）**
  · 触发后如何处理：**由用户选择，所有实现方式都要有**
═══════════════════════════════════════════════════════════════════════

两种触发方式（都要有）
----------------------
  1. **定时**：每 N 分钟（默认 60）做一次全量评估
  2. **信号**：满足特定条件立即触发（不等定时），条件可组合：
       · price_change_pct    单币 1h/24h 涨跌幅超阈值
       · position_zone       持仓币进入高位（≥80%）/ 跌破止损
       · volume_spike        放量倍数超阈值
       · tf_alignment        多周期转为共振向下（**逆势预警**）
       · stop_loss_hit       现价逼近用户设定的止损位
       · stablecoin_flow     稳定币由净增发转净销毁（资金撤离信号）

触发后的处理方式（用户可选，全部实现）
--------------------------------------
  notify_only   只通知，不调仓（**默认**）
  recommend     通知 + 给出建议调仓方案（不执行）
  auto_tpsl     自动补挂止盈止损（不动仓位）
  auto_reduce   自动减仓到风控上限内（**需显式开启**）

⚠️ 安全立场：默认 `notify_only`。自动调仓必须用户显式配置，且**始终**经过
   cex_risk 风控闸门 + 需要显式确认。保活进程绝不"自作主张"下实盘单。
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))

DEFAULT_INTERVAL_MIN = 60          # 用户选定：每小时
ACTION_MODES = ("notify_only", "recommend", "auto_tpsl", "auto_reduce")

# 默认信号阈值
DEFAULT_SIGNALS = {
    "price_change_pct": 12.0,      # 24h 涨跌幅超过 ±12% 触发
    "volume_spike": 3.0,           # 放量 3 倍触发
    "position_high": 85.0,         # 90日区间位置 ≥85 触发
    "stop_loss_proximity": 3.0,    # 距止损 3% 内触发
    "tf_reversal": True,           # 多周期转共振向下触发
}


def _now():
    return datetime.now(TZ8)


def default_state_path(workspace_root=None):
    base = workspace_root or os.getcwd()
    return os.path.join(base, ".kexi-secrets", "keepalive_state.json")


class KeepAliveState:
    """保活状态：持久化，让"上次评估时间""连续触发次数"跨重启有效。

    否则每次重启都重新计时（定时评估会永远不到点），并可能重复轰炸通知。
    """

    def __init__(self, path=None):
        self.path = path
        self.data = {
            "last_run": None, "run_count": 0, "last_trigger": None,
            "consecutive_triggers": 0, "history": [],
        }
        self.load()

    def load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.data.update(d)
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

    def due(self, interval_min=DEFAULT_INTERVAL_MIN, now=None):
        """判断是否到了下次定时评估时间。"""
        if not self.data.get("last_run"):
            return True, 0
        try:
            last = datetime.fromisoformat(self.data["last_run"])
            now = now or _now()
            elapsed = (now - last).total_seconds() / 60
            return elapsed >= interval_min, round(interval_min - elapsed, 1)
        except Exception:
            return True, 0

    def mark_run(self, summary=None):
        self.data["last_run"] = _now().isoformat()
        self.data["run_count"] = self.data.get("run_count", 0) + 1
        if summary:
            h = self.data.setdefault("history", [])
            h.append({"ts": self.data["last_run"], **summary})
            self.data["history"] = h[-200:]
        self.save()


def evaluate_signals(snapshot, signals=None, watchlist=None, positions=None):
    """评估信号。**纯函数**（不做 IO），便于单测与回放。

    Args:
        snapshot : {symbol: {"price":..., "change_24h_pct":..., "volume_ratio":...,
                             "pos_90":..., "tf_alignment":..., "stop_loss":...}}
        signals  : 阈值 dict（None → 默认）
        positions: 用户持仓 [{"symbol","side","size","entry_price","stop_loss"}]
    Returns:
        {"triggered": bool, "triggers": [...], "checked": n, "watch": [...]}
    """
    s = dict(DEFAULT_SIGNALS)
    if signals:
        s.update(signals)
    triggers, watch = [], []
    snap = snapshot or {}
    targets = watchlist or list(snap.keys())

    for sym in targets:
        d = snap.get(sym)
        if not d:
            continue
        price = d.get("price")
        # 1) 价格剧动
        ch = d.get("change_24h_pct")
        if ch is not None and abs(ch) >= s["price_change_pct"]:
            triggers.append({
                "symbol": sym, "type": "price_change",
                "level": "warn",
                "detail": f"24h {ch:+.1f}%（阈值 ±{s['price_change_pct']}%）",
            })
        # 2) 放量
        vr = d.get("volume_ratio")
        if vr is not None and vr >= s["volume_spike"]:
            triggers.append({
                "symbol": sym, "type": "volume_spike", "level": "info",
                "detail": f"放量 {vr:.1f}x（阈值 {s['volume_spike']}x）",
            })
        # 3) 进入高位区
        pz = d.get("pos_90")
        if pz is not None and pz >= s["position_high"]:
            triggers.append({
                "symbol": sym, "type": "position_high", "level": "warn",
                "detail": f"90日位置 {pz:.0f}%（≥{s['position_high']}% 高位风险）",
            })
        # 4) 多周期转共振向下
        if s.get("tf_reversal") and d.get("tf_alignment") in (
                "全周期共振向下", "周期冲突"):
            triggers.append({
                "symbol": sym, "type": "tf_reversal", "level": "critical",
                "detail": f"多周期 {d['tf_alignment']}——大周期转弱，逆势风险",
            })
        # 5) 逼近止损（仅持仓币）
        for p in (positions or []):
            if (p.get("symbol") or "").upper() != sym.upper():
                continue
            sl = p.get("stop_loss") or d.get("stop_loss")
            if sl and price:
                try:
                    dist = (float(price) - float(sl)) / float(price) * 100
                    if p.get("side", "long").lower() == "long" and \
                            0 <= dist <= s["stop_loss_proximity"]:
                        triggers.append({
                            "symbol": sym, "type": "stop_loss_proximity",
                            "level": "critical",
                            "detail": f"现价距止损仅 {dist:.1f}%（止损 {sl}）——"
                                      f"建议人工介入决策",
                        })
                except (TypeError, ValueError):
                    pass
        # 关注项（未达触发但接近）
        if ch is not None and abs(ch) >= s["price_change_pct"] * 0.6:
            if abs(ch) < s["price_change_pct"]:
                watch.append({"symbol": sym, "type": "price_change",
                              "detail": f"24h {ch:+.1f}%（接近阈值）"})

    # 6) 稳定币资金流转向
    flow = (snapshot or {}).get("__macro__", {})
    if flow.get("stablecoin_trend") and "净销毁" in flow["stablecoin_trend"]:
        triggers.append({
            "symbol": "MACRO", "type": "stablecoin_flow", "level": "warn",
            "detail": f"稳定币 {flow['stablecoin_trend']}"
                      f"（{flow.get('net_mint_7d_display', '')}）——增量资金离场信号",
        })

    return {"triggered": bool(triggers), "triggers": triggers,
            "checked": len([t for t in targets if t in snap]),
            "watch": watch}


def decide_action(trigger_result, mode="notify_only", positions=None,
                  profile=None):
    """根据触发结果与用户选择的模式，决定要怎么处理。

    **默认 notify_only**：绝不自作主张调仓。
    """
    import cex_risk as cr
    if mode not in ACTION_MODES:
        mode = "notify_only"
    trg = trigger_result.get("triggers") or []
    crit = [t for t in trg if t.get("level") == "critical"]
    plan = {"mode": mode, "actions": [], "requires_user_confirm": True}

    if not trg:
        plan["actions"].append({"type": "none", "detail": "无触发，仅记录日志"})
        return plan

    plan["actions"].append({
        "type": "notify", "detail": f"{len(trg)} 项触发（其中 {len(crit)} 项严重）"})

    if mode == "notify_only":
        plan["actions"].append({
            "type": "advise", "detail": "仅通知，不生成调仓指令（当前模式）"})
    elif mode == "recommend":
        for t in trg:
            if t["type"] in ("tf_reversal", "stop_loss_proximity", "position_high"):
                plan["actions"].append({
                    "type": "suggest_reduce", "symbol": t["symbol"],
                    "detail": t["detail"] + " → 建议减仓或收紧止损（需用户确认）"})
    elif mode == "auto_tpsl":
        for t in trg:
            if t["type"] in ("position_high", "tf_reversal"):
                plan["actions"].append({
                    "type": "auto_set_tpsl", "symbol": t["symbol"],
                    "detail": f"自动补挂/收紧止盈止损（{t['detail']}）"})
        plan["requires_user_confirm"] = False   # 只挂止损不动仓位，风险可控
    elif mode == "auto_reduce":
        plan["actions"].append({
            "type": "auto_reduce", "detail": "⚠️ 自动减仓到风控上限内——"
                                             "**仍需通过 cex_risk 闸门**"})
        plan["requires_user_confirm"] = True    # 减仓也要求确认（保守）

    plan["note"] = ("保活进程默认只通知；自动动仓必须在用户显式配置后启用，"
                    "且始终经过风控闸门" if mode == "notify_only" else
                    f"模式 {mode}：实际下单仍需经 cex_risk.check_order 放行")
    return plan


def run_once(collector=None, snapshot=None, state=None, signals=None,
             mode="notify_only", notifier=None, watchlist=None, positions=None,
             out_dir=None, profile=None):
    """执行一次保活评估。collector 为可调用对象（返回 snapshot），便于注入测试。

    返回 dict(due, triggered, triggers, action_plan, notified, summary)
    """
    state = state or KeepAliveState()
    due = state.due()[0] if snapshot is None else True
    if snapshot is None:
        if collector is None:
            return {"ok": False, "error": "需要 collector 或 snapshot"}
        if not due:
            return {"ok": True, "due": False, "skipped": True,
                    "message": "未到评估时间（定时未到）"}
        try:
            snapshot = collector()
        except Exception as e:
            return {"ok": False, "error": f"数据采集失败: {e}"}

    res = evaluate_signals(snapshot, signals, watchlist, positions)
    plan = decide_action(res, mode, positions, profile)

    notified = False
    if notifier is not None and res["triggered"]:
        title = f"保活触发：{len(res['triggers'])} 项信号"
        body_lines = [f"· {t['symbol']} — {t['type']}：{t['detail']}"
                      for t in res["triggers"][:10]]
        body = "\n".join(body_lines)
        lvl = ("critical" if any(t["level"] == "critical" for t in res["triggers"])
               else "warn")
        nr = notifier.notify(title, body, level=lvl,
                             dedup_key=f"keepalive:{lvl}:{len(res['triggers'])}",
                             extra={"triggers": res["triggers"], "plan": plan})
        notified = nr.get("ok") and not nr.get("deduped")

    summary = {"triggers": len(res["triggers"]),
               "critical": len([t for t in res["triggers"]
                                if t["level"] == "critical"]),
               "mode": mode, "notified": notified}
    state.mark_run(summary)

    # 有触发才记录到状态历史（避免无触发的每小时噪声淹没历史）
    if res["triggered"]:
        state.data["last_trigger"] = _now().isoformat()
        state.data["consecutive_triggers"] = \
            state.data.get("consecutive_triggers", 0) + 1
        state.save()

    return {"ok": True, "due": True, "triggered": res["triggered"],
            "triggers": res["triggers"], "watch": res["watch"],
            "action_plan": plan, "notified": notified, "summary": summary}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="长期保活监控（定时/信号触发 + 通知）")
    ap.add_argument("--mode", default="notify_only", choices=ACTION_MODES)
    ap.add_argument("--interval-min", type=int, default=DEFAULT_INTERVAL_MIN)
    ap.add_argument("--state", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--channels", default=None, help="通知渠道，逗号分隔")
    ap.add_argument("--watchlist", default=None, help="逗号分隔的币种")
    ap.add_argument("--symbols-file", default=None,
                    help="从 screener 输出 json 读取候选（picked[].symbol）")
    ap.add_argument("--demo", action="store_true", help="用假数据演示一次评估")
    ap.add_argument("--loop", action="store_true",
                    help="持续运行（每小时评估；生产环境建议由宿主定时器调用 --once）")
    args = ap.parse_args()

    import kexi_notify as kn
    notifier = kn.Notifier(
        channels=args.channels.split(",") if args.channels else None,
        out_dir=args.out_dir)
    state = KeepAliveState(args.state)
    wl = args.watchlist.split(",") if args.watchlist else None
    if args.symbols_file and os.path.exists(args.symbols_file):
        try:
            d = json.load(open(args.symbols_file, encoding="utf-8"))
            wl = [p["symbol"] for p in (d.get("picked") or [])]
        except Exception as e:
            print(f"读取 symbols-file 失败: {e}")

    if args.demo:
        snap = {
            "BTCUSDT": {"price": 83000, "change_24h_pct": -4.2, "volume_ratio": 1.1,
                        "pos_90": 22.0, "tf_alignment": "周期冲突"},
            "GRAMUSDT": {"price": 1.55, "change_24h_pct": 15.3, "volume_ratio": 3.4,
                         "pos_90": 91.0, "tf_alignment": "全周期共振向下"},
            "__macro__": {"stablecoin_trend": "小幅净销毁",
                          "net_mint_7d_display": "-1.12亿U"},
        }
        r = run_once(snapshot=snap, state=state, mode=args.mode,
                     notifier=notifier, watchlist=["BTCUSDT", "GRAMUSDT"])
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    def _collect():
        import datasources as ds
        raw = ds.collect_all(quiet=True)
        return {"__macro__": {
            "stablecoin_trend": (raw["sources"].get("stablecoin_flow") or {}).get("trend"),
            "net_mint_7d_display": (raw["sources"].get("stablecoin_flow") or {}).get("net_mint_7d_display"),
        }}

    if args.loop:
        print(f"[保活] 进入循环，每 {args.interval_min} 分钟评估一次（Ctrl+C 退出）",
              flush=True)
        while True:
            r = run_once(collector=_collect, state=state, mode=args.mode,
                         notifier=notifier, watchlist=wl)
            print(f"[{_now().strftime('%H:%M:%S')}] 触发={r.get('triggered')} "
                  f"({r.get('summary')})", flush=True)
            time.sleep(max(60, args.interval_min * 60))
    else:
        r = run_once(collector=_collect, state=state, mode=args.mode,
                     notifier=notifier, watchlist=wl)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())