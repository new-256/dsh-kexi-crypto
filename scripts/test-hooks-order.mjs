// 回归：React Hooks 不得出现在早退 return 之后。
// 现场事故（v1.5.5）：CEX 凭据的 5 个 hook 被插在 SettingsCard 的两个早退之后，
// 于是「点设置 → 首次渲染 cfg=null 早退(0 hook) → 二次渲染调 5 hook」→
// React 抛 "Rendered more hooks than during the previous render" →
// 整棵组件树卸载，**状态灯入口彻底消失**。
// 本检查对每个函数体做机械扫描：早退 return 与 react.use* 调用的相对位置。
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const src = readFileSync(join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/client.js'), 'utf8')
const lines = src.split('\n')

const checks = []
const offenders = []

// 找出所有形如 "function Name(...) {" 开始的顶层函数体
const fnRe = /^(\s{2,6})function\s+([A-Za-z0-9_$]+)\s*\(/gm
let m
const bodies = []
while ((m = fnRe.exec(src)) !== null) {
  const startLine = src.slice(0, m.index).split('\n').length - 1
  // 找函数体结束：以同缩进或更浅的 "}" 结束
  const indent = m[1].length
  let end = startLine + 1
  for (let i = startLine + 1; i < lines.length; i++) {
    const l = lines[i]
    const im = l.match(/^(\s*)\}/)
    if (im && im[1].length <= indent) { end = i; break }
  }
  bodies.push({ name: m[2], start: startLine, end })
}

for (const b of bodies) {
  const body = lines.slice(b.start, b.end + 1)
  // 只认**函数体顶层**（相对深度 1）的早退 return。
  // 早期版本用纯正则，会把嵌套回调里的 `if (!x) return Y;`
  // （例如 getSnapshot 内部的守卫）当成函数早退 → 大量误报，真问题会被淹没。
  // 做法：逐字符累计 {} 深度，记录每行结束时的深度。
  const depthOfLine = []
  let d = 0
  for (const l of body) {
    // 去掉字符串/注释里的括号，避免注释中的 { } 干扰计数
    const clean = l.replace(/"(?:[^"\\]|\\.)*"/g, '""').replace(/'(?:[^'\\]|\\.)*'/g, "''")
      .replace(/\/\/.*$/, '').replace(/\/\*.*?\*\//g, '')
    for (const ch of clean) { if (ch === '{') d++; else if (ch === '}') d--; }
    depthOfLine.push(d)
  }
  const baseDepth = depthOfLine[0] || 1   // 函数签名行结束时的深度
  let earlyReturnIdx = -1
  for (let i = 0; i < body.length; i++) {
    const l = body[i]
    const isTopLevel = depthOfLine[i] === baseDepth
    if (isTopLevel && /^\s*if\s*\(.*\)\s*(return|react\.createElement)/.test(l)) {
      if (earlyReturnIdx < 0) earlyReturnIdx = i
    }
  }
  if (earlyReturnIdx < 0) continue
  for (let i = earlyReturnIdx + 1; i < body.length; i++) {
    if (/react\.use(State|Effect|Ref|Memo|Callback|Reducer|Context)\s*\(/.test(body[i])) {
      offenders.push(`${b.name}() 第 ${b.start + i + 1} 行在顶层早退之后调用 hook: ${body[i].trim().slice(0, 60)}`)
    }
  }
}

checks.push(['无 hook 出现在早退 return 之后', offenders.length === 0,
  offenders.length ? offenders.join(' | ') : ''])

// 结构断言：SettingsCard 的 CEX hook 必须在两个早退之前
const sc = bodies.find((b) => b.name === 'SettingsCard')
if (sc) {
  const bl = lines.slice(sc.start, sc.end + 1)
  const idxEarly = bl.findIndex((l) => /^\s*if\s*\(!cfg\)\s*return/.test(l))
  const idxCex = bl.findIndex((l) => /const CEX = \[/.test(l))
  checks.push(['SettingsCard 里 CEX hook 块存在', idxCex >= 0, `line ${sc.start + idxCex + 1}`])
  checks.push(['CEX hook 块在早退之前（Hooks 顺序稳定）', idxCex >= 0 && idxEarly >= 0 && idxCex < idxEarly,
    idxEarly >= 0 ? `CEX@${idxCex + 1} earlyReturn@${idxEarly + 1}` : '未找到早退'])
  // 该函数内 hook 总数应在早退之前全部出现
  const before = idxEarly >= 0 ? bl.slice(0, idxEarly).join('\n') : ''
  const hookCount = (before.match(/react\.use(State|Effect|Ref|Memo|Callback)/g) || []).length
  checks.push([`早退前已声明全部 ${hookCount} 个 hook`, hookCount >= 9, String(hookCount)])
} else {
  checks.push(['找到 SettingsCard', false, ''])
}

let all = true
for (const [n, ok, e] of checks) {
  console.log((ok ? '  ✓ ' : '  ✗ ') + n + (e && !ok ? '  → ' + e : ''))
  if (!ok) all = false
}
if (offenders.length) {
  console.log('\n违规明细:')
  for (const o of offenders) console.log('  - ' + o)
}
console.log(all ? '\n✅ Hooks 顺序回归通过' : '\n❌ 存在 hook-after-early-return 违规')
process.exit(all ? 0 : 1)
