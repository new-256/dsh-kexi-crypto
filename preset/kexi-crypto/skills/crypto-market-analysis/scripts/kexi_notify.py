#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kexi_notify.py - 通知渠道适配（v1.5.0 新增）

═══════════════════════════════════════════════════════════════════════
用户选择（2026-09-29）通知渠道：**企业微信 / WorkBuddy 内通知 / 仅落盘 / qqbot 等**
（"由用户选择，所有实现方式都要有"）
═══════════════════════════════════════════════════════════════════════

设计原则
--------
1. **落盘永远是兜底**：无论是否配置了外部渠道，通知都写入本地文件。
   这样"渠道全挂"也不会丢事件（保活场景下这点尤其重要）。
2. **渠道彼此独立**：一个渠道失败不影响其他渠道；失败会记录但不抛出。
3. **零密钥硬编码**：webhook / token 从环境变量或配置文件读取。
4. **去重与静默**：同一信号在静默期内不重复打扰（避免"每小时都喊一次"）。

支持渠道
--------
  file        落盘（**始终启用**，无法关闭）
  wecom       企业微信群机器人 webhook
  workbuddy   DSH WorkBuddy 内通知（写事件文件，由宿主插件读取展示）
  qqbot       QQ 机器人（OneBot v11 HTTP 上报接口）
  stdout      控制台（调试用）
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
UA = {"User-Agent": "kexi-notify/1.0", "Content-Type": "application/json"}

CHANNELS = ("file", "wecom", "workbuddy", "qqbot", "stdout")

# 默认静默期：同一 dedup_key 在此时长内不重复发送
DEFAULT_QUIET_SEC = 3600


def _now():
    return datetime.now(TZ8)


class Notifier:
    def __init__(self, channels=None, out_dir=None, quiet_sec=DEFAULT_QUIET_SEC,
                 wecom_webhook=None, qqbot_url=None, workbuddy_dir=None):
        """
        Args:
            channels       : 启用的渠道列表（None → 读 KEXI_NOTIFY_CHANNELS 环境变量，
                             默认仅 file+stdout）
            out_dir        : 落盘目录（事件 jsonl）
            quiet_sec      : 静默期（秒），0 = 不去重
            wecom_webhook  : 企业微信机器人 webhook URL（或环境变量 KEXI_WECOM_WEBHOOK）
            qqbot_url      : OneBot HTTP 上报地址（或 KEXI_QQBOT_URL）
            workbuddy_dir  : WorkBuddy 事件投递目录
        """
        env_ch = os.environ.get("KEXI_NOTIFY_CHANNELS", "")
        self.channels = [c.strip() for c in
                         (channels or (env_ch.split(",") if env_ch else ["file", "stdout"]))
                         if c.strip() in CHANNELS]
        if "file" not in self.channels:
            self.channels.insert(0, "file")     # 落盘不可关闭
        self.out_dir = out_dir or os.getcwd()
        self.quiet_sec = quiet_sec
        self.wecom_webhook = wecom_webhook or os.environ.get("KEXI_WECOM_WEBHOOK", "")
        self.qqbot_url = qqbot_url or os.environ.get("KEXI_QQBOT_URL", "")
        self.workbuddy_dir = workbuddy_dir or os.environ.get(
            "KEXI_WORKBUDDY_DIR",
            os.path.join(os.path.expanduser("~"), ".kexi-notify"))
        self._seen = {}          # dedup_key → last_ts
        self.sent_log = []

    # ---------- 落盘 ----------
    def _file_path(self):
        os.makedirs(self.out_dir, exist_ok=True)
        return os.path.join(self.out_dir, "kexi_notifications.jsonl")

    def _write_file(self, event):
        p = self._file_path()
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return {"channel": "file", "ok": True, "path": p}

    # ---------- 企业微信 ----------
    def _send_wecom(self, event):
        if not self.wecom_webhook:
            return {"channel": "wecom", "ok": False,
                    "error": "未配置 webhook（环境变量 KEXI_WECOM_WEBHOOK）"}
        md = (f"## {event['level_icon']} {event['title']}\n"
              f"> {event['body']}\n"
              f"<font color=\"comment\">{event['ts']}</font>")
        payload = {"msgtype": "markdown", "markdown": {"content": md}}
        return self._post(self.wecom_webhook, payload, "wecom")

    # ---------- qqbot (OneBot v11) ----------
    def _send_qqbot(self, event):
        if not self.qqbot_url:
            return {"channel": "qqbot", "ok": False,
                    "error": "未配置上报地址（环境变量 KEXI_QQBOT_URL）"}
        text = f"{event['level_icon']} {event['title']}\n{event['body']}"
        payload = {"action": "send_private_msg",
                   "params": {"user_id": os.environ.get("KEXI_QQ_USER", ""),
                              "message": text}}
        return self._post(self.qqbot_url, payload, "qqbot")

    def _post(self, url, payload, name, timeout=10):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(), headers=UA, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode()[:200]
            return {"channel": name, "ok": True, "resp": body}
        except urllib.error.HTTPError as e:
            return {"channel": name, "ok": False,
                    "error": f"HTTP {e.code}: {e.read().decode()[:150]}"}
        except Exception as e:
            return {"channel": name, "ok": False, "error": str(e)[:150]}

    # ---------- WorkBuddy（宿主插件读取事件文件展示）----------
    def _send_workbuddy(self, event):
        try:
            os.makedirs(self.workbuddy_dir, exist_ok=True)
            fname = f"evt-{int(time.time()*1000)}-{event['level']}.json"
            p = os.path.join(self.workbuddy_dir, fname)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(event, f, ensure_ascii=False, indent=2)
            return {"channel": "workbuddy", "ok": True, "path": p}
        except Exception as e:
            return {"channel": "workbuddy", "ok": False, "error": str(e)[:150]}

    # ---------- stdout ----------
    def _send_stdout(self, event):
        print(f"[通知/{event['level']}] {event['title']} — {event['body']}", flush=True)
        return {"channel": "stdout", "ok": True}

    # ---------- 主入口 ----------
    def notify(self, title, body, level="info", dedup_key=None, force=False,
               extra=None):
        """发通知。返回 dict(ok, event, results, deduped)。

        level: info | warn | critical | success
        dedup_key: 相同 key 在 quiet_sec 内不重复发送（force=True 可强制）
        """
        icons = {"info": "ℹ️", "warn": "⚠️", "critical": "🚨", "success": "✅"}
        now = _now()
        key = dedup_key or f"{level}:{title}"
        if not force and self.quiet_sec > 0:
            last = self._seen.get(key)
            if last and (time.time() - last) < self.quiet_sec:
                return {"ok": True, "deduped": True, "results": [],
                        "message": f"静默期内（{int(self.quiet_sec - (time.time()-last))}s 后可再发）"}
        self._seen[key] = time.time()

        event = {
            "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
            "level": level, "level_icon": icons.get(level, "•"),
            "title": title, "body": body, "dedup_key": key,
            "extra": extra or {},
        }
        results = []
        for ch in self.channels:
            try:
                if ch == "file":
                    results.append(self._write_file(event))
                elif ch == "wecom":
                    results.append(self._send_wecom(event))
                elif ch == "qqbot":
                    results.append(self._send_qqbot(event))
                elif ch == "workbuddy":
                    results.append(self._send_workbuddy(event))
                elif ch == "stdout":
                    results.append(self._send_stdout(event))
            except Exception as e:
                results.append({"channel": ch, "ok": False, "error": str(e)[:150]})
        self.sent_log.append({"event": event, "results": results})
        ok_any = any(r.get("ok") for r in results)
        return {"ok": ok_any, "deduped": False, "event": event, "results": results}


