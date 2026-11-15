# 项目进度 · progress.md

> **时间基准**：本文件所有「记录时间」取互联网北京时间，来源
> `https://timeapi.io/api/Time/current/zone?timeZone=Asia/Shanghai`（主）
> 与 `https://f.m.suning.com/api/ct.do`（备，epoch 毫秒换算）。
> 两源交叉校验一致：**2026-10-30 01:32:42 (+08:00, 周五)**。
> `https://worldtimeapi.org` 本机 SSL 握手失败，**本次不可用**，未用本机系统时间顶替。
>
> 表中出现的「日志批次时间戳」是门禁执行器用本机时钟生成的文件名标识，**不等于本列北京时间**，
> 两者不要混用（用户规则 24）。
>
> 最近更新：2026-11-15 17:22:08（沟通团 · goutong-tuan）— §九 补 v1.9.24 里程碑③④全部完成 + 交付说明定稿
> 上次更新：2026-11-15 17:14:10（沟通团 · goutong-tuan）— §九 补里程碑①②（task-1 结案、task-2 接手）
> 上上次：2026-11-15 17:10:58（沟通团 · goutong-tuan）— 新增 §九 v1.9.24 迭代（加载失败修复）进度记录
> 上次更新：2026-10-30 02:46:43（沟通团 · goutong-tuan）— §八 补 102→104 口径定案（+2 为两个新模块纳入行数约束，Lead 实跑复证）

## 一、本轮目标

| 编号 | 目标 |
|---|---|
| A | 检查并评估当前工程（全量体检，按严重度分级出结论） |
| B | 改进「团队在 agent 内同时暴露所有团队预设子 agent」的体验：① 6 个委派工具收敛为 1 个；② 内部成员预设不再出现在智能体预设选择器 |

## 二、阶段进度

| # | 阶段 | 负责 | 状态 | 记录时间（北京时间） | 证据 / 备注 |
|---|---|---|---|---|---|
| 0 | 立项与任务分派 | Lead | ✅ 完成 | 2026-10-30 01:26 | 任务板 task-1 ~ task-4 |
| 1 | 基线取证与门禁执行器自检 | 回归团 | ✅ 完成 | 2026-10-30 01:28:45 | `logs/build/gate-index.jsonl`：`selftest-fast` PASS（exit 0）、`selftest-hang` **TIMEOUT-KILLED**（exit 124，证明超时强杀真的生效）、`baseline-verify` **PASS 12 顶层键 / 661 行 patch**、`baseline-preset-contract` **PASS**（含负对照：旧格式被拦 2 字段） |
| 2 | 全量工程体检（只读取证） | 体检团 | ✅ 完成 | 2026-10-30 02:43:29 | `docs/audit-2026-10-30.md`：**P0×2 / P1×7 / P2×9**，总评 **6.5/10**（业务代码 8.5、门禁证据链 4.5 为短板）；**该评分只对改造前 HEAD=07cd884 有效**。含 §10 勘误节（见本文 §七 E1） |
| 3 | v1.9.23 改造（委派工具收敛 + 成员预设隐藏） | 预设团 | ✅ 完成 | 2026-10-30 02:43:29 | ①delegation 组收敛为**单行 `subagent`**，`allowedAgentPresets` **5 项**，身份由 preset id 派生，host/client/门禁三方共用 `lib/members.mjs` 唯一真源；②成员预设不再出现在预设选择器（真机取证：浏览器 roster **15→10**，掉 5 个 `kexi-member-*`，主预设与其它 8 个不受影响；宿主内部 `list()`/`readDocument()`/`mount()` 未动）；③顺带修 3 个真缺陷（见 §八） |
| 4 | 回归门禁 + 真机验收 + 安装包出厂 | 回归团 | ✅ 完成（含 2 项 UNVERIFIED） | 2026-10-30 02:43:29 | 门禁 **36/36**（271s）；验证明细与出厂指纹见 §八；`logs/build/gate-20261030-v1923.md` |
| 5 | 团队协作基础设施（日志/索引/依赖/进度） | 沟通团 | ✅ 完成 | 2026-10-30 01:28:45 | `LOGS-INDEX.md`、`logs/arch|build|debug`、`deps/MANIFEST.md`、`docs/team/*` |
| 6 | 用户进度通报与交付说明准备 | 沟通团 | ✅ update-02 已发；交付说明待 Lead 定稿 | 2026-10-30 02:43:29 | `docs/team/update-02.md`（15 行）；最终交付按 `docs/team/delivery-template.md` 填 |

