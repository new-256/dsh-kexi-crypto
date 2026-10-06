#!/usr/bin/env node
// DPAPI 往返实测（v1.9.1）。
//
// ⚠ 这个测试存在的唯一理由：v1.6.0 那句「实测往返：密文 416 字符、可还原」
// **是假的**。代码里 spawn 了 `powershell.exe -Command -`，`script` 变量构造了
// 却从没被写出，JSON 明文被 PowerShell 当代码解析——整条链从未跑通，
// 但所有静态检查和语法检查都是绿的。**所以这类"跨进程"的东西必须真跑。**
//
// 覆盖：① 加密往返能还原原文 ② 密文不含明文 ③ 密文是裸二进制（非 base64 信封）
//      ④ **明文不落盘**（全程无任何临时文件含明文）⑤ 特殊字符/中文不炸
//      ⑥ 解密路径同样能跑（v1.9.1 之前它是静默失败的）
import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, existsSync, readdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const checks = []
const ck = (n, c, x = '') => checks.push([n, !!c, x])

const dir = mkdtempSync(join(tmpdir(), 'kexi-dpapi-'))

// 与 index.mjs 中的常量逐字一致（复制而非 import：index.mjs 是 plugin 模块，
// 直接 import 会触发 apply 副作用）
const PROTECT_PS = [
  '$ErrorActionPreference = "Stop"',
  'Add-Type -AssemblyName System.Security',
  '$ms = New-Object System.IO.MemoryStream',
  '[Console]::OpenStandardInput().CopyTo($ms)',
  '$bytes = $ms.ToArray()',
  '$prot = [System.Security.Cryptography.ProtectedData]::Protect(',
  '  $bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
  '[System.IO.File]::WriteAllBytes($env:KEXI_TMP_PATH, $prot)',
  '[Console]::Out.Write("OK")',
  '',
].join('\r\n')

const UNPROTECT_PS = [
  '$ErrorActionPreference = "Stop"',
  'Add-Type -AssemblyName System.Security',
  '$bytes = [System.IO.File]::ReadAllBytes($env:KEXI_SRC_PATH)',
  '$un = [System.Security.Cryptography.ProtectedData]::Unprotect(',
  '  $bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
  '[Console]::Out.Write([Convert]::ToBase64String($un))',
  '',
].join('\r\n')

function runPs(scriptText, env, stdinText) {
  return new Promise((resolve) => {
    const sp = join(dir, 'run.ps1')
    writeFileSync(sp, scriptText, 'utf8')
    const ps = spawn('powershell.exe',
      ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', sp],
      { windowsHide: true, env: { ...process.env, ...env } })
    let out = '', err = ''
    ps.stdout.on('data', c => { out += c })
    ps.stderr.on('data', c => { err += c })
    ps.on('close', code => resolve({ code, out, err }))
    if (stdinText != null) ps.stdin.end(stdinText, 'utf8')
    else ps.stdin.end()
  })
}

const SECRET_PAYLOAD = JSON.stringify({
  schema: 'kexi.cexkeys/1', exchange: 'okx', label: 'main',
  api_key: 'AAAABBBBCCCCDDDD', api_secret: 'zzzz_SeCrEt_0123456789',
  passphrase: 'p@ss:word/with+special=chars',
  note: '中文也要能过：仓位/调仓/证伪',
})

// ── ① 加密往返 ────────────────────────────────────────────────────────────
const binPath = join(dir, 'okx.main.dpapi')
const r1 = await runPs(PROTECT_PS, { KEXI_TMP_PATH: binPath + '.tmp' }, SECRET_PAYLOAD)
ck('① PowerShell 以 -File 跑脚本退出码为 0', r1.code === 0, (r1.err || '').slice(0, 160))
ck('① 输出 OK', r1.out.trim() === 'OK', JSON.stringify(r1.out.slice(0, 80)))
ck('① 密文文件已生成', existsSync(binPath + '.tmp'), '')
rmSync(binPath + '.tmp', { force: true })

// ── ② 密文形态 ────────────────────────────────────────────────────────────
const r2 = await runPs(PROTECT_PS, { KEXI_TMP_PATH: binPath }, SECRET_PAYLOAD)
const bin = existsSync(binPath) ? readFileSync(binPath) : Buffer.alloc(0)
ck('② 密文非空', bin.length > 0, 'len=' + bin.length)
ck('② 密文不含明文 api_secret',
   !bin.toString('latin1').includes('zzzz_SeCrEt_0123456789'), '')
