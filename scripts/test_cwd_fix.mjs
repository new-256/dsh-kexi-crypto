// 真实 spawn 验证 kexi_run 的 cwd 修复（v1.5.2）
// 复现线上故障：mode=script + 相对路径 --out kexi_out/xxx.json
import { pathToFileURL } from 'node:url'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdirSync, existsSync, rmSync, readdirSync } from 'node:fs'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const TMP = join(ROOT, '.kexi-test-tmp')
if (existsSync(TMP)) rmSync(TMP, { recursive: true, force: true })
const SETTINGS = join(TMP, 'kexi-settings.json')
process.env.KEXI_SETTINGS_FILE = SETTINGS
process.env.DSH_HOME = TMP
process.env.KEXI_PYTHON = 'C:/Python313/python.exe'

const WS = join(TMP, 'cwd-demo')
mkdirSync(join(WS, 'kexi_out'), { recursive: true })

const spawns = []
// 假 spawn：记录 argv/cwd，模拟脚本按 cwd 解析相对路径
const fakeSub = {
  spawn: ({ argv, cwd }) => {
    spawns.push({ argv, cwd })
    const out = { ok: true, out: 'kexi_out/x.json' }
    return {
      done: Promise.resolve({ exitCode: 0 }),
      collected: {
        stdout: { readFrom: () => ({ text: JSON.stringify(out) }) },
        stderr: { readFrom: () => ({ text: '' }) },
      },
    }
  },
}

const tools = new Map()
const plugin = await import(pathToFileURL(join(ROOT, 'preset/kexi-crypto/kexi-plugin.mjs')).href + '?t=cwd')
plugin.apply({
  get: (n) => (n === 'sandboxPolicy' ? { workspaceRoot: WS } : undefined),
  tools: { register: (t) => { tools.set(t.name, t); return () => tools.delete(t.name) }, get: (n) => tools.get(n) },
  systemPrompt: { section: () => () => { }, getSectionOrder: () => 2810 },
  subprocess: fakeSub,
  interval: () => () => { },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } },
  on: () => () => { }, emit: () => { },
})

const exec = { agent: { id: 'lead', name: 'lead', session: { id: 'sess-cwd', header: { cwd: WS } } }, signal: undefined }

// 关键用例：相对路径 --out（线上正是这个写法导致 FileNotFoundError）
const r = await tools.get('kexi_run').execute({
  mode: 'script', script: 'news_watch.py', args: ['--once', '--out-dir', 'kexi_out/cwdtest'],
}, exec)

console.log('ok =', r.ok, '| error =', String(r.error || '').slice(0, 120))
const sp = spawns[0] || {}
const scriptsDir = join(ROOT, 'preset/kexi-crypto/skills/crypto-market-analysis/scripts')
const checks = [
  ['cwd = 会话工作区（不是脚本目录）', sp.cwd === WS, `cwd=${sp.cwd}`],
  ['脚本用绝对路径调用', String(sp.argv && sp.argv[1] || '').startsWith(scriptsDir), `argv1=${sp.argv && sp.argv[1]}`],
  ['相对 --out-dir 已绝对化到工作区', String((sp.argv || []).join(' ')).includes(join(WS, 'kexi_out/cwdtest')), (sp.argv || []).join(' ')],
]
let allOk = r.ok
for (const [name, pass, info] of checks) {
  console.log((pass ? '  ✓ ' : '  ✗ ') + name + '  [' + info + ']')
  if (!pass) allOk = false
}
console.log(allOk ? '\n✅ CWD 修复生效' : '\n❌ 仍未修复')
