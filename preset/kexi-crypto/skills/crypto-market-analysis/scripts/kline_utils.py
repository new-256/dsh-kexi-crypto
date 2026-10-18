#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kline_utils.py - 全包共享的 K 线口径工具（v1.1.0 新增）

统一收口两个曾在专家包审查中被**实测复现**的系统性缺陷：

[P0-2] 未收盘 bar 污染
    各交易所最后一根日线在盘中是"未走完"的（close 是瞬时价、volume 是部分量）。
    直接参与 MA/MACD/RSI/量比/ATR 会系统性失真。
    实测（DOGE 2026-09-27 18:54，当根仅走完 45.4%）：
        MA10  0.094469 → 0.092872   （偏高 1.72%）
        RSI6  62.61    → 60.50
        量比  0.3221   → 0.5092     （低估 58%）
        MACD HIST 偏差 11.2%

[P0-4] 固定小数位
    低价币（PEPE 等）被 round(x, 2) 抹成 0.0，指标整体报废。

用法：
    import kline_utils as ku
    closed, dropped = ku.drop_open_bar(klines)        # 日线
    closed, dropped = ku.drop_open_bar(klines, ku.H4)
    price = ku.rn(x)                                  # 自适应精度

约定：
    klines = [[ts_ms, open, high, low, close, volume], ...]
    ts 为 **开盘时间**，按时间升序。所有取数（含 exchanges.py 各适配器）
    在进入指标计算前都必须先过 drop_open_bar。
