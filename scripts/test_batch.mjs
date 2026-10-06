// 端到端验证 v1.5.2 批量模式 + files 清单（真 Python 执行）
import { pathToFileURL } from 'node:url'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdirSync, writeFileSync, existsSync, rmSync, readdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const TMP = join(ROOT, '.kexi-test-tmp')
if (existsSync(TMP)) rmSync(TMP, { recursive: true, force: true })
const WS = join(TMP, 'batch-ws')
mkdirSync(join(WS, 'kexi_out'), { recursive: true })

// 造 5 份 K 线（3 份正常，1 份停更，1 份样本不足）
function mkKline(path, bars, lastTs) {
  const k = []
  for (let i = 0; i < bars; i++) {
    const c = 100 + i * 0.5
    k.push([lastTs - (bars - i) * 86400000, c * 0.99, c * 1.02, c * 0.98, c, 1000 + i])
  }
  writeFileSync(path, JSON.stringify({ symbol: 'TEST', interval: '1d', klines: k }))
}
const now = Date.now()
;['AAA', 'BBB', 'CCC'].forEach((s) => mkKline(join(WS, 'kexi_out', `${s}_1d_klines.json`), 200, now))
mkKline(join(WS, 'kexi_out', 'OLD_1d_klines.json'), 200, now - 400 * 86400000)  // 停更 400 天
mkKline(join(WS, 'kexi_out', 'SHORT_1d_klines.json'), 40, now)                    // 样本不足

const realSub = {
  spawn: ({ argv, cwd }) => {
    const p = spawnSync(argv[0], argv.slice(1), { cwd, encoding: 'utf8', timeout: 60000 })
    return {
      done: Promise.resolve({ exitCode: p.status === null ? 1 : p.status }),
      collected: {
        stdout: { readFrom: () => ({ text: p.stdout || '' }) },
        stderr: { readFrom: () => ({ text: p.stderr || '' }) },
      },
    }
  },
}

process.env.KEXI_PYTHON = 'C:\\Python313\\python.exe'
const tools = new Map()
const plugin = await import(pathToFileURL(join(ROOT, 'preset/kexi-crypto/kexi-plugin.mjs')).href + '?t=batch')
plugin.apply({
  get: (n) => (n === 'sandboxPolicy' ? { workspaceRoot: WS } : undefined),
  tools: { register: (t) => { tools.set(t.name, t); return () => tools.delete(t.name) }, get: (n) => tools.get(n) },
  systemPrompt: { section: () => () => { }, getSectionOrder: () => 2810 },
  subprocess: realSub,
  interval: () => () => { },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } },
  on: () => () => { }, emit: () => { },
})
const exec = { agent: { id: 'lead', name: 'lead', session: { id: 's', header: { cwd: WS } } }, signal: undefined }

// ① 批量：跑 indicators.py × 5（含 1 停更 + 1 样本不足，应部分失败）
const r = await tools.get('kexi_run').execute({
  mode: 'script', script: 'indicators.py',
  batch: {
    items: ['AAA', 'BBB', 'CCC', 'OLD', 'SHORT'],
    args: ['--input', 'kexi_out/{item}_1d_klines.json', '--out', 'kexi_out/{item}_1d_indicators.json'],
    concurrency: 3,
  },
}, exec)

console.log('=== 批量结果 ===')
console.log('ok =', r.ok)
console.log(r.digest)
console.log('out_files =', (r.out_files || []).length, '个')
console.log('next =', r.next)

const produced = readdirSync(join(WS, 'kexi_out')).filter((f) => f.endsWith('_1d_indicators.json'))
console.log('\n实际产出的 indicators =', produced.sort().join(', '))

// ② mode=files：本轮/历史分组
const f = await tools.get('kexi_run').execute({ mode: 'files' }, exec)
console.log('\n=== mode=files ===')
console.log(f.digest)

// 断言
const checks = [
  ['批量部分失败仍 ok=true', r.ok === true],
  ['5 项全部产出（脚本不因脏数据失败）', produced.length === 5],
  ['脏数据被闸门标出（停更 401 天 / 样本 40 根）', /停更约 40\d 天/.test(r.digest) && /样本仅 40 根/.test(r.digest)],
  ['next 明确要求剔除脏数据', /剔除/.test(String(r.next))],
  ['逐项 out 路径被收集', (r.out_files || []).length === 5],
  ['files 模式标出【本轮】', /\[本轮\]/.test(f.digest)],
  ['files 模式有历史分组说明', /历史/.test(f.digest)],
]
let all = true
for (const [n, ok] of checks) { console.log((ok ? '  ✓ ' : '  ✗ ') + n); if (!ok) all = false }
console.log(all ? '\n✅ 批量 + files 清单 端到端通过' : '\n❌ 有问题')
rmSync(TMP, { recursive: true, force: true })
