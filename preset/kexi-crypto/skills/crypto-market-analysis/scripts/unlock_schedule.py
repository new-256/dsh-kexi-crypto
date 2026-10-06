#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unlock_schedule.py — 代币解锁时间表与冲击评估（G7）。

【2026-09-30 免费源探测结论】详见 `docs/unlock-source-probe-2026-09-30.md`。
待办要求「先探测能否免 key 拿解锁明细」，实测结论是**没有任何免 key 源能给出
「日期+数量」的解锁事件明细**，逐项证据：

  · DefiLlama  api.llama.fi/emissions、/emission/{protocol} → HTTP 402
    响应体原文 "Upgrade to the paid API plan at https://defillama.com/subscription"
    （解锁/排放数据已整体转付费墙）；/unlocks → 404；defillama.com/unlocks 页面本身
    被 Cloudflare 挑战 403。/protocols 虽然 200，但 9MB 全量正则里 unlock/vesting
    命中全是协议名（Sablier / Unlock Protocol 之类），**没有任何解锁计划字段**。
  · TokenUnlocks api.tokenunlocks.ai → SSL UNEXPECTED_EOF（连试 3 次、两个路径）
  · CryptoRank /v1/price → 401 nginx auth；Tokenomist → 401 "x-api-key not found"；
    CoinMarketCal → 403；VestLabs → TLS 握手失败；Binance sapi → -2014 API-key invalid
  · CoinGecko 免费档 /coins/{id} → 200，但**全字段不含 unlock/vesting/cliff**
    （实测 doublezero 全文正则命中 0）；/circulating_supply 与 /supply_chart 免费档均 404
  · Binance Alpha 公开 bapi → 200，只有汇总 totalSupply/circulatingSupply/listingTime
  · Blockscout v2 /tokens/{addr}、Solana getTokenSupply → 200，仅汇总供应量；
    旧版 /api?module=proxy&action=eth_call 返回 "Unknown module"，无法通用读 vesting 合约

因此**日历本身仍然是手工录入**（registry / --input），工具绝不编造解锁日期。

在此之上，`--verify-supply` 用免 key 的 CoinGecko 免费档拉**汇总供应量**
（流通量 / 总供应 / 最大供应），仅用于校验手工录入的 unlock_pct 是否自洽：
总量口径对不对得上、未来登记量是否覆盖未流通量。这是**校验**不是**取数**——
汇总供应量里没有任何日期，绝不用它反推解锁计划；取不到就如实降级并写 warning。

事件 JSON（--input 或内置 registry 文件）：
  {"symbol": "2Z", "total_supply": 1e9, "events": [
      {"date": "2026-10-01", "name": "团队份额", "unlock_tokens": 178000000}, ... ]}

输出：逐事件给 unlock_pct（占总供应）、days_until、impact_score(0-100)、
impact_level（低/中/高/极高）与 action；近 30 天高冲击事件汇总进 risk_flags。
加 --verify-supply 时追加 supply_check 交叉校验块（并把结论写进 warnings）。

口径铁律：日期为 UTC；unlock_tokens 必须数值；无法核实的事件保留 unverified=true
而不是丢弃。纯 stdlib。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# 同目录公共取数模块（存在则优先复用其重试/超时实现）
try:
    import datasources as ds
except ImportError:      # pragma: no cover - 单文件调用场景
    ds = None

# 内置登记路径（随包分发，用户可在 registry/ 累积已知代币的解锁计划）
_HERE = os.path.dirname(os.path.abspath(__file__))
BUILTIN_REGISTRY = os.path.join(_HERE, "..", "registry", "unlock-schedules.json")

# 冲击分档与默认前瞻窗口
LEVELS = [("极高", 80), ("高", 60), ("中", 35), ("低", 0)]
HORIZON_DAYS = 30

# 取数口径（与 datasources.py 保持一致）
UA = {"User-Agent": "crypto-trend-analyst/1.0 (+kexi-plugin)"}
DEFAULT_TIMEOUT = 8
DEFAULT_RETRIES = 2
CG_BASE = "https://api.coingecko.com/api/v3"

