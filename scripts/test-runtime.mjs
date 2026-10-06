// scripts/test-runtime.mjs — 运行时行为回归（v1.2.0 新能力）。
//
// 与 verify.mjs 的分工：verify 做**静态接线**断言（源码里该有的东西在不在），
// 本脚本用假 ctx **真实驱动模块**做**行为**断言（功能是否真的工作）。两者互补：
// 静态检查会被注释/子串喂饱，行为测试不会。
//
// 覆盖：
//   A. host 活动流：session/event → 可读节点、CLI 节点识别、结果回溯、耗时、
//      子智能体成员名、pipeline 步骤同步、环形缓冲与裁剪、坏输入容错。
//   B. 设置端点：默认值、边界收敛、落盘、裁剪生效、坏 JSON 容错。
//   C. kexi_cli：CLI 探测、设置优先级/开关、派发、**不静默换源**、诚实降级。
//
// 用法: node scripts/test-runtime.mjs      （无需 DSH、无需网络、无第三方依赖）

import { pathToFileURL } from 'node:url'
import { existsSync, rmSync, writeFileSync, mkdirSync, readFileSync, utimesSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const HOST_MOD = join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/index.mjs')
const PLUGIN_MOD = join(ROOT, 'preset/kexi-crypto/kexi-plugin.mjs')
const TMP = join(ROOT, '.kexi-test-tmp')

if (existsSync(TMP)) rmSync(TMP, { recursive: true, force: true })
mkdirSync(TMP, { recursive: true })
const SETTINGS = join(TMP, 'kexi-settings.json')
process.env.KEXI_SETTINGS_FILE = SETTINGS
process.env.DSH_HOME = TMP

let pass = 0, fail = 0
const ok = (cond, msg) => { if (cond) { pass++; console.log('  ✓ ' + msg) } else { fail++; console.log('  ✗ FAIL: ' + msg) } }
const head = (t) => console.log('\n' + t)

// ══════════════════════════════════════════════════════════════════════════
// A/B. host 半个插件
// ══════════════════════════════════════════════════════════════════════════
head('[A] host 活动流与设置端点')

const handlers = new Map()
const routes = new Map()
const fakeWs = { register: (r) => { routes.set(r.path, r); return () => routes.delete(r.path) } }
const fakeCtx = {
  on(name, fn) { if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(fn); return () => { } },
  inject(_deps, cb) { cb({ get: (n) => (n === 'webServer' ? fakeWs : undefined), effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } } }) },
  effect(fn) { const d = fn(); return typeof d === 'function' ? d : () => { } },
}
const emit = (name, ...args) => { for (const fn of (handlers.get(name) || [])) fn(...args) }

// 真实 tool/result 事件工厂：模拟 agent-loop appendToolResult + createToolResultMessage
// （data.message 携带 toolCallId/content/isError，而非顶层 callId/result）。
const toolResult = (callId, resultObj) => {
  const isError = !!(resultObj && resultObj.isError)
  const body = isError
    ? [{ type: 'text', text: JSON.stringify({ isError: true, error: resultObj.error || 'error' }) }]
    : [{ type: 'text', text: JSON.stringify(resultObj) }]
  return {
    type: 'tool/result',
    data: {
      message: {
        role: 'tool', toolCallId: callId, source: { kind: 'tool', callId },
        content: body, isError,
      },
    },
  }
}

const host = await import(pathToFileURL(HOST_MOD).href)
ok(typeof host.apply === 'function', 'host 导出 apply')
if (existsSync(SETTINGS)) rmSync(SETTINGS)
host.apply(fakeCtx)
ok(routes.has('/kexi-dashboard/status'), '注册 /status（研判灯）')
ok(routes.has('/kexi-dashboard/activity'), '注册 /activity（实时进度，需求 1）')
ok(routes.has('/kexi-dashboard/settings'), '注册 /settings（设置面板，需求 3）')
ok(handlers.has('session/event'), '订阅 session/event（活动流权威源）')

function callRoute(path, method = 'GET', body = null, urlOverride = null) {
  return new Promise((resolve) => {
    const listeners = {}
    const req = { method, url: urlOverride || path, on(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn) }, destroy() { } }
    let acc = ''
    const res = {
      _code: 0,
      writeHead(c) { this._code = c },
      end(s) { acc += (s || ''); let j = null; try { j = JSON.parse(acc) } catch { } resolve({ code: this._code, json: j, raw: acc }) },
    }
    const r = routes.get(path)
    if (!r) return resolve({ code: 0, json: null })
    r.handler(req, res)
    if (method === 'POST' || method === 'PUT') {
      setTimeout(() => {
        for (const fn of (listeners['data'] || [])) fn(body === null ? '' : JSON.stringify(body))
        for (const fn of (listeners['end'] || [])) fn()
      }, 0)
    }
  })
}

const sess = { id: 'sess-1', cwd: 'C:/work/kexi-demo' }

// v1.6.1：卡片只展示**有 kexi 活动**的会话（纯聊天/写代码的会话不进卡片）。
// 下面一串成果登记 / 懒发现用例的本意是验证**产物发现**，不是节点过滤，
// 它们过去只靠 turn/start 建桶。加这一次 kexi 调用把会话"点亮"，
// 让这些用例继续测它们真正要测的东西，而不是被新的会话级过滤误伤。
function lightKexi(s, callId) {
  emit('session/event', s, { type: 'tool/call', data: { callId, name: 'kexi_run', arguments: { symbol: 'ETHUSDT' } } })
  emit('session/event', s, toolResult(callId, { ok: true }))
}

emit('session/event', sess, { type: 'turn/start', data: { turn: 1 } })
emit('session/event', sess, { type: 'step/start', data: { turn: 1, step: 1 } })
emit('session/event', sess, { type: 'tool/call', data: { turn: 1, step: 1, callId: 'c1', name: 'kexi_run', arguments: { symbol: 'ETHUSDT', interval: '1d' } } })
emit('session/event', sess, toolResult('c1', { ok: true, digest: 'ETHUSDT 研判完成 risk=中' }))
emit('session/event', sess, { type: 'tool/call', data: { turn: 1, step: 2, callId: 'c2', name: 'agy_run', arguments: { prompt: '审查多空逻辑', background: false } } })
emit('session/event', sess, toolResult('c2', { ok: true }))
emit('session/event', sess, { type: 'tool/call', data: { turn: 1, step: 3, callId: 'c3', name: 'bash', arguments: { command: 'python screener.py --top 100' } } })
emit('session/event', sess, toolResult('c3', { out_files: ['C:/out/screen.json'] }))
emit('session/event', sess, { type: 'tool/call', data: { turn: 1, step: 4, callId: 'c4', name: 'codebuddy_run', arguments: { prompt: '复核解锁时间表' } } })
emit('session/event', sess, toolResult('c4', { isError: true, error: 'timeout' }))
emit('session/event', sess, { type: 'turn/end', data: { turn: 1, reason: { kind: 'completed' } } })

let r = await callRoute('/kexi-dashboard/activity')
ok(r.code === 200, '/activity 返回 200')
let s0 = r.json.sessions[0]
// v1.6.1：共发 7 个节点（turn / step / kexi_run / agy_run / bash / codebuddy_run / turn_end）。
// 时间线现在只展示 kexi 相关：**kexi_run、agy_run、codebuddy_run = 3**。
// 被滤掉的是：turn、step、turn_end（回合生命周期——是"有活动"的信号，不是
// "在研判"的证据）与 bash（不是研判链路工具）。
// ⚠ registerArtifactFor **不推节点**，它只设 b.artifactId；
//    早先这里误以为是 artifact 节点占的第 4 个，实际是 turn_end。
ok(s0.nodes.length === 3, `节点数 = 3（实际 ${s0.nodes.length}）：turn/step/turn_end/bash 已按 kexi 相关性过滤`)
const labels = s0.nodes.map((n) => n.label).join(' | ')
ok(labels.includes('跑研判流水线 ETHUSDT'), 'kexi_run 翻译为中文动作')
ok(labels.includes('agy 执行'), 'agy_run 显示为本地 CLI 节点（需求 2 可见性）')
ok(labels.includes('codebuddy 执行'), 'codebuddy_run 显示为 CLI 节点')
const c1 = s0.nodes.find((n) => n.callId === 'c1')
ok(c1 && c1.status === 'ok', '"工具结果"回溯标记 c1 完成')
ok(c1 && typeof c1.durationMs === 'number', 'c1 记录耗时')
ok(c1 && String(c1.result).includes('研判完成'), 'c1 带"拿到什么"摘要')
ok(s0.nodes.find((n) => n.callId === 'c4').status === 'error', '失败的 CLI 调用标记 error')
ok(s0.stats.cli === 2, `CLI 节点计数 = ${s0.stats.cli}（期望 2）`)
// v1.6.1：时间线只展示 kexi 相关节点。上面发了 4 个 tool/call：
//   kexi_run / agy_run / codebuddy_run 属于研判链路；**bash 不是**。
//   旧实现不过滤 → 计数 4；新实现期望 3。少显示比多显示对（用户报的正是
//   "没派活却满屏像任务流程"，根因就是这类噪音被 describeTool 配了中文描述）。
ok(s0.stats.tools === 3, `工具节点计数 = ${s0.stats.tools}（期望 3：bash 已按 kexi 过滤）`)
ok(!s0.nodes.some((n) => n.tool === 'bash'), '时间线已排除非 kexi 工具（bash）')
ok(s0.nodes.some((n) => n.tool === 'kexi_run'), '时间线仍保留 kexi 工具节点')
// v1.6.1：current（"此刻在干什么"）读**全量**节点，不受 kexi 过滤影响——
// 研判到一半模型在思考时（step/start 无 step/end）卡片不能一片空白。
ok(s0.current && typeof s0.current.label === 'string', 'current 汇报"当前在干什么"')

