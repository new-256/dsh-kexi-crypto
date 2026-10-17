# 修复审计 · v1.9.21 — kexi-crypto 预设在 DSH 0.2.1 上加载失败

- 时间：2026-10-18（北京时间）
- 现象：用户重启 DSH（0.2.1-alpha.5，Web 端口 61499）后报「kexi-crypto 依然加载失败」；
  v1.9.19（{{cwd}}）与 v1.9.20（设置槽位）两处修复后仍不可选。
- 结论：**与前两次是三个互相独立的断点**。本次为 subagent 配置字段在 0.2.1 被删除，
  五个角色委派行携带 `persona` → 整组预设 eager 激活失败。

## 1. 诊断方法（先取证，后动手）

1. host 半健康：`GET /kexi-dashboard/status` 返回
   `{state:"idle", presetActive:false, version:"1.9.20"}`，证明插件 host 行活着，
   问题不在 host；`include:kexi-dashboard` 在 Config 巡检里 `absent` 是「插件无
   Schemastery Config」的正常态（dsh-repo-push-access 同样 absent），非失败证据。
2. 会话 zstd 日志（近 3 小时全量解 zstd 扫描 preset/kexi/error/fail/mount）无命中——
   预设 eager 激活失败只登记在 agentPresets 注册表的 roster `broken` 字段，
   不一定打日志。日志扫描因此是错误的取证方向。
3. **权威取证**：用官方 `@deepseek-ai/dsh` 的 `runProfile({profile:"web"})` 在
   进程内引导与运行中 DSH 同一份 profile，settle 9s 后读
   `ctx.get("agentPresets").list()`，并实际 `mount(ctx,"kexi-crypto")`。
   - roster 中 standard/ptc/minimal/cordis 等全部 `broken:null`；
   - kexi-crypto 的 `broken` 恰好 5 条，逐字如下；
   - `mount` 直接抛 `RemoteError`，文本同第一条。

```
member-market-data (@deepseek-ai/dsh-tool-subagent): tool-subagent: persona is no longer supported; configure persona and tool plugins in an Agent Preset and select it with agentPreset (spawn only)
member-technical   （同上）
member-forecaster  （同上）
member-risk        （同上）
member-falsifier   （同上）
```

## 2. 根因（读上游源码确认，非猜测）

`@deepseek-ai/dsh-tool-subagent@0.2.1-alpha.5/lib/index.js`：

```js
async function apply(ctx, config, session) {
  for (const field of ["persona", "toolFilter"])
    if (Object.hasOwn(config, field))
      throw new Error(`tool-subagent: ${field} is no longer supported; configure persona and tool plugins in an Agent Preset and select it with agentPreset (spawn only)`);
  const presets = new SubagentPresetSelection(config);
  ...
}
```

- `Config`（Schemastery）字段集：`provider, toolName, modelSelectionSettings,
  agentOptions, agentPreset, allowedAgentPresets, maxDepth`。
  **没有** persona/toolFilter/backgroundMode（schemastery 对未知键静默剥离，
  所以 schema 归一化不报错；拦截发生在 apply 的显式 throw）。
- 新架构：角色人格与工具面必须是一个**独立 Agent Preset**，委派行用
  `agentPreset: <id>` 引用；spawn provider 能力位 `agentPreset:true`
  （dsh-subagent-spawn-in-process 确认支持）。
- 注册表时序：委派行 apply 时 `SubagentPresetSelection.validate()` 会
  `presets.readDocument(id)` 校验被引用预设存在 → 成员预设必须**先注册**。
- 一行 throw 即整组预设 eager 激活失败（EntryGroup 全有或全无）。

## 3. 修复内容

1. 新增 `home-plugin/dsh-kexi-crypto/kexi-member-presets.yml`：
   裸 entry list（cordis:include 期望数组，无 `- insert:` 外层），声明
   `kexi-member-market-data|technical|forecaster|risk|falsifier` 五个成员预设，
   order 501–505（排在主预设 order:5 之后，选择器靠后且名称标注「内部预设·勿直接选择」）。
   - persona = 原委派行 5 段人格，**文本零改动搬运**（生成脚本物理行级提取，
     修复后用 YAML 解析逐字比对 728/604/562/575/1022 字符全等）；
   - 工具面 = 旧 `toolFilter.deny` 的白名单重表达：
     - `write/edit/present/ask_user_question/create_goal/update_goal` 对应工具
       **根本不挂载**（比黑名单严，模型看不见）；
     - `dsh-tool-fs` 配 `read:true, readImage:true, write:false, edit:false`；
     - 保留只读检索 tool-fs-search、tool-jobs、平台门控 shell；
     - **挂 kexi-plugin**：成员唯一分析手段是 kexi_run，且需要 kexi:policy 纪律段；
     - 不挂任何 subagent/workflow 行（成员不能再派成员）。
