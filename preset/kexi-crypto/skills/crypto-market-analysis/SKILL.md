---
name: crypto-market-analysis
description: 虚拟币行情获取与技术分析工具集。用于获取主流币历史已收盘K线与实时行情，计算均线/MACD/RSI/量比并识别关键位，执行全市场选币与打新评估，输出标准契约报告与离线SVG看板。在分析加密货币走势、筛选币种、评估新币或给出仓位建议与风险评估时加载。
---

# crypto-market-analysis

虚拟币走势分析专用工具集：取数质检 → 指标计算 → 契约报告 → 质量校验 → 可视化看板。

## 组成

| 脚本 / 资源 | 一行用途 |
|---|---|
| `scripts/kline_utils.py` | 统一口径底层库：剔除未收盘bar、自适应价格精度、整数关口步长、ATR/波动率/回撤计算 |
| `scripts/fetch_klines.py` | K线与实时行情获取（Binance/CoinGecko双源自动降级），自动剔除未收盘bar并计算停更天数 |
| `scripts/indicators.py` | 技术指标计算：MA/EMA、MACD、RSI、量比、均线排列、金叉死叉、背离候选、支撑阻力阶梯与密集成交区/筹码峰 volume_profile（POC/价值区/HVN/LVN） |
| `scripts/screener.py` | 全市场批量筛选：标的池过滤、打分排序、一票否决、BTC大盘闸门、4h共振复核与ADV20流动性门槛 |
| `scripts/enrich_picks.py` | 入选币增强：TP1/TP2/止损三档价位、盘口深度、合约资金费率、流通占比与F&G情绪闸门 |
| `scripts/new_listing.py` | 打新评估：上市≤30天新币发现、鲸鱼行为分（多空比/大单净流向/集中度）与第二波企稳检查 |
| `scripts/exchanges.py` | 多交易所统一适配层：主流CEX与Dexscreener统一抽象，单所失败自动优雅降级 |
| `scripts/listing_effect.py` | 跨所上币分析：首发池识别、首日/7日/30日涨跌追踪与交易所风评画像（--reputation） |
| `scripts/portfolio.py` | 组合量化分析：资产间Pearson相关性矩阵（假分散预警）、ATR14等风险仓位预算与Amihud冲击 |
| `scripts/assemble_report.py` | 契约组装器：把 fetch_klines+indicators 真实输出组装成 kexi.report/1 报告；量化字段取真值，研判字段(verdict/trend/risk)给规则草稿，成员结论经 --trend-json/--risk-json/--verdict-json 覆写，解锁风险经 --unlock-json 合并 |
| `scripts/unlock_schedule.py` | 代币解锁时间表（G7）：手工事件 JSON 或内置 registry 查询，计算解锁占比/冲击分/等级与近30天高冲击风险旗；**2026-09-30 实测无任何免 key 源能给带日期的解锁明细**（DefiLlama emissions 已转 402 付费墙），故日期仍全靠手工录入、绝不编造；`--verify-supply` 用免 key 的 CoinGecko 免费档**汇总供应量**校验 unlock_pct 自洽性（只校验总量口径与日历覆盖度，**不提供解锁日期**），取不到即如实降级 |
| `registry/unlock-schedules.json` | 代币解锁计划本地登记库（手工累积，随包分发） |
| `scripts/position.py` | **位置与拥挤度维度（v1.5.0）**：90日区间位置、距高点距离、放量持续天数、MA20偏离、窄幅横盘识别与位置扣分；权重经历史回测校准 |
| `scripts/timeframe.py` | **多周期研判（v1.5.0）**：日线本地重采样为周/月线（周一起算 UTC+8，剔除未走完周期），判定全周期共振/逆大周期反弹并给出多周期得分 |
| `scripts/datasources.py` | **多面数据源（v1.5.0）**：消息面(RSSHub镜像)、DEX热币、稳定币净流入、合约资金费率与多空比、恐慌贪婪指数、CMC 热搜；8源并发，单源失败优雅降级 |
| `scripts/backtest.py` | **点时间回测（v1.5.0）**：用历史K线在每个观察日重算指标再验证未来真实收益（无前视偏差），按位置/放量/多周期分组，输出**中位数+胜率**结论 |
| `scripts/cex_keystore.py` | **CEX 密钥库（v1.5.0）**：Windows DPAPI 当前用户加密存储，密文无明文、审计日志不含密钥，含提币权限与IP白名单风险体检 |
| `scripts/cex_adapter.py` | **CEX 实盘适配层（v1.5.0）**：Binance/OKX/Gate/MEXC 四家签名与下单，风控闸门前置；默认 `DRY_RUN`，签名已与官方 SDK 逐字节比对 |
| `scripts/cex_risk.py` | **实盘风控（v1.5.0）**：保守/均衡/激进三档硬性风控（单笔上限、杠杆、止损强制、单日次数），`HARD_CEILING` 绝对上限，状态跨重启保留 |
| `scripts/kexi_notify.py` | **通知渠道（v1.5.0）**：落盘(强制)/企业微信/WorkBuddy/QQ机器人/stdout，渠道路由与去重，单渠道故障不影响其他 |
| `scripts/keepalive.py` | **长期保活（v1.5.0）**：定时/信号触发评估（价格异动、放量、高位、止损临近、多周期反转、稳定币流向），四种处置模式，默认仅通知并要求确认 |
| `scripts/entry_plan.py` | **入场时机引擎（v1.5.1）**：四类动作建议（立即入场/回调至X企稳反弹/抄底分批点位/放量突破Y补仓）+ 失效位；入场环境四条件量化（放量增长/高换手/量升盘稳/资金流出减少）；追高嫌疑拦截（pos_90≥80或拥挤拉伸时禁止立即入场） |
| `scripts/coin_profile.py` | **币种行为画像（v1.5.1）**：拉盘/出货模式归纳（震荡拉盘/快速拉盘震荡出货/缓慢拉盘快速出货/趋势行情）；顺大盘/背离大盘（r+beta）；走大饼还是以太行情（对BTC/ETH相关对比）；BTC-ETH比价与风格切换 |
| `scripts/news_watch.py` | **定时消息面监控（v1.5.1）**：三层获取——RSSHub Telegram特殊通道（吴说/Cointelegraph/WatcherGuru/鲸鱼）→常规通道（BlockBeats/金色财经/币安公告）→降级标记；--loop常驻30分钟轮询，状态去重，新增条目经kexi_notify通知；暗网边界诚实声明 |
| `scripts/token_stats.py` | **Token 消耗统计（v1.5.0）**：单次任务与每日消耗（输入/输出/缓存读写/命中率），按任务来源（用户/触发/定时）拆分 |
| `scripts/track_report.py` | **战绩可视化报告（v1.5.0）**：单文件自包含 HTML——战绩总览+超额收益、判断点分析线、三法交叉验证止盈止损、底仓分批建仓策略 |
| `scripts/validate_report.py` | 报告JSON契约校验闸门：校验11个顶层键、拦截停更数据（>3天报error）、支持--fix安全归一 |
| `scripts/compact_report.py` | 报告Token压缩器：完整报告转≤1200字符极简文本摘要与轻量JSON，消除冗余上下文 |
| `scripts/dashboard.py` | 单文件离线看板渲染器：纯stdlib内联SVG手绘蜡烛图/指标/阶梯/情景/风险/组合面板，自适应深浅色 |
| `references/data-sources.md` | 数据源API规格、限流规则、错误重试与极端波动应对说明 |
| `references/screening-strategy.md` | 批量筛选策略定想定稿（信号定义、打分权重、BTC环境闸门与风控规则） |
| `templates/report-template.md` | 研判报告终稿Markdown标准模板（五段式结构） |