emit('subagent/start', { agentType: 'technical_indicators', cwd: 'C:/work/kexi-demo', parentSessionId: 'sess-1', description: '算指标' })
emit('subagent/end', { cwd: 'C:/work/kexi-demo', parentSessionId: 'sess-1', status: 'ok' })
r = await callRoute('/kexi-dashboard/activity')
ok(r.json.sessions.some((s) => s.nodes.some((n) => n.kind === 'subagent' && /指北/.test(n.label))), 'subagent 映射出成员中文名（谁在干活）')

head('[A2] turn/step 生命周期关闭（防节点永久 running / 会话永久 busy）')

const slife = { id: 'sess-life', cwd: 'C:/work/life' }
emit('session/event', slife, { type: 'turn/start' })
emit('session/event', slife, { type: 'step/start' })
emit('session/event', slife, { type: 'step/end' })
emit('session/event', slife, { type: 'tool/call', data: { callId: 'L1', name: 'read', arguments: {} } })
emit('session/event', slife, toolResult('L1', { ok: 1 }))
emit('session/event', slife, { type: 'turn/end', data: { reason: { kind: 'completed' } } })
r = await callRoute('/kexi-dashboard/activity')
const life = r.json.sessions.find((s) => s.id === 'sess-life')
// v1.6.1：本会话只有 turn / read 这类非研判活动 → **整个会话都不该出现在卡片上**。
// 这正是用户报的问题（"没派活却满屏像任务流程"）。早先 isKexiNode 对未知 kind
// 默认放行，`turn_end`（回合结束标记）把只用了 read 的会话整个点亮了。
ok(!life, '纯非研判会话（只有 turn/read）不进入卡片（v1.6.1 会话级过滤）')

// aborted 回合：同样只有 turn 事件 → 同样不该进卡片。
// 但**忙判定仍须正确**：turn 节点被过滤不代表它没被关闭。
// v1.6.1 用另一个带 kexi 活动的会话来验证 aborted 的 busy 语义。
const slife2 = { id: 'sess-life2', cwd: 'C:/work/life2' }
emit('session/event', slife2, { type: 'turn/start' })
emit('session/event', slife2, { type: 'tool/call', data: { callId: 'L2', name: 'kexi_run', arguments: { symbol: 'BTCUSDT' } } })
emit('session/event', slife2, toolResult('L2', { ok: true, digest: 'BTCUSDT 快答完成' }))
emit('session/event', slife2, { type: 'turn/end', data: { reason: { kind: 'aborted' } } })
r = await callRoute('/kexi-dashboard/activity')
const life2 = r.json.sessions.find((s) => s.id === 'sess-life2')
ok(!!life2, '有 kexi 活动的会话即使回合 aborted 也保留在卡片里')
ok(life2.busy === false, 'aborted 回合不 busy（turn 已关闭且工具已结束）')
ok(!life2.nodes.some((n) => n.status === 'running'), 'aborted 会话无残留 running 节点')

head('[A3] 最终看板产物登记 / 内嵌服务（成果在 K析页面直接可见）')

// 准备一个真实的临时离线看板文件
const artFile = join(TMP, 'ETHUSDT-dashboard.html')
writeFileSync(artFile, '<!doctype html><html><head><meta charset="utf-8"><title>ETHUSDT 看板</title></head><body>最终研判成果</body></html>', 'utf8')
ok(routes.has('/kexi-dashboard/artifact'), '注册 /artifact 看板服务路由')

// kexi_dashboard 工具返回 out_files（绝对路径 HTML）→ 自动登记
const sArt = { id: 'sess-art', cwd: TMP }
emit('session/event', sArt, { type: 'tool/call', data: { callId: 'D1', name: 'kexi_dashboard', arguments: { input: 'report.json' } } })
emit('session/event', sArt, toolResult('D1', { ok: true, out_files: [artFile] }))
r = await callRoute('/kexi-dashboard/activity')
const artSess = r.json.sessions.find((s) => s.id === 'sess-art')
ok(artSess.artifact && artSess.artifact.name === 'ETHUSDT-dashboard.html', '/activity 会话带最终成果 artifact（id/name）')
ok(artSess.artifact && /^\/kexi-dashboard\/artifact\?id=/.test(artSess.artifact.url), 'artifact 带可内嵌的 url')
const artId = artSess.artifact.id

// 通过 artifact 路由拿到原始 HTML（iframe 直接展示）
const artResp = await callRoute('/kexi-dashboard/artifact', 'GET', null, '/kexi-dashboard/artifact?id=' + artId)
ok(artResp.code === 200, 'artifact 路由返回 200')
ok(/最终研判成果/.test(artResp.raw), 'artifact 路由返回看板真实 HTML')

// 未知 id → 404
const noArt = await callRoute('/kexi-dashboard/artifact', 'GET', null, '/kexi-dashboard/artifact?id=nope')
ok(noArt.code === 404, '未知 artifact id 返回 404（不可猜路径）')

// 非 HTML 文件 / 相对路径 out_files 不登记（安全/健壮）
// 用独立空目录作为 cwd，避免懒发现扫描到其他测试会话放在 TMP 根的看板。
// v1.6.1：这里必须用 **kexi 工具**，否则整个会话会被会话级过滤剔掉
// （只有 read 的会话压根不在 /activity 列表里，断言就取不到对象了）。
const badCwd = join(TMP, 'bad-nodash')
mkdirSync(badCwd, { recursive: true })
const sBad = { id: 'sess-artbad', cwd: badCwd }
emit('session/event', sBad, { type: 'tool/call', data: { callId: 'D9', name: 'kexi_run', arguments: { symbol: 'ETHUSDT' } } })
emit('session/event', sBad, toolResult('D9', { out_files: ['relative/report.json'] }))
r = await callRoute('/kexi-dashboard/activity')
const badSess = r.json.sessions.find((s) => s.id === 'sess-artbad')
ok(!!badSess, '带 kexi 活动的会话在列表内（会话级过滤放行）')
ok(!badSess.artifact, '相对路径/非 .html 不登记 artifact')

// 懒发现正向：不发射任何看板事件，只在 cwd/kexi_out 放一个看板文件 →
// /activity 组装时应自动发现并补登（不依赖事件是否被正确捕获）。
// v1.5.2：看板必须**晚于会话开始**才算本次成果（见下方陈旧产物反例），
// 所以这里先建会话再落盘——这也才是真实顺序（跑完才渲染出看板）。
const discCwd = join(TMP, 'disc-cwd')
mkdirSync(join(discCwd, 'kexi_out'), { recursive: true })
const sDisc = { id: 'sess-disc', cwd: discCwd }
emit('session/event', sDisc, { type: 'turn/start' })
lightKexi(sDisc, 'L-disc')  // 只建桶，不给看板结果
writeFileSync(join(discCwd, 'kexi_out', 'algo_dashboard.html'),
  '<!doctype html><html><body>懒发现看板</body></html>', 'utf8')
r = await callRoute('/kexi-dashboard/activity')
const discSess = r.json.sessions.find((s) => s.id === 'sess-disc')
ok(discSess.artifact && discSess.artifact.name === 'algo_dashboard.html',
  '无事件但工作目录有看板时，懒发现自动补登成果')

// v1.5.2 反例：看板早于会话开始（= 上一轮跑出来的）→ 不得挂到本会话。
// 取证：上一轮 17:07 的 bull20_dashboard.html 被挂到 23:30 才开的会话上，
// 卡片"最终成果"页显示的是上一次的旧结论。
const staleCwd = join(TMP, 'stale-cwd')
mkdirSync(join(staleCwd, 'kexi_out'), { recursive: true })
writeFileSync(join(staleCwd, 'kexi_out', 'old_dashboard.html'),
  '<!doctype html><html><body>上一轮的旧看板</body></html>', 'utf8')
const old = Date.now() - 6 * 3600 * 1000
utimesSync(join(staleCwd, 'kexi_out', 'old_dashboard.html'), new Date(old), new Date(old))
const sStale = { id: 'sess-stale', cwd: staleCwd }
emit('session/event', sStale, { type: 'turn/start' })
lightKexi(sStale, 'L-stale')
r = await callRoute('/kexi-dashboard/activity')
const staleSess = r.json.sessions.find((s) => s.id === 'sess-stale')
ok(!staleSess.artifact, '上一轮的旧看板不挂到本会话（不展示旧结论）')

// v1.5.2 回归：成果**刷新**。旧写法 `if (!b.artifactId) discoverArtifact(b)` 让成果
// 一旦挂上就永不刷新——实测本轮 00:09 出了新看板 latest.json，卡片仍显示上一轮 17:07 的。
const refreshCwd = join(TMP, 'refresh-cwd')
mkdirSync(join(refreshCwd, 'kexi_out'), { recursive: true })
const sRef = { id: 'sess-refresh', cwd: refreshCwd }
emit('session/event', sRef, { type: 'turn/start' })
lightKexi(sRef, 'L-ref')
// ① 本轮先出一个看板
const refHtml1 = join(refreshCwd, 'kexi_out', 'run1_dashboard.html')
writeFileSync(refHtml1, '<!doctype html><html><body>本轮第一版</body></html>', 'utf8')
writeFileSync(join(refreshCwd, 'kexi_out', 'latest.json'),
  JSON.stringify({ schema: 'kexi.latest/1', dashboard: refHtml1 }), 'utf8')
