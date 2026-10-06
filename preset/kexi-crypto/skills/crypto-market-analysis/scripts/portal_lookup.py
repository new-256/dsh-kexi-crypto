#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portal_lookup.py - 虚拟币门户咨询快查（秒级聚合代币背景与风险核验）

【重要声明与定位】：
    快查只解决"这是什么"，不构成投资建议；同名仿盘/低市值代币必须提示核验。
    本工具用于在进入盘面走势分析之前，快速获取代币基本面骨架：
    · 项目名称、英文简介、官方主页、代币合约与主网平台
    · 市值、市值排名、现价、流通量、总供应量与创世/上市信息
    · Binance 现货上市场状与流动性通道核查
    · 自动化同名仿盘预警、低市值流动性陷阱预警、合约核对风险提示

数据源与降级机制：
    1) CoinGecko 搜索接口（search?query=...）：获取最佳匹配并检出所有同 ticker 候选列表（同名仿盘线索）；
    2) CoinGecko 代币详情（/coins/{id}）：获取官方链接、合约、分类与市场核心指标；
    3) Binance 交易所信息（/api/v3/exchangeInfo）：获取现货交易对状态（多备选节点轮询，容错 451 与网络限制）；
    4) 429 限流保护：自动解析 Retry-After 标头并执行指数退避重试，退避耗尽后如实降级并填充 null，绝不编造虚假数据。

依赖环境：
    零第三方依赖，纯 Python 3 标准库（urllib, json, argparse, os, sys, time, ssl, datetime）。
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

# 东八区时间
TZ8 = timezone(timedelta(hours=8))

# HTTP 请求标头
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# 常见已知计价后缀（用于剥离出 base symbol）
KNOWN_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH", "BNB", "EUR", "TRY", "TUSD")

# Binance 备选公用 API 域名（多节点轮询，应对特定网络环境下的 451/连接阻断）
BINANCE_HOSTS = [
    "data-api.binance.vision",
    "api.binance.com",
    "api1.binance.com",
    "api2.binance.com",
    "api3.binance.com",
]


def create_ssl_context():
    """创建容错 SSL 上下文，避免部分 Windows 平台根证书不全导致的握手失败。"""
    try:
        ctx = ssl.create_default_context()
        return ctx
    except Exception:
        ctx = ssl._create_unverified_context()
        return ctx


SSL_CTX = create_ssl_context()


def strip_quote_suffix(raw_symbol):
    """剥离交易对的计价后缀，提取 base 代币代码。例如 PYTHUSDT -> PYTH。"""
    s = (raw_symbol or "").strip().upper()
    for q in KNOWN_QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return s[:-len(q)]
    return s


def http_get_json(url, timeout=12, retries=2):
    """带有 429 自动退避与网络容错的 HTTP GET JSON 请求。
    
    返回 (data, warnings_list, status_code)。
    """
    warns = []
    attempt = 0
    max_attempts = max(1, retries + 1)

    while attempt < max_attempts:
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
                status = resp.status
                raw_bytes = resp.read()
                try:
                    data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
                    return data, warns, status
                except json.JSONDecodeError as jde:
                    warns.append(f"JSON 解析失败 ({url}): {str(jde)[:80]}")
                    return None, warns, status
        except urllib.error.HTTPError as he:
            status = he.code
            if status == 429:
                # 限流处理：读取 Retry-After
                retry_after_hdr = he.headers.get("Retry-After")
                if retry_after_hdr and retry_after_hdr.isdigit():
                    wait_sec = float(retry_after_hdr)
                else:
                    wait_sec = 0.8 * (2 ** attempt)
                attempt += 1
                if attempt < max_attempts:
                    time.sleep(wait_sec)
                    continue
                else:
                    warns.append(f"CoinGecko 免费档限流(429)，已退避 {attempt} 次后降级")
                    return None, warns, 429
            elif 400 <= status < 500:
                # 4xx（非 429）参数或未找到，无需重试
                warns.append(f"HTTP {status} 客户端错误 ({url})")
                return None, warns, status
            else:
                # 5xx 服务器错误，退避重试
                attempt += 1
                if attempt < max_attempts:
                    time.sleep(0.8 * (2 ** attempt))
                    continue
                warns.append(f"HTTP {status} 服务端错误 ({url})")
                return None, warns, status
        except Exception as e:
            attempt += 1
            if attempt < max_attempts:
                time.sleep(0.8 * (2 ** attempt))
                continue
            warns.append(f"网络请求异常 ({url}): {str(e)[:100]}")
            return None, warns, None

    return None, warns, None


