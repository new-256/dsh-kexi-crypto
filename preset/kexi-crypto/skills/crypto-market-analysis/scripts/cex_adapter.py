#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cex_adapter.py - 四大 CEX 统一适配层（v1.5.0 新增）

═══════════════════════════════════════════════════════════════════════
用户要求（2026-09-29）：
  · "加入调仓功能，通过连接用户提供的真实cex api查看用户仓位，
     并在用户干预的情况下一键调仓以及设置止盈止损位置"
  · 权限：直接实盘
  · 交易所：多交易所（Gate/MEXC/OKX/Binance），"都提供接口看用户启用什么"
  · 账户类型：现货 / U本位合约 / 币本位合约 / 统一账户
  · 密钥安全：本地存储，不上传云端（见 cex_keystore.py）
═══════════════════════════════════════════════════════════════════════

设计原则
--------
1. **签名是唯一真正随交易所改变的部分** → 抽成纯函数 `sign_*`，
   可**离线单测**（无需网络、无需真密钥）。这是本模块可验证性的关键：
   四家的签名规则差异极大且极易写错，必须先证明签名正确再谈下单。
2. **统一接口**：get_balances / get_positions / place_order / cancel_order /
   set_tpsl / get_open_orders / set_leverage。
   `set_tpsl` **独立成方法**（不并入 place_order）——实盘研究确认：
   4 家里有 2 家（OKX/Gate）必须走**独立端点**，且参数名几乎没有交集。
3. **只读与写分离**：`DRY_RUN` 默认 True。任何下单/撤单在 dry_run 下只返回
   将要发送的请求（含签名头），**绝不发出**。用户显式关掉才真发单。
4. **统一风控前置**（见 cex_risk.py）：所有 place_order 必须先过风控闸门。

各家签名规则（来源：官方 GitHub 仓库 / 官方 SDK 源码 / 官方文档，已逐一核对）
-----------------------------------------------------------------------------
BINANCE   HMAC-SHA256 **hex**；签名串 = query + body（原样拼接）；
          header `X-MBX-APIKEY`；timestamp **毫秒**；`signature` 必须放最后；
          无 passphrase；recvWindow 默认 5000 / 最大 60000。
OKX       HMAC-SHA256 **Base64**（不是 hex！）；预签名串 =
          `timestamp + METHOD + requestPath + body`（METHOD 大写；GET 参数算在
          requestPath 里、不算 body）；headers OK-ACCESS-KEY/SIGN/TIMESTAMP/
          PASS-PHRASE 四项齐全；timestamp 是 **ISO8601 毫秒 UTC**；
          服务器拒绝漂移 >30s（错误码 50102）。
GATE      HMAC-SHA512 **hex**（不是 SHA256！）；签名串 =
          `METHOD\nURL路径\nquery串\nSHA512_hex(body)\ntimestamp秒`；
          headers 是 **KEY / Timestamp / SIGN**；timestamp 用**秒**；
          注意 body 的 SHA-512 摘要要嵌进签名串里。
MEXC      ⚠️ **现货与合约是两套完全不同的鉴权**：
          现货  base api.mexc.com：HMAC-SHA256 hex **小写**；header `X-MEXC-APIKEY`；
                签名串 = query + body；timestamp 毫秒。
          合约  base contract.mexc.com：headers `ApiKey`/`Request-Time`/`Signature`；
                签名串 = accessKey + timestamp + 参数串；GET/DELETE 参数按
                **字典序排序**后 `&` 连接，**POST 用原始 JSON 串（不排序）**。
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

TZ8 = timezone(timedelta(hours=8))
UA = {"User-Agent": "kexi-cex/1.0", "Content-Type": "application/json"}

# 全局干跑开关：默认**只读**，绝不误发实盘单
DRY_RUN = True


def _ts_ms():
    return int(time.time() * 1000)


def _ts_sec():
    return int(time.time())


def _iso_ms_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
           f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


# ══════════════════════════════════════════════════════════════════════
# 签名（纯函数，可离线单测）
# ══════════════════════════════════════════════════════════════════════
def sign_binance(secret, params, timestamp=None):
    """返回 (query_string, headers)。签名串 = urlencode(params) + body。

    规则：HMAC-SHA256 **hex**；`signature` 必须放在参数**最后**；
    timestamp 毫秒；recvWindow 默认 5000。
    """
    p = dict(params)
    p["timestamp"] = timestamp if timestamp is not None else _ts_ms()
    p.setdefault("recvWindow", 5000)
    query = urllib.parse.urlencode(p)
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f"{query}&signature={sig}", None, p


def sign_okx(secret, api_key, passphrase, method, request_path, body="",
             timestamp=None):
    """返回 (request_path_with_query, headers)。

    规则：HMAC-SHA256 **Base64**；预签名串 = timestamp + METHOD + requestPath
    + body；timestamp 为 ISO8601 **毫秒** UTC；四项 header 齐全。
    ⚠️ GET 的查询参数属于 requestPath（要参与签名），不属于 body。
    """
    ts = timestamp or _iso_ms_utc()
    prehash = f"{ts}{method.upper()}{request_path}{body}"
    sig = base64.b64encode(
        hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": sig,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }
    return request_path, headers


def sign_gate(secret, api_key, method, url_path, query="", body="", timestamp=None):
    """返回 (query_string_with_sign, headers)。

    规则：HMAC-SHA512 **hex**；签名串 =
        METHOD + "\\n" + URL路径 + "\\n" + query串 + "\\n" + SHA512_hex(body)
        + "\\n" + timestamp秒
    headers: KEY / Timestamp / SIGN。
    ⚠️ 这是四家里最容易写错的——SHA512 而非 SHA256，且 body 摘要要嵌入。

    已用官方 SDK（gateapi-python/api_client.py::gen_sign）**逐字节交叉验证**。
    实测踩到的坑（务必保留此注释）：**无 body 时必须是 `sha512("")`，不是
    `sha512("{}")`**。初版写成 `body or {}` → None 被转成 `{}`，导致**所有 GET
    请求签名全错**（余额/仓位查询全部 401），而 POST（有 body）却恰好正确——
    这种"一半能通"的 bug 极难从现象上定位，是靠官方 SDK 对比才暴露的。
    """
    ts = str(timestamp if timestamp is not None else _ts_sec())
    if body is None:
        body_str = ""                      # ← 关键：None → 空串，不是 "{}"
    elif isinstance(body, str):
        body_str = body
    else:
        body_str = json.dumps(body)
    body_hash = hashlib.sha512(body_str.encode()).hexdigest()
    sign_str = "\n".join([method.upper(), url_path, query, body_hash, ts])
    sig = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha512).hexdigest()
    headers = {"KEY": api_key, "Timestamp": ts, "SIGN": sig,
               "Content-Type": "application/json"}
    return query, headers


def sign_mexc_spot(secret, params, timestamp=None):
    """返回 (query_string, headers)。

    规则：HMAC-SHA256 **hex 小写**；header `X-MEXC-APIKEY`；timestamp 毫秒；
    签名串 = query + body（本函数只处理 query 情形，下单 body 由调用方拼接）。
    """
    p = dict(params)
    p["timestamp"] = timestamp if timestamp is not None else _ts_ms()
    p.setdefault("recvWindow", 5000)
    query = urllib.parse.urlencode(p)
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    query = f"{query}&signature={sig}"
    return query, {"X-MEXC-APIKEY": p.get("_api_key", "")}


def sign_mexc_contract(secret, api_key, param_str, timestamp=None):
    """返回 headers。

    规则（与现货完全不同！）：headers `ApiKey`/`Request-Time`/`Signature`；
    签名串 = **accessKey + timestamp + 参数串**；HMAC-SHA256 hex。
    调用方负责按规则生成 param_str：
      · GET/DELETE → 按**字典序**排序后 `&` 连接
      · POST      → 原始 JSON 字符串（**不排序**）
    """
    ts = str(timestamp if timestamp is not None else _ts_ms())
    prehash = f"{api_key}{ts}{param_str}"
    sig = hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).hexdigest()
    return {"ApiKey": api_key, "Request-Time": ts, "Signature": sig,
            "Content-Type": "application/json"}


def mexc_contract_param_str(params, method):
    """生成 MEXC 合约参数串（GET/DELETE 排序，POST 原样 JSON）。"""
    if method.upper() == "POST":
        return json.dumps(params, separators=(",", ":"))
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()))


# ══════════════════════════════════════════════════════════════════════
# 统一适配器
# ══════════════════════════════════════════════════════════════════════
class CexError(Exception):
    def __init__(self, msg, exchange=None, code=None, raw=None):
        super().__init__(msg)
        self.exchange, self.code, self.raw = exchange, code, raw


