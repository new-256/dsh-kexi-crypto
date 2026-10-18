// test-v1922-fixes.mjs —— v1.9.22 全部修复的回归测试
//
// 依据 docs/diagnosis-session-44da005c.md（459 事件全程跟踪）+ docs/repair-plan-v1.9.22.md
// 每条断言都对应一个**已实测复现**的现场缺陷，不是想象出来的边界。
//
// 跑法：node scripts/test-v1922-fixes.mjs
// 超时保护：外部 run-all-tests.ps1 会带超时；本文件自身不联网的部分应 <2s。

import { readFileSync, existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { normalizeSymbol, normalizeSymbols, PURE_QUOTES } from '../preset/kexi-crypto/lib/symbol-normalize.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')
const SCRIPTS = join(ROOT, 'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts')

let pass = 0
let fail = 0
const failures = []

function ok(name, cond, detail = '') {
  if (cond) {
    pass++
    console.log(`  ✓ ${name}`)
  } else {
    fail++
    const msg = `${name}${detail ? ' — ' + detail : ''}`
    failures.push(msg)
    console.log(`  ✗ ${msg}`)
  }
}

function eq(name, actual, expected) {
  ok(name, JSON.stringify(actual) === JSON.stringify(expected),
    `期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(actual)}`)
}

function section(t) { console.log(`\n[${t}]`) }

// ══════════════════════════════════════════════════════════════════
// D1 · 符号规范化（根因）
// ══════════════════════════════════════════════════════════════════
section('D1 符号规范化')

eq('裸符号 BTC → BTCUSDT', normalizeSymbol('BTC').symbol, 'BTCUSDT')
ok('裸符号 BTC 标记为已改写', normalizeSymbol('BTC').changed === true)
ok('裸符号 BTC 给出改写说明', typeof normalizeSymbol('BTC').note === 'string' && normalizeSymbol('BTC').note.includes('BTCUSDT'))

eq('已带后缀 BTCUSDT 不改写', normalizeSymbol('BTCUSDT').symbol, 'BTCUSDT')
ok('已带后缀 changed=false', normalizeSymbol('BTCUSDT').changed === false)

eq('小写 btc → BTCUSDT', normalizeSymbol('btc').symbol, 'BTCUSDT')
eq('分隔符 btc/usdt → BTCUSDT', normalizeSymbol('btc/usdt').symbol, 'BTCUSDT')
eq('分隔符 ETH-USDT → ETHUSDT', normalizeSymbol('ETH-USDT').symbol, 'ETHUSDT')
eq('前后空白 "  sol  " → SOLUSDT', normalizeSymbol('  sol  ').symbol, 'SOLUSDT')
eq('BTC/ETH 计价对 ETHBTC 保持原样', normalizeSymbol('ETHBTC').symbol, 'ETHBTC')

// 边界：计价币本身不能被补成垃圾符号
ok('纯计价币 USD 报错而非补成 USDUSDT', normalizeSymbol('USD').symbol === null && /计价币/.test(normalizeSymbol('USD').error))
ok('纯计价币 USDT 报错', normalizeSymbol('USDT').symbol === null)
// BTC/ETH/BNB 本身是标的，必须补后缀而不是被当成计价币拒绝
eq('BTC 不被误判为纯计价币', normalizeSymbol('BTC').symbol, 'BTCUSDT')
eq('ETH 不被误判为纯计价币', normalizeSymbol('ETH').symbol, 'ETHUSDT')
ok('PURE_QUOTES 不含 BTC/ETH/BNB', !PURE_QUOTES.includes('BTC') && !PURE_QUOTES.includes('ETH') && !PURE_QUOTES.includes('BNB'))

// 非法输入
ok('空串报错', normalizeSymbol('').symbol === null && normalizeSymbol('').error)
ok('纯空白报错', normalizeSymbol('   ').symbol === null)
ok('非法字符 !@# 报错', normalizeSymbol('!@#').symbol === null)
ok('非法字符不返回符号', normalizeSymbol('!@#').symbol === null)

// 批量
const batch = normalizeSymbols('BTC, eth ,BTCUSDT,  ,SOL')
eq('批量去重保序', batch.symbols, ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])
ok('批量收集改写说明', batch.notes.length === 3)
ok('批量 changed=true', batch.changed === true)
eq('批量对空段不报错', normalizeSymbols('BTC,,ETH').errors, [])

