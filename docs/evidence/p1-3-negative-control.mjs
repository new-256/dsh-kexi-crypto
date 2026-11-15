#!/usr/bin/env node
// docs/evidence/p1-3-negative-control.mjs — P1-3 / P2-7 负对照（体检 2026-10-30）
//
// 要证明：check-preset-mount.mjs 的绿灯不是"永远绿"。做法是构造**影子仓库**
// （把仓库复制到临时目录，只变异一处），再跑同一份验收脚本，要求它红在**预期的
// 那一行**上。变异点全部取自真实历史事故形态，不是随便改坏：
//   A. 旧工具名复活（把 6 行委派工具的旧形态塞回来）
//   B. roster 过滤器未安装（模拟 v1.9.22：浏览器能看到 kexi-member-*）
//   C. 成员/主预设挂载失败（把 v1.9.21 的真实事故形态塞回：tool-subagent 带 persona）
//   D. 脱敏过滤器失效（清空 REDACTIONS → 自检必须 FAIL）
//   E. 正对照 + P2-7 隔离审计（webserver/bot/用户 home 三行必须在场）
//
// 为什么用影子仓库而不是改真仓库：并行团队正在改同一批文件，且真仓库跑一次
// 会拉起真实引导。影子仓库让"注入缺陷"与"验证"完全隔离，可反复执行。
//
// 用法：node docs/evidence/p1-3-negative-control.mjs
// 退出码：0 = 全部负对照符合预期；1 = 有负对照不符合预期。
import { execFileSync, spawnSync } from 'node:child_process'
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = resolve(HERE, '..', '..')
const SCRIPT = join('scripts', 'check-preset-mount.mjs')
const PATCH = join('home-plugin', 'dsh-kexi-crypto', 'cordis.patch.yml')
const LIB = join('home-plugin', 'dsh-kexi-crypto', 'lib', 'index.mjs')
const RUN_TIMEOUT = 180_000

const results = []
const expect = (label, ok, detail = '') => {
  results.push({ label, ok: Boolean(ok), detail })
  console.log(`  ${ok ? '✓' : '✗'}  ${label}`)
  if (!ok) console.log(`       → ${String(detail).split('\n').join('\n         ')}`)
}

/** 影子仓库：复制必要目录，返回可执行的验收脚本路径 */
function shadowRepo(tag) {
  const root = mkdtempSync(join(tmpdir(), `kexi-p13-${tag}-`))
  for (const part of ['package.json', 'home-plugin', 'preset']) {
    cpSync(join(REPO, part), join(root, part), { recursive: true })
  }
  cpSync(join(REPO, SCRIPT), join(root, SCRIPT))
  return root
}

/** 精确单点替换：替换次数不是 1 就直接失败（防止"变异没生效 → 假绿"） */
function mutate(file, from, to) {
  const body = readFileSync(file, 'utf8')
  const hits = body.split(from).length - 1
  if (hits !== 1) {
    throw new Error(`${file} 里待变异片段出现 ${hits} 次（期望 1）：${from.slice(0, 60)}`)
  }
  writeFileSync(file, body.replace(from, to))
}

function runMount(root, env = {}) {
  const script = join(root, SCRIPT)
  const r = spawnSync(process.execPath, [script], {
    cwd: root,
    timeout: RUN_TIMEOUT,
    encoding: 'utf8',
    env: { ...process.env, DSH_SETTLE_MS: '4000', ...env },
  })
  return { code: r.status, out: `${r.stdout || ''}${r.stderr || ''}` }
}

// ── 正对照：真仓库的验收脚本必须是绿的，且带着 P2-7 的三行隔离审计 ──────────
console.log('\n── E. 正对照：真仓库 + P2-7 隔离审计 ──────────────────────────')
const base = runMount(REPO)
console.log(base.out.trim().split('\n').slice(-6).join('\n'))
const isolation = [
  '隔离生效：profileContext.home 指向沙箱',
  'webserver 未挂载（不产生额外端口）',
  'bot-gateway/截屏服务未挂载（无外部消息通道、无截屏进程）',
]
expect('正对照：PRESET-MOUNT PASS 且退出码 0',
  base.code === 0 && base.out.includes('PRESET-MOUNT: PASS'),
  `exit=${base.code}\n${base.out.slice(-600)}`)
expect('P2-7 隔离审计三行在场（隔离 home / 无端口 / 无 bot 与截屏）',
  isolation.every((line) => base.out.includes(line)),
  isolation.filter((line) => !base.out.includes(line)).join('\n') || base.out.slice(-600))
expect('P1-3 可核对行：真调用 mount() 2 次',
  base.out.includes('MOUNT: 真调用 mount() 2 次'),
  base.out.split('\n').filter((l) => l.includes('MOUNT:')).join('\n'))

