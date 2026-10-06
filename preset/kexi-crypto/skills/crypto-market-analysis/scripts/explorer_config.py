#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
explorer_config.py - 区块链浏览器与数据源统一配置面（v1.5.2 新增）

【核心定位与问题回应】
  用户提问：“区块链浏览器相关配置是否应该一应俱全？”
  答案是明确的：否定。「一应俱全」是错误目标 —— 业界大多数链上数据源（Nansen Smart Money、
  Arkham 完整 API、Dune Analytics、Whale Alert 官方 API 等）都需要 API key 或付费商业订阅，
  盲目把配置堆满只会制造大量灰色不可用项，反而让用户误以为工具“什么都能查”。
  正确做法是：统一配置面 + 按需启用 + 能力矩阵如实展示（可用 / 需 key / 需付费 / 不可达），
  让用户清楚知道要拿到更多能力需要付出什么。

【数据源注册表规范】
  REGISTRY 中每一项均为字典，严格包含且仅包含以下 7 个字段：
    - name (str): 数据源显示名称
    - url (str): 探测与基准端点（轻量化探测）
    - free_tier (bool): 产品本身是否提供实质性免费档（商业付费产品即使有401端点亦标为False）
    - needs_key (bool): 是否强制需要 API Key 才能调用核心能力
    - key_env (str | None): 对应的环境变量名称，禁止硬编码和明文记录
    - capabilities (list[str]): 该源所支持的核心能力清单
    - notes (str): 诚实边界与使用说明

【状态分类规则 (四态互斥)】
  1. "需付费": free_tier 为 False。付费性是产品客观事实，绝非单次网络探针结果（即使探针 401 亦属付费）。
  2. "需key": needs_key 为 True 且对应环境变量未配置。Key 门禁决定核心可用性，即使公共 ping 返回 200 仍属需key。
  3. "可用": 免 Key 且网络探针连通正常（或已配置有效 Key），可立即供用户取数。
  4. "不可达": 免 Key（或已配 Key）但遭遇网络故障、超时（>6s）、HTTP 451/403 等地区策略拦截。

【运行模式】
  --list        打印注册表清单（离线展示，不发网络请求）
  --check       对注册表所有端点发起实测探针（超时 6s，单项失败绝不中断）
  --out <path>  产出完整 JSON 快照（契约 schema: kexi.explorer_config/1）
  --no-network  跳过外网探测，基于环境变量配置与静态契约进行推断
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
USER_AGENT = "crypto-trend-analyst/1.0 (+kexi-plugin)"
PROBE_TIMEOUT = 6
MAX_RETRIES = 1
MAX_BODY_BYTES = 4096

RSSHUB_MIRRORS = [
    "https://rsshub.woodland.cafe",
    "https://rsshub.ktachibana.party",
    "https://rsshub.liumingye.cn",
    "https://rsshub.rssforever.com",
]

VERDICT_TEXT = (
    "「一应俱全」是错误目标 —— 业界大多数链上数据源（Nansen Smart Money、Arkham 完整 API、"
    "Dune Analytics、Whale Alert 官方 API）都需要 API key 或付费订阅，盲目把配置堆满只会制造大量灰色不可用项，"
    "反而让用户误以为工具“什么都能查”。正确做法是：统一配置面 + 按需启用 + 能力矩阵如实展示"
    "（可用 / 需 key / 需付费 / 不可达），让用户清楚知道要拿到更多能力需要付出什么。"
)

