# K析研判团 v1.9.23 修复结果（委派工具面收敛 + 内部成员预设隐藏）

- 结项时间：2026-10-30 01:55（北京时间，timeapi.io 取时）
- 基线：`dsh-kexi-crypto@1.9.22`（git `07cd884`）　目标：`1.9.23`
- 方案：`docs/repair-plan-v1.9.23.md`（上游逐条取证 + 最小 harness 证据）
- 任务卡：`task-2`（T2 委派工具面收敛 + 内部成员预设对用户隐藏）

---

## 一、实际改动（diff 摘要）

| 文件 | 改动 |
|---|---|
| `home-plugin/dsh-kexi-crypto/lib/members.mjs`（新，78 行） | 五位成员身份**唯一真源**：`preset id → key/cn/role/short`；`memberOfPreset` / `isInternalPreset` / `isKexiPreset`；`MEMBER_PRESET_IDS` |
| `home-plugin/dsh-kexi-crypto/lib/preset-roster.mjs`（新，101 行） | 设计 B：包裹 `agentPresets.remoteExportList`。单进程 `Symbol.for('kexi.rosterFilter.v1')` 防重复安装；返回新对象；仅过滤 `kexi-member-*`；形状/重复/被替换 → throw；dispose 用 descriptor 精确还原 |
| `home-plugin/dsh-kexi-crypto/cordis.patch.yml` | delegation 组 **6 行 → 1 行**（`toolName: subagent` + `maxDepth: 1` + `allowedAgentPresets` 5 项）；5 个成员预设 name 压缩（含「内部」）、description 单行 ≤30 字、order `501-505 → 901-905` |
| `home-plugin/dsh-kexi-crypto/lib/index.mjs` | `inject=['agentPresets']`；`MEMBER_CN` 由真源摊平（删 5 个旧工具名）；`describeTool('subagent')` 四态文案；`KEXI_TOOLS` 换 `subagent`；`isKexiSubagentCall`（只按 preset 判 kexi）；身份映射 `childPreset/childParent/childCwd/subagentNodes` + 回填 label/icon/member；`subagent/catalog` 停止嗅探 label；`pushNode` 返回落库对象；`subagent/start|end` **改由 `header.parentSession` 反查父会话**（P1-5）；安装 roster 过滤器；`script` 字段改用已解析 args；版本 fallback 1.9.23 |
| `home-plugin/dsh-kexi-crypto/lib/client.js` | 名册 `tool:` → `preset:`（5 个 kexi-member id）；点亮匹配键改 cn/preset/key + 节点 `member/agentPreset`；tooltip 用 role |
| `preset/kexi-crypto/kexi-plugin.mjs` | `kexi_dashboard` 后续指引文案改 `subagent(agent_preset=…)`，移除旧工具名 |
| `preset/kexi-crypto/skills/kexi-team-orchestration/SKILL.md` | 新增 §〇「委派通道」对照表；20 处旧工具名 → `agent_preset=kexi-member-*` |
| `package.json` | 版本 1.9.23 + v1.9.23 变更说明（description 头部） |
| `CHANGELOG.md` | 新增 v1.9.23 / **补上缺失的 v1.9.22 条目** / 修 L692 死链（不再嵌不存在的截图） |
| `README.md` / `HANDOFF.md` | 版本与破坏性变更说明；HANDOFF 新增 §6.6（parent 实参不存在）、§9.0（inject 爆炸半径 + P1-4 不对称）、测试地图补 3 条 |
| `scripts/verify.mjs` | delegation 单行契约 `auditDelegation()` + **旧 6 行负对照**；成员 preset id 从真源取（不再抄一份）；成员预设 name/desc/order 断言；client TEAM preset 三向一致；**取不到 js-yaml 由 notes.push 改 problems.push（P0-2）** |
| `scripts/test-preset-contract.mjs` | 重写：单行 + 真实 `Config` schema + `allowedAgentPresets` 双向对应；4 条负对照（旧 6 行/幽灵预设/漏成员/旧字段） |
| `scripts/test-preset-roster.mjs`（新，126 行） | 上游映射仍在 → 过滤语义 → 5 条负对照 → dispose 可逆 |
| `scripts/test-runtime.mjs` | 夹具补 `agentPresets`（含 `get`）；旧 `agentType` 假身份用例改 `agent_preset` 新链路；新增 **4 次 `subagent` tool/call（全部 JSON 字符串 args，2 条带 `agent_preset`）** 四态断言、身份晚到回填（label+icon+member）、父会话桶归属、持久成员两种时序、非本团队预设的 subagent 不算 kexi |
| `scripts/test-card-kexi-filter.py` | 白名单断言改 `subagent` + 旧名必须消失 + `isKexiSubagentCall` 判据 |
| `scripts/check-preset-mount.mjs` | 新增：插件**确实激活**（roster 过滤器标记）、浏览器 roster 已隐藏且顺序/主预设完好、`readDocument` 5 个成员可读、kexi-crypto 文档委派行恰好 1 个、旧工具名零残留 |
| `scripts/test-v1922-fixes.mjs` | 600 行断言纳入 `members.mjs` / `preset-roster.mjs` |
| `scripts/run-all-tests.ps1` | 接入 `test-preset-roster.mjs`（**UTF-8 BOM 已回查保留**：EF BB BF） |
| `tools/preset-probe/roster-wrap.mjs`（新，工具目录，**不进包**） | 真机取证：真实 `runProfile` + 网关同款作用域取方法 |

