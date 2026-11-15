# K析研判团 v1.9.23 改造方案（委派工具面收敛 + 内部成员预设隐藏）

- 依据：`task-2`（Lead 已定设计 A/B）、DSH 0.2.1-alpha.5 上游源码逐条取证
- 方案制定：2026-10-30 01:38（北京时间，timeapi.io 取时）
- 基线版本：`dsh-kexi-crypto@1.9.22`（git `07cd884`）
- 目标版本：`1.9.23`

---

## 零、方案总原则

1. **禁止降级兜底**（用户规则 1）：拿不到成员身份 → 显式标注未知，不套通用「成员」标签；
   roster 过滤的形状违规 → 当场抛错，不 warn 后放行。
2. **禁止对样例硬编码**（用户规则 3）：成员身份由 **preset id** 数据驱动，唯一真源在
   `lib/members.mjs`；工具名不再是身份信号。
3. **单一职责、不高内聚切割**（规则 26/30）：只新增两个模块，各 ≤200 行且职责单一；
   `index.mjs` 只 import，不再持有第二份身份表。
4. **每阶段完成后立即校验**（规则 16）：`node --check` / `node scripts/verify.mjs`。
5. 测试文件放 `scripts/`（项目既有测试目录，规则 17）。

---

## 一、问题陈述

当前 `cordis.patch.yml` 的 `delegation` 组注册了 **6 个** `dsh-tool-subagent` 行：
通用 `subagent` + 5 个 `toolName=market_data_specialist / technical_indicators /
trend_forecaster / risk_assessor / falsification_challenger`。由此带来两处用户可见问题：

1. **工具面噪音**：主理人一次会话里看到 6 个语义重叠的派活工具，模型选择成本高，
   且"通用 subagent"与"角色 subagent"并存导致按需委派路径分裂。
2. **内部预设外泄**：v1.9.21 为适配 0.2.1 新增的 5 个成员预设 `kexi-member-*`
   出现在用户「智能体预设」选择器里——用户直接选它只会得到一个没有编排能力的单人成员。

---

## 二、上游取证（可复核，非推测）

### 2.1 设计 A：allowedAgentPresets 派生 `agent_preset` 参数

`.../@deepseek-ai/dsh-tool-subagent/lib/index.js`（0.2.1-alpha.5，615 行）：

| 行 | 事实 |
|---|---|
| L311-322 | `Config = z.object({... allowedAgentPresets: z.array(z.string().min(1)).min(1).default(void 0) ...})` |
| L206-209 | 构造期校验：重复 id 抛错；`agentPreset` 必须在 `allowedAgentPresets` 内 |
| L216-221 | `choices = allowedAgentPresets`；参数说明由 `descriptions` 拼出 |
| L227-237 | `validate(ctx)`：逐个 `presets.readDocument(id)`（声明不存在 → 挂载期抛错），并把 declaration 的 `description` 收进参数说明 |
| L250-256 | `resolve()`：模型传的 `agent_preset` 必须在 `choices` 内，否则抛 `agent_preset must be one of: ...` |
| L400-404 | `...presets.choices === void 0 ? {} : { agent_preset: { type:'string', enum:[...choices] } }` |

**结论**：单个行只要配 `allowedAgentPresets`，就能派生带枚举的 `agent_preset` 参数；
五个人格分别由五个成员预设承担，工具名不再是身份信号。

### 2.2 设计 B：隐藏必须发生在 host 侧 roster

| 位置 | 事实 |
|---|---|
| `dsh-agent-preset/lib/index.js` L13-19 | `Config` 只有 `id/name/description/order/plugins`——**没有 hidden** |
| `dsh-client-ui-agent-preset/lib/client.js` L1168-1169 | `presetOptions()` 只过滤 `broken === void 0`，声明侧无法隐藏 |
| 同文件 L1112 | 浏览器 roster 唯一入口 `ctx.remote.agentPresets.list()` |
| `dsh-agent-preset-registry/lib/typert.host.js` L41-46 | `'agentPresets/list'` → `implementation: 'remoteExportList'`, `invocation: {kind:'direct'}` |
| `dsh-agent-preset-registry/lib/index.js` L585-591 | `remoteExportList()` 本体：`{ presets: (await this.list()).map(row => ({...row, isDefault})) }` |
| `dsh-api-gateway/lib/index.js` L1211-1213 | 网关取方法：`reflectionContext.extend({invocation}).get(service)` → `Reflect.get(receiver, implementation ?? method)` |
| `dsh-typert-protocol/lib/index.js` L177-184 | 直接型分发每次调用重新 `Reflect.get`（不缓存函数引用） |