# ── 注册表清单（每个条目严格包含 7 个 key）─────────────────────────
REGISTRY = [
    {
        "name": "Etherscan 多链 (v2)",
        "url": "https://api.etherscan.io/v2/api?chainid=1&module=stats&action=ethprice",
        "free_tier": True,
        "needs_key": True,
        "key_env": "ETHERSCAN_API_KEY",
        "capabilities": [
            "大额链上转账流",
            "代币持币地址数",
            "以太坊及L2转账明细",
            "Gas与网络状态"
        ],
        "notes": "v2统一网关，支持chainid=1/56/137/8453/42161等多链；未配Key查询代币转账返回Missing/Invalid API Key，部分链需付费"
    },
    {
        "name": "Blockscout (Ethereum)",
        "url": "https://eth.blockscout.com/api/v2/stats",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "大额链上转账流",
            "链上地址实体标签",
            "代币转账流",
            "DeFi 资金流"
        ],
        "notes": "完全开源免费免Key，提供/api/v2/token-transfers带USD汇率与地址实体标签"
    },
    {
        "name": "Blockscout (Base)",
        "url": "https://base.blockscout.com/api/v2/stats",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "大额链上转账流",
            "链上地址实体标签",
            "代币转账流"
        ],
        "notes": "Base L2官方开源浏览器，免Key提供完整REST v2 API与代币转账流"
    },
    {
        "name": "Blockscout (Arbitrum)",
        "url": "https://arbitrum.blockscout.com/api/v2/stats",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "大额链上转账流",
            "链上地址实体标签",
            "代币转账流"
        ],
        "notes": "Arbitrum L2开源浏览器，免Key提供REST v2 API"
    },
    {
        "name": "Blockscout (Optimism)",
        "url": "https://optimism.blockscout.com/api/v2/stats",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "大额链上转账流",
            "链上地址实体标签",
            "代币转账流"
        ],
        "notes": "Optimism OP Mainnet官方开源浏览器，完全免Key开放REST v2接口与代币转账明细"
    },
    {
        "name": "Blockscout (Polygon)",
        "url": "https://polygon.blockscout.com/api/v2/stats",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "大额链上转账流",
            "链上地址实体标签",
            "代币转账流"
        ],
        "notes": "Polygon PoS官方开源浏览器，免Key提供v2代币转账流与地址实体标签"
    },
    {
        "name": "Blockscout (Celo)",
        "url": "https://celo.blockscout.com/api/v2/stats",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "大额链上转账流",
            "链上地址实体标签",
            "代币转账流"
        ],
        "notes": "Celo公链开源浏览器，完全免Key开放REST v2接口与持币分析"
    },
    {
        "name": "Blockscout (Gnosis)",
        "url": "https://gnosis.blockscout.com/api/v2/stats",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "大额链上转账流",
            "链上地址实体标签",
            "代币转账流"
        ],
        "notes": "Gnosis Chain官方开源浏览器，免Key提供v2代币转账明细与地址实体标签"
    },
    {
        "name": "Whale Alert 官方 API",
        "url": "https://api.whale-alert.io/v1/status",
        "free_tier": False,
        "needs_key": True,
        "key_env": "WHALE_ALERT_API_KEY",
        "capabilities": [
            "大额链上转账流",
            "交易所净流入",
            "交易所大额充提"
        ],
        "notes": "商业付费服务，无公开免费档，未认证请求返回404/401"
    },
    {
        "name": "Whale Alert (RSSHub Telegram镜像)",
        "url": "https://rsshub.woodland.cafe/telegram/channel/whale_alert",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "大额链上转账流(陈旧)"
        ],
        "notes": "Telegram频道镜像免Key；支持多镜像轮询(woodland.cafe/ktachibana.party/liumingye.cn/rssforever.com)防单点抖动；但上游源已于2020-03-11停更，内容严重陈旧，仅作对照展示"
    },
    {
        "name": "Alternative.me F&G",
        "url": "https://api.alternative.me/fng/?limit=1",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "恐惧贪婪指数"
        ],
        "notes": "免费免Key公开接口，每日更新加密市场情绪指数"
    },
    {
        "name": "Binance 现货",
        "url": "https://api.binance.com/api/v3/ping",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "CEX 行情/持仓量",
            "大额成交追踪"
        ],
        "notes": "公开免Key现货行情接口；实测在当前运行环境受出网地区限制，云端始终返回 HTTP 451 (Service unavailable from a restricted location)，直连不可用，需依赖代理或 Bybit/OKX 兜底"
    },
    {
        "name": "Binance Futures (fapi)",
        "url": "https://fapi.binance.com/fapi/v1/ping",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "CEX 行情/持仓量",
            "资金费率",
            "多空持仓比"
        ],
        "notes": "公开免Key合约指标接口(持仓量/资金费率)；实测在当前运行环境受出网地区限制，云端始终返回 HTTP 451，直连不可用，需依赖代理或 Bybit/OKX 兜底"
    },
    {
        "name": "OKX 公开行情",
        "url": "https://www.okx.com/api/v5/public/time",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "CEX 行情/持仓量",
            "资金费率",
            "系统状态"
        ],
        "notes": "公开v5公共端点免Key可用，国内网络友好度高"
    },
    {
        "name": "Bybit 公开行情",
        "url": "https://api.bybit.com/v5/market/time",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "CEX 行情/持仓量",
            "资金费率"
        ],
        "notes": "公开v5免Key端点，受CloudFront国家地区分发策略限制(HTTP 403)"
    },
    {
        "name": "CoinGecko",
        "url": "https://api.coingecko.com/api/v3/ping",
        "free_tier": True,
        "needs_key": False,
        "key_env": "COINGECKO_API_KEY",
        "capabilities": [
            "代币基础信息",
            "流通市值/FDV",
            "代币合约地址映射"
        ],
        "notes": "基础功能免Key(有频控限额)，可选配COINGECKO_API_KEY提升调用限额"
    },
    {
        "name": "DefiLlama",
        "url": "https://api.llama.fi/v2/chains",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "TVL/协议数据",
            "DeFi 资金流",
            "稳定币每日净增发"
        ],
        "notes": "完全免费开放无Key，国内网络通畅且无严格限额，提供链TVL与稳定币净增发"
    },
    {
        "name": "Dune Analytics",
        "url": "https://api.dune.com/api/v1/query/1/results",
        "free_tier": True,
        "needs_key": True,
        "key_env": "DUNE_API_KEY",
        "capabilities": [
            "大额链上转账流",
            "交易所净流入",
            "DeFi 资金流",
            "自定义SQL数据看板"
        ],
        "notes": "提供免费API额度(每月2500积分需注册获取Key)，高级查询需付费订阅；未配Key返回HTTP 401"
    },
    {
        "name": "Nansen Smart Money",
        "url": "https://api.nansen.ai/v1/smart-money",
        "free_tier": False,
        "needs_key": True,
        "key_env": "NANSEN_API_KEY",
        "capabilities": [
            "聪明钱标签",
            "机构持仓异动",
            "链上地址实体标签"
        ],
        "notes": "商业付费产品(按次计费约$0.05/次)，无公开免费API，未认证返回404"
    },
    {
        "name": "Arkham Intelligence",
        "url": "https://intel.arkm.com/api/transfers/ethereum",
        "free_tier": False,
        "needs_key": True,
        "key_env": "ARKHAM_API_KEY",
        "capabilities": [
            "链上地址实体标签",
            "交易所充提追踪",
            "大额链上转账流"
        ],
        "notes": "完整API属于付费商业权限，无Key访问返回403，无免费开放API"
    },
    {
        "name": "Blockchair",
        "url": "https://api.blockchair.com/ethereum/stats",
        "free_tier": True,
        "needs_key": False,
        "key_env": None,
        "capabilities": [
            "链上基础指标",
            "全网区块状态"
        ],
        "notes": "免费免Key提供宏观统计指标，但不开放免费单钱包流式追踪"
    },
    {
        "name": "OKLink 多链浏览器",
        "url": "https://www.oklink.com/api/v5/explorer/blockchain/summary",
        "free_tier": True,
        "needs_key": True,
        "key_env": "OKLINK_API_KEY",
        "capabilities": [
            "多链基础数据",
            "大额链上转账流",
            "地址余额查询"
        ],
        "notes": "需注册申请OK-ACCESS-KEY，未带Key直接返回401"
    }
]


