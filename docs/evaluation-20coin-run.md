# K析研判团插件 · 20 币选币任务全程跟踪评估报告

**跟踪对象**：会话「给我挑选20个近期看涨的虚拟币」（lead `session-724b7e22`，工作目录 `C:\Users\lcl\Desktop\K析工作文件夹`）
**跟踪方式**：全程读取 5 个会话的加密日志（lead + 4 成员），采集工具调用、团队消息、活动流路由、成果物落盘与 token/缓存数据
**插件版本**：v1.4.6 运行（跟踪期间抓到 2 个实战 bug，已修复为 **v1.4.7** 待发版）
**评估人**：开发侧智能体（本会话）  **报告时间**：2026-09-29

> **勘误说明**：本报告初稿把"跨运行产物错归属"归因为"会话启动早于修复热同步、
> `latest.json` 未写入"。**该结论是错的**。深入取证后确认真正的根因是
> **`workspaceRoot()` 解析错误**（详见 §二·修复 1）——`latest.json` 其实**写成功**了，
> 只是被写进了 DSH 后端安装目录，所以 host 在真实 cwd 里读不到。初稿的错误已改正，
> 其余数据（token/缓存/时间线）不受影响。

---

## 一、任务执行概况（全程时间线）

| 时间 | 阶段 | 执行者 | 关键动作 |
|---|---|---|---|
| 01:46 | 建队 | lead | `kexi_ensure_team` 一次成功，4 持久成员 01:48:39-43 同时就位 |
| 01:48-02:51 | 初筛+扩池 | 数脉 | screener 连续 5 轮参数递进（top100→300→wide→500），期间 api.binance.com 451 封锁，自主探测降级链（data-api.binance.vision 可用）并改脚本 |
| 02:24-03:03 | 富集+质检 | 数脉 | enrich_picks：F&G=74、资金费率、盘口深度、流通盘、停更/锚定剔除（111→86 可用），构建 TierA/TierB 候选池 30 个 |
| 03:04-03:18 | 技术复核 | 指北 | 逐一计算 30 币指标，输出 Top20 排序（GRAM 100分领先） |
| 03:18-03:29 | 情景收敛 | 望潮 | 7 日三情景概率收敛（乐观 31.4% / 基准 54.9% / 悲观 13.7%），自行校验均线排列一致性并修正 |
| 03:26-03:27 | 风险定级 | 守拙 | 20 币四档定级：极高 3 / 高 15 / 中 2 / 低 0，附 6 条纪律与 4 处数据缺口 |
| 03:29-03:43 | 汇编交付 | lead | 组装 top20_report.json / top20_dashboard.html / top20_report.md，`present` 4 份成果物，回合结束 |

**总耗时约 1 小时 57 分钟**，无人工干预，无中断重试成功恢复（llm/retry 自动重试若干次）。

## 二、v1.4.6 三项修复的实战表现 + 抓到的 2 个 bug

### 🔴 修复 1（严重）：工作区根解析成了 DSH 后端安装目录

**同一根因的三个表现**（全部有日志/文件系统实证）：

| # | 表现 | 证据 |
|---|---|---|
| 1 | `kexi_run` 相对路径 `FileNotFoundError` | 成员会话 event 278：`kexi_out/picks/GRAMUSDT_1d.json` 找不到；改成绝对路径（event 288）立刻成功 |
| 2 | `kexi_validate` 解析到后端目录 | event 348 报错路径 = `...\backend\dsh\node_modules\@deepseek-ai\dsh\lib\kexi_out\top20_report.json` |
| 3 | `latest.json` 写进后端目录 → 看板错挂 | 后端 `lib\kexi_out\latest.json` 实测存在（624 B，`kind:dashboard`）；该目录还堆积 `ALGOUSDT_1d_*`（09-28，**说明更早的运行也中招**） |

**根因**：`workspaceRoot()` 读 `ctx.get('sandboxPolicy').workspaceRoot`。该 Service 与
字段都真实存在，但字段文档原文是 *"The absolute `workspace-write` **fallback** root for
calls **without a session cwd**"* —— 语义是"无会话 cwd 时的**部署兜底根**"（本机 =
DSH 后端 `lib` 目录），**不是会话工作区**。

**因果链**：`latest.json` 写到后端目录 → host `readLatestArtifact(cwd)` 在真实 cwd
找不到标记 → 回退 mtime 扫描 → 命中上一任务遗留的 `pythusdt_dashboard.html` →
活动流把旧看板错挂到新会话。**这正是 v1.4.6 修复 #1 想解决的问题，但被上游的路径
bug 架空了**——功能写了却永远读不到。