def coingecko_search(query, timeout=12, retries=2):
    """通过 CoinGecko search 接口模糊搜索代币。返回 (coins_list, warns, url)。"""
    encoded = urllib.parse.quote(query.strip())
    url = f"https://api.coingecko.com/api/v3/search?query={encoded}"
    data, warns, _ = http_get_json(url, timeout=timeout, retries=retries)
    coins = (data or {}).get("coins", []) if isinstance(data, dict) else []
    return coins, warns, url


def select_best_coin(coins, target_symbol):
    """在 CoinGecko 候选列表中选出最佳匹配项，并提取所有同 ticker 候选。
    
    规则：
    1. 优先 symbol 全等（忽略大小写）且 market_cap_rank 最靠前者（有 rank 优先，rank 越小越好）；
    2. 否则取 rank 最靠前者；
    3. 若都无 rank，取第一项。
    同时，将所有同 symbol（全等）的候选记入 same_symbol_candidates。
    """
    if not coins:
        return None, []

    target_sym_upper = target_symbol.strip().upper()
    exact_matches = []
    same_symbol_candidates = []

    for c in coins:
        c_sym = str(c.get("symbol") or "").strip().upper()
        if c_sym == target_sym_upper:
            exact_matches.append(c)
            same_symbol_candidates.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "symbol": c.get("symbol"),
                "market_cap_rank": c.get("market_cap_rank"),
            })

    def rank_key(item):
        r = item.get("market_cap_rank")
        # rank 为空排在后面（9999999）
        return (0 if (r is not None and isinstance(r, (int, float)) and r > 0) else 1,
                r if (r is not None and isinstance(r, (int, float)) and r > 0) else 9999999)

    if exact_matches:
        exact_matches.sort(key=rank_key)
        best = exact_matches[0]
    else:
        sorted_coins = sorted(coins, key=rank_key)
        best = sorted_coins[0]
        # 如果不是 exact match，仍然尝试看有没有跟 best.symbol 相同的
        best_sym = str(best.get("symbol") or "").strip().upper()
        for c in coins:
            if str(c.get("symbol") or "").strip().upper() == best_sym:
                same_symbol_candidates.append({
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "symbol": c.get("symbol"),
                    "market_cap_rank": c.get("market_cap_rank"),
                })

    return best, same_symbol_candidates


def coingecko_fetch_detail(coin_id, timeout=12, retries=2):
    """获取 CoinGecko 代币完整详情。"""
    url = (f"https://api.coingecko.com/api/v3/coins/{coin_id}"
           f"?localization=false&tickers=false&market_data=true"
           f"&community_data=false&developer_data=false")
    data, warns, _ = http_get_json(url, timeout=timeout, retries=retries)
    return data, warns, url


def binance_fetch_exchange_info(binance_symbol, timeout=12, retries=2):
    """轮询 Binance 备选域名查询交易对状态。返回 (exchange_dict, warns, used_url)。"""
    warns = []
    used_url = None

    for host in BINANCE_HOSTS:
        url = f"https://{host}/api/v3/exchangeInfo?symbol={binance_symbol.upper()}"
        data, host_warns, status = http_get_json(url, timeout=timeout, retries=retries)
        if host_warns:
            warns.extend(host_warns)
        if data and isinstance(data, dict):
            syms = data.get("symbols") or []
            if syms:
                target = syms[0]
                used_url = url
                return {
                    "status": target.get("status"),
                    "base_asset": target.get("baseAsset"),
                    "quote_asset": target.get("quoteAsset"),
                    "spot_trading_allowed": target.get("isSpotTradingAllowed"),
                }, warns, used_url
            else:
                # 查到了但 symbols 为空
                return None, warns, url
        elif status == 400:
            # 400 表明该 symbol 不在 Binance 现货列表中，无须轮询后续 host
            return None, warns, url

    return None, warns, used_url