**结论**：可在 host 侧包裹 `remoteExportList`；`list()` 不动，因此内部
`readDocument/resolve/mount` 与挂载时序完全不受影响。

### 2.3 ⚠ 关键陷阱：`ctx.get()` 返回作用域代理

**这不是文档结论，是实测推翻的第一版假设**：直接在 `ctx.get('agentPresets')` 拿到的
对象上 `defineProperty` 覆盖方法，再从另一个 `ctx.get()` 取同名方法比较，
`seesWrapper=false`、`sameInstance=false`（详见 §三 证据 1）。

正确验证路径必须复刻网关 L1211-1213：**作用域接收者 + implementation 映射**。
本方案实现的探针 `scripts/_probe-roster-wrap.mjs` 即按此路径取证。

### 2.4 身份信号：`subagent/start` 的 identity 里没有成员名

`dsh-subagent/lib/index.js` L187-197：`identity = { runId, provider, id: childId, local }`
——**没有 `agentType`/`name`**。旧实现 `idn.agentType || idn.name` 在真实事件下恒为
`undefined`（`test-runtime.mjs` 手工构造了 `agentType`，所以测试是绿的、线上是空的）。

可用且权威的身份来源（数据驱动）：

| 来源 | 取证 |
|---|---|
| 子会话 header 带 `agentPreset` | `dsh-subagent/lib/index.js` `childSessionMeta`：`agentPreset: selectedPreset ?? composedPreset(parent)` |
| 子会话追加 `agent-preset/selected` | 同文件 L1797-1799；注册表转发为 app 级事件：`dsh-agent-preset-registry/lib/index.js` L458-460 |

**结论**：`session/event` 里的 `session.header.agentPreset` + `agent-preset/selected`
事件是成员身份的真源；按需委派与持久团队（继承 `kexi-crypto`）都能覆盖。

---

## 三、设计 B 落地与最小 harness 证据

新增 `home-plugin/dsh-kexi-crypto/lib/preset-roster.mjs`（≤200 行，单一职责）：

```js
export function installPresetRosterFilter(service) // → dispose
// 1) service 非对象 / remoteExportList 不是函数 → throw（初始化即失败）
// 2) 包裹：await 原方法 → 形状校验（presets 必须是数组、每行必须有字符串 id）
//    → 返回**新对象** + 过滤掉 kexi-member-* 前缀行
// 3) dispose：原来是自有属性则还原描述符，否则删除遮蔽属性
```

host 侧在 `apply()` 里：

```js
export const inject = ['agentPresets']            // 缺服务 = 插件不激活（显式可见）
...
const presetsSvc = ctx.get('agentPresets')
ctx.effect(() => installPresetRosterFilter(presetsSvc))   // 绑定插件生命周期
```

### 3.1 证据 1：作用域代理不可被原始实例包裹直接验证（第一版失败记录）

```
DBG own on svc = true
DBG root:   sameInstance=false seesWrapper=false rows=10
DBG extend: sameInstance=false seesWrapper=false rows=10
DBG isolate:sameInstance=false seesWrapper=false rows=10
```

（`own on svc = true` 说明实例自有属性确实写上了，但**作用域接收者不走它**；
只有按网关的 implementation 解析路径才能看到真实行为。）

### 3.2 证据 2：按网关路径包裹后 roster 真的少了 5 行（最终探针原文）

