#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G7 解锁日历 `--verify-supply` 离线回归（零网络）。

背景：2026-09-30 实测确认**没有任何免 key 源能给出「日期+数量」的解锁事件明细**
（DefiLlama emissions 已转 402 付费墙，TokenUnlocks/CryptoRank/Tokenomist 需 key 或不可达，
详见 docs/unlock-source-probe-2026-09-30.md）。因此日历仍是手工录入，脚本只新增
「用汇总供应量校验 unlock_pct 自洽性」的能力——**只校验，不取日期**。

本测试完全离线：monkeypatch 掉 http_get_json 注入 fixture，任何未登记的 URL 直接报错，
顺带保证测试不会偷偷联网。铁律：取不到必须是 None/False，绝不能变成 0 冒充真实值。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                       'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts')
sys.path.insert(0, SCRIPTS)
import unlock_schedule as us   # noqa: E402

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


# ── fixture：只登记测试用到的 URL，其它一律报错（保证零网络）──
_NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _day(offset):
    return (_NOW + timedelta(days=offset)).strftime("%Y-%m-%d")


CG_COIN = {
    "id": "doublezero", "symbol": "2z", "name": "DoubleZero",
    "last_updated": "2026-09-30T05:57:50.000Z",
    "market_data": {
        "circulating_supply": 5112583036.0,
        "total_supply": 9998069911.0,
        "max_supply": 10000000000.0,
    },
}
CG_COIN_NO_SUPPLY = {"id": "nosupply", "symbol": "nosupply", "name": "No Supply",
                     "market_data": {"circulating_supply": None, "total_supply": None,
                                     "max_supply": None}}
FIXTURES = {
    "https://api.coingecko.com/api/v3/search?query=2Z": {
        "coins": [{"id": "doublezero", "symbol": "2Z", "name": "DoubleZero"}]},
    "https://api.coingecko.com/api/v3/search?query=ZZZ": {
        "coins": [{"id": "zzz-one", "symbol": "ZZZ", "name": "ZZZ One"},
                  {"id": "zzz-two", "symbol": "ZZZ", "name": "ZZZ Two"}]},
    "https://api.coingecko.com/api/v3/search?query=AMBI": {
        "coins": [{"id": "amb", "symbol": "AMB1", "name": "Amb One"},
                  {"id": "bgm", "symbol": "AMB2", "name": "Bg Two"}]},
    "https://api.coingecko.com/api/v3/search?query=BOOM": None,   # 模拟取数失败
    ("https://api.coingecko.com/api/v3/coins/doublezero?localization=false&tickers=false"
     "&market_data=true&community_data=false&developer_data=false&sparkline=false"): CG_COIN,
    ("https://api.coingecko.com/api/v3/coins/nosupply?localization=false&tickers=false"
     "&market_data=true&community_data=false&developer_data=false&sparkline=false"): CG_COIN_NO_SUPPLY,
}

_orig_http = us.http_get_json


def _fake_http(url, timeout=None, retries=None):
    if url in FIXTURES:
        doc = FIXTURES[url]
        if doc is None:
            raise RuntimeError("模拟网络失败")
        return json.loads(json.dumps(doc))
    raise AssertionError(f"测试禁止访问未登记 URL（可能联网）: {url}")


us.http_get_json = _fake_http

# ── 1. registry 原路径零回归（不联网）──
reg = us.load_registry(us.BUILTIN_REGISTRY, "2Z")
ck("registry 仍能载入 2Z", reg is not None)
res = us.analyze(reg, now=_NOW)
ck("registry 路径 events=2", len(res["events"]) == 2, str(len(res["events"])))
ck("registry 路径 data_source 仍为 manual", res["data_source"] == "manual", res["data_source"])
ck("未加校验时无 supply_check", "supply_check" not in res)
ck("打分逻辑未被改写（178M/1e9=17.8%）",
   abs(res["events"][0]["unlock_pct"] - 17.8) < 0.01, str(res["events"][0]["unlock_pct"]))

# ── 2. 常驻诚实边界声明 ──
ck("warnings 必带「免 key 源无带日期明细」声明",
   any("免 key 源无法提供带日期的解锁明细" in w for w in res["warnings"]), str(res["warnings"])[:80])
ck("registry 查无此币返回 None", us.load_registry(us.BUILTIN_REGISTRY, "NOPE-NOT-A-COIN") is None)

# ── 3. 免 key 供应量取数 ──
sp = us.fetch_supply("2Z", cg_id="doublezero")
ck("显式 cg_id 走 explicit 分支", sp["resolution"] == "explicit_cg_id", str(sp["resolution"]))
ck("供应量取到（CoinGecko fixture）", sp["available"] is True)
ck("circulating 数值正确", sp["circulating_supply"] == 5112583036.0, str(sp["circulating_supply"]))
ck("total 数值正确", sp["total_supply"] == 9998069911.0, str(sp["total_supply"]))

sp2 = us.fetch_supply("2Z")
ck("唯一完全同代码自动解析", sp2["gecko_id"] == "doublezero" and sp2["resolution"] == "auto_exact_symbol",
   f"{sp2['gecko_id']}/{sp2['resolution']}")