def evaluate_risks(item):
    """生成中文风险提示数组（至少覆盖指定的全部风控规则，命中才加入）。"""
    risks = []
    homepages = item.get("homepage") or []
    same_cands = item.get("same_symbol_candidates") or []
    mkt = item.get("market") or {}
    rank = mkt.get("market_cap_rank")
    mcap = mkt.get("market_cap_usd")
    categories = item.get("categories") or []
    exch = item.get("exchange")

    # 1. 官网为空
    if not homepages:
        risks.append("CoinGecko 未提供官网链接 → 谨防同名仿盘，务必自行核验官方渠道")
    else:
        # 2. 官网存在但无 https 或可能为第三方页面
        has_https = any(isinstance(u, str) and u.lower().startswith("https://") for u in homepages)
        if not has_https:
            risks.append("官网链接需人工二次核验（可能为第三方聚合页）")

    # 3. 同名 ticker 候选 > 1
    if len(same_cands) > 1:
        cand_ids = [c.get("id") or str(c) for c in same_cands[:3]]
        risks.append(f"存在 {len(same_cands)} 个同 ticker 代币（如 {', '.join(cand_ids)}），下单前务必核对合约地址/主链，防同名仿盘")

    # 4. 低市值 / 未上榜
    if rank is None or rank > 2000:
        risks.append("低市值/未上榜代币，警惕拉盘出货与流动性陷阱")

    # 5. 分类为空
    if not categories:
        risks.append("类别未知，CoinGecko 未收录标签")

    # 6. Binance 现货未上市或未开放交易
    if not exch or not exch.get("spot_trading_allowed") or exch.get("status") != "TRADING":
        risks.append("未在 Binance 现货上市，流动性与出入金通道存疑")

    # 7. 流通市值缺失
    if mcap is None:
        risks.append("取不到流通市值，谨慎看待其规模")

    return risks