def _probe_http(url, timeout=PROBE_TIMEOUT, max_retries=MAX_RETRIES):
    """
    轻量 HTTP GET 探测器。
    截断读取前 4KB 数据，返回 (http_status, latency_ms, body_sample, error_msg)。
    对 Telegram 镜像源支持兄弟镜像轮询（woodland.cafe / ktachibana.party / liumingye.cn / rssforever.com）。
    绝不抛出未捕获异常。
    """
    headers = {"User-Agent": USER_AGENT}

    urls_to_try = [url]
    if "/telegram/channel/" in url:
        path = url.split("/telegram/channel/", 1)[1]
        for m in RSSHUB_MIRRORS:
            cand = f"{m}/telegram/channel/{path}"
            if cand not in urls_to_try:
                urls_to_try.append(cand)

    last_err = None
    latency_ms = 0

    for cand_url in urls_to_try:
        for attempt in range(max(1, max_retries + 1)):
            t0 = time.time()
            try:
                req = urllib.request.Request(cand_url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read(MAX_BODY_BYTES)
                    latency_ms = int((time.time() - t0) * 1000)
                    return resp.status, latency_ms, body, None
            except urllib.error.HTTPError as e:
                latency_ms = int((time.time() - t0) * 1000)
                body = b""
                try:
                    body = e.read(MAX_BODY_BYTES)
                except Exception:
                    pass
                if len(urls_to_try) > 1 and e.code in (403, 404, 429, 500, 502, 503):
                    last_err = f"HTTP {e.code}"
                    break
                if 400 <= e.code < 500 and e.code != 429:
                    return e.code, latency_ms, body, f"HTTP {e.code}"
                last_err = f"HTTP {e.code}"
            except Exception as e:
                latency_ms = int((time.time() - t0) * 1000)
                err_msg = str(e)
                if "timed out" in err_msg.lower():
                    last_err = f"超时 {timeout * 1000}ms"
                else:
                    last_err = f"网络异常: {err_msg[:60]}"
            if attempt < max_retries and len(urls_to_try) == 1:
                time.sleep(0.5)

    return None, latency_ms, b"", last_err


def evaluate_entry(entry, probe=True):
    """
    对单个注册表条目进行状态与可用性评估。
    保证只输出四种状态之一: "可用" / "需key" / "需付费" / "不可达"。
    """
    key_env = entry.get("key_env")
    key_val = os.environ.get(key_env, "").strip() if key_env else ""
    key_present = bool(key_val)

    free_tier = entry["free_tier"]
    needs_key = entry["needs_key"]

    # 1. 离线推断模式 (--no-network)
    if not probe:
        if not free_tier:
            status = "需付费"
            reason = "商业付费服务 (需订阅计划)"
        elif needs_key and not key_present:
            status = "需key"
            reason = f"未配置环境变量 {key_env}"
        else:
            status = "可用"
            reason = "免Key公开源(离线推断)" if not needs_key else f"已配置 {key_env}(离线推断)"

        res = dict(entry)
        res.update({
            "状态": status,
            "http_status": None,
            "latency_ms": 0,
            "reason": reason,
            "key_present": key_present,
        })
        return res

    # 2. 实际在线网络探测模式
    http_code, latency_ms, body_sample, err_str = _probe_http(entry["url"], timeout=PROBE_TIMEOUT, max_retries=MAX_RETRIES)

    # 规则 1：商业付费产品 (free_tier=False)，状态恒为 "需付费"
    if not free_tier:
        status = "需付费"
        if http_code:
            reason = f"商业付费服务 (HTTP {http_code})"
        else:
            reason = f"商业付费服务 ({err_str or '需订阅计划'})"

    # 规则 2：需配置 Key (needs_key=True) 且环境变量未设置，状态恒为 "需key"
    elif needs_key and not key_present:
        status = "需key"
        if http_code:
            reason = f"未配置环境变量 {key_env} (端点响应 HTTP {http_code})"
        else:
            reason = f"未配置环境变量 {key_env} ({err_str or '需设置环境变量'})"

    # 规则 3：free_tier=True 且 (无需Key 或 已配置Key)
    else:
        if http_code is not None and 200 <= http_code < 300:
            # 针对 Etherscan 等特定网关：未传Key时虽然 HTTP 200，但 Body 返回 Missing/Invalid API Key
            if b"Missing/Invalid API Key" in body_sample or (b"NOTOK" in body_sample and b"API Key" in body_sample):
                if not key_present:
                    status = "需key"
                    reason = f"未配置 {key_env} (服务端要求API Key)"
                else:
                    status = "需key"
                    reason = f"配置的 {key_env} 无效或受限"
            else:
                status = "可用"
                reason = f"HTTP 200 (延迟 {latency_ms}ms)"
        else:
            status = "不可达"
            if http_code == 451:
                reason = "HTTP 451 (受限国家/地区IP访问限制)"
            elif http_code == 403:
                reason = "HTTP 403 (CloudFront/CDN地区访问拦截)"
            elif http_code:
                reason = f"HTTP {http_code}"
            else:
                reason = err_str or "探测连接失败"

    res = dict(entry)
    res.update({
        "状态": status,
        "http_status": http_code,
        "latency_ms": latency_ms,
        "reason": reason,
        "key_present": key_present,
    })
    return res


def build_capability_matrix(evaluated_entries):
    """
    生成能力矩阵，供设置面板与前端渲染。
    对于每一项独立能力，将提供方归类为:
      - ready_now: 现已可用（免Key免费，探针通畅）
      - needs_user_action: 需用户操作（需配环境变量Key）
      - not_available: 当前不可用（商业付费订阅或网络阻断）
    """
    all_caps = []
    for e in evaluated_entries:
        for cap in e.get("capabilities", []):
            if cap not in all_caps:
                all_caps.append(cap)

    matrix = {}
    for cap in all_caps:
        ready_now = []
        needs_user_action = []
        not_available = []

        for e in evaluated_entries:
            if cap not in e.get("capabilities", []):
                continue
            name = e["name"]
            status = e["状态"]
            notes = e.get("notes", "")
            reason = e.get("reason", "")
            key_env = e.get("key_env")

            if status == "可用":
                ready_now.append({
                    "name": name,
                    "notes": notes,
                })
            elif status == "需key":
                needs_user_action.append({
                    "name": name,
                    "action": f"设置环境变量 {key_env}",
                    "key_env": key_env,
                    "notes": notes,
                })
            elif status == "需付费":
                not_available.append({
                    "name": name,
                    "reason": "需商业付费订阅",
                    "key_env": key_env,
                    "notes": notes,
                })
            else:  # 不可达
                not_available.append({
                    "name": name,
                    "reason": f"当前网络不可达 ({reason})",
                    "key_env": key_env,
                    "notes": notes,
                })

        matrix[cap] = {
            "ready_now": ready_now,
            "needs_user_action": needs_user_action,
            "not_available": not_available,
        }

    return matrix


def print_human_table(evaluated_entries, summary):
    """打印对齐的人类可读表格与汇总信息。"""
    print("=" * 104)
    print(f"{'数据源名称':<28} {'状态':<6} {'HTTP':<6} {'延迟(ms)':<8} {'Key环境':<18} {'状态说明与原因'}")
    print("-" * 104)
    for e in evaluated_entries:
        name = e["name"]
        status = e["状态"]
        http_code = str(e["http_status"]) if e["http_status"] is not None else "-"
        latency = str(e["latency_ms"]) if e["latency_ms"] else "-"
        key_str = e["key_env"] if e["key_env"] else "(免Key)"
        if e.get("key_present"):
            key_str += " [已设]"
        reason = e["reason"]
        print(f"{name:<28} {status:<6} {http_code:<6} {latency:<8} {key_str:<18} {reason}")
    print("=" * 104)
    print(f"[汇总统计] 总计: {summary['total']} | 可用: {summary['可用']} | 需key: {summary['需key']} "
          f"| 需付费: {summary['需付费']} | 不可达: {summary['不可达']}")


def main():
    parser = argparse.ArgumentParser(
        description="区块链浏览器与数据源统一配置面与连通性自检工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  python explorer_config.py --list\n  python explorer_config.py --check --out kexi_out/explorer_config.json"
    )
    parser.add_argument("--list", action="store_true", help="打印注册表数据源清单（离线模式）")
    parser.add_argument("--check", action="store_true", help="对所有注册表端点执行实时在线网络探测")
    parser.add_argument("--no-network", action="store_true", help="跳过实际网络请求，基于环境变量与契约推断状态")
    parser.add_argument("--out", default=None, help="输出完整配置与评估 JSON 文件的路径")
    parser.add_argument("--json-only", action="store_true", help="仅输出 JSON，不打印人读进度表格")

    args = parser.parse_args()

    # 默认行为：如果未指定 --check，则默认按列表清单模式
    do_probe = args.check and not args.no_network
    mode_name = "check" if args.check and not args.no_network else ("check_no_network" if args.check and args.no_network else "list")

    if not args.json_only:
        print(f"[配置面] 模式: {mode_name} | 注册数据源数: {len(REGISTRY)} | 探测中...", flush=True)

    # 依次评估每一项数据源
    evaluated_entries = []
    for entry in REGISTRY:
        evaluated = evaluate_entry(entry, probe=do_probe)
        evaluated_entries.append(evaluated)

    # 计算汇总
    summary = {
        "total": len(evaluated_entries),
        "可用": sum(1 for e in evaluated_entries if e["状态"] == "可用"),
        "需key": sum(1 for e in evaluated_entries if e["状态"] == "需key"),
        "需付费": sum(1 for e in evaluated_entries if e["状态"] == "需付费"),
        "不可达": sum(1 for e in evaluated_entries if e["状态"] == "不可达"),
    }

    capability_matrix = build_capability_matrix(evaluated_entries)

    # 收集所需环境变量名称（去重，不输出值）
    env_vars_needed = []
    for e in REGISTRY:
        k = e.get("key_env")
        if k and k not in env_vars_needed:
            env_vars_needed.append(k)

    recommendations = [
        "设置 ETHERSCAN_API_KEY 可解锁以太坊及各大 EVM L2 (Base, Arbitrum, Polygon 等) 的完整代币转账流与持币者分布分析",
        "Blockscout 7条主流公链与L2 (ETH/Base/Arbitrum/Optimism/Polygon/Celo/Gnosis) 当前完全免费免 Key 可用，提供 REST v2 转账流与地址实体标签，是零成本链上分析首选基石",
        "注册 Dune 账号并配置 DUNE_API_KEY 可免费获得每月 2,500 Credits，用于自定义 SQL 链上数据查询",
        "针对受限网络环境下 Binance 与 Bybit 接口返回 HTTP 451/403 的情况，建议配置本地合规代理或以 OKX / DefiLlama 接口作为备用通道",
        "Nansen Smart Money 与 Arkham 完整 API 为商业付费产品，普通个人分析建议优先使用 Blockscout + DefiLlama 组合，无需盲目采购付费订阅",
    ]

    out_data = {
        "schema": "kexi.explorer_config/1",
        "generated_at": datetime.now(TZ8).isoformat(),
        "mode": mode_name,
        "count": len(evaluated_entries),
        "entries": evaluated_entries,
        "summary": summary,
        "capability_matrix": capability_matrix,
        "recommendations": recommendations,
        "env_vars_needed": env_vars_needed,
        "verdict": VERDICT_TEXT,
    }

    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out_data, f, ensure_ascii=False, indent=2)

    if not args.json_only:
        print_human_table(evaluated_entries, summary)
        if args.out:
            print(f"[输出] 完整评估结果已落盘至: {args.out}", flush=True)

    # 最后一行必须是紧凑的单行 JSON 摘要（供系统契约解析）
    compact_json = {
        "ok": True,
        "schema": "kexi.explorer_config/1",
        "mode": mode_name,
        "total": summary["total"],
        "available": summary["可用"],
        "needs_key": summary["需key"],
        "paid": summary["需付费"],
        "unreachable": summary["不可达"],
    }
    if args.out:
        compact_json["out"] = args.out
    print(json.dumps(compact_json, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
