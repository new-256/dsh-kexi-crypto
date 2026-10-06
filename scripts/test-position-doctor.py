#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""position_doctor.py 回归（v1.8.2）：仓位体检。

这个模块的危险不在算错数，而在**给出没有依据的意见**——
「建议你减仓」是最容易说、也最容易害人错的。所以测试的重点不是
"能不能发现问题"，而是：

  ① **没有可观测依据时必须沉默**（缺标记价、regime 置信度不够、研判未接入）
  ② **每条意见都要有 basis 和 invalidation**（不可观测的前提等于没有前提）
  ③ **市场状态不得单独构成减仓理由**（否则就成了循环论证）
  ④ **从不下单**：自主级别只决定是否附草案，草案也带 reduce_only
  ⑤ 权重口径不得被误读成"占账户净值"
"""
import importlib.util
import re
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'preset', 'kexi-crypto', 'skills',
                       'crypto-market-analysis', 'scripts')
DOC = os.path.join(SCRIPTS, 'position_doctor.py')
PY = os.environ.get('KEXI_PYTHON') or 'python'
checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


if not os.path.exists(DOC):
    print('缺少 position_doctor.py')
    sys.exit(1)

# ── 先做源码级越权检查，再 import ────────────────────────────────────────
# 顺序很重要：越权检查放在 import **之后**的话，一旦模块真的引入了
# cex_adapter，本文件会先在 import 阶段崩掉（ModuleNotFoundError），
# 诊断信息只剩一串 traceback，**看不出是哪条纪律被破**。
# 注入实测过：这条断言确实抓得住，但只表现为 exit=1 的崩溃。
SRC = open(DOC, encoding='utf-8').read()
_overreach = ("import cex_adapter" in SRC) or ("from cex_adapter" in SRC) \
    or bool(re.search(r'\b(place_order|create_order|submit_order|post_order)\s*\(', SRC))
ck('⑧0 越权检查可在 import 前完成（诊断不靠崩溃）', True, '')
if _overreach:
    print('  FAIL  ⑧ 模块引入了下单能力（导入前源码检查）')
    print('        命中行：%s' % [l.strip() for l in SRC.splitlines()
                                if 'cex_adapter' in l and 'import' in l][:2])
    print('        position_doctor 只做诊断，下单必须走 cex_adapter 的自主级别闸门。')
    print('test-position-doctor: 中止（越权，无法继续测试其余纪律）')
    sys.exit(1)

spec = importlib.util.spec_from_file_location('pd', DOC)
pd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pd)


def P(sym, size, mark, entry=None, upnl=None, lev=1, liq=None, side="long"):
    return {"symbol": sym, "side": side, "size": size, "mark_price": mark,
            "entry_price": entry, "unrealized_pnl": upnl,
            "leverage": lev, "liq_price": liq}


# ══════════════ ① 分类：稳定币/核心/山寨/杠杆代币 ══════════════
ck('① 稳定币归类正确', pd._kind("USDT") == "stable", pd._kind("USDT"))
ck('① BTC/ETH/BNB 归为核心', pd._kind("BTCUSDT") == "core" and pd._kind("ETHUSDT") == "core")
ck('① 其它归为山寨', pd._kind("PEPEUSDT") == "alt", pd._kind("PEPEUSDT"))
ck('① 杠杆代币 BTCUP/BTCDOWN 识别正确',
   pd._is_leveraged_token("BTCUPUSDT") and pd._is_leveraged_token("BTCDOWNUSDT"), '')
ck('① 普通币不得被误判为杠杆代币',
   not pd._is_leveraged_token("BTCUSDT") and not pd._is_leveraged_token("PEPEUSDT"), '')

# ══════════════ ② 权重口径 ══════════════
hold, total = pd.normalize([P("BTCUSDT", 1, 60000), P("ETHUSDT", 10, 2000)])
ck('② 名义额 = size × mark', total == 80000, str(total))
ck('② 权重为持仓内部占比且和为 1',
   abs(hold[0]["weight"] + hold[1]["weight"] - 1.0) < 1e-9, str([h["weight"] for h in hold]))
ck('② 缺标记价 → 权重为 None，不拿 0 充数',
   pd.normalize([P("XUSDT", 1, None)])[0][0]["weight"] is None, '')
# ⚠ 只测 weight 不够：把 notional 从 None 改成 0.0 时，0 是 falsy，
#   weight 仍然是 None —— 旧断言**照样通过**。真正的可观测差异在
#   notional 本身与"是否记入数据缺口"上。实测踩过。
ck('② 缺标记价 → notional 为 None 而非 0（0 会被当成"这笔没有敞口"）',
   pd.normalize([P("XUSDT", 1, None)])[0][0]["notional"] is None,
   str(pd.normalize([P("XUSDT", 1, None)])[0][0]["notional"]))
_gap = pd.diagnose([P("XUSDT", 1, None), P("YUSDT", 1, 100)], autonomy="readonly")
ck('② 缺标记价 → 如实记入数据缺口（不静默吞掉）',
   any("缺标记价" in g for g in _gap["data_gaps"]), str(_gap["data_gaps"]))
ck('② 缺标记价 → 该仓不计入总额（不把缺失当零）',
   pd.normalize([P("XUSDT", 1, None), P("YUSDT", 1, 100)])[1] == 100, '')

# ══════════════ ③ 各规则能触发，且依据可观测 ══════════════
res = pd.diagnose([P("BTCUSDT", 5, 60000), P("PEPEUSDT", 100000, 0.008)], autonomy="readonly")
ck('③ 单一币种占比 97% → 触发集中度', any(f["rule"] == "单币集中度超限" for f in res["findings"]), '')
ck('③ 集中度判为 high（超上限 3.9 倍）',
   any(f["rule"] == "单币集中度超限" and f["severity"] == "high" for f in res["findings"]), '')

res2 = pd.diagnose([P("BTCUSDT", 1, 60000, lev=5, liq=55000)], autonomy="readonly")
rules2 = {f["rule"] for f in res2["findings"]}
ck('③ 高杠杆触发', "高杠杆仓位集中" in rules2, str(rules2))
ck('③ 强平价过近触发', "强平价过近" in rules2, str(rules2))

res3 = pd.diagnose([P("BTCUSDT", 1, 50000, entry=70000, upnl=-20000)], autonomy="readonly")
rules3 = {f["rule"] for f in res3["findings"]}
# ⚠ 夹具必须让浮亏**真的超过**阈值：pnl_pct = 浮亏 ÷ 保证金。
#   上一版写 size=1/mark=60000/upnl=-3000 → -3000/60000 = -5%，
#   离 -25% 阈值差得远——测的根本不是想测的东西（已踩过一次同类坑）。
ck('③ 深度浮亏触发（-40% > 阈值 -25%）', "浮亏较深且未见止损" in rules3, str(rules3))
ck('③ 浮亏用占保证金比例计算',
   pd.normalize([P("X", 1, 100, entry=200, upnl=-50, lev=1)])[0][0]["pnl_pct"] == -0.5, '')
# 反向：只亏 5% **不该**报——否则这条规则会变成"每笔都报"的噪音
res3b = pd.diagnose([P("BTCUSDT", 1, 60000, entry=63158, upnl=-3000)], autonomy="readonly")
ck('③ 小幅浮亏（-5%）不报（阈值纪律）',
   "浮亏较深且未见止损" not in {f["rule"] for f in res3b["findings"]},
   str({f["rule"] for f in res3b["findings"]}))

# ══════════════ ④ 每条意见都必须有 basis 与 invalidation ══════════════
allsets = [res, res2, res3,
           pd.diagnose([P("PEPEUSDT", 100000, 0.008), P("DOGEUSDT", 50000, 0.02),
                        P("SOLUSDT", 1000, 30, lev=3, liq=27)],
                       regime={"regime": "下行期", "confidence": 0.7}, autonomy="limited")]
bad = []
for r in allsets:
    for f in r["findings"]:
        if not f.get("basis") or not f.get("invalidation") or not f.get("observed"):
            bad.append(f.get("rule"))
ck('④ 每条意见都有 observed/basis/invalidation（可证伪）', not bad, str(bad[:4]))

# ══════════════ ⑤ 沉默原则：数据不足时必须不报 ══════════════
silent = pd.diagnose([], autonomy="readonly")
ck('⑤ 无持仓 → 零条发现，不是崩溃也不是编一条', silent["findings"] == [], '')
ck('⑤ 无持仓时如实记入数据缺口', any("未取得持仓" in g for g in silent["data_gaps"]), '')

# ⚠ 这里的 PEPE 必须**真的重仓**：上一版 size=100000×0.008 = 名义额 $800，
#   权重仅 1.3%，离 10% 门槛太远——把置信度门槛从 0.5 改成 0 也照样通过，
#   那条纪律实际上从没被测到（注入实测确认）。
lowconf = pd.diagnose([P("PEPEUSDT", 10000000, 0.008), P("BTCUSDT", 1, 60000)],
                      regime={"regime": "下行期", "confidence": 0.3}, autonomy="readonly")
ck('⑤ regime 置信度 0.3（重仓山寨在场）→ 不用它下判断',
   not any("下行期" in f["rule"] for f in lowconf["findings"]),
   str([f["rule"] for f in lowconf["findings"]]))
# 反向：置信度够时**必须**报，否则就成了"永远不报"
hiconf = pd.diagnose([P("PEPEUSDT", 10000000, 0.008), P("BTCUSDT", 1, 60000)],
                     regime={"regime": "下行期", "confidence": 0.8}, autonomy="readonly")
ck('⑤ 置信度 0.8 时同一组合**必须**报（门槛不是形同虚设）',
   any("下行期" in f["rule"] for f in hiconf["findings"]),
   str([f["rule"] for f in hiconf["findings"]]))
ck('⑤ 置信度不足要写进数据缺口', any("置信度" in g for g in lowconf["data_gaps"]), '')

noreg = pd.diagnose([P("PEPEUSDT", 100000, 0.008), P("BTCUSDT", 1, 60000)], autonomy="readonly")
ck('⑤ 未接 regime → 状态检查未启用并说明',
   any("未接入 regime" in g for g in noreg["data_gaps"]), str(noreg["data_gaps"]))
ck('⑤ 未接单币研判 → 单币冲突检查未启用并说明',
   any("未接入单币研判" in g for g in noreg["data_gaps"]), '')

# ══════════════ ⑥ 市场状态不得单独构成减仓理由 ══════════════
# 均衡持仓 + 下行期：不产生"因为要跌所以卖"，只在有重仓山寨时提示复核
flat = pd.diagnose([P("BTCUSDT", 1, 60000), P("ETHUSDT", 5, 2000)],
                   regime={"regime": "下行期", "confidence": 0.8}, autonomy="readonly")
ck('⑥ 均衡持下行期 → 不因市场状态本身报问题',
   not any("下行期" in f["rule"] for f in flat["findings"]),
   str([f["rule"] for f in flat["findings"]]))
# ⚠ 同样要让 PEPE **真的重仓**：上一版 size=100000×0.008 = 名义额 $800，
#   对上 BTC 的 $60000 只有 1.3% 权重，离 10% 门槛差得远。
altheavy = pd.diagnose([P("PEPEUSDT", 10000000, 0.008), P("BTCUSDT", 1, 60000)],
                       regime={"regime": "下行期", "confidence": 0.8}, autonomy="readonly")
regf = [f for f in altheavy["findings"] if "下行期" in f["rule"]]
ck('⑥ 下行期 + 重仓山寨 → 提示复核（而非直接减仓）',
   bool(regf) and regf[0]["proposed"]["action"] == "review",
   str([(f["rule"], f["proposed"]["action"]) for f in altheavy["findings"]]))
ck('⑥ 该条明说市场状态不构成减仓理由',
   bool(regf) and any("不构成减仓理由" in b for b in regf[0]["basis"]), '')

# 对冲腿豁免：上行期持空头应提示"别因转多就平掉对冲"
hedge = pd.diagnose([P("BTCUSDT", 1, 60000), P("BTCUSDT", 1, 60000, side="short")],
                    regime={"regime": "上行期", "confidence": 0.8}, autonomy="readonly")
hf = [f for f in hedge["findings"] if f["rule"] == "上行期持有空头"]
ck('⑥ 上行期空头：提醒对冲腿别被平掉',
   hf and any("对冲" in f["invalidation"] for f in hf),
   str(hf[0]["invalidation"])[:70] if hf else 'no finding')

# ══════════════ ⑦ 自主级别：只决定草案，不决定诊断 ══════════════
pos = [P("BTCUSDT", 5, 60000), P("ETHUSDT", 5, 2000)]
r_ro = pd.diagnose(pos, autonomy="readonly")
r_lim = pd.diagnose(pos, autonomy="limited")
ck('⑦ readonly → 全部 advice_only 且无订单草案',
   all(f["advice_only"] and f["order_draft"] is None for f in r_ro["findings"]), '')
ck('⑦ 非 readonly → 才附订单草案',
   any(f["order_draft"] for f in r_lim["findings"]), '')
ck('⑦ 草案一律 reduce_only（不得反向开仓）',
   all(f["order_draft"]["reduce_only"] for f in r_lim["findings"] if f["order_draft"]), '')
ck('⑦ 草案标明仍须过 cex_adapter 闸门',
   all("cex_adapter" in f["order_draft"]["note"] for f in r_lim["findings"] if f["order_draft"]), '')
ck('⑦ 诊断条数与自主级别无关（只读也要给意见）',
   len(r_ro["findings"]) == len(r_lim["findings"]),
   '%d vs %d' % (len(r_ro["findings"]), len(r_lim["findings"])))

# ══════════════ ⑧ 绝不越权：模块内不得出现下单调用 ══════════════
src = open(DOC, encoding='utf-8').read()
ck('⑧ 模块不 import cex_adapter（无下单能力）',
   "import cex_adapter" not in src and "from cex_adapter" not in src, '')
ck('⑧ 模块不出现 place_order / submit 等下单调用',
   not re.search(r'\b(place_order|create_order|submit_order|post_order)\s*\(', src), '')

# ══════════════ ⑨ 权重口径必须自我声明，防止被误读 ══════════════
ck('⑨ 输出显式声明权重是"持仓内部占比"非净值占比',
   "非占账户净值比例" in src or "非占账户净值比例" in r_ro["weight_basis"], r_ro["weight_basis"][:60])
ck('⑨ 输出显式声明本模块从不下单', "从不下单" in r_ro["note"], '')

# ══════════════ ⑩ CLI：吃 cex_adapter 的真实输出形状 ══════════════
tmp = tempfile.mkdtemp(prefix='kexi-pd-')
pj = os.path.join(tmp, 'positions.json')
# cex_adapter cmd_positions 的实际形状：{ok, action, exchange, positions:[...], count, ...}
with open(pj, 'w', encoding='utf-8') as f:
    json.dump({"ok": True, "action": "positions", "exchange": "binance",
               "positions": [P("BTCUSDT", 5, 60000, lev=1), P("PEPEUSDT", 100000, 0.008)],
               "count": 2, "unrealized_pnl_total": 0}, f, ensure_ascii=False)
outp = os.path.join(tmp, 'pd.json')
r = subprocess.run([PY, DOC, '--positions', pj, '--out', outp, '--autonomy', 'limited'],
                   capture_output=True, text=True, timeout=60)
ck('⑩ CLI 吃 {ok,positions:[...]} 形状跑通', r.returncode == 0, r.stderr[-200:])
if os.path.exists(outp):
    doc = json.load(open(outp, encoding='utf-8'))
    ck('⑩ stdout 末行是紧凑 JSON（供 lastJsonLine）',
       json.loads(r.stdout.strip().splitlines()[-1]).get('ok') is True, r.stdout[-100:])
    ck('⑩ schema 正确', doc.get('schema') == "kexi.position_doctor/1", str(doc.get('schema')))
    ck('⑩ CLI 产出了集中度发现',
       any(f["rule"] == "单币集中度超限" for f in doc["findings"]), '')
# 裸数组也要能吃
pj2 = os.path.join(tmp, 'p2.json')
with open(pj2, 'w', encoding='utf-8') as f:
    json.dump([P("BTCUSDT", 1, 60000)], f)
r2 = subprocess.run([PY, DOC, '--positions', pj2, '--out', os.path.join(tmp, 'p2o.json')],
                    capture_output=True, text=True, timeout=60)
ck('⑩ 也吃裸数组（position_doctor 自己存过的那种）', r2.returncode == 0, r2.stderr[-160:])

# ══════════════ ⑪ 在途委托（v1.9.5）═════════════════════════════════════
# 起因：真实账户里 PYTH 挂着全仓限价卖单（挂 $0.1 / 现价 $0.0774，高 29.1%），
# 而体检不知道，对这笔仓位重复建议"减仓"——而且掩盖了真正该说的事。
ORD = lambda **kw: dict({"instId": "PYTH-USDT", "side": "sell", "sz": "100",
                         "px": "0.1", "state": "live", "accFillSz": "0"}, **kw)
pos = [P("PYTH-SPOT", 100, 0.0774)]
r_o = pd.diagnose(pos, open_orders=[ORD()], autonomy="readonly")
rules_o = {f["rule"] for f in r_o["findings"]}
ck('⑪ 在途挂价远离市价 → 报 high', "挂单价高于市价，很可能永不成交" in rules_o, str(rules_o))
ck('⑪ 减仓建议与在途委托重复 → 报（不再重复建议）',
   "减仓建议与在途委托重复" in rules_o, str(rules_o))
_gapf = [f for f in r_o["findings"] if "挂单价高于市价" in f["rule"]]
ck('⑪ 挂高幅度算对（0.1 / 0.0774 → 约 29%）',
   bool(_gapf) and "29." in _gapf[0]["observed"], str([f["observed"] for f in r_o["findings"]]))
# ⚠ symbol 归一：持仓叫 `PYTH-SPOT`、instId 叫 `PYTH-USDT`，不归一就一条都对不上（实测踩过）
ck('⑪ symbol 归一：`-SPOT` 与 `-USDT` 能对上',
   bool(_gapf) and _gapf[0].get("symbol") == "PYTH-SPOT",
   str([(f["rule"], f.get("symbol")) for f in r_o["findings"]]))
# 挂价贴近市价 → 不该报
r_ok = pd.diagnose(pos, open_orders=[ORD(px="0.0779")], autonomy="readonly")
ck('⑪ 挂价贴近市价（0.0779）不报',
   "挂单价高于市价，很可能永不成交" not in {f["rule"] for f in r_ok["findings"]},
   str({f["rule"] for f in r_ok["findings"]}))
# 已成交/已撤销的不算在途
r_done = pd.diagnose(pos, open_orders=[ORD(state="filled")], autonomy="readonly")
ck('⑪ 已成交的委托不算在途', "减仓建议与在途委托重复" not in {f["rule"] for f in r_done["findings"]}, '')
r_cancel = pd.diagnose(pos, open_orders=[ORD(state="canceled")], autonomy="readonly")
ck('⑪ 已撤销的委托不算在途', "减仓建议与在途委托重复" not in {f["rule"] for f in r_cancel["findings"]}, '')
# 卖单数量远小于持仓 → 不是"全仓在卖"，不该报重复
r_part = pd.diagnose([P("PYTH-SPOT", 100, 0.0774)],
                     open_orders=[ORD(sz="10")], autonomy="readonly")
ck('⑪ 只卖了一小部分不算"全仓在卖"',
   "减仓建议与在途委托重复" not in {f["rule"] for f in r_part["findings"]}, '')
# 未接入时必须自报数据缺口
r_none = pd.diagnose(pos, autonomy="readonly")
ck('⑪ 未接入在途委托时如实报数据缺口',
   any("在途委托" in g for g in r_none["data_gaps"]), str(r_none["data_gaps"]))
# 适配器必须有 open-orders 子命令（工具 action 枚举早就写了 open_orders）
_src = open(os.path.join(SCRIPTS, 'cex_adapter.py'), encoding='utf-8').read()
ck('⑪ 适配器 CLI 真有 open-orders 子命令（工具承诺过、后端却没实现）',
   'add_parser("open-orders"' in _src and 'def cmd_open_orders' in _src, '')
ck('⑪ diagnose 接受 open_orders 参数',
   'open_orders=None' in open(DOC, encoding='utf-8').read(), '')
# ══════════════ 汇总 ══════════════
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-position-doctor: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
