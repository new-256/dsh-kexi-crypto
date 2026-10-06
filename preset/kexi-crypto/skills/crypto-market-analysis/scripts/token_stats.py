#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
token_stats.py - Token 消耗统计（v1.5.0 新增）

═══════════════════════════════════════════════════════════════════════
用户要求（2026-09-29）：
  · "在每次由用户发起的评估任务完成后，向用户给出 token 消耗，
     输出/写入/缓存命中等等"
  · "每天统计一次当天 token 消耗（用户/触发任务/定时任务）"
═══════════════════════════════════════════════════════════════════════

⚠️ 数据来源与**已实测确认**的坑（务必保留）
--------------------------------------------
会话日志：`$DSH_HOME/sessions/<编码cwd>/<sessionId>/session.v4.jsonl.zstd`

坑 1：**多帧 zstd**。文件由多个 zstd 帧顺序拼接，`ZstdDecompressor().decompress()`
      只能解出第一帧。**必须用 `stream_reader(f).read()`**，否则会静默丢掉后面
      绝大部分记录（实测 3.3MB 文件只解出几百行）。

坑 2：**usage 会重复计**。同一次模型调用既写在 `assistant/message.data.usage`，
      又冗余写进 `assistant/message.data.stream[*].chunk.usage`，
      两者数值**完全相同**。实测：连带 chunk 统计 = 10.08M 输入，
      不连带 = 5.04M… → **只统计 `data.usage` 且排除带 `stream` 的 chunk 路径**，
      否则输入/缓存读会**翻倍**虚报。

坑 3：`assistant/attempt` 记录也带 usage 字段，但**其 inputTokens/outputTokens
      全为 0**（实测合计 0）。计入无害，但为避免未来结构变化导致重复，
      本模块**只认** `assistant/message` 与 `compaction/summary` 两类。

坑 4：**缓存命中率的分母**。命中率 = cacheRead / (cacheRead + input)。
      注意 `inputTokens` 在带缓存时是"未命中部分"，所以这个口径才对；
      用 cacheRead/totalTokens 会得出偏高的假命中率。

任务分类（用户/触发/定时）
--------------------------
日志本身不直接标注任务来源，故按**可观测特征**归类：
  · scheduled  定时任务：会话/回合由保活调度器发起（有 keepalive 标记）
  · triggered  信号触发：由 keepalive 信号触发
  · user       用户发起：其余（默认）