图例：✅ 完成 ｜ 🔄 进行中 ｜ ⏸ 等待 ｜ ❌ 失败

## 三、当前基线事实（供各团对齐口径）

- 版本：仓库 `package.json` = **1.9.23**；安装副本 `%APPDATA%\DSH Desktop\dsh-home\profiles\web\node_modules\dsh-kexi-crypto` = **1.9.23**（一致，166 文件、差 0/多余 0）
  —— 本轮开工时的基线是 **1.9.22 / HEAD=07cd884**；体检评分 6.5/10 只对该基线有效（见 §二 阶段 2）
- 宿主：DSH **0.2.1-alpha.5**（`node_modules/@deepseek-ai/dsh-*` 取样均为该版本）
- 工具链：Node **v24.21.0** / npm **11.19.0** / Python **3.13.5**（`C:\Python313\python.exe`）/ PowerShell **7.6.9** / Git **2.50.1**
- 依赖：**零第三方 npm 依赖**（无 `dependencies`/`devDependencies`/`peerDependencies`，无 `node_modules`）；Python 仅标准库；`pywin32` 为 Windows 下 CEX 密钥加密的可选项，本机已装
- 基线门禁：改造前 `verify` PASS、`test-preset-contract` PASS（`logs/build/*.out.log`）；改造后全量结果见 §八

## 四、决策落实记录（原 4 条待确认，Lead 2026-10-30 01:32 已裁定）

| # | 事项 | 结论 | 落实状态 |
|---|---|---|---|
| 1 | `.gitignore` 覆盖策略 | **Lead 授权沟通团修改**。`logs/`、`deps/`、`docs/team/` 入库（审计依据）；`dist/` 整目录不入库；不改动历史已跟踪文件；理由以注释写进 `.gitignore` | ✅ 已完成，见 `.gitignore` 顶部注释（已用 `git check-ignore -v` 逐项验证生效） |
| 2 | 目录说明文件越界 | **不迁**。`logs/arch|build|debug/README.md` 属「目录说明」，归沟通团维护，不算越界；已写入 `LOGS-INDEX.md` §5 维护责任 | ✅ 已完成 |
| 3 | `run-gate.ps1` 时间戳用本机时钟 | 已转回归团：本机 stamp 保留作批次号，另在 `gate-index.jsonl` 增加互联网北京时间字段 | ⏳ 回归团处理中，沟通团不动该文件 |
| 4 | 用户可见说明的链路 | 维持：用户可见内容以 `docs/team/update-NN.md` 为准，经 Lead 转述，各团不直接对用户发话 | ✅ 已在 `collaboration.md` §六 固化 |

**新增口径**：`collaboration.md` §四之一「验收证据必须可复核（三件套）」——
门禁 PASS 必须同时给 **命令 + 输出摘要 + 日志路径**，缺一记 **UNVERIFIED** 且不计入通过数；
FAIL 须附失败原文全文。该格式将直接沿用到最终交付说明。

## 五、下一步

1. **用户侧复核（唯一阻塞项）**：重启 DSH → 刷新浏览器 → 按 5 点自查
   （预设选择器只剩「K析研判团」/ 委派只剩 1 个 `subagent` 且走 `agent_preset` /
   进度弹窗与关系图显示中文成员名 / 设置面板可打开且状态灯不消失 / 控制台无 React 报错）。