r = await callRoute('/kexi-dashboard/activity')
ok(r.json.sessions.find((s) => s.id === 'sess-refresh').artifact.name === 'run1_dashboard.html',
  '成果初次挂载（本轮看板）')
// ② 本轮又出了新版看板并更新 latest.json → 卡片必须跟着换（mtime 变化即刷新）
await new Promise((r2) => setTimeout(r2, 20))
const refHtml2 = join(refreshCwd, 'kexi_out', 'run2_dashboard.html')
writeFileSync(refHtml2, '<!doctype html><html><body>本轮最终版</body></html>', 'utf8')
writeFileSync(join(refreshCwd, 'kexi_out', 'latest.json'),
  JSON.stringify({ schema: 'kexi.latest/1', dashboard: refHtml2 }), 'utf8')
r = await callRoute('/kexi-dashboard/activity')
const refSess = r.json.sessions.find((s) => s.id === 'sess-refresh')
ok(refSess.artifact && refSess.artifact.name === 'run2_dashboard.html',
  `本轮新看板出现后卡片会刷新（实际显示 ${refSess.artifact && refSess.artifact.name}）`)

// [A4] v1.4.6 权威产物标记 latest.json：目录里同族看板并存时，
// 懒发现必须读 latest.json 指认的那份，而不是按 mtime 猜。
// 真实场景：algo_dashboard.html（旧，mtime 更新）与 algo_dashboard_v2.html（权威）
// 并存 —— 纯 mtime 扫描会挑错。
head('[A4] latest.json 权威产物优先（v1.4.6）')
const latestCwd = join(TMP, 'latest-cwd')
mkdirSync(join(latestCwd, 'kexi_out'), { recursive: true })
const authoritative = join(latestCwd, 'kexi_out', 'algo_dashboard_v2.html')
const decoy = join(latestCwd, 'kexi_out', 'algo_dashboard.html')
writeFileSync(authoritative, '<!doctype html><html><body>权威看板 v2</body></html>', 'utf8')
writeFileSync(decoy, '<!doctype html><html><body>过时看板 v1</body></html>', 'utf8')
// 让诱饵的 mtime 更新 —— 纯 mtime 策略必定选错
const future = new Date(Date.now() + 60000)
try { utimesSync(decoy, future, future) } catch { }
writeFileSync(join(latestCwd, 'kexi_out', 'latest.json'), JSON.stringify({
  schema: 'kexi.latest/1', kind: 'pipeline', symbol: 'ALGOUSDT',
  dashboard: authoritative, report: join(latestCwd, 'kexi_out', 'algo_report.json'),
}), 'utf8')
const sLatest = { id: 'sess-latest', cwd: latestCwd }
emit('session/event', sLatest, { type: 'turn/start' })
lightKexi(sLatest, 'L-latest')
r = await callRoute('/kexi-dashboard/activity')
const latestSess = r.json.sessions.find((s) => s.id === 'sess-latest')
ok(latestSess.artifact && latestSess.artifact.name === 'algo_dashboard_v2.html',
  `latest.json 优先于 mtime（登记 ${latestSess.artifact && latestSess.artifact.name}）`)

// 失效 latest.json（指向不存在的文件）→ 回退 mtime 扫描，不报错
const fallbackCwd = join(TMP, 'latest-broken')
mkdirSync(join(fallbackCwd, 'kexi_out'), { recursive: true })
writeFileSync(join(fallbackCwd, 'kexi_out', 'algo_dashboard.html'),
  '<!doctype html><html><body>回退看板</body></html>', 'utf8')
writeFileSync(join(fallbackCwd, 'kexi_out', 'latest.json'),
  JSON.stringify({ schema: 'kexi.latest/1', dashboard: join(fallbackCwd, 'kexi_out', 'gone.html') }), 'utf8')
const sFallback = { id: 'sess-latest-broken', cwd: fallbackCwd }
emit('session/event', sFallback, { type: 'turn/start' })
lightKexi(sFallback, 'L-fb')
r = await callRoute('/kexi-dashboard/activity')
const fbSess = r.json.sessions.find((s) => s.id === 'sess-latest-broken')
ok(fbSess.artifact && fbSess.artifact.name === 'algo_dashboard.html',
  'latest.json 失效（指向不存在文件）→ 回退 mtime 扫描')

// 坏 JSON → 不抛异常，回退扫描
const badJsonCwd = join(TMP, 'latest-badjson')
mkdirSync(join(badJsonCwd, 'kexi_out'), { recursive: true })
writeFileSync(join(badJsonCwd, 'kexi_out', 'algo_dashboard.html'),
  '<!doctype html><html><body>坏标记回退</body></html>', 'utf8')
writeFileSync(join(badJsonCwd, 'kexi_out', 'latest.json'), '{ 坏 JSON', 'utf8')
const sBadJson = { id: 'sess-latest-badjson', cwd: badJsonCwd }
emit('session/event', sBadJson, { type: 'turn/start' })
lightKexi(sBadJson, 'L-badjson')
r = await callRoute('/kexi-dashboard/activity')
const bjSess = r.json.sessions.find((s) => s.id === 'sess-latest-badjson')
ok(bjSess.artifact && bjSess.artifact.name === 'algo_dashboard.html',
  'latest.json 是坏 JSON → 容错回退扫描')

// latest.json 指向非 HTML（防误登记）→ 回退扫描
const nonHtmlCwd = join(TMP, 'latest-nonhtml')
mkdirSync(join(nonHtmlCwd, 'kexi_out'), { recursive: true })
writeFileSync(join(nonHtmlCwd, 'kexi_out', 'algo_dashboard.html'),
  '<!doctype html><html><body>非HTML标记回退</body></html>', 'utf8')
writeFileSync(join(nonHtmlCwd, 'kexi_out', 'latest.json'),
  JSON.stringify({ schema: 'kexi.latest/1', dashboard: join(nonHtmlCwd, 'kexi_out', 'report.json') }), 'utf8')
const sNonHtml = { id: 'sess-latest-nonhtml', cwd: nonHtmlCwd }
emit('session/event', sNonHtml, { type: 'turn/start' })
lightKexi(sNonHtml, 'L-nonhtml')
r = await callRoute('/kexi-dashboard/activity')
const nhSess = r.json.sessions.find((s) => s.id === 'sess-latest-nonhtml')
ok(nhSess.artifact && nhSess.artifact.name === 'algo_dashboard.html',
  'latest.json 指向非 HTML → 不登记，回退扫描')

// ══════════════════════════════════════════════════════════════════════════
// A5. 团队动向 + 评估结论（v1.5.1）
// 取证（session-6d282c27 实测，2026-09-29）：team/member / team/message/queued /
// team/message/delivered / subagent/catalog 事件全部经 session/event 广播但
// 原实现不处理 → 卡片全员待命、评估结果不展示。此处用真实事件形状回归。
// ══════════════════════════════════════════════════════════════════════════
head('[A5] 团队动向 + 评估结论卡片（v1.5.1）')

const teamSess = { id: 'sess-team', cwd: 'C:/work/team-demo' }
// subagent/catalog：childId → 中文名登记（真实形状 {version:0, childId, mode, label}）
emit('session/event', teamSess, {
  type: 'subagent/catalog',
  data: { version: 0, childId: '5b04505c-5993-4231-87fb-dd0f5c8102b0', childCreatedAt: 1790666114345, mode: 'continuable', label: '数脉 · 行情取数与多面数据质检（团队唯一数据入口）' },
})
emit('session/event', teamSess, {
  type: 'subagent/catalog',
  data: { version: 0, childId: '70a0e273-1c66-47b7-891b-735c8133f126', childCreatedAt: 1790666114812, mode: 'continuable', label: '守拙 · 风险定级、实盘风控闸门与保活复盘' },
})
// team/member：provisioning → active（真实形状 {version:2, teamId, member:{...}}）
emit('session/event', teamSess, {
  type: 'team/member',
  data: { version: 2, teamId: 'sess-team', member: { id: '5b04505c-5993-4231-87fb-dd0f5c8102b0', name: 'shuma', description: '数脉 · 行情取数与多面数据质检（团队唯一数据入口）', provider: 'spawn', context: 'fresh', phase: 'provisioning' } },
})
emit('session/event', teamSess, {
  type: 'team/member',
  data: { version: 2, teamId: 'sess-team', member: { id: '5b04505c-5993-4231-87fb-dd0f5c8102b0', name: 'shuma', description: '数脉 · 行情取数与多面数据质检（团队唯一数据入口）', provider: 'spawn', context: 'fresh', phase: 'active' } },
})
emit('session/event', teamSess, {
  type: 'team/member',
  data: { version: 2, teamId: 'sess-team', member: { id: '70a0e273-1c66-47b7-891b-735c8133f126', name: 'shouzhuo', description: '守拙 · 风险定级、实盘风控闸门与保活复盘', provider: 'spawn', context: 'fresh', phase: 'active' } },
})
r = await callRoute('/kexi-dashboard/activity')
let tSess = r.json.sessions.find((s) => s.id === 'sess-team')
ok(tSess && tSess.team && tSess.team.members.length === 2, `team/member 登记名册（${tSess && tSess.team ? tSess.team.members.length : 0} 位成员）`)
ok(tSess.team.members.some((m) => m.name === 'shuma' && m.cn === '数脉' && m.phase === 'active'), '成员中文名映射（shuma→数脉，phase=active）')
// v1.6.1：建队节点由「每个成员一条」改为**合并成一条摘要**。
// 起因：kexi_run 每次都会自动建队，于是"没派活"的会话里也满屏
// 「数脉 已就位 / 守拙 已就位 / …」，看着像团队在干活（用户 2026-09-30 报）。
const tBuild = tSess.nodes.filter((n) => n.kind === 'team_build')
ok(tBuild.length === 1, `两次 team/member 事件合并为 1 条「团队就位」（实际 ${tBuild.length} 条）`)
ok(tBuild[0] && /数脉/.test(tBuild[0].detail || ''), '合并节点 detail 含成员名（数脉）')
ok(tBuild[0] && /守拙/.test(tBuild[0].detail || ''), '合并节点 detail 含成员名（守拙）')
ok(tBuild[0] && (tBuild[0].members || []).length === 2, '合并节点 members 数组含 2 位成员')
ok(!tSess.nodes.some((n) => n.kind === 'team_member'), '旧的逐成员 team_member 节点已不再产生')

