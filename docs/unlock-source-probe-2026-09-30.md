# 解锁日历免费源探测矩阵（2026-09-30 实测）

> 背景：待办「解锁日历接免费源 —— `unlock_schedule.py` 骨架已在但 fetchers 缺，
> TokenUnlocks 有公开接口，值得先探测能否免 key。**先探测再决定**」。
> 本文记录 2026-09-30 在本机（Clash 代理 `127.0.0.1:7890`，出网节点会轮换）
> 对全部候选免 key 源做的真实网络探测。
>
> **探测纪律**：每个 URL 最多尝试 3 次（单次失败不作结论）；SSL/HTTP 错误均记录原始响应体。
>
> **结论（一句话）**：**没有任何免 key 源能提供「日期 + 数量」的解锁事件明细。**
> 能免费拿到的只有**汇总供应量**（流通量 / 总供应 / 最大供应），
> 所以解锁日历本身仍为手工录入，工具新增的只是「用汇总供应量校验 unlock_pct 自洽性」。

---

## 一、探测矩阵

| # | 源 | URL | 状态 | 响应结构 / 关键字段 | 有无带日期的解锁明细 |
|---|---|---|---|---|---|
| 1 | DefiLlama | `https://api.llama.fi/unlocks` | **404** | — | ❌ 端点不存在 |
| 2 | DefiLlama | `https://api.llama.fi/emissions` | **402** | 响应体原文：`Upgrade to the paid API plan at https://defillama.com/subscription` | ❌ **已转付费墙** |
| 3 | DefiLlama | `https://api.llama.fi/emission/defillama` | **402** | 同上（付费墙） | ❌ 已转付费墙 |
| 4 | DefiLlama | `https://api.llama.fi/raises` | **402** | 同上（付费墙） | ❌ 已转付费墙 |
| 5 | DefiLlama | `https://defillama-datasets.llama.fi/emissions` `/unlocks` `/emissionsUnlockSchedules` `/` | **404** ×4 | Cloudflare Not Found 页 | ❌ 旧数据集路径已下线 |
| 6 | DefiLlama | `https://defillama.com/unlocks`（页面） | **403** | Cloudflare `Just a moment...` 挑战 | ❌ 页面级反爬 |
| 7 | DefiLlama | `https://api.llama.fi/protocols` | 200（9.0 MB / 8421 条） | 字段：`id/name/slug/symbol/mcap/tvl/chainTvls/...` | ❌ 全量正则命中 `unlock`×136、`vesting`×296 **全部落在协议名或描述文本**（Sablier、Unlock Protocol、Streamflow 等），无任何解锁计划字段 |
| 8 | DefiLlama | `https://api.llama.fi/protocol/doublezero` | 200（101 KB） | 字段含 `raises`（融资轮次，有 date）、`tvl`、`tokens` | ❌ `unlock` 正则命中 **0**；`raises` 是融资不是解锁 |
| 9 | TokenUnlocks | `https://api.tokenunlocks.ai/` | **SSL 失败** ×3 | `SSL: UNEXPECTED_EOF_WHILE_READING` | ❌ 不可达 |
| 10 | TokenUnlocks | `https://api.tokenunlocks.ai/v1/tokens?symbol=BTC` | **SSL 失败** ×3 | 同上 | ❌ 不可达 |
| 11 | CryptoRank | `https://api.cryptorank.io/v1/price?symbol=BTC` | **401** | nginx `401 Authorization Required` | ❌ 需 key |
| 12 | Tokenomist | `https://api.tokenomist.ai/v1/token/1` | **401** | `{"errorMessage":"x-api-key not found"}` | ❌ 需 key |
| 13 | CoinMarketCal | `https://api.coinmarketcal.com/` | **403** | `{"message":"Forbidden"}` | ❌ 不可用 |
| 14 | VestLabs | `https://api.vestlabs.xyz/api/v1/unlock` | **SSL 失败** | `TLSV1_UNRECOGNIZED_NAME` | ❌ 不可达 |
| 15 | CoinGecko | `https://api.coingecko.com/api/v3/coins/doublezero` | 200（28 KB） | 顶层 38 字段（含 `market_data`）；**`unlock`/`vesting`/`cliff` 正则命中 0**；`has_supply_breakdown: false` | ❌ 免费档**完全没有**解锁字段；只有 `circulating_supply` / `total_supply` / `max_supply` |
| 16 | CoinGecko | `.../coins/bitcoin/circulating_supply` | **404** | `{"error":"Incorrect path"}` | ❌ 该路径不存在 |
| 17 | CoinGecko | `.../coins/doublezero/supply_chart` | **404** | `{"error":"Incorrect path"}` | ❌ 供应量分解属付费档 |
| 18 | CoinGecko | `.../coins/list?include_platform=true` | 200（3.9 MB / 21708 条） | `id/symbol/name/platforms` | ❌ 仅符号映射，无解锁 |
| 19 | Binance | `https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list` | 200（857 KB / 681 token） | `totalSupply` / `circulatingSupply` / `fdv` / `marketCap` / `listingTime` / `holders` | ❌ **仅汇总供应量** |
| 20 | Binance | `https://api.binance.com/sapi/v1/capital/config/getall` | **400** | `{"code":-2014,"msg":"API-key format invalid."}` | ❌ 需 key |
| 21 | Binance | `https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?...` | 200 | 公告目录（catalogs/articles） | ❌ 非结构化 HTML 公告，无日期+数量字段 |
| 22 | Blockscout v2 | `https://eth.blockscout.com/api/v2/tokens/0x6B17...1d0F` | 200 | `total_supply` / `exchange_rate` / `holders_count` | ❌ 仅汇总供应量 |
| 23 | Blockscout 旧版 | `https://eth.blockscout.com/api?module=proxy&action=eth_call&to=...&data=0x18160ddd` | **400** | `{"message":"Unknown module"}` | ❌ 免 key **无法通用读 vesting 合约**（排除「链上直接读归属曲线」这条路） |
| 24 | Solana RPC | `https://api.mainnet-beta.solana.com` `getTokenSupply` | 200 | `value.amount` / `decimals` / `uiAmountString` | ❌ 仅汇总供应量 |
| 25 | L2Beat | `https://l2beat.com/api/scaling/summary` | 200（274 KB） | `projects.*.stage` / `tvs` / `risks` | ❌ 无解锁计划 |