# 交叉校验阈值
SUPPLY_TOLERANCE = 0.05     # 总量口径差异 >5% 判为不自洽
COVERAGE_LOW = 0.5          # 未来登记量不足未流通量的 50% → 日历可能漏登
COVERAGE_HIGH = 1.5         # 超过未流通量的 150% → 与流通量口径矛盾

# 常驻诚实边界：任何免 key 源都给不出带日期的解锁明细（2026-09-30 实测）
MANUAL_ONLY_WARNING = (
    "免 key 源无法提供带日期的解锁明细（DefiLlama emissions 已转 402 付费墙，"
    "TokenUnlocks/CryptoRank/Tokenomist 需 key 或不可达，实测见 "
    "docs/unlock-source-probe-2026-09-30.md）；当前日历为手工录入，日期与数量需自行核实"
)


def http_get_json(url, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES):
    """GET JSON。优先复用 datasources.http_get_json，否则自备同口径回退。

    回退逻辑与 datasources.py 一致：4xx（非 429）立即放弃，其余异常指数退避重试。
    统一抛 RuntimeError，由调用方降级——本脚本任何取数失败都不影响主流程。
    """
    if ds is not None and hasattr(ds, "http_get_json"):
        try:
            return ds.http_get_json(url, timeout=timeout, retries=retries)
        except Exception:
            pass
    last = None
    for i in range(max(1, retries)):
        try:
            req = urllib.request.Request(url, headers=UA)
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