sp3 = us.fetch_supply("ZZZ")
ck("多个完全同代码 → 不猜（None）", sp3["gecko_id"] is None and sp3["available"] is False,
   str(sp3["gecko_id"]))
ck("歧义时提示用 --cg-id", any("--cg-id" in w for w in sp3["warnings"]), str(sp3["warnings"])[:80])

sp4 = us.fetch_supply("AMBI")
ck("多结果且无完全匹配 → 不猜", sp4["gecko_id"] is None, str(sp4["gecko_id"]))

sp5 = us.fetch_supply("BOOM")
ck("检索失败 → None + 如实说明", sp5["gecko_id"] is None and bool(sp5["warnings"]),
   str(sp5["warnings"])[:80])

sp6 = us.fetch_supply("X", cg_id="nosupply")
ck("源端无供应字段 → available=False", sp6["available"] is False)
ck("源端无供应字段绝不填 0 冒充",
   sp6["circulating_supply"] is None and sp6["total_supply"] is None and sp6["max_supply"] is None,
   str(sp6))

# ── 4. 交叉校验：抓出总量口径 10 倍偏差（实测 2Z 真实冲突）──
payload = {"symbol": "2Z", "total_supply": 1_000_000_000,
           "events": [{"date": _day(1), "name": "团队", "unlock_tokens": 178_000_000}]}
r4 = us.analyze(payload, now=_NOW)
chk = us.verify_supply(payload, r4, us.fetch_supply("2Z", cg_id="doublezero"))
ck("校验块标记 available", chk["available"] is True)
ck("识别出登记总量与源端差 10 倍", chk["total_supply_delta_pct"] < -89.9, str(chk["total_supply_delta_pct"]))
ck("不一致时 consistent=False", chk["consistent"] is False)
ck("未流通量 = total - circ", abs(chk["non_circulating"] - 4885486875.0) < 1,
   str(chk["non_circulating"]))
ck("覆盖度 <50% 触发漏登提示", chk["coverage_of_non_circulating_pct"] < 50
   and any("可能漏登" in w for w in chk["warnings"]), str(chk["coverage_of_non_circulating_pct"]))
ck("校验结论写进 res.warnings",
   any("总供应量口径不自洽" in w for w in r4["warnings"]), str(r4["warnings"])[:120])
ck("校验不改写原有打分（仍用登记量）", abs(r4["events"][0]["unlock_pct"] - 17.8) < 0.01,
   str(r4["events"][0]["unlock_pct"]))

# ── 5. 单事件解锁量超总量 ──
bad = {"symbol": "ZZZ", "total_supply": 1_000,
       "events": [{"date": _day(2), "name": "离谱事件", "unlock_tokens": 5000}]}
r5 = us.analyze(bad, now=_NOW)
chk5 = us.verify_supply(bad, r5, {"available": False, "source": "coingecko", "errors": ["mock 失败"]})
ck("事件量超总量被点名", any("超过登记总供应量" in w for w in chk5["warnings"]), str(chk5["warnings"])[:100])

# ── 6. 取数失败必须诚实降级（不静默通过）──
ck("取不到供应量时 available=False", chk5["available"] is False)
ck("取不到供应量时写明未做外部校验",
   any("未对 unlock_pct 做外部校验" in w for w in chk5["warnings"]), str(chk5["warnings"])[:100])
ck("取不到时非流通量/覆盖度为 None（不编造）",
   chk5["non_circulating"] is None and chk5["coverage_of_non_circulating_pct"] is None,
   str(chk5["non_circulating"]))
ck("取不到时 consistent 不谎报 True", chk5["consistent"] is not True, str(chk5["consistent"]))
ck("取数错误被如实转录", any("mock 失败" in e for e in r5["errors"]), str(r5["errors"])[:80])

# ── 7. 未来事件汇总口径：已解锁（days<0）不计入覆盖度 ──
mix = {"symbol": "2Z", "total_supply": 1_000_000_000,
       "events": [{"date": _day(-10), "name": "已解锁", "unlock_tokens": 400_000_000},
                  {"date": _day(3), "name": "未来", "unlock_tokens": 100_000_000}]}
r7 = us.analyze(mix, now=_NOW)
chk7 = us.verify_supply(mix, r7, us.fetch_supply("2Z", cg_id="doublezero"))
ck("覆盖度只算未来事件", abs(chk7["registered_future_unlock_tokens"] - 100_000_000) < 1,
   str(chk7["registered_future_unlock_tokens"]))

us.http_get_json = _orig_http   # 复原，避免影响同进程其它用例

ok = sum(1 for _, c, _ in checks if c)
print('=' * 72)
print(f'G7 解锁日历 --verify-supply 离线回归：{ok}/{len(checks)} 通过')
print('=' * 72)
for n, c, e in checks:
    print(f'  {"✓" if c else "✗"} {n}')
    if not c and e:
        print(f'      → {e}')
sys.exit(0 if ok == len(checks) else 1)