---

## 二、按结论做了什么（第二阶段）

| 结论 | 实施 |
|---|---|
| 无免 key 源给带日期的解锁明细 | **不加 `--fetch`**。加了就是"看起来能用"的假日历，违反项目铁律 |
| 有免 key 汇总供应量（CoinGecko 免费档，实测可用） | 加 `--verify-supply`：取 `circulating_supply` / `total_supply` / `max_supply`，**只校验手工录入的 `unlock_pct` 是否自洽**，绝不反推解锁日期 |
| 取不到（限流 / 断网 / 无字段） | `supply_check.available=false` + warnings 明说"未对 unlock_pct 做外部校验"，字段留 `None` 而非 `0` |
| 恒定边界 | `analyze()` 的 `warnings` 常驻一条声明：免 key 源无法提供带日期的解锁明细，当前日历为手工录入 |

`--verify-supply` 校验三件事：

1. **总量口径**：registry 的 `total_supply` vs 源端 `total_supply`（缺失回落 `max_supply`），偏差 >5% 即报不自洽；
2. **单事件合理性**：任一事件 `unlock_tokens` 超过登记总量即点名该事件；
3. **日历覆盖度**：已登记的**未来**解锁量 ÷（源端总量 − 流通量），<50% 报"可能漏登"，>150% 报"与流通量口径矛盾"。

代码内校验 CoinGecko 代币 id 时**只做唯一性判定**（完全同代码唯一 / 全结果唯一才自动采用，
否则要求 `--cg-id`），多命中绝不瞎猜。

---

## 三、探测顺手挖出的一个真实数据问题（重要）

2Z（DoubleZero）在 registry 里登记的 `total_supply = 1_000_000_000`，但两个互不相关的免 key 源
都说总量是 **100 亿**：

| 来源 | total / max | circulating | 取数时间 |
|---|---|---|---|
| CoinGecko `doublezero` | total `9,998,069,911` / max `10,000,000,000` | `5,112,583,036` | 2026-09-30T05:57:50Z |
| Binance Alpha bapi（DoubleZero / 2Z / Solana） | totalSupply `10000000000` | `3471417500` | 2026-09-30 |

**后果**：registry 的 178,000,000 事件在 1e9 口径下算出 `17.8% → 冲击高 + 风险旗`；
若真实总量是 1e10，同一笔解锁只占 `1.78%`，冲击等级应显著下调。**当前"高冲击"预警很可能是
总量口径错误造成的假警报。**

同时，未来已登记解锁量 258,000,000 只占 CoinGecko 口径未流通量（4,885,486,875）的 **5.3%**，
说明即使总量改对，**日历本身也远未登记完整**。

`--verify-supply` 正是为这类问题准备的：它**不自动改写 registry**（改数据要人确认），
只把冲突作为 warning 顶到输出里。**是否把 registry 的 `total_supply` 改为 1e10、事件数量是否需要
重新核实，需要人工判断，本次未擅自改动。**

---

## 四、诚实边界（务必保留）

- 解锁日历里的**日期与数量目前全部来自人工录入**，两个免 key 源都无法核实；
- `--verify-supply` 只能证明"总量口径对不对、覆盖度够不够"，**不能证明某笔解锁事件为真**；
- 想拿到权威的带日期解锁明细，只有付费路径：DefiLlama 订阅（`defillama.com/subscription`）、
  TokenUnlocks / CryptoRank / Tokenomist / VestLabs 的 API key；
- 链上直接读 vesting 合约这条路在本机也被堵死（Blockscout 免 key 无 `eth_call` 代理模块），
  且即便打通也需要逐币的合约地址 + ABI + 私有解码，不构成通用日历源。
