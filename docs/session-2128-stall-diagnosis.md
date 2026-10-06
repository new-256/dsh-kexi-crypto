# 会话「读交接文档」停滞诊断报告

> 诊断时间：2026-09-29（排查会话 `session-63c0cc5f` 中完成）
> 目标会话：`session-2128bebf-7d45-4cef-9bc8-10e2c7a452cc`（标题「读交接文档」）
> 结论先行：**会话没有被"修坏"，也不是代码 bug——是 LLM 供应商限流（429）叠加目标系统自动续跑的"resume→blocked→pause"空转循环，最终目标被置为 paused；用户最后一条真实消息从未得到回答。**

---

## 1. 会话身份与状态（从持久化存储核实）

| 项 | 值 |
|---|---|
| 会话 id | `session-2128bebf-7d45-4cef-9bc8-10e2c7a452cc` |
| 标题 | 读交接文档（首条用户消息即「读交接文档」） |
| cwd | `C:\Users\lcl\Desktop\DSH插件开发\K析研判团`（仓库根） |
| preset | `kexi-crypto`（后切换为 `cordis`） |
| 创建 | 2026-09-28 22:09（epoch 1790604568867） |
| 最后活动 | 2026-09-29 13:22:16（`goal/change pause rev=18`） |
| 事件数 | 3723 |
| 目标 | `goal-6765d041-...`（rev **18**，phase **paused**，maxGoalRounds 40，roundsStarted **0**） |
| inbox | 残留 **2 条** `<goal_round>` 消息（rev 15、17）反复被拒 |
| modelSelection.pending | `{provider:"ark", model:"ark-code-latest"}`（崩溃时用户正在切模型） |
| 归档状态 | **未归档**（workspace.json `archivedSessionIds` 不含它）→ 排除 archived-session-gate |

来源：`dsh-home\storages\session_projcache\sessions\session-2128bebf-...json`（version 7 持久化），
`dsh-home\storages\workspace.json`（工作区 1327bb46「K析研判团」含本会话与当前排查会话 63c0cc5f）。

## 2. 停滞时间线（真实事件序列）

| 时间 | 事件 | 含义 |
|---|---|---|
| 12:01:55 | `turn/end` turn 18 | **`reason.kind=error`：`upstream_error: Upstream request failed`（PI_AI_ERROR）**。当时 agent 正在查 host 插件如何给新脚本开工具入口（最后一次工具调用是 grep index.mjs 白名单），LLM 上游失败，turn 以 error 结束 |
| 13:16:56 | `goal/change resume rev=4` | 目标系统自动续跑第 19 turn，插入 `<goal_round>` |
| 13:16:57 | `turn/end` turn 19 | **`reason.kind=blocked`**——没有 step/start、没有 LLM 调用，pre-step 直接 reject |
| 13:19:17 | turn 20 step 1 | 用户真实消息「项目历史实操经验成功预测/失败，应建档存盘…」到队；LLM 调用 **×5 重试全部 429**：`All available accounts are currently rate-limited`（chatgptpay） |
| 13:19:38 | `turn/end` turn 20 | error（RATE_LIMIT），5 次重试耗尽 |
| 13:19:54–13:22:16 | turns 21–26 | 每次 `resume → 插 goal_round → turn/start → turn/end blocked → pause`，**零 LLM 调用**，rev 一路涨到 18 |
| 13:22:16 | `goal/change pause rev=18` | 目标最终停在 **paused**（这就是会话"无法继续"的可见形态） |

## 3. 根因分析

### 3.1 直接原因：模型供应商限流
- 崩溃前（12:01）LLM 上游 `PI_AI_ERROR`；续跑时（13:19）chatgptpay 对 `deepseek-v4.1-flash` 连续 429「All available accounts are currently rate-limited」，5 次退避重试全失败。
- 会话历史里同日还出现过 `Request exceeded 570s limit`、`AI provider temporarily unavailable`（provider 不稳是当天常态）。

### 3.2 结构性原因：目标自动续跑空转（resume→blocked→pause 打摆）
- 从后端源码核实（`dsh-agent-loop/lib/index.js` L958-961 + `dsh-goal-round-driver/lib/index.js` L279-300）：
  - `turn()` 里 `preStep` 返回 `{kind:"reject"}` → `turnEnds={kind:"blocked"}`，**不发 LLM 请求**；
  - goal-round-driver 的 `agent/pre-step` 钩子里 `validReservation()` 校验 `goal.phase==="active" && goal.activation==="armed" && attempt.phase==="claimed" && round===roundsStarted+1`，**不满足即 `{kind:"reject"}`**；
  - 每次 resume 只把 `phase` 置为 active，但 attempt/arming 状态在 error 后没建立 → 每条 `<goal_round>` 都被拒 → blocked → driver 又 `requestDrive` → resume → 循环打摆。
- 证据：13:16:56–13:22:16 共 8 次 resume/pause 抖动，且 **turns 19、21-26 都没有 step/start 与 assistant/attempt**（零 token 消耗），与「pre-step reject」完全吻合。

### 3.3 排除项（都核实过）
- **不是归档**：session 不在 workspace `archivedSessionIds`，`underArchivedSession` 不成立。
- **不是会话文件损坏**：zstd 多帧完整可解，projcache 结构完整。
- **不是工作区/路径问题**：cwd 正常，仓库文件齐全。
- **不是插件代码 bug**：v1.5.0 脚本均在仓库且 100% 测试通过；崩溃点只是"接线调研"环节。

## 4. 未完成事项（新会话要接的活）

1. **用户最后一条真实消息从未得到回答**（13:19:17 到达，撞上 429）：
   「项目历史实操经验成功预测/失败，应建档存盘整理思路用于迭代智能体，提炼技能，优化脚本，交付成果可视化报告，有完整的判断点分析线，止盈止损点，底仓策略等」——该需求已被越界 subagent 以 `track-record/` 目录 + `track_report.py` 部分落地，但**面向用户的正式交付与答复尚未给出**。
2. **v1.5.0 发布工作完全未做**（详见 HANDOFF §5.5）：
   - `kexi-plugin.mjs` 的 `SCRIPT_WHITELIST` 仍是旧 14 个脚本，**11 个新脚本（position/timeframe/backtest/datasources/cex_keystore/cex_adapter/cex_risk/kexi_notify/keepalive/token_stats/track_report）均未入白名单**，插件无法经 `kexi_run` 调用；
   - host `index.mjs`、`cordis.patch.yml`、`client.js`、README/INSTALL/CHANGELOG 均无 v1.5.0 内容；
   - `package.json` 仍为 **1.4.7**；
   - 已装副本（`profiles\web\node_modules\dsh-kexi-crypto`）仍是旧 v1.4.7，**未同步**。
3. 全部测试当前基线：`verify PASS (12 顶层键 / 341 行 patch)` · `test-runtime 129/129` · `Python 100%`（本次排查已重跑确认）。

## 5. 恢复建议（给新会话）

- **不要试图在原会话续跑**：goal 已 paused 且 inbox 残留 2 条被拒的 `<goal_round>`，即使换个可用模型也可能继续被 goal-round-driver 拒绝（attempt 状态不对）。用户已计划开新会话，这是正确路径。
- 新会话直接读 `HANDOFF.md`（已更新）→ 跑 §7 标准工作流确认基线 → 按优先级完成 v1.5.0 接线发布 + 交付"项目历史实操经验建档存盘"答复。
- 若想抢救原会话：可在新会话里把 goal 完整目标复制重建（目标原文见本报告 §4 或 HANDOFF §6），不要复用原 goal id。