ck('② 密文不含明文 passphrase',
   !bin.toString('latin1').includes('p@ss:word/with+special=chars'), '')
ck('② 密文是**裸二进制**而非 base64 信封（Python 侧按裸流读）',
   !bin.toString('latin1').trimStart().startsWith('{') && !/^[A-Za-z0-9+/=\r\n]+$/.test(bin.toString('latin1').trim()),
   'head=' + bin.subarray(0, 16).toString('hex'))

// ── ③ 明文绝不落盘 ────────────────────────────────────────────────────────
const leftovers = readdirSync(dir).filter(f => {
  try { return readFileSync(join(dir, f)).toString('latin1').includes('zzzz_SeCrEt') } catch { return false }
})
ck('③ 全程没有任何文件含明文（明文只走 stdin）', leftovers.length === 0, JSON.stringify(leftovers))
ck('③ 临时 .ps1 不残留明文', !existsSync(join(dir, 'run.ps1')) ||
   !readFileSync(join(dir, 'run.ps1'), 'utf8').includes('zzzz_SeCrEt'), '')

// ── ④ 解密往返（旧代码这里是静默失败的）────────────────────────────────────
const r3 = await runPs(UNPROTECT_PS, { KEXI_SRC_PATH: binPath }, '')
ck('④ 解密退出码为 0', r3.code === 0, (r3.err || '').slice(0, 160))
let round = null
try { round = JSON.parse(Buffer.from(r3.out.trim(),'base64').toString('utf8')) } catch { /* 下面断言会报 */ }
ck('④ 解密内容是合法 JSON', round !== null, (r3.out || r3.err).slice(0, 120))
ck('④ 往返完全还原原文（含中文与特殊字符）',
   round && JSON.stringify(round) === SECRET_PAYLOAD,
   round ? JSON.stringify(round).slice(0, 120) : 'null')
ck('④ api_secret 逐字节相同',
   round && round.api_secret === 'zzzz_SeCrEt_0123456789', '')
ck('④ 中文未乱码', round && round.note === '中文也要能过：仓位/调仓/证伪',
   round ? round.note : 'null')

// ── ⑤ 空串与边界 ──────────────────────────────────────────────────────────
const r5 = await runPs(PROTECT_PS, { KEXI_TMP_PATH: join(dir, 'empty.dpapi') }, '')
ck('⑤ 空明文也能加密不报错', r5.code === 0 && r5.out.trim() === 'OK', (r5.err || '').slice(0, 120))

// ── ⑥ 回归：旧写法必须被本测试捕获 ────────────────────────────────────────
// 若有人把 `-File` 改回 `-Command -` 并直接灌明文，就是退回 v1.9.1 之前的 bug。
const bad = await new Promise((resolve) => {
  const ps = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', '-'],
    { windowsHide: true })
  let out = '', err = ''
  ps.stdout.on('data', c => { out += c })
  ps.stderr.on('data', c => { err += c })
  ps.on('close', code => resolve({ code, out, err }))
  ps.stdin.end(SECRET_PAYLOAD, 'utf8')     // 明文直接当脚本灌进去（= 旧写法）
})
// ⚠ 断言只能钉**与编码无关的 ASCII 证据**，原因有二（都是实测踩到的）：
//   ① PowerShell 5.1 的 stderr 走**控制台代码页**（中文系统 GBK/936），
//      Node 按 utf-8 读会变成 `λ` 这样的乱码；
//   ② 它输出的是**本地化中文**消息——是「字符: 10」而不是 `char:10`。
//   所以别去匹配 `Unexpected token` 或 `char:10`；真正稳定的证据是
//   「被当成代码的那串 JSON 原样出现在 stderr 里」——它全是 ASCII。
ck('⑥ 退出码为 0（实测）——所以不能用退出码判失败，本测试据此设计',
   bad.code === 0, 'code=' + bad.code)
ck('⑥ 旧写法 stdout 为空（必然触发 out !== OK 而报错）',
   bad.out.trim() === '', 'out=' + JSON.stringify(bad.out.slice(0, 60)))
ck('⑥ 旧写法把 JSON 当 PowerShell 代码解析（被执行的原文回显在 stderr）',
   bad.err.includes('kexi.cexkeys/1'), bad.err.slice(0, 90).replace(/\s+/g, ' '))
ck('⑥ 出错定位到第 1 行（行号 1 是 ASCII，可靠）',
   /:\s*1\s/.test(bad.err), bad.err.slice(0, 40).replace(/\s+/g, ' '))

