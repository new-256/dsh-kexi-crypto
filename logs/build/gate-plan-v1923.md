# v1.9.23 出厂门禁计划（回归团 · Phase 1 交付）

- 执行者：回归团（huigui-tuan）
- 本文件性质：**只准备、不验收**。Phase 2 逐条打勾时把「结果」列填上才算交付。
- 改造前基线 commit：`07cd884`（v1.9.22）
- 取证批次：`logs/build/.current-ts`（每跑一次门禁重写，指向同批日志前缀）
- 报告落点：Phase 2 → `logs/build/gate-20261030-v1923.md`
- 预定标识：`runId=RUN-20261030-V1923`，`冻结号=FREEZE-20261030-V1923`

---

## 0. 摘要（Phase 1 结论）

| 结论 | 依据 |
|---|---|
| 门禁执行器已就绪并自测通过（含超时强杀） | `selftest-fast` PASS、`selftest-hang` exit 124 + 抓到卡死 node PID |
| 改造前静态门禁基线 = 全绿 | `verify PASS (12 顶层键 / 661 行 patch)`、`PRESET-CONTRACT: PASS` |
| 安装副本当前与工作区**0 内容差异** | `sync-install -CheckOnly` → 154 文件 / 差 6 / 多余 0，6 项全是 T2 新增未同步文件 |
| 已定位 4 个会让门禁「假红/假绿」的环境陷阱 | 见 §3（PSModulePath、EOL 混合、3080、401） |
| 1 项断言（A3 真实委派）纯命令门禁取不到证 | **已定夺**：方案 B 作门禁证据（stub LLM 官方缝已定位），A 仅旁证；见 §5 A3 |
| 防回退判据原口径不具防回退能力，已收紧并经 Lead 认可 | 原口径改造前即满足（5 处）；收紧后**改造前基线 exit 1**（`agent_preset=0 subagent-calls=0`），依据留档见 §5.4.1 |
| 我曾误报「字符串形态零覆盖」，已自查更正 | 实测对象 11 处 / 字符串 5 处；真正零覆盖是 `agent_preset`。已报请 Lead 撤回转给预设团的错信息 |

---

## 1. 门禁执行器：`logs/build/run-gate.ps1`

仓库自带测试脚本都没有超时（规则 12 的硬伤），这个包装器补上三件事：**强制超时并强杀整棵进程树、同步硬件监控、全文落盘**。

```powershell
# 通用形态
pwsh -NoProfile -File logs\build\run-gate.ps1 `
     -Name <门禁项短名> -Command "<要跑的命令行>" `
     -TimeoutSec <秒> [-WorkDir <相对/绝对目录>] [-RunId ...] [-Freeze ...]
```

