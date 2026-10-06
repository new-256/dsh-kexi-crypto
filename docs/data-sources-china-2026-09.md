# 多面数据源可用性报告（2026-09-28 实测）

> 来源：子代理实测 + 中国视角 DNS 污染比对。所有 HTTP 结果均在本机实跑。

## ⚠️ 方法论警告（必读，影响所有历史结论）

本机**不在中国大陆直连网络**上：运行 **FlClash TUN 模式**（"蓝胖云 Meta Tunnel"，
ifIndex 70，默认路由 198.18.0.2）+ `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890`。
**所有**请求——包括显式 `--noproxy "*"`——都从 **64.118.147.52（东京，AS138997）** 出网。

**含义**：
- 本报告里"能通"只证明**端点是活的且免费**，**不能**证明它能过 GFW；
- 因此另做了**中国视角 DNS 比对**（阿里 DoH 223.5.5.5 / DNSPod doh.pub vs Cloudflare
  DoH 1.1.1.1）来判断是否被投毒——这才是"中国能否用"的证据；
- **以前会话里"某源超时/失败"的结论要重新审视**：可能是代理链路抖动，不是真被墙。

## 中国 DoH vs Cloudflare DoH —— 硬证据

| 主机 | 真实 IP (CF DoH) | 中国 DoH 返回 | 判定 |
|---|---|---|---|
| api.dexscreener.com | 172.64.149.113 / 104.18.38.143 | **202.160.130.145 / 185.45.7.165** | **投毒** |
| api.geckoterminal.com | 104.18.0.233 | **157.240.2.50 / 108.160.173.207** | **投毒** |
| api.coingecko.com | 104.20.41.132 | **103.252.115.153 / 69.63.180.173** | **投毒** |
| api.mexc.com | 23.192.45.59 | **65.49.68.152** | **投毒** |
| api.bybit.com | 3.173.254.117 | **74.86.17.48 / 31.13.75.5** | **投毒** |
| fapi.binance.com | 57.182.12.37 | **104.244.43.52** | **疑似投毒** |
| api.llama.fi / stablecoins.llama.fi | 104.20.34.56 | ✅ 正确 | 干净 |
| api.coinpaprika.com | 172.66.162.206 | ✅ 正确 | 干净 |
| api.alternative.me | 54.39.131.114 | ✅ 正确 | 干净 |
| **data-api.binance.vision** | 52.192.172.236 | ✅ 正确（同一组） | **干净** |
| api.theblockbeats.news / theblockbeats.info | — | ✅ 正确（国内） | 干净 |
| www.odaily.news / panewslab / techflowpost / foresightnews | — | ✅ 正确 | 干净 |

`www.okx.com` 在中国 DoH 与 CF DoH 都返回保留地址 `169.254.0.2` → OKX 是
**地理/路由分流解析**，不是投毒；部分线路可达。
**LunarCrush 在中国 DoH 无 A 记录（NXDOMAIN）→ 被 DNS 封锁。**

---

## 1. 消息面 / 情绪

| 源 | 判定 | 实测结论 |
|---|---|---|
| **币世界 Bishijie** | ❌ **彻底死亡** | `www.bishijie.com` 在中国 DoH **和** CF DoH 都是 **NXDOMAIN**；apex 无 A 记录。**无 API、无 RSS** |
| 金色财经 jinse | ❌ 直连 / ✅ **走 RSSHub 镜像** | `api.jinse.cn`/`jinse.cn` NXDOMAIN（.cn 已废弃）；`www.jinse.com` 能解析但**所有请求 TCP 层超时**。**但 `rsshub.rssforever.com/jinse/lives` → 200，22KB，中文快讯，`lastBuildDate` 新鲜** |
| **BlockBeats 律动** | ✅ **走 RSSHub 镜像** | 官方 `api.theblockbeats.news/v1/open-api/open-flash` → 200 但 **`data: []` 恒空**（端点活着但不供数）。**`rsshub.rssforever.com/theblockbeats/newsflash` → 200，23KB，快讯实时**；`/theblockbeats/article` → 200，299KB |
| Odaily 星球日报 | ★ MAYBE | `www.odaily.news/zh-CN` → 200（SSR）；`api.odaily.news` **是真 JSON API**（返回规范的 404 包）但路径未知；RSSHub 路由 **503** |
| **Alternative.me F&G** | ✅ **USE（最优）** | `api.alternative.me/fng/?limit=1` → 200，无 key，**中国 DoH 解析正确、未被墙** |
| CoinPaprika | ✅ 市场数据 / ❌ 新闻 | `/v1/news`、`/v1/events` 均 **404**；`coins/{id}/events` 能通但内容是 **2018–2028 的陈旧历史事件**（减半/会议），**不能当新闻源**。限频 20000/期，实测剩余 19911 |
| CryptoCompare news | ❌ AVOID | `/data/v2/news/` → **401 "API key required"**（已转 CoinDesk 计费） |
| CoinGecko | ⚠️ 慎用 | `/search/trending` 可用；**`/status_updates` → 404（已移除）**；突发限频**实测 ~2 秒内仅 4 次成功即 429**，约 45s 恢复。且**DNS 被投毒**——投毒解析下可能被静默路由到假服务器（实测拿到过伪造的 429 响应体） |
| **CMC 免 key 内容 API（非官方）** | ★ 可用 | `api.coinmarketcap.com/content/v3/news?page=1&size=2` → 200，英文新闻；`data-api/v3/topsearch/rank` → 200（**散户搜索热度**，5/5 稳定）；`data-api/v3/global-metrics/quotes/latest` → 200。**未文档化，必须 try/except 包裹** |
| **RSSHub 公共镜像 `rsshub.rssforever.com`** | ✅ **USE（高价值）** | 稳定 3/3 命中（每次 1.4–1.6s）。可用：`theblockbeats/newsflash`、`theblockbeats/article`、`jinse/lives`、`binance/announcement`。**503（临时挂）**：`odaily/newsflash`、`panewslab/news`、`foresightnews/news`、`coingecko/trending`。注意 `rsshub.app` 官方站 **403**（CF 机器人墙），**必须用镜像或自建** |