// Python 侧一致性（同一套用例必须同结果）
const pyCheck = spawnSync('python', ['-c', `
import sys
sys.path.insert(0, r"${SCRIPTS}")
import kline_utils as ku
cases = ["BTC","BTCUSDT","btc","btc/usdt","ETH-USDT","  sol  ","","!@#","USD","ETHBTC"]
out = []
for c in cases:
    r = ku.normalize_symbol(c)
    out.append("%s|%s" % (r["symbol"] or "None", "1" if r["changed"] else "0"))
print(";".join(out))
`], { encoding: 'utf8', timeout: 30000 })

if (pyCheck.status === 0) {
  const pyRows = pyCheck.stdout.trim().split(';')
  const jsRows = ['BTC','BTCUSDT','btc','btc/usdt','ETH-USDT','  sol  ','','!@#','USD','ETHBTC']
    .map((c) => {
      const r = normalizeSymbol(c)
      return `${r.symbol || 'None'}|${r.changed ? '1' : '0'}`
    })
  eq('JS/Python 符号规范化逐项一致', pyRows, jsRows)
} else {
  ok('Python 侧可调用', false, (pyCheck.stderr || '').slice(0, 200))
}

// ══════════════════════════════════════════════════════════════════
// D2 · 真实成败语义（虚假成功）
// ══════════════════════════════════════════════════════════════════
section('D2 真实成败语义')

