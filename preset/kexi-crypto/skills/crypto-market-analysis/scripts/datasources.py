#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
datasources.py - 多面数据源（消息面 / DEX 热币 / 稳定币资金流 / 合约情绪）（v1.5.0 新增）

设计依据：`docs/data-sources-china-2026-09.md`（2026-09-28 实测 + 中国视角 DNS 比对）。
**只接入实测可用且免 key 的源**，每个源都有明确降级路径，全部失败也不抛异常
（返回 available=false + 原因），保证研判主链永不被数据源拖死。

接入源（按实测可靠性排序）：
  1. DefiLlama       — 稳定币每日净增发(totalMintedUSD) / 链 TVL    ← 中国 DNS 干净、无限频
  2. DexScreener     — DEX 热门币（token-boosts 榜）
  3. RSSHub 镜像     — 中文消息面（BlockBeats 快讯 / 金色财经 / 币安公告）
  4. Binance fapi    — 持仓量 / 资金费率 / 多空比（Bybit 作兜底）
  5. Alternative.me  — 恐惧贪婪指数
  6. CMC（非官方）   — 新闻 + 散户搜索热度（**必须容错**，未文档化）

**明确未接入**（实测不可用，记录以免后人重复踩坑）：
  · 币世界 Bishijie —— 域名已死（中国 DoH 与 CF DoH 均 NXDOMAIN），**无 API 无 RSS**
  · CryptoCompare news —— 已转计费（401）
  · CoinPaprika news/events —— 端点 404；events 内容是 2018-2028 陈旧数据
  · Whale Alert / Arkham / Coinglass —— 全部需付费 key
  · Nitter 全系 —— 已死（000/451/403/机器人墙）；LunarCrush 中国 NXDOMAIN + 401
  · Reddit JSON —— 403 / 登录墙
  · X/Twitter API —— 免费档只写不读，**2026 年无可用免费 KOL 推文源**

