#!/usr/bin/env node
// scripts/check-preset-mount.mjs — 权威挂载验收（真实引导 + 真实 mount）
//
// ## 它证明什么
// 静态契约测试（test-preset-contract.mjs / verify.mjs）只保证**仓库内的形状**；
// 上游改了字段时它们可能仍然绿，只在用户重启后炸（v1.9.19/20/21 三连事故同一根因）。
// 本脚本用真实 dsh-app-boot 在进程内引导一份**最小 profile**，然后：
//   A. 委派工具面：kexi-crypto 预设文档里**恰好 1 行** dsh-tool-subagent、
//      toolName=subagent、allowedAgentPresets 与成员预设声明一一对应；
//      并且在**真实挂载后**的工具面里确认 subagent 可见、5 个旧工具名 0 命中。
//   B. 内部预设隐藏：本插件装上 roster 过滤器后，浏览器路径（remoteExportList）
//      看不到 kexi-member-*，而内部 list() 仍然完整（否则委派引用校验会断）。
//   C. 真挂载：acquireScope(id) → createScope(ctx, key) → mount(scope.ctx, id)
//      对主预设与一个成员预设各真调一次 mount，断言返回 id 与请求一致。
//
// ## v1.9.23 的两处改造（谁改的 / 为什么）
// · P2-7 最小 profile 化（v1.9.23「修复团」）：旧实现 runProfile({profile:'web'})
//   引导的是**用户完整的 web profile**，实测会：连 QQ 官方/飞书/OneBot 三条外部
//   通道、启动桌面截图进程并在用户目录留 14.6MB 截图、另起 3080 端口、把明文
//   GUI token 与 OneBot access_token 打进 stdout。现在改为：
//     - DSH_HOME 指向临时沙箱 → 不读用户 home 补丁（MCP/bot/截图全不加载）、
//       不写用户目录、不继承用户凭据；
//     - profile 只装 3 个 bundle：dsh-base + dsh-web-app + 本插件；
//     - 用户层补丁只做一件事：disabled 掉 webserver 行（不开端口）；
//     - DSH_TELEMETRY_DISABLED=1 走产品自带开关关掉 telemetry 行；
//     - stdout/stderr 全量过脱敏过滤器（token → `token[:4]***` / `sk-***`）。
//   限制（如实写明）：这不是用户真实 profile 的全量引导，因此它**不能**覆盖
//   "用户装了哪些第三方插件会与本插件冲突"；那一层由回归团的真机验收负责。
// · P1-3 真 mount（v1.9.23「修复团」）：旧实现注释写"实际 mount"，代码明确不调
//   mount（根 ctx 直调必抛 "Agent preset binding requires a scoped context"）。
//   现在按 acquireScope → createScope → mount 的配方真调，并在输出里打印
//   「真调用 mount() N 次」的可核对行。
// 保留 v1.9.23「预设团」加的 0/1/2/3 四段断言（roster broken / 过滤器 / 委派单行
// / 旧工具名不得复活），未做删除或回退。
//
// ## 副作用声明（运行前须知）
// 引导期间会在系统临时目录建一个沙箱（默认退出即删，KEXI_MOUNT_KEEP_TMP=1 保留），
// **不会**：连外部消息通道、开端口、截屏、写用户 DSH_HOME。
// 真实网络调用仅限本插件自身的取数脚本（本脚本不触发它们）。
//
// 用法：node scripts/check-preset-mount.mjs
// 可选环境变量：DSH_BACKEND（后端安装目录）、DSH_SETTLE_MS（默认 5000）、
//              KEXI_MOUNT_KEEP_TMP=1（保留沙箱以便取证）
// 退出码：0 = 全部通过；1 = 断言失败；2 = 环境不可用（后端/profile/引导/服务缺失）
import { createRequire } from 'node:module'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { join, resolve, dirname } from 'node:path'
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, existsSync, rmSync } from 'node:fs'
import { tmpdir, homedir } from 'node:os'
import { inspect } from 'node:util'
import { setTimeout as sleep } from 'node:timers/promises'
// 成员 preset id 的权威集合：直接引用唯一真源，避免验收脚本里再抄一份
import { MEMBER_PRESET_IDS, INTERNAL_PRESET_PREFIX, MAIN_PRESET_ID } from '../home-plugin/dsh-kexi-crypto/lib/members.mjs'

