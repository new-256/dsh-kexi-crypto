// logs/build/assert-label-v1923.mjs — v1.9.23 委派身份标签门禁（回归团）
//
// 为什么要有它（别删这段）：
//   HANDOFF §6.1 记录过一次「测试全绿而线上失效」：`tool/call` 的 data.arguments 是
//   **JSON 字符串**，早期 host 只认对象 → 卡片上成员标签全部退化成通用值。
//   v1.9.23 又把 6 个委派工具收敛成 1 个 `subagent`，身份信号从「工具名」变成
//   `arguments.agent_preset`——**两处变更叠在同一个位置**，正是最容易再次静默退化的点。
//   所以这里对 5 种输入各写一条**字面量**断言（不许用 includes('指北') 这类放水匹配）：
//     a. 正常成员委派（tool/call 路径）      → label === `委派 指北·技术指标`
//     b. 坏 JSON args                        → label 含 `参数解析失败`，detail 回原始串
//     c. 合法但缺 agent_preset               → label 含 `继承主理人预设`
//     d. agent_preset 是未知 id              → label 含 `未知预设`，detail 回显 id
//     e. 事件路径 subagent/start             → label === `委派成员 指北·技术指标`
//   a 与 e 前缀不同（`委派 ` vs `委派成员 `），**两条都断言**，任一路径坏掉都要红。
//
// 用法: node logs/build/assert-label-v1923.mjs
// 退出码: 0 = 全部通过；1 = 有失败项
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync, mkdirSync, rmSync } from 'node:fs'

const ROOT = dirname(dirname(dirname(fileURLToPath(import.meta.url))))
const HOST_MOD = join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/index.mjs')
const MEMBERS_MOD = join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/members.mjs')

// 隔离落盘：settings 与 DSH_HOME 都指向 logs/build/_tmp，绝不在仓库根造 .kexi-test-tmp 之类杂物
const TMP = join(ROOT, 'logs/build/_tmp/label-harness')
if (existsSync(TMP)) rmSync(TMP, { recursive: true, force: true })
mkdirSync(TMP, { recursive: true })
process.env.KEXI_SETTINGS_FILE = join(TMP, 'kexi-settings.json')
process.env.DSH_HOME = TMP

const members = await import(pathToFileURL(MEMBERS_MOD).href)
const host = await import(pathToFileURL(HOST_MOD).href)

let pass = 0, fail = 0
const ok = (cond, msg, extra) => {
  if (cond) { pass++; console.log('  ✓ ' + msg) }
  else { fail++; console.log('  ✗ FAIL: ' + msg); if (extra !== undefined) console.log('      actual: ' + JSON.stringify(extra)) }
}

// ── 假 ctx（与 scripts/test-runtime.mjs 同款，无需 DSH / 网络）────────────────
const handlers = new Map()
const routes = new Map()
const fakeWs = { register: (r) => { routes.set(r.path, r); return () => routes.delete(r.path) } }
// v1.9.23 起 host 在 apply() 里要求 agentPresets 服务存在（设计 B 的 roster 过滤），
// 缺失会**直接 throw 拒绝半残运行**（这是对的，规则 1）。所以给一个形状正确的假服务：
// 只需 `remoteExportList()` 返回 `{ presets: [...] }`（见 lib/preset-roster.mjs 的契约检查），
// 其余方法本用例用不到，但一并给出以免将来 host 扩用时炸得莫名其妙。
const fakePresets = {
  async remoteExportList() { return { presets: [] } },
  async list() { return [] },
  async readDocument() { return {} },
  async resolve() { return null },
  async mount() { return null },
}

const fakeCtx = {
  on(name, fn) { if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(fn); return () => { } },
  get(name) { return name === 'agentPresets' ? fakePresets : undefined },
  inject(_deps, cb) { cb({ get: (n) => (n === 'webServer' ? fakeWs : (n === 'agentPresets' ? fakePresets : undefined)), effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } } }) },
  effect(fn) { const d = fn(); return typeof d === 'function' ? d : () => { } },
}
const emit = (name, ...args) => { for (const fn of (handlers.get(name) || [])) fn(...args) }

