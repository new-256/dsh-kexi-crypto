#!/usr/bin/env node
// 持续跟踪一个会话直到研判项目完成。
//
// 判定"完成"不能只看 busy=false：主理人可能在 wait_agent 的静默窗口里
// （v1.5.2 记录的"等待期间不产生任何事件"），此时 busy 也会掉。
// 所以用复合条件：**连续 3 次轮询都非 busy，且期间没有新节点**，才算收工。
//
// 用法：node scripts/_track-session.mjs <sessionId> [轮询秒] [最多分钟] [日志路径]
import { writeFileSync, mkdirSync } from 'node:fs'
import net from 'node:net'
import { dirname } from 'node:path'

const SID = process.argv[2]
const INTERVAL = Number(process.argv[3] || 15) * 1000
const MAX_MIN = Number(process.argv[4] || 45) * 60 * 1000
const OUT = process.argv[5] || '.kexi-test-tmp/track-session.json'
// ⚠ v1.9.8：端口**不能写死**——HANDOFF 早就记了「插件端口每次 DSH 重启都会变」
//   （实测 47896 / 52028 / 61478 / 62604 …）。写死的后果最阴险：
//   DSH 一重启，跟踪器就静默连一个**没人监听的旧端口**，
//   采到 0 个样本也照样输出"跟踪完成"——看起来一切正常。
//   优先级：命令行第 6 个参数 > KEXI_PORT > 自动探测本机监听端口。
const PORT_ARG = process.argv[6] || process.env.KEXI_PORT || ''

function isOpen(net, port) {
  return new Promise((res) => {
    const s = net.connect({ host: '127.0.0.1', port })
    s.setTimeout(180)
    s.on('connect', () => { s.destroy(); res(true) })
    s.on('timeout', () => { s.destroy(); res(false) })
    s.on('error', () => { s.destroy(); res(false) })
  })
}

async function detectPort() {
  const candidates = []
  for (let p = 47800; p < 48200; p++) candidates.push(p)
  for (let p = 52000; p < 52200; p++) candidates.push(p)
  for (let p = 61000; p < 63200; p += 2) candidates.push(p)
  for (const p of candidates) {
    if (await isOpen(net, p)) {
      try {
        const r = await fetch('http://127.0.0.1:' + p + '/kexi-dashboard/status')
        if (r.ok) return p
      } catch (e) { /* 不是 K析，继续试 */ }
    }
  }
  return null
}

let BASE = null
async function activityUrl() {
  if (BASE) return BASE
  const p = PORT_ARG || await detectPort()
  if (!p) { console.log('[track] 未能定位 K析 host——请设 KEXI_PORT 或传第 6 个参数'); return null }
  BASE = 'http://127.0.0.1:' + p + '/kexi-dashboard/activity'
  console.log('[track] 目标端点: ' + BASE)
  return BASE
}

if (!SID) { console.error('用法: node _track-session.mjs <sessionId> [秒] [分钟] [日志] [端口]'); process.exit(1) }
mkdirSync(dirname(OUT), { recursive: true })

const t0 = Date.now()
let quiet = 0            // 连续"无新节点且非忙"的次数
let lastNodes = -1
let lastEval = ''
let lastState = ''
// v1.9.5：**网络异常**与**「不在列表」必须分开计数**。
// 一次瞬时 fetch failed 不足以说明会话结束——接口压根没给出关于会话的结论。
// 实测踩过：据此 break，只采了 1 次样就宣布"跟踪完成"，而会话还在跑。
let netErrStreak = 0
let missingStreak = 0
const MAX_NET_ERR = 5        // 连续网络异常上限，超了才放弃（无法判断状态）
const MISSING_TO_END = 3      // 连续「成功请求且确实不在列表」才算结束（TTL 清理）
const trace = []
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const snap = (s) => ({
  at: new Date().toISOString(), t: Math.round((Date.now() - t0) / 1000),
  nodes: s.nodes.length, busy: s.busy, current: s.current ? s.current.label : null,
  dispatched: s.nodes.filter((n) => n.kind === 'team_msg' && /派活/.test(n.label || ''))
    .map((n) => (n.label.match(/→\s*(\S+)/) || [])[1]).filter(Boolean),
  hasArtifact: !!s.artifact, hasEval: !!s.evaluation,
  evalHead: s.evaluation ? String(s.evaluation.text).slice(0, 120) : null,
  state: s.state,
})

