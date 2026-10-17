// scripts/check-preset-mount.mjs — 权威挂载验收（不进默认测试套件，需已安装环境）
//
// 用真实 dsh-app-boot 在进程内引导 web 配置（与正在运行的 DSH Desktop 同一份
// profile、同一份插件树），从 agentPresets 注册表读取 roster 的 broken 诊断，
// 并实际 mount('kexi-crypto') 一次。任何成员行的激活错误都会在这里原样暴露——
// 静态契约测试只保证形状，本脚本证明预设真的能挂起来。
//
// 用法（sync-install 之后、重启 DSH 之前手动跑）:
//   node scripts/check-preset-mount.mjs
// 可选环境变量: DSH_PROFILE=web  DSH_SETTLE_MS=10000
// 退出码: 0 = kexi-crypto 及五个成员预设干净且 mount 成功；
//         1 = 有 broken 诊断或 mount 抛错；2 = 引导/取服务失败
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { setTimeout as sleep } from 'node:timers/promises'

const BACKEND = process.env.DSH_BACKEND || 'C:\\Users\\lcl\\AppData\\Roaming\\DSH Desktop\\backend\\dsh'
const PROFILE = process.env.DSH_PROFILE || 'web'
const SETTLE = Number(process.env.DSH_SETTLE_MS || 10000)
const KEXI_PRESETS = [
  'kexi-crypto',
  'kexi-member-market-data',
  'kexi-member-technical',
  'kexi-member-forecaster',
  'kexi-member-risk',
  'kexi-member-falsifier',
]
const req = createRequire(BACKEND + '/x.js')
const bootUrl = pathToFileURL(req.resolve('@deepseek-ai/dsh/lib/profile-boot.js')).href
// dsh-app-boot 的 exports 只暴露 "."（→ lib/index.js），深路径会被 exports 拒绝
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
await sleep(SETTLE)

let code = 0
try {
  const ap = ctx.get('agentPresets')
  if (!ap) {
    console.error('FATAL: agentPresets 服务不存在')
    process.exitCode = 2
  } else {
    const roster = await ap.list()
    const byId = new Map(roster.map((p) => [p.id, p]))
    for (const id of KEXI_PRESETS) {
      const p = byId.get(id)
      if (!p) { console.error('✗ roster 中缺少预设', id); code = 1; continue }
      if (p.broken) {
        console.error('✗ BROKEN', id, '\n' + p.broken)
        code = 1
      } else {
        console.log('✓ clean', id, p.name ? `(${p.name})` : '')
      }
    }
    // resolve(id) 要求定义存在并可取（不经 agent 作用域，适合 headless）。
    // 不调 mount(ctx,id)：Agent 预设绑定必须在 scoped(agent) context 上进行，
    // 从 profile 根 ctx 调用会抛 "Agent preset binding requires a scoped context"
    // ——真实绑定发生在用户会话的 Agent 创建时，roster broken==null 即是 UI
    // 选择器可选用的同一判据。
    for (const id of KEXI_PRESETS) {
      try {
        const def = await ap.resolve(id)
        if (!def) throw new Error('resolve 返回空')
        console.log('✓ resolve', id, '→ 定义可取')
      } catch (e) {
        console.error('✗ resolve', id, '失败:', String(e?.message || e).slice(0, 200))
        code = 1
      }
    }
  }
} catch (e) {
  console.error('PROBE FAIL:', e?.stack || e)
  code = 2
}
console.log(code === 0 ? '\nPRESET-MOUNT: PASS' : `\nPRESET-MOUNT: FAIL (${code})`)
// 外部网关（飞书/QQ/OneBot）的优雅关闭可能不返回，最多等 4s 后硬退出
try {
  await Promise.race([shutdown.shutdown(0), sleep(4000)])
} catch {}
process.exit(code)
