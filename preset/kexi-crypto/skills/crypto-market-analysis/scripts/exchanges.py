#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exchanges.py - Unified multi-exchange public API adapters (no keys required).

Supported CEX: binance, okx, gate, mexc, bitget, coinbase
DEX: dexscreener (search-based pair info)

Unified kline format returned: [[ts_ms, open, high, low, close, base_volume], ...] oldest first.
All functions raise on failure; callers handle graceful degradation.
"""

import json
import time
import urllib.request
import urllib.parse

import kline_utils as ku

UA = {"User-Agent": "crypto-trend-analyst/1.0"}

BASES = {
    "binance": "https://api.binance.com",
    "okx": "https://www.okx.com",
    "gate": "https://api.gateio.ws",
    "mexc": "https://api.mexc.com",
    "bitget": "https://api.bitget.com",
    "coinbase": "https://api.exchange.coinbase.com",
    "dexscreener": "https://api.dexscreener.com",
}

QUOTE = {"binance": "USDT", "okx": "USDT", "gate": "USDT", "mexc": "USDT",
         "bitget": "USDT", "coinbase": "USD"}


def _get(url, timeout=12, retries=2):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5 ** i)
    raise RuntimeError(f"{url.split('?')[0]}: {last}")


def _pair(exchange, base):
    if exchange == "okx":
        return f"{base}-{QUOTE[exchange]}"
    if exchange == "gate":
        return f"{base}_{QUOTE[exchange]}"
    if exchange == "coinbase":
        return f"{base}-{QUOTE[exchange]}"
    return f"{base}{QUOTE[exchange]}"


def _get_klines_raw(exchange, base, limit=120):
    """Daily klines, unified [[ts_ms,o,h,l,c,vol],...] oldest first. 未做收盘过滤。"""
    p = _pair(exchange, base)
    q = urllib.parse.quote(p)
    if exchange == "binance":
        raw = _get(f"{BASES[exchange]}/api/v3/klines?symbol={q}&interval=1d&limit={limit}")
        return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
                for k in raw]
    if exchange == "okx":
        raw = _get(f"{BASES[exchange]}/api/v5/market/candles?instId={q}&bar=1D&limit={min(limit,300)}")
        data = raw.get("data", [])
        return sorted([[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
                       for k in data])
    if exchange == "gate":
        raw = _get(f"{BASES[exchange]}/api/v4/spot/candlesticks?currency_pair={q}&interval=1d&limit={limit}")
        # gate v4: [t_sec, volume_quote, close, high, low, open]
        return sorted([[int(float(k[0])) * 1000, float(k[5]), float(k[3]), float(k[4]),
                        float(k[2]), float(k[1])] for k in raw])
    if exchange == "mexc":
        raw = _get(f"{BASES[exchange]}/api/v3/klines?symbol={q}&interval=1d&limit={limit}")
        return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
                for k in raw]
    if exchange == "bitget":
        raw = _get(f"{BASES[exchange]}/api/v2/spot/market/candles?symbol={q}&granularity=1day&limit={min(limit,200)}")
        data = raw.get("data", [])
        return sorted([[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
                       for k in data])
    if exchange == "coinbase":
        raw = _get(f"{BASES[exchange]}/products/{q}/candles?granularity=86400")
        # [time_sec, low, high, open, close, volume], newest first, max 300
        rows = sorted(raw)[:limit] if len(raw) <= limit else sorted(raw)[-limit:]
        return [[int(k[0]) * 1000, float(k[3]), float(k[2]), float(k[1]), float(k[4]), float(k[5])]
                for k in rows]
    raise ValueError(f"unknown exchange: {exchange}")


def get_klines(exchange, base, limit=120):
    """统一口径的日线取数：[[ts_ms,o,h,l,c,vol],...] oldest first，仅含**已收盘** bar。

    各所最后一根日线在盘中尚未走完（close 是瞬时价、volume 是部分量），
    直接参与指标计算会系统性失真，故统一剔除后再返回。
    详见 kline_utils.drop_open_bar 与专家包审查报告 P0-2。
    """
    kl = _get_klines_raw(exchange, base, limit + 1)
    closed, _ = ku.drop_open_bar(kl, ku.D1)
    return closed


def get_ticker(exchange, base):
    """Return {price, change_24h_pct, volume_24h_quote} best-effort."""
    p = _pair(exchange, base)
    q = urllib.parse.quote(p)
    if exchange == "binance":
        d = _get(f"{BASES[exchange]}/api/v3/ticker/24hr?symbol={q}")
        return {"price": float(d["lastPrice"]), "change_24h_pct": float(d["priceChangePercent"]),
                "volume_24h_quote": float(d["quoteVolume"])}
    if exchange == "okx":
        d = _get(f"{BASES[exchange]}/api/v5/market/ticker?instId={q}")["data"][0]
        last, o = float(d["last"]), float(d["open24h"])
        return {"price": last, "change_24h_pct": (last / o - 1) * 100 if o else None,
                "volume_24h_quote": float(d.get("volCcy24h") or 0)}
    if exchange == "gate":
        d = _get(f"{BASES[exchange]}/api/v4/spot/tickers?currency_pair={q}")
        d = d[0] if isinstance(d, list) else d
        return {"price": float(d["last"]), "change_24h_pct": float(d.get("change_percentage") or 0),
                "volume_24h_quote": float(d.get("quote_volume") or 0)}
    if exchange == "mexc":
        d = _get(f"{BASES[exchange]}/api/v3/ticker/24hr?symbol={q}")
        d = d[0] if isinstance(d, list) else d
        return {"price": float(d["lastPrice"]), "change_24h_pct": float(d["priceChangePercent"]),
                "volume_24h_quote": float(d.get("quoteVolume") or 0)}
    if exchange == "bitget":
        d = _get(f"{BASES[exchange]}/api/v2/spot/market/tickers?symbol={q}")["data"][0]
        o = float(d.get("open24h") or 0)
        last = float(d["lastPr"])
        return {"price": last, "change_24h_pct": (last / o - 1) * 100 if o else None,
                "volume_24h_quote": float(d.get("quoteVolume") or 0)}
    if exchange == "coinbase":
        s = _get(f"{BASES[exchange]}/products/{q}/stats")
        last, o = float(s["last"]), float(s["open"])
        return {"price": last, "change_24h_pct": (last / o - 1) * 100 if o else None,
                "volume_24h_quote": float(s.get("volume") or 0) * last}
    raise ValueError(f"unknown exchange: {exchange}")


def dex_search(base):
    """Dexscreener: main DEX pairs for a symbol. Known chains + min liquidity/volume filter."""
    known_chains = {"ethereum", "solana", "bsc", "base", "arbitrum", "polygon",
                    "ton", "avalanche", "optimism", "sui", "hyperliquid"}
    d = _get(f"{BASES['dexscreener']}/latest/dex/search?q={urllib.parse.quote(base)}")
    pairs = []
    for p in (d.get("pairs") or []):
        if p.get("baseToken", {}).get("symbol", "").upper() != base.upper():
            continue
        liq = (p.get("liquidity") or {}).get("usd") or 0
        vol = (p.get("volume") or {}).get("h24") or 0
        if p.get("chainId") not in known_chains or liq < 10_000 or vol < 1_000:
            continue
        pairs.append({
            "dex": p.get("dexId"), "chain": p.get("chainId"),
            "pair": p.get("pairAddress"),
            "price_usd": p.get("priceUsd"),
            "liquidity_usd": liq,
            "volume_24h": vol,
            "created_at_ms": p.get("pairCreatedAt"),
        })
    pairs.sort(key=lambda x: -(x["liquidity_usd"] or 0))
    return pairs


CEX_LIST = ["binance", "okx", "gate", "mexc", "bitget", "coinbase"]

# 各所单次请求最大日线根数（超出需分页，当前窗口内取最早一根作为上市日近似）
MAX_LIMITS = {"binance": 1000, "okx": 300, "gate": 1000, "mexc": 1000,
              "bitget": 200, "coinbase": 300}
