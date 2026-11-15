// scripts/test-preset-contract.mjs — 0.2.1 预设契约回归（v1.9.21 事故钉死；v1.9.23 收敛后重写）
//
// 事故背景：DSH 0.2.1 的 @deepseek-ai/dsh-tool-subagent 移除了 persona/toolFilter/
// backgroundMode 配置字段，apply() 对拒绝字段逐个 throw（"persona is no longer
// supported; configure persona and tool plugins in an Agent Preset ..."），
// 一行失败 → 整个 kexi-crypto 预设挂载失败（roster broken，选择即 RemoteError）。
//
// v1.9.23：6 个委派工具收敛为 1 个（toolName=subagent + allowedAgentPresets）。
// 本测试不硬编码上游字段名，而是从【当前实际安装】的 dsh-tool-subagent 源码里
// 提取 apply 的拒绝字段列表，再对我们补丁里每个 subagent 行做：
//   ① 真实 Config schema 归一化校验（类型/必填错误会暴露）；
//   ② 拒绝字段零出现（apply 会 throw 的键一个都不许有）；
//   ③ allowedAgentPresets 与成员预设声明 **双向** 一一对应——上游 validate()
//      会对每个 id 调 presets.readDocument，漏声明在挂载期才炸，这里提前拦下；
// 并内置负对照：旧 6 行形态 / 引用幽灵预设，必须被同一套判据判坏
// （HANDOFF §6.5：不做负对照的断言只是"永远绿"的装饰）。
//
// 用法: node scripts/test-preset-contract.mjs
import { readFileSync, existsSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'

const ROOT = resolve(dirname(dirname(fileURLToPath(import.meta.url))))
let failures = 0
const ok = (cond, msg) => {
  if (cond) { console.log('  ✓', msg) }
  else { failures++; console.error('  ✗', msg) }
}

const BACKEND = process.env.DSH_BACKEND || 'C:\\Users\\lcl\\AppData\\Roaming\\DSH Desktop\\backend\\dsh'
const req = createRequire(join(BACKEND, 'x.js'))
const PATCH = join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'cordis.patch.yml')

// ── 0. 成员身份唯一真源（不在测试里再抄一份 id 列表）──────────────────────────
const { MEMBER_PRESET_IDS } = await import(
  pathToFileURL(join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'lib', 'members.mjs')).href)
console.log('成员预设（唯一真源）:', MEMBER_PRESET_IDS.join(', '))

// ── 1. 载入真实安装包 ────────────────────────────────────────────────────────
let subagentMod, subagentEntry, subagentSrc
try {
  subagentEntry = req.resolve('@deepseek-ai/dsh-tool-subagent')
  subagentSrc = readFileSync(subagentEntry, 'utf8')
  subagentMod = await import(pathToFileURL(subagentEntry).href)
} catch (e) {
  console.error('FATAL 无法从 DSH_BACKEND 载入 @deepseek-ai/dsh-tool-subagent：', e.message)
  console.error('      DSH_BACKEND =', BACKEND)
  process.exit(2)
}
const ver = JSON.parse(readFileSync(
  req.resolve('@deepseek-ai/dsh-tool-subagent/package.json'), 'utf8')).version
console.log(`真实包 @deepseek-ai/dsh-tool-subagent@${ver}`)
console.log(`  ${subagentEntry}`)

// ── 2. 从源码提取 apply 的拒绝字段列表（不硬编码，跟随上游）─────────────────
const rejectM = subagentSrc.match(/for\s*\(\s*const\s+field\s+of\s+\[([^\]]+)\]\s*\)[\s\S]{0,200}?is no longer supported/)
ok(!!rejectM, '从真实包源码提取到 apply() 拒绝字段列表（正则未失效）')
const rejectedFields = rejectM
  ? [...rejectM[1].matchAll(/['"]([^'"]+)['"]/g)].map((m) => m[1])
  : []
if (rejectM) console.log('  上游 apply 拒绝字段:', rejectedFields.join(', '))
ok(rejectedFields.includes('persona'), '负对照前置：拒绝列表含 persona')
ok(rejectedFields.includes('toolFilter'), '负对照前置：拒绝列表含 toolFilter')