分类依据写在 `classify_turn()` 里，**可审计、不猜**。
"""

import glob
import json
import os
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))

# 只统计这两类记录（见上方"坑 3"）
COUNTED_TYPES = ("assistant/message", "compaction/summary")


def _decode_dirname(name):
    """把会话目录名解码回真实路径，用于与 cwd 比对。

    DSH 的编码（**实测反推确认**，与直觉不同）：
      · 非 ASCII 字符按 **UTF-16BE 码元** 编成 `~XXXX~`——
        是**每字符一个 4 位码元**，不是每字节一个！`插`(U+63D2) → `~63D2~`。
      · `:` 去掉、`\\` → `-`，首尾各补 `-`

    ⚠️ 连续中文时形如 `~63D2~4EF6~5F00~53D1-K~6790~...` —— **相邻码元共享中间的 `~`**，
    且**最后一个码元后面跟的是 `-` 或结尾（没有终结 `~`）**。
    所以既不能"解码一个就跳到下一个 `~` 之后"（会吃掉一个码元），
    也不能要求"后面必须有 `~`"（末尾那个会漏掉）。

    实测踩过三次：
      1) 按"每字节两位"编码 → 编出 `~63~~D2~...`，目录名对不上；
      2) 按"跳到下一个~"解码 → `DSH插件开发` 解成 `DSH插5F00~53D1`；
      3) 用 `(?=~)` 前瞻 → 末尾码元（后面是 `-`）漏解 → `DSH插件开53D1`。
    最终规则：**前瞻 `~` / `-` / 结尾三种边界**。
    """
    import re
    s = name.strip("-")
    s = re.sub(r"~([0-9A-Fa-f]{4})(?=~|$|-)", _dehex, s)
    s = s.replace("~", "")            # 清掉剩余的终结符
    return s.replace("-", "\\")


def _dehex(m):
    try:
        return bytes.fromhex(m.group(1)).decode("utf-16-be")
    except Exception:
        return m.group(0)


def _norm(p):
    return (p or "").replace("/", "\\").rstrip("\\").lower()


def sessions_dirs(cwd=None, dsh_home=None, walk_up=True):
    """返回**所有**相关会话目录（可能多个），按"最匹配优先"排序。

    为什么返回列表而不是单个：脚本常从子目录（如 scripts/）被调用，
    而会话记录是按**编辑时的工作目录**归集的（项目根）。实测：从
    `.../scripts` 调用时按精确匹配找不到任何目录 → 必须向上回退到
    项目根，并把沿途目录都纳入候选。

    walk_up=True 时，会从 cwd 逐级向上查找，收集所有能解码匹配的目录。
    """
    home = dsh_home or os.environ.get("DSH_HOME") or ""
    base = os.path.join(home, "sessions")
    if not os.path.isdir(base):
        return []
    cwd = cwd or os.getcwd()

    decoded = {}
    try:
        entries = [d for d in os.listdir(base)
                   if os.path.isdir(os.path.join(base, d))]
    except Exception:
        return []
    for d in entries:
        decoded[d] = _norm(_decode_dirname(d))

    results, seen = [], set()

    def add(dirname):
        if dirname and dirname not in seen:
            seen.add(dirname)
            results.append(os.path.join(base, dirname))

    # 1) 精确匹配 cwd 及其逐级父目录
    probe = cwd
    while True:
        w = _norm(probe)
        for d, got in decoded.items():
            if got == w:
                add(d)
        if not walk_up:
            break
        parent = os.path.dirname(probe)
        if not parent or parent == probe:
            break
        probe = parent

    # 2) 宽松回退：目录名解码后是 cwd 的前缀/后缀（应对编码规则微调）
    if not results:
        w = _norm(cwd)
        for d, got in decoded.items():
            if got and (w.startswith(got) or got.startswith(w)
                        or (len(got) > 12 and got[-12:] in w)):
                add(d)
    return results


def sessions_dir(cwd=None, dsh_home=None):
    """兼容接口：返回最匹配的单个会话目录（见 sessions_dirs）。"""
    ds = sessions_dirs(cwd, dsh_home)
    return ds[0] if ds else None


def iter_usage(session_dirs, since=None, until=None, cwd_filter=None):
    """逐个会话解压并产出 usage 记录。**已排除 chunk 重复项**。

    Args:
        session_dirs: 字符串或列表（见 sessions_dirs()）
    Yields: dict(session, ts, day, usage, counted_type)
    """
    try:
        import zstandard
    except ImportError:
        raise RuntimeError("需要 zstandard：pip install zstandard")
    if isinstance(session_dirs, str):
        session_dirs = [session_dirs]
    session_dirs = [d for d in (session_dirs or []) if d and os.path.isdir(d)]
    if not session_dirs:
        return
    for sd in session_dirs:
        files = glob.glob(os.path.join(sd, "**", "session.v4.jsonl.zstd"),
                          recursive=True)
        for fp in files:
            dctx = zstandard.ZstdDecompressor()
            sid = os.path.basename(os.path.dirname(fp))
            try:
                # ★ 多帧：必须 stream_reader，decompress() 只会解第一帧
                with open(fp, "rb") as f:
                    text = dctx.stream_reader(f).read().decode("utf-8", "replace")
            except Exception:
                continue
            for ln in text.splitlines():
                if not ln.strip():
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                t = r.get("type")
                if t not in COUNTED_TYPES:
                    continue
                d = r.get("data") or {}
                # ★ 关键：只取 data.usage，**绝不**下钻 data.stream[*].chunk.usage
                u = d.get("usage")
                if not isinstance(u, dict):
                    continue
                ts = (r.get("time") or r.get("ts") or r.get("timestamp")
                      or r.get("createdAt") or d.get("time") or d.get("ts"))
                dt = _parse_ts(ts)
                if since and dt and dt < since:
                    continue
                if until and dt and dt > until:
                    continue
                yield {"session": sid, "file": fp, "ts": dt, "type": t,
                       "usage": u, "raw": r}


def _parse_ts(ts):
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            v = ts / 1000 if ts > 1e11 else ts
            return datetime.fromtimestamp(v, TZ8)
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ8)
    except Exception:
        return None


def norm_usage(u):
    """标准化 usage 字段（兼容不同命名）。"""
    def g(*names):
        for n in names:
            if n in u and u[n] is not None:
                return int(u[n])
        return 0
    inp = g("inputTokens", "input_tokens", "promptTokens", "prompt_tokens")
    out = g("outputTokens", "output_tokens", "completionTokens", "completion_tokens")
    cr = g("cacheReadTokens", "cache_read_tokens", "cachedTokens", "cached_tokens")
    cw = g("cacheWriteTokens", "cache_write_tokens", "cacheCreationTokens")
    # 注意：带缓存时 inputTokens 是"未命中部分"，total 可能是总和
    tot = g("totalTokens", "total_tokens") or (inp + out + cr + cw)
    return {"input": inp, "output": out, "cache_read": cr, "cache_write": cw,
            "total": tot}


def aggregate(records):
    """把 usage 记录聚合成统计量。"""
    acc = {"turns": 0, "input": 0, "output": 0, "cache_read": 0,
           "cache_write": 0, "total": 0, "sessions": set(), "by_type": {},
           "by_session": {}, "by_hour": {}}
    for r in records:
        n = norm_usage(r["usage"])
        acc["turns"] += 1
        for k in ("input", "output", "cache_read", "cache_write", "total"):
            acc[k] += n[k]
        if r.get("session"):
            acc["sessions"].add(r["session"])
            s = acc["by_session"].setdefault(r["session"], {
                "turns": 0, "input": 0, "output": 0, "cache_read": 0, "total": 0})
            s["turns"] += 1
            for k in ("input", "output", "cache_read", "total"):
                s[k] += n[k]
        t = acc["by_type"].setdefault(r["type"], {"turns": 0, "input": 0,
                                                  "output": 0, "cache_read": 0})
        t["turns"] += 1
        for k in ("input", "output", "cache_read"):
            t[k] += n[k]
        if r.get("ts"):
            h = r["ts"].strftime("%Y-%m-%d %H")
            acc["by_hour"][h] = acc["by_hour"].get(h, 0) + n["total"]
    # 命中率口径（见文档"坑 4"）
    denom = acc["cache_read"] + acc["input"]
    acc["cache_hit_rate"] = round(acc["cache_read"] / denom * 100, 2) if denom else 0.0
    acc["sessions"] = sorted(acc["sessions"])
    acc["session_count"] = len(acc["sessions"])
    return acc


def classify_turn(rec):
    """判定该回合属于 用户 / 触发 / 定时。

    依据（**可审计，不猜**）：
      · 记录或其所在会话含 keepalive / 保活 / scheduled 标记 → scheduled
      · 含 signal / 触发 / alert 标记 → triggered
      · 其余 → user
    日志目前不显式标注，故先看显式字段，再看文本特征。
    """
    txt = json.dumps(rec, ensure_ascii=False)[:4000].lower()
    for kw in ("keepalive", "scheduled_task", "保活", "定时评测", "定时任务"):
        if kw in txt:
            return "scheduled"
    for kw in ("signal_trigger", "triggered_by", "信号触发", "告警触发"):
        if kw in txt:
            return "triggered"
    return "user"


def daily_report(day=None, cwd=None, session_dir=None, by_category=False):
    """生成某一天的 token 消耗报告。

    Args:
        day : "YYYY-MM-DD"（默认今天，东八区）
    Returns: dict(date, totals, by_type, by_session, cache_hit_rate, by_category)
    """
    if day is None:
        day = datetime.now(TZ8).strftime("%Y-%m-%d")
    sds = ([session_dir] if isinstance(session_dir, str)
           else (session_dir or sessions_dirs(cwd)))
    if not sds:
        return {"ok": False, "error": f"未找到会话目录（cwd={cwd or os.getcwd()}）",
                "hint": "确认 DSH_HOME 环境变量与会话目录结构"}

    recs = []
    for r in iter_usage(sds):
        if r.get("ts") and r["ts"].strftime("%Y-%m-%d") == day:
            recs.append(r)
    acc = aggregate(recs)
    out = {"ok": True, "date": day, "session_dir": sds[0],
           "session_dirs": sds, **acc}
    if by_category:
        cat = {}
        for r in recs:
            c = classify_turn(r["raw"])
            n = norm_usage(r["usage"])
            d = cat.setdefault(c, {"turns": 0, "input": 0, "output": 0,
                                   "cache_read": 0, "total": 0})
            d["turns"] += 1
            for k in ("input", "output", "cache_read", "total"):
                d[k] += n[k]
        for c, d in cat.items():
            dd = d["cache_read"] + d["input"]
            d["cache_hit_rate"] = round(d["cache_read"] / dd * 100, 2) if dd else 0.0
        out["by_category"] = cat
    return out


def session_report(session_id=None, cwd=None, session_dir=None, limit=5):
    """单次任务（会话）的 token 消耗——用于"每次评估任务完成后"汇报。"""
    sds = ([session_dir] if isinstance(session_dir, str)
           else (session_dir or sessions_dirs(cwd)))
    if not sds:
        return {"ok": False, "error": "未找到会话目录"}
    all_recs = list(iter_usage(sds))
    if not all_recs:
        return {"ok": False, "error": "无 usage 记录"}
    if session_id is None:
        # 取最近活跃的会话
        latest = {}
        for r in all_recs:
            if r.get("ts"):
                latest[r["session"]] = max(latest.get(r["session"], r["ts"]), r["ts"])
        if latest:
            session_id = max(latest, key=latest.get)
    recs = [r for r in all_recs if r["session"] == session_id]
    acc = aggregate(recs)
    return {"ok": True, "session": session_id, "session_dir": sds[0], **acc}


def format_report(rep, title=None):
    """把报告渲染成人类可读的文本（供插件直接贴给用户）。"""
    if not rep.get("ok"):
        return f"❌ Token 统计失败：{rep.get('error')}"
    L = []
    L.append(title or (f"## Token 消耗 · {rep.get('date') or rep.get('session')}"))
    L.append("")
    L.append(f"- 回合数：**{rep['turns']}**（会话 {rep.get('session_count', 1)} 个）")
    L.append(f"- 输入（未命中缓存）：**{rep['input']:,}**")
    L.append(f"- 输出：**{rep['output']:,}**")
    L.append(f"- 缓存读：**{rep['cache_read']:,}**")
    if rep.get("cache_write"):
        L.append(f"- 缓存写：**{rep['cache_write']:,}**")
    L.append(f"- **缓存命中率：{rep['cache_hit_rate']}%**"
             f"（= 缓存读 /(缓存读+输入)）")
    L.append(f"- 合计：**{rep['total']:,}** tokens")
    if rep.get("by_type"):
        L.append("")
        L.append("| 记录类型 | 回合 | 输入 | 输出 | 缓存读 |")
        L.append("|---|---|---|---|---|")
        for t, d in sorted(rep["by_type"].items(), key=lambda x: -x[1]["turns"]):
            L.append(f"| {t} | {d['turns']} | {d['input']:,} | {d['output']:,} | "
                     f"{d['cache_read']:,} |")
    if rep.get("by_category"):
        L.append("")
        L.append("| 任务来源 | 回合 | 输入 | 输出 | 缓存读 | 命中率 |")
        L.append("|---|---|---|---|---|---|")
        names = {"user": "用户发起", "triggered": "信号触发", "scheduled": "定时任务"}
        for c, d in sorted(rep["by_category"].items(), key=lambda x: -x[1]["turns"]):
            L.append(f"| {names.get(c, c)} | {d['turns']} | {d['input']:,} | "
                     f"{d['output']:,} | {d['cache_read']:,} | {d['cache_hit_rate']}% |")
    if rep.get("by_session") and rep.get("session_count", 1) > 1:
        L.append("")
        L.append("| 会话 | 回合 | 合计 |")
        L.append("|---|---|---|")
        for s, d in sorted(rep["by_session"].items(),
                           key=lambda x: -x[1]["total"])[:8]:
            L.append(f"| `{s[:20]}` | {d['turns']} | {d['total']:,} |")
    return "\n".join(L)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="K析 Token 消耗统计")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD（默认今天）")
    ap.add_argument("--session", action="store_true", help="统计最近一次任务")
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--category", action="store_true",
                    help="附按任务来源(用户/触发/定时)拆分")
    ap.add_argument("--days", type=int, default=0, help="输出最近 N 天汇总")
    args = ap.parse_args()

    if args.days:
        from datetime import timedelta as _td
        today = datetime.now(TZ8)
        rows = []
        for i in range(args.days):
            d = (today - _td(days=i)).strftime("%Y-%m-%d")
            r = daily_report(d, args.cwd, by_category=args.category)
            if r.get("ok") and r["turns"]:
                rows.append(r)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"# Token 消耗 · 最近 {args.days} 天\n")
            print("| 日期 | 回合 | 输入 | 输出 | 缓存读 | 命中率 | 合计 |")
            print("|---|---|---|---|---|---|---|")
            for r in rows:
                print(f"| {r['date']} | {r['turns']} | {r['input']:,} | "
                      f"{r['output']:,} | {r['cache_read']:,} | "
                      f"{r['cache_hit_rate']}% | {r['total']:,} |")
            if rows:
                print(f"\n合计 {sum(r['total'] for r in rows):,} tokens / "
                      f"{sum(r['turns'] for r in rows)} 回合")
        return 0

    rep = (session_report(args.session_id, args.cwd) if args.session
           else daily_report(args.day, args.cwd, by_category=args.category))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_report(rep))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())