host.apply(fakeCtx)
if (!routes.has('/kexi-dashboard/activity')) {
  console.error('FATAL: 未注册 /kexi-dashboard/activity，harness 前提不成立')
  process.exit(2)
}

function callRoute(path) {
  return new Promise((resolve) => {
    const listeners = {}
    const req = { method: 'GET', url: path, on(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn) }, destroy() { } }
    let acc = ''
    const res = {
      _code: 0,
      writeHead(c) { this._code = c },
      end(s) { acc += (s || ''); let j = null; try { j = JSON.parse(acc) } catch { } resolve({ code: this._code, json: j, raw: acc }) },
    }
    const r = routes.get('/kexi-dashboard/activity')
    if (!r) return resolve({ code: 0, json: null })
    r.handler(req, res)
  })
}

// 取某会话内指定 callId 的节点（找不到返回 null，让断言显式失败而不是抛错）
async function nodeOf(sessionId, callId) {
  const r = await callRoute('/kexi-dashboard/activity?sessionId=' + encodeURIComponent(sessionId))
  const sess = (r.json?.sessions || []).find((s) => s.id === sessionId)
  if (!sess) return { missingSession: true, raw: r.raw.slice(0, 400) }
  const node = (sess.nodes || []).find((n) => n.callId === callId)
  return node || { missingNode: true, labels: (sess.nodes || []).map((n) => n.label) }
}

// 成员真值一律从 members.mjs 取，不在测试里另抄中文（映射改了必须跟着红）
const zhibei = members.memberOfPreset('kexi-member-technical')
if (!zhibei) { console.error('FATAL: members.mjs 里没有 kexi-member-technical'); process.exit(2) }
const MEMBER_LABELS = members.KEXI_MEMBERS.map((m) => `委派 ${m.cn}·${m.short}`)
const notGeneric = (label) => !MEMBER_LABELS.includes(label) && label !== '委派成员'

console.log('members 真值: ' + MEMBER_LABELS.join(' | '))
console.log('')

// 每个用例用独立会话，并先点一次 kexi_run 让桶进卡片（避免被 v1.6.1 会话级过滤误伤）
function newSession(id) {
  const s = { id, cwd: 'C:/work/kexi-label-' + id, header: { agentPreset: 'kexi-crypto' } }
  emit('session/event', s, { type: 'tool/call', data: { callId: id + '-light', name: 'kexi_run', arguments: { symbol: 'BTCUSDT' } } })
  return s
}

// ── a. 正常成员委派：tool/call + JSON 字符串 ────────────────────────────────
console.log('[a] tool/call 正常成员委派（JSON 字符串 args）')
{
  const s = newSession('label-a')
  const args = JSON.stringify({ agent_preset: 'kexi-member-technical', description: '算 BTC 指标', prompt: '算一下 BTC 技术指标' })
  emit('session/event', s, { type: 'tool/call', data: { turn: 1, step: 1, callId: 'a1', name: 'subagent', arguments: args } })
  const n = await nodeOf(s.id, 'a1')
  const want = `委派 ${zhibei.cn}·${zhibei.short}`
  ok(n && n.label === want, `label === '${want}'`, n && n.label)
  ok(n && n.agentPreset === 'kexi-member-technical', `节点 agentPreset === 'kexi-member-technical'`, n && n.agentPreset)
  ok(n && n.member === zhibei.key, `节点 member === '${zhibei.key}'`, n && n.member)
}