// team/message/queued：主理人派活（真实形状 {version:2, teamId, message:{...}}）
emit('session/event', teamSess, {
  type: 'team/message/queued',
  data: {
    version: 2, teamId: 'sess-team',
    message: {
      id: 'team-message-bcb871e8-003d-4c1e-9910-652720347086',
      senderId: 'sess-team', senderName: 'lead',
      targetId: '5b04505c-5993-4231-87fb-dd0f5c8102b0',
      content: [{ type: 'text', text: '【任务】全市场技术初筛：跑 screener.py 取 Top100+放量池已收盘日线' }],
    },
  },
})
r = await callRoute('/kexi-dashboard/activity')
tSess = r.json.sessions.find((s) => s.id === 'sess-team')
ok(tSess.nodes.some((n) => n.kind === 'team_msg' && /主理人 → 数脉 派活/.test(n.label)), '派活节点"主理人 → 数脉 派活"（targetId 经 catalog 翻译为中文名）')
ok(tSess.nodes.find((n) => n.kind === 'team_msg' && n.status === 'running'), '派活节点 running（未送达时）')
// 成员→lead 回报（queued 里 sender 是成员）
emit('session/event', teamSess, {
  type: 'team/message/queued',
  data: {
    version: 2, teamId: 'sess-team',
    message: {
      id: 'team-message-b2cbe478-7a07-4c77-98e9-341427cd8958',
      senderId: '5b04505c-5993-4231-87fb-dd0f5c8102b0', senderName: 'shuma',
      targetId: 'sess-team',
      content: [{ type: 'text', text: '【数脉 · 候选池交付】候选 60 只，抓取失败 0，数据可用。' }],
    },
  },
})
// team/message/delivered：派活节点标完成
emit('session/event', teamSess, {
  type: 'team/message/delivered',
  data: { version: 2, teamId: 'sess-team', messageId: 'team-message-bcb871e8-003d-4c1e-9910-652720347086', targetId: '5b04505c-5993-4231-87fb-dd0f5c8102b0' },
})
r = await callRoute('/kexi-dashboard/activity')
tSess = r.json.sessions.find((s) => s.id === 'sess-team')
const dispatchNode = tSess.nodes.find((n) => n.kind === 'team_msg' && /派活/.test(n.label))
ok(dispatchNode && dispatchNode.status === 'ok' && dispatchNode.result === '已送达，成员已开始处理', 'delivered 把派活节点标完成（已送达）')
ok(tSess.nodes.some((n) => n.kind === 'team_msg' && /数脉 → 主理人 回报结论/.test(n.label)), '成员回报节点"数脉 → 主理人 回报结论"')
ok(tSess.team.members.find((m) => m.name === 'shuma' && m.lastNote && /全市场技术初筛/.test(m.lastNote)), '成员动向 lastNote 记录派活内容（卡片可见"正在干什么"）')

// assistant/message：长文本 = 最终评估结论（真实形状 data.message.content）
const evalText = '## 一、结论\n'.repeat(80) + '核心判断：20 只初选全数日线多头，但只有 15 只通过风控——整体是高位弱反弹，追高风险大于机会。仓位预算：总仓 ≤25% 净值。'
emit('session/event', teamSess, {
  type: 'assistant/message',
  data: { turn: 3, step: 9, message: { role: 'assistant', content: [{ type: 'text', text: evalText }], source: { kind: 'llm' }, id: 'msg-1' }, usage: {} },
})
r = await callRoute('/kexi-dashboard/activity')
tSess = r.json.sessions.find((s) => s.id === 'sess-team')
ok(tSess.evaluation && tSess.evaluation.text && tSess.evaluation.text.includes('核心判断'), 'assistant 长文本登记为评估结论 evaluation')
ok(tSess.evaluation && tSess.evaluation.text.length <= 1300, '评估结论裁剪到 ≤1300 字符（卡片可读）')

// send_message 工具调用翻译（主理人派活在 tool/call 层也可见）
emit('session/event', teamSess, {
  type: 'tool/call',
  data: { turn: 4, step: 1, callId: 'sm1', name: 'send_message', arguments: { target: 'zhibei', message: '【任务】复核近失候选，把关最终看涨名单' } },
})
r = await callRoute('/kexi-dashboard/activity')
tSess = r.json.sessions.find((s) => s.id === 'sess-team')
ok(tSess.nodes.some((n) => n.tool === 'send_message' && /派活给 指北/.test(n.label)), 'send_message 工具节点翻译为"派活给 指北"')

// 未知团队事件不崩（容错）
emit('session/event', teamSess, { type: 'team/task', data: { version: 2, teamId: 'sess-team', whatever: true } })
r = await callRoute('/kexi-dashboard/activity')
ok(r.code === 200, '未知团队事件不崩（默认分支忽略）')

// ══════════════════════════════════════════════════════════════════════════
// A6. v1.5.2 线上取证回归（会话 "挑选20个近期看涨的虚拟"，2026-09-29 23:3x）
// 下面 4 条全部来自真实 session 事件形状/真实 /activity 响应，不是推测。
// ══════════════════════════════════════════════════════════════════════════
head('[A6] v1.5.2 线上取证回归（卡片无响应 / 标签信息丢失）')

// A6-1：tool/call 的 arguments 是 **JSON 字符串**（真实形状），不是对象。
// 旧实现只认对象 → 标签退化成"派活给 成员"，kexi_run 不显示币种。
emit('session/event', teamSess, {
  type: 'tool/call',
  data: { turn: 5, step: 1, callId: 'sm2', name: 'send_message', arguments: '{"target":"shuma","message":"【任务】复核候选名单"}' },
})
emit('session/event', teamSess, {
  type: 'tool/call',
  data: { turn: 5, step: 2, callId: 'kr1', name: 'kexi_run', arguments: '{"mode":"pipeline","symbol":"BTCUSDT","interval":"1d"}' },
})
r = await callRoute('/kexi-dashboard/activity')
tSess = r.json.sessions.find((s) => s.id === 'sess-team')
ok(tSess.nodes.some((n) => n.tool === 'send_message' && /派活给 数脉/.test(n.label)),
  'arguments 为 JSON 字符串时也能解析（派活给 数脉）')
ok(tSess.nodes.some((n) => n.tool === 'kexi_run' && /BTCUSDT/.test(n.label)),
  'kexi_run 标签带出币种（字符串参数同样解析）')

// A6-2：等待成员期间（wait_agent 静默期）不能显示"空闲"。
// 真实参数：wait_agent timeout_ms=600000（10 分钟），期间无任何 session 事件。
const waitSess = { id: 'sess-wait', cwd: 'C:/work/wait-demo' }
emit('session/event', waitSess, { type: 'turn/start', data: { turn: 1 } })
emit('session/event', waitSess, { type: 'tool/call', data: { turn: 1, step: 1, callId: 'w1', name: 'send_message', arguments: '{"target":"shuma","message":"干活"}' } })
emit('session/event', waitSess, { type: 'tool/call', data: { turn: 1, step: 1, callId: 'w2', name: 'wait_agent', arguments: '{"timeout_ms":600000}' } })
// 把节点时间戳人为回拨 5 分钟，模拟"已经等了一会儿"
{
  const b = r.json.sessions.find((s) => s.id === 'sess-wait')
  void b
}
const waitBucketAge = 5 * 60 * 1000
// 通过重新发射一个 turn/end 之外的节点不便改时间，改为直接断言"新事件后仍 busy"
// ——真正的超窗行为由 A6-3 的成员跨会话用例覆盖。
r = await callRoute('/kexi-dashboard/activity')
let wSess = r.json.sessions.find((s) => s.id === 'sess-wait')
ok(wSess.busy === true, '主理人在 wait_agent 等待期间 busy=true（卡片不再显示空闲）')
ok(wSess.current && /等待成员回报/.test(wSess.current.label), 'current 指向"等待成员回报"')

// A6-3：成员忙碌态跨会话可见（真实：数脉在**自己的** session 桶里干活）
const memberSess = { id: 'mem-shuma-1', cwd: 'C:/work/wait-demo' }
emit('session/event', teamSess, {
  type: 'team/member',
  data: { version: 2, teamId: 'sess-team', member: { id: 'mem-shuma-1', name: 'shuma', description: '数脉 · 行情取数', phase: 'active' } },
})
emit('session/event', memberSess, { type: 'turn/start', data: { turn: 1 } })
emit('session/event', memberSess, { type: 'tool/call', data: { turn: 1, step: 1, callId: 's1', name: 'kexi_run', arguments: '{"mode":"script","script":"screener.py"}' } })
r = await callRoute('/kexi-dashboard/activity')
tSess = r.json.sessions.find((s) => s.id === 'sess-team')
const shumaCard = tSess.team.members.find((m) => m.name === 'shuma')
ok(shumaCard && shumaCard.busy === true, '成员在**自己会话**里干活 → 主理人名册同步点亮（跨会话）')
ok(shumaCard && shumaCard.lastNote && /screener/.test(shumaCard.lastNote), '成员动向显示其真实在跑什么（screener.py）')
void waitBucketAge


