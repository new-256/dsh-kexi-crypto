// logs/build/assert-static-v1923.mjs — 静态防回退断言（v1.9.23 门禁，回归团）
//
// 为什么要有这个脚本（判据可信度的依据，别删这段）：
//   Lead 最初定的判据是「test-runtime.mjs 中 JSON 字符串形态的 arguments 注入 ≥ 2 处」。
//   实测该判据**在改造前就已经满足**（现状 5 处，第 478/482/495/496/517 行），
//   也就是说它改造前后都是绿的 —— **不具备防回退能力**，属于放水判据。
//   真正零覆盖的是 v1.9.23 的身份信号 `agent_preset`（改造前 0 处），
//   所以收紧为：`agent_preset` ≥ 2 处 且 `name: 'subagent'` 的 tool/call 注入 ≥ 2 处。
//
// 预期行为（这就是判据有效性的证明）：
//   · 改造前（HEAD 07cd884 / v1.9.22）：agent_preset=0 subagent-calls=0 → exit 1  ← 基线
//   · 改造后（v1.9.23 新用例落地）：agent_preset>=2 subagent-calls>=2 → exit 0
//
// 用法: node logs/build/assert-static-v1923.mjs
// 退出码: 0 = 判据满足；1 = 未满足（含改造前基线态）
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// 本脚本在 <repo>/logs/build/，仓库根是上两级
const ROOT = dirname(dirname(dirname(fileURLToPath(import.meta.url))))
const TARGET = join(ROOT, 'scripts/test-runtime.mjs')

let src
try {
  src = readFileSync(TARGET, 'utf8')
} catch (e) {
  console.error(`FATAL: 读不到 ${TARGET}: ${e.message}`)
  process.exit(2)
}

const count = (re) => (src.match(re) || []).length
const presetCount = count(/agent_preset/g)          // 身份信号：新链路的核心字段
const subagentCalls = count(/name:\s*'subagent'/g)  // 必须走新工具名，而不是旧成员工具名

console.log(`target            = scripts/test-runtime.mjs`)
console.log(`agent_preset      = ${presetCount}   (require >= 2)`)
console.log(`subagent-calls    = ${subagentCalls}   (require >= 2)`)

// 附带信息（不计入判定，但让门禁报告能一眼看出「旧通道还在不在」）：
//   v1.9.23 收敛后若 test-runtime.mjs 仍只靠 agentType 造 subagent/start，
//   那条用例会照样绿却不证明新链路——把数量打出来便于复核。
const legacyAgentType = count(/agentType:\s*'/g)
console.log(`legacy agentType  = ${legacyAgentType}   (仅信息：旧身份通道用法计数)`)

const ok = presetCount >= 2 && subagentCalls >= 2
console.log(ok ? 'ASSERT-STATIC: PASS' : 'ASSERT-STATIC: FAIL（agent_preset / subagent tool-call 覆盖不足）')
process.exit(ok ? 0 : 1)
