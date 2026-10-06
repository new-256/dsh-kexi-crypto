#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
screener.py - Batch technical screener: pick coins with near-term (7-14d) bullish setups.

Strategy (fixed by design review 2026-09-27):
  Universe : Binance USDT pairs ranked by 24h quote volume, top --top (default 100),
             PLUS "surging newcomers": ranks top..--surge-rank-max (default 300) whose
             7d avg volume >= --surge-mult (default 2.0) x 30d avg volume.
             Excludes stablecoins / wrapped / leveraged tokens.
  Hard conditions (need >= --min-hits of 4):
    H1 trend   : close > MA20 and MA20 rising, OR MA5>MA10>MA20
    H2 momentum: MACD golden cross within last 5 bars, OR (DIF>0, DEA>0, hist rising 3 bars)
    H3 volume  : vol_ma5 >= 1.5 * vol_ma20
    H4 rsi     : 50 <= RSI14 <= 70
  Bonus: 20d-high breakout (close-confirmed, no retracement in 2 bars) +2;
         RSI bottom divergence +2; full bullish alignment (MA5>10>20>60) +1;
         4h resonance +1 (only for shortlist stage).
  Vetoes : 30d gain > 80%; RSI14 > 80; last 3 bars any bar with range >15% AND
           upper wick > 40% of range (blow-off wick).
  Gate   : if BTC itself is below MA20 with MACD below zero -> environment "unfavorable",
           overall confidence downgraded and flagged in output.
  Funnel : screen -> score top --shortlist (15) -> 4h resonance recheck -> final 10.
           Never pads the list: fewer than 10 is reported honestly.

Usage:
  python screener.py --out scan_result.json --md scan_report.md
  python screener.py --top 100 --min-hits 3 --final 10 --delay 0.15
Only stdlib is used.
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

import kline_utils as ku
import position
import timeframe

UA = {"User-Agent": "crypto-trend-analyst/1.0"}
TZ8 = timezone(timedelta(hours=8))
BASE = "https://api.binance.com"

STABLES = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT",
           "USDEUSDT", "USD1USDT", "EURUSDT", "AEURUSDT", "BFUSDUSDT",
           # v1.5.0 补漏：实测"低位横盘候选"里混进了这些——它们本来就是锚定币，
           # 区间位置天然在 40-50%、60 日振幅可低到 0.2%，会伪装成"完美横盘"。
           "RLUSDUSDT", "UUSDT", "USDSUSDT", "PYUSDUSDT", "USDYUSDT",
           "USDFUSDT", "XUSDUSDT", "USDGUSDT", "SUSDUSDT", "GUSDUSDT",
           "LUSDUSDT", "FRAXUSDT", "USDDUSDT", "USDJUSDT", "USTCUSDT"}
WRAPPED = {"WBTCUSDT", "WBETHUSDT", "WETHUSDT", "STETHUSDT", "BNSOLUSDT",
           "WSTETHUSDT", "RETHUSDT", "CBBTCUSDT", "SOLVBTCUSDT", "LSTBTCUSDT"}
# 贵金属/大宗商品代币：不是加密趋势标的，横盘是常态，会污染"低位横盘"候选。
COMMODITY = {"PAXGUSDT", "XAUTUSDT", "KAUUSDT", "XAGUSDT", "XAUMUSDT", "GOLDUSDT"}
LEVERAGED_SUFFIX = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
MIN_ADV_USD = 3_000_000
# 突破加分的量能门槛：突破根成交量须 ≥ 此前20根均量的该倍数，才认"放量突破"。
# 原实现只校验收盘站上/未回踩、完全不看量，导致无量突破也拿到"放量突破(+2)"。
BREAKOUT_VOL_MULT = 1.2
# 低位横盘候选的 60 日振幅上限（v1.5.0）：超过此值说明是高波动而非横盘，
# 不应进入"蓄势"候选（实测 TRUMPUSDT 位置 32.8% 但振幅 169.6%，属崩盘后阴跌）。
MAX_ACCUM_WIDTH = 35.0