**修复**：改用官方契约 `sandboxPolicy.resolve({ session }).workspaceRoot`（文档明确
"会话 cwd 是其 workspace-write 边界"）。回退顺序：`resolve()` → `session.header.cwd`
→ 裸 `workspaceRoot` → `process.cwd()`；`syncCwd()` 同源。
**回归测试** `[J]` 5 条按真实部署形状构造（只有 `resolve()`、`workspaceRoot` 设诱饵
目录、会话带 `header.cwd`），已验证**对旧代码 5/5 失败**、对新代码全过。

### 🟡 修复 2：`kexi_news` schema 缺声明，一调用即被拒

lead 会话 event 39/40 实测：`tool "kexi_news" returned invalid output:
"value.news_available" is not a declared property (additionalProperties: false)`。
根因：统一 `mkTool` 的 `TEXT_SCHEMA` 是 `additionalProperties:false` 却没声明
`news_available`，而该工具返回值带它 → **v1.4.6 新增的新闻功能等于完全没生效**。
已补声明。**本次会话仍是旧代码，故新闻面实际缺席**；成员按纪律未反复重试（符合设计）。

### 🟢 修复 3：autoEnsureTeam（v1.4.6 修复 #2）

lead 显式调用了 `kexi_ensure_team`（event 18）一次成功建 4 人，**未走到自动兜底路径**，
故 once-per-session 语义本次未被实战触发，留待下次验证。

### 其他观察（未修）

- **成员会话 cwd 在活动流为空**：`sessionCwd()` 读 `session.header.cwd`，但成员会话桶
  建立时可能尚未拿到；不影响功能，影响可读性。
- **artifact 路由本身安全有效**：`/kexi-dashboard/artifact?id=a1` → HTTP 200 / 73,825 B，
  id-only + realpath 校验按设计工作；错的只是**登记了哪份文件**（上游路径 bug 所致）。

## 三、Token / 缓存实测（回应此前"缓存命中机制"讨论）

| 会话 | 角色 | 消息数 | 输入 tokens | cacheRead | 命中率 | 输出 |
|---|---|---|---|---|---|---|
| session-724b7e22 | lead | 61 | 3,312,505 | 3,158,272 | **95.3%** | 29,046 |
| dfcc1737 | 数脉 | 92 | 6,533,803 | 6,359,296 | **97.3%** | 58,705 |
| f53a48a7 | 指北 | 57 | 4,946,293 | 4,696,832 | **95.0%** | 31,666 |
| d51e1044 | 望潮 | 36 | 2,552,919 | 2,418,176 | **94.7%** | 26,537 |
| 466db134 | 守拙 | 12 | 839,246 | 717,824 | **85.5%** | 16,764 |
| **成员合计** | 4 人 | 197 | 14,872,261 | 14,192,128 | **95.4%** | 133,672 |
| **运行合计** | 5 会话 | 258 | **18,184,766** | 17,350,400 | **95.4%** | 162,718 |

**关键结论（对比历史）**：
- 本次运行整体命中率 **95.4%**，而历史两次同 preset 研判运行（pyth 5.4%、10 币 5.2%）几乎全冷。**变化不是代码改动带来的——是 provider/模型换到了支持 prompt caching 的通道**（本次成员跑在 deepseek-v4.1-flash、lead glm-5.3-flash，会话内连续命中）。
- 成员占输入 **81.8%**（成员/lead 比 449%）——印证此前"成员是成本大头"的判断，但在 95% 命中下，未命中绝对值仅 ~83 万 tokens，缓存优化空间已很小。
- splice（团队消息重写历史）本次高频发生（lead 17 次、数脉 14 次）**却未破坏命中率**——证明此前"splice 是冷启动元凶"的猜测不成立，可从候选原因清单划掉。
- 待办：历史低命中是"当时 provider 不缓存"这一解释成立与否，需要下次同 provider 复测；本次运行内 provider 未切换，数据干净。

## 四、用户上轮质疑的方法论问题（选币质量维度）

跟踪期间用户指出核心痛点：**初筛偏高位币、缺周线/月线维度、缺消息面与链上资金面**。本次运行的实际表现：

