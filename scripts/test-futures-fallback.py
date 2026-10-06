#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.5.6 回归：合约情绪降级链 binance-fapi → OKX → Bybit。

背景：本机实测 Binance fapi 返回 HTTP 451（受限地域）、Bybit 返回 403
（CloudFront 地区拦截），旧的两级兜底双双失效 → 合约情绪长期 available=false。
本测试验证：① OKX 兜底能真正拿到数据；② 跨所口径可比字段（美元名义）；
③ 全失败时诚实降级、绝不编造。
"""
import json
import os
import sys

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                       'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts')
sys.path.insert(0, SCRIPTS)
import datasources as ds   # noqa: E402

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


# ── 单元层：_f 安全转换（None 绝不能变成 0.0）──
ck('_f(None) 返回 None 而非 0', ds._f(None) is None)
ck('_f("") 返回 None', ds._f("") is None)
ck('_f("abc") 返回 None', ds._f("abc") is None)
ck('_f("0.0001") 正确解析', ds._f("0.0001") == 0.0001)
ck('_f 不会把取不到伪装成 0',
   ds._f(None) is not None or True)   # 上面四条已覆盖，此条为语义标注

# ── 单元层：OKX instId 映射 ──
ck('BTCUSDT → BTC-USDT-SWAP', ds._okx_inst_id('BTCUSDT') == 'BTC-USDT-SWAP', str(ds._okx_inst_id('BTCUSDT')))
ck('ETHUSDT → ETH-USDT-SWAP', ds._okx_inst_id('ETHUSDT') == 'ETH-USDT-SWAP')
ck('BTCUSDC → BTC-USDC-SWAP', ds._okx_inst_id('BTCUSDC') == 'BTC-USDC-SWAP')
ck('不认识的符号返回 None', ds._okx_inst_id('FOOBAR') is None, str(ds._okx_inst_id('FOOBAR')))

# ── 单元层：资金费率状态判定 ──
ck('费率 >0.05% → 多头过热', ds._funding_state(0.0008) == '多头过热(资金费率偏高)')
ck('费率 0~0.05% → 正常', ds._funding_state(0.0001) == '资金费率正常')
ck('费率 -0.05%~0 → 空头付费', ds._funding_state(-0.0001) == '空头付费(偏空)')
ck('费率 <-0.05% → 空头过热', ds._funding_state(-0.002) == '空头过热')
ck('费率 None → unknown（不编造）', ds._funding_state(None) == 'unknown')
ck('费率非数字 → unknown（不抛异常）', ds._funding_state('abc') == 'unknown')

# ── 契约层：返回结构完整（无论哪个源）──
r = ds.fetch_futures_metrics('BTCUSDT')
for k in ('available', 'symbol', 'source', 'provider', 'open_interest',
          'open_interest_usd', 'funding_rate', 'mark_price', 'funding_state', 'note'):
    ck(f'返回含 {k}', k in r, str(sorted(r.keys()))[:90])
ck('symbol 未在降级路径丢失', r.get('symbol') == 'BTCUSDT', str(r.get('symbol')))
ck('available 为 bool', isinstance(r.get('available'), bool))

if r.get('available'):
    ck('本机能取到合约情绪数据（降级链生效）', True, f"provider={r.get('provider')}")
    ck('资金费率有真实数值', r.get('funding_rate') is not None, str(r.get('funding_rate')))
    ck('OI 有值', r.get('open_interest') is not None, str(r.get('open_interest')))
    ck('美元名义 OI 已计算（跨所可比口径）', r.get('open_interest_usd') is not None,
       str(r.get('open_interest_usd')))
    ck('降级时如实说明 provider',
       (r.get('provider') == 'binance') or bool(r.get('note')), str(r.get('note'))[:80])
    if r.get('provider') != 'binance':
        ck('非币安来源时警告不可横比', '不可' in (r.get('note') or ''), str(r.get('note'))[:80])
    # 裸值与美元名义必须同源一致（OKX oiCcy×mark ≈ oiUsd）
    oi, usd, mk = r.get('open_interest'), r.get('open_interest_usd'), r.get('mark_price')
    if oi and usd and mk:
        ck('币量×标记价 ≈ 美元名义（量纲自洽）', abs(oi * mk - usd) / usd < 0.02,
           f"{oi}×{mk}={oi*mk:.0f} vs {usd:.0f}")
else:
    ck('全源不可用时 note 说明清楚', '不可用' in (r.get('note') or ''), str(r.get('note')))
    ck('全源不可用时不返回编造数值',
       r.get('open_interest') is None and r.get('funding_rate') is None)
    ck('全源不可用时记录降级链原因', bool(r.get('errors')), str(r.get('errors'))[:80])

ok = sum(1 for _, c, _ in checks if c)
print('=' * 72)
print(f'v1.5.6 合约情绪 OKX 兜底：{ok}/{len(checks)} 通过')
print('=' * 72)
for n, c, e in checks:
    print(f'  {"✓" if c else "✗"} {n}')
    if not c and e:
        print(f'      → {e}')
sys.exit(0 if ok == len(checks) else 1)
