#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.6.2 回归：团队关系图（谁加入了工作 / 在干什么）。

用户需求："可以使用动态关系图展示谁加入了工作，在干什么
（数据搜集、数据分析或者其他的工作）"。

这个功能最危险的地方不是画不出来，而是**分类悄悄说错**——
把正在做风控的成员标成"数据分析"，图看着挺漂亮，用户却据此判断团队状态。
所以本测试主要盯三件事：
  ① 分类**从成员正在跑的脚本推导**，不按角色硬编码（同一人不同轮次类别会变）；
  ② 分类可信度分两档（tool=真看到在跑什么 / role=只是按角色推测），前端要能区分；
  ③ **说不准时归"其他工作"，不猜**。

全程离线：Node 侧用测试夹具喂事件，不联网。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, '..')
HOST = os.path.join(REPO, 'home-plugin', 'dsh-kexi-crypto', 'lib', 'index.mjs')
CLIENT = os.path.join(REPO, 'home-plugin', 'dsh-kexi-crypto', 'lib', 'client.js')

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


for p in (HOST, CLIENT):
    if not os.path.exists(p):
        print('缺少文件：%s' % p)
        sys.exit(1)
host = open(HOST, encoding='utf-8').read()
client = open(CLIENT, encoding='utf-8').read()

# ══════════════ 一、分类表：覆盖用户点名的三类 ══════════════
ck('① 有 classifyWork 分类函数', 'function classifyWork' in host, '')
ck('① 有按角色兜底的 classifyByRole', 'function classifyByRole' in host, '')
ck('① 用户点名的「数据搜集」在分类表里', "'collect', '数据搜集'" in host, '')
ck('① 用户点名的「数据分析」在分类表里', "'analyze', '数据分析'" in host, '')
ck('① 有「其他工作」兜底类', re.search(r"other:\s*'其他工作'", host) is not None, '')
for cat in ('falsify', 'orchestrate', 'risk'):
    ck('① 分类表含 %s（风控/质疑/编排）' % cat, "'%s'," % cat in host, '')

# ══════════════ 二、分类必须从"正在跑的脚本"推导 ══════════════
# 这条是核心：数脉这一轮可能在取 fetch_klines（数据搜集），
# 下一轮可能在跑 news_watch（仍是搜集），但如果他改跑 indicators 就该是分析。
# 按角色硬编码会把"此刻"答错。
ck('② 分类表里是脚本名（fetch_klines/indicators 等）而非成员名',
   'fetch_klines' in host and 'indicators' in host, '')
ck('② 分类表**不含成员代号**（shuma/zhibei…），否则会退化成角色硬编码',
   not re.search(r"WORK_CATS\s*=\s*\[", host) or
   not re.search(r"\['collect'[^]]*shuma", host), '')
# 顺序：falsify 必须排在 analyze 之前（backtest 两者都命中）
m = re.search(r"const WORK_CATS = \[([\s\S]*?)\n  \]", host)
ck('② 分类表有顺序约束：falsify 排在 analyze 之前（backtest 归属不歧义）',
   bool(m) and m.group(1).find("'falsify'") < m.group(1).find("'analyze'"),
   'falsify 与 analyze 顺序反了 → 证伪跑 backtest 会被标成"数据分析"')

# ══════════════ 三、分类可信度分两档 ══════════════
ck('③ 节点带 catFrom 字段（tool=实测 / role=推测）', 'catFrom' in host, '')
ck('③ catFrom 由 tool 是否存在决定', "catFrom: tool ? 'tool' : 'role'" in host, '')
ck('③ 空 tool 不猜：classifyWork("") 返回 other',
   re.search(r"if \(!t\) return 'other'", host) is not None, '')
ck('③ 未识别的工具归 other 而非硬塞某一类',
   re.search(r"for \(const pair of WORK_CATS\) if \(pair\[2\]\.test\(t\)\) return pair\[0\]\s*\n\s*return 'other'",
             host) is not None, '')

