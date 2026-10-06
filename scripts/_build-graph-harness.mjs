#!/usr/bin/env node
// 生成关系图 UI 夹具：用**真 host 代码**跑出真实 graph JSON，
// 再拼一个在浏览器里加载**真 client.js + 真 React** 的独立页面。
//
// 为什么绕这一大圈：静态断言只能证明"代码写了 graph 字段"，
// 证不了"图真的画得出来、没挤爆、文字没被裁掉、动画不报错"。
// 那是渲染层的事，只有真浏览器说了算。
//
// 用法：node scripts/_build-graph-harness.mjs   → 产出 .kexi-test-tmp/graph-harness.html
import { pathToFileURL } from 'node:url'
import { readFileSync, writeFileSync, mkdirSync, existsSync, rmSync, copyFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const HOST = join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/index.mjs')
const CLIENT = join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/client.js')
const OUT_DIR = join(ROOT, '.kexi-test-tmp')
const REACT = join(process.env.TEMP || process.env.TMPDIR || '.', 'kexi-graph-harness/node_modules')

for (const p of [join(REACT, 'react/umd/react.development.js'),
  join(REACT, 'react-dom/umd/react-dom.development.js')]) {
  if (!existsSync(p)) { console.error('缺少 React：' + p + '\n先在 %TEMP%\\kexi-graph-harness 执行 npm i react@18 react-dom@18'); process.exit(2) }
}

// ── 1. 用真 host 跑出真实 graph ────────────────────────────────────────────
const routes = new Map(), handlers = new Map()
const fakeWs = { register: (r) => { routes.set(r.path, r); return () => routes.delete(r.path) } }
const ctx = {
  on(n, fn) { if (!handlers.has(n)) handlers.set(n, []); handlers.get(n).push(fn); return () => { } },
  inject(_d, cb) { cb({ get: (n) => (n === 'webServer' ? fakeWs : undefined), effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } } }) },
  effect(fn) { const d = fn(); return typeof d === 'function' ? d : () => { } },
}
process.env.DSH_HOME = OUT_DIR
process.env.KEXI_SETTINGS_FILE = join(OUT_DIR, 'kexi-settings.json')
mkdirSync(OUT_DIR, { recursive: true })
const { apply } = await import(pathToFileURL(HOST).href)
apply(ctx)
const emit = (ev, ...a) => { for (const fn of (handlers.get(ev) || [])) fn(...a) }
const call = (p) => new Promise((res) => {
  const req = { method: 'GET', url: p, on() { }, destroy() { } }
  let acc = ''; const r = { writeHead(c) { this._c = c }, end(s) { acc += s || ''; let j = null; try { j = JSON.parse(acc) } catch { }; res(j) } }
  routes.get(p).handler(req, r)
})
const tres = (id, p) => ({ type: 'tool/result', data: { message: { role: 'tool', toolCallId: id, source: { callId: id }, content: [{ type: 'text', text: JSON.stringify(p) }], isError: false } } })

// 真实五人团队，覆盖各种边界：活跃/忙碌/失败/纯推测/长文本
const MEMBERS = [
  { id: 'm-shuma', name: 'shuma', cn: '数脉', role: '行情取数 · 多面数据质检（团队唯一数据入口）', script: 'fetch_klines.py' },
  { id: 'm-zhibei', name: 'zhibei', cn: '指北', role: '指标计算 · 形态量价', script: 'indicators.py' },
  { id: 'm-wangchao', name: 'wangchao', cn: '望潮', role: '三情景概率推演 · 证伪条件', script: 'entry_plan.py' },
  { id: 'm-shouzhuo', name: 'shouzhuo', cn: '守拙', role: '风险定级 · 实盘风控闸门与保活复盘', script: 'cex_risk.py' },
  { id: 'm-zhengfu', name: 'zhengfu', cn: '证伪', role: '唱反调 · 攻击结论前提', script: 'ablation_compare.py' },
]
const lead = { id: 'sess-G', cwd: 'C:/w/kexi' }