2. **A3 缺口补验**：用 stub LLM 跑一遍「真实委派」链路（现有替代证据：真 mount 2 次 +
   标签断言 a–e 覆盖身份派生全部边界，缺「真实模型循环内委派」这一环）。
3. **最终交付说明**：按 [`delivery-template.md`](delivery-template.md) 填 6 个证据块，
   其中「真机验收」块在用户复核前只能记 UNVERIFIED。
4. **表述纪律（规则 34）**：本轮**不得**表述为「全部完成 / 交付完成」，
   只能写「已产出并通过门禁（除 A3 缺口），待用户重启后界面复核」。

## 六、范围纪律巡检（一次性留痕）

**记录时间**：2026-10-30 01:36:15（北京时间，timeapi.io 与苏宁时间接口两源一致）
**目的**：确认除各团声明的写入范围外，没有任何**受版本管理**的文件被改动。
**性质**：时点快照——巡检时回归团正在并发跑门禁，未跟踪文件数会继续增长，数字以本表时刻为准。

**检查命令**（仓库根执行，可原样复现）：

```bash
git rev-parse --short HEAD                      # 定格基线
git status --porcelain=v1                       # 全量变更一览
git diff --name-status                          # 受版本管理文件的改动
git diff --cached --name-status                 # 暂存区（应为空）
git diff --diff-filter=DRC --name-status        # 删除/改名/复制
git ls-files --others --exclude-standard        # 未跟踪文件（不含已忽略）
```

**输出摘要**

| 检查项 | 结果 |
|---|---|
| HEAD | `07cd884`（分支 `main`），与开工基线一致 |
| **受版本管理的文件被改动** | **仅 `.gitignore` 一处**，属沟通团、Lead 已授权，`+24 / -0` 纯新增 |
| 已暂存改动 | 无（`git diff --cached` 为空） |
| 删除 / 改名 / 复制 | 无（`--diff-filter=DRC` 为空） |
| 未跟踪文件 | **89 项，全部落在各自声明范围内，未归类 0 项** |

**未跟踪文件归属分布**（同一时刻原子快照）

| 归属 | 项数 | 内容 |
|---|---|---|
| 沟通团 | 8 | `LOGS-INDEX.md`、`deps/MANIFEST.md`、`docs/team/*.md`×3、`logs/{arch,build,debug}/README.md` |
| 预设团 | 4 | `home-plugin/dsh-kexi-crypto/lib/members.mjs`、`lib/preset-roster.mjs`、`scripts/_probe-roster-wrap.mjs`、`scripts/_probe_tmp.mjs` |
| 回归团 | 71 | 均在 `logs/build/`：批次日志 36 + 批次包装脚本 12 + 执行器/索引 3 + `_tmp` 一次性试验文件 19 + `baseline-installed-diff.txt` 1 |
| 体检团 | 8 | 均在 `logs/debug/`：`run-all-tests*`、`check-preset-mount-*`、`verify-nojsyaml-*`、`hw-monitor-*.csv` |

**结论**：范围纪律**符合**。不存在越界写入；唯一受版本管理文件的改动是已授权的 `.gitignore`；无删除、无暂存、无未归类文件。

**发现 1：`logs/build/_tmp/` 内 19 项一次性试验文件**　→ **已裁定（Lead 2026-10-30）**

- 事实：19 项一次性试验文件（`a.err`、`a.out`、`race1~6.{out,err}.txt`、`sp.{out,err}.txt`、
  `envprobe.ps1`、`hashprobe.ps1`、`race.ps1`，合计约 2.2 KB）会随 `logs/` 一并进入版本库；
  同目录 `*.gate.ps1`（12 项）记录每批次真实执行命令，**是有价值的证据，必须保留**。
- **裁定：不加忽略规则。** 改由回归团在**出厂前清理**一次性试验文件、保留 `*.gate.ps1`，
  并把清理动作**逐条列入 gate 报告**；已转告回归团。