head('[B] 设置端点')
r = await callRoute('/kexi-dashboard/settings')
ok(r.code === 200 && r.json.ok === true, 'GET /settings 200 ok')
const d = r.json.settings
ok(d.showProgressPopup === true, '默认 showProgressPopup=true')
ok(Array.isArray(d.cliPriority) && d.cliPriority[0] === 'agy', '默认 CLI 优先级 agy 优先')
ok(d.screenerWorkers === 6, `默认 screenerWorkers=6（${d.screenerWorkers}）`)
r = await callRoute('/kexi-dashboard/settings', 'POST', { maxEventNodes: 999, screenerTop: 1, riskPerTrade: 99 })
ok(r.json.settings.maxEventNodes === 200, `越界 maxEventNodes 收敛到 200（${r.json.settings.maxEventNodes}）`)
ok(r.json.settings.screenerTop === 10, `越界 screenerTop 收敛到 10（${r.json.settings.screenerTop}）`)
// screenerWorkers must be clamped to 1..16 (too high = exchange rate-limit risk)
r = await callRoute('/kexi-dashboard/settings', 'POST', { screenerWorkers: 999 })
ok(r.json.settings.screenerWorkers === 16, `越界 screenerWorkers 收敛到 16（${r.json.settings.screenerWorkers}）`)
r = await callRoute('/kexi-dashboard/settings', 'POST', { screenerWorkers: 0 })
ok(r.json.settings.screenerWorkers === 1, `screenerWorkers=0 收敛到 1（${r.json.settings.screenerWorkers}）`)
ok(r.json.settings.maxWeight === 0.25, '未提交字段保持默认')
ok(existsSync(SETTINGS), '设置落盘到 kexi-settings.json')
ok(JSON.parse(readFileSync(SETTINGS, 'utf8')).maxEventNodes === 200, '落盘内容 = 收敛后的值')

const low = await callRoute('/kexi-dashboard/settings', 'POST', { maxEventNodes: 3 })
ok(low.json.settings.maxEventNodes === 20, `下限收敛到 20（${low.json.settings.maxEventNodes}）`)
const flood = { id: 'flood', cwd: 'C:/work/flood' }
// v1.6.1：用 kexi 工具，否则整个 flood 会话会被会话级过滤剔掉，
// maxEventNodes 裁剪就无从验证了。
for (let i = 0; i < 30; i++) emit('session/event', flood, { type: 'tool/call', data: { callId: 'f' + i, name: 'kexi_run', arguments: { symbol: 'SYM' + i } } })
r = await callRoute('/kexi-dashboard/activity')
ok(r.json.sessions.find((s) => s.id === 'flood').nodes.length === 20, 'maxEventNodes=20 裁剪生效')
await callRoute('/kexi-dashboard/settings', 'POST', { maxEventNodes: 60 })
r = await callRoute('/kexi-dashboard/activity')
ok(r.json.sessions.find((s) => s.id === 'flood').nodes.length === 30, '上调 60 后返回全部 30 个')
ok((await callRoute('/kexi-dashboard/settings', 'POST', null)).code === 200, '空 body POST 不崩')

head('[C] 研究状态灯回归 + 容错')
emit('kexi/mode', { active: true })
emit('kexi/status', { snapshot: { cwd: 'C:/work/kexi-demo', name: 'kexi-demo', phase: 'running', symbol: 'ETHUSDT', step: '取数', stepIndex: 1, stepTotal: 6 } })
r = await callRoute('/kexi-dashboard/status')
ok(r.json.presetActive === true && r.json.running === 1, '研判灯 presetActive/running 正常')
ok(r.json.sessions[0].symbol === 'ETHUSDT', '研判灯显示币种')
r = await callRoute('/kexi-dashboard/activity')
const wk = r.json.sessions.find((s) => s.id === 'kexi:C:/work/kexi-demo')
ok(wk && wk.nodes.some((n) => /第 1\/6 步/.test(n.label)), 'pipeline 步骤同步进活动流')
try {
  emit('session/event', null, null)
  emit('session/event', sess, { type: 'tool/call', data: {} })
  emit('session/event', sess, { type: 'tool/result', data: { callId: 'nonexistent' } })
  emit('kexi/status', 'not-an-object')
  emit('subagent/start', null)
  ok(true, '坏输入不抛异常')
} catch (e) { ok(false, '坏输入抛异常: ' + e.message) }

// ══════════════════════════════════════════════════════════════════════════
// D/E. kexi_cli 本地 CLI 编排
// ══════════════════════════════════════════════════════════════════════════
head('[D] kexi_cli 本地 CLI 编排（需求 2）')

const tools = new Map()
const calls = []
function makeTools(names) {
  tools.clear()
  // 返回形态对齐真实桥工具 buildResult：文本在 response 而非 digest（D10 回归点）。
  for (const n of names) {
    tools.set(n, { name: n, execute: async (a) => { calls.push({ tool: n, args: a }); return { ok: true, status: 'SUCCESS', response: '同意。形态偏多，结论与指标自洽。', conversationId: 'c-' + n, jobId: 'job-' + n } } })
  }
}
const pctx = {
  tools: { register: (t) => { tools.set(t.name, t); return () => tools.delete(t.name) }, get: (n) => tools.get(n) },
  systemPrompt: { section: () => () => { }, getSectionOrder: () => 2810 },
  subprocess: {}, interval: () => () => { },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } },
  on: () => () => { }, emit: () => { },
}
const call = (name, args) => { const t = tools.get(name); return t ? t.execute(args, {}) : null }

// 场景 D1：装了 agy + codebuddy，未装 mimo
writeFileSync(SETTINGS, JSON.stringify({ cliPriority: ['agy', 'codebuddy', 'mimo'], cliEnabled: { agy: true, codebuddy: true, mimo: true } }), 'utf8')
makeTools(['agy_run', 'agy_status', 'codebuddy_run', 'codebuddy_status'])
const p1 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=1')
p1.apply(pctx)
ok(tools.has('kexi_cli'), '注册 kexi_cli')
ok(tools.has('kexi_run') && tools.has('kexi_validate') && tools.has('kexi_dashboard'), '原有三工具仍注册（回归）')
r = await call('kexi_cli', { action: 'list' })
ok(r.available.join(',') === 'agy,codebuddy', `探测到 agy,codebuddy（${r.available.join(',')}）`)
ok(!r.available.includes('mimo'), '未安装的 mimo 不出现')

calls.length = 0
r = await call('kexi_cli', { action: 'run', prompt: '复核 ETHUSDT 顶背离', context: 'RSI=72' })
ok(r.ok && r.cli === 'agy', '缺省按优先级选 agy')
ok(calls[0].tool === 'agy_run' && String(calls[0].args.prompt).includes('RSI=72'), 'prompt+context 透传且调对工具')
ok(calls[0].args.background === true, '默认后台执行')
ok(r.jobId === 'job-agy_run' && String(r.next).includes('job_output'), 'jobId 回传 + next 指引收集')
r = await call('kexi_cli', { action: 'run', cli: 'codebuddy', prompt: 'x' })
ok(r.cli === 'codebuddy', '可指定 codebuddy')

// 关键：指定不可用 CLI 时必须报错，不得静默换源
const before = calls.length
r = await call('kexi_cli', { action: 'run', cli: 'mimo', prompt: 'x' })
ok(r.ok === false && String(r.error).includes('mimo'), '指定未安装的 mimo → 明确报错（不静默换源）')
ok(calls.length === before, '报错时确实没有调用任何 CLI')
ok(String(r.next || '').includes('谎称'), '附带"不得谎称"纪律')
r = await call('kexi_cli', { action: 'run', cli: 'bogus', prompt: 'x' })
ok(r.ok === false && String(r.error).includes('未知'), '未知 CLI 名 → 报错')
r = await call('kexi_cli', { action: 'run' })
ok(r.ok === false && String(r.error).includes('prompt'), '缺 prompt → 报错')

// 场景 D2：设置改优先级 + 禁用
writeFileSync(SETTINGS, JSON.stringify({ cliPriority: ['codebuddy', 'agy', 'mimo'], cliEnabled: { agy: false, codebuddy: true, mimo: true } }), 'utf8')
makeTools(['agy_run', 'codebuddy_run'])
const p2 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=2')
p2.apply(pctx)
r = await call('kexi_cli', { action: 'list' })
ok(r.available.join(',') === 'codebuddy', `禁用 agy 后只剩 codebuddy（${r.available.join(',')}）`)

head('[E] 无 CLI 时诚实降级 + 坏配置容错')
makeTools([])
const p3 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=3')
p3.apply(pctx)
r = await call('kexi_cli', { action: 'list' })
ok(r.ok === true && r.available.length === 0, '无 CLI 时 list 返回空列表（不报错）')
ok(String(r.digest).includes('未探测到') && String(r.next).includes('不要声称'), '如实说明 + 禁止假装有 CLI 协助')
r = await call('kexi_cli', { action: 'run', prompt: 'x' })
ok(r.ok === false && String(r.error).includes('没有可用'), '无 CLI 时 run 如实失败')