def lookup_single_symbol(raw_query, timeout=12, retries=2, no_exchange=False):
    """针对单标的执行完整的搜索、详情检索、交易所校验与风险评估。"""
    raw_query = raw_query.strip()
    base_symbol = strip_quote_suffix(raw_query)
    binance_symbol = f"{base_symbol}USDT"

    item_warnings = []
    sources = []

    # 1. 检索 CoinGecko 候选
    coins, search_warns, search_url = coingecko_search(base_symbol, timeout=timeout, retries=retries)
    sources.append(search_url)
    item_warnings.extend(search_warns)

    best_coin, same_cands = select_best_coin(coins, base_symbol)

    if not best_coin:
        item_warnings.append(f"CoinGecko 未检索到代币: {base_symbol}")
        item = {
            "query": raw_query,
            "symbol": base_symbol,
            "binance_symbol": binance_symbol,
            "name": None,
            "coingecko_id": None,
            "categories": [],
            "platform": {"native_chain": None, "contracts": {}},
            "homepage": [],
            "market": {
                "current_price_usd": None,
                "market_cap_usd": None,
                "market_cap_rank": None,
                "circulating_supply": None,
                "total_supply": None,
                "max_supply": None,
                "last_updated": None,
            },
            "listing": {
                "genesis_date": None,
                "listing_price": None,
                "listing_currency": None,
                "listing_source": None,
                "listing_source_url": None,
            },
            "exchange": None,
            "risks": ["未在 CoinGecko 找到任何匹配代币，存在高度同名仿盘或无效代码风险"],
            "warnings": item_warnings,
            "sources": sources,
            "same_symbol_candidates": [],
        }
        return item

    cg_id = best_coin.get("id")

    # 2. 取代币详细信息
    detail_data, detail_warns, detail_url = coingecko_fetch_detail(cg_id, timeout=timeout, retries=retries)
    sources.append(detail_url)
    item_warnings.extend(detail_warns)

    detail = detail_data if isinstance(detail_data, dict) else {}

    # 提取官网链接
    raw_homepages = (detail.get("links") or {}).get("homepage") or []
    cleaned_homepages = [h.strip() for h in raw_homepages if isinstance(h, str) and h.strip()]

    # 提取市场指标
    mdata = detail.get("market_data") or {}
    cur_price = (mdata.get("current_price") or {}).get("usd")
    mcap_usd = (mdata.get("market_cap") or {}).get("usd")
    mcap_rank = detail.get("market_cap_rank") or mdata.get("market_cap_rank")
    circ_supply = mdata.get("circulating_supply")
    tot_supply = mdata.get("total_supply")
    max_supply = mdata.get("max_supply")
    last_upd = mdata.get("last_updated")

    market_dict = {
        "current_price_usd": cur_price,
        "market_cap_usd": mcap_usd,
        "market_cap_rank": mcap_rank,
        "circulating_supply": circ_supply,
        "total_supply": tot_supply,
        "max_supply": max_supply,
        "last_updated": last_upd,
    }

    # 平台与合约
    native_chain = detail.get("asset_platform_id")
    contracts = detail.get("platforms") or {}
    platform_dict = {
        "native_chain": native_chain,
        "contracts": contracts,
    }

    # 上市与创世信息
    listing_dict = {
        "genesis_date": detail.get("genesis_date"),
        "listing_price": detail.get("listing_price"),
        "listing_currency": detail.get("listing_currency"),
        "listing_source": detail.get("listing_source"),
        "listing_source_url": detail.get("listing_source_url"),
    }

    # 3. Binance 现货上市场状核验
    exchange_dict = None
    if not no_exchange:
        exch_res, exch_warns, exch_url = binance_fetch_exchange_info(binance_symbol, timeout=timeout, retries=retries)
        if exch_url:
            sources.append(exch_url)
        item_warnings.extend(exch_warns)
        exchange_dict = exch_res

    item = {
        "query": raw_query,
        "symbol": base_symbol,
        "binance_symbol": binance_symbol,
        "name": detail.get("name") or best_coin.get("name"),
        "coingecko_id": cg_id,
        "categories": detail.get("categories") or [],
        "platform": platform_dict,
        "homepage": cleaned_homepages,
        "market": market_dict,
        "listing": listing_dict,
        "exchange": exchange_dict,
        "risks": [],
        "warnings": item_warnings,
        "sources": sources,
        "same_symbol_candidates": same_cands,
    }

    # 4. 生成风险评估
    item["risks"] = evaluate_risks(item)
    return item