## 输出目录规范

全包中间件、数据拉取结果、报告 JSON 与渲染看板统一落盘至工作区约定目录：
`<工作区>/kexi_out/`
示例路径：`kexi_out/btc_1d.json`、`kexi_out/report.json`、`kexi_out/report.html`。执行脚本前请确保或自动创建该目录。

## 报告 JSON 契约（schema_version: "kexi.report/1"）

标准研判成果遵循统一 JSON 契约规范，样例参照 `tests/sample_report.json`，顶层必须包含以下 11 个键：
`schema_version`, `disclaimer`, `generated_at`, `as_of`, `symbol`, `verdict`, `data_quality`, `indicators`, `trend`, `risk`, `report_text`。

### 字段速查表（对照 validate_report.py 校验规则）

| 字段路径 | 类型 | 必填/约束 | 校验规则与错误条件 |
|---|---|---|---|
| `schema_version` | string | 必填 | 必须恒等于 `"kexi.report/1"`，否则抛 error |
| `disclaimer` | string | 必填 | 免责声明字符串，建议非空（空则 warn） |
| `generated_at` | string | 必填 | ISO 8601 生成时间戳 |
| `as_of` | string | 必填 | 数据基准时间（如 `"2026-09-26 08:00 UTC+8"`） |
| `symbol.name` / `code` | string | 必填 | 标的名称与交易代码（如 `"Bitcoin"` / `"BTCUSDT"`） |
| `symbol.interval` | string | 必填 | 请求周期（如 `"1d"`） |
| `symbol.source` | string | 必填 | 数据源标识（如 `"binance"`） |
| `symbol.price` | number | 必填 | 现价数值，严禁字符串数字、NaN、Infinity |
| `data_quality.interval_actual` | string | 必填 | 实际周期，与 `symbol.interval` 不一致时触发警告 |
| `data_quality.bars` | int | 必填 | 参与计算的已收盘 K 线根数 |
| `data_quality.dropped_open_bar` | int | 必填 | 剔除的未收盘 bar 根数（通常为 0 或 1） |
| `data_quality.gaps` | array | 必填 | 时间戳缺口清单，必须为列表 |
| `data_quality.duplicates_removed`| int | 必填 | 实际去重时间戳根数 |
| `data_quality.last_bar_age_days` | number/null | 必填 | 末根 bar 距今天数；**日线（1d）> 3.0 天判定为数据停更，直接抛 error** |
| `data_quality.credibility` | string | 必填 | 枚举值限定：`"可用"` / `"部分可用"` / `"不可用"` |
| `data_quality.notes` | string | 必填 | 质检说明备注 |
| `indicators.ma` | object | 必填 | 必须包含 `ma5`, `ma10`, `ma20`, `ma60` 数值 |
| `indicators.ma_alignment` | string | 必填 | 均线排列形态（如 `"多头"` / `"空头"` / `"纠缠"`） |
| `indicators.macd` | object/null | 必填 | 若为对象必须含 `dif`, `dea`, `hist` 数值；建议含 `state` |
| `indicators.rsi` | object | 必填 | 必须包含 `rsi6`, `rsi14` 数值；建议含 `state` |
| `indicators.volume_ratio` | number | 必填 | 量比数值（末根量 / 剔除末根的 5 日均量） |
| `indicators.volatility` | object | 必填 | 必须包含 `atr_pct` 与 `max_drawdown_30d` 数值 |
| `indicators.support_resistance` | object | 必填 | 包含 `support` 与 `resistance` 数组，每项含 `price`(数值), `strength`, `basis` |
| `indicators.volume_profile` | object | 必填 | 筹码峰：`available=false` 或含 `point_of_control`(POC)、`value_area`(70%价值区)、`hvn`/`lvn` 节点与 `bins` 直方图序列 |
| `trend.direction` | string | 必填 | 枚举值限定：`"多头"` / `"空头"` / `"震荡"` |
| `trend.strength` | string | 必填 | 枚举值限定：`"强"` / `"中"` / `"弱"` |
| `trend.confidence` | string | 必填 | 枚举值限定：`"高"` / `"中"` / `"低"` |
| `trend.stage` / `falsification` | string | 必填 | 趋势阶段说明与证伪防线描述 |
| `trend.scenarios` | array | 必填 | 三情景数组，每项含 `name` (`基准`/`乐观`/`悲观`)、`probability` (0~1且和接近1.0)、`range_low`、`range_high` |
| `risk.level` | string | 必填 | 枚举值限定：`"低"` / `"中"` / `"高"` / `"极高"` |
| `risk.rationale` | string | 必填 | 风险评定依据说明 |
| `risk.warnings` | array | 必填 | 风险警示列表，建议 3~6 条字符串 |
| `risk.extreme_events` | array | 必填 | 历史极端异动事件列表（单根≥10%等） |
| `verdict.headline` | string | 必填 | 核心研判横幅，建议 ≤50 字 |
| `verdict.rating` | string | 必填 | 投资评级（如 `"逢低布局"` / `"观望"`） |
| `verdict.position_advice` | string | 必填 | 仓位管理建议（如 `"底仓 15%~25%"`） |
| `verdict.key_levels` | array | 必填 | 关键交易位清单，每项含 `label`, `price`, `action` |
| `verdict.scenarios_summary` | string | 必填 | 情景推演极简概述 |
| `report_text` | string | 必填 | 最终输出的五段式 Markdown 完整文本 |