writeFileSync(SETTINGS, '{ 坏 JSON', 'utf8')
makeTools(['agy_run'])
const p4 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=4')
p4.apply(pctx)
r = await call('kexi_cli', { action: 'list' })
ok(r.ok === true && r.available.includes('agy'), '坏设置文件回退默认（仍探测到 agy）')

// ══════════════════════════════════════════════════════════════════════════
// F. kexi_run 流水线内置 CLI 自动交叉验证（v1.2.1）
// ══════════════════════════════════════════════════════════════════════════
head('[F] kexi_run 自动 CLI 交叉验证')

// 伪造 subprocess：每个脚本返回能过步骤检查的 stdout JSON，让 pipeline 走到第 5 步
const FAKE_OUT = {
  'fetch_klines.py': { ok: true, count: 200, last_date: '2026-09-27' },
  'indicators.py': { ok: true, rsi14: 55.1 },
  'unlock_schedule.py': { ok: true },
  'assemble_report.py': { ok: true, next: 'done' },
  'validate_report.py': { ok: true, changed: 0 },
  'compact_report.py': { ok: true },
  'dashboard.py': { ok: true },
}
const scriptCalls = []
function fakeSubprocess() {
  return {
    spawn: ({ argv }) => {
      const script = argv.find((a) => a && String(a).endsWith('.py'))
      const name = script ? script.split(/[\\/]/).pop() : 'unknown.py'
      scriptCalls.push(name)
      // assemble_report.py 伪造其 --out 产物（真实写盘），让 crosscheck 回写有落点
      if (name === 'assemble_report.py') {
        const i = argv.indexOf('--out')
        if (i >= 0 && argv[i + 1]) {
          mkdirSync(dirname(String(argv[i + 1])), { recursive: true })
          writeFileSync(String(argv[i + 1]), JSON.stringify({ schema_version: 'kexi.report/1', symbol: 'TEST', verdict: 'hold' }), 'utf8')
        }
      }
      const out = FAKE_OUT[name] || { ok: true }
      return {
        done: Promise.resolve({ exitCode: 0 }),
        collected: {
          stdout: { readFrom: () => ({ text: JSON.stringify(out) }) },
          stderr: { readFrom: () => ({ text: '' }) },
        },
      }
    },
  }
}
// 让 runPython 一次选中候选：KEXI_PYTHON 指向假路径（spawn 被伪造，无实际执行）
process.env.KEXI_PYTHON = 'C:/fake/python.exe'
const FROOT = join(TMP, 'f-root')
mkdirSync(FROOT, { recursive: true })
const fakeExec = { signal: undefined }
const fctx = {
  // workspaceRoot 优先读 ctx.get('sandboxPolicy').workspaceRoot —— 指到临时目录，
  // 避免测试把 kexi_out 写进插件源码目录
  get: (n) => (n === 'sandboxPolicy' ? { workspaceRoot: FROOT } : undefined),
  tools: { register: (t) => { tools.set(t.name, t); return () => tools.delete(t.name) }, get: (n) => tools.get(n) },
  systemPrompt: { section: () => () => { }, getSectionOrder: () => 2810 },
  subprocess: fakeSubprocess(),
  interval: () => () => { },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } },
  on: () => () => { }, emit: () => { },
}

// F1：autoCliCrosscheck 开 + agy 可用 → 第5步调 CLI，结论进 report.json
writeFileSync(SETTINGS, JSON.stringify({ autoCliCrosscheck: true, cliPriority: ['agy', 'codebuddy'], cliEnabled: { agy: true, codebuddy: true } }), 'utf8')
makeTools(['agy_run', 'agy_status'])
const f1 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=f1')
f1.apply(fctx)
calls.length = 0; scriptCalls.length = 0
r = await call('kexi_run', { pipeline: true, symbol: 'BTCUSDT', interval: '1d' })
ok(r && r.ok !== false, `流水线应成功（实际 ${JSON.stringify(String(r && r.error)).slice(0, 120)}）`)
ok(calls.some((c) => c.tool === 'agy_run' && String(c.args.prompt).includes('独立复核员')), '第5步自动派 agy 复核（prompt 含角色设定）')
ok(calls.every((c) => c.args.background === false), '交叉验证走前台（同步骤内联等待）')
ok(scriptCalls.includes('dashboard.py') && scriptCalls.includes('compact_report.py'), '第6/7步仍执行（不阻塞看板）')
ok(String(r.digest || '').includes('CLI 复核'), `digest 带 CLI 复核行（${String(r.digest).slice(0, 80)}…）`)
const rep = JSON.parse(readFileSync(join(FROOT, 'kexi_out', 'BTCUSDT_1d_report.json'), 'utf8'))
ok(rep.crosscheck && rep.crosscheck.status === 'ok' && rep.crosscheck.cli === 'agy', 'crosscheck 写入 report.json（来源=agy）')
ok(rep.crosscheck && typeof rep.crosscheck.note === 'string' && rep.crosscheck.note.length > 0, 'crosscheck.note 有内容')
ok(rep.crosscheck && rep.crosscheck.rule && String(rep.crosscheck.rule).includes('脚本数据'), 'crosscheck 注明"脚本数据为准"规则')

// F2：无任何 CLI → 如实 skipped，不失败
makeTools([])
scriptCalls.length = 0
const f2 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=f2')
f2.apply(fctx)
r = await call('kexi_run', { pipeline: true, symbol: 'ETHUSDT', interval: '1d' })
ok(r && r.ok !== false, '无 CLI 时流水线仍成功（诚实降级）')
const rep2 = JSON.parse(readFileSync(join(FROOT, 'kexi_out', 'ETHUSDT_1d_report.json'), 'utf8'))
ok(rep2.crosscheck && rep2.crosscheck.status === 'skipped', `无 CLI → crosscheck.status=skipped（${rep2.crosscheck && rep2.crosscheck.reason}）`)
ok(!String(r.digest || '').includes('CLI 复核(agy)'), 'digest 不虚报 CLI 复核')

// F3：设置关闭 autoCliCrosscheck → disabled 且不调 CLI
writeFileSync(SETTINGS, JSON.stringify({ autoCliCrosscheck: false }), 'utf8')
makeTools(['agy_run'])
scriptCalls.length = 0
const f3 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=f3')
f3.apply(fctx)
r = await call('kexi_run', { pipeline: true, symbol: 'BNBUSDT', interval: '1d' })
const rep3 = JSON.parse(readFileSync(join(FROOT, 'kexi_out', 'BNBUSDT_1d_report.json'), 'utf8'))
ok(rep3.crosscheck && rep3.crosscheck.status === 'disabled', '关闭设置 → status=disabled')

// F4：CLI 抛错/超时 → skipped，流水线不失败
writeFileSync(SETTINGS, JSON.stringify({ autoCliCrosscheck: true, cliCrosscheckTimeoutSec: 30 }), 'utf8')
tools.set('agy_run', { name: 'agy_run', execute: async () => { throw new Error('boom') } })
const f4 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=f4')
f4.apply(fctx)
r = await call('kexi_run', { pipeline: true, symbol: 'SOLUSDT', interval: '1d' })
ok(r && r.ok !== false, 'CLI 复核失败不拖垮流水线')
const rep4 = JSON.parse(readFileSync(join(FROOT, 'kexi_out', 'SOLUSDT_1d_report.json'), 'utf8'))
ok(rep4.crosscheck && rep4.crosscheck.status === 'skipped' && String(rep4.crosscheck.reason).includes('boom'), `CLI 异常 → skipped(原因记录)`)
delete process.env.KEXI_PYTHON

// ══════════════════════════════════════════════════════════════════════════
// G. kexi_ensure_team 幂等预置原生持久团队（v1.4.0）
// ══════════════════════════════════════════════════════════════════════════
head('[G] kexi_ensure_team 持久团队预置')

const leadAgent = { id: 'lead-id', name: 'lead' }
const rosterState = []
const spawnReqs = []
const fakeTeamSvc = {
  listMembers() {
    return [
      { id: 'lead-id', name: 'lead', role: 'lead', status: 'inactive' },
      ...rosterState.map((m) => ({ id: m.childId, name: m.name, role: 'teammate', status: 'inactive', description: m.description })),
    ]
  },
  async spawnTeammate(agent, req) {
    spawnReqs.push(req)
    rosterState.push({ childId: 'c-' + req.name, name: req.name, description: req.description })
    return { name: req.name }
  },
}
const groot = join(TMP, 'g-root')
mkdirSync(groot, { recursive: true })
const gctx = {
  get: (n) => {
    if (n === 'agentTeams') return fakeTeamSvc
    if (n === 'sandboxPolicy') return { workspaceRoot: groot }
    return undefined
  },
  tools: { register: (t) => { tools.set(t.name, t); return () => tools.delete(t.name) }, get: (n) => tools.get(n) },
  systemPrompt: { section: () => () => { }, getSectionOrder: () => 2810 },
  subprocess: {}, interval: () => () => { },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } },
  on: () => () => { }, emit: () => { },
}
const gexec = { agent: leadAgent, signal: undefined }
const callG = (args) => tools.get('kexi_ensure_team').execute(args, gexec)