for (const m of MEMBERS) {
  emit('session/event', lead, { type: 'team/member', data: { teamId: 'T', member: { id: m.id, name: m.name, description: m.role, provider: 'spawn', context: 'fresh', phase: m.name === 'zhengfu' ? 'failed' : 'active' } } })
  // 每个成员在自己会话里跑自己的脚本（只有还在跑的和刚跑完的会被判 busy）
  const s = { id: m.id, cwd: 'C:/w/kexi' }
  emit('session/event', s, { type: 'tool/call', data: { callId: 'c-' + m.name, name: 'kexi_run', arguments: { mode: 'script', script: m.script, symbol: 'ETHUSDT' } } })
  if (m.name !== 'wangchao') emit('session/event', s, tres('c-' + m.name, { ok: true, digest: '完成' }))
}
// 派活：数脉最长文本，用来试文字是否撑爆节点
emit('session/event', lead, { type: 'team/message/queued', data: { teamId: 'T', message: { id: 'x1', senderId: 'sess-G', senderName: 'lead', targetId: 'm-shuma', content: [{ type: 'text', text: '拉全市场 300 个标的的日线并做成交量质检，输出放量池 Top100' }] } } })
emit('session/event', lead, { type: 'team/message/queued', data: { teamId: 'T', message: { id: 'x2', senderId: 'm-shuma', senderName: 'shuma', targetId: 'sess-G', content: [{ type: 'text', text: '300 根日线已就绪' }] } } })
emit('session/event', lead, { type: 'tool/call', data: { callId: 'L1', name: 'kexi_run', arguments: { symbol: 'ETHUSDT' } } })
emit('session/event', lead, tres('L1', { ok: true, digest: '研判完成' }))

const act = await call('/kexi-dashboard/activity')
const graph = (act.sessions.find((s) => s.id === 'sess-G') || {}).graph
if (!graph) { console.error('真 host 没产出 graph —— 夹具无法生成'); process.exit(1) }

// 边界场景：0 成员（空态）与 1 成员（布局退化）
const none = { state: 'idle', busySessions: 0, sessions: [{ id: 'sess-empty', name: '空团队', busy: false, nodes: [], graph: null, team: null, stats: { nodes: 0, tools: 0, cli: 0 } }] }
const one = { state: 'running', busySessions: 1, sessions: [{ id: 'sess-one', name: '单人', busy: true, nodes: [], artifact: null, team: { members: [] }, stats: { nodes: 3, tools: 2, cli: 0 }, graph: { nodes: [{ id: 'lead', cn: 'K析', role: '主理人 · 统筹编排', cat: 'orchestrate', catLabel: '编排派活', catFrom: 'tool', state: 'active', busy: true, activity: '跑研判流水线 ETHUSDT', isLead: true, t: Date.now() }], edges: [] } }] }
// ⚠ 形状必须和真接口一致，但 sessions 刻意给**空数组**：
//   Indicator 的渲染门槛是 `hasData = Array.isArray(s.sessions) && s.sessions.length`，
//   走 hasData 分支时会渲染一排 Pill（每会话一颗 .kexi-ind），把忙碌灯盖住，
//   点下去开的是**状态弹窗**（无标签页）而不是进度弹窗——夹具会变得点不准。
//   只留 busySessions 触发「⟳ K析」忙碌灯 → showProg → ProgressPopup（带关系图标签页）。
const status = { state: 'running', activeSessions: 1, running: 1, sessions: [], presetActive: true, lastModeAt: Date.now(), version: act.version || '1.6.4' }
// /activity 也要带 version（卡片版本徽标读的是这份）
act.version = act.version || status.version

// ── 2. 拼页面：真 client.js + 真 React + 真 CSS ───────────────────────────
const clientSrc = readFileSync(CLIENT, 'utf8')
// 内联进 <script> 时必须转义，否则源码里的 </script> 会提前闭合标签
const safe = clientSrc.replace(/<\/script/gi, '<\\/script')