## 核心脚本用法与 CLI 参数

### 1. 质量闸门：validate_report.py
校验研判报告完整性与合法性，拦截脏数据与停更标的，支持自动安全归一。
```bash
python scripts/validate_report.py --input kexi_out/report.json --fix --out kexi_out/report.fixed.json
```
- `--input`: （必填）输入的待校验 report.json 路径
- `--fix`: （可选）执行安全归一（NaN/Inf 置 null、清除首尾空格、归一情景概率和为 1.0、截断超过 6 条的 warnings）
- `--out`: （可选）修复后写出的新 JSON 路径
- `--quiet`: （可选）静默模式，仅 stdout 输出紧凑 JSON，不向 stderr 输出人类可读诊断

### 2. Token 压缩器：compact_report.py
将大体积完整 report.json 压缩为 ≤1200 字符的极简摘要块，供 LLM 或成员高密引用。
```bash
python scripts/compact_report.py --input kexi_out/report.json --out kexi_out/report.compact.json --max-chars 1000
```
- `--input`: （必填）输入的 report.json 路径
- `--out`: （可选）写出结构化紧凑 JSON（含 summary_text、key_numbers、dropped_fields）
- `--max-chars`: （可选，默认 1200）stdout 输出纯文本摘要字符上限

### 3. 可视化看板：dashboard.py
渲染单文件离线 HTML 可视化看板，纯标准库手绘 SVG 蜡烛图与指标卡片，零外链、深浅色自适应。
```bash
python scripts/dashboard.py --input kexi_out/report.json --klines kexi_out/btc_1d.json --out kexi_out/report.html --title "BTCUSDT 研判看板"
```
- `--input`: （必填）输入的 report.json 路径
- `--indicators`: （可选）补充 indicators.json 路径
- `--klines`: （可选）传入 K 线原始数据以绘制蜡烛图（如 btc_1d.json）
- `--portfolio`: （可选）传入 portfolio.json 渲染相关性热力图与等风险仓位表
- `--out`: （可选）输出的 HTML 路径（默认 `<输入目录>/kexi_out/report.html`）
- `--title`: （可选）自定义页面标题
- `--no-candles`: （可选）禁用 K 线蜡烛图渲染