```
真实描述符 agentPresets/list → service=agentPresets implementation=remoteExportList
  ✓ agentPresets/list 的实现仍是 remoteExportList（实际 remoteExportList）
包裹前（浏览器路径）15 行:
  standard, ptc, minimal, cordis, kexi-crypto, cordis-agy, cornell-paper-readme, opencode-first,
  claude-first, codex-first, kexi-member-market-data, kexi-member-technical,
  kexi-member-forecaster, kexi-member-risk, kexi-member-falsifier
  ✓ ② 包裹落在实例自有属性上（遮蔽原型方法）
包裹后（浏览器路径）10 行:
  standard, ptc, minimal, cordis, kexi-crypto, cordis-agy, cornell-paper-readme, opencode-first,
  claude-first, codex-first
  ✓ ② 恰好少 5 行，实际 5（kexi-member-market-data, kexi-member-technical, kexi-member-forecaster, kexi-member-risk, kexi-member-falsifier）
  ✓ ② 丢掉的正是不该出现在选择器的 5 个内部成员预设
  ✓ ② 过滤后不含任何 kexi-member-* 前缀行
  ✓ ② 官方 standard 仍在（没有误伤）
  ✓ ② K析研判团主预设 kexi-crypto 仍在（用户还得能选它）
  ✓ ② 相对顺序与过滤前一致（order 排序不被破坏）
  ✓ ③ 包裹前 list() 与 roster 行数一致（基线自洽）
  ✓ ③ 内部 list() 结果完全不变（readDocument/resolve/mount 的调用方不受影响）
  ✓ ③ list() 仍含全部内部成员预设（只过滤对外 roster）
  ✓ ④ 负对照：返回值缺 presets 数组 → 抛错（不静默放行）
  ✓ ④ 负对照：roster 行缺字符串 id → 抛错（判定不了就把问题顶出来）
  ✓ ④ 负对照：服务无 remoteExportList → 安装即抛错
  ✓ ⑤ dispose 删掉遮蔽属性（恢复原型方法）
  ✓ ⑤ dispose 后 roster 恢复完整（可逆）
ROSTER-WRAP: PASS
```

### 3.3 初始化即失败 vs 首次调用才校验形状（偏差说明，已上报 Lead）

任务卡要求「返回值形状不含 presets 数组 → 插件初始化即 throw」。
**实测存在死锁风险，故采用等价强度的替代**：`agentPresets.list()` 内部
`await record.ready`，必要时 `await this.owner.loader.await()`
（`dsh-agent-preset-registry/lib/index.js` L533-543）。在**插件自己的 apply 阶段**
调用它会等待 loader 落定，而 loader 正等着本插件 apply 返回 → 自死锁。

因此：

- **初始化期**（apply）：严格校验「服务存在 + `remoteExportList` 是函数」——不符即 throw，
  插件标红；
- **每次调用**（包裹体内）：校验「返回值是对象 + `presets` 是数组 + 每行有字符串 id」——不符即
  throw，roster 请求当场失败并在 GUI 可见，**绝不返回未过滤的 roster**。

这不是降级：任何契约破损都在**第一个受影响的调用点**变成显式错误。

---

## 四、设计 A 落地

### 4.1 补丁单行

```yaml
- id: delegation
  ...
  config:
    - id: tool-subagent-control
    - id: tool-subagent-list-agents
    - id: tool-subagent
      name: '@deepseek-ai/dsh-tool-subagent'
      config:
        provider: spawn
        toolName: subagent
        maxDepth: 1
        allowedAgentPresets: [kexi-member-market-data, kexi-member-technical,
                              kexi-member-forecaster, kexi-member-risk, kexi-member-falsifier]
```

- 保留 `maxDepth: 1`（D9：0 会让首次委派即被拒）；
- 不传 `agentPreset` → 省略该参数时子代理继承主理人预设（模型自查/复核路径仍在）；
- 成员行 `toolName` 全部删除。

### 4.2 身份映射（唯一真源）

方案 A（最终采用）：`lib/members.mjs` 单表，`verify.mjs` 做**双向集合断言**：

- `members.mjs` 的 `MEMBER_PRESET_IDS` === 补丁 `allowedAgentPresets` === 五个
  `preset-kexi-member-*` 声明行 `config.id`；
- 同时断言 `client.js` 名册条目的 `preset` 集合与上表一致；
- 任一处增删成员而未同步 → 门禁红。

（不解析 patch 进运行时：host 侧解析 YAML 需要 js-yaml 依赖，违反"禁第三方依赖"，
且运行期解析声明文件属于把编译期事实搬到运行期。以门禁双向断言替代，强度等价。）

### 4.3 身份解析路径（fail-visible）

| 场景 | 旧行为 | 新行为 |
|---|---|---|
| `tool/call` 名 `subagent` | 无（工具名不可知） | 先 `JSON.parse(arguments)` 再取 `agent_preset`；命中 → `委派 数脉·取数质检`；解析失败 → 节点显式标「args 解析失败」，不套通用标签 |
| `subagent/start` 身份 | `idn.agentType`（恒 undefined） | `childId → preset` 映射；未知 → 节点标「身份未识别（childId 前 8 位）」+ `unknownIdentity:true`，映射到达后**回填**真实成员 |
| `subagent/catalog` | `label.split(' ')[0]`（嗅探模型自由文本） | 只按 `childId → preset` 取中文名；未命中不写入（不猜） |
| `/activity` 白名单 | 5 个成员工具名 | `subagent` |
| 关系图 | 只认 `team/member` 的 `memberCodes` | 按需委派同样写入 `memberCodes`（preset→key），两类路径一致 |

