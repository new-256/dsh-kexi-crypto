// 回归：mode=files 把本轮最早产物误判为【历史】
// 复现线上：数脉 00:32:03 拉 K 线，news_watch 00:32:37 才写；
// 旧实现在首次 mode=files 时用"最新文件 mtime"当起点 → K 线被判成历史。
import { pathToFileURL } from 'node:url'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdirSync, writeFileSync, existsSync, rmSync, utimesSync } from 'node:fs'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const TMP = join(ROOT, '.kexi-test-tmp')
if (existsSync(TMP)) rmSync(TMP, { recursive: true, force: true })
const WS = join(TMP, 'run-ws')
mkdirSync(join(WS, 'kexi_out'), { recursive: true })
const KOUT = join(WS, 'kexi_out')

// 造历史遗留（6 小时前，上一轮）
const old = new Date(Date.now() - 6 * 3600 * 1000)
writeFileSync(join(KOUT, 'zhibei_final20.json'), '{}')
writeFileSync(join(KOUT, 'bull20_dashboard.html'), '<html></html>')
utimesSync(join(KOUT, 'zhibei_final20.json'), old, old)
utimesSync(join(KOUT, 'bull20_dashboard.html'), old, old)

const tools = new Map()
const plugin = await import(pathToFileURL(join(ROOT, 'preset/kexi-crypto/kexi-plugin.mjs')).href + '?t=rm')
plugin.apply({
  get: (n) => (n === 'sandboxPolicy' ? { workspaceRoot: WS } : undefined),
  tools: { register: (t) => { tools.set(t.name, t); return () => tools.delete(t.name) }, get: (n) => tools.get(n) },
  systemPrompt: { section: () => () => { }, getSectionOrder: () => 2810 },
  subprocess: { spawn: () => ({ done: Promise.resolve({ exitCode: 0 }), collected: { stdout: { readFrom: () => ({ text: '{"ok":true}' }) }, stderr: { readFrom: () => ({ text: '' }) } } }) },
  interval: () => () => { },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } },
  on: () => () => { }, emit: () => { },
})
const exec = { agent: { id: 'lead', name: 'lead', session: { id: 's', header: { cwd: WS } } }, signal: undefined }

// ① 第一次 kexi_run：建标记（模拟成员先跑脚本）
await tools.get('kexi_run').execute({ mode: 'script', script: 'news_watch.py', args: ['--once', '--no-notify'] }, exec)
// ② 之后才有产物写入（本轮的 K 线，比标记晚）
writeFileSync(join(KOUT, 'PYTHUSDT_1d_klines.json'), '{"symbol":"PYTHUSDT"}')
// ③ 再来一个更晚的产物
writeFileSync(join(KOUT, 'news_watch.json'), '{}')

const r = await tools.get('kexi_run').execute({ mode: 'files' }, exec)
console.log(r.digest)

const checks = [
  ['本轮 K 线被判为【本轮】', /\[本轮\]\s+PYTHUSDT_1d_klines\.json/.test(r.digest)],
  ['上一轮产物被判为【历史】', /\[历史\]\s+zhibei_final20\.json/.test(r.digest)],
  ['上一轮看板被判为【历史】', /\[历史\]\s+bull20_dashboard\.html/.test(r.digest)],
]
let all = true
for (const [n, ok] of checks) { console.log((ok ? '  ✓ ' : '  ✗ ') + n); if (!ok) all = false }
console.log(all ? '\n✅ run 标记时序修复确认' : '\n❌ 仍有问题')
rmSync(TMP, { recursive: true, force: true })