def render_digest(items, warnings, elapsed_ms):
    """渲染人类可读的控制台摘要，风格与 fast_analysis.py 对齐。"""
    lines = []
    lines.append(f"[门户快查] {len(items)} 个代币 · 耗时 {elapsed_ms} ms")
    lines.append("用途：仅解决「这是什么」基本面骨架，不构成投资建议；同名仿盘/低市值代币务必核验合约！")
    lines.append("=" * 72)

    for it in items:
        sym = it.get("symbol") or "UNKNOWN"
        name = it.get("name") or "—"
        cg_id = it.get("coingecko_id") or "—"
        mkt = it.get("market") or {}
        price = mkt.get("current_price_usd")
        price_str = f"${price:.6g}" if (price is not None) else "—"
        rank = mkt.get("market_cap_rank")
        rank_str = f"#{rank}" if rank else "未上榜"
        mcap = mkt.get("market_cap_usd")
        if mcap is not None:
            mcap_str = f"${mcap / 1e8:.2f} 亿" if mcap >= 1e8 else f"${mcap / 1e4:.0f} 万"
        else:
            mcap_str = "—"

        lines.append("")
        lines.append(f"■ {sym} ({name}) ｜ CG_ID: {cg_id} ｜ 市值排位: {rank_str}")
        lines.append(f"  现价: {price_str} ｜ 流通市值: {mcap_str}")

        # 供应量
        circ = mkt.get("circulating_supply")
        tot = mkt.get("total_supply")
        circ_str = f"{circ:,.0f}" if circ is not None else "—"
        tot_str = f"{tot:,.0f}" if tot is not None else "—"
        lines.append(f"  流通量: {circ_str} ｜ 总供应: {tot_str}")

        # 分类与平台
        cats = it.get("categories") or []
        cat_str = "、".join(cats[:3]) if cats else "未收录分类"
        plat = it.get("platform") or {}
        native = plat.get("native_chain") or "主网原生/未指定"
        lines.append(f"  所属分类: {cat_str} ｜ 主链: {native}")

        # 交易所现货状态
        exch = it.get("exchange")
        if exch:
            spot_ok = "是" if exch.get("spot_trading_allowed") else "否"
            lines.append(f"  Binance 现货: {exch.get('status')} ｜ 交易对: {exch.get('base_asset')}/{exch.get('quote_asset')} ｜ 现货可交易: {spot_ok}")
        else:
            lines.append("  Binance 现货: 未上市 / 不支持现货")

        # 官方主页
        homepages = it.get("homepage") or []
        if homepages:
            lines.append(f"  官网: {', '.join(homepages[:2])}")
        else:
            lines.append("  官网: 未提供")

        # 同 ticker 候选提示
        same_cands = it.get("same_symbol_candidates") or []
        if len(same_cands) > 1:
            cand_str = "、".join(f"{c['id']}(#{c.get('market_cap_rank') or '无'})" for c in same_cands[:4])
            lines.append(f"  ⚠ 同 Ticker 候选 ({len(same_cands)}个): {cand_str}")

        # 风险项
        for rk in it.get("risks") or []:
            lines.append(f"  ⚠ 风险提示: {rk}")

        # 异常与降级告警
        for wn in it.get("warnings") or []:
            lines.append(f"  [降级说明] {wn}")

    if warnings:
        lines.append("")
        for w in warnings:
            lines.append(f"⚠ 全局警告: {w}")

    lines.append("")
    lines.append("-" * 72)
    return "\n".join(lines)