// G1：默认（持久团队开）→ 创建 5 位，请求形态 fresh+spawn
writeFileSync(SETTINGS, JSON.stringify({ persistentTeam: true }), 'utf8')
const g1 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=g1')
g1.apply(gctx)
ok(tools.has('kexi_ensure_team'), '注册 kexi_ensure_team')
r = await callG({})
ok(r.ok === true, 'ensure_team 成功（默认持久团队开）')
ok(rosterState.length === 5, `创建 5 位持久成员（v1.5.9 加证伪红队，实际 ${rosterState.length}）`)
ok(spawnReqs.every((q) => q.provider === 'spawn' && q.context === 'fresh'), '每位按 fresh + provider spawn 创建')
// ⚠ 这里原本是 `q.name === 'shuma' && q.name === 'shuma'` —— 同一个条件写了两遍的
// **恒真断言**：它既验不了数脉（第 5 位若是前 4 位的重复也照样绿），也发现不了
// 红队成员根本没被创建。改为逐个核对五位成员各被 spawn 恰好一次。
const EXPECT_MEMBERS = ['shuma', 'zhibei', 'wangchao', 'shouzhuo', 'zhengfu']
const spawnedNames = spawnReqs.map((q) => q.name)
for (const nm of EXPECT_MEMBERS) {
  ok(spawnedNames.filter((n) => n === nm).length === 1,
    `成员 ${nm} 被创建恰好一次（实际 ${spawnedNames.filter((n) => n === nm).length} 次）`)
}
ok(spawnedNames.length === EXPECT_MEMBERS.length && new Set(spawnedNames).size === EXPECT_MEMBERS.length,
  `五位成员互不重复且无多余 spawn（实际 ${JSON.stringify(spawnedNames)}）`)
ok(spawnedNames.includes('zhengfu'), '证伪（红队）确实被创建——只数个数会在成员重复时假绿')
ok(String(r.digest).includes('证伪'), '返回名册含证伪中文名')
ok(String(r.digest).includes('主理人') && String(r.digest).includes('数脉'), '返回名册含主理人 + 中文名')

// G2：再次调用 → 幂等跳过，不重复 spawn
const beforeSpawn = spawnReqs.length
r = await callG({})
ok(r.ok && spawnReqs.length === beforeSpawn, `再次调用幂等（spawn 数仍 ${spawnReqs.length}）`)
ok(String(r.digest).includes('均已存在'), 'digest 说明成员均已存在')

// G3：persistentTeam=false → 不 spawn，走按需
rosterState.length = 0; spawnReqs.length = 0
writeFileSync(SETTINGS, JSON.stringify({ persistentTeam: false }), 'utf8')
const g3 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=g3')
g3.apply(gctx)
r = await callG({})
ok(r.ok && rosterState.length === 0, 'persistentTeam=false → 不创建成员')
ok(String(r.digest).includes('已在设置中关闭'), '说明走按需路径')

// G4：无 agentTeams 服务 → 诚实降级，不报错
writeFileSync(SETTINGS, JSON.stringify({ persistentTeam: true }), 'utf8')
const g4ctx = Object.assign({}, gctx, { get: (n) => (n === 'sandboxPolicy' ? { workspaceRoot: groot } : undefined) })
const g4 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=g4')
g4.apply(g4ctx)
r = await tools.get('kexi_ensure_team').execute({}, gexec)
ok(r.ok === true && String(r.digest).includes('未启用原生 Agent Teams'), '无 agentTeams 服务时诚实降级（不报错）')

// ══════════════════════════════════════════════════════════════════════════
// H. kexi_run 确定性兜底建队（v1.4.6）
// 取证：docs/backlog1-team-launch-finding.md —— 建队原先只靠提示词第 0 步，
// 模型跳过则面板静默只剩主理人。这里断言代码级兜底真的生效、且幂等/守开关。
// ══════════════════════════════════════════════════════════════════════════
head('[H] kexi_run 自动兜底建队（v1.4.6）')

// 带假 subprocess 的 ctx：让 pipeline 能跑完，digest 才有 teamNote 落点。
const hroot = join(TMP, 'h-root')
mkdirSync(hroot, { recursive: true })
function hctxFor(svc) {
  return {
    get: (n) => {
      if (n === 'agentTeams') return svc
      if (n === 'sandboxPolicy') return { workspaceRoot: hroot }
      return undefined
    },
    tools: { register: (t) => { tools.set(t.name, t); return () => tools.delete(t.name) }, get: (n) => tools.get(n) },
    systemPrompt: { section: () => () => { }, getSectionOrder: () => 2810 },
    subprocess: fakeSubprocess(),
    interval: () => () => { },
    effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } },
    on: () => () => { }, emit: () => { },
  }
}
function newTeamSvc() {
  const roster = []
  const reqs = []
  return {
    roster, reqs,
    listMembers: () => [
      { id: 'lead-id', name: 'lead', role: 'lead', status: 'inactive' },
      ...roster.map((m) => ({ id: 'c-' + m.name, name: m.name, role: 'teammate', status: 'inactive' })),
    ],
    spawnTeammate: async (agent, req) => { reqs.push(req); roster.push({ name: req.name, description: req.description }); return { name: req.name } },
  }
}

// H1：lead 会话首次 kexi_run（**没**先调 kexi_ensure_team）→ 自动补建 5 位
writeFileSync(SETTINGS, JSON.stringify({ persistentTeam: true, autoCliCrosscheck: false }), 'utf8')
const svcH = newTeamSvc()
const h1ctx = hctxFor(svcH)
const h1 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=h1')
h1.apply(h1ctx)
const leadExec = { agent: { id: 'lead-id', name: 'lead', session: { id: 'sess-h1' } }, signal: undefined }
r = await tools.get('kexi_run').execute({ mode: 'pipeline', symbol: 'ADAUSDT', interval: '1d' }, leadExec)
ok(svcH.roster.length === 5, `未调 ensure_team 直接跑 kexi_run → 自动补建 5 位（实际 ${svcH.roster.length}）`)
ok(svcH.reqs.every((q) => q.provider === 'spawn' && q.context === 'fresh'), '兜底建队形态与 ensure_team 一致（fresh + spawn）')
ok(String(r.digest || '').includes('自动补建'), `digest 如实告知已自动补建（${String(r.digest).slice(-60)}）`)

// H2：同会话第二次 kexi_run → 不再重复探测/派发（每会话只试一次）
const beforeH = svcH.reqs.length
await tools.get('kexi_run').execute({ mode: 'pipeline', symbol: 'DOTUSDT', interval: '1d' }, leadExec)
ok(svcH.reqs.length === beforeH, `同会话第二次只兜底一次（spawn 数仍 ${svcH.reqs.length}）`)

// H3：先调 kexi_ensure_team 再跑 → 兜底幂等（不重复建队）
const svcH3 = newTeamSvc()
const h3 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=h3')
h3.apply(hctxFor(svcH3))
const execH3 = { agent: { id: 'lead-id', name: 'lead', session: { id: 'sess-h3' } }, signal: undefined }
await tools.get('kexi_ensure_team').execute({}, execH3)
ok(svcH3.roster.length === 5, '显式 ensure_team 先建 5 位')
const afterEnsure = svcH3.reqs.length
r = await tools.get('kexi_run').execute({ mode: 'pipeline', symbol: 'LINKUSDT', interval: '1d' }, execH3)
ok(svcH3.reqs.length === afterEnsure, `ensure_team 之后再跑 kexi_run 不重复建队（spawn 数仍 ${svcH3.reqs.length}）`)

// H4：成员会话（有 parentSession）→ 不建队（否则 spawnTeammate 抛 TEAM_LEAD_REQUIRED）
const svcH4 = newTeamSvc()
const h4 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=h4')
h4.apply(hctxFor(svcH4))
const memberExec = { agent: { id: 'm-1', name: 'shuma', session: { id: 'sess-h4', header: { parentSession: 'lead-id' } } }, signal: undefined }
await tools.get('kexi_run').execute({ mode: 'pipeline', symbol: 'XRPUSDT', interval: '1d' }, memberExec)
ok(svcH4.roster.length === 0, '成员会话不建队（避免 TEAM_LEAD_REQUIRED）')

// H5：persistentTeam=false → 兜底也守开关，不建队
writeFileSync(SETTINGS, JSON.stringify({ persistentTeam: false, autoCliCrosscheck: false }), 'utf8')
const svcH5 = newTeamSvc()
const h5 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=h5')
h5.apply(hctxFor(svcH5))
await tools.get('kexi_run').execute({ mode: 'pipeline', symbol: 'ATOMUSDT', interval: '1d' },
  { agent: { id: 'lead-id', name: 'lead', session: { id: 'sess-h5' } }, signal: undefined })
ok(svcH5.roster.length === 0, 'persistentTeam=false → 兜底不建队（守开关）')

// H6：agentTeams 服务异常 → 兜底失败不阻断研判
writeFileSync(SETTINGS, JSON.stringify({ persistentTeam: true, autoCliCrosscheck: false }), 'utf8')
const boomSvc = { listMembers: () => [], spawnTeammate: async () => { throw new Error('team boom') } }
const h6 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=h6')
h6.apply(hctxFor(boomSvc))
r = await tools.get('kexi_run').execute({ mode: 'pipeline', symbol: 'AVAXUSDT', interval: '1d' },
  { agent: { id: 'lead-id', name: 'lead', session: { id: 'sess-h6' } }, signal: undefined })
ok(r && r.ok === true, '建队异常时 pipeline 仍成功（兜底不阻断研判）')
ok(String(r.digest || '').includes('自动补建持久团队失败'), 'digest 如实记录建队失败原因')

// ══════════════════════════════════════════════════════════════════════════
// I. kexi_news 新闻面单次探测 + 降级标记（v1.4.6）
// 取证：retro backlog #4 —— 外部检索 401/被拦时成员反复尝试才放弃，白费等待与
// token。这里断言：探测只跑一次、失败即 news_available=false、按 cwd 共享、禁止重试。
// ══════════════════════════════════════════════════════════════════════════
head('[I] kexi_news 新闻通道探测（v1.4.6）')