### 4. 行情获取：fetch_klines.py
```bash
python scripts/fetch_klines.py --symbol BTCUSDT --interval 1d --limit 120 --out kexi_out/btc_1d.json
python scripts/fetch_klines.py --symbol BTCUSDT --quote  # 实时行情快照
```
- 内置数据源切换（`--source auto/binance/coingecko`）
- 自动剔除未收盘 bar 并计算 `last_bar_age_days`

### 5. 指标计算：indicators.py
```bash
python scripts/indicators.py --input kexi_out/btc_1d.json --out kexi_out/indicators.json
```
- 输出均线系统、MACD、RSI、量比、自适应支撑阻力阶梯、ATR14 及波动率指标。

### 6. 全市场筛选：screener.py
```bash
python scripts/screener.py --top 100 --surge-mult 2.0 --min-hits 3 --vol-mult 1.5 --min-adv-usd 3000000 --out kexi_out/scan_result.json --md kexi_out/scan_report.md
```
- `--min-adv-usd`: 20 日日均成交额门槛（默认 3,000,000 USD），自动剔除无流动性死狗币。

### 7. 打新与上币分析
```bash
python scripts/new_listing.py --days 30 --out kexi_out/new_listings.json
python scripts/listing_effect.py --symbol JTO --out kexi_out/le.json --md kexi_out/le.md
python scripts/listing_effect.py --reputation --sample 30 --out kexi_out/rep.json
```

### 8. 组合分析：portfolio.py
```bash
python scripts/portfolio.py --input kexi_out/scan_result.json --klines-dir kexi_out/ --capital 10000 --risk-per-trade 0.005 --out kexi_out/portfolio.json
```

### 8b. 位置与多周期维度（v1.5.0，选币质量核心）
**为什么加这个**：用户反馈"之前选的很多币技术指标都很好，但已经放量多天、处在高位"。
历史回测证实「已放量多日」是**两轮一致偏负**的信号（30日胜率仅约32%，未放量39–43%），
故拥挤度成为最重扣分项；而单纯"90日位置高"区分度不稳定（均值被极端离群币主导、
换样本会翻转），只作 -1 风险提示，不作硬淘汰。
```bash
# 单币位置画像（不联网，只吃K线）
python scripts/position.py --input kexi_out/btc_1d.json
# 多周期研判：日线→周/月线共振
python scripts/timeframe.py --input kexi_out/btc_1d.json
# screener 已内置：位置扣分 + 低位横盘候选 + 大周期研判
python scripts/screener.py --top 100 --out kexi_out/scan.json --md kexi_out/scan.md
```
- `--no-position-weight`：关闭位置扣分（回到纯趋势口径，供对比）
- `--hard-pos` / `--hard-stretch`：把位置/偏离改为硬性淘汰（默认关闭，因回测不支持）
- `--accum-max 6`：低位横盘候选输出条数上限
- `--no-multi-tf`：关闭周/月线扣分

### 8c. 多面数据源（v1.5.0）
```bash
python scripts/datasources.py --out kexi_out/sources.json          # 全部8源并发
python scripts/datasources.py --only news,fng --out kexi_out/n.json
```
覆盖：消息面（币圈新闻，RSSHub 多镜像）、DEX 热币、稳定币净流入、合约资金费率/多空比、
恐慌贪婪指数、CMC 热搜。**单源失败不影响其他源**，返回 dict 含失败原因，绝不编造。