def render_search_digest(coins, query, elapsed_ms):
    """渲染模糊搜索候选列表的人类可读摘要。"""
    lines = []
    lines.append(f"[门户模糊搜索] 关键词: \"{query}\" · 找到 {len(coins)} 个候选 · 耗时 {elapsed_ms} ms")
    lines.append("请核对代币全称与市值排位（Rank 越小市值越大），避免选错仿盘：")
    lines.append("-" * 72)
    for idx, c in enumerate(coins[:15], 1):
        sym = c.get("symbol") or "—"
        name = c.get("name") or "—"
        cid = c.get("id") or "—"
        rank = c.get("market_cap_rank")
        rank_str = f"#{rank}" if rank else "无市值排名"
        lines.append(f"  {idx:2d}. {sym.upper():<8} ｜ {name:<24} ｜ ID: {cid:<20} ｜ {rank_str}")
    lines.append("-" * 72)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="虚拟币门户咨询快查（快查只解决「这是什么」，不构成投资建议；同名仿盘必须提示核验）"
    )
    parser.add_argument("--symbol", help="单个代币代码，如 PYTH 或 PYTHUSDT")
    parser.add_argument("--symbols", help="批量代币代码，逗号分隔，如 PYTH,BTC,ADA")
    parser.add_argument("--search", help="模糊搜索词，如 \"peth\"，返回候选列表供人工挑选")
    parser.add_argument("--out", help="输出 JSON 文件路径")
    parser.add_argument("--timeout", type=int, default=12, help="HTTP 超时时间（默认 12 秒）")
    parser.add_argument("--retries", type=int, default=2, help="429 及网络异常重试次数（默认 2 次）")
    parser.add_argument("--no-exchange", action="store_true", help="跳过 Binance 现货上市场状核验")

    args = parser.parse_args()

    # 处理输入参数
    sym_list = []
    if args.symbol:
        sym_list.append(args.symbol.strip())
    if args.symbols:
        sym_list.extend([s.strip() for s in args.symbols.split(",") if s.strip()])

    if not sym_list and not args.search:
        err_msg = {"ok": False, "error": "请提供 --symbol、--symbols 或 --search 参数"}
        print(json.dumps(err_msg, ensure_ascii=False))
        return 1

    t0 = time.time()
    global_warnings = []

    # 模糊搜索模式
    if args.search:
        coins, search_warns, search_url = coingecko_search(args.search, timeout=args.timeout, retries=args.retries)
        elapsed_ms = int((time.time() - t0) * 1000)
        global_warnings.extend(search_warns)

        payload = {
            "schema": "kexi.portal/1",
            "mode": "search",
            "query": args.search,
            "generated_at": datetime.now(TZ8).isoformat(),
            "elapsed_ms": elapsed_ms,
            "count": len(coins),
            "candidates": [
                {
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "symbol": (c.get("symbol") or "").upper(),
                    "market_cap_rank": c.get("market_cap_rank"),
                }
                for c in coins
            ],
            "warnings": global_warnings,
            "sources": [search_url],
        }

        if args.out:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
                with open(args.out, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
            except Exception as e:
                global_warnings.append(f"写入文件失败: {str(e)[:80]}")

        print(render_search_digest(coins, args.search, elapsed_ms))
        print(json.dumps({
            "ok": True,
            "mode": "search",
            "query": args.search,
            "count": len(coins),
            "out": args.out,
            "candidates": [c.get("id") for c in coins[:10]]
        }, ensure_ascii=False))
        return 0

    # 标的详情快查模式
    items = []
    for s in sym_list:
        try:
            it = lookup_single_symbol(
                s,
                timeout=args.timeout,
                retries=args.retries,
                no_exchange=args.no_exchange
            )
            items.append(it)
        except Exception as e:
            items.append({
                "query": s,
                "symbol": strip_quote_suffix(s),
                "binance_symbol": f"{strip_quote_suffix(s)}USDT",
                "name": None,
                "coingecko_id": None,
                "categories": [],
                "platform": {"native_chain": None, "contracts": {}},
                "homepage": [],
                "market": {
                    "current_price_usd": None,
                    "market_cap_usd": None,
                    "market_cap_rank": None,
                    "circulating_supply": None,
                    "total_supply": None,
                    "max_supply": None,
                    "last_updated": None,
                },
                "listing": {
                    "genesis_date": None,
                    "listing_price": None,
                    "listing_currency": None,
                    "listing_source": None,
                    "listing_source_url": None,
                },
                "exchange": None,
                "risks": [f"查询发生致命异常: {str(e)[:120]}"],
                "warnings": [f"执行异常: {str(e)[:120]}"],
                "sources": [],
                "same_symbol_candidates": [],
            })

    elapsed_ms = int((time.time() - t0) * 1000)

    payload = {
        "schema": "kexi.portal/1",
        "generated_at": datetime.now(TZ8).isoformat(),
        "elapsed_ms": elapsed_ms,
        "count": len(items),
        "items": items,
        "warnings": global_warnings,
    }

    if args.out:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            global_warnings.append(f"写入文件失败: {str(e)[:80]}")

    print(render_digest(items, global_warnings, elapsed_ms))
    print(json.dumps({
        "ok": True,
        "count": len(items),
        "out": args.out,
        "symbols": [i["symbol"] for i in items]
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
