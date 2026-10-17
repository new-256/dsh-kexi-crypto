// scripts/test-preset-contract.mjs — 0.2.1 预设契约回归（v1.9.21 事故钉死）
//
// 事故：DSH 0.2.1 的 @deepseek-ai/dsh-tool-subagent 移除了 persona/toolFilter/
// backgroundMode 配置字段，apply() 对拒绝字段逐个 throw（"persona is no longer
// supported; configure persona and tool plugins in an Agent Preset ..."），
// 一行失败 → 整个 kexi-crypto 预设挂载失败（roster broken，选择即 RemoteError）。
//
// 本测试不硬编码上游字段名，而是从【当前实际安装】的 dsh-tool-subagent 源码里
// 提取 apply 的拒绝字段列表，再对我们补丁里每个 subagent 行做：
//   ① 真实 Config schema 归一化校验（类型/必填错误会暴露）；
//   ② 拒绝字段零出现（apply 会 throw 的键一个都不许有）；
//   ③ 每个 agentPreset 引用都能在成员预设文件里找到对应声明（注册时序由
//      verify.mjs 的 include 行顺序断言覆盖）；
// 并内置负对照：构造旧格式配置，确认本测试真的会红（防止测试本身失效）。
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
// 源码形如：for (const field of ["persona", "toolFilter"]) if (Object.hasOwn(config, field))
//   throw new Error(`tool-subagent: ${field} is no longer supported; ...`)
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
ok(inlineMemberRows.length === 5, `主补丁内联 5 个成员预设声明行，实际 ${inlineMemberRows.length}`)
// 注册时序：成员预设声明必须全部排在主预设之前
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

// ── 4. 遍历主补丁里每个 dsh-tool-subagent 行 ────────────────────────────────
const subRows = []
const walk = (list, where) => {
  for (const row of list || []) {
    if (row?.name === '@deepseek-ai/dsh-tool-subagent') subRows.push({ row, where })
    // group 行：config 直接是行数组；预设声明行：config.plugins 才是行数组
    if (Array.isArray(row?.config)) walk(row.config, `${where}/${row.id}`)
    else if (Array.isArray(row?.config?.plugins)) walk(row.config.plugins, `${where}/${row.id}`)
  }
}
walk(rows, 'root')
ok(subRows.length === 6, `应有 6 个 dsh-tool-subagent 行（1 通用+5 成员；两个 *-control 行不同名不计），实际 ${subRows.length}`)

let memberRefCount = 0
for (const { row, where } of subRows) {
  const tag = `${row.id}@${where}`
  const cfg = row.config || {}
  // ① 真实 schema 归一化（类型/必填错误在此抛出）
  let schemaOk = true
  try {
    subagentMod.Config(cfg)
  } catch (e) {
    schemaOk = false
    failures++
    console.error('  ✗', tag, 'Config schema 校验失败:', String(e.message).slice(0, 200))
  }
  if (schemaOk) console.log('  ✓', tag, '通过真实 Config schema')

  // ② 上游 apply 拒绝字段零出现
  for (const f of rejectedFields) {
    ok(!Object.hasOwn(cfg, f), `${tag} 不含已移除字段 ${f}`)
  }

  // ③ agentPreset 引用必须有对应声明
  if (cfg.agentPreset) {
    memberRefCount++
    ok(declaredPresetIds.has(cfg.agentPreset), `${tag} 的 agentPreset=${cfg.agentPreset} 在成员预设文件中有声明`)
  }
}
ok(memberRefCount === 5, `恰好 5 个成员行经 agentPreset 引用成员预设，实际 ${memberRefCount}`)

// ── 5. 成员预设自身结构契约（与 verify.mjs 互补，这里聚焦挂载级错误）────────
ok(inlineMemberRows.length === 5, `成员预设 5 行，实际 ${inlineMemberRows.length}`)
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

// ── 6. 负对照：旧格式配置必须能被本测试逻辑识别为坏（证明测试有牙齿）───────
const legacy = { provider: 'spawn', toolName: 'x', maxDepth: 1, persona: '你是…', toolFilter: { deny: ['write'] } }
const caught = rejectedFields.filter((f) => Object.hasOwn(legacy, f))
ok(caught.length === 2, `负对照：旧格式配置被拦截字段数=2（persona+toolFilter），实际 ${caught.length}（${caught.join(',')}）`)

console.log(failures === 0 ? '\nPRESET-CONTRACT: PASS' : `\nPRESET-CONTRACT: FAIL (${failures})`)
process.exit(failures ? 1 : 0)