---

## 二、设计 A 落地证据

- 补丁实测：`delegation` 组仅 1 个 `dsh-tool-subagent` 行；`allowedAgentPresets` 5 项与五个声明行、`members.mjs`、client 名册**四处一致**（verify 双向断言）。
- 上游契约（真实安装包 `@deepseek-ai/dsh-tool-subagent@0.2.1-alpha.5`）：
  `Config` 校验通过；`allowedAgentPresets` 无重复、无未声明引用（`test-preset-contract` 双向断言）。
- 身份链路（`test-runtime` 178 项全绿中的新用例）：
  - `agent-preset/selected` → `subagent/start`：`委派成员 指北·技术指标`；
  - 身份晚到：先显式「身份未识别」→ header 到达后回填 `label/icon=👤/member=shouzhuo` 且清 `unknownIdentity`；
  - 节点落**父会话桶**（`header.parentSession` 反查），不再写进成员自己的桶；
  - 非本团队预设（`standard`）会话的 `subagent` 调用**不算 kexi 活动**（工具名不是判据）；
  - 持久成员 catalog/team-member 两种时序均显示中文名，且模型自由文本 label 不再被当成员名。

## 三、设计 B 落地证据（真机，真实引导）

`node tools/preset-probe/roster-wrap.mjs`（真实 `runProfile` + 网关同款 `ctx.extend({invocation}).get(service)` + `implementation` 解析）：

```
包裹前（浏览器路径）15 行: standard, ptc, minimal, cordis, kexi-crypto, cordis-agy,
  cornell-paper-readme, opencode-first, claude-first, codex-first,
  kexi-member-market-data, kexi-member-technical, kexi-member-forecaster,
  kexi-member-risk, kexi-member-falsifier
包裹后（浏览器路径）10 行: standard, ptc, minimal, cordis, kexi-crypto, cordis-agy,
  cornell-paper-readme, opencode-first, claude-first, codex-first
✓ 恰好少 5 行；✓ 丢掉的正是不该出现的 5 个内部预设；✓ standard/kexi-crypto 仍在
✓ 相对顺序与过滤前一致；✓ 内部 list() 仍 15 行；✓ readDocument 5/5；✓ resolve 5/5 无 broken
✓ mount() 未被过滤器拦截（报的是上游作用域约束）
✓ 只新增 remoteExportList 一个自有属性 + 一个挂载 symbol
✓ 负对照：缺 presets 数组 / 行缺 id / 无方法 / 重复安装 / 被替换后卸载 全部抛错
✓ dispose 后 roster 恢复完整且可再安装
ROSTER-WRAP: PASS（27 项）
```

### 3.1 第一版探针栽在哪（原始失败输出，务必留档）

```
DBG own on svc = true
DBG root:    sameInstance=false seesWrapper=false rows=10
DBG extend:  sameInstance=false seesWrapper=false rows=10
DBG isolate: sameInstance=false seesWrapper=false rows=10
```

结论：`ctx.get()` 返回**作用域代理**，在原始实例上 `defineProperty` 用"实例身份比较"
验证不出任何东西；必须按网关 `Reflect.get(callReceiver, implementation)` 的真实解析路径验证。
（第一次跑 `TYPERT.invocations[].invoke()` 还遇到另一个坑：生成描述符的 `invoke` 用的是
**线名 `list`** 而不是 `implementation: remoteExportList`，所以它直接返回数组、
看起来"过滤没生效"——真正的映射在 `dsh-api-gateway` L1211-1213。）
第二次栽在同一处：dispose 里用普通属性访问比较函数身份，作用域代理每次返回新建的绑定函数
→ 正常卸载被误判成"被替换"。改用 `getOwnPropertyDescriptor().value` 后通过。