- **裁定理由（比方案本身更重要，后续同类问题照此办）**：忽略规则会把「一次性试验」**永久合法化**——
  一旦 `_tmp` 被忽略，下次再有人往里扔东西就没人管了；而「清理」是一个**显式动作**，
  写进 gate 报告后可被逐条审计，比「静默忽略」更可信。
- 因此 `.gitignore` 保持原样（未加入 `logs/build/_tmp/*` 规则）。

**发现 2：`CHANGELOG.md:692` 死链**　→ **已转预设团，待其确认**

- 事实：该行引用 `.kexi-test-tmp/kexi-ver-badge.png`，而 `.kexi-test-tmp/` 被 `.gitignore` 忽略、
  文件实际不存在，属「历史记录里挂着一张打不开的证据」。
- 裁定：`CHANGELOG.md` 在预设团写入范围内，已转其处理；沟通团**未改动**该文件。
- 待办：预设团确认后在本节补一行结论（改掉引用 / 补图 / 说明失效原因）。

## 七、勘误登记（证据链纠错，必须随交付一并披露）

体检结论里被后续实测推翻的条目集中登记在此。**最终交付说明引用体检结论时，必须引用勘误后的版本，不得沿用原表述。**

| 编号 | 位置 | 原表述（**有误**） | 实测更正 | 更正后的缺陷形态 | 证据来源 |
|---|---|---|---|---|---|
| E1 | 体检报告 P1-6 | 「`test-veto` 缺基线时 `except` 静默换合成基线**继续绿**」——定性为**假绿** | 实测 **8/11 通过**（并非全绿）；报错为过期合成基线缺字段：`data_quality 缺少必需字段` | **静默换基线 + 诊断错位**：缺陷确实存在，但形态不是「假绿」，而是「失败被错误地归因」 | 修复团实测，Lead 2026-10-30 转达 |

- **披露要求**：交付说明块① 涉及 P1-6 处必须写明本条已勘误，**不得再出现「假绿」表述**。
- **处理分工**：`docs/audit-2026-10-30.md` 的勘误节由**体检团**补写，沟通团不改该文件。
  状态：体检团**已完成**，落在该报告 §10（2026-10-30 确认）。
- **后续规则**：凡结论被实测推翻，一律在此登记「原表述 / 实测 / 更正后形态」三元组，
  不做「悄悄改掉原文」——**错过的判断本身也是交付物的一部分，必须可追溯**。
- 登记时间：2026-10-30 02:33:09（北京时间，timeapi.io 与苏宁接口一致）；登记人：沟通团

## 八、出厂证据链（2026-10-30 02:43:29 定格）

**门禁结果**（全部 PASS，详见 `logs/build/`）：

| 门禁项 | 结果 |
|---|---|
| `verify` | PASS |
| `test-preset-contract` | PASS |
| `preset-roster` | PASS |
| `test-runtime` | **178 项 / 0 失败** |
| `test-v1922-fixes` | **104 项 PASS**（改造前 102 → 改造后 104：+2 = `members.mjs`、`preset-roster.mjs` 两个新模块纳入「单文件 ≤600 行」断言） |
| `test-team-graph` | **49 / 49** |
| `test-card-kexi-filter` | **38 / 38** |
| `run-all-tests` | **36 / 36 组**（271 秒） |
| 真引导挂载 | PASS（真实 mount 2 次） |

> **102 / 104 口径已定案（Lead 2026-10-30 实跑复证）**：以 **104** 为准。证据三处同值——
> `node scripts/test-v1922-fixes.mjs` 现场输出 `v1.9.22 回归：PASS（104 项）`、
> gate 报告 L66、`logs/build/20261030-022555-v1922-fixes.out.log`。
> **102 是改造前基线**（`docs/audit-2026-10-30.md` 取证时点的数字），差额 +2 来自两个新模块被纳入行数约束。
> 后续若再见到 102，那是基线值，不是矛盾。

**顺带修掉的 3 个真实缺陷**（改造过程中发现并修复，非本轮主目标）：