def test_channels(out_dir=None, dry=True):
    """自检：逐个渠道报告可用性（不实际发骚扰消息，除非 dry=False）。"""
    import tempfile
    d = out_dir or tempfile.mkdtemp(prefix="kexi-notify-")
    n = Notifier(channels=list(CHANNELS), out_dir=d, quiet_sec=0)
    report = []
    for ch in CHANNELS:
        if ch == "file":
            r = n._write_file({"ts": "test", "level": "info", "level_icon": "ℹ️",
                               "title": "自检", "body": "落盘测试", "extra": {}})
        elif ch == "wecom":
            r = ({"channel": "wecom", "ok": True, "note": "已配置，未实际发送"}
                 if n.wecom_webhook else
                 {"channel": "wecom", "ok": False, "error": "未配置 KEXI_WECOM_WEBHOOK"})
        elif ch == "qqbot":
            r = ({"channel": "qqbot", "ok": True, "note": "已配置，未实际发送"}
                 if n.qqbot_url else
                 {"channel": "qqbot", "ok": False, "error": "未配置 KEXI_QQBOT_URL"})
        elif ch == "workbuddy":
            r = n._send_workbuddy({"ts": "test", "level": "info", "level_icon": "ℹ️",
                                   "title": "自检", "body": "投递测试", "extra": {}})
        else:
            r = {"channel": "stdout", "ok": True, "note": "可用"}
        report.append(r)
    return {"out_dir": d, "channels": n.channels, "report": report}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="通知渠道自检/发送")
    ap.add_argument("--test", action="store_true", help="逐渠道自检")
    ap.add_argument("--send", action="store_true", help="实际发送一条测试通知")
    ap.add_argument("--title", default="K析研判团 · 测试通知")
    ap.add_argument("--body", default="这是一条渠道连通性测试。")
    ap.add_argument("--channels", default=None, help="逗号分隔，默认读环境变量")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    if args.send:
        chs = args.channels.split(",") if args.channels else None
        n = Notifier(channels=chs, out_dir=args.out_dir, quiet_sec=0)
        r = n.notify(args.title, args.body, level="info", force=True)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1

    print(json.dumps(test_channels(args.out_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())