## 四、门禁结果（2026-10-30 01:54 本机实跑）

```
node --check 12 文件（index/client/members/preset-roster/kexi-plugin/6 个脚本） → 失败 0
python -m py_compile scripts/test-card-kexi-filter.py                            → OK
node scripts/verify.mjs               → verify: PASS (12 顶层键 / 625 行 patch)
node scripts/test-preset-contract.mjs → PRESET-CONTRACT: PASS
node scripts/test-preset-roster.mjs   → PRESET-ROSTER: PASS
node scripts/test-runtime.mjs         → RUNTIME: ALL PASS (178 通过 / 0 失败)
node scripts/test-card-props.mjs      → ✅ 卡片传值回归通过
node scripts/test-v1922-fixes.mjs     → v1.9.22 回归：PASS（104 项）
node scripts/test-card-kexi-filter.py → 38/38 通过
```

### 4.1 负对照（证明门禁会红，不是装饰）

1. **verify 注入违规**：把委派行 `toolName: subagent` 临时改成
   `market_data_specialist` → `✗ delegation 组契约不合规` → 恢复后 PASS。
2. **verify P0-2**：`DSH_BACKEND=C:\__no_such_backend__` →
   `✗ 未找到 js-yaml（patch YAML 结构校验无法执行）…禁止放行`
   （修复前同样命令是 `PASS` + exit 0，24 处 ok() 被静默跳过）。
3. `test-preset-contract`：旧 6 行形态 / 幽灵预设 `kexi-member-ghost` / 漏一个成员 /
   旧字段 `persona+toolFilter` —— 四条全部被判坏。
4. `test-preset-roster`：无方法 / 服务为 null / 重复安装 / 缺 presets / 行缺 id /
   被替换后 dispose —— 六条全部抛错。
5. `test-runtime`：坏 JSON args → `委派（参数解析失败）`；无 preset → `继承主理人预设`；
   未知 preset → `未知预设` + detail 回显 id。

## 五、审计存档

- 方案与取证：`docs/repair-plan-v1.9.23.md`
- 本结果：`docs/repair-result-v1.9.23.md`
- 真机探针（开发用、不进包）：`tools/preset-probe/roster-wrap.mjs`
- 体检报告（Lead 团队）：`docs/audit-2026-10-30.md`
- 变更日志：`CHANGELOG.md` v1.9.23 / v1.9.22 条目
- 交接：`HANDOFF.md` 头部 v1.9.23 段落、§6.6、§8、§9.0

## 六、未做 / 遗留（明确列出，不静默留空）

> 更新于 task-5 收口：分工调整后，P1-1（test-scripts-smoke.py）、P1-3+P2-7
> （check-preset-mount.mjs 真 mount/最小 profile/token 脱敏）、P1-6（test-stop-plan.py /
> test-veto-disagreement.py）**归修复团（xiufu-tuan，task-6）**，本会话未再改动这三个文件。

1. **逐步骤硬超时未交付（P2-6 部分完成）**：`run-all-tests.ps1` 已有**总超时闸**（每步之前
   检查总预算）与硬件监控联动，但**单步硬超时**在实测中出现"命令没跑却报全绿"的假绿风险
   （Start-Job 传脚本块被序列化；改子进程后退出码传播仍不可靠），本轮**主动移除**该选项，
   宁可写"未交付"也不给门禁留一个会假绿的开关。建议由修复团用 `System.Diagnostics.Process`
   + 临时 `.ps1` + `exit $LASTEXITCODE` 的成熟配方重做，并**用负对照证明超时确实变红**。
2. **`check-preset-mount.mjs` 本轮未执行**：它用真实 `runProfile` 拉起**完整 profile**
   （连 QQ 官方/飞书/OneBot、把明文 GUI token 与 OneBot access_token 打进 stdout、在用户
   目录留截图）。体检 P2-7 要求先最小 profile 化 + token 脱敏；该文件与 P1-3 真 mount 已
   移交修复团。本轮加在该文件里的断言（插件已激活 / roster 已隐藏 / 内部 list+readDocument
   完整 / 委派行恰好 1 个）保留，但**未运行**。跑之前需 `sync-install.ps1` 同步安装副本
   （当前安装副本仍 1.9.22）。