## 2. DEX / 链上

| 源 | 判定 | 实测结论 |
|---|---|---|
| **DexScreener** | ✅ USE（需处理投毒） | `token-boosts/top/v1`（热门榜）、`token-profiles/latest/v1`、`latest/dex/search`、`latest/dex/pairs/{chain}/{pair}` 均 200，**15/15 快速突发无 429**。**但域名在中国被 DNS 投毒 → 中国部署必须固定 CF IP（172.64.149.113 / 104.18.38.143）或走代理** |
| GeckoTerminal | ✅ USE（有 30/min 硬限） | `/networks/trending_pools`、`/networks/solana/trending_pools` → 200。**突发实测第 5 次即 429**，符合文档 30 次/分。**同样被投毒** |
| **DefiLlama** | ✅ **USE（本报告最干净）** | 中国 DoH 解析正确、**20/20 快速突发无任何限频**、无 key。`/v2/chains`、`/v2/historicalChainTvl`、`/protocols`、`stablecoins.llama.fi/stablecoins`、`/stablecoincharts/all` 均 200。**`stablecoincharts/{chain}?stablecoin=1` 的 `totalMintedUSD` = 每日净增发** —— 正是"稳定币大额进出"要的信号。注意 `/overview/stablecoins`、`/overview/exchanges` → 500，改用上面等价端点 |
| 大额转账 | ★ 部分可行 | **TronScan** `apilist.tronscanapi.com/api/token_trc20/transfers?...` → 200（TRC20 转账流，4.2B 条）；**TronGrid** `api.trongrid.io/v1/contracts/{USDT}/events` → 200（6/6 突发通过）。**Blockscout** `eth.blockscout.com/api/v2/stats` → 200。**以太坊大额转账实时流无免费端点**（需索引器 key） |
| Whale Alert | ❌ 付费 | 404 / 需 key |
| Arkham | ❌ 付费 | 400 "sign up for an API key" |
| Coinglass | ❌ 免费档不可用 | `api.coinglass.com` 连接失败 `000`；`open-api-v3` → `{"code":"30001","msg":"API key missing."}` |

## 3. CEX 钱包 / 资金流

| 源 | 判定 | 实测结论 |
|---|---|---|
| **Binance 合约公开数据** | ✅ USE（需备份） | 全部 200 且免 key：`fapi/v1/openInterest`、`futures/data/openInterestHist`、`globalLongShortAccountRatio`、`topLongShortPositionRatio`、`topLongShortAccountRatio`、`takerlongshortRatio`、`fapi/v1/premiumIndex`（资金费率）、`fapi/v1/fundingRate`。**但 `data-api.binance.vision` 不代理合约**（`/fapi/v1/openInterest` → 404 HTML）。**fapi 中国 DNS 解析可疑 → 合约腿在中国网络视为脆弱** |
| Bybit / Bitget | ✅ USE（干净备份） | `api.bybit.com/v5/market/tickers?category=linear` → 200；`api.bitget.com/api/v2/mix/market/ticker` → 200。均免 key |
| CEX 净流入/流出 | ❌ **无免费真值** | 无免费 API 暴露真实 CEX 净流。免费近似：DefiLlama `/protocols` 含 CEX TVL 条目（弱代理）；CoinGlass 是常规来源但计费。**结论：不要指望免费 CEX 净流**，改为自建 Tron/ETH 大额转账监测 |

## 4. 社媒 / KOL