// ── 3. 解析补丁（!!js 标量占位，不影响 subagent 行）─────────────────────────
const yamlPath = join(BACKEND, 'node_modules', 'js-yaml', 'index.js')
ok(existsSync(yamlPath), 'js-yaml 可从 DSH_BACKEND 解析')
const yaml = (await import(pathToFileURL(yamlPath).href)).default
const jsType = new yaml.Type('tag:yaml.org,2002:js', {
  kind: 'scalar', resolve: (d) => typeof d === 'string',
  construct: (d) => ({ __jsExpr: String(d) }),
})
const schema = yaml.JSON_SCHEMA.extend([jsType])

const doc = yaml.load(readFileSync(PATCH, 'utf8'), { schema })
const rows = doc.flatMap((ins) => ins.insert || [])
const inlineMemberRows = rows.filter((r) => /^preset-kexi-member-/.test(r.id))
ok(inlineMemberRows.length === MEMBER_PRESET_IDS.length,
  `主补丁内联 ${MEMBER_PRESET_IDS.length} 个成员预设声明行，实际 ${inlineMemberRows.length}`)
// 注册时序：成员预设声明必须全部排在主预设之前（委派行 apply 时 readDocument 校验）
const iMain = rows.indexOf(rows.find((r) => r.id === 'preset-kexi-crypto'))
for (const r of inlineMemberRows) {
  ok(rows.indexOf(r) < iMain, `${r.id} 必须排在 preset-kexi-crypto 之前（注册时序）`)
}
// persona 正文文件存在（挂载时经 !!js 读取，缺失会 fail-loud 炸掉预设）
for (const r of inlineMemberRows) {
  const slug = r.config?.id?.replace('kexi-member-', '')
  ok(slug && existsSync(join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'member-personas', `${slug}.md`)),
    `persona 正文文件缺失: member-personas/${slug}.md`)
}
const declaredPresetIds = new Set(inlineMemberRows.map((r) => r.config?.id))
console.log('  成员预设声明:', [...declaredPresetIds].join(', '))

// ── 4. 收集补丁里每个 dsh-tool-subagent 行 ──────────────────────────────────
const subRows = []
const walk = (list, where) => {
  for (const row of list || []) {
    if (row?.name === '@deepseek-ai/dsh-tool-subagent') subRows.push({ row, where })
    if (Array.isArray(row?.config)) walk(row.config, `${where}/${row.id}`)
    else if (Array.isArray(row?.config?.plugins)) walk(row.config.plugins, `${where}/${row.id}`)
  }
}
walk(rows, 'root')

/**
 * 委派行契约判据（真实断言与负对照共用同一份实现）。
 * @returns {string[]} 问题列表，空 = 合规
 */
function auditDelegationRows(list, declared) {
  const bad = []
  if (list.length !== 1) bad.push(`应有 1 个 dsh-tool-subagent 行（v1.9.23 收敛），实际 ${list.length}`)
  for (const { row } of list) {
    const cfg = row.config || {}
    try { subagentMod.Config(cfg) } catch (e) { bad.push(`${row.id} 未通过真实 Config schema: ${String(e.message).slice(0, 160)}`) }
    for (const f of rejectedFields) {
      if (Object.hasOwn(cfg, f)) bad.push(`${row.id} 含上游已移除字段 ${f}（apply 会 throw，整组预设挂死）`)
    }
    const allow = cfg.allowedAgentPresets
    if (!Array.isArray(allow)) { bad.push(`${row.id} 缺 allowedAgentPresets（agent_preset 枚举无从派生）`); continue }
    if (new Set(allow).size !== allow.length) bad.push(`${row.id} allowedAgentPresets 含重复 id`)
    for (const id of declared) if (!allow.includes(id)) bad.push(`${row.id} allowedAgentPresets 漏掉已声明预设 ${id}`)
    for (const id of allow) if (!declared.has(id)) bad.push(`${row.id} allowedAgentPresets 引用了未声明的预设 ${id}`)
  }
  return bad
}

// ── 5. 真实断言 ─────────────────────────────────────────────────────────────
const realProblems = auditDelegationRows(subRows, declaredPresetIds)
ok(realProblems.length === 0, `委派行契约不合规:\n      ${realProblems.join('\n      ') || '（无）'}`)
const only = subRows[0]?.row
ok(only?.config?.toolName === 'subagent', `唯一委派行的 toolName 应为 subagent（实际 ${only?.config?.toolName}）`)
ok(only?.config?.maxDepth === 1, `唯一委派行应 maxDepth: 1（D9：0 会让首次委派即被拒）`)
ok(!only?.config?.agentPreset, '单行不应固定 agentPreset（省略时子代理继承主理人预设 = 自查路径）')

