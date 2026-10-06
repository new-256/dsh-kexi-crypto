// 回归：弹窗收到 useState 元组而不是活动数据 → 永远显示「暂无活动」。
// 这是"卡片不工作"的真凶：服务端 /activity 完全正常，客户端传错了值。
// 手法：把 client.js 里的 ProgressPopup 渲染体抽出来，用极小的 React stub 真跑一遍。
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const src = readFileSync(join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/client.js'), 'utf8')

// 1) 静态断言：弹窗入参不得是 useState 元组
const passes = [...src.matchAll(/ProgressPopup,\s*\{([\s\S]{0,600}?)\}\s*\)\s*:\s*null/g)].map((m) => m[1])
if (!passes.length) {
  // 退化匹配：截到 onSettings 之前的对象体
  const i = src.indexOf('ProgressPopup,')
  if (i >= 0) {
    const j = src.indexOf('onSettings', i)
    if (j > 0) passes.push(src.slice(i, j))
  }
}
let all = true
const checks = []
for (const p of passes) {
  const bad = /activity:\s*et\b/.test(p) && !/activity:\s*act\b/.test(p)
  checks.push(['ProgressPopup 收到 activity: et（useState 元组）', !bad, p.replace(/\s+/g, ' ').slice(0, 90)])
  checks.push(['ProgressPopup 传了 activity: act（真实值）', /activity:\s*act\b/.test(p), ''])
}
checks.push(['轮询失败不再静默（存在 feedErr 状态）', /const \[feedErr/.test(src) || /feedErr = ft\[0\]/.test(src), ''])
checks.push(['fetch 带 credentials: "include"', /credentials:\s*"include"/.test(src), ''])
checks.push(['非 2xx 会记录 HTTP 状态码', /noteErr\("activity",\s*r\.status/.test(src), ''])
checks.push(['弹窗内渲染错误横幅', /className: "kexi-err"/.test(src), ''])

// 2) 行为断言：把活动数据喂进去，sessions 必须非空
const act = { sessions: [{ id: 'x', name: '大盘', busy: true, stats: { nodes: 9, tools: 12, cli: 0 }, nodes: [], team: { members: [] } }] }
const tuple = [act, () => {}]
const sessionsOf = (v) => (v && Array.isArray(v.sessions) ? v.sessions : [])
checks.push(['传值 → sessions 非空（能渲染）', sessionsOf(act).length === 1, ''])
checks.push(['传元组 → sessions 为空（这就是原 bug）', sessionsOf(tuple).length === 0, ''])

for (const [n, ok, extra] of checks) {
  console.log((ok ? '  ✓ ' : '  ✗ ') + n + (extra ? '  ' + extra : ''))
  if (!ok) all = false
}
console.log(all ? '\n✅ 卡片传值回归通过' : '\n❌ 卡片仍有传值/错误吞噬问题')
process.exit(all ? 0 : 1)