// ── ⑦ 接线一致性（v1.9.1 新增）────────────────────────────────────────────
// ⚠ ①②③④⑤ 测的是**本文件的脚本副本**，**读不到 index.mjs 的实际接线**。
//   实测证据：把 index.mjs 的 `-File` 改回 `-Command -` 后，
//   本测试仍然全绿——它压根不知道 index.mjs 变了。
//   所以必须静态钉住接线，否则改回旧写法只有运行时报错才发现。
const IDX = join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'lib', 'index.mjs')
const idxSrc = readFileSync(IDX, 'utf8')
ck('⑦ index.mjs 用 -File 跑脚本，绝不用 `-Command -`（那会把 stdin 当代码）',
   !/-Command',\s*'-'/.test(idxSrc) && idxSrc.includes("'-File', scriptPath"), '')
ck('⑦ 两个 DPAPI 脚本都先 Add-Type System.Security（PS 5.1 必需）',
   (idxSrc.match(/Add-Type -AssemblyName System\.Security/g) || []).length >= 2, '')
ck('⑦ 加密走 OpenStandardInput 裸字节（绕开控制台代码页）',
   idxSrc.includes('OpenStandardInput'), '')
ck('⑦ 明文绝不写临时文件（stdin 是唯一明文通道）',
   idxSrc.includes("ps.stdin.end(plain, 'utf8')"), '')

// ⚠ 光做字符串匹配不够：把出参从 base64 改回裸文本，静态匹配未必全中，
//   而**真正会出事**的是「index.mjs 里那份脚本实际跑出来的结果不对」。
//   所以这里从 index.mjs 源码里**抽出它自己的脚本**再真跑一遍——
//   这样改的就是被测对象，而不是副本。
function extractPs(name) {
  const m = new RegExp(
    name + " = \\[(.*?)\\]\\.join\\('\\\\r\\\\n'\\)", 's').exec(idxSrc)
  if (!m) return null
  const lines = []
  const re = /^\s*(?:DPAPI_ASM|'((?:[^'\\]|\\.)*)')\s*,?\s*$/gm
  let mm
  while ((mm = re.exec(m[1])) !== null) {
    lines.push(mm[1] !== undefined
      ? mm[1].replace(/\\'/g, "'").replace(/\\\\/g, '\\')
      : 'Add-Type -AssemblyName System.Security')
  }
  return lines.join('\r\n')
}
const realProtect = extractPs('DPAPI_PROTECT_PS')
const realUnprotect = extractPs('DPAPI_UNPROTECT_PS')
ck('⑦ 能从 index.mjs 源码抽出加密脚本（抽不出 = 形状变了，需同步本测试）',
   !!realProtect && realProtect.includes('Add-Type'), '')
ck('⑦ 能从 index.mjs 源码抽出解密脚本', !!realUnprotect && realUnprotect.includes('Add-Type'), '')

if (realProtect && realUnprotect) {
  const rp = join(dir, 'real.dpapi')
  const a1 = await runPs(realProtect, { KEXI_TMP_PATH: rp }, SECRET_PAYLOAD)
  ck('⑦ 用 index.mjs 真正的加密脚本跑：成功', a1.code === 0 && a1.out.trim() === 'OK',
     (a1.err || '').slice(0, 120))
  const a2 = await runPs(realUnprotect, { KEXI_SRC_PATH: rp }, '')
  let rj = null
  try { rj = JSON.parse(Buffer.from(a2.out.trim(), 'base64').toString('utf8')) } catch { /* 下面报 */ }
  ck('⑦ 用 index.mjs 真正的解密脚本跑：能还原', !!rj, (a2.err || a2.out).slice(0, 100))
  ck('⑦ 真脚本往返中文不乱码',
     !!rj && rj.note === '中文也要能过：仓位/调仓/证伪', rj ? rj.note : 'null')
}

// ── 汇总 ──────────────────────────────────────────────────────────────────
let bad_n = 0
for (const [n, ok, x] of checks) {
  if (!ok) { console.log('  FAIL  ' + n + '   ' + x); bad_n++ }
}
// ⚠ 这里是 JS，不是 Python：别用 `%` 格式化（JS 里 `%` 是取模，会静默算出 NaN）。
console.log(`test-dpapi-roundtrip: ${checks.length - bad_n}/${checks.length} 通过`)
try { rmSync(dir, { recursive: true, force: true }) } catch { /* ignore */ }
process.exit(bad_n ? 1 : 0)
