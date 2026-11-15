# A3（stub LLM 真实委派）— 停手记录：UNVERIFIED 及卡点

- 执行者：回归团　时间预算：Lead 给定 15 分钟硬上限，本记录写于**预算内主动停手**
- 结论：**UNVERIFIED（未完成）**。未产出任何声称通过的证据，未交半成品脚本。

## 已完成的前置定位（可复用，不白做）

| # | 事实 | 证据位置 |
|---|---|---|
| 1 | llm 服务有官方 adapter 扩展点 | `dsh-llm/lib/index.js:1842` `registerAdapter(providers, adapter)`；L1666 文档写明 `with ctx.llm.registerAdapter(providers, adapter)`；要求 adapter 覆盖 `providerInfo/providerRetryPolicy/imageRequestPricing` 等 `LlmAdapter` 成员 |
| 2 | agent 循环确实经 llm 服务取流 | `dsh-agent-loop/lib/index.js:1201` `loopCtx.llm.prepareCall(proposedConfig, signal)`；`:1084` `preparedCall?.stream(request) ?? loopCtx.llm.stream(request)` |
| 3 | 建 Agent 的入口 | `dsh-agent-loop/lib/index.js:1893` `async createAgent(ownerCtx, options)`；`AgentLoop` 注入 `["agents","sessions","llm","tools","systemPrompt","sessionProjections"]`（`:1544-1551`） |
| 4 | 引导脚手架可直接复用 | `scripts/check-preset-mount.mjs`：最小 profile（`dsh-base + dsh-web-app + dsh-kexi-crypto`）、沙箱 `DSH_HOME`、`runProfile`、`acquireScope → createScope → mount` |

## 卡点（两条，任一不解决都无法写出可信 stub）

**卡点 A — 单轮驱动 API 不可发现。**
在 `dsh-agent-loop/lib/index.js` 内检索 `async prompt(` / `async send(` / `async runTurn(` / `async submit(` / `.prompt(` → **0 命中**。
`createAgent()` 返回的是「published handle」，但其驱动方法名与调用契约在构建产物里没有可读签名
（该包是打包后的 lib，只有 `createAgent`/`setupAndPublish` 等内部方法可见）。没有它就无法「让它跑一轮」。

**卡点 B — stub 必须产出的 `tool_call` 事件形状不可枚举。**
`llm.stream(request)` 返回的流事件类型（文本增量 / 工具调用 / 结束标记的字段名与顺序）在 `dsh-llm/lib/index.js`
里没有可读的类型定义或示例；`registerAdapter` 只校验 adapter 是 `LlmAdapter` 实例成员齐全。
不知道确切事件 schema 就写出的 stub，只能是**猜形状**——那正是「交半成品充证据」，故不做。

## 为什么没有绕过去

- 不能改成「直接调 `describeTool()` 或直接 emit 伪造事件」：那是把被测对象 mock 掉，违反 Lead 的三条断言之一
  （① 工具必须被**真实执行**）。
- 不能改成「用真模型跑一轮」：那是方案 A（旁证），且需额度与在线可用性，本轮未授权。

## 若继续，下一步最小路径（建议）

1. 从 `dsh-llm` 的 `.d.ts`（若有）或 `dsh-agent-loop` 的调用点反推流事件 schema（约需 1 轮专门排查）；
2. 确认 handle 的单轮驱动方法（可从 `dsh-cli`/`dsh-sdk-app`/`dsh-headless` 里找真实调用样例——这些包会调用同一 API，
   是最快的「正确用法」来源）；
3. 用最小 profile 引导 + `registerAdapter('deepseek', stub)` + `createAgent` + 驱动一轮，
   stub 首轮返回 `subagent` 工具调用（args 为 JSON 字符串含 `agent_preset: kexi-member-technical`）；
4. 断言三件事：工具真执行 / 子会话 `header.agentPreset` / 活动流节点 `委派成员 指北·技术指标`。