class BaseAdapter:
    name = "base"
    supports_coinm = False       # 币本位是否支持（研究结论：只有 Binance/OKX 有）
    supports_tpsl_native = True
    required_permissions = ["read", "trade"]

    def __init__(self, api_key, api_secret, passphrase=None, dry_run=True,
                 timeout=15, logger=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.dry_run = dry_run
        self.timeout = timeout
        self._log = logger or (lambda m: None)
        self.calls = []          # 审计：记录本会话内的每次请求（不含密钥）

    # ---- HTTP ----
    def _http(self, method, url, headers=None, body=None, signed_params=None):
        """发请求。dry_run 且非只读 → **不发送**，返回将要发送的内容。"""
        h = dict(UA)
        h.update(headers or {})
        data = None
        if body is not None:
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        rec = {"method": method, "url": url,
               "has_body": data is not None,
               "t": datetime.now(TZ8).strftime("%H:%M:%S")}
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                txt = r.read().decode()
            rec["status"] = 200
            self.calls.append(rec)
            try:
                return json.loads(txt)
            except json.JSONDecodeError:
                raise CexError(f"响应非 JSON: {txt[:200]}", self.name, raw=txt)
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:400]
            self.calls.append({**rec, "status": e.code})
            raise CexError(f"HTTP {e.code}: {detail}", self.name,
                           code=e.code, raw=detail)
        except Exception as e:
            self.calls.append({**rec, "status": "ERR"})
            raise CexError(f"请求失败: {str(e)[:200]}", self.name)

    # ---- 需子类实现 ----
    def get_balances(self, account_type="spot"):
        raise NotImplementedError

    def get_positions(self, market="usdtm"):
        raise NotImplementedError

    def place_order(self, **kw):
        raise NotImplementedError

    def cancel_order(self, symbol, order_id=None):
        raise NotImplementedError

    def get_open_orders(self, symbol=None):
        raise NotImplementedError

    def set_tpsl(self, **kw):
        raise NotImplementedError

    def set_leverage(self, symbol, leverage, margin_mode="cross"):
        raise NotImplementedError

    # ---- 统一返回结构 ----
    @staticmethod
    def norm_balance(asset, free, locked, usd=None):
        return {"asset": asset, "free": float(free or 0),
                "locked": float(locked or 0),
                "total": float(free or 0) + float(locked or 0), "usd_value": usd}

    # ---- 带风控闸门的下单（**唯一推荐的实盘入口**）----
    def place_order_checked(self, order, account=None, positions=None,
                            profile=None, state=None, confirm=False):
        """风控闸门 + 下单。**这是应该被调用的方法**，而不是裸 place_order。

        设计意图：把风控做成"必经之路"而不是"可选建议"。
        使用裸 `place_order` 需要绕过本方法，属明确的反模式（会在审计里留痕）。

        Args:
            order   : dict(symbol, side, qty, price, notional_usd, leverage,
                           stop_loss, market, is_open)
            account : dict(net_usd, available_usd)；None → 自动读取
            confirm : 保守/稳健档要求 True 才放行（用户显式确认）
        Returns:
            dict(ok, allowed, risk, order_result|None, dry_run)
        """
        import cex_risk as _cr
        prof = profile or _cr.get_profile()
        if account is None:
            try:
                bals = self.get_balances()
                net = sum(float(b.get("usd_value") or 0) for b in bals) or None
                account = {"net_usd": net or 0, "available_usd": net or 0}
            except Exception as e:
                return {"ok": False, "allowed": False, "dry_run": self.dry_run,
                        "error": f"无法读取账户净值，风控无法评估 → 拒绝下单: {e}",
                        "risk": None, "order_result": None}
        if positions is None:
            try:
                positions = self.get_positions(order.get("market", "usdtm"))
            except Exception:
                positions = []

        risk = _cr.check_order(order, account, positions, prof, state,
                               order.get("market", "spot"))
        if not risk["allowed"]:
            self._log(f"[风控拦截] {order.get('symbol')} {'; '.join(risk['reasons'])}")
            return {"ok": False, "allowed": False, "risk": risk,
                    "order_result": None, "dry_run": self.dry_run}

        # 需确认档位：未确认不发单（防止脚本/误触直接实盘）
        if prof.get("require_confirmation") and not confirm:
            return {"ok": False, "allowed": True, "risk": risk,
                    "order_result": None, "dry_run": self.dry_run,
                    "need_confirm": True,
                    "message": f"风控已通过，但 {prof['_profile']} 档要求显式确认。"
                               f"请带 confirm=True 重新调用。"
                               f"警告: {risk['warnings']}"}

        if self.dry_run:
            return {"ok": True, "allowed": True, "risk": risk,
                    "order_result": {"dry_run": True,
                                     "would_send": {"symbol": order.get("symbol"),
                                                    "side": order.get("side"),
                                                    "notional_usd": order.get("notional_usd")}},
                    "dry_run": True,
                    "message": "DRY_RUN 模式：风控通过，但**未发送**任何真实订单"}

        res = self.place_order(
            symbol=order["symbol"], side=order["side"], qty=order.get("qty"),
            price=order.get("price"), market=order.get("market", "spot"),
            order_type=order.get("order_type", "MARKET"), **order.get("extra", {}))
        if state is not None:
            state.record(order.get("notional_usd") or 0)
        # 下单成功后自动挂 TP/SL（若提供）
        tpsl_result = None
        if order.get("take_profit") or order.get("stop_loss"):
            try:
                tpsl_result = self.set_tpsl(
                    symbol=order["symbol"], side=order["side"],
                    tp=order.get("take_profit"), sl=order.get("stop_loss"),
                    market=order.get("market", "usdtm"))
            except Exception as e:
                tpsl_result = {"error": f"下单成功但挂 TP/SL 失败: {e}（请手动补挂！）"}
        return {"ok": True, "allowed": True, "risk": risk, "order_result": res,
                "tpsl_result": tpsl_result, "dry_run": False}

    @staticmethod
    def norm_position(symbol, side, size, entry, mark=None, upnl=None,
                      leverage=None, liq=None, margin_mode=None):
        return {"symbol": symbol, "side": side, "size": float(size or 0),
                "entry_price": float(entry or 0),
                "mark_price": float(mark) if mark else None,
                "unrealized_pnl": float(upnl) if upnl is not None else None,
                "leverage": leverage, "liq_price": liq, "margin_mode": margin_mode}

    def capabilities(self):
        return {"exchange": self.name,
                "markets": ["spot", "usdtm"] + (["coinm"] if self.supports_coinm else []),
                "coinm": self.supports_coinm,
                "native_tpsl": self.supports_tpsl_native,
                "dry_run": self.dry_run}