// ── b. 坏 JSON ─────────────────────────────────────────────────────────────
console.log('[b] tool/call 坏 JSON args')
{
  const s = newSession('label-b')
  const raw = '{"agent_preset":"kexi-member-technical",'   // 故意截断
  emit('session/event', s, { type: 'tool/call', data: { turn: 1, step: 1, callId: 'b1', name: 'subagent', arguments: raw } })
  const n = await nodeOf(s.id, 'b1')
  ok(n && typeof n.label === 'string' && n.label.includes('参数解析失败'), `label 含 '参数解析失败'`, n && n.label)
  ok(n && notGeneric(n.label), 'label 不等于任何成员标签、也不等于「委派成员」', n && n.label)
  ok(n && n.detail === raw.slice(0, 80), 'detail === 原始 args 前 80 字符', n && n.detail)
}

// ── c. 合法但缺 agent_preset（继承主理人预设）──────────────────────────────
console.log('[c] tool/call 缺 agent_preset')
{
  const s = newSession('label-c')
  const args = JSON.stringify({ description: '并行调研一轮' })
  emit('session/event', s, { type: 'tool/call', data: { turn: 1, step: 1, callId: 'c1', name: 'subagent', arguments: args } })
  const n = await nodeOf(s.id, 'c1')
  ok(n && typeof n.label === 'string' && n.label.includes('继承主理人预设'), `label 含 '继承主理人预设'`, n && n.label)
  ok(n && notGeneric(n.label), 'label 不等于任何成员标签、也不等于「委派成员」', n && n.label)
}

// ── d. 未知 preset id ──────────────────────────────────────────────────────
console.log('[d] tool/call 未知 preset id')
{
  const s = newSession('label-d')
  const unknownId = 'kexi-member-renamed'
  const args = JSON.stringify({ agent_preset: unknownId, description: '改了名但映射没跟' })
  emit('session/event', s, { type: 'tool/call', data: { turn: 1, step: 1, callId: 'd1', name: 'subagent', arguments: args } })
  const n = await nodeOf(s.id, 'd1')
  ok(n && typeof n.label === 'string' && n.label.includes('未知预设'), `label 含 '未知预设'`, n && n.label)
  ok(n && n.detail === unknownId, `detail 回显未知 id 原文 '${unknownId}'`, n && n.detail)
  ok(n && notGeneric(n.label), 'label 不等于任何成员标签、也不等于「委派成员」', n && n.label)
}

// ── e. 事件路径 subagent/start（前缀与 a 不同：`委派成员 `）──────────────────
console.log('[e] 事件路径 subagent/start')
{
  const parentId = 'label-e-parent'
  const childId = 'label-e-child'
  const s = { id: parentId, cwd: 'C:/work/kexi-label-e', header: { agentPreset: 'kexi-crypto' } }
  // 子会话先报身份与父子关系（真实链路：session.header.origin==='subagent' → rememberChildSession）
  const child = { id: childId, cwd: 'C:/work/kexi-label-e', header: { origin: 'subagent', parentSession: parentId, agentPreset: 'kexi-member-technical', cwd: 'C:/work/kexi-label-e' } }
  emit('session/event', child, { type: 'turn/start', data: { turn: 1 } })
  emit('subagent/start', { id: childId, runId: 'run-e', provider: 'stub', local: true })

  const r = await callRoute('/kexi-dashboard/activity?sessionId=' + encodeURIComponent(parentId))
  const sess = (r.json?.sessions || []).find((x) => x.id === parentId)
  const nodes = sess ? (sess.nodes || []) : []
  const n = nodes.filter((x) => x.kind === 'subagent').pop()
  const want = `委派成员 ${zhibei.cn}·${zhibei.short}`
  ok(!!n, '父会话桶里出现 subagent 节点', nodes.map((x) => x.label))
  ok(n && n.label === want, `label === '${want}'（前缀是「委派成员 」，与 a 的「委派 」不同）`, n && n.label)
  ok(n && n.agentPreset === 'kexi-member-technical', `节点 agentPreset === 'kexi-member-technical'`, n && n.agentPreset)
}

console.log('')
console.log(`通过 ${pass} / 失败 ${fail}`)
console.log(fail === 0 ? 'ASSERT-LABEL: PASS' : `ASSERT-LABEL: FAIL (${fail})`)
process.exit(fail === 0 ? 0 : 1)