| # | 缺陷 | 根因 | 影响 |
|---|---|---|---|
| 1 | 关系图「谁在干什么」全员显示「其他工作」，却标着"实测" | `arguments` 实际是 JSON **字符串**，代码按对象取字段，取不到就落到默认标签 | 用户看到的是假信息，且看起来像真的 |
| 2 | 门禁缺 YAML 解析器时，24 条契约断言被**静默放空**仍报 PASS | `verify.mjs` 的 `js-yaml` 获取失败被吞，整块校验跳过 | 门禁可被静默关闭——最危险的一类 |
| 3 | 成员启动事件被写进**成员自己的**会话桶 | 事件按 `agentType` 归桶，未归到主理人会话 | 主理人卡片看不到成员启动，编排过程不可见 |

**出厂包**（**沟通团已独立复算，与 Lead 转述一致**）：

```
路径     ：C:\Users\lcl\Desktop\DSH插件开发\K析研判团\dist\dsh-kexi-crypto-1.9.23.tgz
文件名   ：dsh-kexi-crypto-1.9.23.tgz
字节数   ：1,057,597（本机实测）
SHA-256  ：6C8EEB6C7614C01733A8F58CA27D09001480A9CCF01655FA93561767EBAEDCED
runId    ：RUN-20261030-V1923
冻结号   ：FREEZE-20261030-V1923
基线提交 ：07cd884
命令     ：Get-FileHash dist\dsh-kexi-crypto-1.9.23.tgz -Algorithm SHA256
```

**安装副本**：已同步，`version = 1.9.23`，166 文件、差 0 / 多余 0。

**两项必须如实披露的未完成项（不得计入通过数）**：

| 项 | 内容 | 现有替代证据 | 补齐条件 |
|---|---|---|---|
| A3 | **stub LLM 真实委派** 门禁项 UNVERIFIED | 真 mount 2 次 + 标签断言 a–e，覆盖身份派生的全部边界 | 需「真实模型循环内委派」跑通 |
| GUI | **界面段** UNVERIFIED | 无（浏览器桥未连接，且界面生效需重启 DSH，会终止当前会话） | 用户重启后按 5 点清单自查 |

**本轮瑕疵申报（供最终交付说明引用）**：`docs/audit-2026-10-30.md` 的 P1-6 原定性已被实测推翻，
见本文 §七 E1；交付说明涉及 P1-6 处**不得再出现「假绿」表述**。

## 九、v1.9.24 迭代进度（2026-11-15 立项）

**记录时间**：2026-11-15 17:10:58（北京时间，周日，来源 timeapi.io `https://timeapi.io/api/Time/current/zone?timeZone=Asia/Shanghai`；本次未用苏宁接口交叉校验，苏宁源未复测）
**登记人**：沟通团（goutong-tuan），本文件由沟通团维护
**状态**：已立项 / 🔄 进行中

**背景**：用户在 DSH 桌面版插件管理页看到插件 `dsh-kexi-crypto`「加载失败」。
Lead 已定位根因：补丁声明了上游 DSH **0.2.2-rc.3** 已删除的子路径插件
`dsh-tool-subagent-control/list-agents`（`list_agents` 已并入主入口），导致整个
`kexi-crypto` 预设被判 broken → 插件「加载失败」。

**本轮用户已确认范围选项 A**：修复 + 门禁验证 + 同步安装副本，**不出 dist 包、不提交 git**。

**任务卡（任务板当前状态，快照于 2026-11-15 17:10 北京时间）**：