console.log(`[track] 开始跟踪 ${SID}，每 ${INTERVAL / 1000}s 一次，上限 ${MAX_MIN / 60000} 分钟`)

while (Date.now() - t0 < MAX_MIN) {
  let s = null, apiState = null, fetchErr = null
  try {
    const url = await activityUrl()
    if (!url) { netErrStreak += 1; console.log('[track] 未能定位 K析 host（端口未找到）——设 KEXI_PORT 或第 5 个参数'); if (netErrStreak >= MAX_NET_ERR) break; continue }
    const r = await fetch(url)
    const j = await r.json()
    apiState = j.state
    s = (j.sessions || []).find((x) => x.id === SID)
  } catch (e) {
    fetchErr = String((e && e.message) || e)
    console.log(`[track] 接口异常: ${fetchErr}`)
  }

  if (!s) {
    // ⚠ v1.9.5：**接口异常 ≠ 会话已结束**。
    //   实测踩到：一次瞬时 `fetch failed` 就让跟踪器认定会话不在列表里、
    //   直接 break，只采了 1 次样就"跟踪完成"——而会话明明还在跑。
    //   fetch 失败时**接口压根没给出任何关于会话的结论**，不能据此判断。
    //   改为：连续 N 次「成功请求且确实不在列表」才算结束（TTL 清理）。
    if (fetchErr) {
      netErrStreak += 1
      trace.push({ at: new Date().toISOString(), netError: true, msg: fetchErr })
      console.log(`[track] 网络异常第 ${netErrStreak} 次——**不算会话结束**，继续跟踪`)
      if (netErrStreak >= MAX_NET_ERR) {
        console.log(`[track] 连续 ${MAX_NET_ERR} 次网络异常，放弃跟踪（无法判断会话状态）`)
        break
      }
      continue
    }
    missingStreak += 1
    trace.push({ at: new Date().toISOString(), missing: true, apiState, streak: missingStreak })
    console.log(`[track] 会话不在活动列表中（${missingStreak}/${MISSING_TO_END}，可能已被 TTL 清理）`)
    if (missingStreak >= MISSING_TO_END) break
    continue
  }
  netErrStreak = 0
  missingStreak = 0

  const evalNow = s.evaluation ? String(s.evaluation.text).slice(0, 200) : ''
  const changed = s.nodes.length !== lastNodes || evalNow !== lastEval
  const row = snap(s)
  trace.push(row)

  if (changed) {
    console.log(`[track] t+${row.t}s  nodes=${row.nodes}  busy=${row.busy}  当前=${row.current || '-'}`
      + `  派活=[${row.dispatched.join(',')}]  成果=${row.hasArtifact}  评估=${row.hasEval}`)
    lastNodes = s.nodes.length
    lastEval = evalNow
    lastState = JSON.stringify(row)
  }

  const progressed = s.nodes.length !== lastNodes || evalNow !== lastEval
  if (!s.busy && !progressed) quiet++; else quiet = 0

  if (quiet >= 3) {
    console.log(`[track] ✓ 判定完成：连续 ${quiet} 次「非忙且无新节点」`)
    break
  }
  await sleep(INTERVAL)
}

writeFileSync(OUT, JSON.stringify({
  sessionId: SID, startedAt: new Date(t0).toISOString(),
  endedAt: new Date().toISOString(), finalTrace: trace[trace.length - 1] || null,
  samples: trace.length, trace,
}, null, 2), 'utf8')
console.log(`[track] 日志已写入 ${OUT}（${trace.length} 次采样）`)
