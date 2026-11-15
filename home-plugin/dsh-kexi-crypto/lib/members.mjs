// home-plugin/dsh-kexi-crypto/lib/members.mjs — 五位成员身份的**唯一真源**（v1.9.23）
//
// 为什么需要这个文件：
//   v1.9.23 把 6 个委派工具收敛成 1 个 `subagent`。上游 0.2.1 的 dsh-tool-subagent
//   会按 `allowedAgentPresets` 派生一个 `agent_preset` 枚举参数，**工具名不再是身份
//   信号**——身份只能由 preset id 派生。旧代码在 host / client / 脚本里分别硬编码了
//   五个成员工具名，收敛后这些位置全部失效（表现是成员在卡片上退化成"成员"两个字）。
//
// 纪律（用户规则 3：禁针对样例硬编码）：
//   · 本表是「preset id → 成员身份」的唯一映射，host 活动流/名册、client 名册、
//     门禁脚本三方都从这里取值；别处**不得**再写死成员工具名/中文名。
//   · 拿不到 preset 身份时，调用方必须**显式标注未知**（见 index.mjs 的 unknownIdentity
//     分支），不得静默套用通用「成员」标签（用户规则 1：禁降级兜底）。
//
// 成员 key 与 kexi_ensure_team 的持久成员代号一致：同一批人在"持久团队"与"按需委派"
// 两条路径上是同一个身份，卡片/关系图/名册才对得上。

/** 内部成员预设 id 前缀（设计 B 的过滤判据，也是"内部预设"的唯一定义）。 */
export const INTERNAL_PRESET_PREFIX = 'kexi-member-'

/** 五位成员的唯一真源（冻结：任何人都不该在运行时改写身份表）。 */
export const KEXI_MEMBERS = Object.freeze([
  Object.freeze({ preset: 'kexi-member-market-data', key: 'shuma', cn: '数脉', role: '行情取数与数据质检', short: '取数质检' }),
  Object.freeze({ preset: 'kexi-member-technical', key: 'zhibei', cn: '指北', role: '指标计算 · 形态量价', short: '技术指标' }),
  Object.freeze({ preset: 'kexi-member-forecaster', key: 'wangchao', cn: '望潮', role: '三情景概率 · 证伪条件', short: '趋势推演' }),
  Object.freeze({ preset: 'kexi-member-risk', key: 'shouzhuo', cn: '守拙', role: '风险定级 · 可执行提示', short: '风险定级' }),
  Object.freeze({ preset: 'kexi-member-falsifier', key: 'zhengfu', cn: '证伪', role: '唱反调 · 攻击结论前提', short: '红队证伪' }),
])

/** 委派工具 allowedAgentPresets 的权威取值（顺序即 agent_preset 枚举顺序）。 */
export const MEMBER_PRESET_IDS = Object.freeze(KEXI_MEMBERS.map((m) => m.preset))

const BY_PRESET = new Map(KEXI_MEMBERS.map((m) => [m.preset, m]))
const BY_KEY = new Map(KEXI_MEMBERS.map((m) => [m.key, m]))

/**
 * preset id → 成员身份。
 * @param {unknown} presetId
 * @returns {object|null} 非内部成员预设（含 undefined/空串/非字符串）一律返回 null，
 *   由调用方决定如何**显式**呈现——不要在这里"猜一个成员"。
 */
export function memberOfPreset(presetId) {
  if (typeof presetId !== 'string' || presetId === '') return null
  return BY_PRESET.get(presetId) || null
}

/** 成员代号（shuma/zhibei/...）→ 成员身份；未知返回 null。 */
export function memberOfKey(key) {
  if (typeof key !== 'string' || key === '') return null
  return BY_KEY.get(key) || null
}

/** 主预设 id（会话头 agentPreset 引用它；也是"这是不是一个 K析会话"的判据之一）。 */
export const MAIN_PRESET_ID = 'kexi-crypto'

/** 是否是内部成员预设 id（设计 B 的过滤判据）。 */
export function isInternalPreset(presetId) {
  return typeof presetId === 'string' && presetId.startsWith(INTERNAL_PRESET_PREFIX)
}

/**
 * 是否是本插件的预设（主预设或任一成员预设）。
 * 用途：`subagent` 是通用工具名——只有"属于本团队的委派"才算 kexi 活动节点，
 * 否则别的预设里的普通子代理也会被点亮成研判进度（v1.6.1 那个"卡片满屏团队进度"
 * 的同类问题）。判据全部来自 preset id，不引入工具名硬编码。
 */
export function isKexiPreset(presetId) {
  return presetId === MAIN_PRESET_ID || isInternalPreset(presetId)
}