> ⚠️ **币世界（bishijie.com）已永久停服**（两个解析器均 NXDOMAIN，无 API/RSS）。
> 中文消息面改用 RSSHub 镜像；**X/Twitter、Reddit 在 2026 年无可用免费源**
> （Nitter 已死、LunarCrush 域名失效），社交热度只能用 CMC 热搜 / CoinGecko trending 代理。

### 8d. 点时间回测（v1.5.0，用于校准权重而非预测）
```bash
python scripts/backtest.py --top 50 --limit 700 --step 10 --horizons 7,14,30 --out kexi_out/bt.json --md kexi_out/bt.md
```
- 每个观察日**只用当天及之前**的K线重算指标，再验证未来真实收益（无前视偏差）
- ⚠️ **必须看中位数与胜率，不能看均值**：均值被 +904%/+371% 这类离群币主导，
  两轮独立回测中"位置分组"的均值差**符号会翻转**，只看均值会得出相反结论
- `--step` 越大越快；`--top` 控制每组样本量

### 8e. 实盘接入与风控（v1.5.0）
```bash
python scripts/cex_keystore.py save --exchange binance --api-key ... --api-secret ...
python scripts/cex_keystore.py list          # 只回显标签，绝不回显密钥
python scripts/cex_keystore.py security      # 安全体检（提币权限/IP白名单/DPAPI边界）
python scripts/cex_adapter.py balance --exchange okx --account spot
python scripts/cex_risk.py describe --profile conservative
```
> **安全要点**：密钥用 Windows DPAPI 按**当前用户**加密，明文不落盘、审计日志不含密钥。
> 但 DPAPI **无法防同一用户下的恶意程序**——请务必给 API Key 关闭提币权限、设置 IP 白名单。
> 下单默认 `DRY_RUN=True` 只做推演；真实下单必须显式 `confirm`，且**先过风控闸门**——
> 风控返回 `allowed=False` 时**必须拒绝执行**，不得绕过。

### 8f. 长期保活与通知（v1.5.0）
```bash
python scripts/keepalive.py --demo                 # 用实时数据演示信号触发
python scripts/keepalive.py --loop                 # 常驻（默认每小时评估）
python scripts/kexi_notify.py --test               # 渠道连通性自检
```
- 触发信号：价格异动、放量、90日高位、止损临近、多周期反转、稳定币大额流向
- 处置模式 `--mode`：`notify_only`(默认)/`recommend`/`auto_tpsl`/`auto_reduce`
- **默认仅通知并要求用户确认**，不会自作主张动仓；即使 `auto_*` 也要求确认。
  落盘渠道强制开启——其他渠道全挂也不会丢事件。

### 8g. Token 消耗统计（v1.5.0）
```bash
python scripts/token_stats.py --session            # 最近一次任务
python scripts/token_stats.py --category           # 今日，按 用户/触发/定时 拆分
python scripts/token_stats.py --days 7             # 最近7天
```
- 数据来自 DSH 会话日志 `$DSH_HOME/sessions/.../session.v4.jsonl.zstd`
- ⚠️ 该文件是**多帧 zstd**，必须 `stream_reader` 读取（`decompress()` 只解第一帧，静默丢数据）
- ⚠️ usage **只从 `assistant/message.data.usage` 取**；`data.stream[*].chunk.usage` 是
  同样的数值**冗余副本**，一并统计会让输入/缓存读**翻倍虚报**
- 缓存命中率口径 = `cacheRead / (cacheRead + input)`（用 `cacheRead/total` 会虚高）

### 8h. 战绩可视化报告（v1.5.0）
```bash
python scripts/track_report.py --ledger track-record/ledger.jsonl \
  --plans kexi_out/plans.json --backtest kexi_out/bt.json --out kexi_out/track_report.html
```
产出**单文件自包含 HTML**（零外链、可离线打开），含五部分：
① 战绩总览（**超额收益 vs BTC** 是最关键判据——牛市里随便买都赚，只有超额才说明选币能力）
② 哪些判断维度真的有效（回测验证）
③ 判断点分析线（每个标的的决策时间轴 + 内联 SVG 走势与关键位）
④ 止盈止损点（ATR法/结构法/均线法**三法交叉验证**，取中位值）
⑤ 底仓策略（分3批建仓 + 3级退出 + 纪律条款）

