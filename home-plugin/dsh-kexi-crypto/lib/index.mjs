// dsh-kexi-crypto — host half（家级插件，root realm）。
//
// 三重职责：
//   1. 【活动流】订阅 `session/event`（权威事件源，与 dsh-pet 同源），把
//      turn/step/tool/subagent 事件转成人类可读的「谁在干活、做什么、拿到什么」
//      节点，写入按会话索引的环形缓冲；`GET /kexi-dashboard/activity` 暴露给
//      前端进度弹窗。这解决了"长时间不知道插件在干什么"的核心痛点。
//   2. 【状态灯】收集 preset 插件 ctx.emit 的研判快照（kexi/mode 心跳 + kexi/status），
//      经 `GET /kexi-dashboard/status` 供标题栏研判灯使用。
//   3. 【设置】暴露 `GET/POST /kexi-dashboard/settings`，读写落盘配置
//      （settings.json），设置面板与 pipeline 共用同一份配置。
//
// 事件订阅说明：`session/event` 与 `subagent/*` 是 app 级广播，不受 isolate
// realm（只隔离服务）影响；host 在 root realm 能观测全部会话。

import { readFileSync, writeFileSync, mkdirSync, existsSync, realpathSync, readdirSync, statSync, unlinkSync, renameSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { dirname, join, isAbsolute } from 'node:path'

// ── 插件版本（v1.6.4）────────────────────────────────────────────────────
// 为什么必须有：本次排查出现了一个**很难自证**的现象——用户界面上看到的关系图
// 标签页是新的，但服务端从不返回 graph 字段，节点 kind 仍是旧的 team_member。
// 根因是 host 插件代码在**进程启动时**加载，刷新浏览器不生效，于是
// "源码已改 ≠ 正在跑的就是新代码"，而界面看上去一切正常（标签页在、样式在）。
// 卡片上直接印版本号，是让这类问题**一眼可辨**的最省事办法——
// 看到 v1.6.3 却复现 v1.6.2 的行为，立刻知道是没重启，不用再翻源码。
//
// 读取失败必须**降级而不是崩**：版本号是辅助信息，绝不该让整个 host 起不来。
//
// ⚠ 不能简单 join(dirname, '..')：安装副本里 lib/index.mjs 位于包根**下 3 层**
//   （<pkg>/home-plugin/dsh-kexi-crypto/lib/index.mjs），而源树里包根是仓库根。
//   两边层级不同，所以改成**逐级上溯，找 name 为 dsh-kexi-crypto 的 package.json**。
//
// PLUGIN_VERSION_FALLBACK 是给"读不到文件"兜底的常量。scripts/verify.mjs 会断言它
// 与 package.json 的 version 一致——这样"忘记同步"会**让测试变红**，
// 而不是让用户看到过期版本号还找不到原因。
const PLUGIN_VERSION_FALLBACK = '1.9.17'
let PLUGIN_VERSION = PLUGIN_VERSION_FALLBACK
try {
  let dir = dirname(fileURLToPath(import.meta.url))
  for (let i = 0; i < 6; i++) {
    const pj = join(dir, 'package.json')
    if (existsSync(pj)) {
      const j = JSON.parse(readFileSync(pj, 'utf8'))
      if (j && j.name === 'dsh-kexi-crypto' && j.version) { PLUGIN_VERSION = String(j.version); break }
    }
    const up = dirname(dir)
    if (up === dir) break        // 到根了
    dir = up
  }
} catch (e) { /* 保持 fallback */ }
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'

export const name = 'kexi-dashboard'
export const inject = []

/** 插件根（用于定位默认配置路径）。 */
const HERE = dirname(fileURLToPath(import.meta.url))

/** 活动环形缓冲上限（每会话最多保留的节点数）。 */
const MAX_EVENTS_PER_SESSION = 120
/** 会话表上限。 */
const MAX_SESSIONS = 24

/** 团队成员 id/name → 中文名（v1.5.1 团队动向卡片；v1.5.9 新增证伪红队）。 */
const MEMBER_CN = {
  shuma: '数脉', zhibei: '指北', wangchao: '望潮', shouzhuo: '守拙',
  zhengfu: '证伪', lead: '主理人',
  market_data_specialist: '数脉', technical_indicators: '指北',
  trend_forecaster: '望潮', risk_assessor: '守拙',
  falsification_challenger: '证伪',
}
/** preset 心跳租约 TTL。 */
const PRESET_TTL_MS = 75000
/** 活动节点保留时长（超过则视为过期不再返回）。 */
const ACTIVITY_TTL_MS = 30 * 60 * 1000
// busy 判定的活跃保护：running 节点若超过此时长没新事件，视为"卡死残留"不计 busy
// （防止 turn/step 节点未关闭导致会话永久 busy，新任务进度被淹没）。
const BUSY_FRESH_MS = 60 * 1000

/** 默认设置（与设置面板 schema 对齐）。 */
const DEFAULT_SETTINGS = {
  showProgressPopup: true,
  persistentTeam: true,
  cliAsMembers: true,
  showCliNodes: true,
  autoCliCrosscheck: true,
  cliCrosscheckTimeoutSec: 90,
  maxEventNodes: 60,
  cliPriority: ['agy', 'codebuddy', 'mimo'],
  cliEnabled: { agy: true, codebuddy: true, mimo: true },
  screenerTop: 100,
  screenerWorkers: 6,
  minAdvUsd: 3000000,
  riskPerTrade: 0.005,
  maxWeight: 0.25,
  defaultInterval: '1d',
  defaultLimit: 220,
  // ── CEX 实盘（v1.6.0）─────────────────────────────────────────────────
  // ⚠ autonomy 是**用户唯一的实盘开关**，模型无权修改、也不接受来自模型调用
  //   的参数覆盖（见 kexi_cex 工具：它只读这个值，不透传）。
  //   readonly = 只读（余额/持仓/体检）；confirm = 生成待确认单等你点头；
  //   limited  = 限额内自动（必须带止损）；full = 仅受 HARD_CEILING 约束。
  cexAutonomy: 'readonly',
  cexProfile: 'conservative',
  cexEnabled: { binance: true, okx: true, gate: true, mexc: true },
  cexExtraExchanges: [],      // 预留：用户可追加交易所代号（需对应适配器已实现）
  cexDefaultLabel: 'main',    // 账户标签 → 支持同一交易所多账户
  cexAutoNotionalCapUsd: 200, // limited 档单笔名义上限（美元）
}

/**
 * 工具名 → 中文动作描述与可视化元数据的映射表。
 * 这层是「可视化节点」的可读性关键：把原始工具名翻译成人能看懂的一步。
 */
function describeTool(name, args) {
  // v1.5.2 修复（取证 2026-09-29 真实 session 事件）：DSH 的 tool/call 事件里
  // `data.arguments` 是 **JSON 字符串**（'{"target":"数脉",…}'），不是对象。
  // 旧实现只认 object → a 恒为 {} → 所有依赖参数的标签全部退化成默认值：
  //   send_message 显示"派活给 成员"（丢掉收件人）、kexi_run 不显示币种、
  //   read/write 不显示文件路径、bash/pwsh 不显示命令。
  // 回归测试此前传的是对象，所以测试全绿而线上失效——这里两种形态都吃。
  let a = args
  if (typeof a === 'string') {
    try { a = JSON.parse(a) } catch { a = {} }
  }
  if (!a || typeof a !== 'object') a = {}
  const map = {
    kexi_run: () => {
      if (a.mode === 'script') {
        return { icon: '📊', label: `运行 ${a.script || '脚本'}`, detail: Array.isArray(a.args) ? a.args.join(' ') : null }
      }
      return { icon: '📊', label: `跑研判流水线 ${a.symbol || ''} ${a.interval || ''}`.trim(), detail: a.symbol || null }
    },
    kexi_validate: () => ({ icon: '✅', label: '校验报告契约', detail: a.input || null }),
    kexi_dashboard: () => ({ icon: '🖼️', label: '渲染离线看板', detail: a.input || null }),
    kexi_cli: () => ({ icon: '🤖', label: `CLI ${a.action === 'run' ? (a.cli ? `调度 ${a.cli}` : '调度') : '探测'}`, detail: a.prompt ? clipStr(a.prompt, 90) : null }),
    kexi_news: () => ({ icon: '📰', label: '探测新闻通道', detail: null }),
    kexi_ensure_team: () => ({ icon: '👥', label: '预置研判团队', detail: null }),
    bash: () => ({ icon: '⌨️', label: '执行 bash 命令', detail: clipStr(a.command, 90) }),
    pwsh: () => ({ icon: '⌨️', label: '执行 PowerShell 命令', detail: clipStr(a.command, 90) }),
    read: () => ({ icon: '📖', label: '读取文件', detail: a.file_path || a.path }),
    write: () => ({ icon: '📝', label: '写入文件', detail: a.file_path || a.path }),
    edit: () => ({ icon: '✏️', label: '编辑文件', detail: a.file_path || a.path }),
    glob: () => ({ icon: '🔍', label: '查找文件', detail: a.pattern }),
    grep: () => ({ icon: '🔎', label: '搜索内容', detail: a.pattern }),
    subagent: () => ({ icon: '🧑‍🔬', label: `派发子任务 ${a.description || ''}`.trim(), detail: a.description || null }),
    subagent_fork: () => ({ icon: '🔱', label: '派发分支子任务', detail: a.description }),
    agy_run: () => ({ icon: '🤖', label: `agy 执行${a.background === false ? '(前台)' : ''}`, detail: clipStr(a.prompt, 90) }),
    agy_continue: () => ({ icon: '🤖', label: 'agy 续跑', detail: clipStr(a.prompt, 90) }),
    agy_status: () => ({ icon: '📡', label: 'agy 状态', detail: null }),
    codebuddy_run: () => ({ icon: '🤖', label: `codebuddy 执行${a.backend ? '(' + a.backend + ')' : ''}`, detail: clipStr(a.prompt, 90) }),
    codebuddy_continue: () => ({ icon: '🤖', label: 'codebuddy 续跑', detail: clipStr(a.prompt, 90) }),
    mimo_run: () => ({ icon: '🤖', label: 'mimo 执行', detail: clipStr(a.prompt, 90) }),
    web_search: () => ({ icon: '🌐', label: '联网检索', detail: Array.isArray(a.queries) ? a.queries[0] : null }),
    web_search_multi: () => ({ icon: '🌐', label: `多源检索 ${a.engine || 'auto'}`, detail: a.query }),
    web_fetch_url: () => ({ icon: '📥', label: '抓取网页', detail: a.url }),
    todo_write: () => ({ icon: '📋', label: '更新任务清单', detail: null }),
    present: () => ({ icon: '🎁', label: '交付文件卡片', detail: null }),
    ask_user_question: () => ({ icon: '❓', label: '询问用户', detail: null }),
    create_goal: () => ({ icon: '🎯', label: '建立目标', detail: null }),
    // 团队编排工具（v1.5.1：主理人派活/等成员也翻译成可读节点）
    send_message: () => ({ icon: '📤', label: `派活给 ${MEMBER_CN[String(a.target)] || a.target || '成员'}`, detail: clipStr(a.message, 120) }),
    wait_agent: () => ({ icon: '⏳', label: '等待成员回报', detail: null }),
    list_agents: () => ({ icon: '👥', label: '查看团队名册', detail: null }),
    interrupt_agent: () => ({ icon: '✋', label: '打断成员', detail: a.target || null }),
    team_task_create: () => ({ icon: '📋', label: '创建共享任务', detail: clipStr(a.subject, 90) }),
    team_task_update: () => ({ icon: '📋', label: '更新共享任务', detail: a.action || null }),
  }
  const fn = map[name]
  if (fn) {
    try { return fn() } catch { return { icon: '🔧', label: name, detail: null } }
  }
  // 未知工具：CLI 段（*_run/_continue/_status）与成员工具兜底
  if (/_run$|_continue$|_status$|_quota$/.test(name)) return { icon: '🤖', label: `调用 ${name}`, detail: null }
  if (/^(market_data_specialist|technical_indicators|trend_forecaster|risk_assessor|falsification_challenger)$/.test(name)) {
    const cn = { market_data_specialist: '数脉·行情取数', technical_indicators: '指北·技术指标', trend_forecaster: '望潮·趋势预判', risk_assessor: '守拙·风险评估', falsification_challenger: '证伪·唱反调' }[name]
    return { icon: '👤', label: `委派 ${cn}`, detail: clipStr(a.prompt, 80) }
  }
  return { icon: '🔧', label: name, detail: null }
}

function clipStr(v, n) {
  if (typeof v !== 'string' || !v) return null
  const s = v.replace(/\s+/g, ' ').trim()
  return s.length <= n ? s : s.slice(0, n) + '…'
}

/** 从工具结果里提取一行"拿到了什么"的摘要。 */
function summarizeResult(res) {
  if (res === undefined || res === null) return null
  if (typeof res === 'string') return clipStr(res, 140)
  if (typeof res === 'object') {
    if (typeof res.digest === 'string') return clipStr(res.digest, 200)
    if (typeof res.summary_text === 'string') return clipStr(res.summary_text, 200)
    if (Array.isArray(res.out_files) && res.out_files.length) {
      return res.out_files.map((f) => String(f).split(/[\\/]/).pop()).join(', ')
    }
    if (typeof res.error === 'string') return '错误: ' + clipStr(res.error, 140)
    if (typeof res.ok === 'boolean') return res.ok ? '完成' : '失败'
    try { return clipStr(JSON.stringify(res), 140) } catch { return null }
  }
  return clipStr(String(res), 140)
}

/**
 * 从 tool/result 的 message.content 块里提取结构化结果。
 * content 是内容块数组（如 [{type:'text', text:'{...}'}]）。优先把唯一的 text 块
 * 当 JSON 解析（工具结果通常是 JSON 文本），解析失败则回退为该文本/数组，保证下游
 * summarizeResult / recordArtifact 拿到 out_files、digest 等真实字段。
 */
function extractToolPayload(content) {
  if (content === undefined || content === null) return null
  if (typeof content === 'string') return tryJson(content) ?? content
  if (!Array.isArray(content)) return content
  const texts = content
    .filter((b) => b && typeof b === 'object' && b.type === 'text' && typeof b.text === 'string')
    .map((b) => b.text)
  if (!texts.length) return content
  const joined = texts.join('\n').trim()
  const parsed = tryJson(joined)
  return parsed === undefined ? joined : parsed
}
function tryJson(s) {
  const t = String(s).trim()
  if (!t || !(t[0] === '{' || t[0] === '[')) return undefined
  try { return JSON.parse(t) } catch { return undefined }
}

export function apply(ctx) {
  // ── 会话状态灯（原有能力） ───────────────────────────────────────────────
  const sessions = Object.create(null)
  let presetActiveUntil = 0
  let lastModeAt = 0

  // ── 活动流（新增：需求 1） ───────────────────────────────────────────────
  const activity = Object.create(null)   // sessionId -> { id, cwd, name, preset, nodes[], artifactId? }
  const toolStarts = new Map()           // callId -> { sessionId, nodeIndex }
  // ── 最终看板产物（需求：成果在 K析页面直接可见） ─────────────────────────
  const artifacts = new Map()            // id -> { path, name, sessionId }
  let artifactSeq = 0

  // ── 设置持久化（新增：需求 3） ───────────────────────────────────────────
  let settings = { ...DEFAULT_SETTINGS }
  // 落盘位置优先级：环境变量 > DSH_HOME > 包内（node_modules 重装会丢，仅兜底）。
  function resolveSettingsPath() {
    if (process.env.KEXI_SETTINGS_FILE) return process.env.KEXI_SETTINGS_FILE
    const dshHome = process.env.DSH_HOME
    if (dshHome && String(dshHome).trim()) return join(String(dshHome), 'kexi-settings.json')
    try { return join(homedir(), '.dsh', 'kexi-settings.json') } catch (e) { }
    return join(HERE, '..', '..', 'kexi-settings.json')
  }
  const settingsPath = resolveSettingsPath()

  // ── CEX 凭据（v1.5.5 建；v1.6.0 与 Python 侧打通 + 多账户）──────────────
  // 架构约束（实测得出，别再绕）：host 插件有 web 路由但**没有 subprocess**，
  // preset 插件有 runPython 但**没有 web 路由**。所以密钥加密必须在 host 侧完成。
  // 方案：用 node:child_process 调 PowerShell 的 [ProtectedData]::Protect（Windows DPAPI），
  // 零额外依赖、真加密、与 cex_keystore.py 的保护等级一致。
  //
  // ⚠⚠ v1.6.0 修一个致命断点（v1.5.5 遗留）：原先这里写的是 `{ex}.enc.json`
  // （JSON 信封 + base64 密文，存 $DSH_HOME/kexi-cex/），而 Python 侧
  // `cex_keystore.load_keys()` 只认 `{ex}.{label}.dpapi`（**裸二进制** DPAPI，
  // 存 <工作区>/.kexi-secrets/cex/）。**目录不同 + 格式不同 + 无桥接** →
  // 面板里配好的密钥，Python 永远读不到，等于整个 CEX 功能是死的。
  // 现在两边共用同一目录、同一命名、同一加密原语：
  //     $DSH_HOME/kexi-cex/{exchange}.{label}.dpapi   ← 裸二进制 DPAPI，Python 直接可读
  //     $DSH_HOME/kexi-cex/index.json                 ← 非敏感元数据（掩码/时间/权限）
  // label 维度恢复 → 同一交易所可挂多账户（如币安现货主账户 + 合约子账户）。
  //
  // 安全约束：
  //   ① **永不回显明文**。GET 只返回"是否已配置 + 掩码尾号"（掩码存在 index.json）。
  //   ② 密文落盘，密钥与 DSH 用户凭据绑定，其他账户/拷贝到别的机器都解不开。
  //   ③ 留空字段 = 不改动（PATCH 语义），避免"打开设置面板点保存"就抹掉密钥。
  //   ④ **自主级别 autonomy 是唯一实盘开关，且模型不可翻转**（见 kexi_cex 工具）。
  const CEX_EXCHANGES = ['binance', 'okx', 'gate', 'mexc']
  // 自主级别阶梯。**值就是权限大小**，判定一律「当前级别 >= 要求的最低级别」。
  const CEX_AUTONOMY = {
    readonly: { level: 0, label: '只读', desc: '只能读余额/持仓/体检，任何写操作一律拒绝' },
    confirm: { level: 1, label: '提议+确认', desc: '生成待确认订单草案，**你在面板点确认后才发送**' },
    limited: { level: 2, label: '受限自动', desc: '限额内自动下单（必须带止损），超限转为待确认' },
    full: { level: 3, label: '全自动', desc: 'AI 自主下单，仅受 HARD_CEILING 硬上限约束' },
  }
  function cexAutonomyName() {
    const n = String(settings.cexAutonomy || 'readonly')
    return Object.prototype.hasOwnProperty.call(CEX_AUTONOMY, n) ? n : 'readonly'
  }
  /** 已启用的交易所（内置注册表 + 用户追加）。显式关掉才隐藏；未配置 = 默认启用。 */
  function cexEnabledExchanges() {
    const en = (settings.cexEnabled && typeof settings.cexEnabled === 'object') ? settings.cexEnabled : {}
    const extra = Array.isArray(settings.cexExtraExchanges) ? settings.cexExtraExchanges : []
    const all = CEX_EXCHANGES.concat(extra
      .map((x) => String(x || '').toLowerCase())
      .filter((x) => /^[a-z0-9_]{2,16}$/.test(x) && !CEX_EXCHANGES.includes(x)))
    const seen = []
    for (const x of all) if (en[x] !== false && !seen.includes(x)) seen.push(x)
    return seen
  }
  function cexStoreDir() {
    const dshHome = process.env.DSH_HOME
    if (dshHome && String(dshHome).trim()) return join(String(dshHome), 'kexi-cex')
    try { return join(homedir(), '.dsh', 'kexi-cex') } catch { }
    return join(HERE, '..', '..', 'kexi-cex')
  }
  /** 凭据文件：裸二进制 DPAPI，命名与 cex_keystore.load_keys 的查找规则完全一致。 */
  function cexPath(ex, label) {
    const exs = String(ex || '').toLowerCase().replace(/[^a-z0-9_]/g, '')
    const lab = String(label || settings.cexDefaultLabel || 'main')
      .replace(/[^a-z0-9_.-]/gi, '') || 'main'
    return join(cexStoreDir(), `${exs}.${lab}.dpapi`)
  }
  /** 非敏感元数据索引：host 写、Python 只读。避免为显示状态而解密全部密钥。 */
  function cexIndexPath() { return join(cexStoreDir(), 'index.json') }
  function readCexIndex() {
    try {
      const f = cexIndexPath()
      if (!existsSync(f)) return { schema: 'kexi.cexindex/1', entries: {} }
      const j = JSON.parse(readFileSync(f, 'utf8'))
      if (!j || typeof j !== 'object' || !j.entries || typeof j.entries !== 'object') {
        return { schema: 'kexi.cexindex/1', entries: {} }
      }
      return j
    } catch { return { schema: 'kexi.cexindex/1', entries: {} } }
  }
  function writeCexIndex(idx) {
    try {
      mkdirSync(cexStoreDir(), { recursive: true })
      writeFileSync(cexIndexPath(), JSON.stringify(idx, null, 2), 'utf8')
    } catch { /* 元数据写失败不影响密钥本身已在盘上 */ }
  }

  // ── DPAPI 脚本常量（v1.9.1）──────────────────────────────────────────
  // 必须是**纯 ASCII**：脚本走临时 .ps1 经 `-File` 执行，而 PowerShell 5.1
  // 读无 BOM 的 UTF-8 .ps1 会把非 ASCII 拆坏（本项目既有教训）。
  // 这两个脚本**不含任何密钥**；明文一律只走 stdin，绝不落盘。
  //
  // ⚠ `Add-Type -AssemblyName System.Security` 不是可选的：
  //   Windows 的 `powershell.exe` 是 **5.1（.NET Framework）**，`ProtectedData`
  //   住在 System.Security.dll 里**默认不加载**——不先 Add-Type 就是
  //   「找不到类型 [System.Security.Cryptography.ProtectedData]」。
  //   （本机 pwsh 7.6 反而能直接用；所以在 pwsh 里试通不代表 powershell.exe 能跑，
  //   这正是本条被漏掉的第二个原因。）
  //
  // ⚠ **stdin/stdout 一律按原始字节处理，不经过控制台编码**：
  //   PowerShell 5.1 的 `[Console]::In/Out` 用控制台代码页（中文系统上是 GBK/936），
  //   不是 UTF-8。实测中文备注会变成「仓?调仓」。
  //   所以：入参用 `OpenStandardInput().CopyTo()` 读**裸字节**，
  //   出参用 `ToBase64String()` 交给 Node 按 utf-8 解——两端都与编码无关。
  //   （ASCII 的 API Key 不受影响，但密码短语可能含任意字符，不能赌。）
  const DPAPI_ASM = 'Add-Type -AssemblyName System.Security'
  const DPAPI_PROTECT_PS = [
    '$ErrorActionPreference = "Stop"',
    DPAPI_ASM,
    // 裸字节读 stdin：绝不经过 [Console]::In 的代码页转换
    '$ms = New-Object System.IO.MemoryStream',
    '[Console]::OpenStandardInput().CopyTo($ms)',
    '$bytes = $ms.ToArray()',
    '$prot = [System.Security.Cryptography.ProtectedData]::Protect(',
    '  $bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
    '[System.IO.File]::WriteAllBytes($env:KEXI_TMP_PATH, $prot)',
    '[Console]::Out.Write("OK")',
    '',
  ].join('\r\n')

  const DPAPI_UNPROTECT_PS = [
    '$ErrorActionPreference = "Stop"',
    DPAPI_ASM,
    '$bytes = [System.IO.File]::ReadAllBytes($env:KEXI_SRC_PATH)',
    '$un = [System.Security.Cryptography.ProtectedData]::Unprotect(',
    '  $bytes, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)',
    // base64 出参：绕开控制台代码页，Node 侧再按 utf-8 解
    '[Console]::Out.Write([Convert]::ToBase64String($un))',
    '',
  ].join('\r\n')

  /**
   * 用 PowerShell + Windows DPAPI 把明文加密成**裸二进制**并原子落盘。
   *
   * ⚠ v1.6.0 的修正：旧版返回 base64 密文、外面包一层 JSON 信封存成
   * `{ex}.enc.json`。而 `cex_keystore.load_keys()` 期望**裸 DPAPI 字节流**、
   * 文件名 `{ex}.{label}.dpapi`——两边对不上，面板配的密钥 Python 读不到。
   * 现在直出裸二进制，Python 侧**零改动**即可读取。
   *
   * ⚠ v1.9.1 的修正（用户实测报错："DPAPI 加密失败：…表达式或语法制项无效"）：
   *   旧代码 spawn `powershell.exe -Command -` 并 `stdin.end(plain)`。
   *   **但 `-Command -` 的意思是"从 stdin 读脚本执行"**，不是"先执行再从 stdin 读数据"。
   *   于是唯一写进 stdin 的那串 JSON 明文被 PowerShell **当成 PowerShell 代码解析**，
   *   `{"schema":…` 在第 1 行第 10 字符处炸掉——正是用户看到的报错原文。
   *   更糟的是：**`script` 变量从头到尾没被写进任何地方**，所以这段代码
   *   **从未真正工作过**（解密路径同理：stdin 是空串、脚本不存在，既不解密
   *   也不报错，只是静默失败）。CHANGELOG v1.6.0 那句「实测往返：密文 416 字符、
   *   可还原」是**错的**——没人真正跑通过它。
   *
   *   修法：**脚本走临时 .ps1 文件（不含任何密钥），明文只走 stdin**。
   *   脚本不走 argv：Windows 参数转义对内嵌双引号与换行极脆。
   *   明文不走临时文件：**明文绝不能落盘**，这是安全底线。
   *   两条通道彻底分开，谁也不冒充谁。
   *
   * 原子性：PowerShell 写 `{path}.tmp`，成功后再由 Node rename 到最终路径。
   * 中途断电/崩溃只会留下一个 .tmp 垃圾文件，不会产生"半个密文"——
   * 半截密文会被 Python 侧解出乱码并报失败，比"文件不存在"更难排查。
   */
  function dpapiProtectTo(plain, outPath) {
    const tmpPath = String(outPath) + '.tmp'
    return new Promise((resolve, reject) => {
      const scriptPath = tmpPath + '.ps1'
      let ps
      // ⚠ v1.9.1 的关键修正（用户实测报错："DPAPI 加密失败：…表达式或语法制项无效"）：
      //   旧代码 spawn `powershell.exe -Command -` 并 `stdin.end(plain)`。
      //   **但 `-Command -` 的意思是"从 stdin 读脚本执行"**，不是"执行完再去读 stdin"。
      //   于是唯一写进 stdin 的那串 JSON 明文被 PowerShell **当成 PowerShell 代码解析**，
      //   `{"schema":…` 在第 1 行第 10 字符处炸掉——正是用户看到的报错。
      //   更糟的是：**`script` 变量从头到尾没被写进任何地方**，所以这段代码
      //   **从未真正工作过**（解密路径同理：stdin 是空串，脚本不存在，
      //   既不加密也不输出 OK，只会静默失败）。
      //
      //   修法：**脚本走临时 .ps1 文件（不含任何密钥），明文只走 stdin**。
      //   为什么不让脚本走 argv：Windows 参数转义对内嵌双引号与换行极脆；
      //   为什么不让明文走临时文件：**明文绝不能落盘**，这是安全底线。
      //   两条通道彻底分开，谁也不冒充谁。
      try {
        writeFileSync(scriptPath, DPAPI_PROTECT_PS, 'utf8')
        ps = spawn('powershell.exe',
          ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', scriptPath],
          { windowsHide: true, env: { ...process.env, KEXI_TMP_PATH: tmpPath } })
      }
      catch (e) { reject(new Error('无法启动 PowerShell：' + String((e && e.message) || e))); return }
      let out = '', err = ''
      ps.stdout.on('data', (c) => { out += c })
      ps.stderr.on('data', (c) => { err += c })
      ps.on('error', (e) => reject(e))
      ps.on('close', (code) => {
        try { unlinkSync(scriptPath) } catch { /* 临时脚本残留无害（不含密钥） */ }
        if (code !== 0 || String(out).trim() !== 'OK') {
          reject(new Error('DPAPI 加密失败：' + (String(err).trim().slice(0, 200) || 'code=' + code)))
          return
        }
        try { renameSync(tmpPath, String(outPath)) }
        catch (e) {
          // rename 失败：内容已经在 tmp 里，退化为直接用 tmp 路径（仍保证不是半个密文）
          reject(new Error('凭据落盘失败（rename: ' + String((e && e.message) || e).slice(0, 120) + '）'))
          return
        }
        resolve(true)
      })
      ps.stdin.end(plain, 'utf8')
    })
  }

  function maskKey(ak) {
    const s = String(ak || '')
    return s.length > 6 ? (s.slice(0, 3) + '***' + s.slice(-4)) : '***'
  }

  /**
   * 只回传"配置状态"，绝不回传明文密钥。
   * 掩码/时间等来自 index.json（明文但非敏感），**不解密任何密钥**——
   * 这样即使有几十个账户，面板打开也是瞬时的。
   */
  // 各交易所的凭据字段构成——**单一事实来源**，设置面板据此按交易所渲染表单。
  //
  // 起因（用户 2026-09-30 实测反馈）：OKX 是「密码短语 + API Key + Secret Key」三样，
  // 而 Binance / MEXC 只有 API Key + Secret Key 两样。设置面板以前把 passphrase
  // 无条件常驻显示，且文案写成"仅 OKX/Gate 需要"——**后者是错的**：
  // cex_keystore.py:189 的真实规则是 **只有 OKX 强制要求** passphrase，
  // Gate 并不需要。Binance 用户会看到一个根本不存在的字段。
  //
  // ⚠ 这里与 cex_keystore.py 的规则是两份拷贝，**靠 scripts/test-cex-credential-schema.py
  // 钉死一致性**。改任一处而不改另一处，那个测试会红。
  const CEX_CREDENTIALS = {
    binance: { fields: ['api_key', 'api_secret'], passphrase: false, key_name: 'API Key', secret_name: 'Secret Key' },
    okx: { fields: ['api_key', 'api_secret', 'passphrase'], passphrase: true, key_name: 'API Key', secret_name: 'Secret Key' },
    gate: { fields: ['api_key', 'api_secret'], passphrase: false, key_name: 'API Key', secret_name: 'Secret Key' },
    mexc: { fields: ['api_key', 'api_secret'], passphrase: false, key_name: 'API Key', secret_name: 'Secret Key' },
  }

  function cexStatus() {
    syncAutonomyFile()
    const idx = readCexIndex()
    const out = {
      exchanges: {}, storeDir: cexStoreDir(),
      autonomy: cexAutonomyName(),
      autonomy_levels: CEX_AUTONOMY,
      profile: String(settings.cexProfile || 'conservative'),
      registry: CEX_EXCHANGES.slice(),
      credential_schema: CEX_CREDENTIALS,
      enabled: cexEnabledExchanges(),
      default_label: String(settings.cexDefaultLabel || 'main'),
      auto_notional_cap_usd: Number(settings.cexAutoNotionalCapUsd || 200),
      accounts: [], note: '',
    }
    for (const ex of cexEnabledExchanges()) {
      const rec = idx.entries[ex] || {}
      const labels = (rec.labels && typeof rec.labels === 'object') ? rec.labels : {}
      const keys = Object.keys(labels)
      out.exchanges[ex] = {
        configured: keys.length > 0,
        labels: keys.map((lab) => ({
          label: lab,
          api_key_masked: (labels[lab] || {}).api_key_masked || '***',
          has_passphrase: !!(labels[lab] || {}).has_passphrase,
          updated_at: (labels[lab] || {}).updated_at || null,
        })),
      }
      for (const lab of keys) {
        out.accounts.push({ exchange: ex, label: lab, ...(labels[lab] || {}) })
      }
    }
    const lvl = CEX_AUTONOMY[out.autonomy]
    out.note = '密钥经 Windows DPAPI 加密保存在本机（与你的 Windows 账户绑定），页面永不回显明文。'
      + `当前自主级别：**${lvl.label}** —— ${lvl.desc}。`
      + (out.autonomy === 'readonly' ? '' : '⚠ 已具备真实下单能力，请确认风控档与限额设置。')
    return out
  }

  /**
   * 保存某交易所某账户的凭据（PATCH 语义：留空 = 不改动）。
   *
   * 关键：落盘的是 `{ex}.{label}.dpapi` **裸二进制 DPAPI**，与
   * `cex_keystore.load_keys(store_dir, ex, label)` 的查找规则逐字对应，
   * 所以 Python 侧不需要任何改动就能读到这里存的密钥。
   * 元数据（掩码、时间、是否含 passphrase）另存 index.json——**非敏感**，
   * 面板展示状态时无需解密任何密钥。
   */
  /** 从请求 URL 取查询参数（小工具，CEX 路由用）。 */
  function cexQueryParam(req, name) {
    try {
      const u = new URL(req.url || '', 'http://x')
      return String(u.searchParams.get(name) || '').toLowerCase().trim()
    } catch { return '' }
  }

  /**
   * 把自主级别写进凭据目录，供 Python 侧 `cex_adapter.py` 读取。
   *
   * ⚠ 这是**整个实盘链路的单一权限源**。设计要点：
   *   ① 它**不是** CLI 参数。模型能调 kexi_run 传任意 argv，若权限来自命令行，
   *      模型多写一个 `--autonomy full` 就绕过了。所以脚本只认这个文件。
   *   ② 缺文件 / 非法值 / 读失败 → Python 侧一律退回 readonly（fail-closed）。
   *      "权限读不出来"绝不能等于"放行实盘"。
   *   ③ 每次设置保存都重写，保证面板显示的级别与实际生效的级别**永远是同一个**。
   */
  function syncAutonomyFile() {
    const lvl = cexAutonomyName()
    const doc = {
      schema: 'kexi.autonomy/1',
      level: lvl,
      level_label: CEX_AUTONOMY[lvl].label,
      profile: String(settings.cexProfile || 'conservative'),
      auto_notional_cap_usd: Number(settings.cexAutoNotionalCapUsd || 200),
      enabled: cexEnabledExchanges(),
      updated_at: new Date().toISOString(),
      note: '此文件由 K析研判团 host 插件根据设置面板写入，是实盘权限的**唯一来源**；'
        + '请勿手工编辑——改坏了会自动退回只读档（fail-closed）。',
    }
    try {
      mkdirSync(cexStoreDir(), { recursive: true })
      writeFileSync(join(cexStoreDir(), 'autonomy.json'), JSON.stringify(doc, null, 2), 'utf8')
    } catch { /* 写不了就让 Python 侧按 fail-closed 走只读，安全方向一致 */ }
    return doc
  }

  async function cexSave(patch) {
    const ex = String((patch && patch.exchange) || '').toLowerCase().trim()
    if (!/^[a-z0-9_]{2,16}$/.test(ex)) return { ok: false, error: 'exchange 非法' }
    const known = CEX_EXCHANGES.concat((Array.isArray(settings.cexExtraExchanges) ? settings.cexExtraExchanges : []))
      .map((x) => String(x).toLowerCase())
    if (!known.includes(ex)) return { ok: false, error: `exchange 必须是 ${known.join(' / ')} 之一（新增需先在设置里登记）` }
    const label = String((patch && patch.label) || settings.cexDefaultLabel || 'main')
      .replace(/[^a-z0-9_.-]/gi, '') || 'main'
    const ak = (patch && patch.api_key) ? String(patch.api_key).trim() : null
    const as = (patch && patch.api_secret) ? String(patch.api_secret).trim() : null
    const pp = (patch && patch.passphrase) ? String(patch.passphrase).trim() : null
    if (ak === null && as === null && pp === null) {
      return { ok: false, error: '未提供任何字段（留空 = 不改动，避免误抹已存密钥）' }
    }
    // 留空字段时从**既有密文**里回填，保证不会因为只改 passphrase 就把 key 抹掉。
    // 这一步必须解密旧密文——只在真正要写盘时才做，不是每次打开面板都解密。
    const target = cexPath(ex, label)
    let base = {}
    if (existsSync(target)) {
      try {
        base = (await dpapiUnprotectFile(target)) || {}
        if (typeof base !== 'object') base = {}
      } catch (e) {
        return { ok: false, error: `读取既有 ${ex}/${label} 凭据失败（${String((e && e.message) || e).slice(0, 120)}）——请直接重填全部字段覆盖保存` }
      }
    }
    const payload = JSON.stringify({
      schema: 'kexi.cexkeys/1',
      exchange: ex,
      label,
      api_key: ak !== null ? ak : (base.api_key || ''),
      api_secret: as !== null ? as : (base.api_secret || ''),
      passphrase: pp !== null ? pp : (base.passphrase || ''),
      permissions: base.permissions || ['read', 'trade'],
      updated_at: new Date().toISOString(),
    })
    if (!ak && !as && !pp) {
      return { ok: false, error: '首次保存必须同时提供 api_key 与 api_secret' }
    }
    try {
      mkdirSync(cexStoreDir(), { recursive: true })
      await dpapiProtectTo(payload, target)
    } catch (e) { return { ok: false, error: String((e && e.message) || e).slice(0, 240) } }

    const idx = readCexIndex()
    if (!idx.entries[ex] || typeof idx.entries[ex] !== 'object') idx.entries[ex] = { labels: {} }
    if (!idx.entries[ex].labels || typeof idx.entries[ex].labels !== 'object') idx.entries[ex].labels = {}
    idx.entries[ex].labels[label] = {
      api_key_masked: maskKey(ak !== null ? ak : (base.api_key || '')),
      has_passphrase: (pp !== null ? pp : (base.passphrase || '')) ? true : false,
      updated_at: new Date().toISOString(),
    }
    writeCexIndex(idx)
    return { ok: true, exchange: ex, label, saved: true, status: cexStatus() }
  }

  /** 解除某账户凭据的加密并读回（只在本进程内用于 PATCH 合并，绝不回传给页面）。 */
  function dpapiUnprotectFile(filePath) {
    return new Promise((resolve, reject) => {
      const scriptPath = String(filePath) + '.unprotect.ps1'
      let ps
      // ⚠ 同 dpapiProtectTo：`-Command -` 把 stdin 当脚本执行，
      //   且旧代码的 `script` 变量**从未被写出**——**解密路径同样从未真正工作过**。
      //   改用临时 .ps1；解密不需要明文入参，源路径走 env 即可。
      try {
        writeFileSync(scriptPath, DPAPI_UNPROTECT_PS, 'utf8')
        ps = spawn('powershell.exe',
          ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', scriptPath],
          { windowsHide: true, env: { ...process.env, KEXI_SRC_PATH: String(filePath) } })
      } catch (e) { reject(new Error('无法启动 PowerShell：' + String((e && e.message) || e))); return }
      let out = '', err = ''
      ps.stdout.on('data', (c) => { out += c })
      ps.stderr.on('data', (c) => { err += c })
      ps.on('error', (e) => reject(e))
      ps.on('close', (code) => {
        try { unlinkSync(scriptPath) } catch { /* 无害残留（脚本不含密钥） */ }
        if (code !== 0) { reject(new Error(String(err).trim().slice(0, 200) || 'code=' + code)); return }
        try {
          // 出参是 base64：绕开 PowerShell 5.1 控制台代码页（中文系统 GBK）
          const plain = Buffer.from(String(out).trim(), 'base64').toString('utf8')
          resolve(JSON.parse(plain))
        }
        catch (e) { reject(new Error('解密内容不是合法 JSON')) }
      })
      ps.stdin.end('')
    })
  }

  function cexForget(ex, label) {
    const e = String(ex || '').toLowerCase().trim()
    if (!/^[a-z0-9_]{2,16}$/.test(e)) return { ok: false, error: 'exchange 非法' }
    const lab = String(label || settings.cexDefaultLabel || 'main')
    try {
      const f = cexPath(e, lab)
      if (existsSync(f)) unlinkSync(f)
      const idx = readCexIndex()
      if (idx.entries[e] && idx.entries[e].labels) {
        delete idx.entries[e].labels[lab]
        if (!Object.keys(idx.entries[e].labels).length) delete idx.entries[e]
      }
      writeCexIndex(idx)
      return { ok: true, exchange: e, label: lab, forgotten: true, status: cexStatus() }
    } catch (err) { return { ok: false, error: String((err && err.message) || err) } }
  }

  function loadSettings() {
    try {
      if (settingsPath && existsSync(settingsPath)) {
        const raw = JSON.parse(readFileSync(settingsPath, 'utf8'))
        settings = { ...DEFAULT_SETTINGS, ...(raw && typeof raw === 'object' ? raw : {}) }
      }
    } catch (e) { /* 损坏配置回退默认 */ }
  }
  function saveSettings(patch) {
    const merged = { ...settings, ...(patch && typeof patch === 'object' ? patch : {}) }
    // 数值边界收敛，防止面板传出脏值
    merged.maxEventNodes = clamp(Number(merged.maxEventNodes), 20, 200, DEFAULT_SETTINGS.maxEventNodes)
    merged.screenerTop = clamp(Number(merged.screenerTop), 10, 400, DEFAULT_SETTINGS.screenerTop)
    merged.screenerWorkers = clamp(Number(merged.screenerWorkers), 1, 16, DEFAULT_SETTINGS.screenerWorkers)
    merged.autoCliCrosscheck = merged.autoCliCrosscheck !== false
    merged.cliCrosscheckTimeoutSec = clamp(Number(merged.cliCrosscheckTimeoutSec), 30, 300, DEFAULT_SETTINGS.cliCrosscheckTimeoutSec)
    merged.minAdvUsd = clamp(Number(merged.minAdvUsd), 0, 1e9, DEFAULT_SETTINGS.minAdvUsd)
    merged.riskPerTrade = clamp(Number(merged.riskPerTrade), 0.0005, 0.1, DEFAULT_SETTINGS.riskPerTrade)
    merged.maxWeight = clamp(Number(merged.maxWeight), 0.01, 1, DEFAULT_SETTINGS.maxWeight)
    merged.defaultLimit = clamp(Number(merged.defaultLimit), 60, 1000, DEFAULT_SETTINGS.defaultLimit)
    if (!Array.isArray(merged.cliPriority)) merged.cliPriority = DEFAULT_SETTINGS.cliPriority
    // CEX 自主级别收敛：**只接受四个已知值**，非法值一律退回 readonly。
    // 这条尤其重要——它是实盘权限的入口，脏值绝不能被"就近当成 full"处理。
    if (!Object.prototype.hasOwnProperty.call(CEX_AUTONOMY, String(merged.cexAutonomy))) {
      merged.cexAutonomy = 'readonly'
    }
    if (!['conservative', 'balanced', 'aggressive', 'custom'].includes(String(merged.cexProfile))) {
      merged.cexProfile = 'conservative'
    }
    merged.cexAutoNotionalCapUsd = clamp(Number(merged.cexAutoNotionalCapUsd), 1, 1e6,
      DEFAULT_SETTINGS.cexAutoNotionalCapUsd)
    if (!(merged.cexEnabled && typeof merged.cexEnabled === 'object')) merged.cexEnabled = { ...DEFAULT_SETTINGS.cexEnabled }
    if (!Array.isArray(merged.cexExtraExchanges)) merged.cexExtraExchanges = []
    // label 是文件名的组成部分，必须过滤掉路径分隔符等危险字符
    merged.cexDefaultLabel = String(merged.cexDefaultLabel || 'main').replace(/[^a-z0-9_.-]/gi, '') || 'main'
    settings = merged
    try {
      if (settingsPath) {
        mkdirSync(dirname(settingsPath), { recursive: true })
        writeFileSync(settingsPath, JSON.stringify(settings, null, 2), 'utf8')
      }
      // 任何设置改动都重写权限文件——保证「面板显示的级别」与「实际生效的级别」永远一致
      syncAutonomyFile()
      return { ok: true, settings }
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e), settings }
    }
  }
  loadSettings()

  function clamp(v, lo, hi, dflt) {
    if (!Number.isFinite(v)) return dflt
    return Math.min(hi, Math.max(lo, v))
  }

  function projectName(cwd) {
    const s = String(cwd || '')
    const parts = s.split(/[\\/]/).filter(Boolean)
    return parts.length ? parts[parts.length - 1] : s
  }

  // Session 运行时只暴露 get id()，工作目录在 header.cwd（dsh-session 实测：
  // 无 get cwd() getter）。直接读 session.cwd 永远是 undefined → 空 cwd/"未知会话"。
  function sessionCwd(session) {
    if (!session) return ''
    return String(session.cwd || session.directory || (session.header && session.header.cwd) || (session.data && session.data.cwd) || '')
  }

  // v1.5.2：成员存活跨会话探测。
  // 取证：成员干活的事件落在**成员自己的 session 桶**（数脉 2dc6ef8c busy=true、
  // 14 个节点、current=跑研判流水线），而名册在 lead 桶——只读 lead 桶必然显示待命。
  // member.id === subagent/catalog 的 childId === 成员 session id，故按 id 查桶。
  // 成员的一次工具调用（screener 扫 300 个标的）可跑数分钟，期间无新事件，
  // 所以 running 节点用更宽的 MEMBER_LIVE_MS 判定（上限 10 分钟，与
  // wait_agent(timeout_ms=600000) 对齐），避免"正在干活却显示待命"。
  const MEMBER_LIVE_MS = 10 * 60 * 1000

  // ── kexi 相关性判定（v1.6.1）─────────────────────────────────────────
  // 现场问题（用户 2026-09-30 报）：**没派活，卡片里却满屏"团队进度"**。
  // 根因不是显示层画错，而是数据层把**所有**会话活动都灌了进来：
  //   ① 每次 kexi_run 都会自动建队 → 5 个 `team/member` 节点（"数脉 已就位"…）
  //      **status='ok'，会一直留在时间线里**，哪怕这个会话压根没派活；
  //   ② `turn/start` 每个回合都推节点，纯聊天也算；
  //   ③ `tool/call` 无任何过滤——改文件/跑 git/读网页全部进时间线，
  //      经 describeTool 配了中文描述后**看起来就像任务流程**；
  //   ④ `/activity` 返回所有 TTL 内会话，别的会话进度也堆在这张卡里。
  //
  // 解法是**白名单**而非黑名单：黑名单要靠"排除所有不像的"，漏一个就漏进来；
  // 白名单只认明确属于研判链路的工具，漏掉的代价是"少显示"而不是"多显示"——
  // 对一张给人看状态的卡片，少显示远好过错显示。
  const KEXI_TOOLS = new Set([
    // 本包模型工具
    'kexi_run', 'kexi_validate', 'kexi_dashboard', 'kexi_cex',
    'kexi_cli', 'kexi_news', 'kexi_ensure_team',
    // 成员委派（send_message 的 target 之一即成员）
    'market_data_specialist', 'technical_indicators', 'trend_forecaster',
    'risk_assessor', 'falsification_challenger',
    // 团队编排
    'send_message', 'list_agents', 'wait_agent', 'spawn_teammate',
    'interrupt_agent', 'team_task_create', 'team_task_list', 'team_task_get',
    'team_task_update',
  ])
  // 本地 CLI 交叉验证：agy_/codebuddy_/mimo_ 前缀
  const KEXI_CLI_RE = /^(agy|codebuddy|mimo)_(run|continue|status|quota)$/
  /** 同一波建队的成员事件合并窗口。spawn 五个成员通常在几秒内完成。 */
  const TEAM_BUILD_MERGE_MS = 30 * 1000

  /** 属于研判链路的节点 kind。**未知 kind 默认不显示**（见 isKexiNode 说明）。 */
  const KEXI_NODE_KINDS = new Set([
    'team_build',      // 团队就位（v1.6.1 起已合并为一条）
    'team_msg',        // 派活 / 回报
    'subagent',        // 成员会话开始（谁在干活）
    'subagent_end',    // 成员会话结束
    'kexi_step',       // 研判流水线步骤进度
  ])

  function isKexiTool(name) {
    const n = String(name || '')
    return KEXI_TOOLS.has(n) || KEXI_CLI_RE.test(n)
  }
  /**
   * 节点是否属于「研判链路」。
   *
   * 判定顺序：显式 kexi 标记 > tool 按工具名 > kind 白名单 > **默认 false**。
   *
   * ⚠ 默认 false 而不是 true：早先默认放行，结果 `turn_end`（回合结束标记）
   * 这种纯生命周期节点把一个只用了 `read` 工具的会话整个"点亮"了——
   * 非研判会话照样出现在卡片上，正是用户报的那个问题。
   * 方向必须和 KEXI_TOOLS 一致：**漏显示的代价是少一行，误显示的代价是用户
   * 不再相信这张卡片**。新增 kind 时请显式加进 KEXI_NODE_KINDS 或打 kexi 标记。
   */
  function isKexiNode(n) {
    if (!n) return false
    if (n.kexi === true) return true
    if (n.kexi === false) return false
    if (n.kind === 'tool') return isKexiTool(n.tool)
    return KEXI_NODE_KINDS.has(n.kind)
  }

  function memberSessionBusy(memberId, now) {
    if (!memberId) return null
    const b = activity[String(memberId)]
    if (!b) return null
    const nodes = b.nodes || []
    const run = [...nodes].reverse().find((n) => n.status === 'running')
    if (run && (now - Number(run.t || 0)) < MEMBER_LIVE_MS) {
      // v1.6.2：连同 tool/script/kind 一起返回——关系图要靠它判断这位成员
      // **此刻在干哪一类活**（取数 / 分析 / 风控）。只给一句 note 画不出分类。
      // script 来自 kexi_run 的 arguments——工具名恒为 kexi_run，本身没有区分度。
      return { note: String(run.label || '') + (run.detail ? '：' + clipStr(run.detail, 40) : ''),
        label: String(run.label || ''), tool: run.tool || null, script: run.script || null, kind: run.kind || null }
    }
    if (now - b.updatedAt < 60 * 1000) {
      const last = [...nodes].reverse().find((n) => n.kind === 'tool' || n.kind === 'team_msg')
      return { note: last ? String(last.label || '') : null, label: last ? String(last.label || '') : '',
        tool: (last && last.tool) || null, script: (last && last.script) || null, kind: (last && last.kind) || null }
    }
    return null
  }

  /**
   * 全部已知成员 childId 集合。
   * 成员自己的子会话**也在 activity 表里**（他们跑 kexi_run，会通过 kexi 过滤），
   * 于是关系图页会并排出现「主会话的真图 + 5 个成员会话的『尚未组建团队』」。
   * 用它把成员子会话标出来，前端据此跳过。
   */
  function memberSessionIds() {
    const out = new Set()
    for (const k of Object.keys(activity)) {
      const b = activity[k]
      const t = b && b.team
      if (t && t.members) {
        for (const m of Object.values(t.members)) if (m && m.id) out.add(String(m.id))
      }
      const codes = b && b.memberCodes
      if (codes) for (const childId of Object.keys(codes)) out.add(String(childId))
    }
    return out
  }

  // ── 工作分类（v1.6.2 关系图）─────────────────────────────────────────
  // 用户要的是"谁加入了工作、在干什么（数据搜集 / 数据分析 / 其他）"。
  // 分类**从成员正在跑的脚本名推导**，而不是按角色硬编码——同一个成员这一轮
  // 可能在取数、下一轮在做分析；角色标签回答不了"此刻"这个问题。
  // 顺序有意义：falsify 必须排在 analyze 之前（backtest/validate_report 两者
  // 都命中，但对证伪成员而言那是"质疑复核"而非"数据分析"）。
  const WORK_CATS = [
    ['falsify', '质疑复核', /ablation_compare|backtest|validate_report/i],
    ['orchestrate', '编排派活', /send_message|wait_agent|list_agents|kexi_ensure|team_task/i],
    ['collect', '数据搜集', /fetch_klines|screener|news_watch|datasources|wallet_watch|new_listing|listing_effect|portal_lookup|^exchanges|fetch_quote/i],
    ['risk', '风控执行', /cex|keepalive|track_report|token_stats|portfolio|kexi_notify|unlock_schedule/i],
    ['analyze', '数据分析', /indicators|position|timeframe|entry_plan|coin_profile|fast_analysis|derivs_sentiment|assemble_report|compact_report|dashboard/i],
  ]
  const CAT_LABEL = { other: '其他工作' }
  for (const pair of WORK_CATS) CAT_LABEL[pair[0]] = pair[1]

  function classifyWork(tool) {
    const t = String(tool || '')
    if (!t) return 'other'
    for (const pair of WORK_CATS) if (pair[2].test(t)) return pair[0]
    return 'other'
  }
  /** 没有正在跑的工具时，退回按角色给"通常在干什么"（并在图上标为弱推断）。 */
  function classifyByRole(memberName) {
    if (memberName === 'shuma') return 'collect'
    if (memberName === 'zhibei' || memberName === 'wangchao') return 'analyze'
    if (memberName === 'shouzhuo') return 'risk'
    if (memberName === 'zhengfu') return 'falsify'
    return 'other'
  }

  /**
   * 构建"谁加入了工作、在干什么"的关系图（v1.6.2）。
   *
   * 节点：主理人 + 每位在册成员（含未就位/已失败——它们也是"谁加入了"的一部分）。
   * 边：  派活（主理人→成员）与回报（成员→主理人），取自 team_msg 节点。
   *
   * ⚠ 诚实性：分类有两档可信度——`catFrom` 为 'tool' 表示**真的看到它在跑什么**，
   *   为 'role' 表示只是按角色推测。前端应当用样式区分，不能混为一谈。
   *   「谁在干什么」说不准时，宁可标"其他工作/待确认"，也不要编一个看起来确定的分类。
   */
  function buildGraph(b, now) {
    const nodes = []
    const edges = []
    if (!b) return { nodes, edges }
    const leadBusy = b.nodes.some((n) => isKexiNode(n) && n.status === 'running'
      && (now - Number(n.t || 0)) < BUSY_FRESH_MS)
    const leadLast = [...b.nodes].reverse().find((n) => isKexiNode(n) && n.kind === 'tool')
    nodes.push({
      id: 'lead', cn: 'K析', role: '主理人 · 统筹编排', cat: 'orchestrate',
      catLabel: CAT_LABEL.orchestrate, catFrom: 'tool', state: 'active',
      busy: leadBusy, activity: leadBusy && leadLast ? String(leadLast.label || '') : null,
      isLead: true, t: leadLast ? leadLast.t : b.updatedAt,
    })

    const members = (b.team && b.team.members) ? Object.values(b.team.members) : []
    for (const m of members) {
      const live = memberSessionBusy(m.id, now)
      // 分类输入：成员跑的是 `kexi_run` 包装器，工具名没有区分度；
      // 真正区分「取数 / 分析 / 风控」的是它带的那支脚本。
      // 两者一起喂给 classifyWork（任一命中即算），谁都不命中就归 other。
      const tool = (live && live.tool) || null
      const script = (live && live.script) || null
      const cat = (tool || script) ? classifyWork((script || '') + ' ' + (tool || '')) : classifyByRole(m.name)
      const busy = !!(live || (m.busy && m.lastNoteAt && (now - m.lastNoteAt) < BUSY_FRESH_MS))
      nodes.push({
        id: m.name, cn: m.cn, role: m.description || null,
        cat, catLabel: CAT_LABEL[cat] || CAT_LABEL.other,
        // tool=真看到在跑什么；role=只是按角色推测。两者可信度不同，前端要能区分。
        catFrom: tool ? 'tool' : 'role',
        state: m.phase, busy,
        activity: (live && live.note) || m.lastNote || null,
        tool: tool || null, script: script || null,
        t: m.lastNoteAt || m.updatedAt || b.updatedAt,
      })
    }

    // 边：每个成员只保留**最近一次**交互（派活/回报各一条），图才不会被历史淹没
    const lastEdge = new Map()
    for (const n of b.nodes) {
      if (n.kind !== 'team_msg' || !n.edgeFrom || !n.edgeTo) continue
      lastEdge.set(n.edgeFrom + '>' + n.edgeTo, n)
    }
    for (const n of lastEdge.values()) {
      edges.push({
        from: n.edgeFrom, to: n.edgeTo, rel: n.edgeRel || 'dispatch',
        status: n.status === 'running' ? 'running' : 'ok',
        at: n.t, label: n.label || null,
      })
    }
    return { nodes, edges, catLegend: CAT_LABEL }
  }

  function bucketFor(session) {    const id = String((session && session.id) || 'unknown')
    if (!activity[id]) {
      const cwd = sessionCwd(session)
      activity[id] = {
        id,
        cwd,
        name: projectName(cwd) || '未知会话',
        preset: null,
        nodes: [],
        // v1.5.2：桶创建时刻——用于判定"看板是不是本次跑出来的"。
        // 取证：上一轮 17:07 的 bull20_dashboard.html 被挂到 23:30 才开的会话上，
        // 卡片"最终成果"展示的是**上一次运行的旧结论**，极具误导性。
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }
    }
    return activity[id]
  }

  function pushNode(session, node) {
    const b = bucketFor(session)
    b.nodes.push({ t: Date.now(), seq: b.nodes.length, ...node })
    if (b.nodes.length > MAX_EVENTS_PER_SESSION) b.nodes.splice(0, b.nodes.length - MAX_EVENTS_PER_SESSION)
    b.updatedAt = Date.now()
    const keys = Object.keys(activity)
    if (keys.length > MAX_SESSIONS) {
      const stale = keys.map((k) => activity[k]).sort((a, b2) => a.updatedAt - b2.updatedAt)
      for (let i = 0; i < keys.length - MAX_SESSIONS && i < stale.length; i++) delete activity[stale[i].id]
    }
  }

  // ── 团队名册状态（v1.5.1）：per-session 维护"成员在不在、正干什么" ─────────
  // team/member 事件登记成员与 phase；team/message 事件更新成员最近动向。
  // /activity 响应里以 team 字段暴露，卡片据此点亮成员灯与动向。
  function rosterOf(session) {
    const b = bucketFor(session)
    if (!b.team) {
      b.team = { members: {}, updatedAt: Date.now() }
    }
    return b.team
  }

  function rosterUpdate(session, mem) {
    if (!mem || !mem.name) return
    const t = rosterOf(session)
    const name = String(mem.name)
    // v1.6.2：登记 childId → 成员**英文代号**（memberCodes）。
    // ⚠ 不要写进 `memberIds`——那张表是 childId → **中文名**的显示映射，
    //   由 subagent/catalog 维护，team/message 的标签翻译依赖它。
    //   混写会覆盖中文名，卡片上就变成「主理人 → shuma 派活」。
    // 关系图的边需要的是代号（节点 id 就是 m.name），所以单独一张表。
    if (mem.id) {
      const b0 = bucketFor(session)
      if (!b0.memberCodes || typeof b0.memberCodes !== 'object') b0.memberCodes = {}
      b0.memberCodes[String(mem.id)] = name
    }
    t.members[name] = {
      id: String(mem.id || ''),
      name,
      cn: MEMBER_CN[name] || name,
      phase: String(mem.phase || 'unknown'),   // provisioning | active | failed
      description: clipStr(mem.description, 90),
      lastNote: (t.members[name] && t.members[name].lastNote) || null,
      lastNoteAt: (t.members[name] && t.members[name].lastNoteAt) || null,
      busy: !!(t.members[name] && t.members[name].busy),
      updatedAt: Date.now(),
    }
    t.updatedAt = Date.now()
  }

  /**
   * 消息里的 senderId/targetId（childId 形态）→ 关系图节点 id（成员英文代号 / 'lead'）。
   * 与 memberByRosterId 刻意分开：那个要的是**中文名**（给人看的标签），
   * 这个要的是**代号**（给连线用的稳定 id）。混用会让边接不上节点。
   * 解析不出来就返回 null——**宁可这条边不画，也不要连到虚空**。
   */
  function memberCode(session, memberId) {
    if (!memberId) return null
    const s = String(memberId)
    if (session && String(session.id) === s) return 'lead'
    if (MEMBER_CN[s]) return s            // 传进来本来就是英文代号
    const b = bucketFor(session)
    const codes = b.memberCodes || {}
    if (codes[s]) return codes[s]
    return null
  }

  // childId（subagent/catalog 已登记）或成员英文名 → 中文名
  function rosterName(session, memberId) {
    if (!memberId) return null
    const b = bucketFor(session)
    const ids = b.memberIds || {}
    const s = String(memberId)
    // 目标是主理人自己的会话 id（成员→lead 的回报）
    if (session && String(session.id) === s) return '主理人'
    if (ids[s]) return ids[s]
    if (MEMBER_CN[s]) return MEMBER_CN[s]
    return s.slice(0, 8)
  }

  function memberByRosterId(session, memberId) {
    if (!memberId) return null
    const b = bucketFor(session)
    const ids = b.memberIds || {}
    const s = String(memberId)
    if (ids[s]) return ids[s]        // 中文名
    for (const [childId, cn] of Object.entries(ids)) {
      if (cn === s || MEMBER_CN[cn] === s) return cn
    }
    if (MEMBER_CN[s]) return MEMBER_CN[s]
    return null
  }

  // 成员最近动向（消息文本头部 60 字，卡片上显示"正在干什么"）
  function rosterNote(session, targetId, text) {
    try {
      const name = memberByRosterId(session, targetId)
      if (!name) return
      const t = rosterOf(session)
      // 中文名 → 英文 key（roster 用英文成员名做 key）
      let key = name
      for (const [en, cn] of Object.entries(MEMBER_CN)) {
        if (cn === name) { key = en; break }
      }
      if (!t.members[key]) return
      t.members[key].lastNote = clipStr(text, 60)
      t.members[key].lastNoteAt = Date.now()
      t.members[key].busy = true      // 收到派活 → 干活中；delivered/下一事件会翻回
      t.updatedAt = Date.now()
    } catch (e) { /* ignore */ }
  }

  function rosterDelivered(session, targetId) {
    try {
      const name = memberByRosterId(session, targetId)
      if (!name) return
      const t = rosterOf(session)
      let key = name
      for (const [en, cn] of Object.entries(MEMBER_CN)) {
        if (cn === name) { key = en; break }
      }
      if (!t.members[key]) return
      t.members[key].busy = false
      t.members[key].updatedAt = Date.now()
      t.updatedAt = Date.now()
    } catch (e) { /* ignore */ }
  }

  // 从消息 content 块数组提取纯文本
  function textOf(content) {
    if (typeof content === 'string') return content
    if (!Array.isArray(content)) return ''
    return content
      .filter((b2) => b2 && typeof b2 === 'object' && b2.type === 'text' && typeof b2.text === 'string')
      .map((b2) => b2.text)
      .join(' ')
  }

  // 内部：把一个已确认的看板绝对路径登记到指定会话桶。返回 true 若新建成功。
  function registerArtifactFor(b, absPath) {
    try {
      const real = realpathSync(absPath)
      const id = 'a' + (++artifactSeq).toString(36)
      artifacts.set(id, { path: real, name: real.split(/[\\/]/).pop(), sessionId: b.id })
      b.artifactId = id
      b.updatedAt = Date.now()
      return true
    } catch (e) { return false }
  }

  // ── 最终看板产物登记：从工具结果里识别离线 HTML 看板，登记后供面板内嵌 ──
  function recordArtifact(session, toolName, result) {
    try {
      if (!result || typeof result !== 'object' || !Array.isArray(result.out_files)) return
      // 只取最新一个 .html（离线看板）；kexi_dashboard 或跑流水线产出均可。
      const html = result.out_files
        .map((f) => String(f || ''))
        .filter((f) => isAbsolute(f) && /\.html?$/i.test(f))
        .pop()
      if (!html) return
      registerArtifactFor(bucketFor(session), html)
    } catch (e) { /* 文件不存在/非绝对路径等：忽略，不影响流程 */ }
  }

  // ── 惰性看板发现（健壮性兜底）：不依赖事件是否被正确捕获 ───────────────
  // 背景：成果登记曾两次因事件字段假设偏差失败（顶层字段 / message 嵌套），
  // 用户跑完却在页面看不到成果。这里在 /activity 组装时，对"尚无成果"的会话
  // 直接扫描其工作目录下非递归的看板文件，补登最新一个；15s 节流防高频 stat。
  //
  // v1.4.6：**优先读 kexi_out/latest.json**（插件每次成功渲染写的权威产物标记），
  // 只有读不到/失效时才回退 mtime 扫描 —— 目录里 algo_dashboard.html 与
  // _v2.html、final_picks 与 final_picks2 并存时，mtime 会猜错权威产物。
  const ARTIFACT_SCAN_DIRS = ['', 'kexi_out', 'out', 'output', 'reports']
  const DISCOVER_TTL_MS = 15 * 1000
  const LATEST_MARKER = 'kexi_out/latest.json'

  // 读 kexi_out/latest.json 里的权威看板（校验是绝对路径且真的存在、是 HTML）。
  // v1.5.2：产物必须**晚于本会话开始**才算本次成果（2s 容差吸收 fs 时间戳粒度）。
  // 否则会把上一轮跑出来的看板挂到本会话，卡片"最终成果"展示旧结论。
  function artifactIsFresh(b, absPath) {
    try {
      const born = Number(b.createdAt || b.updatedAt || 0)
      if (!born) return true
      const st = statSync(absPath)
      return st.mtimeMs >= born - 2000
    } catch (e) { return true }   // stat 失败不阻塞（宁可多显示）
  }

  function readLatestArtifact(b) {
    try {
      const cwd = String(b.cwd || '')
      if (!isAbsolute(cwd)) return null
      const marker = join(cwd, 'kexi_out', 'latest.json')
      if (!existsSync(marker)) return null
      const raw = JSON.parse(readFileSync(marker, 'utf8'))
      // 字段名兼容：dashboard 是 kexi_* 工具写入时的约定键。
      const cand = raw && (raw.dashboard || raw.dashboard_path || raw.artifact)
      if (typeof cand !== 'string' || !/\.html?$/i.test(cand)) return null
      const abs = isAbsolute(cand) ? cand : join(cwd, cand)
      if (!existsSync(abs)) return null
      if (!artifactIsFresh(b, abs)) return null
      return abs
    } catch (e) { return null }
  }

  function discoverArtifact(b) {
    try {
      const cwd = String(b.cwd || '')
      if (!isAbsolute(cwd)) return
      if (b.discoverAt && Date.now() - b.discoverAt < DISCOVER_TTL_MS) return
      b.discoverAt = Date.now()
      // ① 权威标记优先
      const marked = readLatestArtifact(b)
      if (marked) { registerArtifactFor(b, marked); return }
      // ② 回退：非递归扫描，取 mtime 最新的看板
      let best = null
      for (const sub of ARTIFACT_SCAN_DIRS) {
        const dir = join(cwd, sub)
        let entries
        try { if (!statSync(dir).isDirectory()) continue; entries = readdirSync(dir) } catch { continue }
        for (const name of entries) {
          // v1.6.4：放宽到**任意** .html。
          // 旧写法只认文件名含 dashboard/看板，于是 dashboard.py --out 的自定义名
          // （实测 `market_report.html`）被整个漏掉——看板明明生成了，
          // 卡片「最终成果」却永远空着，模型还为此多跑了一次 kexi_run 绕路。
          // 安全性由 artifactIsFresh 把关：必须**晚于本会话开始**才算本次成果。
          // 实测同一 kexi_out 下 11 个 html 里 10 个是上一轮的，全被 freshness 挡掉。
          if (!/\.html?$/i.test(name)) continue
          const full = join(dir, name)
          try {
            const st = statSync(full)
            if (!st.isFile()) continue
            if (!artifactIsFresh(b, full)) continue     // v1.5.2：上一轮的看板不认
            if (!best || st.mtimeMs > best.mtimeMs) best = { full, mtimeMs: st.mtimeMs }
          } catch { /* ignore */ }
        }
      }
      if (best) registerArtifactFor(b, best.full)
    } catch (e) { /* 发现失败不影响主流程 */ }
  }

  // ── v1.5.2：成果刷新 ─────────────────────────────────────────────────────
  // 取证（2026-09-30 00:09）：本轮跑完并把 latest.json 指向新看板
  // bull_picks_dashboard.html，但卡片"最终成果"仍显示 bull20_dashboard.html
  // （上一轮 17:07 的旧看板）——因为旧写法是 `if (!b.artifactId) discoverArtifact(b)`，
  // 成果一旦挂上就**永不刷新**，陈旧附件会一直粘住。
  //
  // 触发条件用**权威标记的 mtime 变化**而不是固定时间窗：轮询本来就每 1.5s 一次，
  // 一次 stat 的成本可忽略，而时间窗会让"跑完了但卡片还指着旧看板"持续到窗口过期。
  function refreshArtifact(b) {
    try {
      const cwd = String(b.cwd || '')
      if (!isAbsolute(cwd)) return
      const marker = join(cwd, 'kexi_out', 'latest.json')
      let mtime = 0
      try { mtime = statSync(marker).mtimeMs } catch { mtime = 0 }
      if (mtime && mtime === b.latestSeenMtime && b.artifactId) return
      b.latestSeenMtime = mtime
      const marked = readLatestArtifact(b)
      if (marked) {
        let same = false
        try { same = realpathSync(marked) === (b.artifactId ? artifacts.get(b.artifactId).path : null) } catch { /* ignore */ }
        if (!same) registerArtifactFor(b, marked)
        return
      }
      if (!b.artifactId) discoverArtifact(b)
    } catch (e) { /* 刷新失败不影响主流程 */ }
  }

  // ── 事件订阅：session/event（工具与回合活动） ────────────────────────────
  try {
    ctx.on('session/event', (session, event) => {
      try {
        if (!event || typeof event !== 'object') return
        const type = event.type
        const data = event.data || {}
        switch (type) {
          case 'turn/start':
            // kexi:false —— 纯聊天的回合不该出现在研判卡片上（它是"有活动"的信号，
            // 不是"在研判"的证据）。忙判定改由 kexi 节点承担。
            pushNode(session, { kind: 'turn', icon: '▶️', label: '新一轮开始', status: 'running', kexi: false })
            break
          case 'step/start':
            pushNode(session, { kind: 'step', icon: '🧠', label: '模型思考中', status: 'running', kexi: false })
            break
          case 'step/end':
            // 关闭最近一个仍在 running 的「模型思考中」节点（原实现为空 → 节点永久 running）。
            // 按原设计"思考不单独占节点"，直接把它从缓冲移除（工具节点不受影响）。
            try {
              const b0 = bucketFor(session)
              for (let i = b0.nodes.length - 1; i >= 0; i--) {
                if (b0.nodes[i].kind === 'step' && b0.nodes[i].status === 'running') { b0.nodes.splice(i, 1); break }
              }
            } catch (e) { /* ignore */ }
            break
          case 'tool/call': {
            const d = describeTool(data.name, data.arguments)
            const idx = bucketFor(session).nodes.length
            pushNode(session, {
              kind: 'tool', tool: data.name, icon: d.icon,
              label: d.label, detail: d.detail,
              callId: data.callId || null, status: 'running',
              kexi: isKexiTool(data.name),
              // v1.6.2：关系图要按**成员此刻在跑哪个脚本**分类，而成员跑的是
              // `kexi_run` 这个包装器，真正的脚本名（fetch_klines.py / indicators.py）
              // 只存在于 arguments 里。不单独存下来，分类会全部落到「其他工作」。
              script: (data.arguments && data.arguments.script) || null,
            })
            if (data.callId) toolStarts.set(String(data.callId), { sessionId: String(session && session.id), index: idx })
            break
          }
          case 'tool/result': {
            const b = bucketFor(session)
            // 真实事件结构（agent-loop appendToolResult + createToolResultMessage）：
            //   data = { turn, step, message, error?, meta? }
            //   message = { role:'tool', toolCallId, source:{callId}, content:[...], isError }
            // 顶层没有 callId/result/isError —— 必须从 message 取，否则全是 undefined、
            // 回溯标记失效（v1.4.3 实测：每个工具节点永久 running + 额外冒出"工具返回"）。
            const msg = data.message || {}
            const callId = (msg.toolCallId || (msg.source && msg.source.callId))
              ? String(msg.toolCallId || (msg.source && msg.source.callId))
              : null
            const payload = extractToolPayload(msg.content)   // 结构化结果（含 out_files 等）
            const isErr = msg.isError === true
            const summary = summarizeResult(payload)
            // 成功结果若含离线看板 HTML，登记为最终成果（供面板直接内嵌）。
            if (!isErr) recordArtifact(session, null, payload)
            // 回溯标记对应 tool 节点为完成态
            let hit = false
            if (callId) {
              for (let i = b.nodes.length - 1; i >= 0; i--) {
                if (b.nodes[i].callId === callId) {
                  b.nodes[i].status = isErr ? 'error' : 'ok'
                  b.nodes[i].result = summary
                  b.nodes[i].durationMs = Date.now() - b.nodes[i].t
                  hit = true
                  break
                }
              }
              toolStarts.delete(callId)
            }
            if (!hit) {
              pushNode(session, { kind: 'tool_result', icon: isErr ? '⚠️' : '📥', label: '工具返回', detail: summary, status: isErr ? 'error' : 'ok', kexi: false })
            }
            break
          }
          case 'turn/end': {
            const reason = data.reason && data.reason.kind ? data.reason.kind : null
            // 关闭最近一个仍在 running 的「新一轮开始」节点（原实现从不关 → 永久 busy）。
            try {
              const b0 = bucketFor(session)
              for (let i = b0.nodes.length - 1; i >= 0; i--) {
                if (b0.nodes[i].kind === 'turn' && b0.nodes[i].status === 'running') {
                  b0.nodes[i].status = reason === 'completed' ? 'ok' : 'idle'
                  b0.nodes[i].durationMs = Date.now() - b0.nodes[i].t
                  break
                }
              }
            } catch (e) { /* ignore */ }
            pushNode(session, {
              kind: 'turn_end', icon: reason === 'completed' ? '🏁' : '⏹️',
              label: reason === 'completed' ? '本轮完成' : `本轮结束(${reason || '未知'})`,
              status: reason === 'completed' ? 'ok' : 'idle',
              kexi: false,   // 回合生命周期标记，与 turn 同理
            })
            break
          }
          case 'agent/error':
            pushNode(session, { kind: 'error', icon: '❌', label: '出错', detail: clipStr(data.message || data.error, 180), status: 'error', kexi: false })
            break

          // ── 团队事件（v1.5.1：状态灯卡片"看不到团队动向"的根因修复）─────────
          // 取证（session-6d282c27 实测，2026-09-29）：team/member(8)、
          // team/message/queued(8)、team/message/delivered(8)、subagent/catalog(4)
          // 事件都经 root.session.append → session/event 广播到宿主，但本 switch
          // 原先只认 turn/step/tool——团队事件全部落空 → 活动流里没有任何
          // "数脉/指北在干活"的节点 → TeamRoster activeMembers() 匹配不到成员 →
          // 卡片全员待命 + 评估结果无处展示。这里把团队事件翻译成可读节点，
          // 并维护 per-session 的团队成员状态（roster）供 /activity 暴露。
          case 'team/member': {
            // data = {version:2, teamId, member:{id,name,description,provider,context,phase}}
            const mem = data.member || {}
            const cn = MEMBER_CN[String(mem.name)] || String(mem.name || '')
            const phase = String(mem.phase || '')
            // ⚠ v1.6.1：建队是**自动发生的**（每次 kexi_run 都会 autoEnsureTeam），
            // 旧实现一次建队推 5 个节点，于是"没派活"的会话里也满屏
            // 「数脉 已就位 / 指北 已就位 / …」，看起来像团队在干活。
            // 改为：同一波建队的成员事件**合并进一条**摘要节点，
            // 且只有真的派了活（后续有 team_msg 派活节点）才在时间线里显眼。
            const b0 = bucketFor(session)
            const recent = [...b0.nodes].reverse().find(
              (n) => n.kind === 'team_build' && (Date.now() - Number(n.t || 0)) < TEAM_BUILD_MERGE_MS)
            if (recent && !recent.failed) {
              if (!(recent.members || []).includes(cn)) {
                recent.members = (recent.members || []).concat([cn])
                recent.detail = recent.members.join(' / ')
              }
            } else {
              pushNode(session, {
                kind: 'team_build', icon: phase === 'failed' ? '❌' : '👥',
                label: phase === 'failed' ? `团队就位失败：${cn}` : '团队就位',
                detail: cn, members: [cn], member: mem.name || null,
                failed: phase === 'failed', status: phase === 'failed' ? 'error' : 'ok',
              })
            }
            rosterUpdate(session, mem)
            break
          }
          case 'team/message/queued': {
            // data = {version:2, teamId, message:{id,senderId,senderName,targetId,content:[{type:'text',text}]}}
            const msg = data.message || {}
            const from = MEMBER_CN[String(msg.senderName)] || String(msg.senderName || '?')
            const to = rosterName(session, msg.targetId)
            const text = textOf(msg.content)
            // 只为"主理人派活/催促"建节点（成员→lead 的回执由 delivered 收尾，
            // 否则同一来回会出现两条节点刷屏）。
            if (String(msg.senderName) === 'lead') {
              pushNode(session, {
                kind: 'team_msg', icon: '📤',
                label: `${from} → ${to} ${/催促/.test(text) ? '催促进度' : '派活'}`,
                detail: clipStr(text, 150), messageId: msg.id || null, member: memberByRosterId(session, msg.targetId),
                // v1.6.2：结构化边字段。关系图靠它连边，**不解析 label 字符串**——
                // 中文名会变、格式会改，拿 label 当数据源迟早断。
                edgeFrom: memberCode(session, msg.senderId) || 'lead',
                edgeTo: memberCode(session, msg.targetId) || to,
                edgeRel: 'dispatch',
                status: 'running',
              })
            } else {
              pushNode(session, {
                kind: 'team_msg', icon: '📥',
                label: `${from} → ${to} 回报结论`,
                detail: clipStr(text, 150), messageId: msg.id || null, member: msg.senderName || null,
                edgeFrom: memberCode(session, msg.senderId) || msg.senderName || from,
                edgeTo: 'lead', edgeRel: 'report',
                status: 'ok',
              })
            }
            rosterNote(session, msg.targetId, text)
            break
          }
          case 'team/message/delivered': {
            // data = {version:2, teamId, messageId, targetId}
            // 把最近一条发往该成员、仍在 running 的派活节点标为完成（已送达并开始干活）。
            const targetName = memberByRosterId(session, data.targetId)
            try {
              const b = bucketFor(session)
              for (let i = b.nodes.length - 1; i >= 0; i--) {
                const n = b.nodes[i]
                if (n.kind === 'team_msg' && n.status === 'running' && (!targetName || n.member === targetName)) {
                  n.status = 'ok'
                  n.result = '已送达，成员已开始处理'
                  n.durationMs = Date.now() - n.t
                  break
                }
              }
            } catch (e) { /* ignore */ }
            rosterDelivered(session, data.targetId)
            break
          }
          case 'subagent/catalog': {
            // data = {version:0, childId, childCreatedAt, mode:'continuable', label:'数脉 · …'}
            // 成员子会话登记：把 childId → 中文名的映射记进 roster，
            // 供后续 team/message 事件把 targetId/senderId 翻译成中文名。
            const label = String(data.label || '')
            const cn = label.split(' ')[0]
            if (cn && data.childId) {
              const b = bucketFor(session)
              b.memberIds = b.memberIds || {}
              b.memberIds[String(data.childId)] = cn
            }
            break
          }
          case 'assistant/message': {
            // 主理人最终评估结论（v1.5.1）：data.message.content 是内容块数组，
            // 长 text 块（>600 字）即为最终研判结论文本——存进会话桶的
            // evaluation 字段，卡片"评估结果"直接展示，避免用户跑完还要翻聊天记录。
            try {
              const msg = data.message || {}
              const blocks = Array.isArray(msg.content) ? msg.content : []
              const long = blocks
                .filter((b2) => b2 && b2.type === 'text' && typeof b2.text === 'string')
                .map((b2) => b2.text)
                .find((t) => t && t.length > 600)
              if (long) {
                const b = bucketFor(session)
                b.evaluation = clipStr(long, 1200)
                b.evaluationAt = Date.now()
                b.updatedAt = Date.now()
              }
            } catch (e) { /* ignore */ }
            break
          }
          default: break
        }
      } catch (e) { /* 单事件异常不影响宿主 */ }
    })
  } catch (e) { /* session/event 不可用时降级为仅状态灯 */ }

  // ── 事件订阅：subagent（谁在干活） ───────────────────────────────────────
  try {
    // 真实签名 emit("subagent/start", identity, parent)：identity={runId,provider,id,local}，
    // parent=委派方 agent（.id 是主会话 id）。兼容旧的单 payload 形态。
    ctx.on('subagent/start', (identityOrPayload, parent) => {
      try {
        const idn = identityOrPayload || {}
        const pid = parent && parent.id ? String(parent.id)
          : (idn.parentSessionId || idn.sessionId || idn.id || 'subagent')
        const prov = idn.provider
        const provName = typeof prov === 'string' ? prov : (prov && (prov.name || prov.type)) || ''
        // 兼容旧的单 payload：agentType/name 直接是成员名 → 映射中文（成员中文名信号；
        // 真实双参场景下成员名由紧邻的 tool/call 节点"委派 指北…"给出）。
        const cnName = { market_data_specialist: '数脉', technical_indicators: '指北', trend_forecaster: '望潮', risk_assessor: '守拙', falsification_challenger: '证伪' }[idn.agentType || idn.name] || ''
        const who = cnName || provName
        pushNode({ id: pid, cwd: (parent && sessionCwd(parent)) || idn.cwd || '' }, {
          kind: 'subagent', icon: '🧑‍🔬',
          label: (cnName ? '委派成员 ' + cnName : '成员会话启动') + (!cnName && who ? '（' + who + '）' : ''),
          detail: idn.description || (idn.runId ? 'run ' + String(idn.runId).slice(-8) : null),
          status: 'running',
        })
      } catch (e) { /* ignore */ }
    })
    ctx.on('subagent/end', (identityOrPayload, parent) => {
      try {
        const idn = identityOrPayload || {}
        const pid = parent && parent.id ? String(parent.id)
          : (idn.parentSessionId || idn.sessionId || idn.id || 'subagent')
        const msg = idn.lastAssistantMessage
        const tail = Array.isArray(msg) && msg.length ? String(msg[msg.length - 1].text || '').slice(0, 80) : ''
        pushNode({ id: pid, cwd: (parent && sessionCwd(parent)) || idn.cwd || '' }, {
          kind: 'subagent_end', icon: '🏁', label: '成员会话结束' + (idn.stopReason ? '：' + idn.stopReason : ''),
          detail: tail || null, status: 'ok',
        })
      } catch (e) { /* ignore */ }
    })
  } catch (e) { /* subagent 事件不可用时跳过 */ }

  // ── preset 状态快照（原有） ──────────────────────────────────────────────
  function mergeSnapshot(snap) {
    if (!snap || typeof snap !== 'object' || !snap.cwd) return
    const key = String(snap.cwd)
    sessions[key] = {
      cwd: key,
      name: snap.name || projectName(snap.cwd),
      phase: snap.phase || 'idle',
      symbol: snap.symbol || null,
      step: snap.step || null,
      stepIndex: Number(snap.stepIndex) || 0,
      stepTotal: Number(snap.stepTotal) || 0,
      lastStatus: snap.lastStatus || sessions[key]?.lastStatus || null,
      updatedAt: Number(snap.updatedAt) || Date.now(),
    }
    const keys = Object.keys(sessions)
    if (keys.length > MAX_SESSIONS) {
      const stale = keys
        .map((k) => sessions[k])
        .filter((s) => s.phase !== 'running')
        .sort((a, b) => a.updatedAt - b.updatedAt)
      const drop = Math.max(0, keys.length - MAX_SESSIONS)
      for (let i = 0; i < drop && i < stale.length; i++) delete sessions[stale[i].cwd]
    }
  }

  ctx.on('kexi/mode', (payload) => {
    try {
      if (payload && payload.active) {
        presetActiveUntil = Date.now() + PRESET_TTL_MS
        lastModeAt = Date.now()
      } else {
        presetActiveUntil = 0
      }
    } catch (e) { /* ignore */ }
  })

  ctx.on('kexi/status', (payload) => {
    try {
      const snap = payload && typeof payload === 'object' && payload.snapshot ? payload.snapshot : payload
      mergeSnapshot(snap)
      // 同步进活动流，让弹窗也能看到 pipeline 的 6 步推进
      if (snap && typeof snap === 'object' && snap.cwd) {
        const b = bucketFor({ id: 'kexi:' + String(snap.cwd), cwd: snap.cwd })
        b.preset = 'kexi-crypto'
        b.name = snap.name || projectName(snap.cwd)
        const step = snap.step || null
        const last = b.nodes[b.nodes.length - 1]
        if (step && (!last || last.stepLabel !== step)) {
          pushNode({ id: 'kexi:' + String(snap.cwd), cwd: snap.cwd }, {
            kind: 'kexi_step', icon: '📈', stepLabel: step,
            label: `研判 ${snap.symbol || ''} 第 ${snap.stepIndex || '?'}/${snap.stepTotal || '?'} 步：${step}`.trim(),
            status: snap.phase === 'failed' ? 'error' : (snap.phase === 'running' ? 'running' : 'ok'),
          })
        }
      }
    } catch (e) { /* 单次坏负载不影响宿主 */ }
  })

  // ── HTTP 路由 ────────────────────────────────────────────────────────────
  if (typeof ctx.inject === 'function') {
    ctx.inject(['webServer'], (webCtx) => {
      const ws = webCtx.get('webServer')
      if (!ws || typeof ws.register !== 'function') return

      function json(res, code, obj) {
        res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
        res.end(JSON.stringify(obj))
      }

      // 状态灯
      webCtx.effect(() => ws.register({
        kind: 'exact',
        path: '/kexi-dashboard/status',
        handler: (req, res) => {
          try {
            const now = Date.now()
            const presetActive = now < presetActiveUntil
            const OK_HOLD_MS = 10000
            const STALE_MS = 20000
            const list = Object.keys(sessions)
              .map((k) => sessions[k])
              .filter((s) => {
                const age = now - (Number(s.updatedAt) || 0)
                if (s.phase === 'running') return true
                if (s.phase === 'ok' || s.phase === 'failed') return age < OK_HOLD_MS
                return age < STALE_MS
              })
              .sort((a, b) => b.updatedAt - a.updatedAt)
            const running = list.reduce((n, s) => n + (s.phase === 'running' ? 1 : 0), 0)
            const state = running > 0 ? 'running'
              : (list.some((s) => s.phase === 'failed') ? 'failed'
                : (list.some((s) => s.phase === 'ok') ? 'ok' : 'idle'))
            json(res, 200, { state, activeSessions: list.length, running, sessions: list, presetActive, lastModeAt, version: PLUGIN_VERSION })
          } catch (e) {
            json(res, 500, { error: String((e && e.message) || e) })
          }
        },
      }), 'kexi-dashboard: status route')

      // 活动流（需求 1 数据源）
      webCtx.effect(() => ws.register({
        kind: 'exact',
        path: '/kexi-dashboard/activity',
        handler: (req, res) => {
          try {
            const now = Date.now()
            const limit = settings.maxEventNodes || DEFAULT_SETTINGS.maxEventNodes
            // v1.6.1：当前会话 id（前端能拿到 props.sessionId）。
            // 传了就**只返回该会话**——卡片是"这个会话的研判进度"，
            // 不是全局活动监视器。传不了就退化为"只显示有 kexi 活动的会话"。
            let only = null
            try {
              const u = new URL(req.url || '', 'http://x')
              const s = String(u.searchParams.get('sessionId') || '').trim()
              if (s) only = s
            } catch { /* URL 解析失败就不过滤 */ }
            const list = Object.keys(activity)
              .map((k) => activity[k])
              .filter((b) => now - b.updatedAt < ACTIVITY_TTL_MS)
              // v1.6.1：先按 kexi 节点筛会话，再排序取 limit。
              // 注意顺序很重要——若先 slice 再过滤，缓冲区里一堆非 kexi 噪音
              // 会把真正的研判节点挤出窗口（用户看到的反而是"没有活动"）。
              .filter((b) => b.nodes.some(isKexiNode))
              .filter((b) => (only ? b.id === only : true))
              .sort((a, b) => b.updatedAt - a.updatedAt)
              .map((b) => {
                // v1.5.2：以 REFRESH_TTL_MS 节流刷新成果（权威标记优先），
                // 修"成果挂上后永不刷新 → 卡片长期显示上一轮看板"。
                refreshArtifact(b)
                // ⚠ v1.6.1：只取 kexi 节点。忙判定、工具计数、当前动作**全部**
                // 基于它们——这样"在写代码/纯聊天"不会被误报成"研判中"。
                const nodes = b.nodes.filter(isKexiNode).slice(-limit)
                // v1.5.2：忙碌判定窗口 60s → 180s。
                // 取证：主理人 `wait_agent(timeout_ms=600000)` 会静默等待最多 10 分钟，
                // 期间**不产生任何 session 事件**；旧 60s 窗口一过就把会话判为不忙，
                // 卡片在团队真正干活时反而显示"待命/无响应"。
                // 180s 仍保留"卡死残留不算 busy"的原意（超期未关闭的节点不误报）。
                // ⚠ v1.6.1：**时间线只展示 kexi 节点，但"当前在干什么"读全量**。
                // 这是两个不同的问题：
                //   · 「本会话的研判进度」→ 该过滤，纯聊天/改文件不该占版面；
                //   · 「此刻在干什么」      → 不该过滤，否则研判到一半模型在思考时
                //     卡片会一片空白，工具间隙还会闪。
                // 且误报风险已由**会话级过滤**兜住：没有 kexi 节点的会话
                // （纯聊天/写代码）根本不会进 list，谈不上"混进来"。
                const runningNode = [...b.nodes].reverse().find(
                  (n) => n.status === 'running' && (now - Number(n.t || 0)) < BUSY_FRESH_MS) || null
                const toolCount = nodes.filter((n) => n.kind === 'tool').length
                const cliCount = nodes.filter((n) => n.kind === 'tool' && /^(agy|codebuddy|mimo)_/.test(String(n.tool || ''))).length
                // v1.5.2：等待团队期间，即使 running 节点已超窗，只要**回合未结束**
                // （仍有 turn 节点 running / 会话有 running 的 wait_agent 派活），
                // 也算 busy —— 修"主理人在等成员，卡片却显示空闲"。
                const awaitingTeam = nodes.some((n) => n.status === 'running'
                  && /等待成员回报|派活给/.test(String(n.label || '')))
                // ⚠ v1.6.1：turn 节点已被标 kexi:false 并从 `nodes` 里滤掉，
                // 但**忙判定必须仍然读它**——v1.5.2 解决的正是
                // "wait_agent 静默等 10 分钟、期间不产生任何事件"的空窗，
                // 那里唯一还活着的信号就是未关闭的 turn。
                // 误报风险已由**会话级过滤**兜住：没有 kexi 节点的会话
                // （纯聊天/写代码）根本不会进入 list。
                const turnOpen = b.nodes.some((n) => n.kind === 'turn' && n.status === 'running')
                // 团队名册（v1.5.1）：team/member 事件登记的成员状态 + 最近动向
                let team = null
                if (b.team && b.team.members && Object.keys(b.team.members).length) {
                  team = {
                    members: Object.values(b.team.members).map((m) => {
                      // v1.5.2：成员忙碌态跨会话兜底（成员事件在成员自己的桶里）
                      const live = memberSessionBusy(m.id, now)
                      return {
                        name: m.name, cn: m.cn, phase: m.phase,
                        busy: !!(live || (m.busy && m.lastNoteAt && (now - m.lastNoteAt) < BUSY_FRESH_MS)),
                        lastNote: live && live.note ? live.note : (m.lastNote || null),
                      }
                    }),
                  }
                }
                // 主理人最终评估结论（v1.5.1）：长 assistant 文本块，卡片直接展示
                let evaluation = null
                if (b.evaluation && b.evaluationAt && (now - b.evaluationAt) < ACTIVITY_TTL_MS) {
                  evaluation = { text: b.evaluation, at: b.evaluationAt }
                }
                // v1.5.2：等待团队时也算 busy（wait_agent 静默期不再显示"空闲"）
                const isBusy = !!runningNode || awaitingTeam || turnOpen
                const currentNode = runningNode
                  || (awaitingTeam ? [...nodes].reverse().find((n) => n.status === 'running' && /等待成员回报|派活给/.test(String(n.label || ''))) : null)
                return {
                  id: b.id, name: b.name, cwd: b.cwd, preset: b.preset,
                  updatedAt: b.updatedAt,
                  busy: isBusy,
                  current: currentNode ? { icon: currentNode.icon, label: currentNode.label, detail: currentNode.detail || null, tool: currentNode.tool || null } : null,
                  stats: { nodes: b.nodes.length, tools: toolCount, cli: cliCount },
                  nodes: nodes.slice(-Math.min(limit, settings.maxEventNodes || 60)),
                  team,
                  // v1.6.2：关系图（谁加入了工作 / 在干什么）。
                  // 没有名册就没有图——空名册时返回 null，前端显示"团队尚未组建"，
                  // 而不是画一张空图让人以为坏了。
                  graph: (b.team && b.team.members && Object.keys(b.team.members).length)
                    ? buildGraph(b, now) : null,
                  evaluation,
                  artifact: b.artifactId && artifacts.has(b.artifactId)
                    ? { id: b.artifactId, name: artifacts.get(b.artifactId).name, url: '/kexi-dashboard/artifact?id=' + b.artifactId }
                    : null,
                }
              })
            const busy = list.filter((s) => s.busy).length
            // v1.6.2：标出成员子会话（前端关系图页据此跳过它们）
            const memIds = memberSessionIds()
            json(res, 200, {
              state: busy > 0 ? 'running' : (list.length ? 'idle' : 'empty'),
              busySessions: busy,
              version: PLUGIN_VERSION,   // v1.6.4：卡片印版本，见文件头说明
              sessions: list.map((s) => ({ ...s, isMemberSession: memIds.has(String(s.id)) })),
              settings: {
                showProgressPopup: settings.showProgressPopup,
                showCliNodes: settings.showCliNodes,
                maxEventNodes: settings.maxEventNodes,
                cliAsMembers: settings.cliAsMembers,
                cliEnabled: settings.cliEnabled,
                cliPriority: settings.cliPriority,
              },
            })
          } catch (e) {
            json(res, 500, { error: String((e && e.message) || e) })
          }
        },
      }), 'kexi-dashboard: activity route')

      // 最终看板产物服务（供面板 iframe 内嵌/新标签打开）。
      // 安全：只认 artifacts 表里已登记的 id（不可由请求方指定任意路径），文件存在、
      // 必须是 .html；命中后再 realpath 校验一次，防止登记后被替换/软链。
      webCtx.effect(() => ws.register({
        kind: 'exact',
        path: '/kexi-dashboard/artifact',
        handler: (req, res) => {
          try {
            const u = new URL(req.url, 'http://127.0.0.1')
            const id = u.searchParams.get('id') || ''
            const art = artifacts.get(id)
            if (!art || !/\.html?$/i.test(art.path)) { json(res, 404, { error: 'artifact not found' }); return }
            let real
            try { real = realpathSync(art.path) } catch (e) { json(res, 404, { error: 'artifact gone' }); return }
            const buf = readFileSync(real)
            res.writeHead(200, {
              'Content-Type': 'text/html; charset=utf-8',
              'Cache-Control': 'no-store',
              'X-Content-Type-Options': 'nosniff',
              'Content-Security-Policy': "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; frame-ancestors 'self'",
            })
            res.end(buf)
          } catch (e) {
            json(res, 500, { error: String((e && e.message) || e) })
          }
        },
      }), 'kexi-dashboard: artifact route')

      // 设置（需求 3）
      webCtx.effect(() => ws.register({
        kind: 'exact',
        path: '/kexi-dashboard/settings',
        handler: (req, res) => {
          try {
            if (req.method === 'GET') {
              json(res, 200, { ok: true, settings, defaults: DEFAULT_SETTINGS })
              return
            }
            if (req.method === 'POST' || req.method === 'PUT') {
              let body = ''
              req.on('data', (c) => { body += c; if (body.length > 64 * 1024) req.destroy() })
              req.on('end', () => {
                let patch = null
                try { patch = body ? JSON.parse(body) : {} } catch (e) { json(res, 400, { ok: false, error: 'JSON 解析失败' }); return }
                json(res, 200, saveSettings(patch))
              })
              return
            }
            json(res, 405, { ok: false, error: 'method not allowed' })
          } catch (e) {
            json(res, 500, { ok: false, error: String((e && e.message) || e) })
          }
        },
      }), 'kexi-dashboard: settings route')

      // ── CEX API 凭据（v1.5.5）───────────────────────────────────────────
      // 设计要点（安全）：
      //   ① **永不回显密钥**。GET 只返回"是否已配置 + 掩码尾号"，
      //      不返回 api_key/api_secret 明文——设置页是浏览器里的 DOM，
      //      回显等于把密钥摊在页面上，任何脚本/扩展/截屏都能拿到。
      //   ② 落盘走 cex_keystore.py 的 DPAPI 加密（Windows 用户凭据保护），
      //      不写进 settings.json 这个明文配置文件。
      //   ③ 留空即不改动（PATCH 语义），避免"打开设置面板点保存"就抹掉密钥。
      //   ④ 默认 DRY_RUN：即使配了密钥，实盘下单仍需用户在开关里显式关闭。
      webCtx.effect(() => ws.register({
        kind: 'exact',
        path: '/kexi-dashboard/cex',
        handler: (req, res) => {
          try {
            if (req.method === 'GET') {
              json(res, 200, { ok: true, ...cexStatus() })
              return
            }
            if (req.method === 'POST' || req.method === 'PUT') {
              let body = ''
              req.on('data', (c) => { body += c; if (body.length > 64 * 1024) req.destroy() })
              req.on('end', async () => {
                let patch = null
                try { patch = body ? JSON.parse(body) : {} } catch (e) { json(res, 400, { ok: false, error: 'JSON 解析失败' }); return }
                json(res, 200, await cexSave(patch))
              })
              return
            }
            if (req.method === 'DELETE') {
              json(res, 200, cexForget(cexQueryParam(req, 'exchange'), cexQueryParam(req, 'label')))
              return
            }
            json(res, 405, { ok: false, error: 'method not allowed' })
          } catch (e) {
            json(res, 500, { ok: false, error: String((e && e.message) || e) })
          }
        },
      }), 'kexi-dashboard: cex route')
    })
  }
}
