# Token 预算与消耗机制 · token-budget

K析研判团相对参考包（WorkBuddy 版）最大的结构改造目标：**把 token 消耗从"对话内搬大 JSON"
改成"磁盘落文件、上下文只留摘要与路径"**。本文列七处收口机制、实测数字与调参位。

## 实测基线（ETHUSDT 1d，220 根，端到端 pipeline）

| 环节 | 落盘体积 | 进入对话上下文 |
|------|---------|---------------|
| `fetch_klines.py` 原始 K 线 | ~55 KB JSON | 0（工具不回，仅 `kexi_out/*.json` 路径） |
| `indicators.py` 指标 | ~6 KB | 0 |
| `assemble_report.py` 报告 | ~9 KB | 0 |
| `validate_report.py` 校验结果 | — | `{"ok":true,"errors":[],"warnings":[],"changed":[]}` <100 B |
| `compact_report.py` 摘要 | ~1 KB | **digest ≤900 字符**（工具最终返回） |
| `dashboard.py` 看板 | 62,369 B HTML | 0（present 文件卡片交付，非内联） |
| **单轮 `kexi_run(pipeline)` 净入上下文** | | **约 1.0–1.4 KB**（digest + out_files 路径 + 状态行） |

对比：若把上述原始 JSON 全部读回对话（参考包早期做法），单轮 ≈ 70–80 KB ≈ **2 万+ token**。
pipeline 化后压到 ~1 KB 级，**降幅 ~98%**。多轮 4 成员深度研判工作流（A 全流程）成员结论合计亦封顶 ~2.4 KB（见下 persona 上限）。

## 七处结构性收口

1. **kexi_* 工具只回摘要+路径**（`kexi-plugin.mjs`）
   `kexi_run` 六步产物全落 `<工作区>/kexi_out/`，工具返回值是 `compact_report.py` 出的 ≤900 字符 digest
   + `out_files` 路径数组。模型要细节就 `read` 指定文件，而不是被动吃全量。
2. **compact_report.py 契约压缩器**
   报告 JSON → 极简摘要（默认 ≤1200 字符；pipeline 第 6 步取其 stdout 再由工具 `clip` 到 900）；
   成员读文件不读对话历史里的报告。
3. **委派模板 ≤3 行**（`kexi-team-orchestration` 技能）
   委派给成员只写「任务 + 输入文件路径 + 输出要求」，角色纪律已在各成员 persona 里，不重复粘贴。
   4 次委派省 ~2–4 KB。
4. **成员 persona 输出硬上限**（`cordis.patch.yml` 角色行）
   数脉≤600字、指北≤700字、望潮≤600字、守拙≤500字，且对话内明细最多最近 10 根 K 线（全量落盘）。
   四成员结论合计封顶 ≈2.4 KB。
5. **tool-result-pruner 阈值更紧**（`compaction` 组内）
   `thresholdChars: 6144, headChars: 3072, tailChars: 1024`——比宿主通用预设（agy 用 8192/4096/1024）
   再紧一档；任何单次工具结果超 6144 字符即剪到 头 3072 + 尾 1024 + 省略标记。
6. **compaction 双策略挂载**
   `dsh-compaction-basic`（自动摘要旧轮）+ `dsh-command-compact`（`/compact` 手动）+ 上面的 pruner，长会话不爆上下文。
7. **工具面与派生开销裁剪**（相对宿主通用预设）
   - `tool-web` 关 `fetch`（只 search 摘要，不整页入上下文）；
   - 无 `workflow-*` / `ralph` / `mcp` 行（研判用 subagent 角色团，JS 工作流引擎与 ralph 循环是纯常驻 token）；
   - 委派走 `provider: spawn` 且**无 fork provider** → 子代不继承父对话，角色拿自包含任务书即可。

## 调参位（用户层覆盖，勿改安装包内文件）

在 `%APPDATA%\DSH Desktop\dsh-home\cordis.patch.yml` 覆盖 `compaction` 组的
`tool-result-pruner.config`（整组复制后改三数字）：

```yaml
# 更省（超长会话/低配额）：thresholdChars 4096 / headChars 2048 / tailChars 1024
# 更稳（怕剪掉关键数字）：  thresholdChars 8192 / headChars 5120 / tailChars 2048
```

`kexi_run` 的 digest 上限在 `kexi-plugin.mjs`（pipeline 第 6 步 `compact` stdout `clip(...,900)`、
`kexi_validate`/`kexi_dashboard` 里 `clip(..., 900)`）；改源码后本地路径安装 `dsh restart` 即生效。
成员字数上限在 `cordis.patch.yml` 四个角色 `persona` 文本内（改文案即可，非结构化字段）。

## 反模式（会毁掉上面的收益，别做）

- ❌ 主理人把 `kexi_out/*.json` 整文件 `read` 回对话再复述 → 用 `kexi_dashboard`/`compact` 摘要。
- ❌ 委派成员时把整段 persona/铁律再抄一遍 → 只发 3 行任务书。
- ❌ 一步 `kexi_run(pipeline)` 能干的活，手工拆 6 条 shell 命令逐条贴输出 → 直接调工具。
- ❌ 给 `kexi_validate` 传未过契约的原始指标 JSON 期望它"生成报告" → 组装是 `assemble_report.py`/`pipeline` 的职责。