def _num(v):
    """安全转 float：None / 空串 / 非数字一律 None（绝不让「取不到」伪装成 0）。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None      # 排除 NaN



def parse_date(s):
    """支持 YYYY-MM-DD 或 ISO；返回 aware datetime（UTC）。"""
    s = str(s).strip()
    if len(s) == 10:
        return datetime(int(s[:4]), int(s[5:7]), int(s[8:10]), tzinfo=timezone.utc)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def score_event(unlock_pct, days_until):
    """冲击分（0-100）：规模为主、时间临近加权。仅对未发生事件有时间加权。

    校准（据实战教训：2Z 在解锁前 5 天仍有 17.8% 供应待解锁，追高统计上吃亏，
    工具必须给出高冲击+预警）：base = 4×unlock_pct，≥25% 即满分封顶。
      7 日内: 20%→极高, 15%→高, 9%→中, <9%→低
    """
    base = min(unlock_pct, 25) * 4.0
    if days_until is None:
        return round(min(base, 100), 1)
    if days_until < 0:
        return 0                                # 已解锁事件不再构成立即冲击
    if days_until <= 7:
        mult = 1.0
    elif days_until <= 30:
        mult = 0.8
    elif days_until <= 90:
        mult = 0.5
    else:
        mult = 0.3
    return round(min(base * mult, 100), 1)


def level_for(score):
    for name, thresh in LEVELS:
        if score >= thresh:
            return name
    return "低"


def analyze(payload, now=None):
    now = now or datetime.now(timezone.utc)
    symbol = payload.get("symbol")
    total_supply = payload.get("total_supply")
    events = payload.get("events", [])
    out_events = []
    risk_flags = []
    errors = []
    # 常驻诚实边界：免 key 源给不出带日期的解锁明细，日历永远带这句声明
    warnings = [MANUAL_ONLY_WARNING]

    if not isinstance(total_supply, (int, float)) or total_supply <= 0:
        errors.append(f"{symbol}: total_supply 缺失或非正，无法计算解锁占比")
        total_supply = None
    if not isinstance(events, list):
        errors.append(f"{symbol}: events 必须为数组")
        events = []

    for i, e in enumerate(events):
        if not isinstance(e, dict):
            warnings.append(f"events[{i}] 非对象，已跳过")
            continue
        try:
            dt = parse_date(e["date"])
        except Exception:
            warnings.append(f"events[{i}] 日期无法解析 ({e.get('date')!r})，保留 unverified")
            dt = None
        unlock = e.get("unlock_tokens")
        if not isinstance(unlock, (int, float)):
            warnings.append(f"events[{i}] unlock_tokens 非数值，保留 unverified")
            unlock = None
        days = (dt - now).days if dt else None
        pct = (100.0 * unlock / total_supply) if (unlock is not None and total_supply) else None
        score = score_event(pct, days) if pct is not None else 0
        level = level_for(score)
        action = action_for(level, days)
        rec = {
            "date": e["date"] if e.get("date") else None,
            "name": e.get("name", ""),
            "unlock_tokens": unlock,
            "unlock_pct": round(pct, 3) if pct is not None else None,
            "days_until": days,
            "impact_score": score,
            "impact_level": level,
            "action": action,
            "unverified": bool(dt is None or unlock is None),
        }
        out_events.append(rec)
        if days is not None and 0 <= days <= HORIZON_DAYS and level in ("高", "极高"):
            risk_flags.append(
                f"{days} 天后（{rec['date']}）解锁 {rec['unlock_pct']}% 供应，冲击{level}：{action}")

    upcoming = [r for r in out_events if r["days_until"] is not None and r["days_until"] >= 0]
    nearest = sorted(upcoming, key=lambda r: r["days_until"])[0] if upcoming else None
    max_level = "低"
    for r in upcoming:
        if r["days_until"] <= 90 and level_rank(r["impact_level"]) > level_rank(max_level):
            max_level = r["impact_level"]
    return {
        "symbol": symbol,
        "total_supply": total_supply,
        "as_of": now.strftime("%Y-%m-%d UTC"),
        "events": out_events,
        "nearest_unlock": nearest,
        "horizon_days": HORIZON_DAYS,
        "risk_level_90d": max_level,
        "risk_flags": risk_flags,
        "errors": errors,
        "warnings": warnings,
        "data_source": payload.get("data_source", "manual"),
    }


def action_for(level, days):
    if days is not None and days < 0:
        return "已解锁：关注解锁后抛压释放情况"
    if level == "极高":
        return "解锁前回避追高；已持仓者减仓或对冲，防抛压砸盘"
    if level == "高":
        return "解锁前不新开多单；收紧止损，等抛压落地后再评估"
    if level == "中":
        return "纳入观察；控制仓位，解锁窗口附近留意量能"
    return "影响有限，常规跟踪"


def level_rank(name):
    return {"低": 1, "中": 2, "高": 3, "极高": 4}.get(name, 0)


def load_registry(path, symbol):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    entries = doc.get("tokens", doc if isinstance(doc, list) else [])
    for it in entries:
        if str(it.get("symbol", "")).upper() == symbol.upper():
            return it
    return None


# ── 免 key 汇总供应量取数（只校验，不产生解锁日期）─────────────────────────

def resolve_cg_id(symbol, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES):
    """由代币代码解析 CoinGecko id。**只在能唯一确定时才自动采用**，绝不瞎猜。

    两级判定：① 代码完全相等（忽略大小写）且唯一 → 采用；② 无完全匹配但结果仅一个 → 采用；
    其余（多个完全匹配 / 多个模糊结果 / 查不到）一律不猜，返回 None 并在 notes 说明。
    返回 (gecko_id, notes, mode)；gecko_id 为 None 时 notes 里说明原因。
    """
    notes = []
    if not symbol:
        return None, ["未提供代币代码，无法解析 CoinGecko id"], "no_symbol"
    try:
        doc = http_get_json(f"{CG_BASE}/search?query={urllib.parse.quote(symbol)}",
                            timeout=timeout, retries=retries)
    except Exception as e:
        return None, [f"CoinGecko 代币检索失败: {e}"], "search_failed"
    coins = (doc or {}).get("coins") or []
    if not coins:
        return None, [f"CoinGecko 检索不到代码 {symbol}，请用 --cg-id 指定"], "not_found"
    want = str(symbol).strip().upper()
    exact = [c for c in coins if str(c.get("symbol", "")).strip().upper() == want]
    if len(exact) == 1:
        return exact[0].get("id"), notes, "auto_exact_symbol"
    cands = ", ".join(f"{c.get('id')}({c.get('symbol')})" for c in coins[:5])
    if len(exact) > 1:
        return None, [f"代码 {symbol} 在 CoinGecko 有多个完全同代码的代币，不自动猜测，"
                      f"请用 --cg-id 指定：{cands}"], "ambiguous"
    if len(coins) == 1:
        gid = coins[0].get("id")
        if gid:
            return gid, notes, "auto_unique"
    return None, [f"代码 {symbol} 在 CoinGecko 命中多个结果且无完全同代码项，不自动猜测，"
                  f"请用 --cg-id 指定：{cands}"], "ambiguous"


def fetch_supply(symbol, cg_id=None, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES):
    """取 CoinGecko 免费档的**汇总供应量**（流通量/总供应/最大供应）。

    诚实边界：这里没有任何日期字段，调用方只准拿它做 unlock_pct 的自洽性校验，
    不准反推解锁计划。取不到就 available=False + errors/warnings，绝不填 0 冒充。
    """
    out = {
        "available": False,
        "source": "coingecko",
        "gecko_id": None,
        "resolution": None,
        "name": None,
        "symbol": symbol,
        "circulating_supply": None,
        "total_supply": None,
        "max_supply": None,
        "last_updated": None,
        "errors": [],
        "warnings": [],
    }
    gid = (cg_id or "").strip()
    if gid:
        out["resolution"] = "explicit_cg_id"
    else:
        gid, notes, mode = resolve_cg_id(symbol, timeout=timeout, retries=retries)
        out["resolution"] = mode
        out["warnings"].extend(notes)
    out["gecko_id"] = gid or None
    if not gid:
        return out

    url = (f"{CG_BASE}/coins/{urllib.parse.quote(gid)}?localization=false&tickers=false"
           f"&market_data=true&community_data=false&developer_data=false&sparkline=false")
    try:
        doc = http_get_json(url, timeout=timeout, retries=retries)
    except Exception as e:
        out["errors"].append(f"CoinGecko 供应量取数失败（{gid}）: {e}")
        return out
    md = (doc or {}).get("market_data") or {}
    out["name"] = doc.get("name")
    out["symbol"] = doc.get("symbol") or symbol
    out["last_updated"] = doc.get("last_updated")
    out["circulating_supply"] = _num(md.get("circulating_supply"))
    out["total_supply"] = _num(md.get("total_supply"))
    out["max_supply"] = _num(md.get("max_supply"))
    out["available"] = any(v is not None for v in
                           (out["circulating_supply"], out["total_supply"], out["max_supply"]))
    if not out["available"]:
        out["warnings"].append(f"CoinGecko 未给 {gid} 提供任何供应量字段，无法校验")
    return out


def verify_supply(payload, res, supply):
    """用汇总供应量校验手工录入事件的 unlock_pct 是否自洽（不复用 analyze 的打分逻辑）。

    校验三件事：① registry 的 total_supply 与外部口径是否一致；
    ② 单个事件解锁量是否超过登记总量；③ 已登记的未来解锁量与「未流通量」的覆盖度。
    结论写进 res["warnings"]/res["errors"]，并作为 supply_check 块返回。
    """
    res.setdefault("warnings", [])
    res.setdefault("errors", [])
    chk = {
        "checked": True,
        "source": (supply or {}).get("source"),
        "available": bool((supply or {}).get("available")),
        "gecko_id": (supply or {}).get("gecko_id"),
        "gecko_name": (supply or {}).get("name"),
        "resolution": (supply or {}).get("resolution"),
        "last_updated": (supply or {}).get("last_updated"),
        "registry_total_supply": payload.get("total_supply"),
        "source_total_supply": None,
        "total_supply_basis": None,
        "total_supply_delta_pct": None,
        "circulating_supply": (supply or {}).get("circulating_supply"),
        "non_circulating": None,
        "registered_future_unlock_tokens": 0,
        "registered_future_pct_of_total": None,
        "coverage_of_non_circulating_pct": None,
        "consistent": None,
        "warnings": [],
        "errors": [],
    }
    reg_total = _num(payload.get("total_supply"))
    src_total = _num((supply or {}).get("total_supply"))
    src_max = _num((supply or {}).get("max_supply"))
    circ = _num((supply or {}).get("circulating_supply"))
    if src_total is None and src_max is not None:
        src_total, basis = src_max, "max_supply"
    else:
        basis = "total_supply" if src_total is not None else None
    chk["source_total_supply"] = src_total
    chk["total_supply_basis"] = basis

    # 取数失败：如实降级，说明未做外部校验（绝不静默当作通过）
    if not chk["available"]:
        reason = "; ".join((supply or {}).get("errors") or (supply or {}).get("warnings")
                           or ["未知原因"])
        msg = f"免 key 汇总供应量不可用（{reason}），本次未对 unlock_pct 做外部校验"
        chk["warnings"].append(msg)
        res["warnings"].append(msg)

    # ① 总量口径比对
    if reg_total and src_total:
        delta = (reg_total - src_total) / src_total
        chk["total_supply_delta_pct"] = round(delta * 100, 3)
        if abs(delta) > SUPPLY_TOLERANCE:
            msg = (f"总供应量口径不自洽：登记 {reg_total:,.0f} vs "
                   f"{chk['source']} {basis} {src_total:,.0f}（偏差 {delta * 100:+.1f}%），"
                   f"所有 unlock_pct 请按偏差换算后再判断")
            chk["warnings"].append(msg)
            res["warnings"].append(msg)
            chk["consistent"] = False
    elif reg_total and src_total is None and chk["available"]:
        msg = f"{chk['source']} 未提供 {basis or 'total/max'}，总供应量未能外部比对"
        chk["warnings"].append(msg)
        res["warnings"].append(msg)

    # ② 单事件解锁量是否超过登记总量
    for i, ev in enumerate(payload.get("events") or []):
        if not isinstance(ev, dict):
            continue
        u = _num(ev.get("unlock_tokens"))
        if reg_total and u and u > reg_total:
            msg = f"events[{i}]（{ev.get('name') or ev.get('date')}）解锁量 {u:,.0f} " \
                  f"超过登记总供应量 {reg_total:,.0f}，该事件口径有误"
            chk["warnings"].append(msg)
            res["warnings"].append(msg)
            chk["consistent"] = False

    # ③ 未来登记量 vs 未流通量（用外部总量口径衡量日历覆盖度）
    future_sum = 0.0
    for r in (res.get("events") or []):
        d = r.get("days_until")
        u = _num(r.get("unlock_tokens"))
        if u is not None and d is not None and d >= 0:
            future_sum += u
    chk["registered_future_unlock_tokens"] = future_sum
    if reg_total and future_sum:
        chk["registered_future_pct_of_total"] = round(100.0 * future_sum / reg_total, 3)
    if src_total is not None and circ is not None:
        non_circ = src_total - circ
        chk["non_circulating"] = non_circ
        if non_circ <= 0:
            msg = (f"{chk['source']} 口径下 total({src_total:,.0f}) ≤ circulating({circ:,.0f})，"
                   f"已无未流通供应，请核对总供应量来源")
            chk["warnings"].append(msg)
            res["warnings"].append(msg)
            chk["consistent"] = False
        else:
            cov = future_sum / non_circ
            chk["coverage_of_non_circulating_pct"] = round(cov * 100, 3)
            if future_sum and cov < COVERAGE_LOW:
                msg = (f"已登记的未来解锁量 {future_sum:,.0f} 仅占 {chk['source']} 未流通量"
                       f"（{non_circ:,.0f}）的 {cov * 100:.1f}%，解锁日历可能漏登，请补全后再下结论")
                chk["warnings"].append(msg)
                res["warnings"].append(msg)
            elif cov > COVERAGE_HIGH:
                msg = (f"已登记的未来解锁量 {future_sum:,.0f} 达 {chk['source']} 未流通量"
                       f"（{non_circ:,.0f}）的 {cov * 100:.1f}%，与流通量口径矛盾，请核对总供应量")
                chk["warnings"].append(msg)
                res["warnings"].append(msg)
                chk["consistent"] = False
    else:
        msg = f"{chk['source']} 未同时给出总量与流通量，日历覆盖度未做校验"
        chk["warnings"].append(msg)
        res["warnings"].append(msg)

    for w in (supply or {}).get("warnings") or []:
        if w not in res["warnings"]:
            res["warnings"].append(w)
    for e in (supply or {}).get("errors") or []:
        if e not in res["errors"]:
            res["errors"].append(e)
    if chk["consistent"] is None and chk["available"] and not chk["warnings"]:
        chk["consistent"] = True
    return chk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", help="代币代码（配合内置 registry 使用）")
    ap.add_argument("--input", help="手工事件 JSON 文件（最高优先级）")
    ap.add_argument("--registry", default=BUILTIN_REGISTRY, help="本地 registry（默认包内 builtin）")
    ap.add_argument("--verify-supply", action="store_true",
                    help="用免 key 的 CoinGecko 免费档汇总供应量校验 unlock_pct 自洽性"
                         "（只校验总量口径，**不提供任何解锁日期**）")
    ap.add_argument("--cg-id", help="CoinGecko 代币 id（跳过代码自动解析；同名多币时必填）")
    ap.add_argument("--out")
    ap.add_argument("--md", help="可选：另存 Markdown 摘要")
    args = ap.parse_args()

    payload = None
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            payload = json.load(f)
    elif args.symbol:
        payload = load_registry(args.registry, args.symbol)
        if payload is None:
            print(json.dumps({
                "error": f"registry 中无 {args.symbol} 的解锁计划；请用 --input 手工录入事件",
                "registry": args.registry,
            }, ensure_ascii=False))
            sys.exit(2)
    else:
        ap.error("必须给 --input（手工事件 JSON）或 --symbol（查 registry）")

    res = analyze(payload)
    if args.verify_supply:
        res["supply_check"] = verify_supply(
            payload, res, fetch_supply(payload.get("symbol") or args.symbol, cg_id=args.cg_id))

    text = json.dumps(res, ensure_ascii=False, indent=2)
    if args.out:
        od = os.path.dirname(os.path.abspath(args.out))
        if od:
            os.makedirs(od, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    if args.md:
        lines = [f"# {res['symbol']} 解锁时间表（截至 {res['as_of']}）", ""]
        lines.append(f"未来 90 天风险定级：**{res['risk_level_90d']}**")
        lines.append("")
        lines.append("| 日期 | 事件 | 解锁占比 | 剩余天数 | 冲击 | 动作 |")
        lines.append("|---|---|---|---|---|---|")
        for r in sorted(res["events"], key=lambda x: (x["days_until"] is None, x["days_until"])):
            lines.append("| {d} | {n} | {p}% | {dd} | {lv} | {a} |".format(
                d=r["date"], n=r["name"], p=r["unlock_pct"], dd=r["days_until"],
                lv=r["impact_level"], a=r["action"]))
        if res["risk_flags"]:
            lines += ["", "## 近 30 天高冲击预警", ""]
            lines += ["- " + x for x in res["risk_flags"]]
        if res.get("supply_check"):
            sc = res["supply_check"]
            lines += ["", "## 供应量交叉校验（不提供解锁日期）", ""]
            lines.append(f"- 来源：{sc.get('source')} / {sc.get('gecko_id')} "
                         f"（{sc.get('gecko_name')}），取数时间 {sc.get('last_updated')}")
            lines.append(f"- 登记总供应 {sc.get('registry_total_supply')} vs "
                         f"源端 {sc.get('total_supply_basis')} {sc.get('source_total_supply')}"
                         f"（偏差 {sc.get('total_supply_delta_pct')}%）")
            lines.append(f"- 流通量 {sc.get('circulating_supply')}，未流通量 {sc.get('non_circulating')}，"
                         f"未来登记解锁覆盖 {sc.get('coverage_of_non_circulating_pct')}%")
            for w in sc.get("warnings") or []:
                lines.append(f"- ⚠️ {w}")
        od = os.path.dirname(os.path.abspath(args.md))
        if od:
            os.makedirs(od, exist_ok=True)
        with open(args.md, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    if args.out:
        print(f"unlock-schedule -> {args.out} (events={len(res['events'])}, "
              f"90d risk={res['risk_level_90d']}, flags={len(res['risk_flags'])})")
    else:
        print(text)


if __name__ == "__main__":
    main()