# 赛道映射（base symbol -> 赛道）。未覆盖的归入"其他"，不参与补涨分析。
SECTORS = {
    # L1 公链
    "ETH": "L1公链", "SOL": "L1公链", "ADA": "L1公链", "AVAX": "L1公链",
    "DOT": "L1公链", "TRX": "L1公链", "NEAR": "L1公链", "SUI": "L1公链",
    "SEI": "L1公链", "APT": "L1公链", "TON": "L1公链", "ATOM": "L1公链",
    "ALGO": "L1公链", "VET": "L1公链", "HBAR": "L1公链", "EGLD": "L1公链",
    "FLOW": "L1公链", "ICP": "L1公链", "KAS": "L1公链", "TIA": "L1公链",
    "INJ": "L1公链", "MINA": "L1公链", "ROSE": "L1公链", "CELO": "L1公链",
    "ONE": "L1公链", "ZIL": "L1公链", "QTUM": "L1公链", "IOTA": "L1公链",
    "KAVA": "L1公链", "CFX": "L1公链", "XTZ": "L1公链", "CKB": "L1公链",
    "NEO": "L1公链", "EOS": "L1公链", "ASTR": "L1公链", "XPL": "L1公链",
    # L2 扩容
    "POL": "L2扩容", "ARB": "L2扩容", "OP": "L2扩容", "STRK": "L2扩容",
    "MANTA": "L2扩容", "METIS": "L2扩容", "IMX": "L2扩容", "ZK": "L2扩容",
    "LRC": "L2扩容", "LINEA": "L2扩容", "MNT": "L2扩容",
    # DeFi
    "UNI": "DeFi", "AAVE": "DeFi", "SKY": "DeFi", "MKR": "DeFi",
    "CRV": "DeFi", "SNX": "DeFi", "COMP": "DeFi", "PENDLE": "DeFi",
    "ENA": "DeFi", "DYDX": "DeFi", "GMX": "DeFi", "JUP": "DeFi",
    "RAY": "DeFi", "CAKE": "DeFi", "SUSHI": "DeFi", "1INCH": "DeFi",
    "RESOLV": "DeFi", "MORPHO": "DeFi", "FLUID": "DeFi", "DRIFT": "DeFi",
    "KMNO": "DeFi", "LQTY": "DeFi", "OSMO": "DeFi", "CVX": "DeFi",
    # Meme
    "DOGE": "Meme", "SHIB": "Meme", "PEPE": "Meme", "WIF": "Meme",
    "BONK": "Meme", "FLOKI": "Meme", "TRUMP": "Meme", "PENGU": "Meme",
    "FARTCOIN": "Meme", "MEME": "Meme", "BOME": "Meme", "DOGS": "Meme",
    "NEIRO": "Meme", "PNUT": "Meme", "ACT": "Meme", "MOODENG": "Meme",
    "PUMP": "Meme", "SPX": "Meme", "BRETT": "Meme", "POPCAT": "Meme",
    # AI
    "FET": "AI", "RENDER": "AI", "TAO": "AI", "WLD": "AI",
    "GRT": "AI", "VIRTUAL": "AI", "AIXBT": "AI", "ARKM": "AI",
    "OCEAN": "AI", "NMR": "AI", "CGPT": "AI", "AI16Z": "AI",
    # 游戏
    "XAI": "游戏", "GALA": "游戏", "AXS": "游戏", "SAND": "游戏",
    "MANA": "游戏", "ENJ": "游戏", "PIXEL": "游戏", "PORTAL": "游戏",
    "BIGTIME": "游戏", "YGG": "游戏", "GMT": "游戏", "BEAM": "游戏",
    "SUPER": "游戏", "ILV": "游戏",
    # 跨链
    "RUNE": "跨链", "W": "跨链", "ZRO": "跨链", "AXL": "跨链",
    "STG": "跨链", "SYN": "跨链", "CELR": "跨链",
    # DePIN/存储
    "HNT": "DePIN", "2Z": "DePIN", "IOTX": "DePIN", "AKT": "DePIN",
    "DIMO": "DePIN", "GRASS": "DePIN", "THETA": "DePIN", "FIL": "DePIN",
    "AR": "DePIN", "STORJ": "DePIN", "SC": "DePIN", "HONEY": "DePIN",
    # RWA
    "ONDO": "RWA", "POLYX": "RWA", "OM": "RWA", "CFG": "RWA", "GFI": "RWA",
    # 质押/再质押/BTCFi
    "JTO": "质押再质押", "LDO": "质押再质押", "RPL": "质押再质押",
    "ETHFI": "质押再质押", "EIGEN": "质押再质押", "SSV": "质押再质押",
    "BABY": "BTCFi", "CORE": "BTCFi", "STX": "BTCFi", "MERL": "BTCFi",
    "SOLV": "BTCFi", "PUFFER": "质押再质押",
    # 预言机
    "LINK": "预言机", "PYTH": "预言机", "BAND": "预言机", "API3": "预言机",
    "TRB": "预言机",
    # 支付/价值存储
    "BTC": "支付价值存储", "XRP": "支付价值存储", "XLM": "支付价值存储",
    "BCH": "支付价值存储", "LTC": "支付价值存储", "DASH": "支付价值存储",
    "ZEC": "支付价值存储", "QNT": "支付价值存储",
    # 平台币
    "BNB": "平台币", "OKB": "平台币", "BGB": "平台币", "KCS": "平台币",
    "GT": "平台币", "CRO": "平台币", "FTT": "平台币", "ASTER": "平台币",
    # 其他热点
    "HYPE": "DeFi", "PENGU": "Meme", "MASK": "社交", "CYBER": "社交",
}


def http_get(path, retries=2, timeout=10):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(BASE + path, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                json.JSONDecodeError, OSError) as e:
            last = e
            time.sleep(1.5 ** i)
    raise RuntimeError(f"{path} failed after {retries} retries: {last}")


# ---------- indicator helpers (same formulas as indicators.py) ----------

def sma(vals, n, end=None):
    v = vals if end is None else vals[:end]
    if len(v) < n:
        return None
    return sum(v[-n:]) / n


