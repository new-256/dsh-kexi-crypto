// tools/preset-probe/roster-wrap.mjs — v1.9.23 设计 B 可行性取证（开发用，**不进包**）
//
// ⚠ 本目录 tools/ 不在 package.json 的 files 里：这是开发期取证工具，
//   任何"跑一次就为证明结论"的东西都不该随包发给用户。
//
// 要证明的五件事：
//   ① 「在服务**实例**上 defineProperty 覆盖 remoteExportList」能被**真实浏览器 roster
//      路径**看到——网关取方法的方式是：
//        const callReceiver = receiverContext.extend({ invocation }).get(descriptor.service)
//        const implementation = descriptor.implementation ?? descriptor.method
//        const method = Reflect.get(callReceiver, implementation)
//      （dsh-api-gateway/lib/index.js L1211-1213；agentPresets/list 的 implementation =
//       'remoteExportList'，见 dsh-agent-preset-registry/lib/typert.host.js L41-46）
//      → `ctx.get()` 返回的是**作用域代理**而不是原始实例，直接拿原始实例调方法证明不了
//        任何事（本探针第一版就是这么错的，失败原文见 docs/repair-plan-v1.9.23.md §3.1）。
//   ② 真实引导 profile 后，走该路径取到的 roster 确实少了 5 个 kexi-member-*，
//      非 kexi 预设与 order 排序不受影响；
//   ③ 语义边界：只影响对外 roster；list()/readDocument()/resolve()/mount 仍能拿到
//      五个成员预设（否则委派直接断掉）；
//   ④ 负对照：形状违规/行缺 id/方法缺失/重复安装/被第三方替换后卸载 → 全部当场抛错；
//   ⑤ dispose 精确还原且可逆。
//
// 用法: node tools/preset-probe/roster-wrap.mjs
// 退出码: 0 = 全部断言通过；1 = 断言失败；2 = 引导/取服务失败
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { join, dirname } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'
import { installPresetRosterFilter } from '../../home-plugin/dsh-kexi-crypto/lib/preset-roster.mjs'
import { MEMBER_PRESET_IDS, INTERNAL_PRESET_PREFIX } from '../../home-plugin/dsh-kexi-crypto/lib/members.mjs'

const BACKEND = process.env.DSH_BACKEND || 'C:\\Users\\lcl\\AppData\\Roaming\\DSH Desktop\\backend\\dsh'
const PROFILE = process.env.DSH_PROFILE || 'web'
const req = createRequire(join(BACKEND, 'x.js'))

let failures = 0
const ok = (cond, msg) => {
  console.log(cond ? '  ✓ ' + msg : '  ✗ ' + msg)
  if (!cond) failures++
}

// ── 1. 读真实 typert 描述符，确认 agentPresets/list 的实现映射没变 ────────────
const registryRoot = dirname(req.resolve('@deepseek-ai/dsh-agent-preset-registry/package.json'))
const { TYPERT } = await import(pathToFileURL(join(registryRoot, 'lib', 'typert.host.js')).href)
const desc = TYPERT.invocations.find((d) => d.id === '@deepseek-ai/dsh-agent-preset-registry#agentPresets/list')
if (!desc) {
  console.error('FATAL: 取不到 typert 描述符 agentPresets/list（上游契约已变）')
  process.exit(2)
}
const implementation = desc.implementation ?? desc.method
console.log(`真实描述符 agentPresets/list → service=${desc.service} implementation=${implementation}`)
ok(implementation === 'remoteExportList', `agentPresets/list 的实现仍是 remoteExportList（实际 ${implementation}）`)

// ── 2. 真实引导 profile，拿真服务，并按网关路径取方法 ─────────────────────────
const bootUrl = pathToFileURL(req.resolve('@deepseek-ai/dsh/lib/profile-boot.js')).href
const envUrl = pathToFileURL(req.resolve('@deepseek-ai/dsh-app-boot')).href
const { runProfile } = await import(bootUrl)
const { loadLayeredEnv } = await import(envUrl)

let ctx, shutdown
try {
  ({ ctx, shutdown } = await runProfile({
    profile: PROFILE, environment: loadLayeredEnv('dsh'), patchFiles: [], args: ['--no-open'],
  }))
} catch (e) {
  console.error('BOOT FAIL:', e?.stack || e)
  process.exit(2)
}
await sleep(Number(process.env.DSH_SETTLE_MS || 10000))