2. 主补丁 `cordis.patch.yml`：
   - 第二个 insert 内在 `preset-kexi-crypto` **之前**加
     `{id:kexi-member-presets, name:cordis:include, config:{path:./kexi-member-presets.yml}}`
     （include 相对补丁目录解析，已读 app-boot 源码确认语义）；
   - delegation 组 5 个角色行精简为 `provider:spawn / toolName / maxDepth:1 /
     agentPreset:kexi-member-*`，删除 persona/toolFilter/backgroundMode；
   - 通用 `subagent` 行不带 agentPreset（继承调用方当前预设）；
   - 文件 410 → 362 行；成员人格主体移到独立文件（符合单文件 ≤600 行约定）。
3. maxDepth:1 保留（D9：maxDepth 是绝对深度上限，主理人 depth0 派成员 childDepth1，
   配 0 首次委派即被拒），与成员预设内零委派行形成双保险。

## 4. 验证

- `scripts/test-preset-contract.mjs`（新增，纳入 run-all-tests）：
  从**当前实际安装**的 dsh-tool-subagent 源码正则提取 apply 拒绝字段列表
  （上游变字段不用改测试），用真实 `Config` schema 过每个委派行，断言零拒绝字段、
  5 个 agentPreset 引用全部有声明、成员预设无 subagent 行、挂 kexi-plugin；
  含负对照（旧格式 persona+toolFilter 必须被识别为坏，实测拦截=2）。
- `scripts/verify.mjs`：新增 include 行存在/name/时序/path、成员预设 5 行、
  persona 长度、includeRuntimeContext、fs write/edit=false、禁挂工具、零委派行等断言。
- 全量套件：**33/33 通过**（原 32 + 新增契约测试），日志 docs/build-logs/test-20261018-0208.log；
  硬件监控 docs/build-logs/hw-monitor-20261018-0208.csv
  （CPU 峰值 100% 瞬时、RAM 17.6GB、VRAM 6.5GB，无耗尽）。
- `scripts/check-preset-mount.mjs`（新增，发版手动）：真实引导 web 配置，
  断言 kexi-crypto + 5 成员预设 `broken==null` 且 `mount('kexi-crypto')` 成功。
  安装后执行结果见本文档第 6 节（安装后回填）。

## 5. 影响面与兼容性

- 角色人格与工具能力语义不变（deny 名单全部转译为不挂载 + fs 只读）。
- 五个成员预设会出现在预设选择器（roster 无 hidden 字段，已核对注册表
  AgentPreset Config 只有 id/name/description/order/plugins）；已用
  「内部预设·勿直接选择」命名 + 描述说明 + order 501+ 降低误选，直接选它只得到
  无编排能力的单人成员，不影响主预设。
- 主理人 persona 内「按 send_message 给持久成员派活」的编排流程不变；
  kexi-plugin 的 kexi_ensure_team 走原生 agentTeams.spawnTeammate，与本修复正交
  （spawn 出的持久队友由主理人当前预设 kexi-crypto 的角色工具行驱动）。
- 不引入任何降级/兜底：apply throw 是期望的 fail-loud，修复让配置不再触发它。

## 6. 安装后真实挂载验收（回填）

sync-install -Pack（139 文件 0 diff，v1.9.21）后执行
`node scripts/check-preset-mount.mjs`（真实 runProfile 引导 web 配置）：

```
✓ clean kexi-crypto (K析研判团)
✓ clean kexi-member-market-data (K析成员·数脉（内部预设·勿直接选择）)
✓ clean kexi-member-technical (K析成员·指北（内部预设·勿直接选择）)
✓ clean kexi-member-forecaster (K析成员·望潮（内部预设·勿直接选择）)
✓ clean kexi-member-risk (K析成员·守拙（内部预设·勿直接选择）)
✓ clean kexi-member-falsifier (K析成员·证伪（内部预设·勿直接选择）)
✓ resolve ×6 全部定义可取
PRESET-MOUNT: PASS
```

stderr 无任何 kexi / agent preset 错误行。

## 7. 实现方案的两次迭代（留档，含失败路线）

- **方案 A（cordis:include 外部文件）失败**：
  - 相对 path `./kexi-member-presets.yml` → include 按
    `new URL(path, ctx.baseUrl)` 解析，bundle 行 baseUrl=profile 根，
    报 `config file not found: profiles\web\kexi-member-presets.yml`；
  - 改 `!!js` 绝对路径 → include 构造函数**不支持 JsExpr**，
    报 `extension "" not supported`（extname 对对象取空）。
  - 结论：include 的 path 只接受相对字符串且锚在 profile 根，本包场景不可用。
- **方案 B（内联声明，已采用）**：5 个成员预设直接写进主补丁第二 insert，
  排在主预设之前；persona 正文放包内 `member-personas/{slug}.md`，
  prefix 用值位置 `!!js`（包名锚定 createRequire，同 customSkillDirs 模式，
  在 profile 根 baseUrl 下求值命中安装副本）。文本逐字相等已由
  求值比对 + verify 双重校验。
- 教训沉淀：`!!js` 块标量（`>-`）内容缩进必须比键行深；明文标量不能含
  `: `（三元运算符会破坏 YAML 平文标量）；persona 正文外移 .md 同时解决
  单行长度与主文件膨胀（主补丁 661 行，如继续膨胀应把成员预设整块再外移）。