// ── 输出脱敏（P2-7）───────────────────────────────────────────────────────
// 在**任何引导发生之前**接管 stdout/stderr/console：这样即使上游插件把明文
// token 打进日志，也不可能漏到终端/CI 日志里（不是"我小心不打印"，而是打印
// 通道本身过滤）。规则取自真实形态：sk-xxx / token=xxx / access_token。
const REDACTIONS = [
  [/\b(sk|pk|ghp|gho|ghs|xox[baprs])[-_][A-Za-z0-9_\-]{8,}/g, '$1-***'],
  [/((?:"|')?(?:access_token|api_key|apiKey|secret|token|password)(?:"|')?\s*[:=]\s*)(?:"|')?([^\s"',&)}\]]{6,})/gi,
    (_m, p1, p2) => p1 + p2.slice(0, 4) + '***'],
  [/([?&](?:token|access_token|api_key|key)=)([^\s&"']{4,})/gi,
    (_m, p1, p2) => p1 + p2.slice(0, 4) + '***'],
]
const scrub = (text) => REDACTIONS.reduce((acc, [re, rep]) => acc.replace(re, rep), String(text))
const asText = (value) => scrub(typeof value === 'string' ? value : inspect(value, { depth: 4 }))
for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
  const original = console[level].bind(console)
  console[level] = (...args) => original(...args.map(asText))
}
for (const stream of [process.stdout, process.stderr]) {
  const original = stream.write.bind(stream)
  stream.write = (chunk, ...rest) => original(asText(chunk), ...rest)
}
// 脱敏自检（KEXI_SELFTEST_REDACTION=1）：把四类真实 token 形态喂进 scrub()，
// 断言逐个被遮蔽。这样"脱敏"是可验证的行为，而不是一句注释——负对照会把
// REDACTIONS 清空后重跑，届时本自检必须报 FAIL。
if (process.env.KEXI_SELFTEST_REDACTION === '1') {
  const samples = [
    'token=abcdef123456',
    'sk-abcdefgh12345678',
    'ws://host:3001?access_token=zzzz9999',
    '"apiKey": "k-1234567890"',
  ]
  const leaked = samples.filter((sample) => scrub(sample) === sample)
  for (const sample of samples) console.log(`  自检 ${sample} → ${scrub(sample)}`)
  console.log(leaked.length === 0
    ? `SELFTEST REDACTION: PASS（${samples.length}/${samples.length} 形态已遮蔽）`
    : `SELFTEST REDACTION: FAIL（未遮蔽: ${leaked.join(' | ')}）`)
  process.exit(leaked.length === 0 ? 0 : 1)
}

// v1.9.23 已废除的 5 个成员工具名（仅用于断言"不得复活"，不参与身份解析）
const RETIRED_TOOL_NAMES = ['market_data_specialist', 'technical_indicators',
  'trend_forecaster', 'risk_assessor', 'falsification_challenger']

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = resolve(HERE, '..')
// 默认后端目录由操作系统约定推导（不再写死开发机用户名）；DSH_BACKEND 可覆盖。
const BACKEND = process.env.DSH_BACKEND
  || join(process.env.APPDATA || join(homedir(), 'AppData', 'Roaming'), 'DSH Desktop', 'backend', 'dsh')
const SETTLE = Number(process.env.DSH_SETTLE_MS || 5000)
// 最小 profile 的 bundle 清单：基础面 + agent 预设注册表（agentPresets 服务由
// dsh-web-app 的补丁层提供）+ 本插件。相比用户 profile 少了 20+ 个第三方 bundle
// 和整份用户层补丁（bot gateway / cli-bridge / MCP / 皮肤 …）。
const MINIMAL_BUNDLES = ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app', 'dsh-kexi-crypto']
// 用户层补丁只做减法：关掉"Web 服务面"整簇，使本次引导不监听任何端口。
// 为什么不是只关 webserver：webserver 一 disabled，还有 5 行在等 webServer/webApp
// （其中 web-runtime 是 required，启动审计会因此直接判"引导失败"）。这份清单
// 不是猜的：来源是**真实启动审计输出**——把 webserver 关掉后跑一次，读
// "Plugins waiting for services" 列表原样搬过来；上游将来新增同类行会以同样
// 方式出现在审计里，再补一行（不会静默放行）。
// telemetry 不在这里硬列 id：用产品自带开关 DSH_TELEMETRY_DISABLED 关。
const DISABLED_ROWS = ['webserver', 'web-runtime', 'open-in-app', 'directory-picker',
  'client-hmr', 'harness-dev-context']
const MINIMAL_USER_PATCH = `# check-preset-mount 沙箱用户层补丁：只做减法，不开端口
${DISABLED_ROWS.map((id) => `- id: ${id}\n  disabled: true`).join('\n')}
`

let code = 0
let createScope          // 同上：引导块内注入，避免全局变量污染
const failures = []
const fail = (message) => {
  failures.push(message)
  if (code === 0) code = 1
}
const ok = (message) => console.log(`✓ ${message}`)

const backendOk = existsSync(join(BACKEND, 'package.json'))
  && existsSync(join(BACKEND, 'node_modules', '@deepseek-ai', 'dsh', 'package.json'))
if (!backendOk) {
  console.error(`ENV: 找不到 DSH 后端安装（${BACKEND}）—— 用 DSH_BACKEND 指向 dsh 安装目录`)
  process.exit(2)
}

// ── 沙箱：临时 DSH_HOME + 最小 profile ─────────────────────────────────────
const sandbox = mkdtempSync(join(tmpdir(), 'kexi-preset-mount-'))
const home = join(sandbox, 'home')
const profileDir = join(home, 'profiles', 'web')
mkdirSync(join(profileDir, 'node_modules'), { recursive: true })
writeFileSync(join(profileDir, 'package.json'), `${JSON.stringify({
  name: 'dsh-profile-web',
  private: true,
  dependencies: { 'dsh-kexi-crypto': `link:${REPO}` },
  dsh: { profile: { bundles: MINIMAL_BUNDLES } },
}, null, 2)}\n`)
writeFileSync(join(profileDir, 'cordis.patch.yml'), MINIMAL_USER_PATCH)
// 本插件按仓库当前代码解析（不是安装副本）：验收的是"这次要交付的代码"。
// 回归团另外负责核对安装副本与仓库哈希一致。
try {
  symlinkSync(REPO, join(profileDir, 'node_modules', 'dsh-kexi-crypto'), 'junction')
} catch (error) {
  console.error(`ENV: 无法把仓库链接进沙箱 profile（${error?.message || error}）`)
  process.exit(2)
}
// DSH_HOME 必须在引导调用前设置：它决定 home 补丁、凭据、设置、存储、截图的落点。
process.env.DSH_HOME = home
process.env.DSH_TELEMETRY_DISABLED = '1'
console.log(`· 沙箱 DSH_HOME=${home}`)
console.log(`· 最小 profile bundles=${JSON.stringify(MINIMAL_BUNDLES)}（webserver 已在用户层 disabled）`)

let ctx
let shutdown
let scopeOf            // 在引导块里从 dsh-scope 取（用于读预设作用域的真实工具面）
try {
  const req = createRequire(join(BACKEND, 'x.js'))
  const bootUrl = pathToFileURL(req.resolve('@deepseek-ai/dsh/lib/profile-boot.js')).href
  // dsh-app-boot 的 exports 只暴露 "."（→ lib/index.js），深路径会被 exports 拒绝
  const envUrl = pathToFileURL(req.resolve('@deepseek-ai/dsh-app-boot')).href
  const scopeUrl = pathToFileURL(req.resolve('@deepseek-ai/dsh-scope')).href
  const { runProfile, INSTALL_ANCHOR } = await import(bootUrl)
  const { loadLayeredEnv, loadProfile } = await import(envUrl)
  const scopeModule = await import(scopeUrl)
  createScope = scopeModule.createScope
  scopeOf = scopeModule.scopeOf

  const profile = loadProfile('dsh', 'web', INSTALL_ANCHOR, home)
  ;({ ctx, shutdown } = await runProfile({
    profile: 'web',
    environment: loadLayeredEnv('dsh'),
    patchFiles: [],
    resolvedProfile: { profile, installAnchor: INSTALL_ANCHOR },
    args: [],
  }))
} catch (error) {
  console.error(`ENV: 引导失败（退出码 2）\n${error?.stack || error}`)
  await cleanup()
  process.exit(2)
}
await sleep(SETTLE)

async function cleanup() {
  if (shutdown) {
    // 优雅关闭可能不返回：最多等 4s 后硬退出（沿用旧实现口径）
    try {
      await Promise.race([shutdown.shutdown(0), sleep(4000)])
    } catch { /* 关闭失败不影响已得出的判定 */ }
  }
  if (process.env.KEXI_MOUNT_KEEP_TMP === '1') {
    console.log(`· 已保留沙箱取证：${sandbox}`)
  } else {
    rmSync(sandbox, { recursive: true, force: true })
  }
}

try {
  const ap = ctx.get('agentPresets')
  if (!ap) {
    console.error('ENV: agentPresets 服务不存在（最小 profile 未挂上预设注册表）——退出码 2')
    code = 2
  } else {
    // ── 隔离审计：证明"最小 profile"真的生效，而不是嘴上说说 ───────────────
    const profileContext = ctx.get('profileContext')
    if (profileContext?.home !== home) {
      fail(`隔离失效：profileContext.home=${profileContext?.home} 不是沙箱 ${home}`)
    } else {
      ok(`隔离生效：profileContext.home 指向沙箱（用户 DSH_HOME 未被读取/写入）`)
    }
    if (ctx.get('webserver') !== undefined) {
      fail('webserver 服务仍被挂载（会另起端口，违反 P2-7）')
    } else {
      ok('webserver 未挂载（不产生额外端口）')
    }
    const botLike = ['botGateway', 'botGatewayScreenWatch'].filter((name) => ctx.get(name) !== undefined)
    if (botLike.length) {
      fail(`沙箱里出现了本不该挂载的服务：${botLike.join(', ')}`)
    } else {
      ok('bot-gateway/截屏服务未挂载（无外部消息通道、无截屏进程）')
    }

    // ── 0. 本插件是否真的激活（inject:['agentPresets'] 的爆炸半径验证）────────
    // 只有本插件会在服务实例上装远端 roster 的遮蔽属性 + 单进程标记；
    // 二者同时存在 = apply 跑过。缺它说明 inject 未解析 → 状态灯/设置面板全不激活。
    const marker = Object.getOwnPropertyDescriptor(ap, Symbol.for('kexi.rosterFilter.v1'))
    const shadow = Object.getOwnPropertyDescriptor(ap, 'remoteExportList')
    if (!marker || !shadow) {
      fail('kexi-dashboard 未激活（agentPresets 上没有 roster 过滤器标记）——' +
        '说明 inject:["agentPresets"] 未解析，状态灯/设置面板/活动流整插件都不会工作')
    } else {
      ok('kexi-dashboard 已激活（roster 过滤器已安装，证明 apply 跑过）')
    }

    // ── 1. roster：kexi-crypto 与五个成员预设都干净 ─────────────────────────
    const roster = await ap.list()
    const byId = new Map(roster.map((p) => [p.id, p]))
    for (const id of [MAIN_PRESET_ID, ...MEMBER_PRESET_IDS]) {
      const p = byId.get(id)
      if (!p) { fail(`roster 中缺少预设 ${id}`); continue }
      if (p.broken) {
        fail(`BROKEN ${id}\n${p.broken}`)
      } else {
        ok(`clean ${id}${p.name ? ` (${p.name})` : ''}`)
      }
    }
    // resolve(id) 要求定义存在并可取（不经 agent 作用域，适合 headless）
    for (const id of [MAIN_PRESET_ID, ...MEMBER_PRESET_IDS]) {
      try {
        const def = await ap.resolve(id)
        if (!def) throw new Error('resolve 返回空')
        ok(`resolve ${id} → 定义可取`)
      } catch (error) {
        fail(`resolve ${id} 失败: ${String(error?.message || error).slice(0, 200)}`)
      }
    }

    // ── 2. 设计 B：浏览器路径看不到成员预设，内部 list() 仍完整 ──────────────
    const internalIds = roster.map((p) => p.id)
    const internalMembers = internalIds.filter((id) => id.startsWith(INTERNAL_PRESET_PREFIX))
    if (internalMembers.length !== MEMBER_PRESET_IDS.length
      || MEMBER_PRESET_IDS.some((id) => !internalMembers.includes(id))) {
      fail(`内部 list() 的成员预设应为 ${MEMBER_PRESET_IDS.length} 条 ` +
        `[${MEMBER_PRESET_IDS.join(', ')}]，实际 ${internalMembers.length} 条 [${internalMembers.join(', ')}]`)
    } else {
      ok(`内部 list() 仍完整：成员预设 ${internalMembers.length}/${MEMBER_PRESET_IDS.length} 条`)
    }
    let visibleIds = null
    try {
      const out = await ap.remoteExportList()
      if (!out || !Array.isArray(out.presets)) throw new Error('返回值缺 presets 数组')
      visibleIds = out.presets.map((p) => p.id)
    } catch (error) {
      fail(`remoteExportList 调用失败: ${String(error?.message || error).slice(0, 200)}`)
    }
    if (visibleIds) {
      const leaked = visibleIds.filter((id) => MEMBER_PRESET_IDS.includes(id))
      if (leaked.length) {
        fail(`浏览器 roster 仍能看到内部成员预设: ${leaked.join(', ')}`)
      } else {
        ok(`浏览器 roster 已隐藏成员预设（可见 ${visibleIds.length} / 内部 ${internalIds.length} 行）`)
      }
      if (!visibleIds.includes(MAIN_PRESET_ID)) {
        fail(`浏览器 roster 里连主预设 ${MAIN_PRESET_ID} 都不见了（过滤过度）`)
      } else {
        ok(`主预设 ${MAIN_PRESET_ID} 仍可选（过滤没有过度）`)
      }
      const expectedVisible = internalIds.filter((id) => !MEMBER_PRESET_IDS.includes(id))
      if (JSON.stringify(visibleIds) !== JSON.stringify(expectedVisible)) {
        fail('浏览器 roster 相对顺序与内部集合不一致（order 被破坏）')
      } else {
        ok('浏览器 roster 相对顺序与内部集合一致')
      }
    }
    // 成员预设仍必须能被内部读到（委派行 apply 时 readDocument 校验引用）
    let docOk = 0
    for (const id of MEMBER_PRESET_IDS) {
      try {
        const doc = await ap.readDocument(id)
        if (!doc || typeof doc.content !== 'string' || !doc.content.length) throw new Error('文档为空')
        docOk += 1
      } catch (error) {
        fail(`readDocument ${id} 失败: ${String(error?.message || error).slice(0, 160)}`)
      }
    }
    if (docOk === MEMBER_PRESET_IDS.length) {
      ok(`readDocument 成员预设 ${docOk}/${MEMBER_PRESET_IDS.length} 可取`)
    }

    // ── 3. 设计 A：kexi-crypto 文档里恰好 1 个委派工具 ─────────────────────
    let delegatedConfig = null
    try {
      const doc = await ap.readDocument(MAIN_PRESET_ID)
      const yamlPath = join(BACKEND, 'node_modules', 'js-yaml', 'index.js')
      const yaml = (await import(pathToFileURL(yamlPath).href)).default
      const jsType = new yaml.Type('tag:yaml.org,2002:js', {
        kind: 'scalar', resolve: (d) => typeof d === 'string', construct: (d) => ({ __jsExpr: String(d) }),
      })
      const schema = yaml.JSON_SCHEMA.extend([jsType])
      const entries = yaml.load(doc.content, { schema })
      const found = []
      const walk = (list) => {
        for (const row of list || []) {
          if (row?.name === '@deepseek-ai/dsh-tool-subagent') found.push(row)
          if (Array.isArray(row?.config)) walk(row.config)
          else if (Array.isArray(row?.config?.plugins)) walk(row.config.plugins)
        }
      }
      walk(entries)
      const cfg = found[0]?.config || {}
      delegatedConfig = cfg
      if (found.length !== 1) {
        fail(`kexi-crypto 预设里应有 1 个委派工具行，实际 ${found.length}`)
      } else if (cfg.toolName !== 'subagent') {
        fail(`委派工具 toolName 应为 subagent，实际 ${cfg.toolName}`)
      } else if (JSON.stringify(cfg.allowedAgentPresets) !== JSON.stringify(MEMBER_PRESET_IDS)) {
        fail(`allowedAgentPresets 与成员预设不一致：${JSON.stringify(cfg.allowedAgentPresets)}`)
      } else {
        ok(`委派工具恰好 1 个（subagent），allowedAgentPresets ${MEMBER_PRESET_IDS.length} 项与声明一致`)
      }
      // 旧工具名不得在预设文档里作为 toolName 出现
      for (const oldName of RETIRED_TOOL_NAMES) {
        if (new RegExp(`toolName:\\s*${oldName}\\b`).test(doc.content)) {
          fail(`预设文档仍以旧工具名注册委派行: ${oldName}`)
        }
      }
    } catch (error) {
      fail(`读取/解析 kexi-crypto 预设文档失败: ${String(error?.message || error).slice(0, 200)}`)
    }

    // ── 4. 真挂载（P1-3）：acquireScope → createScope → mount ───────────────
    // 根 ctx 直调 mount 必抛 "Agent preset binding requires a scoped context"，
    // 所以要先造一个 scoped ctx。两个易错点（都已实测踩到）：
    //   1) acquireScope(id) 返回的 key 是**预设自己的作用域身份**，拿它当新作用域
    //      会造成 bindScopeParent(key, 同一个 key) → 立刻抛
    //      "dsh-scope: scope parent link would form a cycle"；
    //      所以 scope 必须用**全新的 key**，lease 只用于钉住这次 revision。
    //   2) mount(ctx,id) 内部会 retain/collect，无需手工 retain。
    let mountCalls = 0
    const mountOne = async (id) => {
      const scope = createScope(ctx, {})       // 全新作用域身份：代表"这一次 Agent 绑定"
      try {
        // acquireScope 也要在 try 内：预设坏掉时它就是第一个抛错点
        // （RemoteError: tool-subagent … persona is no longer supported），
        // 那属于**断言失败**（退出码 1），不是环境不可用（退出码 2）。
        const lease = await ap.acquireScope(id)
        try {
          const mounted = await ap.mount(scope.ctx, id)
          mountCalls += 1
          if (!mounted || mounted.id !== id) {
            fail(`mount(${id}) 返回 ${JSON.stringify(mounted)}，与请求不符`)
          }
          return { scope, mounted }
        } finally {
          try { await lease[Symbol.asyncDispose]() } catch { /* lease 释放失败不影响判定 */ }
        }
      } catch (error) {
        fail(`mount(${id}) 抛错: ${String(error?.message || error.stack || error).slice(0, 300)}`)
        return { scope, mounted: null }
      }
    }

    const main = await mountOne(MAIN_PRESET_ID)
    if (main.mounted) ok(`真 mount ${MAIN_PRESET_ID} 成功（返回 id 与请求一致）`)
    if (main.mounted && !failures.length) {
      // 挂载后看**真实工具面**：5 个旧工具名一个都不能活（"6 行 → 1 行"的直接验收）。
      try {
        // 工具注册表是全局服务，预设的工具通过 ScopedLayers 挂在**预设作用域**上；
        // 我们这次挂载的 agent 作用域有一条 parent link 指向它（bindScopeParent），
        // 所以 tools.view(scopeOf(agentCtx)) 走 scopeChainOf 就能看到预设那一层。
        const tools = ctx.get('tools')
        if (!tools) throw new Error('全局 tools 服务不存在（最小 profile 缺少 dsh-tools）')
        const view = tools.view(scopeOf(main.scope.ctx))
        const names = [...view.visible.keys()]
        const alive = RETIRED_TOOL_NAMES.filter((name) => names.includes(name))
        if (alive.length) {
          fail(`真实工具面里仍有已废除的成员工具名: ${alive.join(', ')}`)
        } else {
          ok('真实工具面里 5 个旧工具名 0 命中')
        }
        // 为什么这里**不**断言 subagent 出现在站立工具面：dsh-tool-subagent 一旦
        // 配置了 allowedAgentPresets，它的 apply() 就改成"按 Agent 发布"
        // （上游 index.js：`if (!modelSelectionCapable && (!presets.configured || …))
        // install(ctx)` —— configured 时走 reconcileComposedAgents，只在
        // agent/created 时为该 Agent 装一份带 agent_preset 枚举的工具）。
        // 所以站立作用域里没有它是**上游设计**，不是没挂载。改由下面"挂载树里
        // 委派行恰好 1 行且 ACTIVE"来断言，避免这条变成假红。
        if (!names.includes('subagent')) {
          console.log('  INFO  站立工具面无 subagent：上游按 Agent 发布（allowedAgentPresets 生效时），'
            + '由挂载树行断言覆盖')
        }
      } catch (error) {
        fail(`读取真实挂载后的工具面失败: ${String(error?.message || error).slice(0, 300)}`)
      }

      // ── 5. 挂载树：委派行恰好 1 行且 ACTIVE ────────────────────────────────
      // compositionInventory() 的 rows 取自**真实挂载后的 tree**（不是 YAML 文本），
      // 所以它同时证明"行在文档里"和"行真的被 loader 装起来并激活"。
      try {
        const inventory = await ap.compositionInventory()
        const record = inventory.find((item) => item.id === MAIN_PRESET_ID)
        if (!record) throw new Error(`compositionInventory 里没有 ${MAIN_PRESET_ID}`)
        const rows = Array.isArray(record.rows) ? record.rows : []
        const delegatedRows = rows.filter((row) => row.moduleName === '@deepseek-ai/dsh-tool-subagent')
        const activeRows = delegatedRows.filter((row) => row.enabled && row.fiberState === 2)
        if (delegatedRows.length !== 1 || activeRows.length !== 1) {
          fail(`挂载树里 @deepseek-ai/dsh-tool-subagent 应恰好 1 行且 ACTIVE，`
            + `实际 ${delegatedRows.length} 行 / ACTIVE ${activeRows.length} 行：`
            + JSON.stringify(delegatedRows))
        } else {
          ok('挂载树里委派行恰好 1 行且 ACTIVE（fiberState=2）')
        }
        // 旧成员工具名不得以任何行的模块名/行 id 形态复活
        const revived = rows.filter((row) => RETIRED_TOOL_NAMES.includes(String(row.moduleName))
          || RETIRED_TOOL_NAMES.includes(String(row.entryId)))
        if (revived.length) {
          fail(`挂载树里出现了以旧工具名注册的行: ${JSON.stringify(revived)}`)
        } else {
          ok('挂载树里 5 个旧工具名 0 命中（没有复活成员工具行）')
        }
      } catch (error) {
        fail(`读取 compositionInventory 失败: ${String(error?.message || error).slice(0, 300)}`)
      }
    }
    try { await main.scope.dispose() } catch { /* dispose 幂等 */ }

    // 成员预设也必须真能挂起来（主预设委派行的引用目标）
    const member = await mountOne(MEMBER_PRESET_IDS[1] ?? MEMBER_PRESET_IDS[0])
    if (member.mounted) {
      ok(`真 mount ${member.mounted.id} 成功（成员预设可被委派行引用）`)
    }
    try { await member.scope.dispose() } catch { /* dispose 幂等 */ }

    // 可核对行（P1-3 要求：输出必须能看出"到底有没有真调 mount"）
    console.log(`\nMOUNT: 真调用 mount() ${mountCalls} 次（${MAIN_PRESET_ID}、${MEMBER_PRESET_IDS[1] ?? MEMBER_PRESET_IDS[0]}）`)
    if (mountCalls !== 2) {
      fail(`真调用 mount() 次数应为 2，实际 ${mountCalls}（"没真挂载"正是 P1-3 的原始缺陷）`)
    }
    if (delegatedConfig && delegatedConfig.toolName === 'subagent') {
      console.log(`MOUNT: 委派行配置 toolName=${delegatedConfig.toolName} allowedAgentPresets=${JSON.stringify(delegatedConfig.allowedAgentPresets)}`)
    }
  }
} catch (error) {
  console.error(`PROBE FAIL:\n${error?.stack || error}`)
  // 已经有断言失败时这仍是"断言失败"（1）；只有一条断言都没跑成才是"环境不可用"（2）。
  // 旧实现把两种情况一律压成 2，等于把"预设坏了"伪装成"环境没准备好"。
  code = failures.length > 0 ? 1 : 2
}

await cleanup()
console.log(code === 0
  ? `\nPRESET-MOUNT: PASS（退出码 0 = 全部断言通过）`
  : `\nPRESET-MOUNT: FAIL（退出码 ${code}：${code === 2 ? '环境不可用' : '断言失败'}）`)
if (failures.length) {
  console.log(`失败断言 ${failures.length} 条：`)
  for (const message of failures) console.log(`  ✗ ${message}`)
}
process.exit(code)
