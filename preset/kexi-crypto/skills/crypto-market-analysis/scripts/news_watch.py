#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_watch.py - 定时消息面监控（v1.5.1 新增）

背景（用户要求，2026-09-29）：
    "虚拟币行情、世界局势、暗网、社媒多方面信息应该占有很高比重；
     社区在无法正常通过 api 获取消息的情况下，是不是应该采取特殊方式
     获取消息，做一些定时获取消息的工具"

设计（三层获取策略，全部免 key、实测可用）：

  第 1 层  RSSHub 特殊通道（"特殊方式获取消息"）：
    经 RSSHub 镜像读 Telegram 频道——2026 年 X/Twitter 免费档只写不读、
    Nitter 全灭，而 RSSHub /telegram/channel/* 镜像实测 4/4 可用（2026-09-29）：
      · wublockchainenglish  吴说区块链英文（加密研究）
      · Cointelegraph        行情快讯
      · WatcherGuru          世界局势宏观快讯（JUST IN）
      · whale_alert          鲸鱼链上大额转账（B5 大额资金面）
    （DeItaone / BanklessDaily 实测 503，不接入——记录在案避免重复踩坑）

  第 2 层  常规通道（datasources.fetch_news）：
    BlockBeats 快讯 / 金色财经 / 币安公告（RSSHub 镜像多路轮询）

  第 3 层  降级标记：
    全部失败 → available=false + 「消息面缺失」显式标记，绝不阻塞主链。

暗网监控的立场（诚实边界）：
    暗网数据源（泄漏市场/勒索统计等）全部需要付费 API 或 Tor 网络访问，
    直接访问暗网在本工具的能力与安全边界之外。**不做**任何暗网爬取；
    暗网相关事件经由正规渠道（Cointelegraph/WatcherGuru 等快讯里提到的
    黑客/泄漏事件）覆盖。此边界向用户明示，不假装有暗网监控能力。

定时模式：
    --loop          常驻（每 interval_min 分钟拉一轮，默认 30）
    --once          单轮（默认）
    状态持久化 news_watch_state.json（去重已见标题 + 上次运行时间）
    新增标题触发 kexi_notify 通知（落盘渠道强制开启，绝不丢事件）

产出：
    kexi_out/news_watch.json   本轮快照（所有通道 + 新增项标记）
    kexi_notifications.jsonl   通知事件（kexi_notify 落盘）
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

import datasources as ds

TZ8 = timezone(timedelta(hours=8))

DEFAULT_INTERVAL_MIN = 30
MAX_SEEN_TITLES = 500          # 去重窗口（防状态文件无限膨胀）

# ── 特殊通道（RSSHub Telegram 镜像，实测 2026-09-29 4/4 可用）─────────────
TELEGRAM_FEEDS = [
    ("吴说区块链", "/telegram/channel/wublockchainenglish"),
    ("Cointelegraph", "/telegram/channel/Cointelegraph"),
    ("WatcherGuru世界局势", "/telegram/channel/WatcherGuru"),
    ("WhaleAlert鲸鱼", "/telegram/channel/whale_alert"),
]
# 实测不可用（503），记录在案：DeItaone、BanklessDaily、OdailyNewsDaily、
# techflowpost、WuBlockchain（中文版）。不要重新接入浪费时间。

# 分类关键词（把快讯粗分为 行情/宏观世界局势/大额资金/社媒热度）
TOPIC_KEYWORDS = {
    "行情": ["bitcoin", "btc", "eth", "ethereum", "price", "surge", "drop",
             "rally", "crash", "altcoin", "solana", "xrp", "gain", "loss",
             "etf", "futures", "long", "short"],
    "世界局势": ["fed", "利率", "关税", "tariff", "war", "冲突", "election",
                 "regulation", "sec", "监管", "ban", "制裁", "sanction",
                 "inflation", "cpi", "关税战", "trade war"],
    "大额资金": ["whale", "transfer", "moved", "usdt", "usdc", "inflow",
                 "outflow", "etf inflow", "stablecoin", "mint", "burn"],
    "社媒/热点": ["vitalik", "elon", "musk", "trump", "cz", "saylor",
                  "trending", " viral", "announc"],
}


def classify(title):
    """按关键词把一条快讯归入话题类。"""
    t = (title or "").lower()
    for topic, kws in TOPIC_KEYWORDS.items():
        for kw in kws:
            if kw in t:
                return topic
    return "其他"


def fetch_special_channels(limit_per_feed=5):
    """第 1 层：RSSHub Telegram 特殊通道（多镜像轮询 + 时间预算）。"""
    items, ok, fail = [], [], []
    t0 = time.time()
    for name, path in TELEGRAM_FEEDS:
        if time.time() - t0 > ds.NEWS_TIME_BUDGET_SEC:
            fail.append(f"{name}(时间预算用尽)")
            break
        got = None
        for mirror in ds.RSSHUB_MIRRORS:
            try:
                txt = ds.http_get_text(mirror + path, timeout=6, retries=1)
                got = ds._parse_rss(txt, name, limit=limit_per_feed)
                if got:
                    break
            except Exception:
                continue
        if got:
            items.extend(got)
            ok.append(name)
        else:
            fail.append(f"{name}(所有镜像失败)")
    # Telegram 条目补 topic 归类
    for it in items:
        it["topic"] = classify(it.get("title"))
        it["channel"] = "telegram@rsshub"
    return {
        "available": bool(items), "count": len(items), "items": items,
        "sources_ok": ok, "sources_fail": fail[:6],
    }


def fetch_regular_news():
    """第 2 层：常规中文消息面（datasources.fetch_news）。"""
    r = ds.fetch_news(limit_per_feed=5, max_total=12)
    for it in r.get("items", []):
        it["topic"] = classify(it.get("title"))
        it["channel"] = "rsshub"
    return r


def load_state(path):
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_run": None, "seen_titles": [], "run_count": 0}


def save_state(path, state):
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def watch_once(state=None, notify=True, out_dir=None):
    """跑一轮监控：三层获取 + 去重 + 通知。纯逻辑（IO 集中在此一处）。"""
    state = state or {"seen_titles": [], "run_count": 0}
    seen = set(state.get("seen_titles") or [])

    special = fetch_special_channels()
    regular = fetch_regular_news()

    all_items = (special.get("items") or []) + (regular.get("items") or [])
    fresh = [it for it in all_items if (it.get("title") or "")[:60] not in seen]

    # 去重窗口更新
    for it in fresh:
        seen.add((it.get("title") or "")[:60])
    state["seen_titles"] = list(seen)[-MAX_SEEN_TITLES:]
    state["run_count"] = state.get("run_count", 0) + 1
    state["last_run"] = datetime.now(TZ8).isoformat()

    # 通知（新增条目里挑高价值的：世界局势/大额资金/行情头 5 条）
    notified = False
    if notify and fresh:
        try:
            import kexi_notify as kn
            n = kn.Notifier(channels=None, out_dir=out_dir)
            prio = [it for it in fresh if it.get("topic") in ("世界局势", "大额资金")]
            head = (prio or fresh)[:5]
            body = "\n".join(
                f"· [{it.get('topic')}] {it.get('title', '')[:60]}" for it in head)
            nr = n.notify(
                f"消息面监控：{len(fresh)} 条新增（{len(prio)} 条高价值）",
                body, level="info",
                dedup_key=f"news-watch:{state['run_count']}")
            notified = bool(nr.get("ok")) and not nr.get("deduped")
        except Exception:
            notified = False

    # ── 陈旧条目剔除（v1.5.5）───────────────────────────────────────────
    # 实测教训：WhaleAlert 这个 RSSHub 频道自 2020-03-11 起就停更了，
    # 但抓取**不报错**——它会返回 20 条格式完好、时间戳指向 2020 年的旧帖。
    # 不做时间过滤的话，这些会被当作"当前鲸鱼动向"喂进研判，
    # 让 2020 年的转账看起来像今天的资金面证据。**过期即丢弃，并如实报告**。
    STALE_DAYS = 7
    now_dt = datetime.now(TZ8)
    kept, stale_dropped, stale_by_feed = [], 0, {}
    for it in all_items:
        raw = it.get("pub") or it.get("published") or ""
        age_ok = None
        for fmt in ("%a, %d %b %Y %H:%M:%S GMT", "%a, %d %b %Y %H:%M:%S %Z",
                    "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                t = datetime.strptime(str(raw).strip(), fmt)
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                age_ok = (now_dt - t.astimezone(TZ8)).days
                break
            except Exception:
                continue
        if age_ok is not None and age_ok > STALE_DAYS:
            stale_dropped += 1
            f = str(it.get("feed") or it.get("channel") or "?")
            stale_by_feed[f] = max(stale_by_feed.get(f, 0), age_ok)
            continue
        if age_ok is not None:
            it["age_days"] = age_ok
        kept.append(it)
    all_items = kept
    fresh = [it for it in kept if it.get("is_fresh", True)]

    # 话题分布统计（消息面"高比重"的量化呈现）
    topics = {}
    for it in all_items:
        t = it.get("topic", "其他")
        topics[t] = topics.get(t, 0) + 1

    snap = {
        "available": bool(all_items),
        "fetched_at": datetime.now(TZ8).isoformat(),
        "total": len(all_items), "fresh": len(fresh),
        "topics_count": topics,
        "special_channels": {
            "available": special["available"], "count": special["count"],
            "sources_ok": special["sources_ok"],
            "sources_fail": special["sources_fail"],
        },
        "regular_news": {
            "available": regular.get("available"),
            "count": regular.get("count", 0),
            "sources_ok": regular.get("sources_ok", []),
            "sources_fail": regular.get("sources_fail", []),
        },
        "fresh_items": fresh[:20],
        "stale_filter": {
            "enabled": True, "max_age_days": STALE_DAYS, "dropped": stale_dropped,
            "by_feed": stale_by_feed,
            "note": ("已丢弃 %d 条超过 %d 天的条目。WhaleAlert 的 RSSHub 频道自 2020-03-11 "
                     "起停更但**抓取不报错**，不过滤会把 2020 年的转账当成今天的资金面证据。"
                     % (stale_dropped, STALE_DAYS)) if stale_dropped else
                    "无过期条目",
        },
        "notified": notified,
        "dark_web_note": (
            "暗网数据源需付费 API/Tor 访问，超出本工具能力与安全边界；"
            "暗网相关事件（黑客/泄漏）经由 Cointelegraph/WatcherGuru 快讯覆盖，"
            "不假装有直接暗网监控能力"),
        "note": "" if all_items else
                "消息面全部通道不可用（RSSHub 镜像集体下线/网络中断）——"
                "显式标记「消息面缺失」，不阻塞技术面研判",
    }
    return snap, state


def main():
    import argparse
    ap = argparse.ArgumentParser(description="定时消息面监控（RSSHub 特殊通道 + 常规通道 + 降级标记）")
    ap.add_argument("--once", action="store_true", help="单轮（默认）")
    ap.add_argument("--loop", action="store_true", help="常驻（每 interval 分钟一轮）")
    ap.add_argument("--interval-min", type=int, default=DEFAULT_INTERVAL_MIN)
    ap.add_argument("--out-dir", default=None, help="产出目录（默认 kexi_out）")
    ap.add_argument("--no-notify", action="store_true", help="只落盘不通知")
    args = ap.parse_args()

    out_dir = args.out_dir or os.path.join(os.getcwd(), "kexi_out")
    os.makedirs(out_dir, exist_ok=True)
    state_path = os.path.join(out_dir, "news_watch_state.json")
    out_path = os.path.join(out_dir, "news_watch.json")

    def _run():
        state = load_state(state_path)
        snap, state = watch_once(state, notify=not args.no_notify, out_dir=out_dir)
        save_state(state_path, state)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        print(f"[消息面] 总 {snap['total']} 条 / 新增 {snap['fresh']} 条 "
              f"/ 话题 {snap['topics_count']} → {out_path}", flush=True)
        # 紧凑 JSON 摘要行（供 lastJsonLine 解析）
        print(json.dumps({
            "ok": True, "available": snap["available"], "total": snap["total"],
            "fresh": snap["fresh"], "topics": snap["topics_count"], "out": out_path,
        }, ensure_ascii=False), flush=True)
        return snap

    if args.loop:
        print(f"[消息面] 常驻模式，每 {args.interval_min} 分钟一轮（Ctrl+C 退出）", flush=True)
        while True:
            _run()
            time.sleep(max(60, args.interval_min * 60))
    _run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