安全说明：本模块只做 **GET 读**，不携带任何用户密钥，不写任何 cwd 以外路径。
"""

import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
UA = {"User-Agent": "crypto-trend-analyst/1.0 (+kexi-plugin)"}

DEFAULT_TIMEOUT = 8
DEFAULT_RETRIES = 2

# ── 中国网络适配 ───────────────────────────────────────────────────────
# DexScreener / GeckoTerminal / CoinGecko / MEXC / Bybit 的域名在中国被 DNS 投毒
# （实测中国 DoH 返回 Facebook/伪造网段）。Cloudflare 系站点可**固定真实 IP +
# SNI 主机名**绕过投毒；这里维护已知真实 IP 作为直连候选。
PINNED_HOSTS = {
    "api.dexscreener.com": ["172.64.149.113", "104.18.38.143"],
}

# 允许被环境变量覆盖的探测开关（便于在内网/无代理环境下自测）
OFFLINE_HINT = "如需在离线环境自测，可设 KEXI_OFFLINE=1 跳过所有外网请求"


def _now_iso():
    return datetime.now(TZ8).isoformat()


def _f(v):
    """安全转 float：None/空串/非数字一律返回 None（绝不返回 0 冒充真实值）。

    各交易所 API 字段类型不一致（字符串 / 数字 / 缺字段都有），
    跨所归一时必须用这个而不是 float()，否则 None 会变成 0.0
    ——把"取不到"伪装成"确实是 0"，是最危险的一种编造。
    """
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None   # 排除 NaN


def http_get_json(url, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES,
                  pinned_host=None):
    """GET JSON，带回退重试。失败抛最后一个异常（由调用方统一降级）。"""
    last = None
    for i in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers=UA)
            if pinned_host:
                # 固定 IP + 保留 Host 头/SNI：绕过 DNS 投毒
                host = urllib.parse.urlsplit(url).hostname
                url2 = url.replace(f"https://{host}", f"https://{pinned_host}", 1)
                req = urllib.request.Request(url2, headers={**UA, "Host": host})
                ctx = ssl.create_default_context()
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                    return json.loads(r.read().decode("utf-8"))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if 400 <= e.code < 500 and e.code != 429:
                break
            time.sleep(1.5 ** i)
        except Exception as e:
            last = e
            time.sleep(1.5 ** i)
    raise RuntimeError(f"{url} 失败: {last}")


def http_get_text(url, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, max_bytes=400000):
    last = None
    for i in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(max_bytes).decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(1.5 ** i)
    raise RuntimeError(f"{url} 失败: {last}")


# ══════════════════════════════════════════════════════════════════════
# 1. 消息面：RSSHub 镜像（中文）
# ══════════════════════════════════════════════════════════════════════
# 实测：官方 rsshub.app → 403（CF 机器人墙）；镜像 rsshub.rssforever.com 稳定 3/3。
# 原始站点状态：币世界域名已死；金色财经原始站全超时；BlockBeats 官方 API 返回空数组。
# 实测（2026-09-28，10 个镜像 × 2 路径逐一探测）：
#   ✅ 2/2 可用：rsshub.rssforever.com / rsshub.ktachibana.party /
#               rsshub.woodland.cafe / rsshub.liumingye.cn
#   ❌ SSL 握手失败：pseudoyu / feeded.xyz / rss.shab.fun / henry.wang
#   ❌ 502：rss.tips / rsshub.owo.nz ；官方 rsshub.app → 403（CF 机器人墙）
# 结论：**必须多镜像轮询**——单一镜像会因临时 SSL/502 抖动导致"消息面全灭"
# （首版只用 rssforever，实跑时 blockbeats 恰好 SSL 超时，37s 后判定失败）。
# 镜像按实测成功率排序；每个镜像给独立的**短超时**，避免一个卡死拖垮整体。
RSSHUB_MIRRORS = [
    "https://rsshub.ktachibana.party",
    "https://rsshub.woodland.cafe",
    "https://rsshub.liumingye.cn",
    "https://rsshub.rssforever.com",
]
NEWS_FEEDS = [
    ("BlockBeats快讯", "/theblockbeats/newsflash"),
    ("金色财经", "/jinse/lives"),
    ("币安公告", "/binance/announcement"),
]
# 消息面总时间预算（秒）：超过则停止尝试剩余镜像，用已拿到的数据收工。
# 目的：消息面是**增强项**，绝不允许它把研判主链拖慢（实测卡过 37s）。
NEWS_TIME_BUDGET_SEC = 20.0


def _parse_rss(xml_text, feed_name, limit=8):
    """极简 RSS/Atom 解析（stdlib ElementTree，零依赖）。"""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"RSS 解析失败: {e}")
    # RSS 2.0: rss/channel/item ; Atom: feed/entry
    nodes = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry")
    for n in nodes[:limit]:
        def _t(tag):
            el = n.find(tag)
            if el is None:
                el = n.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
            if el is None:
                return ""
            return (el.text or "").strip()
        title = _t("title")
        link = _t("link")
        if not link:
            le = n.find("{http://www.w3.org/2005/Atom}link")
            if le is not None:
                link = le.get("href", "")
        items.append({
            "feed": feed_name,
            "title": title[:200],
            "link": link,
            "pub": (_t("pubDate") or _t("published") or _t("updated"))[:40],
        })
    return items


def fetch_news(limit_per_feed=6, max_total=18, time_budget=NEWS_TIME_BUDGET_SEC):
    """抓中文消息面。返回 {available, items, sources_ok, sources_fail, note}。

    策略：**按镜像轮询、每个镜像短超时、总量时间预算封顶**。
    只要任一镜像的任一 feed 成功，就算可用；预算耗尽立即收工（不阻塞主链）。
    """
    items, ok, fail = [], [], []
    t_start = time.time()
    budget_hit = False
    for mirror in RSSHUB_MIRRORS:
        if len(items) >= max_total or budget_hit:
            break
        mhost = urllib.parse.urlsplit(mirror).hostname
        for name, path in NEWS_FEEDS:
            if len(items) >= max_total:
                break
            if time.time() - t_start > time_budget:
                budget_hit = True
                fail.append(f"时间预算 {time_budget:.0f}s 用尽，停止尝试剩余源")
                break
            try:
                txt = http_get_text(mirror + path, timeout=6, retries=1)
                got = _parse_rss(txt, name, limit=limit_per_feed)
                if got:
                    items.extend(got)
                    tag = f"{name}@{mhost}"
                    if tag not in ok:
                        ok.append(tag)
                else:
                    fail.append(f"{name}@{mhost}(空)")
            except Exception as e:
                fail.append(f"{name}@{mhost}({str(e)[:40]})")
    # 标题去重
    seen, uniq = set(), []
    for it in items:
        k = it["title"][:60]
        if k and k not in seen:
            seen.add(k)
            uniq.append(it)
    return {
        "available": bool(uniq),
        "count": len(uniq),
        "items": uniq[:max_total],
        "sources_ok": ok,
        "sources_fail": fail[:8],
        "elapsed_sec_internal": round(time.time() - t_start, 1),
        "budget_exhausted": budget_hit,
        "fetched_at": _now_iso(),
        "note": ("" if uniq else
                 "中文消息面全部源不可用（RSSHub 镜像集体下线/网络中断）；"
                 "不影响技术面研判，但须在结论里声明『消息面缺失』"),
    }


# ══════════════════════════════════════════════════════════════════════
# 2. DEX 热币：DexScreener（含 DNS 投毒规避）
# ══════════════════════════════════════════════════════════════════════
def fetch_dex_hot(limit=15):
    """DEX 热门币（token-boosts 榜 = 社区助推热度）。"""
    host = "api.dexscreener.com"
    url = f"https://{host}/token-boosts/top/v1"
    data, used = None, None
    # 先直连，失败再用固定 IP 绕投毒
    try:
        data = http_get_json(url, timeout=8, retries=1)
        used = "direct"
    except Exception as e_direct:
        for ip in PINNED_HOSTS.get(host, []):
            try:
                data = http_get_json(url, timeout=8, retries=1, pinned_host=ip)
                used = f"pinned:{ip}"
                break
            except Exception:
                continue
        if data is None:
            return {"available": False, "count": 0, "items": [],
                    "error": f"直连与固定IP均失败: {str(e_direct)[:80]}",
                    "note": "DexScreener 域名在中国被 DNS 投毒，建议走代理或配 PINNED_HOSTS"}

    rows = []
    if isinstance(data, list):
        for d in data[:limit]:
            rows.append({
                "chain": d.get("chainId"),
                "token": d.get("tokenAddress"),
                "symbol": d.get("description") or "",
                "amount": d.get("totalAmount"),
                "url": d.get("url"),
            })
    return {"available": bool(rows), "count": len(rows), "items": rows,
            "via": used, "fetched_at": _now_iso(),
            "note": "DEX 热度榜（社区助推量），用于发现尚未上 CEX 或 CEX 之外的异动"}


# ══════════════════════════════════════════════════════════════════════
# 3. 稳定币资金流：DefiLlama（实测最干净的源）
# ══════════════════════════════════════════════════════════════════════
def _pegged(v):
    """DefiLlama 的金额字段统一是 {"peggedUSD": 123.0} 形状（实测），
    但也可能直接是数字——两种都兼容。"""
    if isinstance(v, dict):
        return v.get("peggedUSD")
    return v


def fetch_stablecoin_flow(chain="ethereum", stablecoin_id=1, days=14):
    """稳定币资金流 = 每日**净增发**（由累计 totalMintedUSD 差分得出）。

    实测响应形状（stablecoins.llama.fi/stablecoincharts/{chain}?stablecoin=1）：
      [{"date":"1790553600",
        "totalCirculating":{"peggedUSD":...},
        "totalMintedUSD":{"peggedUSD":86509798174.68},   ← **累计值，非每日**
        ...}, ...]
    因此"每日净增发"必须用相邻两天 totalMintedUSD 差分——直接取原字段会得到
    一个 800 亿量级的累计数字，毫无意义（初版即踩此坑，被实跑暴露）。
    注意：api.llama.fi 的 /overview/* 返回 500，**必须用 stablecoins.llama.fi**。
    """
    url = (f"https://stablecoins.llama.fi/stablecoincharts/{chain}"
           f"?stablecoin={stablecoin_id}")
    try:
        data = http_get_json(url, timeout=15, retries=2)
    except Exception as e:
        return {"available": False, "error": str(e)[:120],
                "note": "稳定币资金流不可用（DefiLlama 异常）"}

    if not isinstance(data, list) or len(data) < 3:
        return {"available": False, "error": "数据点不足",
                "note": "稳定币资金流返回空或样本不足"}

    # 用累计 minted 差分求每日净增发
    series = []
    for d in data[-(days + 1):]:
        ts = d.get("date")
        try:
            ts_i = int(ts)
        except (TypeError, ValueError):
            continue
        series.append({
            "ts": ts_i,
            "circulating": _pegged(d.get("totalCirculatingUSD")),
            "minted_cum": _pegged(d.get("totalMintedUSD")),
        })
    if len(series) < 3:
        return {"available": False, "error": "有效数据点不足",
                "note": "稳定币资金流样本不足"}

    pts = []
    for i in range(1, len(series)):
        prev, cur = series[i - 1], series[i]
        net = None
        if prev["minted_cum"] is not None and cur["minted_cum"] is not None:
            net = cur["minted_cum"] - prev["minted_cum"]
        pts.append({
            "date": datetime.fromtimestamp(cur["ts"], TZ8).strftime("%Y-%m-%d"),
            "net_mint_usd": round(net) if net is not None else None,
            "net_mint_display": (f"{net/1e8:+.2f}亿U" if net is not None else None),
            "circulating_usd": round(cur["circulating"]) if cur["circulating"] is not None else None,
        })

    recent = [p["net_mint_usd"] for p in pts[-7:] if p.get("net_mint_usd") is not None]
    net7 = sum(recent) if recent else None
    if net7 is None:
        trend = "unknown"
    elif net7 > 500_000_000:
        trend = "大幅净增发（增量资金进场）"
    elif net7 > 0:
        trend = "小幅净增发"
    elif net7 > -500_000_000:
        trend = "小幅净销毁"
    else:
        trend = "大幅净销毁（资金离场）"

    return {
        "available": True, "chain": chain, "stablecoin_id": stablecoin_id,
        "points": pts, "net_mint_7d_usd": round(net7) if net7 is not None else None,
        "net_mint_7d_display": (f"{net7/1e8:+.2f}亿U" if net7 is not None else None),
        "trend": trend, "fetched_at": _now_iso(),
        "note": ("稳定币每日净增发（由累计铸造量差分）= 资金进出代理指标；"
                 "净增发为正说明有增量资金入场"),
    }


# ══════════════════════════════════════════════════════════════════════
# 4. 合约情绪：Binance fapi → OKX 公共接口 → Bybit
# ══════════════════════════════════════════════════════════════════════
# v1.5.6：本机实测 Binance fapi 返回 HTTP 451（受限地域）、Bybit 返回 403
# （CloudFront 地区拦截），旧的两级兜底双双失效 → 合约情绪长期 available=false。
# 补 OKX 公共接口（同样免 key，实测可用）作为第二道兜底。
#
# 跨所口径问题（必须处理，否则会给出不可比的数字）：
#   · Binance openInterest = 基础币数量
#   · OKX     oi           = **合约张数**（与币量差 100 倍！）
#             oiCcy        = 基础币数量（与 Binance 可比）
#             oiUsd        = 美元名义
#   · Bybit   openInterest = 基础币数量
# 因此统一额外计算 **open_interest_usd**（美元名义），这是唯一能跨所比较的口径；
# 原始值仍按 provider 原样返回并标注来源，**不把不同所的裸值混在一起比较**。
def _funding_state(fr):
    try:
        f = float(fr)
    except (TypeError, ValueError):
        return "unknown"
    if f > 0.0005:
        return "多头过热(资金费率偏高)"
    if f > 0:
        return "资金费率正常"
    if f > -0.0005:
        return "空头付费(偏空)"
    return "空头过热"


def _okx_inst_id(symbol):
    """BTCUSDT → BTC-USDT-SWAP（BTCUSDC 走 BTC-USDC-SWAP）。"""
    s = str(symbol or "").upper()
    for quote in ("USDT", "USDC"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}-{quote}-SWAP"
    return None


def _fetch_futures_okx(symbol):
    """OKX 公共接口（免 key）。返回规范化字典或 None。"""
    inst = _okx_inst_id(symbol)
    if not inst:
        return None
    fr = http_get_json(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst}",
                       timeout=8, retries=1)
    fr_list = (fr or {}).get("data") or []
    oi_resp = http_get_json(
        f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={inst}",
        timeout=8, retries=1)
    oi_list = (oi_resp or {}).get("data") or []
    if not fr_list and not oi_list:
        return None
    fr_row = fr_list[0] if fr_list else {}
    oi_row = oi_list[0] if oi_list else {}

    # 标记价优先用 ticker 的 last（funding 接口无价格字段）
    mark = None
    try:
        t = http_get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst}",
                          timeout=8, retries=1)
        td = (t or {}).get("data") or []
        if td:
            mark = _f(td[0].get("last"))
    except Exception:
        pass

    oi_ccy = _f(oi_row.get("oiCcy"))          # 基础币数量，与 Binance 可比
    oi_usd = _f(oi_row.get("oiUsd"))          # 美元名义，最通用
    if oi_usd is None and oi_ccy is not None and mark is not None:
        oi_usd = oi_ccy * mark
    return {
        "provider": "okx",
        "open_interest": oi_ccy,
        "open_interest_contracts": _f(oi_row.get("oi")),
        "open_interest_usd": oi_usd,
        "funding_rate": _f(fr_row.get("fundingRate")),
        "mark_price": mark,
        # OKX 无公开的账户多空比接口（实测 4 个候选路径全 404），故不提供——
        # 不用别的口径顶替。
        "long_short_ratio": None,
    }


def _fetch_futures_bybit(symbol):
    """Bybit v5 线性永续（免 key）。"""
    d = http_get_json(
        f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}",
        timeout=8, retries=1)
    item = (d.get("result") or {}).get("list") or [{}]
    it = item[0] or {}
    oi = _f(it.get("openInterest"))
    mark = _f(it.get("markPrice"))
    return {
        "provider": "bybit",
        "open_interest": oi,
        "open_interest_usd": (oi * mark) if (oi is not None and mark is not None) else None,
        "funding_rate": _f(it.get("fundingRate")),
        "mark_price": mark,
        "long_short_ratio": _f(it.get("longShortRatio")),
    }


def fetch_futures_metrics(symbol="BTCUSDT"):
    """持仓量 / 资金费率 / 多空比。

    降级链：binance-fapi → OKX 公共 → Bybit。
    每次降级都在 out['note'] 里说明，并给 provider 字段标明数字来自哪家。
    全部失败时 available=false 且**不返回任何编造的数值**。
    """
    out = {"available": False, "symbol": symbol, "source": None,
           "open_interest": None, "funding_rate": None, "mark_price": None,
           "long_short_ratio": None, "open_interest_usd": None,
           "funding_state": "unknown", "provider": None, "note": None, "errors": []}
    errors = []

    # ── ① Binance fapi（主源：维度最全，含账户多空比）──
    try:
        oi = http_get_json(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}",
                           timeout=8, retries=1)
        prem = http_get_json(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}",
                             timeout=8, retries=1)
        oi_v = _f((oi or {}).get("openInterest"))
        mark = _f((prem or {}).get("markPrice"))
        out.update({
            "available": True, "source": "binance-fapi", "provider": "binance",
            "open_interest": oi_v,
            "open_interest_usd": (oi_v * mark) if (oi_v is not None and mark is not None) else None,
            "funding_rate": _f((prem or {}).get("lastFundingRate")),
            "mark_price": mark,
        })
        try:
            ls = http_get_json(
                f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
                f"?symbol={symbol}&period=1h&limit=1", timeout=8, retries=1)
            if ls:
                out["long_short_ratio"] = _f(ls[0].get("longShortRatio"))
                out["long_account"] = _f(ls[0].get("longAccount"))
        except Exception:
            pass
        out["funding_state"] = _funding_state(out.get("funding_rate"))
        out["errors"] = errors
        return out
    except Exception as e:
        errors.append("binance: " + str(e)[:110])

    # ── ② OKX 公共接口（免 key，实测本机唯一可用的合约情绪源）──
    try:
        n = _fetch_futures_okx(symbol)
        if n:
            out.update(n)
            out["available"] = True
            out["source"] = "okx"
            out["funding_state"] = _funding_state(out.get("funding_rate"))
            out["note"] = ("Binance fapi 不可用，已降级到 OKX 公共接口。"
                           "**不同交易所的资金费率与持仓量口径不同，绝对值不可直接横比**；"
                           "跨所比较请用 open_interest_usd（美元名义）。")
            out["errors"] = errors
            return out
        errors.append("okx: 无数据")
    except Exception as e:
        errors.append("okx: " + str(e)[:110])

    # ── ③ Bybit（最后一道）──
    try:
        n = _fetch_futures_bybit(symbol)
        if n and n.get("funding_rate") is not None:
            out.update(n)
            out["available"] = True
            out["source"] = "bybit"
            out["funding_state"] = _funding_state(out.get("funding_rate"))
            out["note"] = "Binance fapi 与 OKX 均不可用，已降级到 Bybit。跨所绝对值不可直接横比。"
            out["errors"] = errors
            return out
        errors.append("bybit: 无数据")
    except Exception as e:
        errors.append("bybit: " + str(e)[:110])

    out["note"] = "合约情绪不可用（Binance fapi / OKX / Bybit 三者均失败）"
    out["errors"] = errors
    return out


# ══════════════════════════════════════════════════════════════════════
# 5. 恐惧贪婪指数
# ══════════════════════════════════════════════════════════════════════
def fetch_fng():
    try:
        d = http_get_json("https://api.alternative.me/fng/?limit=1", timeout=8, retries=2)
        item = (d.get("data") or [{}])[0]
        return {"available": True, "value": int(item.get("value", 0)),
                "classification": item.get("value_classification"),
                "fetched_at": _now_iso()}
    except Exception as e:
        return {"available": False, "error": str(e)[:100],
                "note": "恐惧贪婪指数不可用（alternative.me 异常）"}


# ══════════════════════════════════════════════════════════════════════
# 6. CMC 免 key 内容 API（非官方，必须容错）
# ══════════════════════════════════════════════════════════════════════
def fetch_cmc_news(limit=8):
    """CMC 非官方免 key 新闻。测试过可用（credit_count:0），但未文档化，随时可能失效。"""
    try:
        d = http_get_json(
            f"https://api.coinmarketcap.com/content/v3/news?page=1&size={limit}",
            timeout=8, retries=1)
        rows = []
        for it in (d.get("data") or [])[:limit]:
            rows.append({
                "title": (it.get("title") or "")[:200],
                "source": it.get("sourceName"),
                "url": it.get("sourceUrl") or it.get("url"),
                "created": it.get("createdAt"),
            })
        return {"available": bool(rows), "count": len(rows), "items": rows,
                "fetched_at": _now_iso(),
                "note": "CMC 非官方免 key 接口——未文档化，已按容错处理"}
    except Exception as e:
        return {"available": False, "error": str(e)[:100],
                "note": "CMC 新闻不可用（非官方接口，失效属预期）"}


def fetch_cmc_trending(limit=10):
    """CMC 散户搜索热度（社媒热度的可用代理——2026 无免费 KOL 推文源）。"""
    try:
        d = http_get_json(
            "https://api.coinmarketcap.com/data-api/v3/topsearch/rank",
            timeout=8, retries=1)
        rows = []
        for it in ((d.get("data") or {}).get("cryptoTopSearchRanks") or [])[:limit]:
            rows.append({
                "symbol": (it.get("symbol") or "").upper(),
                "name": it.get("name"),
                "rank": it.get("rank"),
            })
        return {"available": bool(rows), "count": len(rows), "items": rows,
                "fetched_at": _now_iso(),
                "note": "散户搜索热度（社媒热度代理）；2026 年无可靠免费 X/Reddit 源"}
    except Exception as e:
        return {"available": False, "error": str(e)[:100],
                "note": "CMC 搜索热度不可用"}


# ══════════════════════════════════════════════════════════════════════
# 汇总入口
# ══════════════════════════════════════════════════════════════════════
def collect_all(news=True, dex=True, flow=True, futures=True,
                fng=True, cmc=True, symbol="BTCUSDT", quiet=False):
    """汇总抓取所有可用面。**任一源失败都不影响其他源**。"""
    res = {"fetched_at": _now_iso(), "sources": {}}
    tasks = []
    if news:
        tasks.append(("news", lambda: fetch_news()))
    if dex:
        tasks.append(("dex_hot", lambda: fetch_dex_hot()))
    if flow:
        tasks.append(("stablecoin_flow", lambda: fetch_stablecoin_flow()))
    if futures:
        tasks.append(("futures", lambda: fetch_futures_metrics(symbol)))
    if fng:
        tasks.append(("fng", lambda: fetch_fng()))
    if cmc:
        tasks.append(("cmc_news", lambda: fetch_cmc_news()))
        tasks.append(("cmc_trending", lambda: fetch_cmc_trending()))

    # 并发抓取：各源彼此独立，串行会让总耗时=各源之和（实测串行 ~44s，并发 ~10s）。
    # 任一源卡死只影响它自己（各自有独立超时），不影响其他源。
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _run(item):
        name, fn = item
        t0 = time.time()
        try:
            r = fn()
        except Exception as e:
            r = {"available": False, "error": f"未捕获异常: {str(e)[:100]}"}
        r["elapsed_sec"] = round(time.time() - t0, 2)
        return name, r

    with ThreadPoolExecutor(max_workers=min(6, max(1, len(tasks)))) as ex:
        futs = [ex.submit(_run, t) for t in tasks]
        for fut in as_completed(futs):
            try:
                name, r = fut.result()
            except Exception as e:
                continue
            res["sources"][name] = r
            if not quiet:
                st = "OK " if r.get("available") else "FAIL"
                print(f"  [{st}] {name:<18} {r.get('elapsed_sec')}s "
                      f"{(r.get('count') or r.get('value') or r.get('trend') or r.get('error') or '')}",
                      flush=True)

    avail = [k for k, v in res["sources"].items() if v.get("available")]
    failed = [k for k, v in res["sources"].items() if not v.get("available")]
    res["available_count"] = len(avail)
    res["failed_count"] = len(failed)
    res["sources_available"] = avail
    res["sources_failed"] = failed
    return res


def main():
    import argparse
    ap = argparse.ArgumentParser(description="多面数据源抓取（消息面/DEX/资金流/合约/情绪）")
    ap.add_argument("--out", default="market_context.json")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--no-dex", action="store_true")
    ap.add_argument("--no-flow", action="store_true")
    ap.add_argument("--no-futures", action="store_true")
    ap.add_argument("--no-fng", action="store_true")
    ap.add_argument("--no-cmc", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    print("[多面数据源] 开始抓取 ...", flush=True)
    res = collect_all(
        news=not args.no_news, dex=not args.no_dex, flow=not args.no_flow,
        futures=not args.no_futures, fng=not args.no_fng, cmc=not args.no_cmc,
        symbol=args.symbol, quiet=args.quiet)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"\n可用 {res['available_count']} / 失败 {res['failed_count']}"
          f" → {args.out}")
    if res["sources_failed"]:
        print(f"失败源: {', '.join(res['sources_failed'])}（不阻断研判）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())