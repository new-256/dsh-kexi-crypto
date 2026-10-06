# Backlog #1 核查结论（基于真实会话日志，非推测）

> 日期 2026-09-28 · 取证脚本：`.scratch/*.py`（只读扫描 `dsh-home\sessions` + `.session-cleaner-trash`）

## 方法

解压全部 DSH 会话日志（zstd → jsonl），按 `session` 头的 `agentPreset == "kexi-crypto"`
筛选，逐条统计 `tool/call` 事件（**不是**文本出现次数——系统提示里的工具名会被误计为调用）。

## 事实

kexi-crypto 会话共 22 个：**lead 6 个 / 队友子会话 16 个**。

| lead 会话 | 任务（首条用户消息） | 调用 kexi_ensure_team | 队友子会话 |
|---|---|---|---|
| session-5538552b | （日志已清理，只剩子会话） | 日志缺失，但子会话带 initTail ⇒ **是** | 4 |
| session-6fc2320e | （日志已清理，只剩子会话） | 日志缺失，但子会话带 initTail ⇒ **是** | 4 |
| session-8b93a56e | 给我找10个将来预期走势较好的虚拟币 | **是**（第 2 个工具调用） | 4 |
| session-ea75ca19 | 看一下pyth未来走向 | **是**（第 1 个工具调用） | 4 |
| session-da82e305 | 为我配置一下模型（oioi API） | 否 | 0（非研判任务，正确） |
| session-2128bebf | 读交接文档 | 否 | 0（非研判任务，正确） |

**决定性证据**：16 个子会话全部带 `kexi_ensure_team` 专属标记
`【初始化】收到本条后只用一句话回复「数脉/指北/望潮/守拙 就绪」`——
该字符串只存在于 `kexi-plugin.mjs` 的 `TEAM_MEMBERS[].prompt`，
模型不可能逐字复现。子会话 `subagent/descriptor` 的 `mode=continuable provider=spawn`
与 label 也与 `TEAM_MEMBERS[].description` 逐字一致。

```
label='数脉 · 行情取数与数据质检（团队唯一数据入口）' mode=continuable provider=spawn
label='指北 · 指标计算与形态/量价解读'            mode=continuable provider=spawn
label='望潮 · 7 日三情景概率推演与证伪条件'        mode=continuable provider=spawn
label='守拙 · 风险定级与可执行风控提示'            mode=continuable provider=spawn
```

调用后的返回也正常（真实回放）：

```
kexi kexi_ensure_team: OK
digest:
已创建持久成员：数脉、指北、望潮、守拙
当前团队名册：
· 主理人：running   · 数脉：running   · 指北：running   · 望潮：running   · 守拙：running
```

## 结论

**原 Backlog #1 的前提不成立——拉起能力没有空转。**

`kexi_ensure_team` 在全部 4 个真正的研判 lead 会话里都被调用了，
且每次都在会话最前面（第 1 或第 2 个工具调用），4 位持久成员全部建成、面板显示 5 人。
两个没调用的 lead 都是非研判任务（配置模型 / 读交接文档），**不调用才是对的**。
复盘里"lead 直接用了原生 send_message/spawn 而非 kexi_ensure_team"是把
`kexi_ensure_team` 建队**之后** lead 用 `send_message` 按序派活当成了绕过——
那正是设计要的行为（建队用 kexi_ensure_team，派活用 send_message）。

## 但发现一个真实的健壮性缺口（本次修复）

拉起**完全依赖模型自觉执行提示词第 0 步**，没有任何代码级兜底：

- 6 个 lead 里有 2 个没调用（虽然都恰好是非研判任务）；
- 一旦模型跳过第 0 步就直接 `kexi_run` 跑研判，面板只会显示主理人 1 人，
  插件"5 人团队"的核心承诺静默失效，且**没有任何地方会提示这件事**。

这不满足 §8「队友供给要确定性、幂等」。修复方向（v1.4.6）：
把建队从"纯提示词约定"变成**确定性代码路径**——首次真正的研判调用
（`kexi_run` / `kexi_dashboard`）时自动幂等补建，尊重 `persistentTeam` 开关，
并把结果如实回报进工具 digest，不再依赖模型记不记得第 0 步。