# ══════════════ 四、图结构 ══════════════
ck('④ 有 buildGraph', 'function buildGraph' in host, '')
ck('④ 图含主理人节点（isLead）', 'isLead: true' in host, '')
ck('④ 图含 edges', 'edges: []' in host or 'const edges = []' in host, '')
ck('④ 边由结构化字段 edgeFrom/edgeTo 得出，**不解析 label 字符串**',
   'n.edgeFrom' in host and 'n.edgeTo' in host, '')
ck('④ team_msg 节点带 edgeFrom/edgeTo/edgeRel',
   'edgeFrom:' in host and 'edgeTo:' in host and 'edgeRel:' in host, '')
ck('④ 派活 rel=dispatch / 回报 rel=report', "'dispatch'" in host and "'report'" in host, '')
ck('④ 边两端不在图里时不画（避免指向虚空的线）', 'if (!a || !b) return null' in client, '')
ck('④ 每个成员只保留最近一次交互（否则历史会把图淹没）',
   'lastEdge' in host, '')

# ══════════════ 五、诚实性：说不准就不说 ══════════════
ck('⑤ 空名册时 graph 返回 null（不画空图让人以为坏了）',
   '? buildGraph(b, now) : null' in host, '')
ck('⑤ 客户端空态有明确说明而不是空白',
   '本会话尚未组建团队' in client, '')
ck('⑤ 「按角色推测」的节点用虚线+降透明度与实测区分',
   'kexi-guess' in client and 'kexi-guess' in host or 'stroke-dasharray' in client, '')
ck('⑤ 图例说明了虚线圈的含义（不把推测伪装成确定）',
   '按角色推测' in client, '')
# 最关键的一条：**绝不编造**——没有观测就说没有
ck('⑤ 无观测时 activity 留空，不填占位文本',
   re.search(r"activity: \(live && live\.note\) \|\| m\.lastNote \|\| null", host) is not None, '')

# ══════════════ 六、渲染可用性 ══════════════
ck('⑥ 手绘 SVG（无外部图库依赖）', 'react.createElement("svg"' in client, '')
ck('⑥ 有 viewBox 与无障碍标签',
   'viewBox' in client and 'aria-label' in client, '')
ck('⑥ 忙碌成员有呼吸环动画', 'kexi-gpulse' in client, '')
ck('⑥ 活跃边有流动虚线动画', 'kexi-gflow' in client, '')
ck('⑥ SVG 里的小字有文字版兜底（可读屏/可复制）',
   'kexi-glegend' in client and client.count('kexi-glegend') >= 2, '')
ck('⑥ 图是弹窗的一个标签页（不挤占原有视图）', 'tabBtn("graph"' in client, '')
ck('⑥ 标签页切换分支接上了 graph', 'tab === "graph" ? graphBody' in client, '')