def macd_full(closes, fast=12, slow=26, signal=9):
    """Return (dif, dea, hist) series aligned to tail, or None."""
    if len(closes) < slow + signal:
        return None
    kf, ks = 2 / (fast + 1), 2 / (slow + 1)
    ef = sum(closes[:fast]) / fast
    es = sum(closes[:slow]) / slow
    difs = []
    for i, c in enumerate(closes):
        if i >= fast:
            ef = c * kf + ef * (1 - kf)
        if i >= slow:
            es = c * ks + es * (1 - ks)
        if i >= slow - 1:
            difs.append(ef - es)
    kd = 2 / (signal + 1)
    dea = sum(difs[:signal]) / signal
    deas = [dea]
    for d in difs[signal:]:
        dea = d * kd + dea * (1 - kd)
        deas.append(dea)
    difs = difs[len(difs) - len(deas):]
    hists = [(d - e) * 2 for d, e in zip(difs, deas)]
    return difs, deas, hists


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0)
        losses += max(-ch, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(ch, 0)) / n
        al = (al * (n - 1) + max(-ch, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def golden_cross_within(difs, deas, bars=5):
    """DIF crossed above DEA within the last `bars` bars."""
    n = len(difs)
    for i in range(max(1, n - bars), n):
        if difs[i - 1] <= deas[i - 1] and difs[i] > deas[i]:
            return True
    return False


def rsi_bottom_divergence(klines, closes, lookback=60):
    """Simple RSI bottom divergence: price lower low, RSI higher low (two 30-bar halves)."""
    if len(closes) < lookback + 14:
        return False
    half = lookback // 2
    seg_close = closes[-lookback:]
    p1 = min(seg_close[:half])
    p2 = min(seg_close[half:])
    if p2 >= p1 * 0.995:  # require a real lower low (0.5% tolerance)
        return False
    # RSI at the two lows (approximate with truncated series)
    i1 = len(closes) - lookback + seg_close[:half].index(p1)
    i2 = len(closes) - half + seg_close[half:].index(p2)
    r1 = rsi(closes[:i1 + 1])
    r2 = rsi(closes[:i2 + 1])
    return r1 is not None and r2 is not None and r2 > r1 + 2


# ---------- scoring ----------

def score_symbol(klines, vol_mult, surge_mult=None, min_adv_usd=MIN_ADV_USD,
                 position_weight=True, hard_pos=None, hard_stretch=None,
                 multi_tf=True):
    """Score one symbol's daily klines. Returns dict or None (data insufficient).

    v1.5.0：新增**位置 / 拥挤度**维度（position.py）。此前打分全是趋势/动量类信号，
    "技术指标好但已放量多日涨到高位"的币天然占优（用户实测反馈 + 20 币会话验证：
    入选 25 币 90 日区间位置中位 92.9%、16/20 在高位区、0 个横盘）。
    现在位置度量既进 `vetoes`（可选硬否决，默认关）也进 `score`（默认扣分）。
    """
    if len(klines) < 60:
        return None
    closes = [k[4] for k in klines]
    vols = [k[5] for k in klines]
    c = closes[-1]
    adv20_quote = round(sum(k[4] * k[5] for k in klines[-20:]) / 20) if len(klines) >= 20 else None

    ma5, ma10, ma20, ma60 = sma(closes, 5), sma(closes, 10), sma(closes, 20), sma(closes, 60)
    ma20_prev = sma(closes, 20, end=len(closes) - 5)
    vol_ma5, vol_ma20 = sma(vols, 5), sma(vols, 20)
    r14 = rsi(closes, 14)
    m = macd_full(closes)
    if r14 is None or m is None or vol_ma5 is None or vol_ma20 is None or ma20 is None:
        return None
    difs, deas, hists = m

    hits, reasons = 0, []

    # H1 trend
    ma20_rising = ma20_prev is not None and ma20 > ma20_prev
    early_bull = ma5 and ma10 and ma5 > ma10 > ma20
    if (c > ma20 and ma20_rising) or early_bull:
        hits += 1
        reasons.append("H1趋势: 站上MA20且拐头向上" if (c > ma20 and ma20_rising)
                       else "H1趋势: MA5>MA10>MA20多头初期")

    # H2 momentum
    gc = golden_cross_within(difs, deas, 5)
    zero_up = difs[-1] > 0 and deas[-1] > 0 and len(hists) >= 3 and \
        hists[-1] > hists[-2] > 0 and hists[-2] >= hists[-3]
    if gc or zero_up:
        hits += 1
        reasons.append("H2动量: MACD近5根金叉" if gc else "H2动量: DIF/DEA零上且柱体放大")

    # H3 volume
    vol_ratio = vol_ma5 / vol_ma20 if vol_ma20 else 0
    if vol_ratio >= vol_mult:
        hits += 1
        reasons.append(f"H3量能: 5日均量/20日均量={vol_ratio:.2f}")

    # H4 rsi
    if 50 <= r14 <= 70:
        hits += 1
        reasons.append(f"H4强度: RSI14={r14:.1f}处于50-70强势区")

    # ---- vetoes ----
    vetoes = []
    limit_adv = min_adv_usd if min_adv_usd is not None else MIN_ADV_USD
    if adv20_quote is not None and adv20_quote < limit_adv:
        vetoes.append(f"流动性不足: 20日均成交额 {adv20_quote/1e4:.0f}万U (<{int(limit_adv // 1e6)}M)")
    gain_30d = (c / closes[-31] - 1) * 100 if len(closes) > 31 else 0
    if gain_30d > 80:
        vetoes.append(f"近30日已涨{gain_30d:.0f}%(>80%)")
    if r14 > 80:
        vetoes.append(f"RSI14={r14:.1f}>80过热")
    for k in klines[-3:]:
        o, h, l, cc = k[1], k[2], k[3], k[4]
        rng = h - l
        if l > 0 and rng / l > 0.15 and rng > 0 and (h - max(o, cc)) / rng > 0.4:
            vetoes.append("近3日出现插针长上影(振幅>15%)")
            break

    # ---- 位置 / 拥挤度（v1.5.0）----
    # 直接回应用户痛点：不再只看"趋势好不好"，同时看"现在在什么位置"。
    # 默认只扣分（不硬否决）——高位不等于必跌，硬否决会错杀主升浪；
    # 需要更严格时用 hard_pos / hard_stretch 显式开启（供回测对比）。
    pos = position.analyze_position(klines)
    pos_penalty, pos_reasons = (0, [])
    if pos and position_weight:
        pos_penalty, pos_reasons = position.position_penalty(pos)
        vetoes.extend(position.position_vetoes(pos, hard_pos=hard_pos,
                                              hard_stretch=hard_stretch))

    # ---- 多周期（周线/月线）大周期研判（v1.5.0）----
    # 只做位置扣分还不够：实测发现"日线漂亮"常是**大周期下跌里的反弹**
    # （ALGO/SEI/BTC 日线多头排列，但周线 MA20<MA50、月线价格在 MA20 下方）。
    # 该维度与位置独立：位置看"涨到哪了"，多周期看"大方向支不支持"。
    tf_view, tf_score, tf_reasons = None, 0, []
    if multi_tf and len(klines) >= 200:
        tf_view = timeframe.multi_tf_view(klines)
        tf_score, tf_reasons = timeframe.tf_alignment_score(tf_view)

    # ---- bonus ----
    bonus, bonus_reasons = 0, []
    prior20_high = max(k[2] for k in klines[-21:-1])
    broke_idx = None
    for i in range(len(klines) - 3, len(klines)):
        if klines[i][4] > prior20_high:
            broke_idx = i
    if broke_idx is not None:
        held = all(klines[j][4] > prior20_high for j in range(broke_idx, len(klines)))
        if held:
            # 必须确认量能：突破根成交量 ≥ 此前20根均量 × BREAKOUT_VOL_MULT，
            # 否则只是无量价穿、不构成"放量突破"，不给该加分（修"名不副实"bug）。
            prior20_vol = sum(vols[broke_idx-20:broke_idx]) / 20 if broke_idx >= 20 else 0
            broke_vol_ratio = vols[broke_idx] / prior20_vol if prior20_vol else 0
            if broke_vol_ratio >= BREAKOUT_VOL_MULT:
                bonus += 2
                bonus_reasons.append(f"放量突破近20日高点(突破根量比{broke_vol_ratio:.2f})且2根内未回踩(+2)")
            else:
                bonus_reasons.append(f"价穿近20日高点但突破根无量(量比{broke_vol_ratio:.2f}<{BREAKOUT_VOL_MULT})，不加分")
    if rsi_bottom_divergence(klines, closes):
        bonus += 2
        bonus_reasons.append("RSI底背离(+2)")
    if ma60 and ma5 and ma10 and ma5 > ma10 > ma20 > ma60:
        bonus += 1
        bonus_reasons.append("完全多头排列(+1)")

    surge_ratio = None
    if len(vols) >= 37:
        v7 = sum(vols[-7:]) / 7
        v30 = sum(vols[-37:-7]) / 30
        surge_ratio = v7 / v30 if v30 else None

    gain_7d = (c / closes[-8] - 1) * 100 if len(closes) > 8 else 0
    prior20_high = max(k[2] for k in klines[-21:-1])

    # 位置扣分 + 多周期加减分并入总分
    base_score = hits * 2 + bonus
    total_score = base_score + pos_penalty + tf_score

    return {
        "close": c, "hits": hits, "reasons": reasons,
        "bonus": bonus, "bonus_reasons": bonus_reasons,
        "vetoes": vetoes, "adv20_quote": adv20_quote, "rsi14": round(r14, 1),
        "vol_ratio": round(vol_ratio, 2),
        "gain_30d": round(gain_30d, 1),
        "gain_7d": round(gain_7d, 1),
        "above_ma20": bool(ma20 and c > ma20),
        "near_ma20_pct": round(abs(c / ma20 - 1) * 100, 2) if ma20 else None,
        "hist_rising": bool(len(hists) > 1 and hists[-1] > hists[-2]),
        "trigger_20d_high": ku.rn(prior20_high),
        "surge_ratio": round(surge_ratio, 2) if surge_ratio else None,
        "ma20": ma20, "ma60": ma60,
        "dif": difs[-1], "dea": deas[-1],
        # v1.5.0：位置维度（把原 score 拆成 base/penalty 便于回测与归因）
        "position": pos,
        "position_penalty": pos_penalty,
        "position_reasons": pos_reasons,
        # v1.5.0：多周期（周/月）大周期研判
        "multi_tf": tf_view,
        "tf_score": tf_score,
        "tf_reasons": tf_reasons,
        "base_score": base_score,
        "score": total_score,
        "invalidation": ku.rn(min(ma20, max(k[3] for k in klines[-10:]))),
        "last_date": datetime.fromtimestamp(klines[-1][0] / 1000, TZ8).strftime("%Y-%m-%d"),
    }


def resonance_4h(symbol, delay):
    """4h confirmation: MA20 rising or MACD bullish on 4h. Returns (bool, note)."""
    time.sleep(delay)
    try:
        raw = http_get(f"/api/v3/klines?symbol={symbol}&interval=4h&limit=60")
    except Exception as e:
        return None, f"4h数据获取失败: {e}"
    closes = [float(k[4]) for k in raw]
    if len(closes) < 40:
        return None, "4h样本不足"
    ma20 = sma(closes, 20)
    ma20_prev = sma(closes, 20, end=len(closes) - 3)
    m = macd_full(closes)
    trend_ok = ma20 and ma20_prev and (closes[-1] > ma20 and ma20 > ma20_prev)
    macd_ok = m and (golden_cross_within(m[0], m[1], 3) or (m[0][-1] > m[1][-1] > 0))
    if trend_ok and macd_ok:
        return True, "4h趋势+动量双共振"
    if trend_ok or macd_ok:
        return True, "4h部分共振(" + ("趋势" if trend_ok else "动量") + ")"
    return False, "4h未共振"


def sector_of(symbol):
    return SECTORS.get(symbol.replace("USDT", ""), "其他")


def sector_laggard_analysis(results, picked_symbols, top_sectors_n=3, max_per_sector=3, max_total=8):
    """赛道强度评估 + 补涨候选筛选。

    赛道强度 = 成员7日涨幅中位数 + 站上MA20占比*10 + 平均量比（越高越强）。
    补涨候选：强势赛道内、未入选主名单、无一票否决、滞涨但结构成型(>=2条)：
      贴近MA20(<=5%) / 量比>=1.2 / RSI14在45-62 / MACD柱体回升
    """
    sectors = {}
    for s, sc in results.items():
        if sc is None:
            continue
        sec = sector_of(s)
        if sec == "其他":
            continue
        sectors.setdefault(sec, []).append((s, sc))

    stats = []
    for sec, members in sectors.items():
        if len(members) < 3:
            continue
        gains = sorted(m[1]["gain_7d"] for m in members)
        med_gain = gains[len(gains) // 2]
        pct_above = sum(1 for _, m in members if m["above_ma20"]) / len(members)
        avg_vol = sum(m["vol_ratio"] for _, m in members) / len(members)
        strength = med_gain + pct_above * 10 + avg_vol
        stats.append({
            "sector": sec, "members": len(members),
            "median_gain_7d": round(med_gain, 1),
            "pct_above_ma20": round(pct_above * 100),
            "avg_vol_ratio": round(avg_vol, 2),
            "strength": round(strength, 2),
        })
    stats.sort(key=lambda x: -x["strength"])
    top_sectors = [x["sector"] for x in stats[:top_sectors_n]]

    laggards = []
    for sec in top_sectors:
        med = next(x["median_gain_7d"] for x in stats if x["sector"] == sec)
        cap = max(med, 12)
        cands = []
        for s, sc in sectors[sec]:
            if s in picked_symbols or sc["vetoes"]:
                continue
            g = sc["gain_7d"]
            if not (-10 < g <= cap):
                continue
            structure, hits_desc = 0, []
            if sc["near_ma20_pct"] is not None and sc["near_ma20_pct"] <= 5:
                structure += 1
                hits_desc.append(f"贴近MA20({sc['near_ma20_pct']}%)")
            if sc["vol_ratio"] >= 1.2:
                structure += 1
                hits_desc.append(f"量比{sc['vol_ratio']}")
            if 45 <= sc["rsi14"] <= 62:
                structure += 1
                hits_desc.append(f"RSI{sc['rsi14']}蓄势区")
            if sc["hist_rising"]:
                structure += 1
                hits_desc.append("MACD柱回升")
            if structure < 2:
                continue
            cands.append({
                "symbol": s, "sector": sec, "gain_7d": g,
                "structure_points": structure, "structure_desc": hits_desc,
                "close": sc["close"], "rsi14": sc["rsi14"],
                "vol_ratio": sc["vol_ratio"],
                "trigger": sc["trigger_20d_high"],
                "invalidation": sc["invalidation"],
                "last_date": sc["last_date"],
            })
        cands.sort(key=lambda x: (-x["structure_points"], -x["vol_ratio"]))
        laggards.extend(cands[:max_per_sector])
    laggards = laggards[:max_total]
    return {"sector_strength": stats, "top_sectors": top_sectors, "laggards": laggards}


def _median(xs):
    """中位数；空列表返回 None。"""
    vals = sorted(x for x in xs if x is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return round(vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2, 1)


def accumulation_candidates(results, exclude, max_total=6):
    """低位横盘 / 震荡调整候选（v1.5.0 新增）。

    用户原话："低位横盘的、震荡调整的有没有考虑"——此前**完全没有**。原策略
    要求 H1 站上MA20 且 MA20 拐头向上，**横盘币天然不满足**（价格贴着均线、
    均线走平），因此系统性地只会选出"已经在涨"的币。

    本函数走**独立通道**，不要求趋势信号，而是找：
      · 仍在低位/中低位（区间位置 < 55）—— 没被炒过
      · 近期窄幅横盘（range_width_60 窄 或 base_days 较长）或量能萎缩
      · 流动性达标、无一票否决
      · 给出"突破触发位"（区间上沿）与"失效位"（区间下沿），供挂单参考

    返回候选列表（不含主名单成员）。设计上**只提供候选、不直接入选**——
    由主理人/成员按用户意图决定是否纳入（"宁缺毋滥"原则不变）。
    """
    out = []
    for s, sc in results.items():
        if sc is None or s in exclude or sc["vetoes"]:
            continue
        pos = sc.get("position")
        if not pos:
            continue
        p90 = pos.get("pos_90")
        if p90 is None or p90 >= 55:
            continue
        width = pos.get("range_width_60")
        vs = pos.get("vol_shrink_ratio")
        base_days = pos.get("base_days") or 0
        # 硬性要求：区间必须真的**相对窄**，否则不是横盘而是高波动下跌/暴涨。
        # 实测教训：TRUMPUSDT 位置 32.8%（低位）但 60 日振幅 169.6%——
        # 只看"缩量"会把它当成横盘候选，实际是崩盘后阴跌，完全不是横盘蓄势。
        if width is None or width > MAX_ACCUM_WIDTH:
            continue
        tight = width <= 25.0
        shrinking = (vs is not None and vs <= 1.0)
        # 合格条件：窄幅横盘，或已维持较长横盘；缩量只作加分，不单独构成资格
        if not (tight or base_days >= 30):
            continue
        # 结构分：越低位 + 越窄 + 越缩量 越优先
        struct = 0
        desc = []
        if tight:
            struct += 2
            desc.append(f"60日窄幅{width:.1f}%")
        if base_days >= 30:
            struct += 1
            desc.append(f"横盘约{base_days}天")
        if shrinking:
            struct += 1
            desc.append(f"缩量{vs:.2f}")
        if p90 < 35:
            struct += 2
            desc.append(f"低位{p90:.0f}%")
        elif p90 < 55:
            struct += 1
            desc.append(f"中低位{p90:.0f}%")
        out.append({
            "symbol": s,
            "pos_90": p90, "zone": pos.get("zone"),
            "range_width_60": width, "base_days": base_days,
            "vol_shrink_ratio": vs, "dist_ma20_pct": pos.get("dist_ma20_pct"),
            "structure_points": struct, "structure_desc": desc,
            "close": sc["close"], "rsi14": sc["rsi14"], "vol_ratio": sc["vol_ratio"],
            "adv20_quote": sc.get("adv20_quote"),
            # 区间上沿=突破触发位；区间下沿=失效位（供挂单/止损参考）
            "breakout_trigger": pos.get("hi_90"),
            "invalidation": pos.get("lo_90"),
            "last_date": sc["last_date"],
        })
    out.sort(key=lambda x: (-x["structure_points"], x["pos_90"] or 100))
    return out[:max_total]


def btc_environment():
    """Market gate: BTC below MA20 AND MACD below zero -> unfavorable."""
    try:
        raw = http_get("/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=60")
    except Exception as e:
        return {"status": "unknown", "note": f"BTC数据获取失败: {e}"}
    closes = [float(k[4]) for k in raw]
    ma20 = sma(closes, 20)
    m = macd_full(closes)
    below = ma20 and closes[-1] < ma20
    macd_below = m and m[0][-1] < 0 and m[1][-1] < 0
    if below and macd_below:
        return {"status": "unfavorable",
                "note": f"BTC位于MA20({ma20:.0f})下方且MACD零下，全市场假突破率升高，整体置信度降一级"}
    return {"status": "neutral", "note": "BTC未触发环境警报"}


def build_universe(top, surge_rank_max, delay):
    tickers = http_get("/api/v3/ticker/24hr")
    rows = []
    for t in tickers:
        s = t["symbol"]
        if not s.isascii():  # e.g. Chinese-named meme tokens break URL encoding
            continue
        if not s.endswith("USDT") or s in STABLES or s in WRAPPED or s in COMMODITY:
            continue
        if s.endswith(LEVERAGED_SUFFIX):
            continue
        try:
            rows.append((s, float(t["quoteVolume"]), float(t["priceChangePercent"])))
        except (KeyError, ValueError):
            continue
    rows.sort(key=lambda x: -x[1])
    core = [s for s, _, _ in rows[:top]]
    surge_candidates = [s for s, _, _ in rows[top:surge_rank_max]]
    return core, surge_candidates, {s: v for s, v, _ in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--surge-rank-max", type=int, default=300)
    ap.add_argument("--surge-mult", type=float, default=2.0)
    ap.add_argument("--min-hits", type=int, default=3)
    ap.add_argument("--vol-mult", type=float, default=1.5)
    ap.add_argument("--min-adv-usd", type=float, default=3000000)
    ap.add_argument("--shortlist", type=int, default=15)
    ap.add_argument("--final", type=int, default=10)
    ap.add_argument("--limit", type=int, default=400,
                    help="日线根数（v1.5.0 由 120 提到 400：月线需 ≥35 根才有 MACD，"
                         "400 根日线≈13 个月≈56 根月线；同时让 180 日新高/长窗口位置可信）")
    ap.add_argument("--delay", type=float, default=0.15)
    ap.add_argument("--workers", type=int, default=6,
                    help="并发取数线程数（默认 6；1 = 旧的严格串行口径，便于复现历史结果）")
    ap.add_argument("--out", default="scan_result.json")
    ap.add_argument("--md", default="scan_report.md")
    # ── v1.5.0 位置维度开关（供回测对比不同口径）────────────────────────
    ap.add_argument("--no-position-weight", action="store_true",
                    help="关闭位置扣分（回到 v1.4.x 的纯趋势口径，便于回测对比）")
    ap.add_argument("--hard-pos", type=float, default=None,
                    help="区间位置 ≥ 此值时硬性否决（默认关；如 95）")
    ap.add_argument("--hard-stretch", type=float, default=None,
                    help="高于MA20 ≥ 此值(%%)时硬性否决（默认关；如 40）")
    ap.add_argument("--accum-max", type=int, default=6,
                    help="低位横盘/震荡候选上限（默认 6）")
    ap.add_argument("--no-multi-tf", action="store_true",
                    help="关闭周线/月线大周期加减分（周线月线仍需 ≥200 根日线才生效）")
    args = ap.parse_args()

    log = lambda m: print(m, flush=True)
    t0 = time.time()
    log("[1/4] 构建标的池 ...")
    core, surge_syms, vol_map = build_universe(args.top, args.surge_rank_max, args.delay)
    log(f"      核心池 {len(core)} 个 + 新势币候选 {len(surge_syms)} 个")

    env = btc_environment()
    log(f"[2/4] 大盘环境闸门: {env['status']} — {env['note']}")

    def fetch_daily(symbol):
        # 多取 1 根：剔除未收盘的当根后仍保证 args.limit 根已收盘日线（口径统一）
        raw = http_get(f"/api/v3/klines?symbol={symbol}&interval=1d&limit={args.limit + 1}")
        kl = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
              for k in raw]
        closed, _ = ku.drop_open_bar(kl, ku.D1)
        return closed

    log("[3/4] 批量取数与打分 ...")
    results, errors = {}, {}

    # ── 并发取数（v1.2.0）────────────────────────────────────────────────
    # 原实现是逐币串行：每币 time.sleep(delay) + 一次网络往返，100 核心池 +
    # 最多 200 新势候选 = 数分钟纯等待（用户实感「卡住很久不知道在干什么」）。
    # 改为线程池并发：网络等待重叠，整体耗时近似 = 最慢单币。用 stdlib
    # ThreadPoolExecutor（零依赖），并发数保守默认 6 以免触发交易所限频。
    # --workers 1 可退回旧的严格串行行为（完全可复现的历史口径）。
    def fetch_many(symbols, label, need_score=True):
        """并发取数；返回 {symbol: (klines, score)}，失败进 errors。"""
        out = {}
        if not symbols:
            return out
        if args.workers <= 1:
            for i, s in enumerate(symbols):
                time.sleep(args.delay)
                try:
                    kl = fetch_daily(s)
                    out[s] = (kl, score_symbol(
                        kl, args.vol_mult, min_adv_usd=args.min_adv_usd,
                        position_weight=not args.no_position_weight,
                        hard_pos=args.hard_pos, hard_stretch=args.hard_stretch,
                        multi_tf=not args.no_multi_tf,
                    ) if need_score else None)
                except Exception as e:
                    errors[s] = str(e)
                    log(f"      [skip] {s}: {e}")
                if (i + 1) % 10 == 0:
                    log(f"      {label} {i + 1}/{len(symbols)}（失败 {len(errors)}）...")
            return out
        from concurrent.futures import ThreadPoolExecutor, as_completed
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(fetch_daily, s): s for s in symbols}
            for fut in as_completed(futs):
                s = futs[fut]
                try:
                    kl = fut.result()
                    out[s] = (kl, score_symbol(
                        kl, args.vol_mult, min_adv_usd=args.min_adv_usd,
                        position_weight=not args.no_position_weight,
                        hard_pos=args.hard_pos, hard_stretch=args.hard_stretch,
                        multi_tf=not args.no_multi_tf,
                    ) if need_score else None)
                except Exception as e:
                    errors[s] = str(e)
                    log(f"      [skip] {s}: {e}")
                done += 1
                if done % 10 == 0:
                    log(f"      {label} {done}/{len(symbols)}（失败 {len(errors)}）...")
        return out

    # surge pool pre-filter by volume ratio
    surge_in = []
    surge_fetched = fetch_many(surge_syms, "新势币预筛")
    for s in surge_syms:
        if s not in surge_fetched:
            continue
        kl, sc = surge_fetched[s]
        if sc and sc["surge_ratio"] and sc["surge_ratio"] >= args.surge_mult:
            surge_in.append((s, kl, sc))
    log(f"      成交额突增新势币入围 {len(surge_in)} 个")

    pool = list(core) + [s for s, _, _ in surge_in]
    pre_scored = {s: sc for s, _, sc in surge_in}
    # 只对尚未打分的核心池成员取数（新势入围者已在上一步算过）
    todo = [s for s in pool if s not in pre_scored]
    fetched = fetch_many(todo, "主池扫描")
    for s in pool:
        if s in pre_scored:
            results[s] = pre_scored[s]
        elif s in fetched:
            kl, sc = fetched[s]
            results[s] = sc

    # funnel stage 1+2
    passed = []
    for s, sc in results.items():
        if sc is None or sc["vetoes"] or sc["hits"] < args.min_hits:
            continue
        passed.append((s, sc))
    passed.sort(key=lambda x: (-x[1]["score"], -vol_map.get(x[0], 0)))
    shortlist = passed[:args.shortlist]
    log(f"      初筛通过 {len(passed)} 个，进入 4h 复核 {len(shortlist)} 个")

    # funnel stage 3: 4h resonance
    log("[4/4] 4h 共振复核 ...")
    final = []
    for s, sc in shortlist:
        ok, note = resonance_4h(s, args.delay)
        sc["resonance_4h"] = ok
        sc["resonance_note"] = note
        if ok:
            sc["bonus"] += 1
            sc["bonus_reasons"].append("4h共振(+1)")
            sc["score"] += 1
        final.append((s, sc))
    resonant = [x for x in final if x[1]["resonance_4h"]]
    resonant.sort(key=lambda x: (-x[1]["score"], -vol_map.get(x[0], 0)))
    picked = resonant[:args.final]

    # 赛道强度 + 补涨候选（基于全量扫描结果，零额外请求）
    log("[+] 赛道强度评估与补涨候选筛选 ...")
    picked_symbols = {s for s, _ in picked}
    sector_info = sector_laggard_analysis(results, picked_symbols)
    log(f"      强势赛道 Top{len(sector_info['top_sectors'])}: "
        + ", ".join(sector_info["top_sectors"])
        + f"；补涨候选 {len(sector_info['laggards'])} 个")

    # 低位横盘 / 震荡调整候选（v1.5.0）：独立通道，不要求趋势信号
    accum = accumulation_candidates(results, picked_symbols, max_total=args.accum_max)
    log(f"      低位横盘/震荡候选 {len(accum)} 个")

    # 位置画像统计（供报告与成员判断"本次选币整体在什么位置"）
    pos_zone = {}
    for s, sc in picked:
        z = ((sc.get("position") or {}).get("zone")) or "未知"
        pos_zone[z] = pos_zone.get(z, 0) + 1
    pos_stats = {
        "zone_counts": pos_zone,
        "median_pos_90": _median([(sc.get("position") or {}).get("pos_90") for _, sc in picked
                                  if (sc.get("position") or {}).get("pos_90") is not None]),
        "penalized_count": sum(1 for _, sc in picked if (sc.get("position_penalty") or 0) < 0),
        "stretched_count": sum(1 for _, sc in picked if (sc.get("position") or {}).get("stretched")),
        "crowded_count": sum(1 for _, sc in picked if (sc.get("position") or {}).get("crowded")),
        "consolidating_in_pool": sum(
            1 for sc in results.values()
            if sc and (sc.get("position") or {}).get("consolidating")),
    }
    log(f"      位置画像: {pos_zone} 中位区间位置={pos_stats['median_pos_90']}% "
        f"（扣分 {pos_stats['penalized_count']} / 拉伸 {pos_stats['stretched_count']} / 放量多日 {pos_stats['crowded_count']}）")

    # 多周期画像（v1.5.0）：本次入选的币，大周期到底支不支持
    tf_align = {}
    tf_risk = []
    for s, sc in picked:
        tfv = sc.get("multi_tf")
        if not tfv:
            tf_align["样本不足"] = tf_align.get("样本不足", 0) + 1
            continue
        a = tfv.get("alignment") or "未知"
        tf_align[a] = tf_align.get(a, 0) + 1
        if tfv.get("risk_note"):
            tf_risk.append({"symbol": s, "note": tfv["risk_note"]})
    tf_stats = {
        "alignment_counts": tf_align,
        "tf_penalized_count": sum(1 for _, sc in picked if (sc.get("tf_score") or 0) < 0),
        "tf_boosted_count": sum(1 for _, sc in picked if (sc.get("tf_score") or 0) > 0),
        "risk_notes": tf_risk,
        "enabled": not args.no_multi_tf,
        "limit_bars": args.limit,
    }
    log(f"      多周期画像: {tf_align}（扣分 {tf_stats['tf_penalized_count']} / "
        f"逆大势风险 {len(tf_risk)} 个）")
    for _r in tf_risk[:3]:
        log(f"        · {_r['symbol']}: {_r['note']}")

    confidence = "中" if env["status"] == "unfavorable" else "中高"
    output = {
        "generated_at": datetime.now(TZ8).isoformat(),
        "elapsed_sec": round(time.time() - t0, 1),
        "params": vars(args),
        "environment": env,
        "overall_confidence": confidence,
        "universe_size": len(pool),
        "passed_screen": len(passed),
        "shortlisted": len(shortlist),
        "resonant_count": len(resonant),
        "errors": errors,
        "picked": [{
            "symbol": s, "score": sc["score"], "hits": sc["hits"],
            "close": sc["close"], "rsi14": sc["rsi14"],
            "vol_ratio": sc["vol_ratio"], "gain_30d": sc["gain_30d"],
            "surge_ratio": sc["surge_ratio"], "adv20_quote": sc.get("adv20_quote"),
            "reasons": sc["reasons"], "bonus_reasons": sc["bonus_reasons"],
            "resonance_note": sc["resonance_note"],
            "invalidation": sc["invalidation"], "last_date": sc["last_date"],
            # v1.5.0 位置维度：把"在什么位置"与"趋势好不好"并列输出
            "position": sc.get("position"),
            "position_penalty": sc.get("position_penalty"),
            "position_reasons": sc.get("position_reasons"),
            # v1.5.0 多周期（周线/月线）大周期研判
            "multi_tf": sc.get("multi_tf"),
            "tf_score": sc.get("tf_score"),
            "tf_reasons": sc.get("tf_reasons"),
            "base_score": sc.get("base_score"),
        } for s, sc in picked],
        "shortlist_detail": [{
            "symbol": s, "score": sc["score"], "hits": sc["hits"],
            "resonance_4h": sc["resonance_4h"], "reasons": sc["reasons"],
        } for s, sc in final],
        "sector_analysis": sector_info,
        # v1.5.0：整体位置画像 + 低位横盘/震荡候选 + 多周期
        "position_profile": pos_stats,
        "accumulation_candidates": accum,
        "timeframe_profile": tf_stats,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # markdown report
    L = []
    L.append(f"# 技术筛选扫描报告（{output['generated_at'][:16]} UTC+8）\n")
    L.append(f"- 标的池：{len(pool)} 个（Top {args.top} + 成交额突增新势币 {len(surge_in)} 个）")
    L.append(f"- 大盘环境：{env['status']} — {env['note']}")
    L.append(f"- 整体置信度：{confidence}")
    L.append(f"- 漏斗：初筛通过 {len(passed)} → 打分前 {len(shortlist)} → 4h 共振 {len(resonant)} → 最终 {len(picked)}\n")
    if len(picked) < args.final:
        L.append(f"> ⚠️ 宁缺毋滥：仅 {len(picked)} 个通过全部关卡，不足 {args.final} 个不硬凑。\n")
    L.append("| # | 币种 | 总分 | 硬条件 | RSI14 | 量比 | 30日涨幅 | **90日位置** | **位置扣分** | **多周期** | 失效位 | 4h复核 |")
    L.append("|---|------|------|--------|-------|------|----------|-------------|-------------|-----------|--------|--------|")
    for i, p in enumerate(output["picked"], 1):
        _pos = p.get("position") or {}
        _p90 = _pos.get("pos_90")
        _tf = p.get("multi_tf") or {}
        _tal = _tf.get("alignment") or "样本不足"
        L.append(f"| {i} | {p['symbol']} | {p['score']} | {p['hits']}/4 | "
                 f"{p['rsi14']} | {p['vol_ratio']} | {p['gain_30d']}% | "
                 f"{_p90 if _p90 is not None else '-'}%（{_pos.get('zone', '-')}） | "
                 f"{p.get('position_penalty') or 0} | "
                 f"{_tal}（{p.get('tf_score') or 0}） | "
                 f"{p['invalidation']} | {p['resonance_note']} |")

    # 多周期大周期研判（v1.5.0）：回答"是不是应该结合周线月线去看"
    _tp = output.get("timeframe_profile") or {}
    _risk_notes = _tp.get("risk_notes") or []
    L.append("\n## 大周期研判（周线/月线，v1.5.0 新增）\n")
    L.append(f"- 样本深度：每币取 **{_tp.get('limit_bars')}** 根日线"
             f"（周线/月线由日线本地重采样，最后一根未走完的周/月线已剔除）")
    L.append(f"- 周期一致性分布：{_tp.get('alignment_counts')}")
    L.append(f"- 因大周期被扣分：**{_tp.get('tf_penalized_count', 0)}** 个；"
             f"全周期共振向上加分：{_tp.get('tf_boosted_count', 0)} 个\n")
    L.append("> **为什么要看大周期**：实测发现「日线很漂亮」经常只是"
             "**大周期下跌通道里的一次反弹**。例如 ALGO/SEI 在 90 日区间位置 93%+"
             "（看似高位），但放到 2.7 年维度仅 1.8%/3.7%（近乎历史最低），"
             "且周线 MA20 < MA50、月线价格在 MA20 下方——纯日线视角无法分辨这两者。\n")
    if _risk_notes:
        L.append("### ⚠️ 逆大周期反弹风险清单\n")
        L.append("| 币种 | 风险说明 |")
        L.append("|------|---------|")
        for r in _risk_notes:
            L.append(f"| {r['symbol']} | {r['note']} |")
        L.append("\n> 这些标的**日线信号成立、但大周期方向相反**。"
                 "处理原则：降低仓位、缩短持有周期、严格执行失效位止损，"
                 "或直接用下方「低位横盘候选」替代。\n")
    else:
        L.append("> 本次入选未发现「日线多头但周/月线空头」的逆大势标的。\n")

    # 逐币三周期明细
    _with_tf = [p for p in output["picked"] if p.get("multi_tf")]
    if _with_tf:
        L.append("### 逐币三周期明细\n")
        L.append("| 币种 | 日线 | 周线 | 月线 | 一致性 |")
        L.append("|------|------|------|------|--------|")
        for p in _with_tf:
            t = p["multi_tf"]
            def _fmt(x):
                if not x or x.get("insufficient"):
                    return "样本不足"
                return f"{x.get('trend', '-')} / {x.get('macd_state', '-')}"
            L.append(f"| {p['symbol']} | {_fmt(t.get('daily'))} | "
                     f"{_fmt(t.get('weekly'))} | {_fmt(t.get('monthly'))} | "
                     f"{t.get('alignment')} |")
        L.append("")
    # 位置画像摘要（v1.5.0）：让报告第一屏就能看出"这批币在什么位置"
    _pp = output.get("position_profile") or {}
    if _pp.get("median_pos_90") is not None:
        L.append(f"\n> **位置画像（v1.5.0 新增）**：入选币 90 日区间位置中位数 "
                 f"**{_pp['median_pos_90']}%**；分布 {_pp.get('zone_counts')}；"
                 f"触发位置扣分 {_pp.get('penalized_count', 0)} 个、"
                 f"拉伸过度 {_pp.get('stretched_count', 0)} 个、"
                 f"放量多日 {_pp.get('crowded_count', 0)} 个。")
        if (_pp.get("median_pos_90") or 0) >= 70:
            L.append(">\n> ⚠️ **本批入选整体偏高位**：位置扣分只压排序、不做一票否决（避免错杀主升浪）。"
                     "若希望更看重「低位/未启动」，请看下方「低位横盘/震荡候选」，"
                     "或用 `--hard-pos 95` 启用硬性否决。")

    # 低位横盘 / 震荡调整候选（v1.5.0）—— 回答"低位横盘的有没有考虑"
    _acc = output.get("accumulation_candidates") or []
    L.append("\n## 低位横盘 / 震荡调整候选（独立通道，v1.5.0 新增）\n")
    if _acc:
        L.append("> 原策略要求「站上MA20 且 MA20 拐头向上」，**横盘币天然不满足**，"
                 "因此系统性地只会选出「已经在涨」的币。本表走独立通道：不要求趋势信号，"
                 "只找仍在低位/中低位、近期窄幅横盘或量能萎缩的标的。"
                 "**仅作候选，不自动入选**——由研判决定是否纳入。\n")
        L.append("| 币种 | 90日位置 | 60日振幅 | 横盘天数 | 缩量比 | 距MA20 | 突破触发位 | 失效位 | 结构分 |")
        L.append("|------|---------|---------|---------|--------|--------|-----------|--------|--------|")
        for a in _acc:
            L.append(f"| {a['symbol']} | {a['pos_90']}%（{a['zone']}） | "
                     f"{a['range_width_60']}% | {a['base_days']} | "
                     f"{a['vol_shrink_ratio']} | {a['dist_ma20_pct']}% | "
                     f"{a['breakout_trigger']} | {a['invalidation']} | {a['structure_points']} |")
        L.append("\n> 说明：突破触发位=90日区间上沿（站上才算启动），失效位=90日区间下沿。"
                 "横盘币的买点逻辑与趋势币不同：等**放量突破上沿**再介入，而非追当前价。")
    else:
        L.append("> 本次扫描在全池中**未发现**同时满足「低位/中低位 + 窄幅横盘或缩量」"
                 "的合格候选（这本身是一个市场信息：当前主流币普遍已在高位）。")
    L.append("\n## 入选理由明细\n")
    for i, p in enumerate(output["picked"], 1):
        L.append(f"### {i}. {p['symbol']}（截至 {p['last_date']}，收盘 {p['close']}）")
        for r in p["reasons"]:
            L.append(f"- {r}")
        for r in p["bonus_reasons"]:
            L.append(f"- {r}")
        L.append(f"- 失效位：{p['invalidation']}（日线收盘跌破则逻辑不成立）\n")

    # 赛道强度 + 补涨观察名单
    sa = output.get("sector_analysis", {})
    if sa.get("sector_strength"):
        L.append("\n## 赛道强度排行\n")
        L.append("| 赛道 | 成员数 | 7日中位涨幅 | 站上MA20占比 | 平均量比 | 强度分 |")
        L.append("|------|--------|------------|-------------|---------|--------|")
        for x in sa["sector_strength"]:
            mark = " ★" if x["sector"] in sa["top_sectors"] else ""
            L.append(f"| {x['sector']}{mark} | {x['members']} | {x['median_gain_7d']}% | "
                     f"{x['pct_above_ma20']}% | {x['avg_vol_ratio']} | {x['strength']} |")
        L.append("")
    if sa.get("laggards"):
        L.append("## 补涨观察名单（强势赛道内滞涨但结构成型）\n")
        L.append("> 非立即入场信号：放量突破触发位才确认补涨启动；失效位跌破则排除。\n")
        L.append("| 币种 | 赛道 | 7日涨幅 | 结构成型 | 现价 | 触发位(20日高) | 失效位 |")
        L.append("|------|------|---------|---------|------|----------------|--------|")
        for g in sa["laggards"]:
            L.append(f"| {g['symbol']} | {g['sector']} | {g['gain_7d']}% | "
                     f"{'+'.join(g['structure_desc'])} | {g['close']} | {g['trigger']} | {g['invalidation']} |")
        L.append("")
    L.append("---\n**免责声明**：本名单由量化规则自动筛选，仅为技术信号参考，不构成投资建议。"
             "虚拟币波动剧烈，请独立决策并严格控制仓位。")
    with open(args.md, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    log(f"\n完成，耗时 {output['elapsed_sec']}s。最终入选 {len(picked)} 个：")
    for i, p in enumerate(output["picked"], 1):
        log(f"  {i:>2}. {p['symbol']:<12} score={p['score']} hits={p['hits']} rsi={p['rsi14']}")
    log(f"结果: {args.out} / {args.md}")


if __name__ == "__main__":
    main()