"""

import math
import time

M1 = 60_000
M5 = 5 * M1
M15 = 15 * M1
H1 = 60 * M1
H4 = 4 * H1
D1 = 24 * H1
W1 = 7 * D1

INTERVAL_MS = {"1m": M1, "5m": M5, "15m": M15, "1h": H1, "4h": H4, "1d": D1, "1w": W1}


# 计价币，**最长优先**匹配（USDT 必须排在 USD 之前，否则 USDUSDT 会被切错）。
QUOTE_ASSETS = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "BTC", "ETH", "BNB", "USD")

# 纯计价币：单独出现时只有"计价单位"含义、不可能是标的本身。
# BTC/ETH/BNB 虽可作计价币（BTCUSDT 之外还有 ETHBTC），但它们本身是标的，
# 所以**不在此列**——把 BTC 判成"计价币而非标的"就挡住了最常见的一次输入。
PURE_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD")

# 合法基币：字母数字，1~15 位。用于拒绝明显非法的输入，而不是把它补成垃圾符号。
import re as _re
_BASE_RE = _re.compile(r"^[A-Z0-9]{1,15}$")
_SEP_RE = _re.compile(r"[\s/\\_-]+")


def normalize_symbol(raw):
    """规范化交易对符号 —— 全包唯一真源（v1.9.22）。

    现场取证（session-44da005c，2026-10-18）：
      kexi_run(mode=fast) 传裸符号 "BTC"，三条下游取数路径同时失败：
        CG_IDS.get("BTC")   → 无此键（键是 BTCUSDT）→ no CoinGecko id mapping
        Binance ?symbol=BTC → HTTP 400
        OKX instId=BTC      → 51001 Instrument ID does not exist
      实测 fetch_binance('BTC') 9.38 秒失败 / ('BTCUSDT') 0.61 秒成功；
      20 币 × ~6 秒 ≈ 120 秒，与会话 elapsed_ms=119636 精确吻合。
      同一会话内模型自行改用 'BTCUSDT' 后立刻成功——用户被迫规避缺陷的证据。

    设计约束（用户规则 1：禁降级兜底）：
      只做**一次确定性改写**并**回显**改写结果。绝不"猜一个看起来对的然后
      假装成功"——调用方必须看得见自己传的 BTC 变成了 BTCUSDT。

    返回 dict：{symbol, base, quote, changed, input, note, error}
      · 成功时 symbol 为非空字符串、error 为 None
      · 失败时 symbol 为 None、error 说明原因
    """
    input_ = "" if raw is None else str(raw)
    cleaned = _SEP_RE.sub("", input_.strip().upper())

    base_ret = {"symbol": None, "base": None, "quote": None,
                "changed": False, "input": input_, "note": None}

    if not cleaned:
        return dict(base_ret, error="符号为空")
    if not cleaned.isalnum() or not cleaned.isascii():
        return dict(base_ret, error="符号含非法字符：%s" % input_)

    # ① 已带计价后缀 → 只校验，不改写
    for q in QUOTE_ASSETS:
        if len(cleaned) > len(q) and cleaned.endswith(q):
            base = cleaned[: -len(q)]
            if not _BASE_RE.match(base):
                return dict(base_ret, error="符号基币部分非法：%s" % input_)
            changed = cleaned != input_
            return {"symbol": cleaned, "base": base, "quote": q,
                    "changed": changed, "input": input_,
                    "note": ("已规范化为 %s" % cleaned) if changed else None,
                    "error": None}

    # ①b 输入本身就是一个**纯计价币**（"USD"/"USDT"）→ 这是错误输入，不是缺后缀。
    #     早先的实现会把它补成 USDUSDT 这种根本不存在的交易对，然后发请求、
    #     白白等完 5 host × 3 retry。宁可立刻报错也不要制造垃圾符号。
    #     注意：BTC/ETH/BNB 不在 PURE_QUOTES 里——它们本身是标的，
    #     走 ② 分支补成 BTCUSDT 才是用户想要的结果。
    if cleaned in PURE_QUOTES:
        return dict(base_ret, error="%s 是计价币而非标的，请给出完整交易对（如 BTCUSDT）" % cleaned)

    # ② 未带计价后缀 → 补 USDT，并**明确报告改写**
    #    本函数不判断"这个币是否存在"——那是取数层的职责。这里只回答
    #    "要发给交易所的符号应该长什么样"。
    if not _BASE_RE.match(cleaned):
        return dict(base_ret, error="符号基币部分非法：%s" % input_)
    symbol = cleaned + "USDT"
    return {"symbol": symbol, "base": cleaned, "quote": "USDT",
            "changed": True, "input": input_,
            "note": "未带计价后缀，已补为 %s（原输入 %s）" % (symbol, input_),
            "error": None}


def normalize_symbols(raw):
    """批量规范化，保持顺序并去重。

    raw 可以是 list，或逗号分隔的字符串（模型与用户都会顺手写 "A,B"）。
    返回 dict：{symbols, notes, errors, changed}
    """
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    elif raw is None:
        items = []
    else:
        items = [raw]

    symbols, notes, errors = [], [], []
    for item in items:
        for part in str("" if item is None else item).split(","):
            t = part.strip()
            if not t:
                continue
            r = normalize_symbol(t)
            if r["error"]:
                errors.append(r["error"])
                continue
            if r["note"]:
                notes.append(r["note"])
            if r["symbol"] not in symbols:
                symbols.append(r["symbol"])
    return {"symbols": symbols, "notes": notes, "errors": errors,
            "changed": bool(notes)}


def detect_step_ms(klines):
    """由时间戳中位差推断真实周期（毫秒）；样本不足返回 None。"""
    if not klines or len(klines) < 3:
        return None
    ds = sorted(klines[i + 1][0] - klines[i][0] for i in range(len(klines) - 1))
    return ds[len(ds) // 2]


def humanize_ms(ms):
    if not ms:
        return None
    for name, v in INTERVAL_MS.items():
        if ms == v:
            return name
    if ms % D1 == 0:
        return f"{ms // D1}d"
    if ms % H1 == 0:
        return f"{ms // H1}h"
    return f"{ms}ms"


def drop_open_bar(klines, interval_ms=None):
    """剔除尚未走完的最后一根 bar。

    返回 (closed_klines, dropped_count)。interval_ms 为 None 时按数据自身推断。
    注意：各交易所（Binance/OKX/Gate/MEXC/Bitget/Coinbase）的日线 ts 均为
    开盘/桶起始时间，故 ts + 周期 <= now 即表示已收盘，规则通用。
    """
    if not klines:
        return klines, 0
    step = interval_ms or detect_step_ms(klines)
    if not step:
        return klines, 0
    now_ms = int(time.time() * 1000)
    closed = [k for k in klines if k[0] + step <= now_ms]
    return closed, len(klines) - len(closed)


def decimals_for(x):
    """按数量级决定小数位，避免小价格币被固定精度抹成 0。"""
    if x is None:
        return 2
    a = abs(x)
    if a >= 1000:
        return 2
    if a >= 1:
        return 4
    if a >= 0.01:
        return 5
    if a >= 0.0001:
        return 7
    if a >= 1e-6:
        return 9
    return 12


def rn(x):
    """自适应精度取整（替代固定 round(x, 2) / round(x, 4)）。"""
    if x is None:
        return None
    return round(x, decimals_for(x))


def nice_step(price):
    """整数心理关口步长：1/2/5×10^k 中不超过价格 5% 的最大者。

    修复原实现 10**(位数-2) 的错误——它让 DOGE 产出 1.0 USDT（+923%）、
    低价币产出 -1.0，只有 BTC 这类大价格标的才正确。
    """
    if price is None or price <= 0:
        return 1.0
    exp = math.floor(math.log10(price))
    target = price * 0.05
    best = None
    for k in range(exp - 3, exp + 2):
        for m in (1, 2, 5):
            s = m * 10 ** k
            if s <= target:
                best = s if best is None else max(best, s)
    return best if best else price * 0.01


def atr(klines, n=14):
    """Wilder ATR（绝对价格）。"""
    if len(klines) < n + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i][2], klines[i][3], klines[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def ann_vol(closes, n):
    """近 n 根日线年化波动率（%，加密 7×24 按 365 天）。"""
    if len(closes) < n + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(365) * 100


def max_dd(closes, n):
    """近 n 根最大回撤（%，负值）。"""
    if not closes:
        return None
    seg = closes[-n:] if len(closes) >= n else closes
    peak, mdd = seg[0], 0.0
    for c in seg:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    return mdd * 100
