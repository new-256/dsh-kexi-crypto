#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cg_registry.py - CoinGecko 币表解析（v1.9.22 新增，从 fetch_klines.py 拆出）

为什么独立成模块：
  ① 用户规则 26：单文件 ≤600 行。fetch_klines.py 加上本逻辑后到 658 行，
     必须拆分——不是"接近上限还行"，而是超了就得拆。
  ② 职责单一：本模块只回答"这个交易对对应哪个 CoinGecko coin id"，
     与取数/落盘无关，独立后可以单独测试。

背景（D5 缺陷）：早先 CG_IDS 是一张**硬编码 15 项的白名单**且是唯一来源，
任何表外币即使符号正确，CoinGecko 回退路径也必然失败
（"no CoinGecko id mapping for XXX; add it to CG_IDS"）。
这违反"研发优先泛用方案、禁止硬编码"的原则——不能因为某个币不在
手写清单里就判它取不到数。

现在的解析顺序：
  ① 静态常用表命中 → 直接用（省一次网络请求）
  ② 未命中 → 查 CoinGecko /coins/list 全量币表（约 2 万项），按 symbol 匹配
  ③ 结果缓存 7 天到 <cwd>/kexi_out/.cache/cg_coins_list.json
  ④ 网络不可用且未命中 → **明确报错**，绝不"猜一个"
  ⑤ 同名多候选 → 按市值取最高者；拿不到市值就列出候选并**拒绝猜测**
"""

import json
import os
import time

# 常用币速查表（**不是白名单**，只是省一次网络请求的缓存）。
# Binance symbol -> CoinGecko coin id
CG_IDS = {
    "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "BNBUSDT": "binancecoin",
    "SOLUSDT": "solana", "XRPUSDT": "ripple", "DOGEUSDT": "dogecoin",
    "ADAUSDT": "cardano", "AVAXUSDT": "avalanche-2", "LINKUSDT": "chainlink",
    "DOTUSDT": "polkadot", "LTCUSDT": "litecoin", "TRXUSDT": "tron",
    "PEPEUSDT": "pepe", "SUIUSDT": "sui", "TONUSDT": "the-open-network",
}

CG_LIST_CACHE_TTL_SEC = 7 * 86400
_CG_LIST_MEM = None

# 网络取数的注入点：默认用 urllib 实现，测试可替换本模块的 _http_get 以避免联网
_http_get = None


def set_http_get(fn):
    """注入 HTTP 实现（测试用）。fn(url, timeout=...) -> 已解析的 JSON。"""
    global _http_get
    _http_get = fn


def _get(url, timeout=20):
    """取 JSON。优先用注入实现，否则退到 urllib。"""
    if _http_get is not None:
        return _http_get(url, timeout=timeout)
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-trend-analyst/1.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _cg_cache_path():
    """缓存路径：<cwd>/kexi_out/.cache/cg_coins_list.json。

    cwd 在插件里恒被设成会话工作区（v1.5.2 修过），所以缓存跟着工作区走，
    不会写进插件安装目录。
    """
    base = os.path.join(os.getcwd(), "kexi_out", ".cache")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        return None
    return os.path.join(base, "cg_coins_list.json")


def _read_cache(p):
    if not p or not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            blob = json.load(f)
        return blob
    except Exception:
        return None


def _load_cg_list():
    """取 CoinGecko 全量币表（带内存 + 磁盘缓存）。失败返回 None。"""
    global _CG_LIST_MEM
    if _CG_LIST_MEM is not None:
        return _CG_LIST_MEM

    p = _cg_cache_path()
    blob = _read_cache(p)
    if blob and time.time() - float(blob.get("_cached_at") or 0) < CG_LIST_CACHE_TTL_SEC:
        _CG_LIST_MEM = blob.get("coins") or []
        return _CG_LIST_MEM

    try:
        raw = _get("https://api.coingecko.com/api/v3/coins/list", timeout=20)
    except Exception:
        # 网络不可用：有旧缓存就用旧缓存（仍解析不出时如实报错，不猜）
        if blob:
            _CG_LIST_MEM = blob.get("coins") or []
            return _CG_LIST_MEM
        return None

    if not isinstance(raw, list):
        return None
    coins = [{"id": c.get("id"), "symbol": (c.get("symbol") or "").lower(),
              "name": c.get("name")} for c in raw if isinstance(c, dict) and c.get("id")]
    _CG_LIST_MEM = coins
    if p:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"_cached_at": time.time(), "count": len(coins), "coins": coins},
                          f, ensure_ascii=False)
        except Exception:
            pass
    return coins


def _strip_quote(sym):
    """BTCUSDT -> BTC。已带最长计价后缀优先。"""
    base = sym
    for q in ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD"):
        if base.endswith(q) and len(base) > len(q):
            return base[:-len(q)]
    return base


def resolve_coingecko_id(symbol):
    """把 BTCUSDT 解析成 CoinGecko coin id。

    返回 (coin_id, note)。解析不出来时 coin_id 为 None 且 note 说明原因
    ——调用方据此给出可执行的错误，而不是一个含糊的 "no mapping"。

    匹配策略：
      · 静态表命中 → 直接用
      · 否则按 symbol（去掉计价后缀后的基币）在全量币表里找
      · 多个候选取**市值最高者**（同名不同链的代币很常见），并把歧义写进 note
      · 网络不可用且无缓存 → 返回 None，note 说明"币表不可达"
    """
    sym = (symbol or "").upper()
    if sym in CG_IDS:
        return CG_IDS[sym], None

    base = _strip_quote(sym)
    if not base:
        return None, "无法从 %s 解析出基币" % symbol

    coins = _load_cg_list()
    if coins is None:
        return None, ("%s 不在常用币表中，且 CoinGecko 币表不可达——"
                      "无法解析 coin id；请检查网络或改用 --source binance/okx" % symbol)
    if not coins:
        return None, "CoinGecko 币表为空，无法解析"

    target = base.lower()
    hits = [c for c in coins if c.get("symbol") == target]
    if not hits:
        return None, ("CoinGecko 全量币表（%d 项）中找不到 symbol=%s 的币——"
                      "%s 可能不存在或已下架" % (len(coins), target, symbol))
    if len(hits) == 1:
        return hits[0]["id"], None

    # 多候选：按市值排序取最高者。拿不到市值就**不猜**，把候选列出来。
    try:
        ids = ",".join(str(h["id"]) for h in hits[:40])
        mk = _get("https://api.coingecko.com/api/v3/coins/markets"
                  "?vs_currency=usd&ids=%s" % ids, timeout=20)
        ranked = sorted([m for m in mk if isinstance(m, dict)],
                        key=lambda m: float(m.get("market_cap") or 0), reverse=True)
        if ranked:
            pick = ranked[0]
            others = "、".join("%s(%s)" % (m.get("id"), m.get("symbol"))
                              for m in ranked[1:4] if m.get("id"))
            note = ("%s 在 CoinGecko 有 %d 个同名币，已按市值取 %s"
                    % (symbol, len(hits), pick.get("id"))
                    + ("，其余候选：%s" % others if others else ""))
            return pick.get("id"), note
    except Exception:
        pass
    cands = "、".join(h["id"] for h in hits[:8])
    return None, ("%s 在 CoinGecko 有 %d 个同名币且无法按市值排序，"
                  "拒绝猜测。候选：%s——请用完整币名或改用 --source binance/okx"
                  % (symbol, len(hits), cands))
