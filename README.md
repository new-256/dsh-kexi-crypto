# K析研判团 · DSH 插件（dsh-kexi-crypto）

**加密货币趋势研判 6 人专家团** —— 加密货币全流程研判与选币系统，
面向选币质量升级、多面数据源、真实 CEX 实盘接入与风控、长期保活与通知、Token 消耗统计、历史实操经验建档交付全新重构。

版本 **v1.5.9** · 依赖 DSH ≥ 0.1.7-rc

> 一句话：选 preset「K析研判团」开会话 → 说"研判一下 ETH"或"帮我选币" → 主理人 K析 调度
> 数脉/指北/望潮/守拙/证伪 五位成员（持久团队/按需协作）→ 28 个脚本全链量化支撑 → 过 JSON 契约闸门 →
> 交付五段式结论 + 单文件离线 HTML 研判/战绩看板。

---

## 目录

- [它是什么](#它是什么) · [快速开始](#快速开始) · [架构](#架构) · [核心数据铁律与纪律](#核心数据铁律与纪律)
- [v1.5.0 六大升级能力](#v150-六大升级能力) · [Token 消耗优化点](#token-消耗优化点) · [可视化与实操战绩](#可视化与实操战绩)
- [目录结构](#目录结构) · [文档](#文档) · [排错](#排错)

## 它是什么

| 层 | 组件 | 说明 |
|----|------|------|
| 专家团 | preset `kexi-crypto` | 主理人 persona + **5 个角色**持久成员（数脉/指北/望潮/守拙/**证伪**，可按需派活），独立 persona/最小工具面/禁再委派。**证伪 = 红队**，专攻结论前提 |
| 数据链 | 28 个零依赖 Python 脚本 | 取数→指标→组装→校验→压缩→看板，位置画像、多周期、点时间回测、多源数据、CEX密钥库/风控/实盘、保活通知、Token统计、战绩报告、**决策留痕对账** |
| 工具面 | `kexi_run` / `kexi_validate` / `kexi_dashboard` / `kexi_cli` / `kexi_news` / `kexi_ensure_team` | 包装全链脚本的模型工具：`SCRIPT_WHITELIST` 包含全量 25 个脚本，一条 `kexi_run(mode=pipeline)` 跑完研判，只回 ≤900 字符摘要 + 产物路径 |
| 实时状态 | 标题栏研判灯 + 实时进度弹窗 | 家级 host+client 插件：⟳ 研判中（含 symbol 与步骤进度）/ ✓ / ✗，点击弹窗展示节点时间线与内嵌成果看板 |

## 快速开始

```powershell
dsh plugin --profile web add "C:\Users\lcl\Desktop\DSH插件开发\K析研判团"   # 安装（本地路径）
dsh restart                                                                  # 或重启 DSH 桌面端
```

然后在 Web GUI 新建会话 → 顶部选择智能体预设「**K析研判团**」→ 直接对话：

```
研判一下 ETHUSDT 日线，结合周月线看
帮我筛选近期低位横盘启动的币，给出前 5 与回测结论
查看我的 CEX 仓位并做保守风控评估
生成历史实操经验与战绩可视化报告
```

安装自检（改过本包后必跑）：

```powershell
node scripts/verify.mjs          # 打包契约静态校验（YAML/!!js/exports/frontmatter/py_compile）
node scripts/test-runtime.mjs    # 运行时行为测试（129 项通过）
```

要求：`Python ≥ 3.9`（仅标准库；默认探测 `C:\Python313\python.exe` → `python` → `py`，
可用环境变量 `KEXI_PYTHON` 指定）。详见 [INSTALL.md](INSTALL.md)。

## 架构

```
package.json (dsh.bundle.patch + dsh.client + icon + locale)
├── home-plugin/dsh-kexi-crypto/
│   ├── cordis.patch.yml          ← host 行 + preset 声明（18 个插件行）
│   └── lib/index.mjs + client.js ← 研判灯 + 实时进度弹窗 + 设置面板
├── preset/kexi-crypto/
│   ├── kexi-plugin.mjs           ← kexi_* 工具集 + 纪律段 + 状态心跳 + 持久团队预置 + SCRIPT_WHITELIST(25脚本)
│   └── skills/                   ← 3 个技能（研判工具集 / 团队编排 / token 纪律）
│       └── crypto-market-analysis/scripts/  25 个 Python 脚本 + tests/
├── track-record/                 ← 战绩档案库（ledger.jsonl / runs / score / evidence / report.html）
└── locale/{en,zh}.json · icon.svg
```

数据流（单币研判，即 `kexi_run(mode=pipeline)` 的六步）：

```
fetch_klines.py ──→ indicators.py ──→ assemble_report.py ──→ validate_report.py --fix
   (双源K线+质检)     (MA/MACD/RSI/       (契约草稿+成员         (11 键闸门+停更拦截)
                       筹码峰/ATR)         结论覆写合并)
                                                    ↓
                 五段式文本 + present 文件卡片 ←── dashboard.py ──→ compact_report.py
                       (进度弹窗实时显示每一步)    (自包含 HTML)      (≤900 字符上下文摘要)

旁路（可选，v1.2.0）：kexi_cli ──→ agy / codebuddy / mimo（本地 CLI 交叉验证，结论回流并标注来源）
```

**活动流数据源**：host 订阅 `ctx.on('session/event')`（DSH 权威事件源，与 `dsh-pet` 同源），
把 `turn/step/tool/subagent` 事件归一成节点写入按会话索引的环形缓冲（每会话上限
`maxEventNodes`，默认 60），`tool/result` 以 `callId` **回溯标记**原节点（附耗时与结果摘要），
避免一个工具产生两个节点刷屏。前端轮询 `/kexi-dashboard/activity` 渲染时间线。

## 核心数据铁律与纪律

由 `kexi-plugin.mjs` 注入系统提示词（`kexi:policy` 段）+ 各成员 persona 双保险：

1. **数值事实** — 所有价格/指标必须来自脚本落盘输出，禁止凭记忆报价或编造；
2. **已收盘口径** — 一切指标基于最后一根**已收盘** K 线（`drop_open_bar` 全链统一剔除），数据源降级换粒度必须如实声明 `interval_actual`；
3. **如实降级与受限明示** — 停更（日线 >3 天 / 周线 >10 天无新收盘）、缺口、流动性不足（ADV20 <3M USD 一票否决）必须显式警告；2026 年 X/Twitter 无可用免费读源，必须向用户明示该限制，情绪面以 F&G / CMC 热搜替代；大额已上 CEX 币资金流入由 DefiLlama 稳定币流向覆盖；
4. **位置与多周期同看** — 给出评级前必须交代 90 日区间位置与距高点回撤，高位且放量多日（拥挤度）严厉扣分；逆周/月线大周期的反弹只能定性为弱反弹，不给正面评级；
5. **事前可证伪与风控反向约束** — 预测必须显式落盘 `entry_price`、`as_of` 和三情景完整价格区间；守拙与望潮判定为高危的标的坚决剔除，坚持宁缺毋滥，不得为凑数硬推；
6. **红队强制** — 深研时**证伪必须最后派且必须真派**：守拙管风险闸门、望潮管情景推演，**没有人专门攻击结论前提**是真实的编排空缺。主理人不得把红队意见当"又一个风控意见"消音；反对与望潮冲突时两条都呈现并下调整体置信度；
7. **结论置信度显式化** — 快答输出 `confidence`（高/中/低 + 逐条扣分理由）。**低置信自动收回「立即入场」**——方向可以猜，仓位不能赌；
8. **决策必须留痕** — 出结论即 `prediction_log.py record`，7 天后 `reconcile` 自动对账（按评级/置信度/是否给过立即入场分组出校准表）。未走完窗口与无数据的样本**一律不计入命中率**；
9. **跨源并列而非二选一** — 多源可用时并列给出两套数字并标差异（`derivs_sentiment.py --cross-source`）。跨所绝对额由各所体量结构性决定，只作参考不计入分歧；
10. **实盘保守与绝对受控** — 密钥本地 Windows DPAPI 当前用户加密存储不上云、严禁提币权限；实盘交易与保活调仓默认 DRY_RUN 与 notify_only，风控硬闸门不可突破，任何真实资金操作**必须经用户显式确认**；
11. **免责必备** — 任何研判输出必须附 7×24 不构成投资建议免责声明。

## v1.5.0 六大升级能力

1. **选币质量升级（P1）**：
   - `position.py`：90日区间位置、回撤幅度、20日涨幅分位、放量持续天数（高位拥挤严扣分）、窄幅横盘识别；
   - `timeframe.py`：日线本地重采样为周线/月线，识别全周期共振与逆大周期反弹；
   - `backtest.py`：点时间历史回测（无前视偏差），以**中位数与胜率**校准规则。
2. **多面数据源（P2）**：
   - `datasources.py`：RSSHub 中文热点镜像、DEX 热币、DefiLlama 稳定币资金流、合约持仓/费率/多空比、Alternative.me 恐慌贪婪指数、CMC 热搜等 7 大源并发采集；单源失败优雅降级。
3. **真实 CEX 接入与实盘风控（P3）**：
   - `cex_keystore.py`：Windows DPAPI 本地加密存储不上云，安全体检强制拦截提币权限；
   - `cex_risk.py`：保守档硬性风控闸门（单笔上限、杠杆上限、止损必填、强平距离、单日频次限制、`HARD_CEILING` 绝对上限）；
   - `cex_adapter.py`：Binance/OKX/Gate/MEXC 实盘适配，默认 DRY_RUN，必须用户确认。
4. **长期保活与多渠道通知（P4）**：
   - `keepalive.py`：定时（默认每小时）与 6 类异动信号触发，默认 notify_only；
   - `kexi_notify.py`：企业微信、WorkBuddy、QQ机器人、落盘（强制兜底）多渠道推送。
5. **Token 消耗统计（P5）**：
   - `token_stats.py`：会话日志多帧 zstd 解析，排除 chunk 冗余虚报，按任务来源分类汇总。
6. **项目历史实操经验建档交付（P6）**：
   - `track_report.py`：单文件自包含 HTML 战绩报告（战绩总览、判断点分析线、三法交叉止盈止损、底仓分批策略）；
   - `track-record/`：实操预测建档存盘（台账 `ledger.jsonl`、运行记录、回测证据、评分看板）。

## Token 消耗优化点

参考包（WorkBuddy 版）审计出的最大浪费源是"大 JSON 回对话 + 成员全文转述"。本包七处结构性收口：

| # | 机制 | 效果 |
|---|------|------|
| 1 | kexi_* 工具只回**摘要+路径**：pipeline 产物全部落盘 `kexi_out/`，工具返回 ≤900 字符 digest | 单轮研判上下文从数十 KB 降到 <1.5KB |
| 2 | `compact_report.py`（契约报告 → 极简 JSON/文本） | 报告全文只在磁盘，成员读文件不读对话 |
| 3 | 委派模板 ≤3 行（任务+输入路径+输出要求），角色纪律在 persona 里不重复 | 4 次委派省 ~2-4KB |
| 4 | 成员 persona 硬上限（数脉≤600字/指北≤700字/望潮≤600字/守拙≤500字/证伪≤400字），对话内明细最多 10 根 | 成员结论总量封顶 |
| 5 | tool-result-pruner `6144/3072/1024`（比宿主默认更紧一档） | 意外大结果自动剪 |
| 6 | compaction-basic + command-compact 全量挂载 | 长会话自动压缩 |
| 7 | `tool-web` 关闭 fetch、无 workflow/ralph/mcp 行、spawn 不继承父上下文（无 fork provider） | 减常驻工具面与派生开销 |

量化口径详见 [docs/token-budget.md](docs/token-budget.md)。

## 可视化与实操战绩

**主交付 — 单文件离线 HTML 看板**（`dashboard.py`，纯 stdlib 手绘内联 SVG，无外部 CDN 依赖，深浅色自适应）：
K线蜡烛+均线系统、核心技术指标矩阵、关键位阶梯分布、7~30 日三情景预判、风险定级与异常监测五大板块；
有 portfolio 输入时追加组合面板。生成后主理人以文件卡片（present）交付，浏览器直接打开即用。

**实操战绩交付 — 自包含 HTML 战绩报告**（`track_report.py`）：
单文件离线 HTML 报告，提供：①战绩总览与超额收益（vs BTC）；②判断点分析线；③三法交叉验证止盈止损点；④底仓分批策略。

**次通道 — 标题栏研判灯**（本包 host+client 插件）：
K析 preset 会话常驻显示；普通会话仅在有研判活动时出现。
⟳ 蓝色呼吸=研判中（附 symbol 与 `步骤 x/6`）、✓ 绿=完成、✗ 红=失败、灰=就绪；点击弹出详情面板，多会话并行时逐一列出。

**实时进度弹窗 — 解决"长时间不知道插件在干什么"**：
点击灯（或点状态栏）显示**节点时间线**：host 订阅 DSH 权威事件源
`session/event`，把每一次工具调用翻译成可读节点——在干什么（`📊 跑研判流水线 ETHUSDT` / `📊 运行 position.py`）、
谁在干活（`🧑‍🔬 子智能体启动：指北·技术指标`）、本地 CLI 参与（`🤖 agy 执行`）、
拿到了什么（`↳ ETHUSDT 研判完成 risk=中 · 耗时 1.2s`）、pipeline 走到第几步
（`📈 第 3/7 步：组装契约报告`）。耗时与失败原因都直接可见，不再黑箱等待。

**本地 CLI 编排与内置交叉验证**：`kexi_cli` 探测本地 CLI 桥（agy / codebuddy / mimo），流水线第 5 步自动派发复核，结论回流写入 report。无 CLI 时诚实降级。

**设置面板**：设置 → 插件 → K析研判团，可调弹窗开关、CLI 优先级与开关、筛选池大小、ADV20 门槛、单笔风险预算、默认周期与取数根数；落盘 `$DSH_HOME/kexi-settings.json`。

## 目录结构

```
K析研判团/                          ← 本仓库（DSH bundle）
├── package.json                    bundle 声明（patch/client/icon/locale/exports）
├── icon.svg · locale/{en,zh}.json  插件卡片
├── cordis.patch.yml 位置 → home-plugin/dsh-kexi-crypto/
├── home-plugin/dsh-kexi-crypto/    host 行 + 研判灯 + 进度弹窗 + 设置面板
├── preset/kexi-crypto/
│   ├── kexi-plugin.mjs             kexi_* 工具集 + 纪律 + 心跳桥 + CLI 自动交叉验证 + SCRIPT_WHITELIST(25脚本)
│   └── skills/
│       ├── crypto-market-analysis/ SKILL.md + scripts/×25 + registry/ + references/ + templates/ + tests/
│       ├── kexi-team-orchestration/ 工作流 A–I 编排 SOP
│       └── token-budget-discipline/ token 纪律速查
├── track-record/                   战绩档案库（ledger.jsonl / report.html / evidence / runs / score）
├── scripts/verify.mjs              打包自检（静态接线 + D9 防回归，改包后必跑）
├── scripts/test-runtime.mjs        运行时行为回归（129 项通过，npm test）
├── docs/                           需求核对表、停滞诊断、回测、数据源调研与契约文档
└── README.md · INSTALL.md · CHANGELOG.md
```

## 文档

| 文档 | 内容 |
|------|------|
| [HANDOFF.md](HANDOFF.md) | **接手必读**：架构要点、标准工作流、环境事实、血泪教训、未闭环问题 |
| [INSTALL.md](INSTALL.md) | 安装/升级/卸载/Python 环境/10 条坑点 |
| [docs/requirements-checklist.md](docs/requirements-checklist.md) | 14 项需求完成度逐条核对表（A-F 全覆盖） |
| [docs/session-2128-stall-diagnosis.md](docs/session-2128-stall-diagnosis.md) | 会话停滞诊断与恢复指引 |
| [docs/data-sources-china-2026-09.md](docs/data-sources-china-2026-09.md) | 2026 国内网络环境多面数据源实测报告 |
| [docs/report-contract.md](docs/report-contract.md) | `kexi.report/1` 11 键契约 + 校验规则表 |
| [docs/token-budget.md](docs/token-budget.md) | token 预算实测与优化机制明细 |
| [CHANGELOG.md](CHANGELOG.md) | 详细更新日志（v1.5.0 重大升级） |
| `preset/.../SKILL.md` | 25 脚本 CLI 手册 + 契约字段速查 + 铁律 |
| `preset/.../kexi-team-orchestration/SKILL.md` | 工作流 A–I 编排 SOP |

## 排错

| 症状 | 处理 |
|------|------|
| 预设列表里没有「K析研判团」 | `dsh restart`；`dsh plugin --profile web list` 确认已装；看 DSH 控制台 `[kexi]` 报错 |
| `kexi_run` 报「无法启动 Python」 | 设环境变量 `KEXI_PYTHON=C:\path\to\python.exe` 后重启；或确认 `python` 在 PATH |
| 研判灯不出现 | 灯需要 host+webServer 同进程（桌面/`dsh serve` 默认满足）；纯 CLI TUI 无此服务属预期 |
| 进度弹窗不弹/无节点 | 弹窗只在**点状态灯**时手动唤出（不自动弹）；检查总开关 `showProgressPopup` 是否被关；节点只来自**当前生效会话** |
| 设置面板改动没生效 | 设置落盘 `$DSH_HOME/kexi-settings.json`；`maxEventNodes` 等有边界收敛（20–200），越界会被夹回 |
| `kexi_cli` 说没有可用 CLI | 对应 CLI 桥插件（agy/codebuddy/mimo first-bridge）未安装或已在设置里被关；属预期降级，不是故障 |
| 会话出现「subagent depth 1 exceeds maxDepth 0」 | v1.2.1 已修复（D9）；若仍出现说明旧版还在跑——重启 DSH 后 `kexi_cli list` 正常、成员委派成功即新 bundle 生效 |
| report.json 无 crosscheck 或 status=skipped | 正常：无可用 CLI / 超时 / 设置关闭了 autoCliCrosscheck 都如实记 skipped，`reason` 字段写明原因 |
| 技能列表里没有 crypto-market-analysis | 跑 `node scripts/verify.mjs`（校验 customSkillDirs `!!js` 表达式）；重启（技能根 watch 通常免重启） |
| 看板打开空白 | 检查是否把 `.md` 当 `.html` 打开；`dashboard.py --input` 必须接**校验通过**的 report.json |
| 报告 validate 报停更 error | 正常拦截——该标的日线已停更 >3 天，换标的或降周期 |

---

*本插件所有输出为技术面研判参考，不构成任何投资建议。加密资产 7×24 交易，风险自负。*