| 任务 | 负责人 | 状态 | 内容 |
|---|---|---|---|
| task-1 | xiufu-tuan（修复团） | 🔄 in_progress | 删除失效的 `list-agents` 预设行；版本 1.9.23 → 1.9.24（package.json / CHANGELOG / README / HANDOFF 四处一致）；全仓 grep `list-agents` 确认无残留；不得改动已安装副本 |
| task-2 | huigui-tuan（回归团） | ⏸ pending（blockedBy task-1） | 门禁：经 `logs/build/run-gate.ps1` 跑 verify / test-runtime / test-preset-contract / test-preset-roster / test-v1922-fixes / **check-preset-mount（必须 PASS 且真调用 mount() 2 次）** / run-all-tests 全量；结论写 `logs/build/gate-2026MMDD-v1924.md` |
| task-3 | huigui-tuan（回归团） | ⏸ pending（blockedBy task-2） | 门禁全绿后同步安装副本（`scripts\sync-install.ps1` 不带 -Pack）到 `%APPDATA%\DSH Desktop\dsh-home\profiles\web\node_modules\dsh-kexi-crypto`，并用 -CheckOnly 复证差异 0；读回 version=1.9.24 且无 list-agents 行 |
| task-4 | goutong-tuan（沟通团，本文件维护方） | ⏸ pending（ready=true，**门禁 task-2 通过前不得 complete**） | 用户沟通与进度同步、交付说明骨架 |

**本轮不做**：不出 dist 包、不 git commit、不重启运行中的 DSH（插件变更需用户自行重启后生效）。

**用户侧唯一阻塞项（预告）**：修复并同步安装副本后，需**用户重启 DSH 桌面端**使 `cordis.patch.yml` 变更生效。

> 本节状态将随里程碑（修复完成 / 门禁结果 / 同步安装完成）更新；数字与门禁原文以 Lead 转交为准，
> 沟通团不编造、不脑补，未验证项一律记 UNVERIFIED。

**里程碑更新（2026-11-15 17:14:10，timeapi.io）**：①② 修复团已结案 task-1——删除失效的 `list-agents` 子路径行后，
`verify.mjs` 全绿、`check-preset-mount.mjs` **PRESET-MOUNT: PASS（真调用 mount() 2 次）**；回归团已接手 task-2 门禁，
之后执行 task-3 同步安装副本。
注：该 PASS 系 Lead 转交口径（修复团阶段性自检），完整门禁（run-all-tests 全量等）以 task-2 结论为准，本节不重复计数。

**里程碑更新（2026-11-15 17:22:08，timeapi.io）——③④ 全部完成**：
- task-2 门禁 **7/7 PASS**（全量回归 **36/36**，elapsed 160.4s；`check-preset-mount` PRESET-MOUNT: PASS 且真调用 mount() 2 次；
  冻结 **RUN-20261115-V1924 / FREEZE-20261115-V1924**；verify 12 顶层键 / 631 行 patch；v1922-fixes 104 项；runtime 178/0）。原文：`logs/build/gate-20261115-v1924.md`。
- task-3 同步安装副本成功，但首跑 **exit 1**：安装副本残留 v1.9.23 死文件 `kexi-member-presets.yml`
  （sha256 275C3440…，与 v1.9.23 留证逐字节一致）；留证（`logs/build/removed-installed-kexi-member-presets-v1924.yml`）后删除，
  `-CheckOnly` 复证 **168 文件 差异 0 / 多余 0** → exit 0；安装副本 `version = 1.9.24`、无 `list-agents` 声明行（lead 已独立复核）。
- 残余风险 **R8**：死文件再现，疑为 DSH 0.2.2-rc.3 升级/插件管理器按缓存重解压所致，根因未完全查明。
- **GUI 段 UNVERIFIED**：待用户重启 DSH 后复核（① 加载失败消失 ② 选择器只有 1 个「K析研判团」 ③ 设置面板可开 ④ console 无 hooks 报错 ⑤ 委派节点显示「委派 指北·技术指标」）。
- **A3 stub LLM 真委派仍为 UNVERIFIED**（与 v1.9.23 同一卡点，本轮未补测）。
- 交付说明已定稿：`docs/team/delivery-v1.9.24.md`（数字以门禁报告为准）。
- 本轮未出 dist 包、未 git commit。
- 门禁 task-2 已通过、里程碑全部完成，沟通团 task-4 已 complete（2026-11-15，见任务板）。
