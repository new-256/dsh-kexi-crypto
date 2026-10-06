#!/usr/bin/env node
// 运行期验证：成果登记。用一个复刻"实测故障现场"的临时目录跑真 host。
//
// 现场（C:\Users\lcl\Desktop\K析工作文件夹）还原为：
//   kexi_out/market_report.html   ← 本次会话产出（文件名不含 dashboard）
//   kexi_out/old_dashboard.html   ← 上一轮的旧看板
//   kexi_out/latest.json          ← 指向旧看板（权威标记，但已陈旧）
//
// 旧代码：文件名过滤只认 dashboard/看板 → market_report.html 被漏
//         → latest.json 指向旧的被 freshness 拒绝
//         → 「最终成果」永远空着
import { pathToFileURL } from 'node:url'
import { mkdtempSync, mkdirSync, writeFileSync, utimesSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const HOST = join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/index.mjs')
const cwd = mkdtempSync(join(tmpdir(), 'kexi-art-'))
const outDir = join(cwd, 'kexi_out')
mkdirSync(outDir, { recursive: true })

// 旧看板：2 小时前
const oldH = join(outDir, 'old_dashboard.html')
writeFileSync(oldH, '<html><body>上一轮</body></html>', 'utf8')
const t2h = new Date(Date.now() - 2 * 3600 * 1000)
utimesSync(oldH, t2h, t2h)
// latest.json 指向旧看板（陈旧的权威标记）
writeFileSync(join(outDir, 'latest.json'), JSON.stringify({
  kind: 'dashboard', schema: 'kexi.latest/1', dashboard: oldH,
}), 'utf8')
// 本次产出：刚刚，文件名**不含 dashboard**（旧代码必然漏掉）
const fresh = join(outDir, 'market_report.html')
writeFileSync(fresh, '<html><body>本轮研判结论</body></html>', 'utf8')

const routes = new Map(), handlers = new Map()
const ws = { register: (r) => { routes.set(r.path, r); return () => routes.delete(r.path) } }
const ctx = {
  on(n, f) { if (!handlers.has(n)) handlers.set(n, []); handlers.get(n).push(f); return () => { } },
  inject(_d, cb) { cb({ get: (n) => (n === 'webServer' ? ws : undefined), effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } } }) },
  effect(fn) { const d = fn(); return typeof d === 'function' ? d : () => { } },
}
process.env.DSH_HOME = cwd
process.env.KEXI_SETTINGS_FILE = join(cwd, 'kexi-settings.json')
const { apply } = await import(pathToFileURL(HOST).href)
apply(ctx)
const emit = (ev, ...a) => { for (const f of (handlers.get(ev) || [])) f(...a) }
const call = (p) => new Promise((res) => {
  const req = { method: 'GET', url: p, on() { }, destroy() { } }
  let a = ''
  const r = { writeHead(c) { this._c = c }, end(s) { a += s || ''; let j = null; try { j = JSON.parse(a) } catch { }; res(j) } }
  routes.get(p).handler(req, r)
})

// 建桶：一个 kexi 工具调用就够（旧代码靠 turn 事件建桶，这里用 turn 更贴近真实）
const sess = { id: 'sess-art', cwd }
emit('session/event', sess, { type: 'turn/start' })
emit('session/event', sess, {
  type: 'tool/call',
  data: { callId: 'c1', name: 'kexi_run', arguments: { mode: 'script', script: 'dashboard.py' } },
})

let pass = 0, fail = 0
const ok = (c, m) => { if (c) { pass++; console.log('  ✓ ' + m) } else { fail++; console.log('  ✗ FAIL: ' + m) } }

const r1 = await call('/kexi-dashboard/activity')
const s1 = (r1.sessions || [])[0]
ok(!!s1, '会话出现在 /activity')
ok(s1 && s1.artifact != null, '自定义名 market_report.html 被登记为成果（旧代码此处为空）')
ok(s1 && s1.artifact && /market_report\.html$/.test(s1.artifact.name),
   `登记的是本轮看板而非旧看板（实得 ${s1 && s1.artifact ? s1.artifact.name : 'null'}）`)
ok(s1 && s1.artifact && /id=/.test(s1.artifact.url), 'artifact 带可内嵌的 url')

// 反向：绝不能把上一轮的旧看板当成成果
ok(s1 && s1.artifact && !/old_dashboard/.test(s1.artifact.name), '没有误挂上一轮的旧看板')

// 陈旧权威标记不得绕过 freshness
ok(!(s1 && s1.artifact && /old_dashboard/.test(s1.artifact.name)),
   'latest.json 指向陈旧看板时被 freshness 拒绝（没有 latest 优先的短路漏洞）')

console.log('\n  version =', r1.version)
console.log(`\ntest-artifact-runtime: ${pass} 通过 / ${fail} 失败`)
process.exit(fail ? 1 : 0)