3. **安装副本未同步**：`%APPDATA%\...\dsh-kexi-crypto` 仍是 1.9.22；用户重启前需
   `powershell -File scripts/sync-install.ps1 -Pack`，否则界面看着是新的、服务端是旧的
   （HANDOFF §9.16 同类陷阱）。
4. **全量 `run-all-tests.ps1` 未跑**：本轮跑的是 `-Quick`（31/31 全绿，已含 verify /
   preset-contract / preset-roster / runtime / v1922 / card-props / 卡片过滤 / 团队关系图 /
   Python 安全套件等）；`-Quick` 会跳过的端到端 4 步（test_batch / runmarker / cwd_fix /
   test_all.py）留到发版门禁与修复团的门禁项一起跑。
5. **P1-4 不对称未修（体检结论，刻意）**：持久团队成员继承主理人预设、拿全套工具；
   只有按需委派成员是裁剪后的角色面。已在 HANDOFF §9.0 写明与绕行建议。
6. **P1-1 / P1-6 未做**：按分工归修复团。
7. **顺手清理**：已删 `scripts/__pycache__/`（跑 py 测试留下的产物）；
   `.gitignore` 已有 `__pycache__/` 与 `*.pyc` 规则（本轮未改，属沟通团范围）。

## 七、体检驱动修复结果（task-5）

### 7.1 改造前红 → 改造后绿（实测原文）

| 项 | 改造前 | 改造后 |
|---|---|---|
| P0-2 | `DSH_BACKEND=C:\__no_such_backend__` → `PASS` + exit 0 | `✗ 未找到 js-yaml（patch YAML 结构校验无法执行）…禁止放行` → exit 1 |
| P0-1 | `test-team-graph.py` 夹具全喂对象；`script` 恒 null | 夹具改喂 JSON 字符串（保留指北对象形态）→ `test-team-graph: 49/49 通过`（新增：字符串形态解析成功 + 坏 JSON 归「其他工作」且 activity 含「参数解析失败」） |
| P1-5 | 节点回落到 `identity.id` → 写进成员自己的桶 | `test-runtime` 新增断言：节点在**父会话桶**、不新增成员桶节点 → `RUNTIME: ALL PASS (178)` |
| P1-2 | 未接线 | `run-all-tests.ps1 -Quick` → 31/31 全绿（含 test-v1922-fixes 104 项） |
| P1-7 | 无机械断言；CHANGELOG 缺 v1.9.22 | `verify` 新增四处版本一致断言（package.json/CHANGELOG 头部/README/HANDOFF）+ 已补 v1.9.22 条目 → `verify: PASS` |
| P2-1 | 死文件在库 | 已删除；`verify` 断言文件不存在 → `verify: PASS` |
| P2-6 | 无总超时/无硬件监控/34 处死参数/乱码 | 总超时闸生效、`_hw-monitor.ps1` 联动（写 `%TEMP%\kexi-hw`）、死参数清零、UTF-8 输出、BOM 保留（EF BB BF）→ `全部通过：31 / 31` |

### 7.2 负对照

- P0-2：见上表「改造前」列（同一命令改前 PASS / 改后 FAIL）。
- P0-1：坏 JSON 用例属负对照——若无显式标注，`activity` 里不会出现「参数解析失败」，该断言必红。
- P1-5：`ok(!r.json.sessions.some(s => s.id === childTech && ...))` 是反向断言——若回落到成员桶，立刻红。
- 门禁自检：`verify` 的「旧 6 行委派形态必须被判坏」负对照、`test-preset-contract` 三条负对照、`test-preset-roster` 六条负对照均在每次运行时执行。

### 7.3 本轮踩到的两个"假绿/自伤"（留档）

1. 逐步骤超时用 `Start-Job` 传脚本块会被序列化成字符串 → 作业里把整行当模块名加载，命令**根本没跑**而 `$LASTEXITCODE` 为 `$null` → `[int]$null=0` → 报 31/31 全绿。改用子进程后又有退出码传播问题。**结论：该方案不可靠，本轮移除，宁可写"未交付"也不留给门禁一个会假绿的开关。**
2. 硬件监控最初写进 `.kexi-test-tmp`，而 `test-runtime.mjs` 启动时 `rmSync` 整个目录 → EPERM 让行为回归整整一红。改写到 `%TEMP%\kexi-hw` 后恢复。**共享临时目录的清理权必须唯一。**
