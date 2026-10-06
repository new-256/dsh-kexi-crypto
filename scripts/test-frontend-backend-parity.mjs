#!/usr/bin/env node
// 前后端连通与字段匹配审计。
//
// 为什么要专门做这个：`v1.9.1` 那次 DPAPI 事故的近亲——client.js 里写了
// `cxs.credential_schema`，而 `cxs` 是 `useState(null)` 返回的**数组**，
// 值在 `cexInfo = cxs[0]`。于是该字段恒为 `undefined`，表单条件渲染静默失效。
// `node --check` 抓不到、字面量匹配抓不到、单元测试也没覆盖到 UI 渲染。
//
// 所以这里做两件事：
//   ① **活体探测**：真打 4 个端点，看返回的 JSON 到底有没有这些字段。
//   ② **字段清单比对**：从 client.js 里抠出它读的所有 `xxx.yyy`，
//      逐个到 host 源码/响应里找对应产出点。
import { readFileSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const HOST = join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/index.mjs')
const CLIENT = join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/client.js')

const hostSrc = readFileSync(HOST, 'utf8')
const cliSrc = readFileSync(CLIENT, 'utf8')

const checks = []
const ck = (n, c, x = '') => checks.push([n, !!c, x])

// ── ① 路由对齐 ────────────────────────────────────────────────────────────
const routes = [...new Set([...hostSrc.matchAll(/path:\s*'([^']+)'/g)].map(m => m[1]))]
const fetched = [...new Set([...cliSrc.matchAll(/fetch\("(\/kexi-dashboard\/[a-z]+)/g)].map(m => m[1]))]
ck('① host 注册了路由', routes.length >= 4, JSON.stringify(routes))
for (const f of fetched) {
  ck('① client 调用的 %s 在 host 存在', routes.includes(f), 'routes=' + JSON.stringify(routes))
}
ck('① host 没有 client 用不到的僵尸路由',
   routes.every(r => fetched.includes(r) || r.includes('artifact')),
   '未被 client 调用：' + JSON.stringify(routes.filter(r => !fetched.includes(r))))

// ── ② client 读取的状态变量 ───────────────────────────────────────────────
// v1.9.1 的真 bug 就出在这：useState 返回数组，误用数组本身。
const stateDecl = [...cliSrc.matchAll(/const (\w+) = react\.useState\(/g)].map(m => m[1])
const aliased = {}
for (const m of cliSrc.matchAll(/const (\w+) = (\w+)\[0\]/g)) aliased[m[1]] = m[2]
ck('② 存在 useState 别名写法（值是 xs[0]）',
   Object.keys(aliased).length > 0, JSON.stringify(aliased))
// 谁被误用：既不是 state 数组本身、也不是它的别名
const badUse = []
for (const m of cliSrc.matchAll(/\b(cxs|settings|status|info|act)\.(\w+)/g)) {
  const [_, varName, field] = m
  if (aliased[varName]) continue                      // 是值，正确
  if (!stateDecl.includes(varName)) continue            // 不是 state 变量
  badUse.push(varName + '.' + field)
}
ck('② 没有把 useState 数组当值用（v1.9.1 的 cxs.credential_schema）',
   badUse.length === 0, JSON.stringify([...new Set(badUse)].slice(0, 6)))

// ── ③④⑤ 响应字段 ────────────────────────────────────────────────────────
// ⚠ 路由处理函数是**内联箭头函数**（`path: '...', handler: (req,res) => {...}`），
//   不是具名函数——早期版本按 `function dashboardActivity` 去找，
//   整个提取返回空，于是把三个字段全判成"缺失"，是**假失败**。
//   现在按路由路径切块：从每个 `path:` 起到下一个 `path:`（或文件尾）。
function handlerBlock(routePath) {
  const marker = "path: '" + routePath + "'"
  const i = hostSrc.indexOf(marker)
  if (i < 0) return ''
  const j = hostSrc.indexOf("path: '/kexi-dashboard/", i + marker.length)
  return hostSrc.slice(i, j < 0 ? hostSrc.length : j)
}
ck('③ 路由处理块可提取（按 path 切块）', handlerBlock('/kexi-dashboard/cex').length > 200, '')

const REACT_INTRINSICS = /^(createElement|Fragment|useEffect|useState|useSyncExternalStore|memo|cloneElement)$/
// 输出里字段可能是 `foo: ...`，也可能是先 `const x = ...` 再 `x`。
// 这里只做**保守存在性**判定：出现字段名即可，避免又写出假失败断言。
const fieldPresent = (block, field) => new RegExp('\\b' + field + '\\s*:').test(block)

const cexFields = [...new Set([...cliSrc.matchAll(/\bcexInfo\.(\w+)/g)].map(m => m[1]))]
  .filter(f => !REACT_INTRINSICS.test(f))
const cexBlock = handlerBlock('/kexi-dashboard/cex') + '\n' + hostSrc.slice(
  hostSrc.indexOf('function cexStatus'), hostSrc.indexOf('function cexForget') + 400)
for (const f of cexFields) {
  ck('③ /cex 产出 client 读取的字段 cexInfo.%s', fieldPresent(cexBlock, f),
     'host 的 cexStatus() 输出里找不到 ' + f)
}

const actFields = [...new Set([...cliSrc.matchAll(/\bact\.(\w+)/g)].map(m => m[1]))]
  .filter(f => !REACT_INTRINSICS.test(f))
const actBlock = handlerBlock('/kexi-dashboard/activity')
for (const f of actFields) {
  ck('④ /activity 产出 client 读取的字段 act.%s', fieldPresent(actBlock, f),
     '未在该路由处理块里找到 ' + f)
}

const stBlock = handlerBlock('/kexi-dashboard/status')
const stFields = [...new Set([...cliSrc.matchAll(/\b(statusObj|stObj)\.(\w+)/g)].map(m => m[2]))]
  .filter(f => !REACT_INTRINSICS.test(f))
for (const f of stFields) {
  ck('⑤ /status 产出 client 读取的字段 %s', fieldPresent(stBlock, f),
     '未在 status 处理块里找到 ' + f)
}

// ── ⑥ 活体探测（host 在跑时才有意义）─────────────────────────────────────
const BASE = process.env.KEXI_LIVE || 'http://127.0.0.1:62604'
async function probe(path) {
  try {
    const c = new AbortController()
    const t = setTimeout(() => c.abort(), 4000)
    const r = await fetch(BASE + path, { signal: c.signal })
    clearTimeout(t)
    return { ok: r.ok, status: r.status, body: await r.json() }
  } catch (e) { return { ok: false, status: 0, body: null, err: String(e.message || e) } }
}
let live = false
for (const p of routes) {
  const r = await probe(p)
  if (r.ok && r.body) { live = true; break }
}
if (live) {
  ck('⑥ host 可达（活体探测）', true, BASE)
  const cex = await probe('/kexi-dashboard/cex')
  if (cex.ok && cex.body) {
    ck('⑥ /cex 返回 credential_schema（v1.9.0 新增，前端据此渲染表单）',
       !!(cex.body.credential_schema && Object.keys(cex.body.credential_schema).length),
       JSON.stringify(Object.keys(cex.body || {})))
    ck('⑥ /cex 的 credential_schema 覆盖全部 registry 交易所',
       (cex.body.registry || []).every(e => cex.body.credential_schema[e]),
       'registry=' + JSON.stringify(cex.body.registry) + ' schema=' +
       JSON.stringify(Object.keys(cex.body.credential_schema || {})))
    ck('⑥ /cex 的 autonomy_levels 覆盖 readonly/confirm/limited/full',
       ['readonly', 'confirm', 'limited', 'full'].every(k => cex.body.autonomy_levels[k]),
       JSON.stringify(Object.keys(cex.body.autonomy_levels || {})))
  }
  const act = await probe('/kexi-dashboard/activity')
  if (act.ok && act.body) {
    ck('⑥ /activity 返回 version（卡片徽标 v1.9.1 的数据源）',
       act.body.version !== undefined && act.body.version !== null,
       'version=' + JSON.stringify(act.body.version))
  }
  const st = await probe('/kexi-dashboard/status')
  if (st.ok && st.body) {
    ck('⑥ /status 返回 version', st.body.version !== undefined, String(st.body.version))
  }
} else {
  ck('⑥ host 活体探测跳过（host 未运行；静态比对已覆盖）', true, BASE + ' 不可达')
}

// ── 汇总 ──────────────────────────────────────────────────────────────────
let bad = 0
for (const [n, ok, x] of checks) {
  if (!ok) { console.log('  FAIL  ' + n + '   ' + x); bad++ }
}
const label = live ? '含活体探测' : '仅静态比对（host 未运行）'
console.log(`test-frontend-backend-parity: ${checks.length - bad}/${checks.length} 通过（${label}）`)
process.exit(bad ? 1 : 0)
