// scripts/test-preset-roster.mjs — 设计 B 回归（v1.9.23：内部成员预设不出现在 GUI 选择器）
//
// 为什么单开一条：这段逻辑的正确性依赖**上游分发机制**（typert 端点 'agentPresets/list'
// → implementation 'remoteExportList'），不是我们自己的算法。所以这里既断言
// 实际安装包里的映射没变（映射一变，包裹就失效且无任何报错），也断言过滤器语义：
//   · 只过滤 kexi-member-*，其它预设与相对顺序原样保留；
//   · 返回新对象（不原地改宿主缓存）；
//   · 形状违规/行缺 id/无方法/重复安装/被替换后卸载 → 全部抛错；
//   · dispose 精确还原且可逆。
// 真实引导下的端到端证据见 tools/preset-probe/roster-wrap.mjs（不进包）与
// scripts/check-preset-mount.mjs（发版门禁）。
//
// 用法: node scripts/test-preset-roster.mjs
import { readFileSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { installPresetRosterFilter } from '../home-plugin/dsh-kexi-crypto/lib/preset-roster.mjs'
import { MEMBER_PRESET_IDS } from '../home-plugin/dsh-kexi-crypto/lib/members.mjs'

const ROOT = resolve(dirname(dirname(fileURLToPath(import.meta.url))))
let failures = 0
const ok = (cond, msg) => {
  if (cond) { console.log('  ✓', msg) } else { failures++; console.error('  ✗', msg) }
}
const head = (s) => console.log('\n[' + s + ']')
const throwsWith = async (fn, re) => {
  try { await fn(); return false } catch (e) { return re.test(String(e && e.message)) }
}

// ── 1. 上游映射（唯一外部依赖点）────────────────────────────────────────────
head('1. 上游 typert 映射仍是 remoteExportList')
const BACKEND = process.env.DSH_BACKEND || 'C:\\Users\\lcl\\AppData\\Roaming\\DSH Desktop\\backend\\dsh'
const req = createRequire(join(BACKEND, 'x.js'))
let spec = null
try {
  const registryRoot = dirname(req.resolve('@deepseek-ai/dsh-agent-preset-registry/package.json'))
  const mod = await import(pathToFileURL(join(registryRoot, 'lib', 'typert.host.js')).href)
  spec = mod.TYPERT.invocations.find((d) => d.id === '@deepseek-ai/dsh-agent-preset-registry#agentPresets/list')
} catch (e) {
  console.error('  ✗ 无法从 DSH_BACKEND 载入 typert 描述符：', e.message, '\n    DSH_BACKEND =', BACKEND)
  process.exit(2)
}
ok(!!spec, '取到 agentPresets/list 的生成描述符')
const impl = spec ? (spec.implementation ?? spec.method) : ''
ok(impl === 'remoteExportList', `implementation 仍是 remoteExportList（实际 ${impl || '无'}）——若上游改名，本过滤器静默失效，必须重新设计`)
ok(/^agentPresets\//.test(spec?.id ? spec.id.split('#')[1] : ''), '端点命名空间仍是 agentPresets')

// ── 2. 过滤语义 ─────────────────────────────────────────────────────────────
head('2. 过滤语义（只动对外 roster）')
// 原型方法形态：与真实服务一致（方法在 class 原型上，实例本身没有该属性）
class FakeRegistry {
  constructor(rows) { this.rows = rows }
  async list() { return this.rows.map((r) => ({ ...r })) }
  async remoteExportList() { return { presets: (await this.list()).map((r) => ({ ...r, isDefault: r.id === 'standard' })) } }
}
const rows = [
  { id: 'standard', order: 1 },
  { id: 'kexi-crypto', order: 5, name: 'K析研判团' },
  ...MEMBER_PRESET_IDS.map((id, i) => ({ id, order: 901 + i })),
  { id: 'ptc', order: 2 },
]
const svc = new FakeRegistry(rows)
const before = await svc.remoteExportList()
const beforeIds = before.presets.map((p) => p.id)
ok(beforeIds.length === rows.length, `基线：包裹前 ${rows.length} 行`)

const dispose = installPresetRosterFilter(svc)
ok(Object.hasOwn(svc, 'remoteExportList'), '包裹落在实例自有属性上（遮蔽原型方法）')
const after = await svc.remoteExportList()
const afterIds = after.presets.map((p) => p.id)
ok(afterIds.length === rows.length - MEMBER_PRESET_IDS.length,
  `过滤后 ${rows.length - MEMBER_PRESET_IDS.length} 行（实际 ${afterIds.length}）`)
ok(!afterIds.some((id) => id.startsWith('kexi-member-')), '过滤后不含任何 kexi-member-*')
ok(afterIds.includes('kexi-crypto') && afterIds.includes('standard') && afterIds.includes('ptc'), '非成员预设全部保留（没有误伤）')
const expected = beforeIds.filter((id) => !id.startsWith('kexi-member-'))
ok(JSON.stringify(afterIds) === JSON.stringify(expected), '相对顺序与过滤前一致（order 排序不被破坏）')
ok(after !== before && after.presets !== before.presets, '返回新对象与新数组（不原地改宿主缓存）')
ok(beforeIds.length === rows.length, '包裹前的数组未被就地修改')
const stillList = (await svc.list()).map((r) => r.id)
ok(stillList.length === rows.length, 'list() 仍返回完整集合（过滤不得下沉到 list()，否则委派引用校验会断）')
ok(MEMBER_PRESET_IDS.every((id) => stillList.includes(id)), 'list() 仍含全部成员预设（readDocument/resolve/mount 的调用方靠它）')
ok(after.presets.find((p) => p.id === 'kexi-crypto')?.isDefault === false, '行内其它字段原样保留（isDefault 不被破坏）')

// ── 3. 负对照：注入违规必须当场抛错 ─────────────────────────────────────────
head('3. 负对照（形状违规一律 fail-loud，不静默放行）')
ok(await throwsWith(() => installPresetRosterFilter({}), /remoteExportList/), '无 remoteExportList → 安装即抛错')
ok(await throwsWith(() => installPresetRosterFilter(null), /服务缺失/), '服务为 null → 安装即抛错')
ok(await throwsWith(() => installPresetRosterFilter(svc), /已在本进程安装过/), '重复安装 → 抛错（不静默跳过）')

const badShape = { async remoteExportList() { return {} } }
installPresetRosterFilter(badShape)
ok(await throwsWith(() => badShape.remoteExportList(), /presets 数组/), '返回值缺 presets 数组 → 抛错')

const badRow = { async remoteExportList() { return { presets: [{ isDefault: false }] } } }
installPresetRosterFilter(badRow)
ok(await throwsWith(() => badRow.remoteExportList(), /字符串 id/), 'roster 行缺字符串 id → 抛错（判定不了就顶出来）')

// 被第三方替换后卸载：拒绝盲目还原（否则会把别人的实现抹掉）
const installed = Object.getOwnPropertyDescriptor(svc, Symbol.for('kexi.rosterFilter.v1')).value.wrapper
Object.defineProperty(svc, 'remoteExportList', { configurable: true, writable: true, value: async () => ({ presets: [] }) })
ok(await throwsWith(async () => dispose(), /已被替换/), '方法被第三方替换后 dispose → 抛错')
Object.defineProperty(svc, 'remoteExportList', { configurable: true, writable: true, value: installed })

// ── 4. dispose 精确还原 ─────────────────────────────────────────────────────
head('4. dispose 精确还原且可逆')
dispose()
ok(!Object.hasOwn(svc, 'remoteExportList'), 'dispose 删掉遮蔽属性（恢复原型方法）')
ok(Object.getOwnPropertyDescriptor(svc, Symbol.for('kexi.rosterFilter.v1')) === undefined, 'dispose 清掉单进程挂载标记')
const restored = await svc.remoteExportList()
ok(restored.presets.length === rows.length, 'dispose 后 roster 恢复完整（可逆）')
const dispose2 = installPresetRosterFilter(svc)
ok(typeof dispose2 === 'function', 'dispose 后可重新安装（标记已清，不会假报重复）')
dispose2()

console.log(failures === 0 ? '\nPRESET-ROSTER: PASS' : `\nPRESET-ROSTER: FAIL (${failures})`)
process.exit(failures ? 1 : 0)