---

## 五、成员预设元数据压缩（设计 B 配套）

`description` 会被塞进 `agent_preset` 参数说明（`"${id}: ${description}"`），
必须单行、≤30 字、可辨识角色；`order` 移到 900+（排在所有用户可选用预设之后）。

| preset | 旧 name | 新 name | 新 description |
|---|---|---|---|
| kexi-member-market-data | K析成员·数脉（内部预设·勿直接选择） | 数脉·行情取数（内部） | 行情取数与数据质检，团队唯一数据入口 |
| kexi-member-technical | K析成员·指北（…） | 指北·技术指标（内部） | 指标、形态量价、位置与多周期研判 |
| kexi-member-forecaster | K析成员·望潮（…） | 望潮·趋势推演（内部） | 7 日三情景概率与可证伪区间推演 |
| kexi-member-risk | K析成员·守拙（…） | 守拙·风险定级（内部） | 风险定级、实盘风控闸门与复盘 |
| kexi-member-falsifier | K析成员·证伪（…） | 证伪·红队（内部） | 攻击结论前提的唱反调官 |

---

## 六、门禁改造与负对照设计

| 脚本 | 改动 | 负对照（证明会红） |
|---|---|---|
| `verify.mjs` | delegation 组只许 1 行 subagent；`allowedAgentPresets` 5 项与成员声明**双向**一致；无残留 5 个 toolName；成员预设 name/desc/order 断言；host 无硬编码工具名；client 名册 preset 集合一致 | 临时注入旧 6 行补丁片段 → 断言必须失败（脚本内 `assertSelfFails` 自检块） |
| `test-preset-contract.mjs` | 单行 → 真实 `Config` schema 校验；`allowedAgentPresets` 引用的每个 id 均有声明；六个旧 toolName 零出现 | 构造 `allowedAgentPresets: ['kexi-member-ghost']` → 必须被判坏 |
| `scripts/test-preset-roster.mjs`（新增） | 用**真实 typert 描述符**验证包裹在作用域接收者上生效；新对象语义；三条形状负对照 | 已在探针中实测三条全红，正式测试固化 |
| `check-preset-mount.mjs` | 真挂载后：`readDocument('kexi-crypto')` 的 subagent 行数=1、`allowedAgentPresets` 5 项；`remoteExportList()` 无 kexi-member-*，`list()` 仍有（未越权）；五个成员预设 `broken === undefined` | 需已 sync 安装副本（发版手动门禁） |
| `test-runtime.mjs` | 成员身份用例改走 `session.header.agentPreset` / `agent-preset/selected`；新增 tool/call args 为**字符串**的用例 | 断言未映射时标签含「身份未识别」 |
| `test-card-kexi-filter.py` | 白名单断言 `subagent` 而非 5 个旧名 | — |

---

## 七、影响面

| 文件 | 改动 |
|---|---|
| `home-plugin/dsh-kexi-crypto/lib/members.mjs`（新） | 唯一身份真源 |
| `home-plugin/dsh-kexi-crypto/lib/preset-roster.mjs`（新） | roster 呈现过滤 |
| `home-plugin/dsh-kexi-crypto/lib/index.mjs` | import 真源、白名单、describeTool、身份映射/回填、inject、roster 过滤安装 |
| `home-plugin/dsh-kexi-crypto/lib/client.js` | 名册 `tool` → `preset`，匹配按 preset/cn/key |
| `home-plugin/dsh-kexi-crypto/cordis.patch.yml` | delegation 6 行→1 行；5 个成员预设 name/desc/order |
| `preset/kexi-crypto/kexi-plugin.mjs` | L1088 digest 文案（委派指引） |
| `preset/kexi-crypto/skills/kexi-team-orchestration/SKILL.md` | 全篇工具名引用改 `subagent`+`agent_preset` |
| `scripts/verify.mjs`、`test-preset-contract.mjs`、`test-runtime.mjs`、`test-card-kexi-filter.py`、`check-preset-mount.mjs` | 门禁与回归 |
| `scripts/test-preset-roster.mjs`（新）、`scripts/_probe-roster-wrap.mjs`（取证） | 新测试与证据 |
| `README.md`/`HANDOFF.md`/`CHANGELOG.md`/`INSTALL.md`/`package.json` | 版本与文档同步 |