const nrootBase = join(TMP, 'n-root')
const newsSearchCalls = []
let newsSearchBehavior = { kind: 'ok' }
function nrootFor(i) {
  const d = join(nrootBase + '-' + i)
  mkdirSync(join(d, 'kexi_out'), { recursive: true })
  return d
}
function nctxFor(root) {
  return {
    get: (n) => (n === 'sandboxPolicy' ? { workspaceRoot: root } : undefined),
    tools: {
      register: (t) => { tools.set(t.name, t); return () => tools.delete(t.name) },
      get: (n) => {
        if (n === 'web_search_multi' || n === 'web_search') {
          return {
            name: n,
            execute: async (a) => {
              newsSearchCalls.push({ tool: n, args: a })
              if (newsSearchBehavior.kind === 'ok') return { answer: '比特币今日震荡，市场情绪中性', sources: [{ url: 'https://x.com', title: 't' }] }
              if (newsSearchBehavior.kind === 'blocked') return { error: '401 Unauthorized', sources: [] }
              if (newsSearchBehavior.kind === 'throw') throw new Error('network down')
              return { sources: [] }
            },
          }
        }
        return undefined
      },
    },
    systemPrompt: { section: () => () => { }, getSectionOrder: () => 2810 },
    subprocess: {}, interval: () => () => { },
    effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } },
    on: () => () => { }, emit: () => { },
  }
}

// I1：通道可用 → 单次探测成功
writeFileSync(SETTINGS, JSON.stringify({ newsProbeTimeoutSec: 3 }), 'utf8')
newsSearchBehavior = { kind: 'ok' }; newsSearchCalls.length = 0
const i1 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=i1')
i1.apply(nctxFor(nrootFor(1)))
ok(tools.has('kexi_news'), '注册 kexi_news')
r = await tools.get('kexi_news').execute({}, { agent: { id: 'lead', session: { id: 'sess-i1' } } })
ok(r.ok === true && r.news_available === true, '通道可用 → news_available=true')
ok(newsSearchCalls.length === 1, `只探测一次（实际 ${newsSearchCalls.length}）`)
ok(String(r.digest).includes('可用'), 'digest 说明通道可用')

// I2：401/被拦 → news_available=false + 显式标记缺失 + 不重试
newsSearchBehavior = { kind: 'blocked' }; newsSearchCalls.length = 0
const i2 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=i2')
i2.apply(nctxFor(nrootFor(2)))
r = await tools.get('kexi_news').execute({}, { agent: { id: 'lead', session: { id: 'sess-i2' } } })
ok(r.ok === true && r.news_available === false, '401/被拦 → news_available=false（不报错）')
ok(String(r.digest).includes('新闻面缺失') && String(r.digest).includes('禁止反复尝试'), '显式标记「新闻面缺失」+ 禁止重试')
ok(String(r.digest).includes('401'), 'reason 记录 401 事实')
// 同会话第二次调用 → 走缓存，不再探测
const callsAfter = newsSearchCalls.length
r = await tools.get('kexi_news').execute({}, { agent: { id: 'lead', session: { id: 'sess-i2' } } })
ok(newsSearchCalls.length === callsAfter, '同会话第二次调用不再探测（走缓存）')

// I3：工具抛异常 → 同样降级标记
newsSearchBehavior = { kind: 'throw' }; newsSearchCalls.length = 0
const i3 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=i3')
i3.apply(nctxFor(nrootFor(3)))
r = await tools.get('kexi_news').execute({}, { agent: { id: 'lead', session: { id: 'sess-i3' } } })
ok(r.ok === true && r.news_available === false && String(r.digest).includes('新闻面缺失'), '工具异常 → 降级标记缺失')

// I4：无任何检索工具 → news_available=false + 原因
const i4ctx = {
  get: (n) => (n === 'sandboxPolicy' ? { workspaceRoot: nrootFor(4) } : undefined),
  tools: { register: (t) => { tools.set(t.name, t); return () => tools.delete(t.name) }, get: () => undefined },
  systemPrompt: { section: () => () => { }, getSectionOrder: () => 2810 },
  subprocess: {}, interval: () => () => { },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } },
  on: () => () => { }, emit: () => { },
}
const i4 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=i4')
i4.apply(i4ctx)
r = await tools.get('kexi_news').execute({}, { agent: { id: 'lead', session: { id: 'sess-i4' } } })
ok(r.ok === true && r.news_available === false && String(r.digest).includes('未注册'), '无检索工具 → 标记缺失并说明原因')

// I5：落盘 news_status.json 供同 cwd 成员共享（第二个实例读到缓存，不重复探测）
newsSearchBehavior = { kind: 'blocked' }; newsSearchCalls.length = 0
const i5 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=i5')
i5.apply(nctxFor(nrootFor(5)))
r = await tools.get('kexi_news').execute({}, { agent: { id: 'lead', session: { id: 'sess-i5' } } })
const nfile = r.out_files && r.out_files[0]
ok(!!nfile && existsSync(nfile), '探测结论落盘 news_status.json')
const i5b = await import(pathToFileURL(PLUGIN_MOD).href + '?t=i5b')
i5b.apply(nctxFor(nrootFor(5)))
const callsBefore = newsSearchCalls.length
r = await tools.get('kexi_news').execute({}, { agent: { id: 'other-member', session: { id: 'sess-i5b' } } })
ok(r.ok && r.news_available === false, '同 cwd 另一成员读到共享结论')
ok(newsSearchCalls.length === callsBefore, '共享结论生效：新成员不重复探测')

// ── [J] workspaceRoot 解析回归（v1.4.7 实战 bug）─────────────────────────────
// 背景：旧实现读 ctx.get('sandboxPolicy').workspaceRoot —— 那是"**无会话 cwd** 时
// 的部署兜底根"（本机 = DSH 后端 lib 目录），不是会话工作区。实战后果：kexi_run
// 相对路径 FileNotFoundError、kexi_validate 解析到后端目录、kexi_dashboard 把
// latest.json 写到后端目录 → host 读不到权威标记 → 回退 mtime → 旧看板错挂。
// 正确 API：sandboxPolicy.resolve({session}) → { workspaceRoot }（会话 cwd 边界）。
// 真实部署里该 Service 只暴露 resolve()，**没有**可用的 workspaceRoot 语义。

// J1：真实部署形状 —— 只有 resolve()，且它返回会话 cwd；部署根指向诱饵目录
const jRoot = join(TMP, 'j-session-cwd')
const jDecoy = join(TMP, 'j-deploy-fallback')
mkdirSync(jRoot, { recursive: true }); mkdirSync(jDecoy, { recursive: true })
let jResolveSaw = null
const jctx = {
  get: (n) => (n === 'sandboxPolicy' ? {
    workspaceRoot: jDecoy,                      // 部署兜底根（诱饵：绝不该被选中）
    resolve: (req) => { jResolveSaw = req; return { mode: 'workspace-write', workspaceRoot: jRoot } },
  } : undefined),
  tools: { register: (t) => { tools.set(t.name, t); return () => tools.delete(t.name) }, get: (n) => tools.get(n) },
  systemPrompt: { section: () => () => { }, getSectionOrder: () => 2810 },
  subprocess: fakeSubprocess(),
  interval: () => () => { },
  effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } },
  on: () => () => { }, emit: () => { },
}
const j1 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=j1')
j1.apply(jctx)
const jSession = { id: 'sess-j1', header: { cwd: jRoot } }
// kexi_news 会按 root 写盘，用它观察 root 解析结果（无副作用、无需 spawn 成功）
r = await tools.get('kexi_news').execute({}, { agent: { id: 'lead', session: jSession } })
ok(jResolveSaw !== null && jResolveSaw.session === jSession, 'J1 走 sandboxPolicy.resolve({session}) 而非裸 workspaceRoot')
const jOut = (r.out_files || [])[0] || ''
ok(jOut.includes(jRoot.split(/[\\/]/).pop()), `J1 产物落在会话 cwd（实际 ${jOut}）`)
ok(!jOut.includes('j-deploy-fallback'), 'J1 没有误用部署兜底根（旧实现的 bug）')

// J2：宿主只有裸 workspaceRoot、没有 resolve() → 回退 session.header.cwd
const jRoot2 = join(TMP, 'j-header-cwd')
mkdirSync(jRoot2, { recursive: true })
const jctx2 = Object.assign({}, jctx, {
  get: (n) => (n === 'sandboxPolicy' ? { workspaceRoot: jDecoy } : undefined),
})
const j2 = await import(pathToFileURL(PLUGIN_MOD).href + '?t=j2')
j2.apply(jctx2)
r = await tools.get('kexi_news').execute({}, { agent: { id: 'lead', session: { id: 'sess-j2', header: { cwd: jRoot2 } } } })
const jOut2 = (r.out_files || [])[0] || ''
ok(jOut2.includes('j-header-cwd'), `J2 无 resolve() 时回退 header.cwd（实际 ${jOut2}）`)
ok(!jOut2.includes('j-deploy-fallback'), 'J2 不误用部署兜底根')

// ── 清理 ─────────────────────────────────────────────────────────────────
try { rmSync(TMP, { recursive: true, force: true }) } catch { }

console.log('\n' + '='.repeat(60))
if (fail === 0) { console.log(`RUNTIME: ALL PASS  (${pass} 通过 / 0 失败)`); console.log('='.repeat(60)); process.exit(0) }
console.log(`RUNTIME: FAILED  (${pass} 通过 / ${fail} 失败)`)
console.log('='.repeat(60))
process.exit(1)