### 8i. 入场时机引擎（v1.5.1，回答"什么时候进"）
```bash
python scripts/entry_plan.py --klines kexi_out/BTCUSDT_1d_klines.json \
  --btc-klines kexi_out/BTCUSDT_1d_klines.json \
  --indicators kexi_out/BTCUSDT_1d_indicators.json --out kexi_out/entry_plan.json
```
综合评估后**必须给出入场/出场时机**（用户核心需求），四类动作：
- `enter_now` 立即入场：趋势（收盘>MA20 且多头排列/MA20向上）+ 量能（温和放量或高换手）+ 大盘（BTC盘稳或站上MA20）+ 无追高嫌疑，四门全过
- `pullback_buy` 回调反弹入场：下探支撑位（MA20/筹码POC/强支撑）企稳反弹后买，附确认信号（不破支撑+收盘收复MA20+缩量回踩）
- `bottom_fish` 抄底分批：距90日高点回撤≤-25%时启用，三档 -30%/-45%/-60% 等额分批
- `breakout_add` 突破补仓：放量（量比≥1.2x）收盘突破最近阻力后加仓
- 每条建议必带 `basis` 依据列表（可解释、可证伪）；**追高嫌疑**（pos_90≥80/crowded/stretched）flagged=true 时**永不给立即入场**
- 入场环境四条件（用户点名量化）：放量增长（3日/20日量比≥1.2）、高换手（量比≥2.0且连续爆量≤5天）、量升盘稳（本币放量+BTC ATR%<5）、资金流出减少（稳定币7日净增发转正）
- `invalidation` 失效位：跌破最后支撑 → 看涨逻辑失效离场

### 8j. 币种行为画像（v1.5.1，回答"这个币习惯怎么走"）
```bash
python scripts/coin_profile.py --klines kexi_out/SOLUSDT_1d_klines.json \
  --btc-klines kexi_out/BTCUSDT_1d_klines.json --eth-klines kexi_out/ETHUSDT_1d_klines.json \
  --out kexi_out/coin_profile.json
```
三层画像（每层附证据列表）：
- **拉盘/出货模式**（pump_dump_pattern）：震荡拉盘（波段市，支撑买阻力卖）/ 快速拉盘震荡出货（一波流，追高危险）/ 缓慢拉盘快速出货（温水煮青蛙）/ 趋势行情（回踩均线加仓）——从历史每轮上涨段的斜率×量能×顶部形态归纳
- **大盘关系**（market_regime）：顺大盘（r≥0.6，附beta：>1.2放大器）/ 背离大盘（r≤0.2，独立行情）/ 弱相关；**走大饼还是以太**（对BTC/ETH相关差≥0.15判归大饼/以太/双驱/不分）
- **BTC-ETH 关系**（btc_eth_linkage）：90日对数收益相关、ETH/BTC比价30日变动——升=山寨/ETH系行情窗口，降=大饼主导
- **画像≠预测**：只归纳历史操盘习惯，绝不外推为必然（`how_to_use` 列使用纪律）

### 8k. 定时消息面监控（v1.5.1，"特殊方式获取消息"）
```bash
python scripts/news_watch.py --once                # 单轮
python scripts/news_watch.py --loop --interval-min 30   # 常驻（默认30分钟）
```
三层获取策略（全部免 key，实测 2026-09-29 全通）：
1. **特殊通道**：RSSHub 镜像读 Telegram 频道——吴说区块链/Cointelegraph/WatcherGuru（世界局势）/WhaleAlert（鲸鱼大额转账）。2026年 X/Twitter 无免费读源、Nitter 全灭，这是社媒消息面的替代主路
2. **常规通道**：BlockBeats/金色财经/币安公告（datasources.fetch_news）
3. **降级标记**：全灭时 `available=false` + 显式「消息面缺失」，绝不阻塞技术面研判
- 话题分类（行情/世界局势/大额资金/社媒热点），`topics_count` 量化各面比重
- 状态持久化 `news_watch_state.json`（去重已见标题500条），新增条目经 kexi_notify 通知（世界局势/大额资金优先）
- **暗网边界（诚实声明）**：暗网数据源需付费API/Tor访问，超出能力与安全边界；暗网相关事件（黑客/泄漏）经正规快讯渠道覆盖，**不假装有暗网监控能力**