// ── A. 旧工具名复活（把 6 行委派工具的旧形态塞回来）─────────────────────────
console.log('\n── A. 变异：复活旧委派工具行（market_data_specialist）────────────')
{
  const root = shadowRepo('a')
  const patch = join(root, PATCH)
  // 必须注入到**主预设的 plugins 列表**里（delegation 组的配置列表）：
  // readDocument('kexi-crypto') 读的是该预设自己的文档，追加到整份 bundle patch
  // 文件末尾是看不见的（第一次变异就踩到这个坑，绿灯说明变异没生效而不是脚本坏）。
  // 锚点取主预设 allowedAgentPresets 的最后一项（该串在文件里唯一）。
  mutate(patch, '                    - kexi-member-falsifier\n',
    '                    - kexi-member-falsifier\n'
    + '              # 负对照注入：旧工具名复活\n'
    + "              - id: tool-market-data-specialist\n"
    + "                name: '@deepseek-ai/dsh-tool-subagent'\n"
    + '                config:\n'
    + '                  provider: spawn\n'
    + '                  toolName: market_data_specialist\n')
  const r = runMount(root)
  console.log(r.out.trim().split('\n').slice(-8).join('\n'))
  expect('旧工具名复活 → 断言失败（委派行数≠1 且旧 toolName 命中）',
    r.code === 1 && r.out.includes('应有 1 个委派工具行') && r.out.includes('market_data_specialist'),
    `exit=${r.code}\n${r.out.slice(-700)}`)
  rmSync(root, { recursive: true, force: true })
}

// ── B. roster 过滤器未安装（模拟 v1.9.22：浏览器能看到 kexi-member-*）────────
console.log('\n── B. 变异：注释掉 roster 过滤器安装（模拟 v1.9.22）──────────────')
{
  const root = shadowRepo('b')
  const lib = join(root, LIB)
  mutate(lib, 'ctx.effect(() => installPresetRosterFilter(presetsSvc))',
    '// 负对照注入：故意不装过滤器（v1.9.22 行为）')
  const r = runMount(root)
  console.log(r.out.trim().split('\n').slice(-10).join('\n'))
  expect('过滤器缺失 → 断言失败（浏览器 roster 泄露 + 未激活标记）',
    r.code === 1
    && r.out.includes('浏览器 roster 仍能看到内部成员预设')
    && r.out.includes('kexi-dashboard 未激活'),
    `exit=${r.code}\n${r.out.slice(-800)}`)
  rmSync(root, { recursive: true, force: true })
}

// ── C. 预设挂载失败（v1.9.21 真实事故：tool-subagent 带 persona）─────────────
console.log('\n── C. 变异：给委派行塞回 persona（v1.9.21 真实事故形态）──────────')
{
  const root = shadowRepo('c')
  const patch = join(root, PATCH)
  mutate(patch, `                  toolName: subagent`,
    `                  toolName: subagent\n                  persona: 负对照注入的人员设定`)
  const r = runMount(root)
  console.log(r.out.trim().split('\n').filter((l) => l.includes('BROKEN') || l.includes('mount') || l.includes('PRESET-MOUNT')).slice(0, 10).join('\n'))
  expect('预设坏了 → 断言失败（BROKEN kexi-crypto 且挂载抛错，退出码 1 不是 2）',
    r.code === 1 && r.out.includes('BROKEN kexi-crypto')
    && /mount\(kexi-crypto\) 抛错/.test(r.out),
    `exit=${r.code}\n${r.out.slice(-800)}`)
  rmSync(root, { recursive: true, force: true })
}

// ── D. 脱敏过滤器失效 ─────────────────────────────────────────────────────
console.log('\n── D. 变异：清空 REDACTIONS（脱敏自检必须失败）──────────────────')
{
  const selfTest = runMount(REPO, { KEXI_SELFTEST_REDACTION: '1' })
  console.log(selfTest.out.trim())
  expect('未变异：脱敏自检 PASS（4/4 形态遮蔽）且退出码 0',
    selfTest.code === 0 && selfTest.out.includes('SELFTEST REDACTION: PASS'),
    `exit=${selfTest.code}\n${selfTest.out}`)

  const root = shadowRepo('d')
  const scriptPath = join(root, SCRIPT)
  const body = readFileSync(scriptPath, 'utf8')
  const start = body.indexOf('const REDACTIONS = [')
  const end = body.indexOf('\n]\n', start)
  if (start < 0 || end < 0) throw new Error('找不到 REDACTIONS 数组边界，无法构造变异')
  writeFileSync(scriptPath, `${body.slice(0, start)}const REDACTIONS = []\n${body.slice(end + 3)}`)
  const mutated = runMount(root, { KEXI_SELFTEST_REDACTION: '1' })
  console.log(mutated.out.trim())
  expect('清空 REDACTIONS → 自检 FAIL 且退出码 1（脱敏不是纸面承诺）',
    mutated.code === 1 && mutated.out.includes('SELFTEST REDACTION: FAIL'),
    `exit=${mutated.code}\n${mutated.out}`)
  rmSync(root, { recursive: true, force: true })
}

// ── 汇总 ──────────────────────────────────────────────────────────────────
const bad = results.filter((r) => !r.ok)
console.log('\n' + '='.repeat(72))
console.log(`P1-3/P2-7 负对照：${results.length - bad.length}/${results.length} 符合预期`)
console.log('='.repeat(72))
for (const r of results) {
  console.log(`  ${r.ok ? '✓' : '✗'} ${r.label}`)
  if (!r.ok) console.log(`      → ${String(r.detail).split('\n').join('\n        ')}`)
}
process.exit(bad.length ? 1 : 0)