const html = `<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>K析 关系图 · 渲染自检</title>
<style>
/* DSH 设计令牌的近似值：真实页面由宿主注入变量，这里给等价回退，
   免得 harness 里的颜色全是空的、看不出对比度问题 */
:root{
  --dsw-alias-border-l1:#2a2f3a; --dsw-alias-border-l2:#3a4150;
  --dsw-alias-bg-layer-1:#1a1e26; --dsw-alias-label-primary:#e6e9ef;
  --dsw-alias-label-secondary:#9aa3b2;
  --dsw-static-blue-500:#3b82f6; --dsw-static-green-500:#22c55e;
  --dsw-alias-state-error-primary:#ef4444;
}
html,body{margin:0;background:#0f1218;color:var(--dsw-alias-label-primary);
  font-family:system-ui,"Segoe UI","Microsoft YaHei",sans-serif}
#root{padding:16px}
#hud{font:12px ui-monospace,Consolas,monospace;color:#9aa3b2;white-space:pre;
  background:#0a0d12;border:1px solid #2a2f3a;border-radius:8px;padding:10px;margin-bottom:12px}
</style></head>
<body>
<div id="hud">booting…</div>
<div id="root"></div>
<script>${readFileSync(join(REACT, 'react/umd/react.development.js'), 'utf8')}</script>
<script>${readFileSync(join(REACT, 'react-dom/umd/react-dom.development.js'), 'utf8')}</script>
<script>window.__ModuleLoader__ = { load: function (m) { window.__kexiModule = m; } };</script>
<script>${safe}</script>
<script>
(function () {
  var hud = document.getElementById('hud');
  var SCEN = (location.hash || '#main').slice(1);
  var DATA = { main: ${JSON.stringify(act)}, empty: ${JSON.stringify(none)}, one: ${JSON.stringify(one)} }[SCEN];
  window.__SCEN = SCEN;
  // 拦截轮询：把真实 host 产出的数据喂进去，不去打网络
  window.fetch = function (url) {
    if (String(url).indexOf('/activity') >= 0) return Promise.resolve({ ok: true, json: function () { return Promise.resolve(DATA); } });
    if (String(url).indexOf('/status') >= 0) return Promise.resolve({ ok: true, json: function () { return Promise.resolve(${JSON.stringify(status)}); } });
    return Promise.resolve({ ok: false, status: 404, json: function () { return Promise.resolve({}); } });
  };
  try {
    var mod = window.__kexiModule.factory(function (n) { if (n === 'react') return window.React; throw new Error('未预期的依赖: ' + n); });
    var captured = {};
    mod.apply({ inject: function (deps, cb) { cb({
      get: function (n) { return n === 'slots' ? { register: function (e, c) { captured[e.name] = c; return function () {}; }, inject: function () {} } : undefined; },
      slots: { register: function (e, c) { captured[e.name] = c; return function () {}; }, inject: function () {} }
    }); } });
    window.__captured = captured;
    var Comp = captured['conversation.session.header.utilities'];
    if (!Comp) throw new Error('没抓到 Indicator 组件，实际抓到: ' + Object.keys(captured).join(','));
    window.ReactDOM.createRoot(document.getElementById('root')).render(
      window.React.createElement(Comp, { sessionId: (DATA.sessions[0] || {}).id })
    );
    hud.textContent = 'ok  场景=' + SCEN + '  会话=' + ((DATA.sessions[0]||{}).id) +
      '  graph节点=' + (((DATA.sessions[0]||{}).graph||{}).nodes||[]).length;
    window.__HARNESS_READY = true;
  } catch (e) {
    hud.textContent = 'HARNESS ERROR: ' + (e && e.message || e);
    window.__HARNESS_ERROR = String(e && e.stack || e);
    window.__HARNESS_READY = true;
  }
})();
</script>
</body></html>`

const out = join(OUT_DIR, 'graph-harness.html')
writeFileSync(out, html, 'utf8')
console.log('夹具已生成：' + out)
console.log('真实 graph 节点 ' + graph.nodes.length + ' 个 / 边 ' + graph.edges.length + ' 条')
for (const n of graph.nodes) console.log('  ' + n.cn + '  ' + n.cat + '(' + n.catFrom + ')  busy=' + n.busy + '  ' + (n.activity || ''))