**不受影响**（刻意）：持久团队编排（`kexi_ensure_team` / `send_message` 派活）、
实盘链路、Python 脚本链、看板渲染。

---

## 八、验收标准

1. `node scripts/verify.mjs` 全绿，且新增断言在注入违规时**确实变红**；
2. `node scripts/test-preset-contract.mjs` 全绿（真实安装包 schema 校验单行）；
3. `node scripts/test-preset-roster.mjs` 全绿（真实 typert 路径 + 3 条负对照）；
4. `node scripts/test-runtime.mjs` 全绿（成员身份新路径 + JSON 字符串 args 用例）；
5. `node scripts/check-preset-mount.mjs` 全绿（sync 安装副本后）：
   委派工具 1 个、roster 已过滤、五预设 clean；
6. `powershell -File scripts/run-all-tests.ps1` 16 步全绿；
7. 全仓不再出现 `market_data_specialist|technical_indicators|trend_forecaster|risk_assessor|falsification_challenger`
   作为工具名/身份键（除变更日志与"旧名已废除"类说明）。

---

## 九、风险与遗留

1. **作用域代理行为依赖上游实现**：本方案在 0.2.1-alpha.5 实测通过；若上游改成缓存
   方法引用，包裹会失效。`check-preset-mount.mjs` 的 roster 断言是发版门禁，可第一时间发现。
2. **成员身份依赖 `session.header.agentPreset` 与 `agent-preset/selected`**：两者都是
   上游公开事实（前者进 session header、后者是注册表自身重建身份的依据）。若上游改契约，
   节点会显式显示「身份未识别」而不是静默套通用标签。
3. **`ctx.inject(['agentPresets'])` 的耦合**：注册表不启用时 host 卡片不激活（显式可见），
   这是有意的——没有 roster 就无法安全实现设计 B，宁可整插件不激活也不要半残。
4. 安装副本需 `sync-install.ps1` 后才能跑 `check-preset-mount.mjs`；本方案不改发行链路。


---

## 十、体检驱动修复（task-5，2026-10-30 追加）

依据：`docs/audit-2026-10-30.md`（P0×2 / P1×7 / P2×9）。按 Lead 分工，本会话负责：
P0-1 / P0-2 / P1-5 / P1-2 / P1-7 / P2-1 / P2-6；**P1-1、P1-3、P1-6、P2-7 归修复团**。

| 项 | 问题本质 | 修法 |
|---|---|---|
| P0-2 | `verify.mjs` 取不到 js-yaml 时 `notes.push` → 24 处 patch 契约断言静默跳过仍打印 PASS | 改 `problems.push`（缺依赖 = 检查没跑 = 红） |
| P0-1 | `tool/call` 的 `arguments` 是 JSON 字符串，`data.arguments.script` 恒 undefined → 关系图全员「其他工作」却标 catFrom='tool'（实测样式） | `describeTool` 解析一次并交出 `script`（对象/字符串两形态）；坏 JSON 显式标「参数解析失败」；夹具改喂真实字符串（保留 1 条对象形态） |
| P1-5 | `subagent/start` 的第二形参 `parent` 永远为空（上游只 `callback(info)`，dispatch 把 args[1] 剥成 thisArg）→ 成员启动/结束写进成员自己的桶 | 用子会话 `header.parentSession` 反查父会话（`subagentHome`）；`verify` 断言改正（不得再要求双参签名） |
| P1-2 | `test-v1922-fixes.mjs` 手跑全绿但没接线 | 接入 `run-all-tests.ps1` |
| P1-7 | 版本号四处漂移风险 | `verify.mjs` 断言 package.json / CHANGELOG 顶部 / README / HANDOFF 四处**同值**（不硬编码 1.9.23）；补写 CHANGELOG 缺失的 v1.9.22 条目 |
| P2-1 | `kexi-member-presets.yml`（321 行）是零引用死代码，且是"第二份人格真源"陷阱 | grep 证零引用后删除；`verify` 加"不得复活"断言；`package.json` 描述改为记录废弃原因 |
| P2-6 | `run-all-tests.ps1` 无超时、无硬件监控、第三参数 `$PSScript` 从未定义（死参数）、输出编码乱码 | 加总超时闸（每步前检查）+ 联动 `_hw-monitor.ps1`（写 TEMP，避免与 test-runtime 的 rmSync 打架）+ 删除 34 处死参数 + UTF-8 输出；**逐步骤硬超时本轮未交付**（见 result §六） |