// ── 6. 负对照：证明这套判据真的会红 ─────────────────────────────────────────
// ① 旧 6 行形态（通用 subagent + 5 个旧 toolName 行）
const legacyRows = [
  { row: { id: 'tool-subagent', name: '@deepseek-ai/dsh-tool-subagent', config: { provider: 'spawn', toolName: 'subagent' } }, where: 'root/delegation' },
  ...['market_data_specialist', 'technical_indicators', 'trend_forecaster', 'risk_assessor', 'falsification_challenger']
    .map((tn) => ({ row: { id: `member-${tn}`, name: '@deepseek-ai/dsh-tool-subagent', config: { provider: 'spawn', toolName: tn, maxDepth: 1, agentPreset: 'kexi-member-x' } }, where: 'root/delegation' })),
]
const legacyProblems = auditDelegationRows(legacyRows, declaredPresetIds)
ok(legacyProblems.some((p) => /应有 1 个/.test(p)), `负对照①：旧 6 行形态被判坏（${legacyProblems[0] || '未判坏'}）`)

// ② 引用幽灵预设（上游 validate 会在挂载期 throw，这里必须提前红）
const ghostRows = [{
  row: { id: 'tool-subagent', name: '@deepseek-ai/dsh-tool-subagent', config: { provider: 'spawn', toolName: 'subagent', maxDepth: 1, allowedAgentPresets: [...MEMBER_PRESET_IDS, 'kexi-member-ghost'] } },
  where: 'root/delegation',
}]
const ghostProblems = auditDelegationRows(ghostRows, declaredPresetIds)
ok(ghostProblems.some((p) => /未声明的预设 kexi-member-ghost/.test(p)), '负对照②：allowedAgentPresets 引用未声明预设被判坏')

// ③ 漏一个成员（只加声明不改 allow，或反之）
const shortRows = [{
  row: { id: 'tool-subagent', name: '@deepseek-ai/dsh-tool-subagent', config: { provider: 'spawn', toolName: 'subagent', maxDepth: 1, allowedAgentPresets: MEMBER_PRESET_IDS.slice(0, -1) } },
  where: 'root/delegation',
}]
const shortProblems = auditDelegationRows(shortRows, declaredPresetIds)
ok(shortProblems.some((p) => /漏掉已声明预设/.test(p)), '负对照③：allowedAgentPresets 漏成员被判坏')

// ④ 旧格式配置（persona/toolFilter）必须被 schema/拒绝字段判据拦下
const legacyCfg = { provider: 'spawn', toolName: 'subagent', maxDepth: 1, persona: '你是…', toolFilter: { deny: ['write'] } }
const caught = rejectedFields.filter((f) => Object.hasOwn(legacyCfg, f))
ok(caught.length === 2, `负对照④：旧格式配置被拦截字段数=2（persona+toolFilter），实际 ${caught.length}（${caught.join(',')}）`)

// ── 7. 成员预设自身结构契约 ─────────────────────────────────────────────────
for (const r of inlineMemberRows) {
  ok(r.name === '@deepseek-ai/dsh-agent-preset', `${r.config?.id} 是 dsh-agent-preset 声明行`)
  const plugs = r.config?.plugins || []
  // 成员预设里绝不允许再出现任何 dsh-tool-subagent 行（成员不可再委派）
  const nested = []
  ;(function scan(list) {
    for (const p of list || []) {
      if (p.name === '@deepseek-ai/dsh-tool-subagent') nested.push(p.id)
      if (Array.isArray(p.config)) scan(p.config)
    }
  })(plugs)
  ok(nested.length === 0, `${r.config?.id} 内无 subagent 行（成员不可再委派），发现: ${nested.join(',') || '无'}`)
  ok(plugs.some((p) => p.name === 'dsh-kexi-crypto/kexi-plugin'), `${r.config?.id} 挂有 kexi-plugin（成员分析手段）`)
}

console.log(failures === 0 ? '\nPRESET-CONTRACT: PASS' : `\nPRESET-CONTRACT: FAIL (${failures})`)
process.exit(failures ? 1 : 0)
