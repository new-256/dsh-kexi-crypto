#!/usr/bin/env node
// kexi_cex 返回值「无 undefined」回归（v1.9.4）
//
// 起因：用户会话 session-0d90495b「对我当前的仓位给出意见」里，
// `kexi_cex` 连续 5 次调用中 **4 次**报
//   `tool "kexi_cex" returned invalid output: value is not lossless JSON`
// 而**唯一成功的那次**恰好是返回了错误字符串的那次（`action=balances 必须指定 exchange`）。
//
// 反直觉的比例就是线索：成功的那条有 `error: "…字符串"`，
// 失败的 4 条是 `error: undefined`。
// `{ error: undefined }` 在 JS 里是**存在的属性**、值为 undefined；
// `JSON.stringify` 会悄悄丢掉它（所以本地怎么测都绿），
// 但框架要求**无损**序列化，遇到 undefined 直接拒绝整个返回值。
//
// 本测试用**框架同款的严格检查**（遍历自有属性键）而不是 JSON.stringify ——
// 后者会把问题藏起来，那正是这个 bug 能活到今天的原因。
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const PLUGIN = join(ROOT, 'preset/kexi-crypto/kexi-plugin.mjs')
const src = readFileSync(PLUGIN, 'utf8')
const checks = []
const ck = (n, c, x = '') => checks.push([n, !!c, x])

/** 框架同款：递归找**自有**属性里值为 undefined 的。JSON.stringify 找不到它们。 */
function undefinedPaths(v, path = '$', depth = 0, seen = new Set()) {
  const out = []
  if (v === null || typeof v !== 'object' || depth > 6) return out
  if (seen.has(v)) return out
  seen.add(v)
  for (const k of Object.getOwnPropertyNames(v)) {
    const val = v[k]
    if (val === undefined) { out.push(path + '.' + k); continue }
    if (val && typeof val === 'object') out.push(...undefinedPaths(val, path + '.' + k, depth + 1, seen))
  }
  return out
}

// ── ① 源码层：kexi_cex 的 return 不得再用对象字面量写 undefined ────────────
const cexReturn = src.slice(src.indexOf('const cexTool = mkTool'), src.indexOf('ctx.effect(() => ctx.tools.register(cexTool)'))
ck('① 找到 kexi_cex 工具定义', cexReturn.length > 2000, String(cexReturn.length))
ck('① kexi_cex 的 return 里没有对象字面量式 undefined',
   !/^\s+(error|next|digest|out_files|ok|hint):[^=]*undefined\s*,?\s*$/m.test(cexReturn),
   (cexReturn.match(/^\s+\w+:[^=]*undefined\s*,?\s*$/m) || [''])[0])
ck('① 改为按需赋值（有 res.error= 的条件分支）',
   /if \(parsed\.error\) res\.error/.test(cexReturn), '')
ck('① 拦截提示走 res.next 按需赋值',
   /if \(parsed\.blocked_by\) \{\s*\n\s*res\.next/.test(cexReturn), '')

// ── ② 真实调用：把工具跑起来，用框架同款检查验收 ──────────────────────────
// 直接 import 会触发 plugin 注册，所以这里用子进程 + 桩 ctx。
const probe = `
import(process.argv[1]).then(m => {
  const captured = {}
  // ctx.tools 要同时有 register（插件用它注册）和 get（插件用它查别的工具）
  const tools = {
    register: (t) => { captured[t.name] = t; return () => {} },
    get: () => null,
  }
  const ctx = {
    tools,
    systemPrompt: { section: () => {}, getSectionOrder: () => 0 },
    subprocess: {}, timer: null,
    interval: () => () => {},
    effect: (f) => { const d = f(); return typeof d === 'function' ? d : () => {} },
    emit: () => {}, log: () => {}, config: { get: () => undefined },
  }
  m.apply(ctx)
  const tool = captured['kexi_cex']
  if (!tool) { console.log(JSON.stringify({ error: 'no kexi_cex tool' })); process.exit(3) }
  const handler = tool.handler || tool.run || tool.execute
  if (typeof handler !== 'function') { console.log(JSON.stringify({ error: 'no handler' })); process.exit(4) }
  const oldHome = process.env.DSH_HOME
  process.env.DSH_HOME = process.env.TEMP || process.env.TMP || 'C:\\\\Windows\\\\Temp'
  Promise.resolve(handler({ action: 'health' }, { signal: undefined }))
    .then(r => { process.env.DSH_HOME = oldHome; console.log(JSON.stringify({ ok: true, res: r })) })
    .catch(e => { console.log(JSON.stringify({ ok: false, err: String(e && e.message || e) })) })
}).catch(e => { console.log(JSON.stringify({ error: 'import: ' + e.message })); process.exit(5) })
`
const url = 'file:///' + PLUGIN.replace(/\\/g, '/').replace(/ /g, '%20')
const r = spawnSync(process.execPath, ['--input-type=module', '-e', probe, url],
  { encoding: 'utf8', timeout: 90000 })
let payload = null
try { payload = JSON.parse(r.stdout.trim().split('\n').pop()) } catch { /* 下面报 */ }

ck('② kexi_cex 工具可被取到并调用', !!payload && payload.ok === true,
   (payload && (payload.error || payload.err)) || r.stderr.slice(-160))
if (payload && payload.ok) {
  const res = payload.res
  ck('② 返回值是普通对象', !!res && typeof res === 'object', typeof res)
  const bad = undefinedPaths(res)
  ck('② 返回值**没有任何 undefined 自有属性**（框架同款检查）', bad.length === 0,
     '发现: ' + bad.join(', '))
  // 交叉验证：JSON.stringify 看不到问题——这正是它藏了这么久的原因
  ck('② JSON.stringify 往返本身是绿的（所以本地测不出来）',
     JSON.stringify(JSON.parse(JSON.stringify(res))) !== undefined, '')
  ck('② ok 字段是布尔', typeof res.ok === 'boolean', String(res.ok))
  ck('② digest 是非空字符串', typeof res.digest === 'string' && res.digest.length > 0,
     JSON.stringify(res.digest || '').slice(0, 80))
  ck('② out_files 是字符串数组', Array.isArray(res.out_files)
     && res.out_files.every(f => typeof f === 'string'), JSON.stringify(res.out_files))
  // ⚠ v1.9.10：不能假定 health 一定成功——探针环境里 runPython 可能失败，
  //   而修复后的工具**在失败时会正确带上 error**（这正是我们要的）。
  //   按 ok 分支断言，别把「失败时没有 error」当成通过。
  ck('② error 键与 ok 一致（成功无 error / 失败必有 error）',
     res.ok ? !Object.prototype.hasOwnProperty.call(res, 'error')
            : (Object.prototype.hasOwnProperty.call(res, 'error') && !!res.error),
     'ok=' + res.ok + ' own keys: ' + Object.keys(res).join(','))
}

let bad = 0
for (const [n, ok, x] of checks) {
  if (!ok) { console.log('  FAIL  ' + n + '   ' + x); bad++ }
}
console.log(`test-cex-tool-serialization: ${checks.length - bad}/${checks.length} 通过`)
process.exit(bad ? 1 : 0)