### 8l. 多标的批量与产物溯源（v1.5.2，**成员禁止手写 Python**）
```bash
# 一次调用跑完 N 个标的（kexi_run 批量模式；下例为 25 币）
kexi_run(mode=script, script=indicators.py, batch={
  items: ["AAVEUSDT","XRPUSDT", ...],
  args: ["--input","kexi_out/{item}_1d_klines.json","--out","kexi_out/{item}_1d_indicators.json"],
  concurrency: 4 })
# 清单也可从文件读：itemsFromFile="kexi_out/coins.txt"（每行一个，# 注释）
```
- **为什么必须批量**：逐个调 kexi_run 跑 25 币×3 脚本需 75 次工具调用，成员会绕开工具层手写 Python——2026-09-29 实测出现 4 个自写脚本，其中 1 个 `SyntaxError: no binding for nonlocal 'opt'` 崩掉整轮返工。
- **`{item}` 占位**：`args` 里任意位置替换；没有占位时用 `itemArg`（如 `"--symbol"`）把 item 接在该参数后。上限 60 项/次，并发默认 4（上限 8，避免交易所限流）。
- **脏数据闸门（关键）**：脚本本身**不会**因数据停更/样本不足而失败（实测 `indicators.py` 对 400 天前的 K 线照跑不误，产出"看起来正常"的指标，且其 JSON 里根本没有 `last_bar_age_days`）。批量模式改为读**产出文件**的 `bars`/`as_of` 自行判定：停更 >3 天 或 样本 <120 根 → 该项标 ⚠，`next` 明确要求**按铁律直接剔除，不得写脚本重试掩盖**。
- **产物溯源**：`kexi_run(mode=files)` 返回本轮/历史分组——只引用【本轮】（判定基准 `kexi_out/.kexi_run.json` 的 `started_at`），【历史】是上一轮及更早的文件。2026-09-29 实测主理人曾把上一轮的 `zhibei_final20.json` 当本轮产物，反复用 pwsh 排查来源白耗步数。
- 明细落盘 `kexi_out/_batch_<script>_<ts>.json`（逐项 ok/耗时/out/脏数据标记）。

### 9. 报告组装：assemble_report.py（流水线第 3 步）
```bash
# 纯规则草稿（无人工/成员结论时）
python scripts/assemble_report.py --klines kexi_out/btc_1d.json --indicators kexi_out/indicators.json --out kexi_out/report.json
# 成员结论覆写（trend/risk/verdict 三块 JSON 片段，仅含要覆写的字段）
python scripts/assemble_report.py --klines kexi_out/btc_1d.json --indicators kexi_out/indicators.json --out kexi_out/report.json --trend-json kexi_out/member_trend.json --risk-json kexi_out/member_risk.json
```
- 量化字段（价格/指标/支撑阻力/数据质量）直接取脚本真实输出；研判字段（verdict/trend/risk）先按规则生成 `draft: true` 草稿，成员结论以 `--*-json` 浅合并覆写并将 `draft` 置 false。
- 输出即满足报告契约，可直接送 `validate_report.py`；规则草稿的 `risk.warnings` 恒 ≥3 条（含量比/RSI/回撤/ATR 条件项与 7×24 免责提示）。

## 标准流水线与 DSH 工具优先原则

单币分析标准执行流水线：
```
fetch_klines.py → indicators.py → [unlock_schedule.py] → assemble_report.py → validate_report.py --fix → dashboard.py → 呈现交付
（unlock_schedule 查内置 registry，无该代币数据则跳过——不编造日期；有数据则 assemble 经 --unlock-json 合并风险旗）
（可选 `unlock_schedule.py --symbol X --verify-supply` 会额外拉 CoinGecko 免费档汇总供应量，
  校验 registry 登记的 total_supply 口径与日历覆盖度；冲突以 warning 形式顶出，**不自动改写 registry**）
```

> **DSH 工具优先原则**：
> 在 DSH 交互会话中，若当前环境注册了 `kexi_run`、`kexi_validate`、`kexi_dashboard`、`kexi_cli` 等专属工具，**严禁自行拼接执行多条底层 shell 命令**，必须优先直接调用工具。`kexi_run(mode=pipeline, symbol=...)` 一条命令跑完上面全部**七步**（v1.2.1 起第 5 步为内置 CLI 交叉验证）并只回 ≤900 字符摘要 + 产物路径；`kexi_run(mode=script, script=..., args=[...])` 跑白名单内任意单脚本（调试特殊参数用）。只有在工具缺失时才用 pwsh 依次执行各脚本。
>
> **第 5 步是自动的**：流水线把报告紧凑摘要派给优先级最高的本地 CLI（agy/codebuddy/mimo）
> 做独立形态复核，结论写进 `report.json` 的 `crosscheck` 键并在 digest 尾部标注
> （`；CLI 复核(agy): 同意`）。**不需要主理人手动调 `kexi_cli`**。无 CLI / 超时 / 关闭设置
> （`autoCliCrosscheck`）都如实记 `skipped`（含 `reason`），不阻塞看板渲染。
>
> `kexi_cli(action=list|run)` 用于**更细粒度**的手动交叉验证（如单独复核解锁逻辑、
> 复算风险等级），结论作参考并标注来源；无可用 CLI 时如实说明、单模型继续
> （禁止假装有 CLI 协助）。详见 `kexi-team-orchestration` 技能第四节。

## 异常拦截与风控内置