const fa = readFileSync(join(SCRIPTS, 'fast_analysis.py'), 'utf8')
ok('fast_analysis 不再恒 print ok:True', !/print\(json\.dumps\(\{"ok": True, "count": len\(rows\)/.test(fa))
ok('全失败时返回非零退出码', /if all_failed:[\s\S]{0,600}?return 3/.test(fa))
ok('存在 all_failed 判定', /all_failed = bool\(rows\) and ok_count == 0/.test(fa))
ok('存在 degraded 标记', /"degraded": degraded/.test(fa))
ok('存在 failed_symbols 列表', /"failed_symbols": failed_symbols/.test(fa))
ok('失败评级纳入判定', /BAD_RATINGS = \("取数失败", "数据不可用", "未分析·超预算"\)/.test(fa))

const plugin = readFileSync(join(ROOT, 'preset', 'kexi-crypto', 'kexi-plugin.mjs'), 'utf8')
ok('plugin 读取脚本自身 ok 而非只看 exit code', /scriptOk === false/.test(plugin))
ok('plugin 失败时返回 ok:false', /!fr\.ok \|\| scriptOk === false[\s\S]{0,200}?ok: false/.test(plugin))
ok('plugin 向上透传 degraded', /degraded,/.test(plugin))

// ══════════════════════════════════════════════════════════════════
// D3 · 取数性能预算
// ══════════════════════════════════════════════════════════════════
section('D3 取数性能预算')

const fk = readFileSync(join(SCRIPTS, 'fetch_klines.py'), 'utf8')
ok('fetch_binance 有 time_budget 参数', /def fetch_binance\(symbol, interval, limit, time_budget=10\.0\)/.test(fk))
ok('fetch_binance 检查 deadline', /if time\.time\(\) > deadline:/.test(fk))
ok('HTTP 400 短路整条 host 链', /"400" in str\(e\)[\s\S]{0,200}?break/.test(fk))
ok('HTTP 451 短路整条 host 链', /"451" in str\(e\)[\s\S]{0,200}?break/.test(fk))
ok('http_get 支持 deadline', /def http_get\(url, retries=3, timeout=15, deadline=None\)/.test(fk))
ok('http_get 重试前检查 deadline', /deadline is not None and time\.time\(\) > deadline/.test(fk))
ok('fast_analysis 有 --time-budget', /--time-budget/.test(fa))
ok('超预算标记为未分析', /未分析·超预算/.test(fa))
ok('参照币使用轻预算 3s', /load_or_fetch\(name, "1d", args\.limit, out_dir, args\.refresh, budget=3\.0\)/.test(fa))
ok('非法符号在取数前短路', /if sym_errors:[\s\S]{0,400}?return 2/.test(fa))

// ══════════════════════════════════════════════════════════════════
// D4 · 产物语义校验
// ══════════════════════════════════════════════════════════════════
section('D4 产物语义校验')

ok('定义了产物签名表', /ARTIFACT_SIGNATURES/.test(plugin))
ok('实现了 semanticMismatch', /function semanticMismatch/.test(plugin))
ok('语义不符纳入质量标记', /const mismatch = semanticMismatch/.test(plugin))
ok('识别不出内容时不妄下结论', /if \(!actual\) return null/.test(plugin))

// 用与插件相同的规则复算现场真实事故
const SIG = [
  { kind: 'position', fileHint: /_position\b|position_/i, fields: ['pos_90', 'drawdown', 'range_90'] },
  { kind: 'indicators', fileHint: /_indicators\b|indicators_/i, fields: ['ma', 'ema', 'macd', 'rsi'] },
  { kind: 'klines', fileHint: /_klines\b|klines_/i, fields: ['klines'] },
  { kind: 'entry_plan', fileHint: /_entry(_plan)?\b|entry_plan_/i, fields: ['entry_now', 'entry_action', 'invalidation'] },
  { kind: 'fast_analysis', fileHint: /fast_analysis/i, fields: ['rows', 'elapsed_ms'] },
]
function detectKind(d) {
  for (const s of SIG) if (s.fields.some((f) => Object.prototype.hasOwnProperty.call(d, f))) return s.kind
  return null
}
function mismatch(p, d) {
  const n = String(p).split(/[\\/]/).pop() || ''
  const a = detectKind(d)
  if (!a) return null
  const c = SIG.find((s) => s.fileHint.test(n))
  if (!c || c.kind === a) return null
  return `${n} claims ${c.kind} but is ${a}`
}
ok('现场事故：indicators 内容写进 _position.json 被抓出',
  /\bclaims position but is indicators/.test(mismatch('kexi_out/BTCUSDT_1d_position.json', { ma: {}, rsi: 50 }) || ''))
ok('正确配对不误报', mismatch('kexi_out/BTCUSDT_1d_indicators.json', { ma: {}, rsi: 50 }) === null)
ok('正确 position 不误报', mismatch('kexi_out/BTCUSDT_1d_position.json', { pos_90: 0.7 }) === null)
ok('无法识别内容不误报', mismatch('kexi_out/whatever.json', { foo: 1 }) === null)

// ══════════════════════════════════════════════════════════════════
// D5 · CoinGecko 币表泛化
// ══════════════════════════════════════════════════════════════════
section('D5 CoinGecko 币表泛化')

// v1.9.22：本逻辑已按用户规则 26 拆到独立模块 cg_registry.py
const cgPath = join(SCRIPTS, 'cg_registry.py')
const cg = existsSync(cgPath) ? readFileSync(cgPath, 'utf8') : ''

ok('cg_registry.py 存在（按规则26拆分）', existsSync(cgPath))
ok('实现了 resolve_coingecko_id', /def resolve_coingecko_id\(symbol\)/.test(cg))
ok('实现了全量币表加载', /def _load_cg_list\(\)/.test(cg))
ok('币表有 TTL 缓存', /CG_LIST_CACHE_TTL_SEC/.test(cg))
ok('同名多候选按市值裁决', /market_cap/.test(cg))
ok('解析失败时明确报错而非猜', /拒绝猜测/.test(cg))
ok('静态表降级为速查缓存（有注释说明）', /不是白名单/.test(cg))
ok('HTTP 实现可注入（便于离线测试）', /def set_http_get\(fn\)/.test(cg))

// fetch_klines 侧只转发、不重复实现
ok('fetch_klines 转发到 cg_registry', /from cg_registry import CG_IDS, resolve_coingecko_id/.test(fk))
ok('fetch_coingecko 走动态解析', /coin, note = resolve_coingecko_id\(symbol\)/.test(fk))
ok('fetch_quote 也走动态解析', /coin, note = resolve_coingecko_id\(symbol\)[\s\S]{0,200}?binance failed/.test(fk))
ok('注入 http_get 给 cg_registry', /_cg\.set_http_get/.test(fk))

// ══════════════════════════════════════════════════════════════════
// D10 · 落盘快照滞后
// ══════════════════════════════════════════════════════════════════
section('D10 落盘快照滞后')

ok('写盘后回读校验', /落盘校验失败/.test(fk))
ok('回读不一致时 exit 4', /sys\.exit\(4\)/.test(fk))
ok('比对 bars 与末根时间戳', /len\(_rb_kl\) != len\(result\["klines"\]\) or _mem_last != _rb_last/.test(fk))
ok('插件侧读取 self_check', /d\.self_check && d\.self_check\.ok === false/.test(plugin))

// ══════════════════════════════════════════════════════════════════
// D11 · 质检字段
// ══════════════════════════════════════════════════════════════════
section('D11 质检字段')

ok('输出含 self_check', /result\["self_check"\] = \{/.test(fk))
ok('self_check 含 bars', /"bars": len\(result\["klines"\]\)/.test(fk))
ok('self_check 含 last_closed_open', /"last_closed_open": result\.get\("last_closed_open"\)/.test(fk))

// ══════════════════════════════════════════════════════════════════
// 编排层 D6/D7/D8/D9
// ══════════════════════════════════════════════════════════════════
section('编排层 D6/D7/D8/D9')

const skill = readFileSync(join(ROOT, 'preset', 'kexi-crypto', 'skills', 'kexi-team-orchestration', 'SKILL.md'), 'utf8')

// D8：选币触发词
ok('Workflow D 触发词含「选币」', /Workflow D[\s\S]{0,600}?选币/.test(skill))
ok('Workflow D 触发词含「挑 N 个」', /Workflow D[\s\S]{0,600}?挑 N 个/.test(skill))
ok('Workflow D 触发词含「给我推荐几个」', /Workflow D[\s\S]{0,600}?给我推荐几个/.test(skill))

// D7：仓位诊断工作流
ok('存在 Workflow O（仓位体检）', /### Workflow O：仓位体检/.test(skill))
ok('Workflow O 覆盖「仓位诊断」', /Workflow O[\s\S]{0,400}?仓位诊断/.test(skill))
ok('Workflow O 与 G 有区分说明', /与 Workflow G 的区别/.test(skill))

// D6：派活判据可操作
ok('快速通道给出「必须派活」判据表', /什么时候\*\*必须\*\*派活/.test(skill))
ok('判据含多标的组合决策', /多个标的的组合决策/.test(skill))
ok('判据含真实资金动作', /真实资金动作/.test(skill))
ok('有「建了队就必须派活」专节', /建了队就必须派活/.test(skill))

// D9：等待期纪律
ok('等待期纪律写入 SKILL', /等待时不要空转/.test(skill))
ok('wait_agent 超时应对写清楚', /timedOut:true/.test(skill))
ok('plugin 规则含建队必派活', /建了队就必须派活/.test(plugin))
ok('plugin 规则含仓位走 Workflow O', /Workflow O/.test(plugin))
ok('plugin 规则含等待期纪律', /等待期纪律/.test(plugin))

// 标题与实际工作流数量一致
ok('标题改为 A~O 全量枚举', /## 一、标准工作流（A~O）/.test(skill))

// ══════════════════════════════════════════════════════════════════
// S4 · 独立数值复核入口（verify_numbers.py）
// ══════════════════════════════════════════════════════════════════
section('S4 独立数值复核入口')

const vnPath = join(SCRIPTS, 'verify_numbers.py')
const vn = existsSync(vnPath) ? readFileSync(vnPath, 'utf8') : ''

ok('verify_numbers.py 存在', existsSync(vnPath))
ok('复用主链指标函数（口径一致）', /import indicators as ind_mod/.test(vn) && /import position as pos_mod/.test(vn))
ok('只读原始 K 线（不读已有指标产物做输入）', /klines_from|_klines_from/.test(vn))
ok('支持 --check name=value 直接核对', /--check/.test(vn))
ok('有相对容差而非零容差', /REL_TOL = 0\.005/.test(vn))
ok('识别语义错位（position 文件装指标内容）', /semantic_mismatch/.test(vn))
ok('只列差异不裁决', /不替你裁决/.test(vn))
ok('符号走统一规范化', /ku\.normalize_symbol/.test(vn))
ok('已纳入 kexi_run 白名单', /'verify_numbers\.py'/.test(plugin))
ok('规则 9b 指向官方复核入口', /9b\) \*\*需要独立复核时用官方入口/.test(plugin))
ok('SKILL 写入复核用法', /verify_numbers\.py/.test(skill))

// ══════════════════════════════════════════════════════════════════
// 单文件行数（用户规则 26：≤600 行）
// ══════════════════════════════════════════════════════════════════
section('单文件行数约束')

for (const [label, p, limit] of [
  ['symbol-normalize.mjs', join(ROOT, 'preset', 'kexi-crypto', 'lib', 'symbol-normalize.mjs'), 600],
  ['kline_utils.py', join(SCRIPTS, 'kline_utils.py'), 600],
  ['fetch_klines.py', join(SCRIPTS, 'fetch_klines.py'), 600],
  ['cg_registry.py', join(SCRIPTS, 'cg_registry.py'), 600],
  ['verify_numbers.py', join(SCRIPTS, 'verify_numbers.py'), 600],
]) {
  if (!existsSync(p)) { ok(`${label} 存在`, false); continue }
  const n = readFileSync(p, 'utf8').split('\n').length
  ok(`${label} ≤${limit} 行`, n <= limit, `实际 ${n} 行`)
}

// ══════════════════════════════════════════════════════════════════
console.log('\n' + '─'.repeat(60))
if (fail === 0) {
  console.log(`v1.9.22 回归：PASS（${pass} 项）`)
  process.exit(0)
} else {
  console.log(`v1.9.22 回归：FAIL（${pass} 通过 / ${fail} 失败）`)
  console.log('\n失败项：')
  failures.forEach((f) => console.log('  · ' + f))
  process.exit(1)
}