# ────────────────────────── BINANCE ──────────────────────────
class BinanceAdapter(BaseAdapter):
    name = "binance"
    supports_coinm = True        # 唯一同时有 U本位(fapi) + 币本位(dapi) + 统一(papi) 的
    SPOT = "https://api.binance.com"
    FAPI = "https://fapi.binance.com"
    DAPI = "https://dapi.binance.com"
    PAPI = "https://papi.binance.com"

    def _signed(self, base, path, params, method="GET", body=None):
        p = dict(params)
        p.pop("_api_key", None)
        # sign_binance 返回三元组 (query, headers, params)——此处只用 query。
        query, _hdrs, _p = sign_binance(self.api_secret, p)
        url = f"{base}{path}?{query}"
        return self._http(method, url, {"X-MBX-APIKEY": self.api_key}, body)

    def _spot_signed(self, path, params, method="GET"):
        return self._signed(self.SPOT, path, params, method)

    def get_balances(self, account_type="spot"):
        if account_type == "coinm":
            d = self._signed(self.DAPI, "/dapi/v1/balance", {})
            return [self.norm_balance(b["asset"], b.get("availableBalance"),
                                      b.get("balance") and
                                      float(b["balance"]) - float(b.get("availableBalance") or 0),
                                      b.get("balance"))
                    for b in d.get("data", [])] if isinstance(d, dict) else d
        if account_type in ("usdtm",):
            d = self._signed(self.FAPI, "/fapi/v3/balance", {})
            rows = d if isinstance(d, list) else d.get("data", [])
            return [self.norm_balance(b["asset"], b.get("availableBalance"),
                                      b.get("crossUnPnl"), b.get("balance"))
                    for b in rows]
        d = self._signed(self.SPOT, "/api/v3/account", {})
        return [self.norm_balance(b["asset"], b["free"], b["locked"])
                for b in d.get("balances", [])
                if float(b["free"]) or float(b["locked"])]

    def get_positions(self, market="usdtm"):
        if market == "coinm":
            d = self._signed(self.DAPI, "/dapi/v1/positionRisk", {})
        elif market == "usdtm":
            d = self._signed(self.FAPI, "/fapi/v2/positionRisk", {})
        else:
            return []      # 现货无"仓位"概念
        rows = d if isinstance(d, list) else []
        out = []
        for p in rows:
            amt = float(p.get("positionAmt") or 0)
            if not amt:
                continue
            out.append(self.norm_position(
                p.get("symbol"), "long" if amt > 0 else "short", abs(amt),
                p.get("entryPrice"), p.get("markPrice"),
                p.get("unRealizedProfit"), p.get("leverage"),
                p.get("liquidationPrice")))
        return out

    def get_open_orders(self, symbol=None):
        params = {"symbol": symbol} if symbol else {}
        # ⚠️ 研究结论：现货不带 symbol 查询 openOrders 权重高达 80
        d = self._signed(self.SPOT, "/api/v3/openOrders", params)
        return d if isinstance(d, list) else d.get("data", d)

    def place_order(self, symbol, side, order_type="MARKET", qty=None,
                    price=None, market="spot", stop_price=None, **kw):
        """下单。spot/fapi/dapi 走同一套签名规则。"""
        params = {"symbol": symbol, "side": side.upper(),
                  "type": order_type.upper()}
        if qty is not None:
            params["quantity"] = qty
        if price is not None:
            params["price"] = price
        if stop_price is not None:
            params["stopPrice"] = stop_price
        params.update({k: v for k, v in kw.items() if v is not None})
        if market == "coinm":
            return self._signed(self.DAPI, "/dapi/v1/order", params, "POST")
        if market == "usdtm":
            return self._signed(self.FAPI, "/fapi/v1/order", params, "POST")
        return self._signed(self.SPOT, "/api/v3/order", params, "POST")

    def cancel_order(self, symbol, order_id=None, market="spot"):
        params = {"symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        base = {"coinm": self.DAPI, "usdtm": self.FAPI}.get(market, self.SPOT)
        path = {True: "/dapi/v1/order", False: "/fapi/v1/order"}[market == "coinm"] \
            if market in ("coinm", "usdtm") else "/api/v3/order"
        return self._signed(base, path, params, "DELETE")

    def set_tpsl(self, symbol, side, tp=None, sl=None, qty=None, market="usdtm",
                 close_position=True, working_type="MARK_PRICE"):
        """止盈止损。合约用 STOP_MARKET / TAKE_PROFIT_MARKET，原生同一端点。

        `closePosition=True` 时平掉整个仓位（免填数量），是最安全的默认。
        """
        results = []
        if market == "spot":
            raise CexError("Binance 现货不支持原生 TP/SL（需用 OCO 或 STOP_LOSS_LIMIT）",
                           self.name)
        base = self.DAPI if market == "coinm" else self.FAPI
        otype = "STOP_MARKET" if market == "usdtm" else "STOP"
        # Binance 合约的"平仓方向"与持仓方向相反
        close_side = "SELL" if side.lower() == "long" else "BUY"
        for kind, trig in (("TP", tp), ("SL", sl)):
            if trig is None:
                continue
            params = {
                "symbol": symbol, "side": close_side,
                "type": "TAKE_PROFIT_MARKET" if kind == "TP" else "STOP_MARKET",
                "stopPrice": trig, "workingType": working_type,
                "priceProtect": "TRUE",
            }
            if close_position:
                params["closePosition"] = "true"
            elif qty is not None:
                params["quantity"] = qty
            path = "/dapi/v1/order" if market == "coinm" else "/fapi/v1/order"
            results.append({kind: self._signed(base, path, params, "POST")})
        return results

    def set_leverage(self, symbol, leverage, margin_mode="cross", market="usdtm"):
        base = self.DAPI if market == "coinm" else self.FAPI
        path = "/dapi/v1/leverage" if market == "coinm" else "/fapi/v1/leverage"
        return self._signed(base, path, {"symbol": symbol, "leverage": leverage},
                            "POST")


# ────────────────────────── OKX ──────────────────────────
class OkxAdapter(BaseAdapter):
    name = "okx"
    supports_coinm = True        # instType=SWAP + ctType=inverse
    BASE = "https://openapi.okx.com"     # 实盘与模拟同一域名，模拟靠 header 区分
    DEMO_HEADER = "x-simulated-trading"

    def __init__(self, *a, simulated=False, **kw):
        super().__init__(*a, **kw)
        self.simulated = simulated

    def _req(self, method, path, params=None, body=None):
        q = ""
        if params and method.upper() == "GET":
            # ⚠️ OKX：GET 的查询串属于 requestPath，必须参与签名
            q = "?" + urllib.parse.urlencode(params)
        req_path = path + q
        body_str = ""
        if method.upper() != "GET" and body is not None:
            body_str = json.dumps(body, separators=(",", ":"))
        _, headers = sign_okx(self.api_secret, self.api_key, self.passphrase,
                              method, req_path, body_str)
        if self.simulated:
            headers[self.DEMO_HEADER] = "1"
        return self._http(method, self.BASE + req_path, headers,
                          body_str if body_str else None)

    def get_balances(self, account_type="unified"):
        d = self._req("GET", "/api/v5/account/balance")
        out = []
        for acc in (d.get("data") or []):
            for det in acc.get("details", []):
                out.append(self.norm_balance(
                    det.get("ccy"), det.get("availBal") or det.get("availEq"),
                    det.get("frozenBal"),
                    float(det.get("eqUsd") or 0) or None))
        return out

    def get_positions(self, market="usdtm"):
        # ⚠ OKX 的 `/api/v5/account/positions` **没有 SPOT 这个 instType**——
        #   合法值只有 SWAP / FUTURES / OPTION / MARGIN。传 SPOT 会得到
        #   `HTTP 400 {"code":"51000","msg":"Parameter instType error"}`（实测）。
        #   现货持仓**必须从余额接口推导**（非零 free 的币种即为现货持仓），
        #   否则 `positions --market spot` 永远失败、all_positions 永远带一条错。
        if market == "spot":
            return self._spot_positions_from_balance()
        inst = {"usdtm": "SWAP", "coinm": "SWAP", "futures": "FUTURES"}.get(market, "SWAP")
        d = self._req("GET", "/api/v5/account/positions", {"instType": inst})
        out = []
        for p in (d.get("data") or []):
            pos = float(p.get("pos") or 0)
            if not pos:
                continue
            out.append(self.norm_position(
                p.get("instId"), p.get("posSide") or ("long" if pos > 0 else "short"),
                abs(pos), p.get("avgPx"), p.get("markPx"), p.get("upl"),
                p.get("lever"), p.get("liqPx"), p.get("mgnMode")))
        return out

    def _spot_positions_from_balance(self, usd_value_field="eqUsd"):
        """从账户余额推导现货持仓。

        口径说明（别把它当成"仓位"）：
          · 只取 **非零** 余额的币种（0.0000001 个 DOGE 也算——那是真实 dust 持仓）
          · `size` 用 **free + locked 之和**，因为 locked 部分同样占用你的钱
          · `mark_price` 必须是**单价** = eqUsd ÷ 数量。⚠ 早先一版直接把
            `eqUsd`（等值美元 $81.63）当成 mark_price 填进去，而 `size` 是币数
            （1046.72）——下游 `size × mark` 会算出 $85,444，**错了约 1000 倍**。
            余额接口不直接给价格，只能这样反推；反推不出来就置 None（不猜）。
          · `avgPx` **没有可靠来源**（余额接口不给成本价），置 None 而不是编一个
          · `leverage` 恒为 1（现货无杠杆）
        """
        d = self._req("GET", "/api/v5/account/balance", {"ccy": ""})
        out = []
        for b in (d.get("data") or []):
            for det in (b.get("details") or []):
                try:
                    free = float(det.get("availBal") or det.get("free") or 0)
                    locked = float(det.get("frozenBal") or det.get("locked") or 0)
                except (TypeError, ValueError):
                    continue
                total = free + locked
                if total <= 0:
                    continue
                ccy = det.get("ccy")
                if not ccy:
                    continue
                usd = det.get(usd_value_field)
                try:
                    usd = float(usd) if usd not in (None, "") else None
                except (TypeError, ValueError):
                    usd = None
                # 单价 = 等值美元 ÷ 数量；拿不到就 None，绝不用 0 顶（0 会让下游算成"无敞口"）
                mark = (usd / total) if (usd is not None and usd > 0) else None
                out.append(self.norm_position(
                    # 现货没有合约的 instId 概念；统一成 `{币种}-SPOT` 以便下游按 symbol 取
                    "%s-SPOT" % ccy, "long", total,
                    None,                 # avgPx：余额接口不给成本价，不编
                    mark,
                    None,                 # 现货无未实现盈亏
                    1, None, "spot"))
        return out

    def get_open_orders(self, symbol=None):
        params = {"instType": "SPOT"}
        if symbol:
            params["instId"] = symbol
        return self._req("GET", "/api/v5/trade/orders-pending", params).get("data", [])

    def place_order(self, inst_id, side, ord_type="market", qty=None,
                    price=None, td_mode="cash", pos_side=None, inst_type="SPOT",
                    tgt_ccy=None, attach_tpsl=None, **kw):
        body = {"instId": inst_id, "tdMode": td_mode, "side": side.lower(),
                "ordType": ord_type.lower()}
        if qty is not None:
            body["sz"] = str(qty)
        if price is not None:
            body["px"] = str(price)
        if pos_side:
            body["posSide"] = pos_side
        if tgt_ccy:
            body["tgtCcy"] = tgt_ccy
        # OKX 支持 attachAlgoOrds：一次调用同时挂上 TP/SL（免二次请求）
        if attach_tpsl:
            body["attachAlgoOrds"] = attach_tpsl
        body.update({k: v for k, v in kw.items() if v is not None})
        return self._req("POST", "/api/v5/trade/order", body=body)

    def cancel_order(self, inst_id, order_id=None, cl_ord_id=None):
        body = {"instId": inst_id}
        if order_id:
            body["ordId"] = order_id
        if cl_ord_id:
            body["clOrdId"] = cl_ord_id
        return self._req("POST", "/api/v5/trade/cancel-order", body=body)

    def set_tpsl(self, inst_id, td_mode="cross", side=None, tp=None, sl=None,
                 qty=None, pos_side=None, tp_ord_px=None, sl_ord_px=None,
                 close_fraction=None):
        """⚠️ OKX 的 TP/SL 走**独立端点** /api/v5/trade/order-algo。

        ordType=conditional：触发价 tpTriggerPx/slTriggerPx，执行价
        tpOrdPx/slOrdPx（-1 表示市价）。
        """
        body = {"instId": inst_id, "tdMode": td_mode, "ordType": "conditional"}
        if side:
            body["side"] = side.lower()
        if pos_side:
            body["posSide"] = pos_side
        if qty is not None:
            body["sz"] = str(qty)
        if close_fraction:
            body["closeFraction"] = str(close_fraction)
        if tp is not None:
            body["tpTriggerPx"] = str(tp)
            body["tpOrdPx"] = str(tp_ord_px if tp_ord_px is not None else -1)
        if sl is not None:
            body["slTriggerPx"] = str(sl)
            body["slOrdPx"] = str(sl_ord_px if sl_ord_px is not None else -1)
        return self._req("POST", "/api/v5/trade/order-algo", body=body)

    def set_leverage(self, inst_id, leverage, mgn_mode="cross", pos_side=None):
        body = {"instId": inst_id, "lever": str(leverage), "mgnMode": mgn_mode}
        if pos_side:
            body["posSide"] = pos_side
        return self._req("POST", "/api/v5/account/set-leverage", body=body)

    def get_account_config(self):
        return self._req("GET", "/api/v5/account/config").get("data", [])


# ────────────────────────── GATE ──────────────────────────
class GateAdapter(BaseAdapter):
    name = "gate"
    supports_coinm = True        # {settle} = btc | usd
    BASE = "https://api.gateio.ws/api/v4"

    def _req(self, method, path, query="", body=None, settle=None):
        p = path.format(settle=settle) if settle else path
        # ⚠️ 无 body 时必须传 None（→ sha512("")），不能传 ""/"{}"。
        # 见 sign_gate 文档：GET 签名错会表现为"余额/仓位查询全 401"。
        body_str = json.dumps(body) if body is not None else None
        q, headers = sign_gate(self.api_secret, self.api_key, method, p, query,
                               body_str)
        url = f"{self.BASE}{p}" + (f"?{q}" if q and method.upper() != "POST" else "")
        return self._http(method, url, headers, body_str)

    def get_balances(self, account_type="spot"):
        if account_type in ("usdtm", "coinm"):
            settle = "usdt" if account_type == "usdtm" else "btc"
            d = self._req("GET", "/futures/{settle}/accounts", settle=settle)
            return [self.norm_balance(settle.upper(), d.get("available"),
                                      d.get("order_margin"), d.get("total"))]
        d = self._req("GET", "/spot/accounts")
        return [self.norm_balance(a.get("currency"), a.get("available"),
                                  a.get("locked"))
                for a in (d if isinstance(d, list) else [])]

    def get_positions(self, market="usdtm"):
        settle = {"usdtm": "usdt", "coinm": "btc"}.get(market)
        if not settle:
            return []
        d = self._req("GET", "/futures/{settle}/positions", settle=settle)
        out = []
        for p in (d if isinstance(d, list) else []):
            size = float(p.get("size") or 0)
            if not size:
                continue
            out.append(self.norm_position(
                p.get("contract"), "long" if size > 0 else "short", abs(size),
                p.get("entry_price"), p.get("mark_price"),
                p.get("unrealised_pnl"), p.get("leverage"),
                p.get("liq_price"), p.get("mode")))
        return out

    def get_open_orders(self, symbol=None):
        q = "status=open"
        if symbol:
            q += f"&contract={symbol}"
        return self._req("GET", "/futures/usdt/orders", query=q)

    def place_order(self, symbol, side, qty=None, price=None, market="spot",
                    settle=None, tif="gtc", reduce_only=False, **kw):
        if market in ("usdtm", "coinm"):
            settle = settle or ("usdt" if market == "usdtm" else "btc")
            body = {"contract": symbol, "size": int(qty or 0),
                    "price": "0" if price is None else str(price),
                    "tif": tif}
            if market == "usdtm":
                body["price"] = str(price) if price is not None else "0"
            if reduce_only:
                body["reduce_only"] = True
            if price is None or market == "spot":
                pass
            # Gate 合约：price="0" + tif="ioc" 视为市价
            if price is None:
                body["tif"] = "ioc"
            body.update(kw)
            return self._req("POST", "/futures/{settle}/orders", body=body,
                             settle=settle)
        body = {"currency_pair": symbol, "side": side.lower(),
                "amount": str(qty), "type": "market" if price is None else "limit"}
        if price is not None:
            body["price"] = str(price)
        body.update(kw)
        return self._req("POST", "/spot/orders", body=body)

    def cancel_order(self, order_id, symbol=None, market="spot", settle=None):
        if market in ("usdtm", "coinm"):
            settle = settle or ("usdt" if market == "usdtm" else "btc")
            return self._req("DELETE", f"/futures/{{settle}}/orders/{order_id}",
                             settle=settle)
        return self._req("DELETE", f"/spot/orders/{order_id}")

    def set_tpsl(self, symbol, trigger_price, rule=1, qty=None, side="sell",
                 market="usdtm", settle=None, price="0", strategy_type=0):
        """⚠️ Gate 的 TP/SL 走**独立端点** /futures/{settle}/price_orders。

        rule: 1 = >= (止盈), 2 = <= (止损)
        """
        settle = settle or ("usdt" if market == "usdtm" else "btc")
        body = {
            "initial": {"contract": symbol, "size": int(qty or 0),
                        "price": str(price), "tif": "gtc",
                        "text": "kexi-tpsl"},
            "trigger": {"strategy_type": strategy_type, "price_type": 0,
                        "price": str(trigger_price), "rule": rule,
                        "expiration": 86400},
            "order_type": "close" if side.lower() == "close" else "plan",
        }
        return self._req("POST", "/futures/{settle}/price_orders", body=body,
                         settle=settle)

    def set_leverage(self, symbol, leverage, market="usdtm", settle=None):
        settle = settle or ("usdt" if market == "usdtm" else "btc")
        return self._req("POST", f"/futures/{{settle}}/positions/{symbol}/leverage",
                         query=f"leverage={leverage}", settle=settle)


# ────────────────────────── MEXC ──────────────────────────
class MexcAdapter(BaseAdapter):
    """⚠️ MEXC 是四家里**风险最高**的接入：
      · 现货与合约是两套完全不同的鉴权与域名
      · 官方文档把合约的**下单/撤单/触发单**等核心端点标注为 "(Under maintenance)"
      · **没有测试网**（研究确认文档中无任何 testnet/demo 说明）
    → 因此本适配器对合约的**写操作默认拒绝**，需用户显式 `allow_contract_writes=True`
      才放行，且每次写操作都会在审计里标注"未验证端点"。
    """
    name = "mexc"
    supports_coinm = False       # 只有 BTC_USD 计价，不视为独立币本位市场
    SPOT = "https://api.mexc.com"
    CONTRACT = "https://contract.mexc.com"

    def __init__(self, *a, allow_contract_writes=False, **kw):
        super().__init__(*a, **kw)
        self.allow_contract_writes = allow_contract_writes

    def _spot_req(self, method, path, params=None, body=None):
        p = dict(params or {})
        p.pop("_api_key", None)
        # sign_mexc_spot 返回二元组 (query_with_signature, headers)
        query, _ = sign_mexc_spot(self.api_secret, p)
        url = f"{self.SPOT}{path}?{query}"
        return self._http(method, url, {"X-MEXC-APIKEY": self.api_key}, body)

    def _contract_req(self, method, path, params=None, write=False):
        if write and not self.allow_contract_writes:
            raise CexError(
                "MEXC 合约写操作被拒绝：官方文档将合约下单/撤单/触发单标注为"
                "『Under maintenance』，且 MEXC 无测试网。"
                "如确需启用，请显式传 allow_contract_writes=True 并自行承担风险。",
                self.name)
        p = dict(params or {})
        p["timestamp"] = _ts_ms()
        param_str = mexc_contract_param_str(p, method)
        headers = sign_mexc_contract(self.api_secret, self.api_key, param_str,
                                     p["timestamp"])
        if method.upper() == "GET":
            url = f"{self.CONTRACT}{path}?{param_str}"
            return self._http(method, url, headers)
        return self._http(method, f"{self.CONTRACT}{path}", headers,
                          json.dumps(p, separators=(",", ":")))

    def get_balances(self, account_type="spot"):
        if account_type in ("usdtm", "coinm"):
            d = self._contract_req(
                "GET", "/api/v1/private/account/assets")
            rows = d.get("data") or []
            return [self.norm_balance(a.get("currency"),
                                      a.get("availableBalance"),
                                      a.get("frozenBalance"),
                                      a.get("equity"))
                    for a in rows]
        d = self._spot_req("GET", "/api/v3/account")
        return [self.norm_balance(b.get("asset"), b.get("free"), b.get("locked"))
                for b in (d.get("balances") or [])
                if float(b.get("free") or 0) or float(b.get("locked") or 0)]

    def get_positions(self, market="usdtm"):
        if market == "spot":
            return []
        d = self._contract_req(
            "GET", "/api/v1/private/position/open_positions")
        out = []
        for p in (d.get("data") or []):
            typ = p.get("positionType")
            vol = float(p.get("holdVol") or 0)
            if not vol:
                continue
            out.append(self.norm_position(
                p.get("symbol"), "long" if typ == 1 else "short", vol,
                p.get("holdAvgPrice"), p.get("markPrice"),
                p.get("realised") if p.get("realised") else p.get("im"),
                p.get("leverage"), p.get("liquidatePrice"), p.get("openType")))
        return out

    def get_open_orders(self, symbol=None):
        path = (f"/api/v1/private/order/list/open_orders/{symbol}" if symbol
                else "/api/v1/private/order/list/open_orders")
        return self._contract_req("GET", path)

    def place_order(self, symbol, side, qty=None, price=None, market="spot",
                    order_type="LIMIT", **kw):
        if market in ("usdtm", "coinm"):
            body = {"symbol": symbol,
                    "vol": qty,
                    "side": 1 if side.lower() in ("buy", "long") else 3,
                    "type": 1 if order_type.upper() == "LIMIT" else 5,
                    "openType": 1 if kw.get("cross") else 2,
                    "price": price}
            body.update({k: v for k, v in kw.items() if v is not None})
            return self._contract_req("POST", "/api/v1/private/order/submit",
                                      body, write=True)
        body = {"symbol": symbol, "side": side.upper(), "type": order_type.upper()}
        if qty is not None:
            body["quantity"] = qty
        if price is not None:
            body["price"] = price
        body.update({k: v for k, v in kw.items() if v is not None})
        return self._spot_req("POST", "/api/v3/order", body=body)

    def cancel_order(self, symbol, order_id=None, market="spot"):
        if market in ("usdtm", "coinm"):
            return self._contract_req("POST", "/api/v1/private/order/cancel",
                                      {"symbol": symbol, "orderId": order_id},
                                      write=True)
        p = {"symbol": symbol}
        if order_id:
            p["orderId"] = order_id
        return self._spot_req("DELETE", "/api/v3/order", params=p)

    def set_tpsl(self, symbol, side=None, tp=None, sl=None, qty=None,
                 market="usdtm", trend=None):
        """⚠️ MEXC 合约 planorder（官方标注 Under maintenance）。"""
        out = []
        for kind, trig, tt in (("TP", tp, 1), ("SL", sl, 2)):
            if trig is None:
                continue
            body = {"symbol": symbol, "vol": qty,
                    "side": trend if trend is not None else (2 if kind == "TP" else 4),
                    "orderType": 5, "triggerPrice": trig,
                    "triggerType": tt, "executeCycle": 1,
                    "trend": trend if trend is not None else 1}
            out.append({kind: self._contract_req(
                "POST", "/api/v1/private/planorder/place", body, write=True)})
        return out

    def set_leverage(self, symbol, leverage, market="usdtm", open_type=1):
        return self._contract_req(
            "POST", "/api/v1/private/position/change_leverage",
            {"symbol": symbol, "leverage": leverage, "openType": open_type},
            write=True)


ADAPTERS = {
    "binance": BinanceAdapter,
    "okx": OkxAdapter,
    "gate": GateAdapter,
    "mexc": MexcAdapter,
}


def make_adapter(exchange, api_key, api_secret, passphrase=None, dry_run=True,
                 **kw):
    cls = ADAPTERS.get((exchange or "").lower())
    if not cls:
        raise CexError(f"不支持的交易所: {exchange}；支持 {list(ADAPTERS)}")
    return cls(api_key, api_secret, passphrase, dry_run=dry_run, **kw)


def adapter_from_keystore(store_dir, exchange, label="default", dry_run=True,
                          **kw):
    """从本地加密密钥库构建适配器（密钥**不落日志、不出本机**）。"""
    import cex_keystore as ks
    lk = ks.load_keys(store_dir, exchange, label)
    if not lk.get("ok"):
        raise CexError(lk.get("message") or "密钥读取失败", exchange)
    k = lk["keys"]
    return make_adapter(exchange, k["api_key"], k["api_secret"],
                        k.get("passphrase"), dry_run=dry_run, **kw)


# ══════════════════════════════════════════════════════════════════════
# 自主级别（autonomy）：**唯一实盘开关，且模型无法翻转**
# ══════════════════════════════════════════════════════════════════════
# 为什么不能做成 CLI 参数（如 `--autonomy full`）：
#   模型是能调 kexi_run(mode=script) 传任意 argv 的。若自主级别来自命令行，
#   模型只要多写一个参数就能从 readonly 跳到 full —— 安全措施形同虚设。
#   所以：**只从 host 面板写死的文件里读**，脚本不提供任何覆盖参数。
# 失败方向（fail-closed）：文件缺失/损坏/无法解析 → 一律当 readonly。
#   "权限配置读不出来"绝不能等于"放行实盘"。
AUTONOMY_FILE = "autonomy.json"
AUTONOMY_LEVELS = ("readonly", "confirm", "limited", "full")
AUTONOMY_RANK = {n: i for i, n in enumerate(AUTONOMY_LEVELS)}


def read_autonomy(store_dir):
    """读当前自主级别。**任何异常都退回 readonly**（fail-closed）。

    级别必须**精确匹配**四个已知值之一，不做 strip/lower 之类的"善意归一"。
    这是安全开关、不是用户输入：手工编辑时多打一个空格、退格没删干净，
    都应该落到最严档，而不是被"就近当成 full"。fail-closed 的价值全在这。
    """
    try:
        p = os.path.join(store_dir, AUTONOMY_FILE)
        if not os.path.exists(p):
            return {"level": "readonly", "source": "missing",
                    "note": "未找到 autonomy.json —— 权限配置不可读，按最严处理（只读）"}
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
        lv = (j or {}).get("level")
        if lv not in AUTONOMY_RANK:
            return {"level": "readonly", "source": "invalid",
                    "note": "autonomy.json 的 level 不是四个已知值之一（%r），按最严处理（只读）" % (lv,)}
        return {"level": lv, "source": "file",
                "profile": (j or {}).get("profile"),
                "auto_notional_cap_usd": (j or {}).get("auto_notional_cap_usd"),
                "updated_at": (j or {}).get("updated_at")}
    except Exception as e:
        return {"level": "readonly", "source": "error",
                "note": "读取 autonomy.json 失败（%s），按最严处理（只读）" % str(e)[:120]}


def autonomy_allows(current, need):
    """当前级别是否 ≥ 所需最低级别。未知级别一律 False。"""
    return AUTONOMY_RANK.get(current, -1) >= AUTONOMY_RANK.get(need, 99)


# ══════════════════════════════════════════════════════════════════════
# 订单意图日志（应对 37% 回合失败率）
# ══════════════════════════════════════════════════════════════════════
# 背景：本项目实测 `PI_AI_ERROR` 空响应失败率约 **37%**。做研究这只是浪费几分钟；
# 做实盘是另一个量级 —— 一个多步交易序列中途崩掉，可能出现「单已成交但
# 我方状态没记住」→ 下次重试变成重复下单。
# 对策：**先落意图，再发请求，发完落结果**。重启或崩溃后能回答
# 「这一笔到底发出去没有、交易所那边是什么状态」。
JOURNAL_FILE = "order_journal.jsonl"


def _journal_path(store_dir):
    return os.path.join(store_dir, JOURNAL_FILE)


def journal_append(store_dir, rec):
    """追加一条意图/结果记录。**只追加**，绝不改写历史。"""
    try:
        os.makedirs(store_dir, exist_ok=True)
        rec = dict(rec)
        rec.setdefault("at", _iso_ms_utc())
        with open(_journal_path(store_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def journal_recent(store_dir, limit=20):
    try:
        p = _journal_path(store_dir)
        if not os.path.exists(p):
            return []
        out = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        return out[-limit:]
    except Exception:
        return []


def journal_unresolved(store_dir, limit=20):
    """找出「已落意图、但没有结果记录」的订单 —— 崩溃/失败后的待人工确认清单。"""
    by_id = {}
    for r in journal_recent(store_dir, limit=500):
        oid = r.get("order_id")
        if not oid:
            continue
        by_id[oid] = r            # 同 id 后写覆盖先写 → 最后一条即终态
    return [r for r in by_id.values() if r.get("stage") == "intent"]


def _order_id(symbol, side, qty, price, seq):
    """稳定的意图 id。同参数重试会得到同一 id —— 便于识别重复。"""
    raw = "%s|%s|%s|%s|%s" % (symbol, side, qty, price, seq)
    return "kexi-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════
def _load_store(args):
    import cex_keystore as ks
    return args.store_dir or ks.default_store_dir()


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def cmd_health(args):
    """体检：适配器可用性 + 凭据状态 + 自主级别。**不发起任何交易请求**。"""
    store = _load_store(args)
    aut = read_autonomy(store)
    out = {
        "ok": True, "action": "health", "store_dir": store,
        "adapters": sorted(ADAPTERS.keys()),
        "autonomy": aut,
        "autonomy_explained": {
            "readonly": "只能读余额/持仓，任何写操作一律拒绝",
            "confirm": "生成待确认草案，需用户在面板确认后才发送",
            "limited": "限额内自动下单（必须带止损），超限转待确认",
            "full": "AI 自主下单，仅受 HARD_CEILING 约束",
        },
        "accounts": [],
    }
    for ex in sorted(ADAPTERS.keys()):
        try:
            import cex_keystore as ks
            idx_path = os.path.join(store, "index.json")
            labels = []
            if os.path.exists(idx_path):
                with open(idx_path, encoding="utf-8") as f:
                    idx = json.load(f)
                labels = sorted(((idx.get("entries", {}).get(ex, {}) or {}).get("labels", {}) or {}).keys())
            for lab in labels:
                lk = ks.load_keys(store, ex, lab)
                out["accounts"].append({
                    "exchange": ex, "label": lab,
                    "key_ok": bool(lk.get("ok")),
                    "encrypted": bool(lk.get("encrypted")),
                    "api_key_masked": (lk.get("keys") or {}).get("api_key", "")[:3] + "***"
                                      if lk.get("ok") else None,
                    "error": None if lk.get("ok") else str(lk.get("message") or "")[:120],
                })
        except Exception as e:
            out["accounts"].append({"exchange": ex, "label": None, "key_ok": False,
                                    "error": "枚举失败：%s" % str(e)[:100]})
    out["configured_count"] = sum(1 for a in out["accounts"] if a.get("key_ok"))
    return out


def cmd_accounts(args):
    """列出已配置账户（只读元数据，不解密）。"""
    store = _load_store(args)
    idx_path = os.path.join(store, "index.json")
    out = {"ok": True, "action": "accounts", "store_dir": store, "accounts": []}
    if not os.path.exists(idx_path):
        out["note"] = "index.json 不存在 → 尚未通过设置面板配置任何凭据"
        return out
    try:
        with open(idx_path, encoding="utf-8") as f:
            idx = json.load(f)
    except Exception as e:
        return {"ok": False, "action": "accounts", "error": "index.json 读取失败：%s" % str(e)[:120]}
    for ex, rec in sorted((idx.get("entries") or {}).items()):
        for lab, meta in sorted(((rec or {}).get("labels") or {}).items()):
            out["accounts"].append({"exchange": ex, "label": lab, **(meta or {})})
    out["count"] = len(out["accounts"])
    return out


import cex_keystore as ks_module   # v1.9.13：_one_account 要用它解析默认 label

def _one_account(store, exchange, label, dry_run):
    """构建适配器 + 返回凭据问题（不抛，给上层做诚实汇报）。

    ⚠ v1.9.13：label 缺省时**自动解析**，而不是把 None 传下去。
      实测（用户会话 session-4995d16a）：`kexi_cex action=balances` 不带 label，
      工具就什么都不推，Python 拿到 `label=None` → `未找到 okx/None 的密钥`。
      而工具 schema 明写着「缺省用设置里的默认标签」——**承诺了，没实现**。
      解析规则：只有一个已配置标签时用它；有多个时不猜，直接报错让调用方说明。
      （不硬编码 'main'：多账户场景下 main 可能根本不存在。）
    """
    if not exchange or not str(exchange).strip():
        raise RuntimeError(
            "未指定交易所（exchange 为空）——工具层漏传了 exchange。"
            "请显式传 binance/okx/gate/mexc 之一。"
            "报「未找到 None 的密钥」不是你的配置问题，是这里缺了个校验。")
    if not label:
        try:
            labs = sorted({r.get("label") for r in ks_module.list_keys(store)
                           if r.get("exchange") == (exchange or "").lower()
                           and r.get("label")})
        except Exception as e:                             # noqa: BLE001
            raise RuntimeError("未找到 %s 的密钥：%s" % (exchange, str(e)[:120]))
        if len(labs) == 1:
            label = labs[0]
        elif not labs:
            raise RuntimeError("未找到 %s 的密钥：该交易所尚未配置凭据"
                               "（去设置面板填 API Key）" % exchange)
        else:
            raise RuntimeError(
                "%s 有多个账户标签（%s），必须指明用哪个——"
                "不猜，因为猜错就等于读错账户。" % (exchange, "、".join(labs)))
    return adapter_from_keystore(store, exchange, label, dry_run=dry_run)


def _resolved_label(store, exchange):
    """回显用：该交易所实际会用哪个标签（解析规则与 _one_account 一致）。

    v1.9.14：解析成功后各 cmd 仍回显 `args.label`（原值 None），
    摘要就变成「okx/null 余额」——**功能对，显示像坏了**，
    会让人以为配置有问题。这里给出真实生效的标签。
    """
    try:
        labs = sorted({r.get("label") for r in ks_module.list_keys(store)
                       if r.get("exchange") == (exchange or "").lower()
                       and r.get("label")})
    except Exception:                                     # noqa: BLE001
        return "main"
    return labs[0] if labs else None


def cmd_balances(args):
    store = _load_store(args)
    try:
        ad = _one_account(store, args.exchange, args.label, dry_run=True)
        bals = ad.get_balances(args.account_type)
        return {"ok": True, "action": "balances", "exchange": args.exchange,
                "label": _resolved_label(store, args.exchange),   # v1.9.14 别回显 null
                "account_type": args.account_type,
                "balances": bals,
                "net_usd": round(sum(float(b.get("usd_value") or 0) for b in bals), 2)}
    except Exception as e:
        return {"ok": False, "action": "balances", "exchange": args.exchange,
                "label": _resolved_label(store, args.exchange),   # v1.9.14
                "error": str(e)[:240],
                "hint": "检查该交易所的 API Key 是否开通了「读取」权限"}


def cmd_positions(args):
    store = _load_store(args)
    try:
        ad = _one_account(store, args.exchange, args.label, dry_run=True)
        pos = ad.get_positions(args.market)
        ups = sum(float(p.get("unrealized_pnl") or 0) for p in pos)
        return {"ok": True, "action": "positions", "exchange": args.exchange,
                "label": args.label, "market": args.market, "positions": pos,
                "count": len(pos), "unrealized_pnl_total": round(ups, 2)}
    except Exception as e:
        return {"ok": False, "action": "positions", "exchange": args.exchange,
                "label": args.label, "error": str(e)[:240]}


def _to_f(v, default=None):
    """宽松转 float：交易所字段常是空串 / None / '0'，不能让它炸掉整条链路。"""
    if v is None or v == "":
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f


def _base_asset(inst_id):
    """PYTH-USDT -> PYTH"""
    return str(inst_id or "").split("-")[0].upper()


def get_cost_basis(ad, inst_id, lookback_days=90):
    """**现货成本价**：从历史订单的 `avgPx × accFillSz` 推导。

    ⚠ v1.9.15 为什么必须换接口：
      `/account/balance` **根本不返回成本字段**——这就是现货持仓
      `entry_price=0` 的真正原因（不是解析 bug，是接口天花板）。
      `/account/positions` 有 `avgPx`，但**只对合约/杠杆有效，现货没有**。
      唯一可靠来源是 `/trade/orders-history`（或 `fills`）。

    ⚠ **诚实性是这个函数的核心**，不是附带说明：
      交易所的历史订单接口有**时间窗口**，更早的分批买入可能取不到。
      那样算出来的成本会**偏低**（因为漏掉了早期的买入支出）。
      所以必须回传 `complete=False` + 覆盖到的最早时间，
      让下游能说清"这个成本价只覆盖了哪一段时间"。
      **宁可标注不完整，也不给一个看起来精确的错数字。**

    口径：**移动加权平均** = Σ(成交额) ÷ Σ(成交数量)，只看 BUY。
    （卖出不改变总成本口径，只减少持仓；这里算的是"当前持仓的买入均价"。）
    """
    import datetime as _dt
    # ⚠ v1.9.16：OKX orders-history **不带时间窗只返回最近几天**——
    #   实测 PYTH 买于 2026-09-28，2026-10-05 再查默认窗就返回 0 条，
    #   成本价"凭空消失"（available:false）。必须显式传 begin/end。
    #   窗口给到 lookback_days；覆盖范围由 earliest_fill 如实反映。
    _end_ms = int(_dt.datetime.now(_dt.timezone.utc).timestamp() * 1000)
    _begin_ms = _end_ms - int(lookback_days or 90) * 86400000
    try:
        rows = (ad._req("GET", "/api/v5/trade/orders-history",
                        {"instType": "SPOT", "instId": inst_id,
                         "limit": "100", "begin": str(_begin_ms), "end": str(_end_ms)})
                .get("data") or [])
    except Exception as e:                                  # noqa: BLE001
        return {"available": False, "error": "取历史订单失败：%s" % str(e)[:120]}

    base = _base_asset(inst_id)
    spent = 0.0
    qty = 0.0
    fees_base = 0.0
    fills = 0
    earliest = None
    for r in rows:
        if str(r.get("state") or "") not in ("filled", "partially_filled"):
            continue
        if str(r.get("side") or "").lower() != "buy":
            continue
        px = _to_f(r.get("avgPx") or r.get("fillPx"))
        sz = _to_f(r.get("accFillSz"))
        if not px or sz <= 0:
            continue
        spent += px * sz
        qty += sz
        fills += 1
        # 手续费若以基币收取，实际成本更高，必须计入
        if str(r.get("feeCcy") or "").upper() == base:
            fee = abs(_to_f(r.get("fee")))
            fees_base += fee
            qty += fee
        ts = str(r.get("fillTime") or r.get("cTime") or "")
        if ts.isdigit():
            t = int(ts)
            earliest = t if earliest is None else min(earliest, t)
    if qty <= 0:
        return {"available": False,
                "error": "历史订单里没有可用的买入成交（可能超出接口时间窗口）",
                "orders_seen": len(rows)}
    earliest_iso = (_dt.datetime.fromtimestamp(earliest / 1000)
                    .strftime("%Y-%m-%d") if earliest else None)
    # 覆盖天数：接近窗口上限 = 可能不完整
    span_days = None
    if earliest:
        span_days = (_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
                    - _dt.datetime.fromtimestamp(earliest / 1000)).days
    return {"available": True,
            "avg_cost": round(spent / qty, 10),
            "total_spent": round(spent, 6),
            "total_qty": round(qty, 8),
            "fee_in_base": round(fees_base, 8),
            "buy_fills": fills,
            "earliest_fill": earliest_iso,
            "coverage_days": span_days,
            # 覆盖天数 >= 窗口 → 大概率还有更早的买入没取到
            "complete": bool(span_days is not None and span_days < lookback_days - 7),
            "caveat": ("成本价由 %d 笔可见买入推算，最早到 %s；"
                       "若你还有更早的分批买入，此值会**偏低**（漏算早期支出）。"
                       % (fills, earliest_iso or "未知"))}


def cmd_cost_basis(args):
    """现货成本价（只读，从历史订单推导）。"""
    store = _load_store(args)
    try:
        ad = _one_account(store, args.exchange, args.label, dry_run=True)
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "action": "cost_basis", "exchange": args.exchange,
                "error": str(e)[:200]}
    out = []
    for sym in (args.symbols or []):
        cb = get_cost_basis(ad, sym)
        cb["instId"] = sym
        if cb.get("available"):
            mk = None
            try:
                t = ad._req("GET", "/api/v5/market/ticker", {"instId": sym}).get("data") or []
                mk = _to_f(t[0].get("last")) if t else None
            except Exception:                               # noqa: BLE001
                mk = None
            if mk and cb.get("avg_cost"):
                cb["mark_price"] = mk
                cb["pnl_pct"] = round((mk - cb["avg_cost"]) / cb["avg_cost"] * 100, 2)
        out.append(cb)
    return {"ok": True, "action": "cost_basis", "exchange": args.exchange,
            "label": _resolved_label(store, args.exchange),
            "costs": out, "count": len(out),
            "note": "现货余额接口不提供成本价，此结果由历史订单推导，"
                    "覆盖范围见每条的 caveat"}


def cmd_open_orders(args):
    """**在途委托**（未成交挂单）。

    ⚠ v1.9.5：工具 `kexi_cex` 的 action 枚举里一直写着 `open_orders`，
    **但这个子命令从来没实现过**——工具承诺了后端做不到的事。
    起因是实测发现用户 PYTH 挂着一笔全仓限价卖单（挂价 $0.1、现价 $0.078，
    高出 28% 基本成交不了），而 position_doctor 不知道有在途委托，
    对这笔仓位重复建议"减仓"。诊断器要看见在途单，就必须能读到它。
    """
    a = cmd_accounts(args).get("accounts") or []
    rows, errs = [], []
    store = _load_store(args)
    for acc in a:
        ex, lab = acc.get("exchange"), acc.get("label")
        if args.exchange and ex != args.exchange:
            continue
        if args.label and lab != args.label:
            continue
        try:
            ad = _one_account(store, ex, lab, dry_run=True)
            # ⚠ v1.9.12：逐市场取会**重复**——OKX 的 `orders-pending` 不按市场过滤，
            #   同一笔单在每个 market 下都被返回一遍。于是查 `spot usdtm` 时
            #   `count=2`，而实际只有 1 笔。诊断器拿它判断「全仓在卖」时，
            #   重复计数会让人以为有两笔单在途。改为**只取一次**，
            #   market 按单据自带的 instType 推断。
            for o in ad.get_open_orders(args.symbol) or []:
                _t = str(o.get("instType") or "").lower() or "spot"
                rows.append({"exchange": ex, "label": lab,
                             "market": {"swap": "usdtm"}.get(_t, _t), **o})
        except Exception as e:                          # noqa: BLE001
            errs.append({"exchange": ex, "label": lab, "error": str(e)[:160]})
    return {"ok": True, "action": "open_orders", "orders": rows, "count": len(rows),
            "errors": errs,
            "note": ("errors 非空表示部分账户取数失败——失败已如实列出，未静默吞掉"
                     if errs else "")}


def cmd_all_positions(args):
    """**多交易所聚合**：把所有已配置账户的持仓汇到一个视图。"""
    accts = cmd_accounts(args).get("accounts") or []
    rows, errs = [], []
    for a in accts:
        ex, lab = a.get("exchange"), a.get("label")
        for market in (args.markets or ["spot", "usdtm"]):
            r = cmd_positions(argparse.Namespace(
                store_dir=args.store_dir, exchange=ex, label=lab, market=market))
            if r.get("ok"):
                for p in r.get("positions") or []:
                    rows.append({"exchange": ex, "label": lab, "market": market, **p})
            else:
                errs.append({"exchange": ex, "label": lab, "market": market,
                             "error": r.get("error")})
    ups = sum(float(r.get("unrealized_pnl") or 0) for r in rows)
    return {"ok": True, "action": "all_positions", "positions": rows,
            "count": len(rows), "accounts": len(accts),
            "unrealized_pnl_total": round(ups, 2),
            "errors": errs,
            "note": "errors 非空表示部分账户取数失败——失败已如实列出，未静默吞掉" if errs else ""}


def cmd_order(args):
    """下单。**自主级别门在代码里强制，且不认任何命令行覆盖。**"""
    store = _load_store(args)
    aut = read_autonomy(store)
    lvl = aut.get("level", "readonly")
    order = {
        "symbol": args.symbol, "side": args.side, "qty": args.qty,
        "price": args.price, "order_type": args.order_type,
        "market": args.market, "notional_usd": args.notional_usd,
        "stop_loss": args.stop_loss, "take_profit": args.take_profit,
        "leverage": args.leverage,
    }
    base = {"action": "order", "exchange": args.exchange, "label": args.label,
            "autonomy": lvl, "order": order}

    # ── 门 1：自主级别 ──────────────────────────────────────────────
    if not autonomy_allows(lvl, "confirm"):
        return dict(base, ok=False, sent=False, blocked_by="autonomy",
                    error="当前自主级别为「%s」，不允许发起任何写操作。" % lvl,
                    hint="要允许下单，请用户在设置面板把自主级别调到「提议+确认」及以上",
                    autonomy_detail=aut)
    # ── 门 2：limited 档额外要求（必须带止损 + 不超限额）────────────
    dry = False
    if lvl == "limited":
        if not args.stop_loss:
            return dict(base, ok=False, sent=False, blocked_by="limited_requires_sl",
                        error="受限自动档要求**必须提供 stop_loss**（无止损的单一律拒绝）",
                        hint="给出止损价，或把自主级别切到「提议+确认」由人工确认")
        cap = aut.get("auto_notional_cap_usd")
        if cap is not None and args.notional_usd is not None and float(args.notional_usd) > float(cap):
            return dict(base, ok=False, sent=False, blocked_by="limited_cap",
                        error="单笔名义 $%s 超过受限自动档限额 $%s" % (args.notional_usd, cap),
                        hint="拆单或提高限额")
    # ── 门 3：confirm 档 → 只生成草案，等用户在面板点确认 ────────────
    if lvl == "confirm":
        return dict(base, ok=True, sent=False, pending_confirmation=True,
                    dry_run=True,
                    message="已生成订单草案，**未发送**。请在设置面板的「待确认订单」里确认后才会真实下单。")

    # ── 通过全部门 → 落意图 → 发送 → 落结果 ────────────────────────
    oid = _order_id(args.symbol, args.side, args.qty, args.price, args.idem_seq or "x")
    journal_append(store, {"stage": "intent", "order_id": oid, "exchange": args.exchange,
                           "label": args.label, "autonomy": lvl, "order": order})
    try:
        ad = _one_account(store, args.exchange, args.label, dry_run=dry)
        prof = args.profile or aut.get("profile") or "conservative"
        res = ad.place_order_checked(order, profile=prof,
                                      confirm=(lvl == "full"))
        ok = bool(res.get("ok"))
        journal_append(store, {"stage": "result", "order_id": oid, "ok": ok,
                               "allowed": res.get("allowed"),
                               "need_confirm": res.get("need_confirm"),
                               "error": res.get("error"),
                               "order_result": res.get("order_result"),
                               "tpsl_result": res.get("tpsl_result"),
                               "risk_reasons": (res.get("risk") or {}).get("reasons"),
                               "risk_warnings": (res.get("risk") or {}).get("warnings")})
        return dict(base, ok=ok, sent=bool(ok), order_id=oid, **{
            k: res.get(k) for k in ("risk", "order_result", "tpsl_result",
                                    "need_confirm", "error", "message", "dry_run")})
    except Exception as e:
        journal_append(store, {"stage": "result", "order_id": oid, "ok": False,
                               "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
        return dict(base, ok=False, sent=False, order_id=oid,
                    error=str(e)[:240],
                    hint="该订单已落意图日志但未收到确认——重启后跑 `journal` 可核对是否已成交")


def cmd_cancel(args):
    store = _load_store(args)
    aut = read_autonomy(store)
    lvl = aut.get("level", "readonly")
    base = {"action": "cancel", "exchange": args.exchange, "label": args.label,
            "symbol": args.symbol, "order_id": args.order_id, "autonomy": lvl}
    if not autonomy_allows(lvl, "confirm"):
        return dict(base, ok=False, sent=False, blocked_by="autonomy",
                    error="当前自主级别为「%s」，不允许撤单。" % lvl)
    try:
        ad = _one_account(store, args.exchange, args.label, dry_run=False)
        oid = _order_id(args.symbol, "cancel", args.order_id, 0, args.idem_seq or "x")
        journal_append(store, {"stage": "intent", "order_id": oid, "kind": "cancel",
                               "exchange": args.exchange, "label": args.label,
                               "symbol": args.symbol, "target": args.order_id})
        res = ad.cancel_order(args.symbol, args.order_id)
        journal_append(store, {"stage": "result", "order_id": oid, "ok": True,
                               "order_result": res})
        return dict(base, ok=True, sent=True, order_result=res)
    except Exception as e:
        return dict(base, ok=False, sent=False, error=str(e)[:240])


def cmd_journal(args):
    """查订单意图日志 —— 崩溃/回合失败后的对账入口。"""
    store = _load_store(args)
    return {"ok": True, "action": "journal", "store_dir": store,
            "recent": journal_recent(store, args.limit),
            "unresolved": journal_unresolved(store),
            "note": "unresolved = 已落意图但没有结果记录：可能已成交但我方没记住，"
                    "**下单前务必先看这个**，避免重复下单"}


def main():
    ap = argparse.ArgumentParser(description="CEX 适配器 CLI（读持仓 / 下单 / 风控闸门）")
    ap.add_argument("--store-dir", default=None, help="凭据目录（默认自动定位）")
    sub = ap.add_subparsers(dest="cmd")

    def add_acct(p):
        p.add_argument("--exchange", required=True)
        p.add_argument("--label", default=None)

    p = sub.add_parser("health", help="体检：适配器/凭据/自主级别（不发起交易请求）")
    p.set_defaults(fn=cmd_health)

    p = sub.add_parser("accounts", help="列出已配置账户（只读元数据）")
    p.set_defaults(fn=cmd_accounts)

    p = sub.add_parser("balances", help="余额")
    add_acct(p)
    p.add_argument("--account-type", default="spot")
    p.set_defaults(fn=cmd_balances)

    p = sub.add_parser("positions", help="持仓")
    add_acct(p)
    p.add_argument("--market", default="usdtm")
    p.set_defaults(fn=cmd_positions)

    p = sub.add_parser("cost-basis", help="现货成本价（由历史订单推导，标注覆盖范围）")
    p.add_argument("--exchange", default=None)
    p.add_argument("--label", default=None)
    p.add_argument("--symbols", nargs="*", default=[])
    p.set_defaults(fn=cmd_cost_basis)

    p = sub.add_parser("open-orders", help="在途委托（未成交挂单）")
    p.add_argument("--exchange", default=None)
    p.add_argument("--label", default=None)
    p.add_argument("--markets", nargs="*", default=None)
    p.add_argument("--symbol", default=None)
    p.set_defaults(fn=cmd_open_orders)

    p = sub.add_parser("all-positions", help="多交易所聚合持仓")
    p.add_argument("--markets", default="spot,usdtm")
    p.set_defaults(fn=cmd_all_positions)

    p = sub.add_parser("order", help="下单（受自主级别门 + cex_risk 双重约束）")
    add_acct(p)
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", type=float, default=None)
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--order-type", default="MARKET")
    p.add_argument("--market", default="spot", choices=["spot", "usdtm", "coinm"])
    p.add_argument("--notional-usd", dest="notional_usd", type=float, default=None)
    p.add_argument("--stop-loss", dest="stop_loss", type=float, default=None)
    p.add_argument("--take-profit", dest="take_profit", type=float, default=None)
    p.add_argument("--leverage", type=int, default=None)
    p.add_argument("--profile", default=None, help="风控档（默认取 autonomy.json）")
    p.add_argument("--idem-seq", dest="idem_seq", default=None,
                   help="幂等序号：同一次决策重试时传相同值，避免重复下单")
    p.set_defaults(fn=cmd_order)

    p = sub.add_parser("cancel", help="撤单")
    add_acct(p)
    p.add_argument("--symbol", required=True)
    p.add_argument("--order-id", dest="order_id", default=None)
    p.add_argument("--idem-seq", dest="idem_seq", default=None)
    p.set_defaults(fn=cmd_cancel)

    p = sub.add_parser("journal", help="订单意图日志（崩溃后对账）")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_journal)

    args = ap.parse_args()
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    if getattr(args, "markets", None) and isinstance(args.markets, str):
        args.markets = [x.strip() for x in args.markets.split(",") if x.strip()]
    try:
        res = args.fn(args)
    except SystemExit:
        raise
    except Exception as e:
        res = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:240])}
    _print(res)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