| 源 | 判定 | 实测结论 |
|---|---|---|
| X / Twitter API | ❌ AVOID | 免费档实际为**只写**（发帖），**无可用的读取/搜索**；Basic 起收费。（文档结论，未 HTTP 验证） |
| Nitter | ❌ **全灭** | `nitter.net`/`nitter.poast.org`/`nitter.privacydev.net` → 连接失败 `000`；`xcancel.com` → **451**；`lightbrd.com` → **403**；`nitter.tiekoetter.com` → 200 但返回 **Anubis 机器人验证页**，非 RSS |
| LunarCrush | ❌ | 中国 DoH **NXDOMAIN**；`api4` → **401 Invalid token** |
| Reddit | ❌ | `www.reddit.com/r/.../hot.json` → **403**；`old.reddit.com` → 302 登录墙。redlib/libreddit 镜像 429/502；pushshift → 403（已死） |
| **社媒热度替代** | ✅ | **CoinGecko `/search/trending`** 与 **CMC `data-api/v3/topsearch/rank`** —— 用"散户搜索热度"代理社媒热度。注意 CoinGecko 的 `community_data`/`sentiment_votes_up_percentage` 已从免费端移除（实测为 null） |

**结论：2026 年没有可靠的免费 X/Reddit 源** —— 社媒热度只能用搜索热度代理，**不能**做 KOL 推文级监测。

## 5. 周线 / 月线 K 线（全部实测）

| 源 | 端点 | 1w | 1M | 备注 |
|---|---|---|---|---|
| **Binance vision** | `data-api.binance.vision/api/v3/klines?interval=1w` / `1M` | ✅ 200 | ✅ 200 | 免 key；**DNS 干净** |
| Binance 主站 | `api.binance.com` | — | — | **451，禁用** |
| OKX | `bar=1W` / `1M` | ✅ 200 | ✅ 200 | |
| Gate | `interval=1w` / `1M` | ✅ 200 | ❌ **400 invalid interval 1M**，用 `30d` ✅ | |
| MEXC | `interval=1W` / `1M` | ✅ 200 | ✅ 200 | 免 key（但域名被投毒） |
| Bybit | `interval=W` / `M` | ✅ 200 | ✅ 200 | |
| Bitget | `granularity=1week` | ✅ | 未测 | |
| CoinGecko | `/coins/{id}/ohlc?days=` | ❌ | ❌ | **实测粒度：days=1→30分钟，7/30→4小时，180/365→96小时(4天)，max→1根**。**完全不支持 1w/1M** |

**⚠️ 对齐陷阱**：东亚交易所（OKX/Gate/MEXC/Bybit 现货）周线以**周一 00:00 UTC** 起算，
而 Binance vision 的周线开盘是 `1789344000000`（**2026-09-13 周日 00:00 UTC**）——
**不要跨交易所混用原生周线**。

**结论：取日线本地重采样为 1w/1M 是正确选择**（已验证：本插件 `timeframe.py` 即
采用该方案，且以周一 00:00 UTC+8 为周起点、自然月为月起点，避开上述不一致）。

---

## 最终建议（按实施价值排序，全部免 key）

**Tier 1（先做）**
1. **DefiLlama**（`api.llama.fi` + `stablecoins.llama.fi`）—— 资金面 + 稳定币增发。
   **中国 DNS 干净、无限频、免 key**。性价比最高。
2. **DexScreener**（`token-boosts/top/v1`、`latest/dex/search`）—— DEX 热币。
   15/15 突发通过。**必须固定 CF IP 或走代理（域名被投毒）**。
3. **RSSHub 镜像 `rsshub.rssforever.com`**（`theblockbeats/newsflash`、`jinse/lives`、
   `binance/announcement`）—— **中文**消息面，在原始站点/API 已死或被 CF 墙的情况下仍可用。
   建议自建 RSSHub 作为兜底。
4. **Binance 公开数据** —— `data-api.binance.vision` 取**现货日线**（本地重采样）；
   `fapi.binance.com` 取持仓量/资金费率/多空比。**Bybit、Bitget 作为免 key 备份**。

**Tier 2（按需）**
5. **Alternative.me F&G** —— 一个便宜的宏观情绪数字，未被墙。
6. **CMC 免 key 内容 API** —— 免费英文新闻 + 散户搜索热度。**非官方，必须容错**。
7. **GeckoTerminal** —— 第二个热池视角，但**30/min 硬限 + 投毒**，与 CoinGecko 共享配额。
8. **TronScan / TronGrid** —— 免费大额 USDT 转账监测（稳定币主通道），6/6 突发干净。

**明确 AVOID**：CryptoCompare news（401）、Whale Alert（404/付费）、Arkham（需 key）、
Coinglass 免费档（缺 key）、Nitter 全系（死/机器人墙/451）、LunarCrush（中国 NXDOMAIN + 401）、
Reddit JSON（403/登录墙）、**币世界（域名已死）**、金色财经原始站（超时）、
CoinPaprika events（陈旧历史数据）、`api.binance.com`（451）、**原生周线**（各所周起点/周期码不一致）。

**最大不确定性**：本报告所有"可达"结论都经东京 TUN 出网测得。DNS 污染表是唯一
直接的中国视角证据。关闭代理后应复验 DexScreener/GeckoTerminal/CoinGecko/MEXC/Bybit。