/** 复刻网关取方法的两行（作用域接收者 + implementation 映射），再原样调用。 */
async function browserRosterIds() {
  const callReceiver = ctx.extend({ invocation: 'kexi-probe' }).get(desc.service)
  const method = Reflect.get(callReceiver, implementation)
  if (typeof method !== 'function') throw new Error(`作用域接收者上没有可调用方法 ${implementation}`)
  const out = await method.apply(callReceiver, [])
  if (!out || !Array.isArray(out.presets)) throw new Error(`roster 返回值形状异常：${JSON.stringify(out)?.slice(0, 120)}`)
  return out.presets.map((row) => row.id)
}

let code = 0
try {
  const svc = ctx.get('agentPresets')
  if (!svc) {
    console.error('FATAL: agentPresets 服务不存在')
    process.exitCode = 2
  } else {
    const beforeIds = await browserRosterIds()
    const internalBefore = (await svc.list()).map((p) => p.id)
    // 语义边界基线：安装前后**自有属性只应多出 remoteExportList + 挂载标记**。
    // （不能用 svc[key] 的函数身份比较：ctx.get() 是作用域代理，每次取方法的
    //   包装对象都不同，比身份只会得到假失败——这是第 2 次栽在同一件事上。）
    const ownBefore = new Set(Object.getOwnPropertyNames(svc))
    const symBefore = new Set(Object.getOwnPropertySymbols(svc))
    console.log(`\n包裹前（浏览器路径）${beforeIds.length} 行:\n  ${beforeIds.join(', ')}`)

    const dispose = installPresetRosterFilter(svc)
    ok(Object.hasOwn(svc, 'remoteExportList'), '② 包裹落在实例自有属性上（遮蔽原型方法）')

    const afterIds = await browserRosterIds()
    console.log(`\n包裹后（浏览器路径）${afterIds.length} 行:\n  ${afterIds.join(', ')}\n`)

    const dropped = beforeIds.filter((id) => !afterIds.includes(id))
    ok(dropped.length === MEMBER_PRESET_IDS.length,
      `② 恰好少 ${MEMBER_PRESET_IDS.length} 行，实际 ${dropped.length}（${dropped.join(', ')}）`)
    ok(MEMBER_PRESET_IDS.every((id) => dropped.includes(id)), '② 丢掉的正是不该出现在选择器的 5 个内部成员预设')
    ok(!afterIds.some((id) => id.startsWith(INTERNAL_PRESET_PREFIX)), '② 过滤后不含任何 kexi-member-* 前缀行')
    ok(afterIds.includes('standard'), '② 官方 standard 仍在（没有误伤）')
    ok(afterIds.includes('kexi-crypto'), '② K析研判团主预设 kexi-crypto 仍在（用户还得能选它）')
    const expected = beforeIds.filter((id) => !id.startsWith(INTERNAL_PRESET_PREFIX))
    ok(JSON.stringify(afterIds) === JSON.stringify(expected), '② 相对顺序与过滤前一致（order 排序不被破坏）')

    // ── ③ 语义边界：过滤只影响对外 roster ────────────────────────────────────
    const internalAfter = (await svc.list()).map((p) => p.id)
    ok(JSON.stringify(internalAfter) === JSON.stringify(internalBefore),
      '③ 内部 list() 结果完全不变（若把过滤下沉到 list() 这里立刻红）')
    ok(MEMBER_PRESET_IDS.every((id) => internalAfter.includes(id)), '③ list() 仍含五个内部成员预设（委派行靠它校验引用）')
    let readOk = 0
    for (const id of MEMBER_PRESET_IDS) {
      try {
        const doc = await svc.readDocument(id)
        if (doc && typeof doc.content === 'string' && doc.content.length > 0) readOk++
      } catch { /* 下面统一断言 */ }
    }
    ok(readOk === MEMBER_PRESET_IDS.length, `③ readDocument() 仍能读全部 5 个成员预设（实际 ${readOk}/5）`)
    let resolveOk = 0
    const resolveBroken = []
    for (const id of MEMBER_PRESET_IDS) {
      try {
        const def = await svc.resolve(id)
        if (def) resolveOk++
        if (def && def.broken) resolveBroken.push(`${id}: ${def.broken}`)
      } catch { /* 下面统一断言 */ }
    }
    ok(resolveOk === MEMBER_PRESET_IDS.length, `③ resolve() 仍能解析全部 5 个成员预设（实际 ${resolveOk}/5）`)
    ok(resolveBroken.length === 0, `③ 五个成员预设均无 broken 诊断（实际 ${resolveBroken.join(' | ') || '无'}）`)
    // mount 是从 profile 根 ctx 无法完成的（上游要求 agent 作用域），这里只证明
    // mount **没有被过滤器挡掉**：要么在 agent 作用域里成功，要么报的是作用域错误。
    let mountNote = 'skipped'
    try {
      await svc.mount(ctx, MEMBER_PRESET_IDS[1])
      mountNote = 'mounted-at-root'
    } catch (e) {
      mountNote = String(e?.message || e)
      ok(/scoped context/i.test(mountNote), `③ mount() 未被过滤器拦截（报的是上游作用域约束：${mountNote.slice(0, 60)}…）`)
    }
    if (mountNote === 'mounted-at-root') ok(true, '③ mount() 在 profile 根 ctx 直接成功（上游行为）')
    const ownAfter = Object.getOwnPropertyNames(svc).filter((k) => !ownBefore.has(k))
    const symAfter = Object.getOwnPropertySymbols(svc).filter((s) => !symBefore.has(s))
    ok(JSON.stringify(ownAfter) === JSON.stringify(['remoteExportList']),
      `③ 只新增 remoteExportList 一个自有属性（实际 ${JSON.stringify(ownAfter)}）`)
    ok(symAfter.length === 1 && symAfter[0] === Symbol.for('kexi.rosterFilter.v1'),
      '③ 只新增单进程挂载标记一个 symbol（没在服务上乱挂东西）')

    // ── ④ 负对照：注入违规，证明校验会红 ───────────────────────────────────
    const callOn = (service) => Reflect.get(service, implementation).call(service)
    let caughtShape = false
    const badShape = { async remoteExportList() { return {} } }
    try { installPresetRosterFilter(badShape); await callOn(badShape) } catch (e) { caughtShape = /presets 数组/.test(String(e.message)) }
    ok(caughtShape, '④ 负对照：返回值缺 presets 数组 → 抛错（不静默放行）')

    let caughtId = false
    const badRow = { async remoteExportList() { return { presets: [{ isDefault: false }] } } }
    try { installPresetRosterFilter(badRow); await callOn(badRow) } catch (e) { caughtId = /字符串 id/.test(String(e.message)) }
    ok(caughtId, '④ 负对照：roster 行缺字符串 id → 抛错（判定不了就把问题顶出来）')

    let caughtMissing = false
    try { installPresetRosterFilter({}) } catch { caughtMissing = true }
    ok(caughtMissing, '④ 负对照：服务无 remoteExportList → 安装即抛错')

    let caughtDup = false
    try { installPresetRosterFilter(svc) } catch (e) { caughtDup = /已在本进程安装过/.test(String(e.message)) }
    ok(caughtDup, '④ 负对照：重复安装 → 抛错（不静默跳过）')

    // 被第三方替换后再卸载：必须拒绝盲目还原
    // （取原始值必须用 descriptor：服务的普通属性访问走作用域代理，会拿到绑定包装函数）
    const installedWrapper = Object.getOwnPropertyDescriptor(svc, Symbol.for('kexi.rosterFilter.v1')).value.wrapper
    Object.defineProperty(svc, 'remoteExportList', { configurable: true, writable: true, value: async () => ({ presets: [] }) })
    let caughtHijack = false
    try { dispose() } catch (e) { caughtHijack = /已被替换/.test(String(e.message)) }
    ok(caughtHijack, '④ 负对照：方法被第三方替换后 dispose → 抛错（拒绝抹掉别人的实现）')
    // 复原现场（装回我们那个被替换掉的包裹函数），继续验证正常 dispose
    Object.defineProperty(svc, 'remoteExportList', { configurable: true, writable: true, value: installedWrapper })

    // ── ⑤ dispose 精确还原且可逆 ───────────────────────────────────────────
    dispose()
    ok(!Object.hasOwn(svc, 'remoteExportList'), '⑤ dispose 删掉遮蔽属性（恢复原型方法）')
    ok(svc[Symbol.for('kexi.rosterFilter.v1')] === undefined, '⑤ dispose 清掉单进程挂载标记（可再次安装）')
    const restored = await browserRosterIds()
    ok(restored.length === beforeIds.length, '⑤ dispose 后 roster 恢复完整（可逆）')
    const dispose2 = installPresetRosterFilter(svc)
    ok(typeof dispose2 === 'function', '⑤ dispose 后可重新安装（标记已清）')
    dispose2()

    code = process.exitCode === 2 ? 2 : (failures ? 1 : 0)
  }
} catch (e) {
  console.error('PROBE FAIL:', e?.stack || e)
  code = 2
}

console.log(code === 0 ? '\nROSTER-WRAP: PASS' : `\nROSTER-WRAP: FAIL (${code})`)
try {
  await Promise.race([shutdown.shutdown(0), sleep(4000)])
} catch {}
process.exit(code)