# ══════════════ 七、运行期验证（真起 host，喂事件，看真图） ══════════════
driver = os.path.join(tempfile.gettempdir(), 'kexi_graph_probe.mjs')
with open(driver, 'w', encoding='utf-8') as f:
    f.write(r'''
import { pathToFileURL } from 'node:url'
const HOST = process.argv[2]
const routes = new Map(); const handlers = new Map()
const fakeWs = { register: (r) => { routes.set(r.path, r); return () => routes.delete(r.path) } }
// ⚠ host 的 web 路由服务来自 **ctx.inject**（不是 ctx.get）——照抄 test-runtime.mjs 的
// harness 形状，否则 routes 永远为空，探针会静默拿不到数据。
const ctx = {
  on(name, fn) { if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(fn); return () => { } },
  inject(_deps, cb) { cb({ get: (n) => (n === 'webServer' ? fakeWs : undefined), effect: (fn) => { const d = fn(); return typeof d === 'function' ? d : () => { } } }) },
  effect(fn) { const d = fn(); return typeof d === 'function' ? d : () => { } },
}
const { apply } = await import(pathToFileURL(HOST).href)
apply(ctx)
const emit = (ev, ...a) => { for (const fn of (handlers.get(ev) || [])) fn(...a) }
function call(path){ return new Promise(res=>{ const req={method:'GET',url:path,on(){},destroy(){}}
  let acc=''; const r={writeHead(c){this._c=c},end(s){acc+=s||'';let j=null;try{j=JSON.parse(acc)}catch{};res(j)}}
  routes.get(path).handler(req,r) }) }

const lead = { id:'sess-L', cwd:'C:/w' }
const shumaId = 'mem-shuma-0000-0000-0000-000000000001'
const zhibeiId = 'mem-zhibei-0000-0000-0000-00000000002'
for (const [id, name, cn] of [[shumaId,'shuma','数脉'],[zhibeiId,'zhibei','指北']]) {
  emit('session/event', lead, { type:'team/member', data:{ teamId:'t',
    member:{ id, name, description: cn+' 职责', provider:'spawn', context:'fresh', phase:'active' } } })
}
emit('session/event', lead, { type:'team/message/queued', data:{ teamId:'t', message:{
  id:'m1', senderId:'sess-L', senderName:'lead', targetId:shumaId,
  content:[{type:'text',text:'拉 300 个标的的日线并做质检'}] } } })
// 数脉正在跑取数脚本
emit('session/event', { id: shumaId, cwd:'C:/w' },
  { type:'tool/call', data:{ callId:'a1', name:'kexi_run', arguments:{ mode:'script', script:'fetch_klines.py' } } })
// 指北在跑指标脚本
emit('session/event', { id: zhibeiId, cwd:'C:/w' },
  { type:'tool/call', data:{ callId:'b1', name:'kexi_run', arguments:{ mode:'script', script:'indicators.py' } } })
// 回报
emit('session/event', lead, { type:'team/message/queued', data:{ teamId:'t', message:{
  id:'m2', senderId:shumaId, senderName:'shuma', targetId:'sess-L',
  content:[{type:'text',text:'300 根日线已就绪'}] } } })

const r = await call('/kexi-dashboard/activity')
const s = (r && r.sessions || []).find(x => x.id === 'sess-L')
console.log(JSON.stringify({ graph: s && s.graph }))
''')
try:
    out = subprocess.run(['node', driver, HOST], capture_output=True, text=True, timeout=90)
    payload = json.loads([l for l in out.stdout.strip().splitlines() if l.startswith('{')][-1])
    g = payload.get('graph')
    ck('⑦ 运行期：会话带 graph', isinstance(g, dict), out.stderr[-200:])
    if isinstance(g, dict):
        nodes = {n['id']: n for n in g['nodes']}
        ck('⑦ 运行期：主理人在图里', nodes.get('lead', {}).get('isLead') is True, str(list(nodes)))
        ck('⑦ 运行期：两位成员都在图里（"谁加入了工作"）',
           'shuma' in nodes and 'zhibei' in nodes, str(list(nodes)))
        ck('⑦ 运行期：数脉被判为「数据搜集」',
           nodes.get('shuma', {}).get('cat') == 'collect', str(nodes.get('shuma')))
        ck('⑦ 运行期：指北被判为「数据分析」',
           nodes.get('zhibei', {}).get('cat') == 'analyze', str(nodes.get('zhibei')))
        ck('⑦ 运行期：两者 catFrom=tool（实测非推测）',
           nodes.get('shuma', {}).get('catFrom') == 'tool'
           and nodes.get('zhibei', {}).get('catFrom') == 'tool', '')
        ck('⑦ 运行期：num成员正在忙', nodes.get('shuma', {}).get('busy') is True, '')
        edges = g.get('edges') or []
        ck('⑦ 运行期：有派活边 lead→shuma',
           any(e['from'] == 'lead' and e['to'] == 'shuma' for e in edges), str(edges))
        ck('⑦ 运行期：有回报边 shuma→lead',
           any(e['from'] == 'shuma' and e['to'] == 'lead' for e in edges), str(edges))
        ck('⑦ 运行期：边带 rel 区分派活/回报',
           {e['rel'] for e in edges} <= {'dispatch', 'report'} and edges, str(edges))
        ck('⑦ 运行期：成员带 activity 文本（在干什么）',
           bool(nodes.get('shuma', {}).get('activity')), str(nodes.get('shuma')))
except Exception as e:
    ck('⑦ 运行期探针可执行', False, '%s: %s' % (type(e).__name__, e))
finally:
    try:
        os.remove(driver)
    except OSError:
        pass

# ══════════════ 汇总 ══════════════
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-team-graph: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