1. **停更数据拦截（last_bar_age_days）**：`fetch_klines.py` 自动计算最新已收盘日线时间戳距离当前时间的天数。若 `last_bar_age_days > 3.0`（日线停更超 3 天），`validate_report.py` 将直接报错阻断，判定标的数据不可用，防止分析已下架或僵尸币。
2. **流动性门槛（--min-adv-usd）**：`screener.py` 内置 20 日日均成交额门槛（默认 3M USD），硬性过滤非流动性伪信号。
3. **已收盘 bar 口径**：所有指标计算与打分严格基于最后一根已收盘 bar。盘中未收盘 bar 因瞬时价与残余成交量会导致 MA 与量比系统性失真，全包通过 `kline_utils.py::drop_open_bar` 自动剔除。

## 铁律

1. **数值事实**：所有行情与指标数值必须来自脚本实际运行结果，严禁凭记忆编造。
2. **如实降级**：数据获取失败或停更时走降级与如实报告流程，标注“数据缺失/不可用”，严禁虚构。
3. **免责必备**：所有报告与交付卡片必须附带合规免责声明，不得构成交易承诺。
4. **口径一致**：必须基于最后一根已收盘 K 线判断趋势；若数据源降级导致周期改变，必须如实声明真实周期（`interval_actual`）。
5. **自适应精度**：价格与指标输出必须采用自适应精度（`kline_utils.rn`），禁止无脑使用固定 2 位小数导致低价小币种数据失真归零。
6. **位置必须与大周期同看（v1.5.0）**：给出"逢低布局/买入"评级前，必须同时交代
   ①90日区间位置与距高点距离 ②是否已放量多日 ③周线/月线方向。
   **逆大周期的反弹不得给正面评级**——日线再好，月线空头时只能定为"反弹"而非"反转"。
   已在高位且放量多日者，只能给"减仓/不追"，不得进推荐前列。
7. **回测看中位数与胜率，不看均值（v1.5.0）**：收益分布长尾极端（个币可 +900%），
   均值会被少数离群币完全主导，甚至让结论**符号翻转**。任何"某维度有效"的结论
   必须同时给出中位数与胜率，并说明样本量与区间。
8. **实盘风控不可绕过（v1.5.0）**：`cex_risk.check_order` 返回 `allowed=False` 时
   **必须拒绝下单**，不得以"用户要求"或"机会难得"为由绕过；改单只能按返回的
   `adjustments` 缩小到合规范围。实盘默认 `DRY_RUN`，真实下单需显式确认。
9. **不制造虚假安全感（v1.5.0）**：涉及资金安全的功能必须如实说明能力边界——
   例如 DPAPI 只能防"文件被拷走"，**防不住同一用户下的恶意程序**；
   无 DPAPI 时保存明文密钥必须**拒绝**而非静默接受。
10. **保活默认不动仓（v1.5.0）**：定时/信号触发的评估默认只通知并要求用户确认；
   即便选择 `auto_*` 模式也必须保留确认环节，绝不静默执行资金操作。
11. **事前可证伪与历史建档（v1.5.0）**：预测必须显式落盘 `entry_price` 与 `as_of` 基准时间，三情景必须给出具体价格区间（事前可证伪、事后可评分）；风控结论必须反向约束名单，宁缺毋滥，极高危标的坚决剔除；任务成果与实操经验及时建档存盘，通过 `track_report.py` 生成可视化报告交付。
12. **数据源诚实与受限明示（v1.5.0 B5/B6）**：2026 年 X/Twitter 无可用免费读源，必须向用户明示此项限制，采用 Alternative.me 恐慌贪婪指数与 CMC 热搜作为情绪代理；大额已上 CEX 币资金流由 DefiLlama 稳定币流入流向覆盖。
13. **综合评估必须给入场时机（v1.5.1）**：只给"买什么"不给"什么时候进"是残缺结论——必须用 `entry_plan.py` 给出四类动作（立即入场/回调企稳反弹/抄底分批/突破补仓）+ 失效位；**追高嫌疑 flagged=true 时禁止给"立即入场"**；入场环境四条件（放量/高换手/量升盘稳/资金流出减少）不满足时只能给"等回调/等突破"，不得硬给立即入场。
14. **币种画像必须给、且只作定性输入（v1.5.1）**：候选币须用 `coin_profile.py` 说明拉盘/出货习惯、顺/逆大盘、走大饼还是以太；画像影响情景权重（快速拉盘震荡出货的币反弹目标给低、趋势币回调目标给深），但**画像≠预测**，不得当作确定性外推。
15. **消息面占高比重 + 定时获取（v1.5.1）**：行情、世界局势、大额资金、社媒消息在研判中占高比重（望潮/守拙结论须引用消息面证据）；获取走 news_watch.py 三层（Telegram特殊通道→常规→降级标记）；长任务用 `--loop` 定时拉取；**暗网监控超出能力边界，必须明示**，不得假装有暗网数据。