| 能力 | 实现 |
|---|---|
| 强制超时 | `WaitForExit(timeout)`；超时 → `taskkill /PID <包> /T /F` 杀整棵树，打印**强杀前残留子进程**（PID/进程名/命令行）+ stdout/stderr 末 40 行 |
| 硬件监控（规则 7） | 开跑前同步拉起 `scripts/_hw-monitor.ps1`，CSV 落 `<批次>-<名>.hw.csv`；CSV 未生成直接抛错，不静默跳过 |
| 全文落盘 | `<批次>-<名>.out.log` / `.err.log`（不再只看 tail） |
| 索引 | `logs/build/gate-index.jsonl` 追加一行：超时/退出码/耗时/日志路径/**互联网北京时间** |
| 退出码 | `0` 成功；`124` 超时强杀；`97` 核心 cmdlet 缺失（环境残缺，见 §3.1）；其它 = 命令自身退出码原样透传 |

**自测证据**（Phase 1 已跑）：

| 自测 | 期望 | 实测 |
|---|---|---|
| `selftest-fast`（`node -e console.log`） | 0 | PASS，0.3s，CSV 生成 |
| `selftest-hang`（node 睡 120s，超时 6s） | 124 + 卡死位置 | exit 124，8.1s，抓到 `PID 27604 node.exe -e "…setTimeout…"` |
| `smoke-beijing` | 索引带互联网北京时间 | `beijing_time=2026-10-30T01:36:35+08:00`，`time_source=timeapi.io+f.m.suning.com`，`clock_skew_s=-1` |

时间源规则（规则 24）：主 `timeapi.io`，备 `f.m.suning.com/api/ct.do`（epoch 毫秒）；两源差 >5s 记 `time_disc`；**两源都断就写 `source_unavailable`，绝不用本机时间顶替**（本机时间只写进 `local_clock`/`clock_skew_s` 作漂移参考）。

---

## 2. 改造前门禁基线（07cd884 / v1.9.22）

### 2.1 基线副本怎么拿的（这里有个坑，必须照抄）

```powershell
git -c core.autocrlf=false archive --format=tar 07cd884 -o dist\baseline-07cd884.tar
New-Item -ItemType Directory -Force -Path dist\baseline-07cd884 | Out-Null
tar -xf dist\baseline-07cd884.tar -C dist\baseline-07cd884
```

> ⚠ **不要用 `git worktree add`**：本仓库 `core.autocrlf=true` 且无 `.gitattributes`，worktree 检出会把 LF 全转成 CRLF
> （实测 `home-plugin/dsh-kexi-crypto/lib/index.mjs`：worktree 100743 B / 1821 处 CRLF，而 blob 是 98922 B / 0 处）。
> `git archive` 默认也吃 autocrlf，必须显式 `-c core.autocrlf=false`。校验方法：
> `git hash-object dist\baseline-07cd884\<file>` 与 `git rev-parse 07cd884:<file>` 相等
> （实测 `632e85cf5193130ec7d59ca9ce5644882e2b3512` 三者一致）。
>
> 结论：`dist/baseline-07cd884` 是**blob 基线**（行为基线），**不是工作区字节基线**。两者在 28 个文件上 EOL 不同，见 §3.2。

### 2.2 基线结果

| # | 门禁项 | 命令（工作目录 = `dist/baseline-07cd884`） | 结果 | 证据 |
|---|---|---|---|---|
| 1 | 静态接线 + 语法 | `node scripts/verify.mjs` | **PASS**（12 顶层键 / 661 行 patch，3.0s） | `logs/build/20261030-013513-baseline-final-verify.out.log` |
| 2 | 预设契约 | `node scripts/test-preset-contract.mjs` | **PASS**（0.7s） | `logs/build/20261030-013517-baseline-final-contract.out.log` |
| 3 | blob 基线 vs 安装副本 | `powershell -File scripts/sync-install.ps1 -CheckOnly` | **差 28 / 多余 0**（148 文件），**28 项全为 EOL-only** | `logs/build/20261030-013518-baseline-final-vs-installed.out.log`、`logs/build/baseline-installed-diff.txt` |
| 4 | 工作区 vs 安装副本 | 同 3（工作目录 = 仓库根，01:33 时点） | **差 6 / 多余 0**（154 文件），6 项全是 T2 新增未同步文件 | `logs/build/20261030-013357-baseline-sync-install-check-fixed.out.log` |

第 3 项的 28 项已逐项验证为纯 EOL 差异（`\r\n` → `\n` 归一后内容完全相同），不是代码差异；清单见 `logs/build/baseline-installed-diff.txt`。

第 4 项的 6 项（均为「安装副本缺失」，即 T2 正在写的新文件）：
```
home-plugin/dsh-kexi-crypto/lib/members.mjs
home-plugin/dsh-kexi-crypto/lib/preset-roster.mjs
docs/team/collaboration.md
docs/team/progress.md
docs/team/update-01.md
scripts/_probe-roster-wrap.mjs
```

> 基线是**移动靶**：Phase 1 期间 T2 在实时写工作区（01:28→01:34 之间文件数 151→154）。
> Phase 2 必须在「冻结窗口」内跑，且开跑瞬间记录 `git rev-parse HEAD` + `git status --porcelain -- ':!dist'` + `git diff | git hash-object --stdin`。

---

## 3. 环境事实（Phase 2 前必须知道，否则会误判）

### 3.1 PSModulePath 污染 → `Get-FileHash` 变 CommandNotFound（已修）

`Start-Process` 起的 PowerShell 5.1 原样继承上游 PowerShell 7 的 `PSModulePath`，其中
`c:\program files\powershell\7\Modules\Microsoft.PowerShell.Utility` 排在 5.1 同名模块**前面**，
5.1 加载 Core 版失败 → `Get-FileHash` 直接 CommandNotFound。

- 症状极隐蔽：`sync-install.ps1` 不报环境错，而是「每个文件都算不出哈希」，看起来像同步全错。
- 现场证据：`logs/build/20261030-013033-baseline-sync-install-check.out.log`（+ `.err.log`）、`…-retry`。
- Phase 1 复现结论：`&` 直接调用 = 正常；`Start-Process` = 必然复现（非竞态）。
- 对策（已在 `run-gate.ps1` 内固化）：子进程内把 `PSModulePath` 归一为 5.1 三个标准目录，并对
  `Get-FileHash`/`Get-ChildItem`/`Get-CimInstance` 硬断言，缺失即 `exit 97`。
- **旁证**：修好后同一命令 stderr 0 字节、输出正常（`…-check-fixed.out.log`）。

### 3.2 工作区 EOL 混合（29/149 个文件是 CRLF）→ 出厂字节可复现性风险

- 实测：`scripts/sync-install.ps1` 工作区 7144 B / 153 处 CRLF，而 HEAD blob 是 LF；`locale/en.json`、`kexi-plugin.mjs` 等同类。
- `core.autocrlf=true` 让 `git status` 对 CRLF/LF 都报「干净」→ **git 完全不设防**。
- 后果：`npm pack` 打的是**磁盘字节**，只要有人在冻结后 `git checkout -- .`、编辑重存、或跑 renormalize，
  这 28–29 个文件字节变化 → tgz 的 SHA-256 变，冻结号失效。
- 对策：冻结后禁止任何 git 写操作；打包前后各算一次 tgz SHA-256；必要时 `git -c core.autocrlf=false checkout` 统一后重新冻结。

### 3.3 `check-preset-mount.mjs` 会绑 **3080**，不是 GUI 的 60944

证据：`@deepseek-ai/dsh-web-app/cordis.patch.yml` → `port: !!js ctx.webStartup.port ?? 3080`，
`check-preset-mount.mjs` 只传 `args: ['--no-open']`（无 `--port`）。而 GUI 60944 是 Desktop 启动时带的 `--port`。
→ 两者不冲突；实测 3080 当前**空闲**。若 3080 被占，webserver 的 listen 失败会让 fiber 判 FAILED（boot 失败 exit 2），
这是 Phase 2 该脚本最常见的「非代码原因红」。

### 3.4 GUI 裸 HTTP 是 401 → GUI 验收只能走浏览器

`GET http://127.0.0.1:60944/` 无认证返回 **401 Unauthorized**（端口占用者 PID 30916）。
→ 任何 curl/Invoke-WebRequest 式冒烟都拿不到证据；GUI 验收必须用浏览器（DSH 浏览器工具或 playwright MCP）截图 + 读 console。
→ 也意味着 `/kexi-dashboard/status`、`/kexi-dashboard/activity` 同样需要认证，不能作为脚本侧证据源。

### 3.5 打包边界

`package.json.files` 含 `home-plugin/ preset/ locale/ docs/ scripts/ track-record/` + 6 个根文件；
`logs/`、`dist/`、`tools/`、`deps/` **不在** files 内（不会进包）。
→ 写进 `docs/evidence/` 的东西**会进 tgz**；原始日志写 `logs/build/` 才不进包。

---

## 4. Phase 2 待执行清单（逐条命令 + 期望 + 超时）

> 约定：每条命令都用 `run-gate.ps1` 跑（自带超时 + 硬件 CSV + 全文日志），`-RunId RUN-20261030-V1923 -Freeze FREEZE-20261030-V1923`。
> 任何一条 FAIL，报告里必须带「可复现命令 + 失败原文（日志行）」。

| # | 步骤 | 命令 | 期望 | 超时 |
|---|---|---|---|---|
| 0 | claim task-3 + 冻结取证 | `git rev-parse HEAD`；`git status --porcelain -- ':!dist'`；`git diff \| git hash-object --stdin` | 记录三者到报告表头 | — |
| 1 | 全量回归 | `run-gate.ps1 -Name full-regression -Command "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-all-tests.ps1"` | 末行「全部通过 N / N」，exit 0 | 1800s |
| 2 | 预设契约（单独） | `… -Command "node scripts/test-preset-contract.mjs"` | `PRESET-CONTRACT: PASS` | 300s |
| 3 | **真实引导挂载** | `… -Command "node scripts/check-preset-mount.mjs"` | `✓ clean` ×6、`PRESET-MOUNT: PASS`、exit 0；**先确认 3080 空闲** | 300s |
| 4 | 断言 A1（静态） | 见 §5 A1（verify.mjs 覆盖则引用其输出行，否则跑 `logs/build/assert-a1.mjs`） | delegation 组 1 行 + `allowedAgentPresets` 恰 5 项 | 120s |
| 5 | 断言 A2（宿主/浏览器双面） | 见 §5 A2 | 浏览器侧 list 不含 `kexi-member-*`；宿主侧 `list()`/`readDocument()` 仍见 5 个 | 300s |
| 5b | 断言 A3（方案 B，门禁必须） | 见 §5 A3：stub LLM + 真调 `agent_preset=kexi-member-technical` | 子会话 `header.agentPreset` 正确 + 节点显「指北」 | 300s |
| 5c | 补充单测 a–e（JSON 字符串 / 坏 JSON / 缺 preset / 未知 id / 事件路径） | `run-gate.ps1 -Command "node logs\build\assert-label-v1923.mjs"` | a: label==`委派 指北·技术指标`；b/c/d 四条边界字面量；e: 事件路径 label==`委派成员 指北·技术指标` | 120s |
| 5d | 静态防回退（`agent_preset`/`subagent` 覆盖） | `run-gate.ps1 -Command "node logs\build\assert-static-v1923.mjs"` | 改造后 `agent_preset≥2` 且 `subagent-calls≥2`（改造前基线 exit 1，见 §5.4.1） | 120s |
| 6 | 同步安装副本 + 打包预检 | `… -Command "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sync-install.ps1 -Pack"` | 末行「✅ 源与安装副本完全一致」，差 0 / 多余 0；`npm pack --dry-run` 段打印 version/total files | 900s |
| 7 | 安装副本核对 | `… -CheckOnly`；读安装副本 `package.json.version`；比对 `cordis.patch.yml` 单委派工具 | version=`1.9.23`；差 0 / 多余 0；delegation 组只 1 行 | 600s |
| 8 | GUI **数据段 + 代码段**（我做，见 §5.6-a/b） | a：进程内引导后跑 roster 过滤 / 宿主 list / `mount` 断言；b：`node scripts/test-hooks-order.mjs` + `node scripts/test-frontend-backend-parity.mjs` | roster 不含成员 + 宿主含 5 条 + mount 成功；hooks-order 与 parity 双 PASS | 600s |
| 8b | GUI **界面段**（**Lead 做**，见 §5.6-c） | Lead 用本会话已连浏览器复核 5 点 | 报告标 `UNVERIFIED(待 Lead 界面复核)`；**绝不自填 PASS、绝不伪造观察记录** | — |
| 9 | 出厂打包 | `npm pack --pack-destination dist` | 产出 `dist\dsh-kexi-crypto-1.9.23.tgz` | 600s |
| 10 | tgz 内容校验 | `tar -tzf dist\dsh-kexi-crypto-1.9.23.tgz`（见 §4.1 断言项） | 关键路径齐全、无 `logs/`/`dist/`/`node_modules/`、**不含 `scripts/_probe*`（有则 FAIL 并挂起出厂）** | 300s |
| 11 | 出品指纹 | `Get-FileHash -Algorithm SHA256` + `(Get-Item).Length` | 写进报告：绝对路径、文件名、字节数、SHA-256、runId、冻结号 | — |

### 4.1 tgz 内容校验断言项

必须存在：`package/package.json`、`package/home-plugin/dsh-kexi-crypto/cordis.patch.yml`、
`package/home-plugin/dsh-kexi-crypto/lib/index.mjs`、`package/home-plugin/dsh-kexi-crypto/lib/client.js`、
`package/preset/kexi-crypto/kexi-plugin.mjs`、`package/preset/kexi-crypto/skills/`（含 `crypto-market-analysis`）、
`package/scripts/`、`package/locale/zh.json`、`package/locale/en.json`。

必须不存在：`package/logs/`、`package/dist/`、`package/node_modules/`、`package/__pycache__/`、`*.pyc`。

**必须不存在（Lead 定夺 ②，硬门禁）**：任何匹配 `package/scripts/_probe*` 的条目。
探针脚本不得随包出厂；预设团会把它们移到 `tools/`（`tools/` 不在 `files` 内）。
**发现残留 → 记 FAIL 并挂起出厂**，不得「说明一下就放行」。

断言命令（三条都要落日志）：
```powershell
tar -tzf dist\dsh-kexi-crypto-1.9.23.tgz > logs\build\<批次>-tgz-list.txt
Select-String -Path logs\build\<批次>-tgz-list.txt -Pattern 'package/scripts/_probe'      # 期望 0 命中
Select-String -Path logs\build\<批次>-tgz-list.txt -Pattern 'package/(logs|dist|node_modules|.*__pycache__)/'  # 期望 0 命中
```

交叉核对：tgz 文件清单 ↔ `sync-install.ps1` 的 `$dirs`+`$rootFiles` ↔ `package.json.files` 三者一致。

---

## 5. 改造后必须新增的三条断言（Lead 指定，Phase 2 逐条打勾）

### A1 — 委派组唯一性与成员一一对应 ⬜

- 判据（静态半）：`delegation` 组（或等价 container）**只有 1 行** `dsh-tool-subagent`；
  该行 `allowedAgentPresets` **恰为 5 项**，且与 5 个成员预设声明一一对应（不多不少、无错字）。
- 判据（**挂载半，Lead 定夺 ①**）：在**挂载后的上下文 tools 列表**里复核：
  - `subagent` **恰好 1 个**委派工具（不能出现两个同名/同职责委派工具）；
  - 5 个旧成员工具名 **0 命中**：`market_data_specialist`、`technical_indicators`、
    `trend_forecaster`、`risk_assessor`、`falsification_challenger`。
  > 收集口径：先从 `allowedAgentPresets` 取权威 5 项（真源 `lib/members.mjs` 的 `MEMBER_PRESET_IDS`），
  > 再对挂载上下文注册的工具做名字集合比对。**这半必须做**——只查 YAML 挡不住「YAML 收敛了但插件仍注册旧工具」。
- 取证：静态半引用 `scripts/verify.mjs` 输出行；挂载半与 A2/3a 复用同一次进程内引导（见 §5.6-a）。
- 交付物：`logs/build/<批次>-a1.out.log`（打印「subagent 计数=1」「5 旧名命中=0」两行原文）。

### A2 — 双面可见性（浏览器过滤掉、宿主内部仍可读）⬜

- 判据三条同时成立：
  1. 真实引导后 roster 中 `kexi-crypto` **非 broken**（同 §4 第 3 步）；
  2. **浏览器侧** `agentPresets/list` 结果 **不含** `kexi-member-*`（过滤生效，UI 不暴露成员）；
  3. **宿主侧** 内部 `list()` / `readDocument()` **仍能读到 5 个成员预设**（过滤只作用于对外面，没把内部能力一起滤掉）。
- 取证：走 typert 描述符 `@deepseek-ai/dsh-agent-preset-registry#agentPresets/list` 的真实调用路径，
  对比「带过滤参数」与「宿主直连 `ctx.get('agentPresets').list()`」两份结果。
  T2 已留下 `scripts/_probe-roster-wrap.mjs` 可作起点，但**它不得随包出厂**（Lead 已通知预设团移到 `tools/`；
  出厂校验硬断言 tgz 内不含 `package/scripts/_probe*`，见 §4.1）。我的 A2 验证脚本只放在 `logs/build/`。
- 交付物：`logs/build/<批次>-a2.out.log`（打印两份 list 的 id 集合 + 差异集合）。

### A3 — 委派工具真调用一次（`agent_preset=kexi-member-technical`）⬜

> **Lead 定夺（2026-10-30）：以方案 B 作门禁证据；另补两条纯单测（§5.4，不混进 A3）；A 仅作旁证；C 不用。**

**A3 判据（方案 B 取证）**：子会话 `header.agentPreset === 'kexi-member-technical'`；
且喂给进度弹窗的 activity 节点渲染为「**指北**」而非通用标签。

**做法**：进程内真实引导 profile → 建 scoped agent context → 注入 **stub LLM**（离线、固定回复、不联网不耗额度）
→ 真调委派工具 `agent_preset=kexi-member-technical` → 断言上述两件事。

**先行验证项（Phase 2 第一件事）**：stub LLM 的接入方式——**已定位官方扩展点，可行性由「未知」上调为「高」**：

- `@deepseek-ai/dsh-llm/lib/index.js:137` 明确写着 `with \`ctx.llm.registerAdapter(providers, adapter)\``，
  即 llm 服务暴露了**注册 provider adapter 的官方缝**；
- `@deepseek-ai/dsh-agent-loop/index.js:1084/1201` 走的是 `loopCtx.llm.stream(request)` /
  `llm.prepareCall(proposedConfig, signal)` —— 只要 adapter 注册在**当前配置所用的 provider 名下**，
  预设会话就会自然打到 stub，无需改配置、无需改业务代码。
- 剩余待验细节：booted ctx 上 adapter 注册的时机（须在 agent 创建前）与 `prepareCall` 的返回形状。
  若某一步卡住，立即上报 Lead 改为「受限说明 + §5.4 单测覆盖」，**不自行降级成 PASS**。

**交付物**：`logs/build/<批次>-a3b.out.log`（含 child header 原文 + 节点 label 原文）。

### 5.4 A3 之外的补充单测（Lead 定夺 ① — 纯单测可覆盖，**不混进 A3**）⬜

防的是 HANDOFF §6.1 那个坑复发：`tool/call` 的 `arguments` 是 **JSON 字符串**，
早期 host 只认对象 → 线上标签全退化成通用值，而**回归测试传的是对象所以全绿**。

> **覆盖现状（Phase 1 二次核实；同时更正我此前向 Lead 报错的一句）**：
> - `scripts/test-runtime.mjs` 的 `tool/call` 注入**两种形态都存在**：对象形态 11 处；
>   **JSON 字符串形态 5 处**（第 **478 / 482 / 495 / 496 / 517** 行，单引号 JSON）。
>   → 我先前说的「全部传对象、字符串形态零覆盖」**是错的**（当时只看了 106–118 那一段），此处更正。
> - **真正的零覆盖**：`agent_preset` 作为**测试输入/断言对象**，在全部测试脚本里一次都没有
>   （`test-runtime.mjs` **0 处**；`verify.mjs` 仅 2 处，且都只是报错文案与注释里提到这个词，不构成覆盖）；
>   `tool/call` 中 `name:'subagent'` 的用例同样为 0。
>   而 v1.9.23 的身份信号**正是 `agent_preset`** → 这才是 §6.1 会复发的真缺口。
> - 附带结论：A1 的**静态半**其实已被 `verify.mjs:43` 覆盖（`allowedAgentPresets` 严格等于成员预设声明，
>   不一致即报错），所以 A1 真正要新增的是**挂载半**（tools 列表里 `subagent`=1、5 旧名=0）。
> - 附带发现：`test-runtime.mjs:153` 仍用**旧成员工具名** `agentType: 'technical_indicators'` 造 `subagent/start`，
>   156 行据此断言「指北」。v1.9.23 收敛后，这条用例守的是**已废弃的身份通道**，需同步改造（否则改造后照样绿，但不证明新链路）。

**身份来源（唯一真源）**：`home-plugin/dsh-kexi-crypto/lib/members.mjs` 的 `KEXI_MEMBERS`
（`cn`/`short`：数脉·取数质检、**指北·技术指标**、望潮·趋势推演、守拙·风险定级、证伪·红队证伪）。
正常成员 label = 固定前缀 `委派 ` + `<cn>·<short>`。
**断言必须从 `members.mjs` 读真值再拼，不得在测试里另抄一份中文**（否则映射改了测试不红）。

Lead 定夺的四条**字面量**断言（每条独立断言，禁止合并成「不是通用标签就算过」）：

| # | 喂入（`arguments` 为 **JSON 字符串**） | 必须满足（字面量） |
|---|---|---|
| a | `{"agent_preset":"kexi-member-technical","description":"…","prompt":"…"}` | `label === '委派 指北·技术指标'` |
| b | 坏 JSON（如 `{"agent_preset":"kexi-member-technical",` 截断） | `label.includes('参数解析失败')`；**且** ≠ 任何成员 label、≠ `委派成员`；`detail` = 原始 args 前 80 字符 |
| c | 合法 JSON 但**缺** `agent_preset`（通用委派 / 继承主理人预设） | `label.includes('继承主理人预设')`；**且** ≠ 任何成员 label、≠ `委派成员` |
| d | 合法 JSON，`agent_preset` 为**未知 id**（如 `kexi-member-unknown`） | `label.includes('未知预设')`；`detail` 回显该 id 原文；**且** ≠ 任何成员 label、≠ `委派成员` |
| e | **事件路径**：`emit('subagent/start', { agentType:'kexi-member-technical', … })`（**不是** `tool/call`） | 节点 `label === '委派成员 指北·技术指标'` |

> ⚠ **两种前缀是不同路径，断言必须分开写（Lead 补细节 ③）**：
> - `tool/call` 节点 label（`describeTool` 出）：`委派 指北·技术指标` —— 前缀 **`委派 `**，断言 = 用例 **a**；
> - `subagent/start` 事件节点 label（事件路径出）：`委派成员 指北·技术指标` —— 前缀 **`委派成员 `**，断言 = 用例 **e**；
> - **不得**用 `includes('指北')` 之类的宽松匹配把两者混成一条（那样任何一条路径坏掉都测不出来）。

- 实现路径（无需 DSH / 网络 / 第三方依赖，仓库内已有成熟配方）：
  照 `scripts/test-runtime.mjs` 的 `fakeCtx`（`on`/`inject`/`effect` + `fakeWs.register`）→ `host.apply(fakeCtx)`
  → a–d 用 `emit('session/event', sess, { type:'tool/call', data:{ callId, name:'subagent', arguments: '<JSON 字符串>' } })`
  → e 用 `emit('subagent/start', {...})`
  → 都读 `routes.get('/kexi-dashboard/activity')` 返回体断言对应节点 `label`/`detail`。
- 我的实现落在 **`logs/build/assert-label-v1923.mjs`**（我的写入范围），不改 `scripts/`。

### 5.4.1 静态防回退断言（Lead 定夺 ④ — 判据按实测缺口收紧，Lead 已认可）⬜

**为什么不能按「凡 JSON 字符串形态 ≥2 处」来判**：实测该形态**现在就有 5 处**（第 478/482/495/496/517 行），
用这个判据今天立刻 trivially 绿 → 是放水，挡不住任何回退。
**真正的缺口是 `agent_preset` 零出现**，所以判据必须锁定它。

**判据（收紧后）**：`scripts/test-runtime.mjs` 同时满足
1. 出现 `agent_preset` **≥ 2 处**（对应 §5.4 的 a–d 用例中的至少正常 + 一条边界）；
2. 出现 `name: 'subagent'` 的 `tool/call` 注入 **≥ 2 处**（确保身份走新工具名，不是旧成员工具名）。

**命令（工作目录：仓库根；超时 120s）**：
```powershell
pwsh -NoProfile -File logs\build\run-gate.ps1 -Name assert-static -Command "node logs\build\assert-static-v1923.mjs" -TimeoutSec 120
```
（判据逻辑固化在 `logs/build/assert-static-v1923.mjs`，避免每次手敲正则走样；对 `scripts/` 只读。）

#### 判据可信度依据（Lead 要求：连改造前基线原文一起留档）

**改造前基线（HEAD 07cd884 / v1.9.22，FAIL 是预期的）**——三件套：

```
[FAIL] 静态防回退断言（改造前基线，证明判据确实有牙）
  命令：pwsh -NoProfile -File logs\build\run-gate.ps1 -Name assert-static-baseline -Command "node logs\build\assert-static-v1923.mjs" -TimeoutSec 120
  摘要：target            = scripts/test-runtime.mjs
        agent_preset      = 0   (require >= 2)
        subagent-calls    = 0   (require >= 2)
        legacy agentType  = 1   (仅信息：旧身份通道用法计数)
        ASSERT-STATIC: FAIL（agent_preset / subagent tool-call 覆盖不足）
        → exit 1
  日志：logs/build/20261030-014837-assert-static-baseline.out.log（stderr 0 字节）
```

**收紧理由（写进报告时照抄）**：
> 原判据「JSON 字符串形态 ≥ 2 处」在改造前**即已满足**（现状 5 处），改造前后都是绿的，
> 因此**不具备防回退能力**；实测 `agent_preset` 为 0、`subagent` tool/call 为 0，
> 而 v1.9.23 的身份信号正是 `agent_preset`，故收敛为上面两条。改造前 exit 1 就是判据有牙的证据。
> （同时说明：我最初向 Lead 报的「字符串形态零覆盖」是取证不完整导致的误判，已更正；
> `test-runtime.mjs` 里对象形态 11 处、字符串形态 5 处并存。）

### 5.5 A（真模型一轮最小委派）— 仅旁证 ⬜

- 额度允许就跑一轮（如「让指北算一下 BTC 指标」）作为**旁证**；做完只写「旁证」，**不进 PASS 计数**。
- 没做则写 `BLOCKED(额度未用)`，并注明「链路已由 A3(方案 B) + §5.4 覆盖」。不得因此把 A3 记成 PASS。

### 5.6 GUI 验收三段分工（Lead 定夺 ③ — 我只做 a、b；c 归 Lead）⬜

> **前提变更**：Lead 不能代授权浏览器工具（工具层要求用户原话里的显式意图）。
> 因此界面段由 Lead 做，我只做**数据段**与**代码段**，报告里界面项一律标 `UNVERIFIED(待 Lead 界面复核)`。

#### a. 数据段（我负责）— GUI 选择器的数据源，等价于界面事实

一次进程内真实引导，断言三件事：

| # | 断言 | 判据 |
|---|---|---|
| a1 | 浏览器路径 roster **不含** `kexi-member-*` | 走 typert 描述符 `agentPresets/list` 真实调用；结果 id 集合中 `kexi-member-` 前缀命中 **0** |
| a2 | 宿主内部 `list()` **仍含 5 条**成员预设 | `ctx.get('agentPresets').list()` 里 `kexi-member-*` 恰 **5** 条，且等于 `MEMBER_PRESET_IDS`（真源 `lib/members.mjs`） |
| a3 | `mount('kexi-member-technical')` **成功** | 返回 `{ id: 'kexi-member-technical' }` 且不抛错 |

**a3 的可执行配方（Phase 1 已从源码核实，不是猜的）**：
`mount(ctx, id)` 内部走 `bind()` → `scopeOf(ctx)`，根 ctx 无 scope 会抛
`Agent preset binding requires a scoped context`（`dsh-agent-preset-registry/lib/index.js`）。
必须用官方 `@deepseek-ai/dsh-scope` 的 `createScope(ctx, key)` 造一个 scoped ctx：

```js
import { createScope, scopeOf } from '@deepseek-ai/dsh-scope'
const lease = await ap.acquireScope('kexi-member-technical')  // { key, [Symbol.asyncDispose] }
const scope = createScope(ctx, lease.key)                     // 内部 ctx.plugin(scope)，需真实 Context
if (scopeOf(scope.ctx) !== lease.key) throw new Error('scope 未生效')   // 先自证
const preset = await ap.mount(scope.ctx, 'kexi-member-technical')       // 期望 { id: 'kexi-member-technical' }
await scope.dispose()
```
> ⚠ `createScope` 需要真实 cordis `Context`（要有 `.plugin()`）→ **这一段不能用 fakeCtx**，
> 必须走真实引导（与 `check-preset-mount.mjs` 同一条路）。而 §5.4 的 label 单测相反，用 fakeCtx 即可。
> 另注：`check-preset-mount.mjs` 原注释说不调 `mount()`，是因为它从 profile 根 ctx 直接调——现在补上 scope 就能调。

**报告必须贴的源码原文（Lead 要求，防止将来有人重写这段再踩一次）**——已抓取留档在
`logs/build/evidence-scoped-context-mount.txt`（含 `bind()`、`mount()`、`createScope()`、`scopeOf()`、`acquireScope()` 原文）：

```
# dsh-agent-preset-registry/lib/index.js
654: async bind(ctx, generation) {
655:   const key = scopeOf(ctx);
656:   if (key === void 0) throw new Error("Agent preset binding requires a scoped context");
```
报告里连同 `mount(ctx, id)` 方法体一起贴，并注明「根 ctx 直调必抛」。

#### b. 代码段（我负责）

- `node scripts/test-hooks-order.mjs`（Rules of Hooks 静态扫描，防「点设置后整棵树卸载、状态灯一起消失」）
- `node scripts/test-frontend-backend-parity.mjs`（前后端字段匹配）
- 两条都要三件套；任一条 FAIL 即停。

#### c. 界面段（**Lead 做**，我只提供检查点清单并标 UNVERIFIED）

sync-install + `dsh restart` + 浏览器刷新后，请复核以下 5 点（我按此写进报告待勾选）：

1. 预设选择器里 **只有 1 个**「K析研判团」入口，且**看不到** `kexi-member-*` 成员预设（过滤生效）；
2. **设置面板可打开**（点齿轮 → 面板出现，不白屏）；
3. 打开设置后**标题栏「K析」状态灯不消失**（v1.5.5 那个 hooks 违规的症状）；
4. 浏览器 console **0 条** React hooks 报错（`Rendered more hooks than during the previous render` / `change in the order of Hooks`）；
5. 进度弹窗里委派节点显示「**委派 指北·技术指标**」而非通用「成员」。

> 我在报告里对应写法：`[UNVERIFIED(待 Lead 界面复核)] GUI 界面段` + 上述 5 条清单 + **不填 PASS**。
> 拿不到 Lead 复核结论时，该段既不计入通过数，也不影响其它段已取证的门禁。

---

## 6. 残余风险 / 需 Lead 决策

| 编号 | 风险 | 影响 | 处置 |
|---|---|---|---|
| R1 | ~~A3 证据形态未定~~ → **已定夺**：B 作门禁证据 + §5.4 两条单测；A 仅旁证；C 不用 | stub LLM 注入能否成功仍未知 | Phase 2 首件事验证注入；不可行即上报改成「受限说明 + 单测覆盖」，不自降为 PASS |
| R2 | ~~`_probe-*` 随包出厂~~ → **已定夺**：预设团移到 `tools/`（不在 files 内）；我负责断言 tgz 内 0 条 `package/scripts/_probe*` | 若预设团漏移 → 出厂被我这道门禁拦下 | 发现残留 → 记 FAIL 并**挂起出厂**，不「说明一下就放行」 |
| R3 | 工作区 EOL 混合（29/149 CRLF） | 冻结后字节漂移 → tgz SHA-256 不可复现、冻结号失效 | 冻结后禁 git 写操作；打包前后双算 SHA-256 |
| R4 | blob 基线 ≠ 工作区字节基线（28 文件 EOL 不同） | 用 baseline 做哈希对账会得到 28 个假差异 | 行为回归用 blob 基线；字节对账一律用工作区 |
| R5 | T2 并发写工作区 | 门禁期间文件变动 → 结果不可复现 | 冻结窗口内跑；开跑记录 HEAD + status + diff 指纹；发现变动即作废重跑 |
| R6 | `docs/evidence/` 会进 tgz（docs/ 在 files 内） | 包体积 + 内部报告外泄 | 只把最终门禁报告放 `docs/evidence/`；原始日志留 `logs/build/` |
| R7 | 3080 被占会让 `check-preset-mount` 非代码原因失败 | 误判为代码缺陷 | 跑前 `Get-NetTCPConnection -LocalPort 3080` 断言空闲 |
| R8 | GUI **界面段**依赖 Lead 用已连浏览器复核（我无权使用浏览器工具） | 该项在我这边只能到 `UNVERIFIED(待 Lead 界面复核)` | 我把 §5.6-c 五点清单写死；数据段/代码段独立取证；**界面段不填 PASS 也不拖累其它项** |
| R9 | 身份映射若在测试里另抄一份中文，则 `members.mjs` 改错也不会红 | 假绿 | §5.4 断言一律从 `members.mjs` 读 `cn`/`short` 再拼 |

---

## 7. 证据索引

| 用途 | 路径 |
|---|---|
| 门禁执行器 | `logs/build/run-gate.ps1` |
| 门禁索引（含互联网北京时间） | `logs/build/gate-index.jsonl` |
| 基线 verify | `logs/build/20261030-013513-baseline-final-verify.out.log` |
| 基线 preset-contract | `logs/build/20261030-013517-baseline-final-contract.out.log` |
| 基线 vs 安装副本（28 EOL 差异清单） | `logs/build/baseline-installed-diff.txt` |
| 工作区 vs 安装副本（6 缺失） | `logs/build/20261030-013357-baseline-sync-install-check-fixed.out.log` |
| PSModulePath 污染现场 | `logs/build/20261030-013033-baseline-sync-install-check.{out,err}.log` |
| 超时强杀自测 | `logs/build/20261030-012842-selftest-hang.*.log` |
| 基线副本（blob 保真） | `dist/baseline-07cd884/` |

---

## 8. 报告书写口径（Lead 定夺 ③④，写进 `gate-20261030-v1923.md` 时照抄）

### 8.1 每条门禁一律三件套

口径来源：`docs/team/collaboration.md` §四之一「验收证据必须可复核（三件套）」。
**缺任何一件记 `UNVERIFIED`，不计入通过数；只有「我确认过了」没有落盘的一律 UNVERIFIED。**

```
[PASS|FAIL|UNVERIFIED] <门禁项名称>
  命令：<人能原样复制的一整行，含工作目录与超时设置>
  摘要：<关键判定行原文；FAIL 用完整失败原文，不截断>
  日志：logs/build/<批次>-<名>.out.log
```

FAIL 项额外附「可复现命令 + 失败原文全文」；超时项要给退出码 `124` + 最后日志原文（证明确实被杀）。

### 8.2 EOL / 冻结号口径（必须原样写进报告）

> **`core.autocrlf=true` 下工作区 CRLF 属 Git 正常行为，冻结号以磁盘字节为准。**
>
> - 本仓库不加 `.gitattributes`（Lead 定夺：避免把 29 个文件重归一化、冲掉本轮 review 面）。
> - 实测工作区 **29/149 个文件为 CRLF**，而 `git status` 对其与 LF 一视同仁（都报「干净」）→ git 不设防。
> - 因此：**冻结后禁止任何 git 写操作**（`checkout` / `stash` / `add --renormalize` / 编辑器批量重存）。
> - `npm pack` **前后各算一次** tgz SHA-256，两次都记进报告（不一致即证明打包期间字节漂移，FAIL）。
> - 冻结指纹同时记录：`git rev-parse HEAD`、`git status --porcelain -- ':!dist'`、`git diff | git hash-object --stdin`。

### 8.3 GUI 验收分工（Lead 定夺 ③ — 已定稿，覆盖我最初的「向 Lead 要许可」设想）

- GUI 裸 HTTP 为 401 → 脚本侧取不到证据；**浏览器工具我不能用**（Lead 无法代授权，工具层要求用户原话里的显式意图）。
- **最终分工**：数据段（§5.6-a）+ 代码段（§5.6-b）我负责；**界面段（§5.6-c）由 Lead 用本会话已连浏览器复核**。
- 报告写法：界面段 **固定标 `UNVERIFIED(待 Lead 界面复核)`** + 附 §5.6-c 五点清单；
  **绝不伪造观察记录、绝不写「看起来正常」、绝不自填 PASS**；该段不进通过数、也不拖累其它已取证项。

### 8.4 报告必须额外贴的两段原文（Lead 要求，判据与配方的可信度依据）

1. **判据为什么收紧 + 改造前基线全文**：照抄 §5.4.1 的「判据可信度依据」块
   （含 `agent_preset=0 subagent-calls=0`、`ASSERT-STATIC: FAIL`、`exit 1` 与日志路径
   `logs/build/20261030-014837-assert-static-baseline.out.log`）。
2. **`mount` 必须 scoped context 的源码原文**：照抄 §5.6-a 末的 `bind()` 654–656 行原文
   + `mount(ctx, id)` 方法体，并注明「根 ctx 直调必抛 `Agent preset binding requires a scoped context`」；
   留档文件 `logs/build/evidence-scoped-context-mount.txt`。

### 8.5 交付纪律

- 一条一条写，**FAIL 即停**，不跳步、不把 FAIL 改成 PASS、不把 UNVERIFIED 计入通过数。
- 出厂三件套之外再加：tgz 绝对路径 + 文件名（含版本/日期）+ 字节数 + SHA-256（打包前后各一次）+ `runId=RUN-20261030-V1923` + `冻结号=FREEZE-20261030-V1923`。