- **位置偏差已被量化证实**：对本次 picked 25 币测 90 日区间位置，中位 66.3%，≥80% 高位 12 个（48%）；screener 信号全部是趋势/动量类（MA排列/MACD/RSI蓄势/突破20日高加分），**没有距高点回撤、区间位置、低位横盘时长任何字段**——高位延伸币天然占优，与用户观察一致。
- PYTH 案例复盘：上次报告其实已标注 RSI14=80.3、高于 MA20 36.8%、风险=高、"多头中继后段末端特征渐显"，但**该信息在初筛字段里不可见**，到风控层才出现——初筛和风控之间缺一个"位置/拥挤度"门槛。
- 望潮本次虽然给了"中低置信度、追高风险大于补涨机会"的总结论，且守拙给了"无一低档"的定级，**说明团队对高位风险有感知**，但这只是事后提示，没有反向影响入选名单。
- **消息面**：kexi_news 本次因 schema bug 实际缺席；即便正常，当前也只做"是否可搜"探测，未接入币世界等垂直源。实测币世界 API 不可直连（SSL 失败）；可用的免费渠道有 CoinPaprika events（OK）、Cryptocompare news（需key）、CoinGecko global（OK）；链上/衍生品可用的有 Binance openInterest（OK）、blockchain.info（OK）、DexScreener（OK）、DefiLlama（超时不可用）。
- **链上/资金面**：screener 目前完全没有 DEX 热币、稳定币大额进出、CEX 钱包异动、社媒热度等字段；enrich_picks 已有的资金费率/盘口深度/流通盘是很好的起点，但都是 CEX 内数据。

## 五、结论与建议（优先级排序）

**做得好的**：
1. 团队协作全链路零人工干预跑通，建队/派发/收件/汇编节奏清晰，`宁缺毋滥`（25→30→20）被执行。
2. 数据源降级链实战验证有效（451 封锁下自动切换镜像）。
3. 缓存命中率 95%+，provider 层缓存已足够，插件层无需自建缓存（此前讨论的最终答案）。
4. 成果物链路完整（json/md/html 三件套 + present 卡片），artifact 路由安全校验有效。

**待修（已在 v1.4.7 完成）**：
1. 【已修】工作区根解析错误（§二·修复 1）——最严重，且**影响范围超出本次运行**（后端
   目录里 09-28 的 `ALGOUSDT_1d_*` 说明更早的运行也把产物写错了地方）。修复后
   `latest.json` 才会真正落到用户工作区，v1.4.6 的修复 #1 才算真正生效。
2. 【已修】`TEXT_SCHEMA` 补 `news_available` 声明（否则 `kexi_news` 永远报错）。
3. 【待办】成员 cwd 显示修复（活动流可读性，低优先级）。

**建议纳入下一轮功能设计（回应用户上轮质疑，未实现）**：
1. 【高】screener 增加"**位置/拥挤度**"维度：90 日区间位置、距 90 日高点回撤、20 日
   涨幅分位——并在打分里给高位延伸币扣分或加 veto。这是用户 pyth 案例的直接对应项：
   上次 pyth 报告其实已标注 RSI14=80.3、高于 MA20 36.8%、风险=高，但**该信息不在初筛
   字段里**，到风控层才出现，无法反向影响入选名单。
2. 【中】初筛与风控之间加一道"位置闸门"，让望潮/守拙的高位风险结论能**反馈到名单**，
   而不是仅作为事后提示（本次运行结论已说"追高风险大于补涨机会"，但 20 币照样交付）。
3. 【中】消息面接入可用的免费源：实测 **CoinPaprika events（可用）**、CoinGecko global
   （可用）；币世界无公开 API（SSL 直连失败），Cryptocompare news 需 key。
4. 【低/需评估】链上资金面（DEX 热币 = DexScreener 可用、稳定币大额进出、CEX 钱包异动、
   KOL 社媒监测）：DexScreener / Binance openInterest / blockchain.info 实测可用；
   DefiLlama 超时；X 社媒监测受 API 成本所限，建议单独立项而非塞进现有流水线。
5. 【中】用户提出的"历史数据量化初判"：技术上可行（现有脚本链已落 K 线），建议先做
   **信号回测**（把 screener 的打分规则放到历史窗口上测命中率/回撤），用数据判断
   动量信号在高位的真实胜率——这同时也能量化回答"选在高位风险有多高"。

**遗留观察项**（下次运行继续跟踪）：
- autoEnsureTeam 的 once-per-session 语义仍未被实战触发（本次 lead 显式调用了 ensure）。
- 历史低命中的根因（provider 不缓存 vs splice）本次已排除 splice；等下次 provider
  不变时复测可完全闭环。

---

*数据来源：`dsh-home/sessions/` 五个会话日志全文 + `kexi_out/` 产物 + `/kexi-dashboard/activity` 快照；跟踪期间未干预任务执行。*
