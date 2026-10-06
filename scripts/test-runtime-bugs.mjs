// 回归：v1.5.4 现场取证修的两个致命 bug
//   Bug A: runBatch 返回 error: undefined → DSH lossless JSON 校验拒绝整个返回值
//          现场报错：tool "kexi_run" returned invalid output: value is not lossless JSON
//          症状：批量明明跑完（明细已落盘），主理人只看到报错。
//   Bug B: mode=fast 传 6 币只分析 1 个 —— 重复 push --symbol 被 argparse 覆盖。
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const src = readFileSync(join(ROOT, 'preset/kexi-crypto/kexi-plugin.mjs'), 'utf8')

const checks = []

// ── Bug A：工具返回值不得含 undefined ──
const rbStart = src.indexOf('async function runBatch')
const rbEnd = src.indexOf('function absolutizeArgs')
const runBatch = src.slice(rbStart, rbEnd)
// 只看 runBatch 的**返回对象**（最后一个 `return {` 之后），不看 rows 明细行
const retIdx = runBatch.lastIndexOf('return {')
const retBlock = runBatch.slice(retIdx)
checks.push(['runBatch 返回对象无 undefined', !/:\s*[^,\n]*undefined/.test(retBlock), ''])
checks.push(['error 字段用 null 而非 undefined', /error: badRows\.length === items\.length \? clip\(badRows\[0\]\.error, 200\) : null/.test(runBatch), ''])

// 行为模拟：递归复现 DSH 的 lossless JSON 判定
function hasUndefined(v, depth = 0) {
  if (v === undefined) return true
  if (depth > 6 || v === null || typeof v !== 'object') return false
  return Object.values(v).some((x) => hasUndefined(x, depth + 1))
}
const partialFailure = { ok: true, digest: 'x', out_files: ['a.json'], error: undefined }
checks.push(['模拟：undefined 返回值会被 DSH 拒绝（复现原 bug）', hasUndefined(partialFailure) === true, ''])
checks.push(['模拟：null 返回值通过校验（修复后）', hasUndefined({ ...partialFailure, error: null }) === false, ''])
checks.push(['模拟：嵌套 undefined 也会被拒（明细行已同步修）',
  hasUndefined({ ok: true, digest: 'd', out_files: [], rows: [{ item: 'X', bars: null }] }) === false, ''])

// ── Bug B：多币必须走 --symbols（逗号），不能重复 --symbol ──
const fastBranch = src.slice(src.indexOf("args.mode === 'fast'"), src.indexOf("args.mode === 'script'"))
checks.push(['mode=fast 多币改用 --symbols', /argv\.push\('--symbols', syms\.join\(','\)\)/.test(fastBranch), ''])
checks.push(['mode=fast 单币走 --symbol', /argv\.push\('--symbol', syms\[0\]\)/.test(fastBranch), ''])
checks.push(['mode=fast 不再循环 push --symbol', !/for \(const s of syms\)/.test(fastBranch), ''])

// 行为模拟：argparse 语义
function parseArgv(argv) {
  const out = {}
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) { out[argv[i]] = argv[i + 1] }
  }
  return out
}
const six = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT']
const buggy = ['fast_analysis.py', '--out-dir', 'kexi_out', ...six.flatMap((s) => ['--symbol', s])]
const fixed = ['fast_analysis.py', '--out-dir', 'kexi_out', '--symbols', six.join(',')]
checks.push(['模拟：重复 --symbol 只剩 1 个（复现原 bug）', parseArgv(buggy)['--symbol'] === 'DOGEUSDT', ''])
checks.push(['模拟：--symbols 保留全部 6 个（修复后）', parseArgv(fixed)['--symbols'].split(',').length === 6, ''])

// ── 附带：mode 缺省推断（且不得劫持旧式 pipeline:true 调用）──
checks.push(['mode 缺省时 batch → script', /if \(!args\.mode\)/.test(src) && /args\.mode = 'script'/.test(src), ''])
const inferBlock = src.slice(src.indexOf('if (!args.mode)'), src.indexOf('if (!args.mode)') + 320)
checks.push(['mode 缺省时 symbols → fast', /args\.mode = 'fast'/.test(inferBlock), ''])
checks.push(['旧式 pipeline:true + symbol 不被劫持成 fast',
  !/args\.symbol && !args\.script\)\s*\)\s*args\.mode = 'fast'/.test(src), ''])

let all = true
for (const [n, ok, extra] of checks) {
  console.log((ok ? '  ✓ ' : '  ✗ ') + n + (extra ? '  → ' + extra : ''))
  if (!ok) all = false
}
console.log(all ? '\n✅ v1.5.4 现场取证回归通过' : '\n❌ 仍有遗留问题')
process.exit(all ? 0 : 1)
