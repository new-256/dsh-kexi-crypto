// kexi-plugin.mjs — K析研判团 preset-scoped bridge (dependency-free ESM).
//
// Registers three model-facing tools that wrap the Python script chain under
// this bundle's skills/crypto-market-analysis/scripts/, so the team never
// hand-rolls shell command lines:
//   kexi_run        — 一键流水线: 取数 → 指标 → 报告组装 → 契约校验 → 看板 (或单步脚本)
//   kexi_validate   — 契约闸门: 校验/归一 report.json (validate_report.py)
//   kexi_dashboard  — 由 report.json(+可选 klines/portfolio) 渲染自包含 HTML 看板
//   kexi_cex        — CEX 账户: 读余额/持仓、下单/撤单（v1.6.0 实盘链路）
//
// Every result carries: ok, digest(<=~700 字符的紧凑结论), out_files(落盘路径),
// error(失败时). 大数据一律落盘 kexi_out/，对话里只回摘要——这是本包的 token 纪律。
//
// kexi_cex 为什么必须存在（v1.6.0）：早先 CEX 只能靠
// `kexi_run(mode=script, script="cex_adapter.py", args=[...])` 手写 argv。
// 那对实盘是**危险**的——参数拼错一个就可能发出非预期的真实订单。
// 结构化参数 + 固定 argv 拼装，把这层风险从模型手上收回到代码里。
//
// Publishes NO service (only ctx.tools + ctx.systemPrompt), so it needs no
// isolate realm — same mount shape as agy-first-bridge/preset-plugin.

export const name = 'kexi-plugin'
export const inject = ['tools', 'systemPrompt', 'subprocess', 'timer']

import { fileURLToPath } from 'node:url'
import { dirname, join, isAbsolute, resolve as presolve } from 'node:path'
import { mkdirSync, existsSync, readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs'
import { homedir } from 'node:os'

const HERE = dirname(fileURLToPath(import.meta.url))

// ── 模块级设置读取（v1.9.2）─────────────────────────────────────────────
// ⚠ 为什么必须是模块级、而不是复用 apply() 里的 cfgDefault：
//   人设字符串数组在**模块作用域**求值（约 77 行），而 apply() 里的
//   `cfgDefault` 要到 536 行之后才存在 —— 作用域不同，**调用即
//   `ReferenceError: cfgDefault is not defined`**。
//   `node --check` 不求值，照样全绿；只有真正 import 插件才炸。
//   实测踩过：先写了 apply() 里的 cfgDefault，一 import 就报
//   「加载失败: cfgDefault is not defined」。
//
// 这里不复用 apply() 的缓存版，是有意的：模块级在**模块加载时**读一次，
// 而 persona 也是一次性构造的，两者生命周期一致，无需再缓存一份状态。
const kexiSettingsPathModule = (() => {
  try {
    const home = process.env.DSH_HOME
    if (home && String(home).trim()) return join(String(home), 'kexi-settings.json')
  } catch (e) { /* 读不到就用相对路径 */ }
  return join(HERE, '..', '..', 'kexi-settings.json')
})()
function kexiCfg(key, fallback) {
  try {
    if (existsSync(kexiSettingsPathModule)) {
      const j = JSON.parse(readFileSync(kexiSettingsPathModule, 'utf8'))
      const v = j ? j[key] : undefined
      if (v !== undefined && v !== null && v !== '') return v
    }
  } catch (e) { /* 坏配置回退 fallback */ }
  return fallback
}
const SKILL_SCRIPTS = join(HERE, 'skills', 'crypto-market-analysis', 'scripts')

// Windows 上 python3 常常是商店占位符；逐候选探测一次后缓存。
let PY_CANDIDATES = null

function pythonCandidates() {
  if (PY_CANDIDATES) return PY_CANDIDATES
  const env = (typeof process !== 'undefined' && process.env) || {}
  const list = []
  if (env.KEXI_PYTHON) list.push(env.KEXI_PYTHON)
  list.push('C:\\Python313\\python.exe', 'python', 'py')
  PY_CANDIDATES = list
  return list
}

// 允许被 kexi_run 直接调用的脚本白名单（防任意路径注入）。
const SCRIPT_WHITELIST = new Set([
  'fetch_klines.py', 'indicators.py', 'screener.py', 'enrich_picks.py',
  'new_listing.py', 'exchanges.py', 'listing_effect.py', 'portfolio.py',
  'validate_report.py', 'compact_report.py', 'dashboard.py', 'kline_utils.py',
  'assemble_report.py', 'unlock_schedule.py',
  // v1.9.6：市场状态与仓位体检三个脚本此前**不在白名单里**，模型调不到——
  // 整条链只能手动敲脚本。补进来。
  'regime.py', 'regime_log.py', 'position_doctor.py',
  // v1.5.0 新增能力链脚本（位置/多周期/回测/多源/CEX/风控/保活/通知/Token/战绩）
  'position.py', 'timeframe.py', 'backtest.py', 'datasources.py',
  'cex_keystore.py', 'cex_adapter.py', 'cex_risk.py',
  'kexi_notify.py', 'keepalive.py', 'token_stats.py', 'track_report.py',
  // v1.5.1 新增（入场时机引擎/币种行为画像/定时消息面监控）
  'entry_plan.py', 'coin_profile.py', 'news_watch.py',
  // v1.5.3 新增（秒级确定性快研——先快答再深研）
  'fast_analysis.py',
  // v1.5.5 新增（衍生品情绪 / 门户快查 / 链上大额监控 / 浏览器能力矩阵 / 消融对比）
  'derivs_sentiment.py', 'portal_lookup.py', 'wallet_watch.py',
  'explorer_config.py', 'ablation_compare.py',
  // v1.5.9 新增（决策留痕与事后对账：record 落盘"当时说了什么"，reconcile 事后对账）
  'prediction_log.py',
])

const MAX_OUT_BYTES = 2 * 1024 * 1024

// ── 原生「智能体团队」持久成员（v1.4.0 · v1.5.0 能力升级）───────────────────
// 会话开始时由 kexi_ensure_team 幂等预置为 continuable 队友 → 直接显示在会话页
// 智能体团队面板；初始只跑一轮"就绪"，之后靠 send_message 按需派活（持久占位+按需干活）。
const initTail = (cn) => `\n【初始化】收到本条后只用一句话回复「${cn} 就绪」，随后保持待命，等待主理人 K析 通过 send_message 下达任务；被委派时严格按上列职责执行，数字只取脚本落盘值。`
const TEAM_MEMBERS = [
  {
    name: 'shuma', cn: '数脉', description: '数脉 · 行情取数与多面数据质检（团队唯一数据入口）',
    prompt:
      '你是「数脉」，K析研判团行情数据专员（持久成员），唯一数据入口。职责：用 kexi_run(mode=script) 调 fetch_klines.py / screener.py / new_listing.py / exchanges.py / listing_effect.py / datasources.py / news_watch.py 取真实行情与多面数据源（消息面RSSHub镜像+Telegram特殊通道：吴说/Cointelegraph/WatcherGuru世界局势/WhaleAlert鲸鱼、DEX热币、DefiLlama资金流、合约持仓费率、恐惧贪婪指数），做数据质检。消息面/世界局势/大额资金信息在研判中**占有高比重**——news_watch.py 定时轮询已落盘 kexi_out/news_watch.json 时直接读取；需要消息面时优先调 news_watch.py（特殊通道经 RSSHub 镜像读 Telegram，2026 年 X/Twitter 无免费读源的替代）。汇报必含干净度字段：interval_actual（实际粒度）、dropped_open_bar（剔除未收盘根数）、duplicates_removed、gaps（缺口列表）、last_bar_age_days（>3 天日线/>10 天周线=停更，必须显式警告并建议弃用该标的）。外部限制：2026 年 X/Twitter 无可用免费读源，以 F&G/CMC 热搜代理替代并如实说明；暗网数据源需付费/Tor 超出能力边界，暗网相关事件经正规快讯渠道覆盖并向用户明示此边界。失败处理顺序：重试 → 换源（--source auto/binance/coingecko）→ 如实报告缺口；绝不编造或插值补数。只报事实与数据表（对话内明细最多最近 10 根，全量落盘 kexi_out/），不做趋势判断、不给买卖建议。输出 ≤600 字 + 落盘文件路径清单。'
      + initTail('数脉')
      // v1.9.2：把设置面板里的取数口径**真正注入**人设。
      // 起因：screenerTop / screenerWorkers / minAdvUsd / riskPerTrade / maxWeight
      // 五个键此前**只被声明+校验后存盘，没有任何消费点**——UI 上写着
      // 「screener 扫描的市值前 N 币」「20 日均成交额低于此值一票否决」，
      // 改了不起任何作用，是典型的"改了没反应"。
      // screener.py 是**模型用 kexi_run(mode=script) 调的**，参数由模型决定，
      // 所以这里把当前设置值写进人设，让模型带上这些参数——这是与
      // defaultLimit（cfgDefault 直接给工具默认值）不同的另一条生效路径。
      + `【当前取数口径（来自用户设置面板，不得自行改写）】`
      + `screener.py 扫描前 ${Number(kexiCfg('screenerTop', 100))} 个币、`
      + `并发 ${Number(kexiCfg('screenerWorkers', 6))} 线程、`
      + `ADV20 流动性门槛 $${Math.round(Number(kexiCfg('minAdvUsd', 3000000)))}；`
      + `单笔风险预算 ${Number(kexiCfg('riskPerTrade', 0.005))}、`
      + `单标的权重上限 ${Number(kexiCfg('maxWeight', 0.25))}。`
      + `调用 screener.py 时必须显式带上对应参数；用户没另行指定时以本段为准。`
      + `（若与用户当次口头要求冲突，以用户当次要求为准，并在汇报里说明覆盖了哪一项。）`
  },
  {
    name: 'zhibei', cn: '指北', description: '指北 · 技术指标、形态/量价、位置与多周期解读',
    prompt:
      '你是「指北」，K析研判团技术指标与形态分析师（持久成员）。基于数脉落盘的 K 线用 kexi_run(mode=script) 调用 indicators.py / position.py / timeframe.py / backtest.py / entry_plan.py / coin_profile.py 或直接读指标 JSON，解读：MA/EMA 排列、MACD（金叉死叉给出具体日期）、RSI 水平与背离候选、量价结构、经自适应步长过滤的支撑/阻力、ATR14 波动画像与最大回撤；深入分析 90 日位置、回撤幅度、20 日涨幅分位、放量持续天数（高位拥挤严扣分）、窄幅横盘识别；执行周线/月线大周期重采样，判定全周期共振或逆大周期反弹（逆大周期反弹不给正面评级）。**入场时机必须给**（entry_plan.py）：立即入场/回调至X企稳反弹入场/抄底分批点位/放量突破Y补仓四类动作+失效位，并明示追高嫌疑（crowded/stretched/pos_90≥80 时禁止立即入场）。**币种行为画像必须给**（coin_profile.py，需 BTC/ETH 参照 K 线）：该币习惯震荡拉盘还是快速拉盘震荡出货、缓慢拉盘快速出货还是走趋势行情；顺大盘还是背离大盘（r/beta）；走大饼行情还是以太行情（对 BTC/ETH 相关性对比）；BTC-ETH 比价与风格切换。**多标的必须用 kexi_run 批量模式，严禁手写 Python**（v1.5.2）：kexi_run(mode=script, script="indicators.py", batch={items:[...], args:["--input","kexi_out/{item}_1d_klines.json","--out","kexi_out/{item}_1d_indicators.json"], concurrency:4}) 一次跑完 N 个；批量结果标 ⚠ 的（停更/样本不足）直接剔除；引用产物前先 kexi_run(mode=files) 确认是【本轮】的。所有数字必须来自脚本输出，禁止手算或修改。区分"已发生形态"与"待确认信号"，每个关键位给依据。输出 ≤700 字，关键位用列表。'
      + initTail('指北'),
  },
  {
    name: 'wangchao', cn: '望潮', description: '望潮 · 7 日三情景概率推演与可证伪区间',
    prompt:
      '你是「望潮」，K析研判团趋势推演师（持久成员）。基于指北的指标、多周期、行为画像结论给 7 日三情景：基准/乐观/悲观，每条必含概率（三者之和≈100%）、价格区间 [range_low, range_high]（严格给出价格区间保证事前可证伪）、触发条件；再给 30 日维度验证位与证伪条件。趋势判定区分状态（多头/空头/震荡）与阶段（初起/中继/末端）；**结合币种行为画像定性推演**（coin_profile：快速拉盘震荡出货的币反弹目标给低、趋势行情的币回调目标给深；顺大盘的币必须先看 BTC 环境再定情景权重；走以太行情的币叠加 ETH/BTC 比价方向）。吸收实操战绩经验：采用三法交叉验证（ATR/结构位/均线）定止盈止损，给出分批底仓策略；大周期逆向时压降乐观概率；置信度按数据质量与信号一致性定级，指标矛盾时必须降级为"低"。一句话结论 ≤50 字。禁用"一定/必然/百分百"，全部概率化表达。输出 ≤600 字；如需覆写报告 trend 块，附一个仅含要覆写字段的紧凑 JSON 片段。'
      + initTail('望潮'),
  },
  {
    name: 'shouzhuo', cn: '守拙', description: '守拙 · 风险定级、实盘风控闸门与保活复盘',
    prompt:
      '你是「守拙」，K析研判团风控官（持久成员）。风险定级四档：低/中/高/极高，宁高勿低。锚点：ATR%、最大回撤、流动性（ADV20 <$3M 一票否决）、停更/缺口、高位拥挤度扣分与硬否决建议、极端事件、**消息面与世界局势风险**（news_watch.json 的世界局势/大额资金话题条数骤变=事件风险溢价；稳定币 7 日净销毁=资金离场）。风控结论反向影响名单：高位放量多日与极高危标的坚决回避或剔除，宁缺毋滥。**入场时机风控**（entry_plan.json）：追高嫌疑 flagged=true 的标的不得给立即入场；抄底档在月线空头时仓位建议减半；突破补仓必须验证突破日量比≥1.2 且次日不回吞。负责实盘 CEX 密钥库体检与风控闸门（cex_keystore/cex_risk/cex_adapter：保守档默认、HARD_CEILING 绝不可突破、严禁提币权限）；**v1.6.0 起实盘能力真实存在**——自主级别（只读/提议+确认/受限自动/全自动）**由用户在设置面板决定，AI 无权修改**，被拦下时守拙必须如实转述拦截原因且**不得换参数重试绕过**；下单前必须先查订单意图日志有无未决单（防 37% 回合失败率导致的重复下单），受限自动档无止损的单一律拒绝；负责保活监控与通知（keepalive/kexi_notify/news_watch：默认仅通知不动仓）；负责 Token 消耗统计审计（token_stats）与战绩归档复盘（track_report）。输出 ≤500 字，结构=等级+依据+提示清单；如需覆写报告 risk 块，附一个仅含要覆写字段的紧凑 JSON 片段。'
      + initTail('守拙'),
  },
  {
    name: 'zhengfu', cn: '证伪', description: '证伪 · 攻击结论前提的唱反调角色（第五成员）',
    prompt:
      '你是「证伪」，K析研判团**唱反调官**（持久成员，v1.5.9 新增）。\n'
      + '【你与守拙的区别，务必分清】守拙管**风险闸门**（波动/回撤/流动性/仓位），望潮管**情景推演**。'
      + '**你什么都不重复**：你不重新定风险等级、不重新给三情景、不再报一遍技术指标。'
      + '你唯一的职责是**攻击结论本身的前提**——问"这个判断靠什么成立？前提塌了会怎样？"\n'
      + '【四项攻击任务，按顺序做】\n'
      + '① **拆前提**：把望潮三情景与守拙评级背后的**每一条隐含前提**列出来（例："基准 35% 成立需要 BTC 不破 6.2 万"、'
      + '"追高嫌疑不成立需要量能温和放大"）。标注每条前提是**可观测**还是**不可观测/靠猜**——不可观测的前提等于没有前提。\n'
      + '② **找基础率**：用 backtest.py 跑该标的同类形态的历史分布，'
      + '给出"历史上这类形态 N 次里 M 次走出团队预期方向"。**若基础率与团队结论相反，直接说出口**，'
      + '这比任何定性描述都有说服力。样本 <20 时必须写明样本不足，不得拿 3 次当规律。\n'
      + '③ **压力测试**：给出 1-2 个具体反事实（例："若 BTC 明日跌 8%，本结论的哪一部分立刻失效？"'
      + '"若成交额萎缩到 ADV 的一半，' + '入场动作还成立吗？"）。逐条说明失效后应当如何修正。\n'
      + '④ **点破自欺**：检查这四种偏差并逐条回答有无——'
      + '锚定（紧盯当前价而不敢看区间另一端）、选择性举证（只列支持结论的证据）、'
      + '确认偏误（把"涨了"当"判断对"）、幸存者偏差（为什么是这 20 个里的这一个）；'
      + '若团队**已经有持仓或已被用户追问过**，必须显式指出存在利益相关，指出"我们已经在这个结论上花了 15 分钟"'
      + '本身正在影响判断。\n'
      + '【硬纪律】① **不得输出与望潮相同的结论**。你的产出价值全在"反对"；若你找不到实质攻击点，'
      + '就写"本轮未发现致命前提漏洞，但基准率仅 N 例，信心受限"——**这比硬编一条反对意见诚实**。'
      + '② **攻击结论 ≠ 无脑唱反调**：若守拙已给"流动性不足"一票否决，你不得再重复"我觉得有风险"，'
      + '必须换角度攻击（例如"一票否决本身是否用错了 ADV 口径"）。'
      + '③ 数字必须来自 backtest.py / fast_analysis.json 等落盘文件，禁止自己估算频率；'
      + '④ 你**不推翻**结论，你**标注**结论的脆弱度，并给出"看到什么就撤回看多"的明确触发条件。\n'
      + '【输出格式，≤400 字，四段】①最脆弱的前提（1-2 条，标可观测/不可观测）②历史基础率（含样本数）'
      + '③反事实压力测试（若…则…失效）④撤回条件（看到什么我就改口）+ 一句话结论。'
      + '**禁止**输出"一定/必然/百分百"，也禁止输出与团队一致的"看多/看空"结论。'
      + initTail('证伪'),
  },
]

function clip(text, n) {
  if (typeof text !== 'string') return ''
  const t = text.trim()
  if (t.length <= n) return t
  return t.slice(0, n) + `\n…[truncated ${t.length - n} chars — 完整内容在落盘文件里]`
}

// 带超时的 Promise 包裹（CLI 交叉验证用：本地 CLI 可能挂死，不能拖垮流水线）。
function withTimeout(promise, ms, label) {
  return new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error(`${label || '操作'}超时（${Math.round(ms / 1000)}s）`)), ms)
    Promise.resolve(promise).then(
      (v) => { clearTimeout(t); res(v) },
      (e) => { clearTimeout(t); rej(e) }
    )
  })
}

// 从 stdout 尾部尝试解析最后一个 JSON 行（脚本约定：成功输出单个紧凑 JSON）。
function lastJsonLine(text) {
  if (!text) return null
  const lines = text.split(/\r?\n/).reverse()
  for (const line of lines) {
    const s = line.trim()
    if (s.startsWith('{') && s.endsWith('}')) {
      try { return JSON.parse(s) } catch { /* keep scanning */ }
    }
  }
  return null
}

// argv 约定：调用方传 [scriptName, ...args]（脚本名在 whitelist 校验后传入）。
// 逐候选解释器 spawn：argv = [python, scriptName, ...args]。
//
// v1.5.2 修复（P0，取证 2026-09-29 23:3x "挑选20个近期看涨的虚拟"会话）：
//   旧实现 4 处调用全部传 `cwd = scriptsDir` 且 argv[0] 是**裸脚本名**——
//   Python 因此在**插件安装目录的 scripts/ 里**执行，而不是会话工作区。后果：
//     · 数脉 `screener.py --out kexi_out/scan_result.json` → FileNotFoundError
//       （相对路径解析到 scripts/kexi_out/，该目录不存在）；
//     · `news_watch.py --once` 内部 makedirs 后**静默把产物写进插件包**并回报 OK；
//     · 成员被迫自行诊断"工作目录落在脚本目录"并改用绝对路径重跑，白费 2 轮。
//   新实现：argv[0] 传**绝对脚本路径**，cwd 传**会话工作区根**。
//   Python 启动时会把脚本所在目录自动加入 sys.path[0]，所以
//   `import datasources / kline_utils` 等兄弟模块导入不受 cwd 影响。
async function runPython(ctx, exec, argv, cwd) {
  let lastErr = null
  for (const py of pythonCandidates()) {
    try {
      const handle = ctx.subprocess.spawn({
        argv: [py, ...argv],
        cwd,
        stdio: { stdin: 'ignore', stdout: { maxBytes: MAX_OUT_BYTES }, stderr: { maxBytes: MAX_OUT_BYTES } },
        graceMs: 5000,
        ...(exec && exec.signal ? { signal: exec.signal } : {}),
      })
      const done = await handle.done
      const stdout = handle.collected.stdout.readFrom(0).text || ''
      const stderr = handle.collected.stderr.readFrom(0).text || ''
      if (done.exitCode !== 0) {
        return {
          ok: false,
          exitCode: done.exitCode,
          python: py,
          error: clip(stderr || stdout || `exit ${done.exitCode}`, 900),
          digest: clip(stdout, 400),
        }
      }
      return { ok: true, python: py, stdout, stderr, parsed: lastJsonLine(stdout) }
    } catch (e) {
      lastErr = e
      // ENOENT 之类：换下一个候选；其他错误直接返回。
      const msg = String((e && e.message) || e)
      if (!/ENOENT|not found|spawn/i.test(msg)) break
    }
  }
  return { ok: false, error: `无法启动 Python（试过 ${pythonCandidates().join(', ')}）：${String((lastErr && lastErr.message) || lastErr)}。可设环境变量 KEXI_PYTHON 指定解释器。` }
}

// v1.5.2：把脚本参数里的**相对路径**改写成相对工作区根的绝对路径。
// 派活指令里普遍写 `--out kexi_out/xxx.json`（人读起来最自然），有了这层兜底，
// 即使 cwd 解析出意外也不会把产物丢进插件安装目录。
//
// 路径参数集合**从脚本自身推导**（扫 argparse 里默认值含路径的选项），
// 而不是手写白名单——手写清单必然漏项（实测漏了 --out-dir 就又落进插件目录）。
const PATHY_EXT = /\.(json|jsonl|md|html?|csv|txt|log)\b/i
function derivePathFlags(dir) {
  const flags = new Set(['--out', '--in', '--input'])
  let files = []
  try { files = readdirSync(dir).filter((f) => f.endsWith('.py')) } catch { return flags }
  for (const f of files) {
    let src = ''
    try { src = readFileSync(join(dir, f), 'utf8') } catch { continue }
    // add_argument("--xxx", …, default="kexi_out/y.json") 形态
    const re = /add_argument\(\s*['"](--[a-z0-9-]+)['"][^)]{0,400}?\)/gi
    let m
    while ((m = re.exec(src))) {
      const flag = m[1]
      const body = m[0]
      if (/kexi_out/i.test(body) || PATHY_EXT.test(body)) flags.add(flag)
    }
  }
  return flags
}
let PATH_FLAGS = null

// ── v1.5.2：批量执行（kexi_run mode=script + batch）────────────────────────
// 动机（2026-09-29 实测）：25 币 × 3 脚本若逐个调 kexi_run 需要 75 次工具调用，
// 成员于是绕开工具层手写 Python —— 本轮出现 4 个自写脚本，其中 1 个直接
// `SyntaxError: no binding for nonlocal 'opt'` 崩掉，整轮返工。
// 批量模式把"跑 N 次"收进一次工具调用：{item} 模板占位 + 有限并发，
// 逐项 ok/失败都回报，产物照常落盘并登记进本轮标记。
const BATCH_MAX_ITEMS = 60
const BATCH_DEFAULT_CONCURRENCY = 4
const BATCH_MAX_CONCURRENCY = 8

async function runBatch(ctx, exec, root, scriptsDir, script, batch) {
  // ① 取 items
  let items = []
  if (Array.isArray(batch.items)) {
    items = batch.items.map((x) => String(x).trim()).filter(Boolean)
  } else if (batch.itemsFromFile) {
    try {
      const p = isAbsolute(String(batch.itemsFromFile)) ? String(batch.itemsFromFile) : join(root, String(batch.itemsFromFile))
      items = readFileSync(p, 'utf8').split(/\r?\n/).map((s) => s.trim()).filter((s) => s && !s.startsWith('#'))
    } catch (e) {
      return { ok: false, error: `读不到 itemsFromFile：${batch.itemsFromFile}（${String((e && e.message) || e)}）` }
    }
  }
  if (!items.length) return { ok: false, error: 'batch 需要 items（数组）或 itemsFromFile（每行一个）' }
  if (items.length > BATCH_MAX_ITEMS) {
    return { ok: false, error: `批量上限 ${BATCH_MAX_ITEMS} 项，本次 ${items.length} 项——请分批或先用 screener.py 收窄候选` }
  }

  // ② 模板：args 里 {item} 占位；没有占位则用 itemArg 追加
  const tpl = Array.isArray(batch.args) ? batch.args.map(String) : []
  const hasTpl = tpl.some((a) => a.includes('{item}'))
  if (!hasTpl && !batch.itemArg) {
    return { ok: false, error: 'batch.args 里需含 {item} 占位（如 ["--input","kexi_out/{item}_1d_klines.json"]），或提供 itemArg（如 "--symbol"）' }
  }

  const conc = Math.max(1, Math.min(BATCH_MAX_CONCURRENCY,
    Number(batch.concurrency) || BATCH_DEFAULT_CONCURRENCY))
  const scriptPath = join(scriptsDir, script)

  // ③ 逐项构造 argv（相对路径同样绝对化到工作区）
  const buildArgv = (item) => {
    const subst = (a) => a.split('{item}').join(item)
    let rest
    if (hasTpl) rest = tpl.map(subst)
    else rest = [...tpl, String(batch.itemArg), item]
    return [scriptPath, ...absolutizeArgs(rest, root)]
  }

  // ④ 有限并发执行
  const rows = new Array(items.length)
  let cursor = 0
  // 数据质量闸门：脚本本身**不会**因为数据停更/样本不足而失败
  // （实测 indicators.py 对 400 天前的 K 线照跑不误，产出"看起来正常"的指标，
  //  而且它的 stdout 末行是人话、JSON 里也没有 last_bar_age_days 字段）。
  // 批量是候选池入口，必须在这里把脏数据挑出来，否则停更币会混进最终名单。
  // 做法：直接读**产出文件**取 bars / as_of（as_of 用来算停更天数）。
  const STALE_DAYS = 3
  const MIN_BARS = 120
  function qualityOf(item, outPath) {
    let d = null
    try { d = JSON.parse(readFileSync(outPath, 'utf8')) } catch (e) { d = null }
    if (!d || typeof d !== 'object') return null
    const bars = typeof d.bars === 'number' ? d.bars
      : (d.data_quality && typeof d.data_quality.bars === 'number' ? d.data_quality.bars : undefined)
    const asOf = typeof d.as_of === 'string' ? d.as_of : (d.data_quality && d.data_quality.as_of)
    const flags = []
    let age = null
    if (asOf) {
      const m = String(asOf).match(/(\d{4})-(\d{2})-(\d{2})/)
      if (m) {
        const t = Date.parse(`${m[1]}-${m[2]}-${m[3]}T00:00:00+08:00`)
        if (!Number.isNaN(t)) {
          age = Math.floor((Date.now() - t) / 86400000)
          if (age > STALE_DAYS) flags.push(`停更约 ${age} 天`)
        }
      }
    }
    if (typeof bars === 'number' && bars < MIN_BARS) flags.push(`样本仅 ${bars} 根(<${MIN_BARS})`)
    return { bars, age, flags }
  }
  // 从 argv 里取 --out 的值（脚本常常只写文件、不回显 out 字段）
  function outFromArgv(argv) {
    const i = argv.indexOf('--out')
    return i >= 0 && argv[i + 1] ? argv[i + 1] : null
  }
  async function worker() {
    for (;;) {
      const i = cursor++
      if (i >= items.length) return
      const item = items[i]
      const t0 = Date.now()
      const argv = buildArgv(item)
      try {
        const r = await runPython(ctx, exec, argv, root)
        const parsed = r.parsed || {}
        const outPath = (parsed && parsed.out) ? String(parsed.out) : outFromArgv(argv)
        const q = outPath ? qualityOf(item, outPath) : null
        rows[i] = {
          item,
          ok: !!r.ok,
          ms: Date.now() - t0,
          out: outPath,
          flags: (q && q.flags.length) ? q.flags : null,
          bars: q && q.bars != null ? q.bars : null,
          age: q && q.age != null ? q.age : null,
          error: r.ok ? null : clip(String(r.error || ''), 160),
        }
      } catch (e) {
        rows[i] = { item, ok: false, ms: Date.now() - t0, out: null, error: clip(String((e && e.message) || e), 160) }
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(conc, items.length) }, () => worker()))

  const okRows = rows.filter((r) => r.ok)
  const badRows = rows.filter((r) => !r.ok)
  const flagged = okRows.filter((r) => r.flags && r.flags.length)
  const cleanRows = okRows.filter((r) => !r.flags || !r.flags.length)
  const outFiles = okRows.map((r) => r.out).filter(Boolean)

  // ⑤ 落盘明细 + 登记本轮产物
  let detailFile = null
  try {
    const p = join(root, 'kexi_out', `_batch_${script.replace(/\.py$/, '')}_${Date.now().toString(36)}.json`)
    mkdirSync(join(root, 'kexi_out'), { recursive: true })
    writeFileSync(p, JSON.stringify({ script, at: new Date().toISOString(), total: items.length, rows }, null, 2), 'utf8')
    detailFile = p
  } catch (e) { /* ignore */ }

  const digest = [
    `[批量] ${script} × ${items.length} 项（并发 ${conc}）`,
    `成功 ${okRows.length} / 失败 ${badRows.length}｜其中数据可用 ${cleanRows.length}、脏数据 ${flagged.length}`,
    ...okRows.slice(0, 30).map((r) => `  ${r.flags ? '⚠' : '✓'} ${r.item}  ${r.ms}ms${r.flags ? '  [' + r.flags.join('；') + ']' : ''}${r.out ? '  → ' + r.out.split(/[\\/]/).pop() : ''}`),
    ...badRows.slice(0, 10).map((r) => `  ✗ ${r.item}  ${r.error}`),
    badRows.length > 10 ? `  …另有 ${badRows.length - 10} 项失败，见明细文件` : '',
  ].filter(Boolean).join('\n')

  // 注意：工具返回值里**不能出现 undefined**。
  // DSH 对 tool 输出做 lossless JSON 校验，含 undefined 的对象会被整体拒绝，
  // 现场表现是 `tool "kexi_run" returned invalid output: value is not lossless JSON`
  // ——批量明明跑完了（明细文件已落盘），主理人却只看到一个报错。
  // 这里一律用 null；keys 带 undefined 的行在落盘时也会被 JSON.stringify 丢弃，无害。
  return {
    ok: badRows.length < items.length,     // 部分失败也算可用，全失败才 false
    digest: clip(digest, 1800),
    out_files: outFiles.length ? outFiles : (detailFile ? [detailFile] : []),
    error: badRows.length === items.length ? clip(badRows[0].error, 200) : null,
    next: [
      flagged.length ? `⚠ ${flagged.length} 项数据不可用（停更/样本不足）——按铁律直接剔除，不要写脚本重试掩盖` : '',
      badRows.length ? `失败 ${badRows.length} 项：多为停更/样本不足/流动性不足——同样剔除` : '',
      '下一步用 kexi_run(mode=files) 核对本轮产物后再汇总',
    ].filter(Boolean).join('；'),
  }
}

function absolutizeArgs(argv, root) {
  if (!root || !Array.isArray(argv)) return argv
  if (!PATH_FLAGS) {
    // 用模块级 SKILL_SCRIPTS（apply() 内的 scriptsDir 是闭包变量，模块级取不到）
    try { PATH_FLAGS = derivePathFlags(SKILL_SCRIPTS) } catch { PATH_FLAGS = new Set(['--out', '--in', '--input']) }
  }
  return argv.map((a, i) => {
    const s = String(a)
    const prev = i > 0 ? String(argv[i - 1]) : ''
    if (!PATH_FLAGS.has(prev)) return a
    if (!s || /^[A-Za-z]:[\\/]/.test(s) || s.startsWith('\\\\') || s.startsWith('/')) return a
    return isAbsolute(s) ? a : join(root, s)
  })
}

// 解析工作区根（v1.4.7 修复：这是本插件最严重的历史 bug）。
//
// 旧实现读的是 ctx.get('sandboxPolicy').workspaceRoot。该 Service 确实存在，
// 但它的 workspaceRoot 文档原文是 **"The absolute workspace-write fallback root
// for calls without a session cwd"** —— 即"**没有会话 cwd 时**的兜底根"，
// 不是"当前会话的工作区"。本机该兜底根是 DSH 后端安装目录
// `...\backend\dsh\node_modules\@deepseek-ai\dsh\lib`，于是每次调用都把用户
// 工作区解析成了后端 lib 目录。
//
// 正确 API 是 resolve({ session })：返回 SandboxExecutionPolicy
// { mode, workspaceRoot, sessionId }，其中 workspaceRoot 已按
// "会话 cwd = workspace-write 边界，无 cwd 才回退部署根" 解析好。
//
// 实战后果（2026-09-29 20 币选币会话全程取证）：
//   · kexi_run 的相对路径参数在错误目录下找不到文件（FileNotFoundError）；
//   · kexi_validate 把 'kexi_out/top20_report.json' 解析到后端 lib 目录；
//   · kexi_dashboard 默认 out 与 latest.json 全部落到后端 lib\kexi_out\，
//     导致 host 侧 readLatestArtifact 在真实 cwd 里读不到权威标记 → 回退 mtime
//     扫描 → 把**上一个任务**的看板错挂到本次会话。
//   两处症状同一根因。
function workspaceRoot(ctx, exec) {
  const session = (() => { try { return exec && exec.agent && exec.agent.session } catch { return undefined } })()
  // ① 官方契约：按会话解析出的 workspace-write 边界
  try {
    const sp = ctx.get && ctx.get('sandboxPolicy')
    if (sp && typeof sp.resolve === 'function') {
      const pol = sp.resolve(session ? { session } : {})
      if (pol && typeof pol.workspaceRoot === 'string' && pol.workspaceRoot) return pol.workspaceRoot
    }
  } catch { /* 继续降级 */ }
  // ② 会话 header.cwd（Session.header.cwd；host 插件 sessionCwd() 同款写法）
  try {
    const cwd = session && session.header && session.header.cwd
    if (typeof cwd === 'string' && cwd) return cwd
  } catch { /* 继续降级 */ }
  // ③ 部署兜底根（仅当会话无 cwd；语义上不是会话工作区）
  try {
    const sp = ctx.get && ctx.get('sandboxPolicy')
    if (sp && typeof sp.workspaceRoot === 'string' && sp.workspaceRoot) return sp.workspaceRoot
  } catch { /* 继续降级 */ }
  // ④ 最后兜底：process.cwd()。注意它在后端进程里指向安装目录，产物会写错地方。
  return (typeof process !== 'undefined' && process.cwd()) || HERE
}

// ── 权威产物标记 latest.json（v1.4.6）──────────────────────────────────────
// 背景：目录里会同时存在 algo_dashboard.html / algo_dashboard_v2.html、
// final_picks / final_picks2 等同族产物，按 mtime 猜"哪份是本次权威"会猜错
// （补渲染、手工重命名、并行成员写盘都会让 mtime 失真）。
// 约定：每次成功的渲染都把本次权威产物写进 <cwd>/kexi_out/latest.json，
// 懒发现（host discoverArtifact）**优先读它**，读不到才回退 mtime 扫描。
const LATEST_SCHEMA = 'kexi.latest/1'

/**
 * 合并写入 kexi_out/latest.json（保留本次运行不涉及的旧字段）。
 * @returns {string|null} 落盘的绝对路径；失败返回 null（绝不阻断研判）。
 */
function writeLatest(root, patch) {
  try {
    const dir = join(root, 'kexi_out')
    mkdirSync(dir, { recursive: true })
    const file = join(dir, 'latest.json')
    let prev = {}
    try { if (existsSync(file)) prev = JSON.parse(readFileSync(file, 'utf8')) || {} } catch (e) { prev = {} }
    const payload = {
      ...prev,
      ...patch,
      schema: LATEST_SCHEMA,
      at: new Date().toISOString(),
      latest_path: file,
    }
    // 去掉值为 undefined/null 的键，避免把上一轮的 symbol 覆盖成 null 时留下噪声
    for (const k of Object.keys(payload)) {
      if (payload[k] === undefined) delete payload[k]
    }
    payload.note = '本次运行的权威产物。懒发现优先读本文件；不要按 mtime 猜哪份看板最新。'
    writeFileSync(file, JSON.stringify(payload, null, 2), 'utf8')
    return file
  } catch (e) { return null }
}

function renderText(label, value) {
  const v = value && typeof value === 'object' ? value : { text: String(value) }
  const parts = []
  parts.push(`kexi ${label}: ${v.ok ? 'OK' : 'FAILED'}`)
  if (v.error) parts.push(`error: ${v.error}`)
  if (v.digest) parts.push(`digest:\n${v.digest}`)
  if (v.out_files && v.out_files.length) parts.push(`files: ${v.out_files.join(' | ')}`)
  if (v.next) parts.push(`next: ${v.next}`)
  return [{ type: 'text', text: parts.join('\n') }]
}

const TEXT_SCHEMA = { type: 'object', additionalProperties: false, required: ['ok'], properties: {
  ok: { type: 'boolean' }, error: { type: 'string' }, digest: { type: 'string' },
  out_files: { type: 'array', items: { type: 'string' } }, next: { type: 'string' },
  // v1.4.7 修复：kexi_news 在返回值上带 news_available；harness 会按 output.schema
  // 严格校验（additionalProperties:false），漏声明会让工具直接报
  // "value.news_available is not a declared property" —— 实战（20 币选币会话）抓到。
  news_available: { type: 'boolean' },
  // v1.5.2：批量模式返回聚合结果（kexi_run batch）。同样必须声明，
  // 否则 harness 按 output.schema 严格校验会直接拒收。
  batch: { type: 'object', additionalProperties: true },
} }

function mkTool(name_, description, parameters, label, execute) {
  return {
    name: name_,
    description,
    parameters: { type: 'object', additionalProperties: false, required: parameters.required || [], properties: parameters.properties },
    output: { schema: TEXT_SCHEMA, render: (args, value) => renderText(label, value) },
    execute,
  }
}

export function apply(ctx) {
  const scriptsDir = SKILL_SCRIPTS

  // ── 状态灯桥接：向家级 kexi-dashboard collector 推送研判活动快照 ──────────
  // 事件是 app 级广播，不受 isolate realm 影响（对齐 agy-first-bridge）。
  // kexi/mode：每 30s 心跳，家级灯据此决定本会话「K析就绪」常驻。
  // kexi/status：携带当前会话工作目录的一条活动记录（phase/symbol/step），
  //   研判流水线推进时更新，家级灯据此显示 ⟳/✓/✗。
  let workspaceCwd = ''
  const state = { phase: 'idle', symbol: null, step: null, stepIndex: 0, stepTotal: 0, lastAt: 0, lastStatus: null }
  function tryEmit(evt, payload) { try { ctx.emit(evt, payload) } catch (e) { /* 宿主无 emit 时静默 */ } }
  function statusSnapshot() {
    return { cwd: workspaceCwd, phase: state.phase, symbol: state.symbol, step: state.step,
      stepIndex: state.stepIndex, stepTotal: state.stepTotal, lastStatus: state.lastStatus, updatedAt: state.lastAt }
  }
  function publish() { state.lastAt = Date.now(); tryEmit('kexi/status', { snapshot: statusSnapshot() }) }
  function announceMode() { syncCwd(); tryEmit('kexi/mode', { active: true }) }
  announceMode()
  try {
    // 优先用 cordis timer 作用域（inject 'timer' → ctx.interval），setInterval 兜底。
    const t = ctx.interval ? ctx.interval(announceMode, 30000)
      : (ctx.setInterval ? ctx.setInterval(announceMode, 30000) : null)
    if (t && ctx.effect) ctx.effect(() => () => { try { t() } catch (e) {} })
  } catch (e) { /* timer 不可用：仅一次宣告 */ }

  // 流水线推进钩子：kexi_run 每步调用 setPhase 更新灯。
  function setPhase(root, phase, symbol, stepIndex, stepTotal) {
    if (root && !workspaceCwd) workspaceCwd = root
    state.phase = phase; if (symbol !== undefined) state.symbol = symbol
    state.step = stepIndex ? `步骤 ${stepIndex}/${stepTotal}` : null
    state.stepIndex = stepIndex || 0; state.stepTotal = stepTotal || 0
    publish()
  }

  // 工作区解析 + 每轮心跳同步 cwd（workspaceRoot 首次调用可能未就绪）。
  // v1.4.7：与 workspaceRoot() 同源 —— 走 sandboxPolicy.resolve({session})，
  // 旧实现直接读 .workspaceRoot（无会话语义的部署兜底根）会显示错目录。
  function syncCwd(exec) {
    try { workspaceCwd = workspaceRoot(ctx, exec) } catch (e) { /* ignore */ }
  }
  syncCwd()

  // ── v1.5.2：本轮产物标记（解决"工作区跨运行混用导致来源混淆"）────────────
  // 取证（2026-09-29 23:5x）：kexi_out/ 里上一轮 17 个文件与本轮 17 个同目录，
  // 主理人把上一轮的 wangchao_scenarios.json / zhibei_final20.json 误当本轮产物，
  // 连续用 pwsh 排查文件来源白耗 2 个 step。
  // 这里维护 kexi_out/.kexi_run.json（本轮起点 + 已登记产物），
  // 配合 kexi_run(mode=files) 让成员**问插件**而不是猜。
  const RUN_MARKER = join('kexi_out', '.kexi_run.json')

  function readRunMarker(root) {
    try {
      const p = join(root, RUN_MARKER)
      if (!existsSync(p)) return null
      return JSON.parse(readFileSync(p, 'utf8'))
    } catch (e) { return null }
  }

  function writeRunMarker(root, patch) {
    try {
      const prev = readRunMarker(root) || {}
      const payload = {
        run_id: prev.run_id || ('run-' + Date.now().toString(36)),
        started_at: prev.started_at || new Date().toISOString(),
        updated_at: new Date().toISOString(),
        artifacts: Array.isArray(prev.artifacts) ? prev.artifacts.slice(-40) : [],
        ...patch,
      }
      const p = join(root, RUN_MARKER)
      mkdirSync(join(root, 'kexi_out'), { recursive: true })
      writeFileSync(p, JSON.stringify(payload, null, 2), 'utf8')
      return payload
    } catch (e) { return null }
  }

  // 登记本轮产物（文件名 + mtime），供 mode=files 区分本轮/历史
  function markArtifacts(root, files) {
    const list = (Array.isArray(files) ? files : [files]).filter(Boolean).map((f) => {
      const abs = isAbsolute(f) ? f : join(root, String(f))
      let mt = 0
      try { mt = statSafeMtime(abs) } catch (e) { /* ignore */ }
      return { path: abs, name: abs.split(/[\\/]/).pop(), mtime: mt }
    })
    if (!list.length) return null
    const prev = readRunMarker(root) || { artifacts: [] }
    const merged = prev.artifacts.concat(list).slice(-60)
    return writeRunMarker(root, { artifacts: merged })
  }

  function statSafeMtime(p) {
    try { return statSync(p).mtimeMs } catch (e) { return 0 }
  }

  // v1.5.2：确保本轮标记存在，且**早于任何脚本产物**。
  // 在 kexi_run 入口就建（而不是等 mode=files 查询时才建）——成员先跑脚本、
  // 之后才可能查清单；等查询时再建，起点会落在产物之后，把本轮文件误判成历史。
  function ensureRunMarker(root) {
    const prev = readRunMarker(root)
    if (prev && prev.run_id) return prev
    return writeRunMarker(root, { started_at: new Date().toISOString() })
  }

  // ── kexi_run: 单脚本 / 一键流水线 ───────────────────────────────────────────
  const runTool = mkTool('kexi_run',
    'K析研判团分析流水线（Python 脚本链，产物自动落盘 kexi_out/，对话只回紧凑摘要）。mode=pipeline：一条命令完成 取K线→指标→组装report.json→契约校验归一→HTML看板，返回摘要+文件路径（研判字段为规则草稿，成员可覆写）。mode=script：运行白名单内单个脚本（fetch_klines/indicators/assemble_report/screener/portfolio/position/timeframe/backtest/datasources/cex_adapter/cex_risk/cex_keystore/kexi_notify/keepalive/token_stats/track_report/dashboard等25个脚本）。大 JSON 不回传，用返回的路径继续。',
    {
      required: ['mode'],
      properties: {
        mode: { type: 'string', enum: ['pipeline', 'script', 'files', 'fast'], description: 'pipeline=一键全链; script=单脚本（可带 batch 批量）; fast=秒级确定性快研（先快答再深研）; files=本轮/历史产物清单（解决跨运行来源混淆）' },
        symbol: { type: 'string', description: '交易对，如 BTCUSDT（pipeline 必填；mode=fast 单币也用它）' },
        symbols: { type: 'string', description: 'mode=fast 批量：逗号分隔多个交易对（秒级出多币结论）' },
        source: { type: 'string', enum: ['auto', 'binance', 'okx', 'coingecko'], description: 'mode=fast 数据源（默认 auto=自动降级链；指定 binance 则只取币安，取不到即失败而不静默降级）' },
        interval: { type: 'string', enum: ['1h', '4h', '1d', '1w'], description: 'K线周期，默认 1d' },
        limit: { type: 'integer', description: 'K线根数，默认 220（够算 MA60/波动率）' },
        source: { type: 'string', enum: ['auto', 'binance', 'okx', 'coingecko'], description: '数据源，默认 auto（binance 主源，失败降级）' },
        script: { type: 'string', description: 'script 模式的脚本名（白名单内，如 screener.py）' },
        args: { type: 'array', items: { type: 'string' }, description: '传给该脚本的额外命令行参数（原样 argv）' },
        batch: {
          type: 'object',
          description: 'v1.5.2 批量模式：一次跑多个标的，省掉 N 次工具调用（成员不再需要手写 Python）',
          properties: {
            items: { type: 'array', items: { type: 'string' }, description: '要跑的项目列表（代码或文件名片段）' },
            itemsFromFile: { type: 'string', description: '或：从文本文件读列表（每行一个，# 开头为注释）' },
            args: { type: 'array', items: { type: 'string' }, description: '参数模板，支持 {item} 占位，如 ["--input","kexi_out/{item}_1d_klines.json","--out","kexi_out/{item}_1d_indicators.json"]' },
            itemArg: { type: 'string', description: 'args 里没有 {item} 时，把 item 接在该参数后（如 "--symbol"）' },
            concurrency: { type: 'integer', description: '并发数，默认 4，上限 8（避免交易所限流）' },
          },
        },
        since: { type: 'string', description: "mode=files 时：'run' 只看本轮 / 'all' 全列（默认本轮优先分组）" },
      },
    },
    'kexi_run',
    async (args, exec) => {
      const root0 = workspaceRoot(ctx, exec)
      // v1.5.2：入口即建本轮标记（早于任何脚本产物），mode=files 才能正确分组
      try { ensureRunMarker(root0) } catch (e) { /* ignore */ }

      // v1.5.4：mode 缺省时按参数推断，避免"带了 batch 却落进 pipeline 分支"
      // → 报 'pipeline 模式需要 symbol'，批量能力被静默忽略。
      // 注意：**不能**仅凭存在 symbol 就推断成 fast——旧式
      // kexi_run({pipeline:true, symbol:'X'}) 是文档化的调用形式，推断成 fast
      // 会把整条流水线劫持走（回归：test-runtime 读不到 report.json）。
      // 只有 symbols（pipeline 从不接受该参数）才是无歧义的快答信号。
      if (!args.mode) {
        if (args.batch) args.mode = 'script'
        else if (args.symbols) args.mode = 'fast'
      }

      // ── v1.5.2 mode=files：本轮产物清单（解决跨运行来源混淆）──────────────
      // 成员此前靠 pwsh 猜"哪些文件是本轮的"，反复排查白耗步数；
      // 这里直接给权威答案：本轮 run 起点之后写的 = 本轮，更早的 = 历史。
      if (args.mode === 'files') {
        const outDir = join(root0, 'kexi_out')
        let entries = []
        try { entries = readdirSync(outDir) } catch { /* 目录不存在 */ }
        let marker = readRunMarker(root0)
        // 兜底补建：取**现在**作为起点。
        // 反证：早先版本用"目录里最新文件的 mtime"当起点，实测把本轮最早产出的
        // 文件（数脉 00:32:03 拉的 K 线）判成【历史】——因为 news_watch 00:32:37
        // 更晚，起点反而落在它之后。正常路径由 ensureRunMarker 在每次 kexi_run
        // 入口就建好标记（早于任何脚本产物），这里只是没走过 kexi_run 时的兜底。
        if (!marker) {
          marker = writeRunMarker(root0, { started_at: new Date().toISOString() })
        }
        const since = marker ? (Date.parse(marker.started_at) || 0) : 0
        // 60s 容差：吸收"标记写入瞬间"与"文件系统时间戳"之间的先后抖动
        const GRACE_MS = 60 * 1000
        const rows = []
        for (const name of entries) {
          if (name.startsWith('.')) continue
          const full = join(outDir, name)
          let st = null
          try { st = statSync(full) } catch { continue }
          if (!st.isFile()) continue
          const isRun = since > 0 && st.mtimeMs >= since - GRACE_MS
          rows.push({
            name, kb: Math.round((st.size / 1024) * 10) / 10,
            mtime: new Date(st.mtimeMs).toISOString().slice(0, 19).replace('T', ' '),
            scope: isRun ? '本轮' : '历史',
          })
        }
        rows.sort((a, b) => (a.scope === b.scope ? b.mtime.localeCompare(a.mtime) : (a.scope === '本轮' ? -1 : 1)))
        const runRows = rows.filter((r) => r.scope === '本轮')
        const digest = (rows.length
          ? [`本轮 run 起点：${marker ? marker.started_at : '(无标记)'}`,
            `本轮产物 ${runRows.length} 个 / 历史遗留 ${rows.length - runRows.length} 个`,
            '--- 本轮 ---',
            ...runRows.map((r) => `  [本轮] ${r.name}  ${r.kb}KB  ${r.mtime}`),
            '--- 历史（上一轮及更早，勿当本轮结论引用）---',
            ...rows.filter((r) => r.scope !== '本轮').slice(0, 25).map((r) => `  [历史] ${r.name}  ${r.mtime}`)]
          : ['kexi_out 为空或不存在']).join('\n')
        return {
          ok: true, digest: clip(digest, 1800),
          out_files: runRows.map((r) => join(outDir, r.name)),
          next: '只引用标记【本轮】的文件；【历史】是上一轮及更早的产物。'
            + '引用前先核对该文件 mtime 是否晚于本轮 run 起点。',
        }
      }

      // ── v1.5.3 mode=fast：秒级确定性快研（先快答，再按需升级深研）──────
      // 性能实测（2026-09-30）：单币团队协作全流程 17 分钟，而同一套分析脚本
      //   fetch 505ms + indicators 89 + position 65 + timeframe 60
      //   + entry_plan 92 + coin_profile 90 ≈ 0.9 秒。
      // 99.9% 的时间花在 LLM 编排往返（几十次顺序模型调用 + 37% provider 重试），
      // 用户反馈"结果出来币价都涨了几个点"就是这个代价。
      // 本模式把整条链收进一次工具调用，口径与团队流程**完全一致**（同一批脚本）。
      if (args.mode === 'fast') {
        const syms = []
        if (args.symbol) syms.push(String(args.symbol).toUpperCase().trim())
        if (args.symbols) {
          String(args.symbols).split(',').forEach((s) => {
            const t = s.trim().toUpperCase()
            if (t) syms.push(t)
          })
        }
        if (!syms.length) return { ok: false, error: 'mode=fast 需要 symbol 或 symbols（逗号分隔）' }
        const outDir = join(root0, 'kexi_out')
        const argv = [join(scriptsDir, 'fast_analysis.py'), '--out-dir', outDir]
        // 多币必须用逗号分隔的 --symbols：argparse 的 --symbol 重复出现时
        // **后者覆盖前者，只会留下最后一个**。现场取证：symbols="A,B,C,D,E,F"
        // 六个币只分析了 1 个、digest 显示"1 币"，主理人以为批量坏了改逐个调用，
        // 批量能力形同虚设。
        if (syms.length === 1) argv.push('--symbol', syms[0])
        else argv.push('--symbols', syms.join(','))
        if (args.interval) argv.push('--interval', String(args.interval))
        if (args.limit) argv.push('--limit', String(args.limit))
        // v1.5.7：显式指定数据源。现场取证——模型会传 source:"binance" 以求
        // 拿到正规口径，但本分支原先**静默丢弃**该参数：模型以为强制了币安，
        // 实际仍在自动降级链里跑（可能拿到 CoinGecko 的 4 天粒度），
        // 结论与预期不符却无人察觉。要么真传下去，要么明确报错，不能装没看见。
        if (args.source) argv.push('--source', String(args.source))
        const fr = await runPython(ctx, exec, argv, root0)
        if (!fr.ok) return { ok: false, error: fr.error, digest: fr.digest || '' }
        const parsed = fr.parsed || {}
        const files = [join(outDir, 'fast_analysis.json'), join(outDir, 'fast_analysis.md')]
        try { markArtifacts(root0, files) } catch (e) { /* ignore */ }
        return {
          ok: true,
          digest: clip(String(fr.stdout || ''), 1800),
          out_files: files.filter((f) => existsSync(f)),
          next: `秒级快答（${parsed.elapsed_ms != null ? parsed.elapsed_ms + 'ms' : '~1s'}）。`
            + '若用户要成员交叉验证/深度论证再升级 kexi_run(mode=pipeline) 或派活团队——'
            + '本结果的 fast_analysis.json 可直接喂给团队，无需重算。',
        }
      }

      if (args.mode === 'script') {
        const script = String(args.script || '').replace(/^.*[\\/]/, '')
        if (!SCRIPT_WHITELIST.has(script)) return { ok: false, error: `脚本不在白名单：${script || '(空)'}。可用：${[...SCRIPT_WHITELIST].join(', ')}` }

        // ── v1.5.2 批量模式 ────────────────────────────────────────────────
        // 取证：25 币复核若逐个调 kexi_run 需要 75 次调用，成员因此手写 Python
        // （本轮实测 4 个自写脚本，其中 1 个 SyntaxError 崩掉）。这里提供批量：
        //   batch: { items|itemsFromFile, args（含 {item} 占位）, itemArg, concurrency }
        if (args.batch && typeof args.batch === 'object') {
          const res = await runBatch(ctx, exec, root0, scriptsDir, script, args.batch)
          // 登记本轮产物 → mode=files 才能把它们标成【本轮】
          try {
            const arts = (res.out_files || []).concat(
              (res.batch && res.batch.detail) ? [res.batch.detail] : [])
            if (arts.length) writeRunMarker(root0, { artifacts: arts.map((p) => ({ path: p, name: String(p).split(/[\\/]/).pop(), mtime: statSafeMtime(p) })) })
          } catch (e) { /* ignore */ }
          return res
        }

        const argv = [join(scriptsDir, script), ...absolutizeArgs(Array.isArray(args.args) ? args.args.map(String) : [], root0)]
        const r = await runPython(ctx, exec, argv, root0)
        if (!r.ok) return { ok: false, error: String(r.error || '(未知)'), digest: r.digest || '' }
        const parsed = r.parsed
        const outFiles = parsed && parsed.out ? [String(parsed.out)] : []
        if (outFiles.length) { try { markArtifacts(root0, outFiles) } catch (e) { /* ignore */ } }
        return {
          ok: true,
          digest: clip(r.stdout, 700),
          out_files: outFiles,
          next: parsed && parsed.next ? String(parsed.next) : '结果 JSON 已在 digest；如需全量请读 out_files。',
        }
      }
      // pipeline: fetch_klines → indicators → assemble_report → validate --fix → dashboard
      const root = workspaceRoot(ctx, exec)
      const outDir = join(root, 'kexi_out')
      try { mkdirSync(outDir, { recursive: true }) } catch (e) { /* 已存在或无权限：脚本会各自报错 */ }
      const sym = String(args.symbol || '').toUpperCase().trim()
      if (!sym) return { ok: false, error: 'pipeline 模式需要 symbol（如 BTCUSDT）' }
      // v1.4.6 确定性兜底：真正的研判调用发生时，若模型跳过了提示词第 0 步
      // （kexi_ensure_team），这里幂等补建持久团队，保证原生面板始终 5 人。
      // 只在 lead 会话生效、每会话只试一次、尊重 persistentTeam 开关。
      let teamNote = ''
      try {
        const te = await autoEnsureTeam(exec)
        if (te && te.state === 'created' && te.created && te.created.length) {
          teamNote = `；已自动补建持久团队（${te.created.join('、')}）——下次可省略 kexi_ensure_team`
        } else if (te && te.state === 'error') {
          teamNote = `；自动补建持久团队失败（${clip(String(te.error || ''), 90)}），按需协作继续`
        }
      } catch (e) { /* 兜底失败绝不阻断研判 */ }
      const interval = ['1h', '4h', '1d', '1w'].includes(String(args.interval)) ? String(args.interval) : (String(cfgDefault('defaultInterval') || '1d'))
      const limit = Number.isInteger(args.limit) && args.limit > 0 ? args.limit : (Number(cfgDefault('defaultLimit')) || 220)
      const source = ['auto', 'binance', 'coingecko'].includes(String(args.source)) ? String(args.source) : 'auto'
      const tag = `${sym}_${interval}`
      setPhase(root, 'running', sym, 1, 6)
      const step = async (script, extra) => {
        const argv = [join(scriptsDir, script), ...absolutizeArgs(extra, root)]
        const r = await runPython(ctx, exec, argv, root)
        return { r, parsed: r.parsed || {} }
      }
      // 1) 取数（脚本内置停更拦截 last_bar_age_days、去重、缺口检测、剔除未收盘 bar）
      const kl = join(outDir, `${tag}_klines.json`)
      let s = await step('fetch_klines.py', ['--symbol', sym, '--interval', interval, '--limit', String(limit), '--source', source, '--out', kl])
      if (!s.r.ok) { state.phase = 'failed'; state.lastStatus = 'fetch'; publish(); return { ok: false, error: `fetch_klines: ${s.r.error}`, next: '可改 source=coingecko 重试；或如实告知用户数据源不可用。' } }
      // 2) 指标（MA/EMA/MACD/RSI/背离扫描/量价/支撑阻力/波动率）
      setPhase(root, 'running', sym, 2, 6)
      const ind = join(outDir, `${tag}_indicators.json`)
      s = await step('indicators.py', ['--input', kl, '--out', ind])
      if (!s.r.ok) { state.phase = 'failed'; state.lastStatus = 'indicators'; publish(); return { ok: false, error: `indicators: ${s.r.error}`, out_files: [kl] } }
      // 3) 组装契约报告（量化字段取真实输出；研判字段给规则草稿，成员结论可覆写）
      setPhase(root, 'running', sym, 3, 6)
      const report = join(outDir, `${tag}_report.json`)
      // 3a) 解锁时间表（G7）：查内置 registry；无该代币数据则退出码 2、跳过（不编造日期）
      const unlockFile = join(outDir, `${tag}_unlock.json`)
      const unl = await step('unlock_schedule.py', ['--symbol', sym, '--out', unlockFile])
      const unlockArg = unl.r.ok ? ['--unlock-json', unlockFile] : []
      const av = await step('assemble_report.py', ['--klines', kl, '--indicators', ind, '--out', report, ...unlockArg])
      if (!av.r.ok) { state.phase = 'failed'; state.lastStatus = 'assemble'; publish(); return { ok: false, error: `assemble_report: ${av.r.error}`, out_files: [kl, ind], next: '中间文件已落盘，可修字段后单独调 kexi_validate。' } }
      const aparsed = av.parsed || {}
      // 4) 契约闸门 + 安全归一（就地写回）
      setPhase(root, 'running', sym, 4, 6)
      let vr = await step('validate_report.py', ['--input', report, '--fix', '--out', report, '--quiet'])
      if (!vr.r.ok) {
        state.phase = 'failed'; state.lastStatus = 'validate'; publish()
        return {
          ok: false,
          error: `契约校验未过: ${vr.r.error || vr.r.digest || ''}`,
          digest: clip(JSON.stringify(aparsed), 300),
          out_files: [report],
          next: '按 docs/report-contract.md 修正 verdict/trend/risk 后重跑 kexi_validate --fix，再 kexi_dashboard。',
        }
      }
      // 5) 本地 CLI 自动交叉验证（v1.2.1）：validate 通过后、渲染看板前，
      //    把紧凑摘要派给本地 CLI（agy/codebuddy/mimo）做独立形态复核，
      //    结论写进 report.json 的 crosscheck 键（契约允许额外键）。
      //    设计纪律：
      //    a) 默认开（设置面板 autoCliCrosscheck 可关），CLI 一个都没有就如实
      //       记 skipped，绝不假装有 CLI 参与、也不让 pipeline 失败；
      //    b) CLI 结论是"参考意见"：写 agreement（agree/disagree/unclear）+
      //       note + 来源标注，**不改动** verdict/trend/risk 等权威字段；
      //    c) 失败/超时同 skipped 处理（p5 与看板互不阻塞）。
      const STEP_TOTAL = 7
      let crosscheck = null
      const autoCross = cfgDefault('autoCliCrosscheck') !== false
      setPhase(root, 'running', sym, 5, STEP_TOTAL)
      if (autoCross) {
        const avail = availableClis()
        if (!avail.length) {
          crosscheck = { status: 'skipped', reason: '本机无可用的本地 CLI（agy/codebuddy/mimo 均未探测到）' }
        } else {
          const cr2 = await step('compact_report.py', ['--input', report])
          const summary = cr2.r.ok ? clip(cr2.r.stdout, 900) : clip(JSON.stringify(aparsed), 600)
          const pick = avail[0]
          const spec = CLI_SPEC[pick]
          try {
            const tool = (ctx.tools && typeof ctx.tools.get === 'function') ? ctx.tools.get(spec.run) : null
            const execCli = tool && (tool.execute || tool.handler || tool.run)
            if (typeof execCli !== 'function') throw new Error(`CLI 工具 ${spec.run} 不可调用`)
            const prompt = [
              `你是独立复核员。请对下面这份加密货币研判报告做**独立第二视角**检查：`,
              `1) 技术形态判定（趋势/背离/关键位）是否有明显逻辑错误；`,
              `2) 结论与数据是否自洽；`,
              `3) 有没有夸大或过度自信的表述。`,
              `只输出一段 ≤120 字的复核意见，并以下面三选一开头：同意 / 有异议 / 无法判断。`,
              `不要重新取数，只依据所给摘要判断。`,
            ].join('\n')
            const raw = await withTimeout(
              execCli({ prompt, context: summary, background: false }),
              (Number(cfgDefault('cliCrosscheckTimeoutSec')) || 90) * 1000,
              'CLI 交叉验证超时'
            )
            // 字段名按真实桥工具对齐：agy/codebuddy 成功结果文本在 response，
            // 旧的 digest/text/content 保留兜底（D10：先前只认后者，agy 实机会退成
            // 存整个 JSON、agreement 永远 unclear——假工具返回 digest 才掩盖了它）。
            const txt = typeof raw === 'string' ? raw
              : (raw && (raw.response || raw.digest || raw.text || raw.content)) || JSON.stringify(raw)
            const s2 = String(txt || '')
            // 判定词不一定在最前面（可能有称呼/客套），扫描正文开头一段更稳。
            const probe = s2.slice(0, 40)
            const agreement = probe.includes('有异议') ? 'disagree'
              : (probe.includes('同意') ? 'agree'
                : (probe.includes('无法判断') || probe.includes('无法确定') ? 'unclear' : 'unclear'))
            crosscheck = {
              status: 'ok', cli: pick, cliName: spec.cn, agreement,
              note: clip(s2, 200), at: new Date().toISOString(),
              rule: 'CLI 结论为参考意见，与脚本数据冲突时以脚本数据为准',
            }
          } catch (e) {
            crosscheck = { status: 'skipped', reason: `CLI(${pick}) 交叉验证失败: ${clip(String((e && e.message) || e), 160)}` }
          }
        }
      } else {
        crosscheck = { status: 'disabled', reason: '设置面板已关闭 autoCliCrosscheck' }
      }
      try {
        const rp = JSON.parse(readFileSync(report, 'utf8'))
        rp.crosscheck = crosscheck
        writeFileSync(report, JSON.stringify(rp, null, 2), 'utf8')
      } catch (e) { /* 报告写入 crosscheck 失败不阻塞主流程 */ }

      // 6) 看板（自包含 HTML；--klines 使蜡烛图可离线渲染）
      setPhase(root, 'running', sym, 6, STEP_TOTAL)
      const dash = join(outDir, `${tag}_dashboard.html`)
      let dr = await step('dashboard.py', ['--input', report, '--klines', kl, '--out', dash, '--title', `${sym} ${interval} K析研判看板`])
      // 7) 紧凑摘要（~700 字符，供对话引用，避免整份 JSON 进上下文）
      setPhase(root, 'running', sym, 7, STEP_TOTAL)
      const cr = await step('compact_report.py', ['--input', report])
      const digest = cr.r.ok ? clip(cr.r.stdout, 900) : clip(JSON.stringify(aparsed), 500)
      const xchkLine = crosscheck && crosscheck.status === 'ok'
        ? `；CLI 复核(${crosscheck.cli}): ${crosscheck.agreement === 'agree' ? '同意' : crosscheck.agreement === 'disagree' ? '有异议' : '无法判断'}`
        : ''
      state.phase = dr.r.ok ? 'ok' : 'failed'
      state.lastStatus = dr.r.ok ? 'pipeline' : 'dashboard'
      publish()
      // v1.4.6：成功渲染即登记权威产物，懒发现优先读它而非按 mtime 猜。
      let latestFile = null
      if (dr.r.ok) {
        latestFile = writeLatest(root, {
          kind: 'pipeline', symbol: sym, interval, report, klines: kl, dashboard: dash,
        })
      }
      return {
        ok: dr.r.ok,
        digest: digest + xchkLine + teamNote,
        out_files: dr.r.ok ? [report, dash, ...(latestFile ? [latestFile] : [])] : [report],
        next: dr.r.ok
          ? '研判已落盘。深度研判请委派 trend_forecaster/risk_assessor 覆写 trend/risk 后重跑 kexi_dashboard；用 present 交付看板。'
          : `看板渲染失败（报告本身可用）：${clip(dr.r.error, 200)}；可单独重试 kexi_dashboard。`,
      }
    })

  // ── kexi_validate ──────────────────────────────────────────────────────────
  const validateTool = mkTool('kexi_validate',
    '校验/归一一份 kexi.report/1 契约的 report.json（validate_report.py）。返回 {ok,errors,warnings,changed}。错误为硬失败（缺键/类型错/停更数据）；--fix 安全归一（概率和归一、NaN→null、warnings 截 6 条）。',
    {
      required: ['input'],
      properties: {
        input: { type: 'string', description: 'report.json 路径（相对工作区或绝对）' },
        fix: { type: 'boolean', description: '安全归一并写回（默认 false）' },
      },
    },
    'kexi_validate',
    async (args, exec) => {
      const root = workspaceRoot(ctx, exec)
      const inp = args.input.includes(':') || args.input.startsWith('/') || args.input.startsWith('\\')
        ? args.input : join(root, args.input)
      const argv = [join(scriptsDir, 'validate_report.py'), '--input', inp, '--quiet']
      if (args.fix) argv.push('--fix', '--out', inp)
      const r = await runPython(ctx, exec, argv, workspaceRoot(ctx, exec))
      if (!r.ok) return { ok: false, error: String(r.error || '(未知)'), digest: r.digest || '' }
      return { ok: true, digest: clip(r.stdout, 900), out_files: [inp] }
    })

  // ── kexi_dashboard ─────────────────────────────────────────────────────────
  const dashTool = mkTool('kexi_dashboard',
    '由 report.json 渲染自包含离线 HTML 研判看板（dashboard.py：SVG蜡烛+均线+RSI/MACD+支撑阻力+三情景概率条+风险面板+相关性热力图）。返回看板路径，用 present 交付。',
    {
      required: ['input'],
      properties: {
        input: { type: 'string', description: 'report.json 路径' },
        klines: { type: 'string', description: '可选：*_klines.json 以渲染蜡烛图' },
        portfolio: { type: 'string', description: '可选：portfolio.py 输出以渲染相关性/仓位预算' },
        out: { type: 'string', description: '可选：输出 HTML 路径，默认 kexi_out/<symbol>_dashboard.html' },
        title: { type: 'string', description: '可选：看板标题' },
      },
    },
    'kexi_dashboard',
    async (args, exec) => {
      const root = workspaceRoot(ctx, exec)
      // v1.6.4：参数防御。
      // JSON Schema 只是**声明**，运行时不强制——模型确实会传错类型。
      // 旧写法 `p.includes(':')` 在收到非字符串时抛原始 TypeError，
      // 模型拿到 "p.includes is not a function" 完全无从纠正
      // （2026-09-30 实测：kexi_dashboard 崩了，主理人只好改用 kexi_run 跑
      //   dashboard.py 绕路，而那条路返回的键是 `out` 不是 `out_files`，
      //   于是成果也没能登记——一次类型错误连锁出两个故障）。
      // 这里：非字符串一律当缺省处理（可选参数直接跳过），
      // 必填的 input 若是错类型则**明确回报实际收到的类型**，
      // 让模型能自我纠正，而不是对着一个 TypeError 猜。
      const strOr = (v) => (typeof v === 'string' ? v : '')
      const abs = (p0) => {
        const q = strOr(p0)
        return q && (q.includes(':') || q.startsWith('/') || q.startsWith('\\')) ? q : (q ? join(root, q) : q)
      }
      if (args.input !== undefined && typeof args.input !== 'string') {
        return { ok: false, digest: '', error: `input 必须是字符串路径，收到 ${Array.isArray(args.input) ? '数组' : typeof args.input}。请只传一个 report.json 路径。` }
      }
      const inpRaw = strOr(args.input).trim()
      if (!inpRaw) return { ok: false, digest: '', error: 'kexi_dashboard 缺 input（report.json 路径），例如 kexi_out/market_report.json' }
      const inp = abs(inpRaw)
      const out = strOr(args.out) ? abs(strOr(args.out))
        : join(root, 'kexi_out', (inpRaw.split(/[\\/]/).pop() || 'report').replace(/\.json$/, '') + '_dashboard.html')
      const argv = [join(scriptsDir, 'dashboard.py'), '--input', inp, '--out', out]
      const klines = strOr(args.klines)
      if (klines) argv.push('--klines', abs(klines))
      const portfolio = strOr(args.portfolio)
      if (portfolio) argv.push('--portfolio', abs(portfolio))
      const title = strOr(args.title)
      if (title) argv.push('--title', title)
      const r = await runPython(ctx, exec, argv, root)
      if (!r.ok) return { ok: false, error: String(r.error || '(未知)'), digest: r.digest || '' }
      // v1.4.6：单独重渲染看板也更新权威产物标记（否则 latest.json 会指向旧看板）。
      const latestFile = writeLatest(root, {
        kind: 'dashboard', report: inp, dashboard: out,
        ...(klines ? { klines: abs(klines) } : {}),
      })
      return {
        ok: true, digest: clip(r.stdout, 700),
        out_files: latestFile ? [out, latestFile] : [out],
        next: '用 present 工具把该 HTML 作为文件卡片交付给用户。',
      }
    })

  ctx.effect(() => ctx.tools.register(runTool), 'kexi:tool:run')
  ctx.effect(() => ctx.tools.register(validateTool), 'kexi:tool:validate')
  ctx.effect(() => ctx.tools.register(dashTool), 'kexi:tool:dashboard')

  // ── kexi_cli：本地 CLI 节点参与（需求 2）────────────────────────────────
  // 设计取向：不硬依赖 agy-first-bridge / codebuddy-first-bridge 等包——预设里
  // 硬引用它们，一旦用户卸载某个桥，整个预设组合会加载失败。改为运行时探测
  // ctx.tools 里已注册的 CLI 工具，按设置里的优先级派发；一个都没有就如实返回
  // available=[] 并让主理人回落为单模型执行（诚实降级，不假装有 CLI 帮忙）。
  const CLI_SPEC = {
    agy: { run: 'agy_run', cont: 'agy_continue', status: 'agy_status', cn: 'agy (Gemini/Antigravity)' },
    codebuddy: { run: 'codebuddy_run', cont: 'codebuddy_continue', status: 'codebuddy_status', cn: 'codebuddy (腾讯 CodeBuddy)' },
    mimo: { run: 'mimo_run', cont: 'mimo_continue', status: 'mimo_status', cn: 'mimo (小米 MiMo)' },
  }
  let kexiSettings = null
  const kexiSettingsPath = (() => {
    try {
      const home = process.env.DSH_HOME
      if (home && String(home).trim()) return join(String(home), 'kexi-settings.json')
    } catch (e) { }
    return join(HERE, '..', '..', 'kexi-settings.json')
  })()
  function loadKexiSettings() {
    if (kexiSettings !== null) return kexiSettings
    try {
      if (existsSync(kexiSettingsPath)) kexiSettings = JSON.parse(readFileSync(kexiSettingsPath, 'utf8'))
    } catch (e) { /* 坏配置回退默认 */ }
    return kexiSettings || {}
  }
  /** 读一个设置项（供 pipeline 默认值使用）。 */
  function cfgDefault(key) {
    try { return loadKexiSettings()[key] } catch (e) { return undefined }
  }
  // 设置可能被面板改动，定期失效重读（30s），避免长期缓存旧值
  if (ctx.interval) {
    try {
      const t = ctx.interval(() => { kexiSettings = null }, 30000)
      if (t && ctx.effect) ctx.effect(() => () => { try { t() } catch (e) { } })
    } catch (e) { /* timer 不可用时退化为首次读取 */ }
  }

  function availableClis() {
    const out = []
    for (const key of Object.keys(CLI_SPEC)) {
      const spec = CLI_SPEC[key]
      let has = false
      try { has = !!(ctx.tools && typeof ctx.tools.get === 'function' && ctx.tools.get(spec.run)) } catch (e) { has = false }
      if (!has) {
        // 退化探测：某些版本没有 tools.get
        try { has = !!(ctx.tools && Array.isArray(ctx.tools.list) && ctx.tools.list().some((x) => (x && x.name) === spec.run)) } catch (e) { has = false }
      }
      if (has) out.push(key)
    }
    const cfg = loadKexiSettings()
    const enabled = (cfg && cfg.cliEnabled) || {}
    const pri = Array.isArray(cfg && cfg.cliPriority) && cfg.cliPriority.length ? cfg.cliPriority : Object.keys(CLI_SPEC)
    return out
      .filter((k) => enabled[k] !== false)
      .sort((a, b) => {
        const ia = pri.indexOf(a); const ib = pri.indexOf(b)
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
      })
  }

  const cliTool = mkTool(
    'kexi_cli',
    '把一段独立子任务派给本地已安装的 CLI 智能体（agy / codebuddy / mimo）执行，返回其结论摘要。用于并行/交叉验证：例如让 agy 复核技术形态、让 codebuddy 复核代币解锁逻辑。action=list 先看有哪些 CLI 可用；action=run 派发。ctx 里传上下文文本（脚本 digest、结论片段），prompt 传要它做的事。',
    {
      type: 'object',
      additionalProperties: false,
      required: ['action'],
      properties: {
        action: { type: 'string', enum: ['list', 'run'], description: 'list=探测可用 CLI；run=派发任务' },
        cli: { type: 'string', description: '可选：指定 CLI（agy/codebuddy/mimo），缺省按设置优先级选第一个可用的' },
        prompt: { type: 'string', description: '要 CLI 完成的任务（action=run 必填）' },
        context: { type: 'string', description: '可选：附带给 CLI 的上下文（数据摘要/结论片段），避免它重复取数' },
        cwd: { type: 'string', description: '可选：CLI 工作目录' },
        background: { type: 'boolean', description: '可选：是否后台运行（默认 true，长任务建议 true）' },
      },
    },
    'kexi_cli',
    async (args) => {
      const avail = availableClis()
      if (args.action === 'list') {
        if (!avail.length) {
          return {
            ok: true, available: [], digest: '未探测到任何本地 CLI 桥工具（agy_run/codebuddy_run/mimo_run）。'
              + '可在设置面板「K析研判团」里检查开关，或安装对应 CLI 桥插件。',
            next: '单模型继续执行即可，不要声称有 CLI 协助。',
          }
        }
        return {
          ok: true, available: avail,
          digest: '可用本地 CLI（按设置优先级）：' + avail.map((k) => k + '=' + CLI_SPEC[k].cn).join('；'),
          next: '用 kexi_cli(action=run, cli=..., prompt=...) 派发交叉验证任务。',
        }
      }
      // action=run
      // 显式指定 CLI 时必须真的用它：若指定项不可用，直接报错而不是静默回落到
      // 别的 CLI——否则结论会被误标来源（例如用户要 mimo 复核，实际是 agy 跑的）。
      const want = args.cli ? String(args.cli) : null
      if (want && !CLI_SPEC[want]) {
        return { ok: false, error: `未知 CLI「${want}」，可选：${Object.keys(CLI_SPEC).join(', ')}` }
      }
      if (want && avail.indexOf(want) < 0) {
        return {
          ok: false, available: avail,
          error: avail.length
            ? `指定的 CLI「${want}」当前不可用（可用：${avail.join(', ')}）。请改用可用 CLI，或用 action=list 查看。`
            : `指定的 CLI「${want}」不可用：未探测到任何本地 CLI 桥工具。`,
          next: '不要谎称已让该 CLI 参与；改用可用 CLI，或如实说明并单模型继续。',
        }
      }
      const pick = want || avail[0]
      if (!pick) {
        return {
          ok: false, available: [], error: avail.length
            ? `指定的 CLI「${args.cli}」不可用（可用：${avail.join(', ')}）`
            : '没有可用的本地 CLI 桥工具（agy_run/codebuddy_run/mimo_run 均未注册）',
          next: '不要谎称已让 CLI 参与；如实说明并单模型继续。',
        }
      }
      if (!args.prompt || !String(args.prompt).trim()) return { ok: false, error: 'action=run 需要 prompt' }
      const spec = CLI_SPEC[pick]
      const tool = (ctx.tools && typeof ctx.tools.get === 'function') ? ctx.tools.get(spec.run) : null
      const exec = tool && (tool.execute || tool.handler || tool.run)
      if (typeof exec !== 'function') return { ok: false, error: `CLI 工具 ${spec.run} 不可调用` }
      const fullPrompt = args.context
        ? String(args.prompt) + '\n\n[上下文]\n' + String(args.context)
        : String(args.prompt)
      try {
        const raw = await exec({
          prompt: fullPrompt,
          cwd: args.cwd || undefined,
          background: args.background === undefined ? true : !!args.background,
        })
        const txt = typeof raw === 'string' ? raw : (raw && (raw.response || raw.digest || raw.text || raw.content)) || JSON.stringify(raw)
        const jobId = raw && (raw.jobId || raw.job_id || raw.id)
        return {
          ok: true, cli: pick, cliName: spec.cn, jobId: jobId || null,
          digest: clip(String(txt || ''), 900),
          next: jobId
            ? `后台任务已派发（jobId=${jobId}）。用 job_output 收集结果；结果到手后把它作为「本地 CLI 交叉结论」纳入研判，并注明来自 ${pick}。`
            : `已收到 ${pick} 结论，纳入研判时注明来源 ${pick}。`,
        }
      } catch (e) {
        return { ok: false, cli: pick, error: `调用 ${spec.run} 失败：${String((e && e.message) || e)}` }
      }
    })
  ctx.effect(() => ctx.tools.register(cliTool), 'kexi:tool:cli')

  // ── kexi_news：新闻面单次探测 + 降级标记（v1.4.6）────────────────────────
  // 背景（retro v1.4.5 backlog #4）：成员补抓新闻面时外部检索 401/被拦，反复
  // 尝试后才放弃，白等白费 token。给一个确定性探测入口：**单次调用**、带超时、
  // 结果落盘 kexi_out/news_status.json 供同 cwd 各成员共享；不可用时返回
  // news_available=false，成员据此显式标记「新闻面缺失」并快速跳过，禁止重试。
  const NEWS_TTL_MS = 10 * 60 * 1000
  let newsCache = null // 会话内缓存（每会话插件实例独立，先读盘再探测）

  function newsStatusPath(root) { return join(root, 'kexi_out', 'news_status.json') }
  function readNewsCache(root) {
    try {
      const p = newsStatusPath(root)
      if (!existsSync(p)) return null
      const j = JSON.parse(readFileSync(p, 'utf8'))
      if (j && typeof j.at === 'number' && Date.now() - j.at < NEWS_TTL_MS && typeof j.news_available === 'boolean') return j
    } catch (e) { /* 坏文件当无缓存 */ }
    return null
  }
  function saveNewsCache(root, payload) {
    try {
      const p = newsStatusPath(root)
      mkdirSync(dirname(p), { recursive: true })
      writeFileSync(p, JSON.stringify({ at: Date.now(), ...payload }, null, 2), 'utf8')
      return p
    } catch (e) { return null }
  }

  // 运行时探测可用的联网检索工具（不硬依赖 web-search-panel 包，诚实降级）。
  function availableSearchTool() {
    try {
      const t = ctx.tools && typeof ctx.tools.get === 'function'
        ? (ctx.tools.get('web_search_multi') || ctx.tools.get('web_search'))
        : null
      return t && typeof (t.execute || t.handler || t.run) === 'function' ? t : null
    } catch (e) { return null }
  }

  // 单次探测：真正跑一次最小查询。成功=有实质返回；异常/401/被拦/空返回=不可用。
  async function probeNewsOnce(root) {
    const tool = availableSearchTool()
    if (!tool) {
      return { news_available: false, reason: '未注册可用的联网检索工具（web_search_multi/web_search）', source: null }
    }
    try {
      const execFn = tool.execute || tool.handler || tool.run
      const raw = await withTimeout(
        execFn({ query: '加密货币 市场 新闻 最新', engine: 'auto', maxResults: 2, freshness: 'week' }),
        (Number(cfgDefault('newsProbeTimeoutSec')) || 12) * 1000,
        '新闻检索探测超时'
      )
      const txt = typeof raw === 'string' ? raw
        : (raw && (raw.answer || raw.digest || raw.summary || raw.text || raw.content)) || JSON.stringify(raw)
      const s = String(txt || '')
      const blocked = /401|403|forbidden|unauthorized|blocked|rate.?limit|timeout|超时|被拦|无返回/i.test(s.slice(0, 300))
      if (s.trim().length > 0 && !blocked) {
        return { news_available: true, reason: null, source: 'web_search' }
      }
      return { news_available: false, reason: clip(s, 120) || '检索无返回', source: 'web_search' }
    } catch (e) {
      return { news_available: false, reason: clip(String((e && e.message) || e), 120), source: 'web_search' }
    }
  }

  const newsTool = mkTool('kexi_news',
    '新闻面通道探测（带降级）：**只调用一次**。检查本地是否有可用的联网检索工具，并做一次最小查询探测。返回 news_available：true=可用（可按需抓取）；false=不可用（401/被拦/超时/未注册）→ 本次研判应**显式标记「新闻面缺失」并快速跳过**，不要反复尝试联网检索。结果按 cwd 落盘 kexi_out/news_status.json，10 分钟内同目录成员共享同一结论。无参数。',
    { required: [], properties: {} },
    'kexi_news',
    async (args, exec) => {
      const root = workspaceRoot(ctx, exec)
      // ① 会话内缓存（同一成员会话只探测一次）
      if (newsCache && Date.now() - newsCache.at < NEWS_TTL_MS) {
        return { ok: true, news_available: newsCache.news_available, digest: newsDigest(newsCache), out_files: newsCache.file ? [newsCache.file] : [] }
      }
      // ② 落盘缓存（同 cwd 其他成员共享，避免各自重试）
      const cached = readNewsCache(root)
      if (cached) {
        newsCache = { ...cached }
        return { ok: true, news_available: cached.news_available, digest: newsDigest(cached), out_files: [newsStatusPath(root)] }
      }
      // ③ 真正探测一次
      const res = await probeNewsOnce(root)
      const rec = { ...res, at: Date.now() }
      newsCache = rec
      const file = saveNewsCache(root, res)
      newsCache.file = file || null
      return { ok: true, news_available: res.news_available, digest: newsDigest({ ...res, file }), out_files: file ? [file] : [] }
    })
  function newsDigest(rec) {
    const base = rec.news_available
      ? '新闻检索通道可用（本次为探测，未抓正文）；需要新闻/催化剂时用 web_search/web_search_multi 抓取。'
      : `新闻检索通道不可用（${rec.reason || '未知原因'}）→ 本次研判显式标记「新闻面缺失」，跳过新闻补充，禁止反复尝试。`
    return base + (rec.file ? `\n状态已落盘：${rec.file}` : '')
  }
  ctx.effect(() => ctx.tools.register(newsTool), 'kexi:tool:news')

  // ── 持久团队供给（v1.4.0 建队 · v1.4.6 确定性兜底）─────────────────────────
  // 背景（v1.4.6 取证结论，见 docs/backlog1-team-launch-finding.md）：建队原先
  // **只**靠提示词第 0 步「先调用 kexi_ensure_team」，没有任何代码级兜底。真实
  // 会话回放显示 4 个研判 lead 都自觉调用了它（能力没空转），但供给链路的确定性
  // 不满足「队友供给要确定性、幂等」——模型一旦跳过第 0 步，面板会静默只剩主理人
  // 1 人，且没有任何地方提示。故把建队抽成普通函数，kexi_ensure_team 与首次研判
  // 调用共用同一条幂等路径。
  function teamService() {
    try { return (typeof ctx.get === 'function') ? ctx.get('agentTeams') : (ctx.agentTeams || null) } catch (e) { return null }
  }

  /** 判断调用方是否团队主理人（成员会话不建队，否则 spawnTeammate 抛 TEAM_LEAD_REQUIRED）。 */
  function isLeadAgent(exec, svc) {
    try {
      const a = exec && exec.agent
      if (!a) return false
      // 首选服务自己的判定（权威）：role==='lead' 才建队。
      if (svc && typeof svc.tryMembership === 'function') {
        const m = svc.tryMembership(a)
        return !!m && m.role === 'lead'
      }
      if (a.session && a.session.header) return !a.session.header.parentSession
      // 信息不足时不阻断——保证兜底仍然生效；真若误判，spawnTeammate 会抛错并被捕获降级。
      return true
    } catch (e) { return false }
  }

  /**
   * 幂等建队（kexi_ensure_team 与自动兜底共用）。
   * @returns {{ok:boolean, state:string, digest:string, created?:string[], skipped?:string[], error?:string}}
   */
  async function buildPersistentTeam(exec) {
    const cfg = loadKexiSettings()
    // 设置项 persistentTeam 缺省 true（用户可在设置面板关闭持久占位）。
    if (cfg.persistentTeam === false) {
      return { ok: true, state: 'disabled', created: [], skipped: [],
        digest: '持久团队已在设置中关闭（persistentTeam=false）；本次按需调用成员即可，原生面板仅显示主理人。' }
    }
    // 防御式获取实验性 agentTeams 服务（未启用/不可用时诚实降级，不报错）。
    const teamSvc = teamService()
    if (!teamSvc || typeof teamSvc.spawnTeammate !== 'function' || typeof teamSvc.listMembers !== 'function') {
      return { ok: true, state: 'unavailable', created: [], skipped: [],
        digest: '当前 DSH 未启用原生 Agent Teams 运行时；团队以按需成员方式协作（成员会出现在实时进度弹窗），原生智能体团队面板暂只显示主理人。' }
    }
    if (!isLeadAgent(exec, teamSvc)) {
      return { ok: true, state: 'not-lead', created: [], skipped: [],
        digest: '当前会话不是团队主理人（成员会话），跳过建队。' }
    }
    try {
      const existing = teamSvc.listMembers(exec.agent) || []
      const have = new Set(existing.map((m) => m && m.name).filter(Boolean))
      const created = []; const skipped = []
      for (const spec of TEAM_MEMBERS) {
        if (have.has(spec.name)) { skipped.push(spec.cn); continue }
        await teamSvc.spawnTeammate(exec.agent, {
          name: spec.name,
          description: spec.description,
          prompt: [{ type: 'text', text: spec.prompt }],
          context: 'fresh',
          provider: 'spawn',
          signal: exec.signal,
        })
        created.push(spec.cn)
      }
      const roster = teamSvc.listMembers(exec.agent) || []
      const lines = roster.map((m) => {
        const tag = m.role === 'lead' ? '主理人' : (TEAM_MEMBERS.find((x) => x.name === m.name)?.cn || m.name)
        return `· ${tag}：${m.status}`
      })
      const head = [
        created.length ? `已创建持久成员：${created.join('、')}` : '四位成员均已存在，无需重复创建',
        skipped.length && created.length ? `（已跳过：${skipped.join('、')}）` : '',
      ].join('')
      return {
        ok: true, state: created.length ? 'created' : 'exists', created, skipped,
        digest: [head, '当前团队名册：', ...lines].filter(Boolean).join('\n'),
      }
    } catch (e) {
      return { ok: false, state: 'error', created: [], skipped: [],
        error: `预置持久团队失败（可继续按需协作）：${String((e && e.message) || e)}` }
    }
  }

  // 会话级「已尝试自动建队」记忆：同一会话只试一次，避免每次 kexi_run 都探测/派发。
  const autoEnsureTried = new Set()
  function sessionKey(exec) {
    try {
      const s = exec && exec.agent && exec.agent.session
      const id = s && s.id
      return id ? String(id) : 'default'
    } catch (e) { return 'default' }
  }

  /**
   * 确定性兜底：真正的研判调用首次发生时，若团队尚未就绪就自动补建一次。
   * 幂等（已存在则全是 skipped）、尊重 persistentTeam 开关、成员会话直接跳过。
   * @returns {null|object} null 表示本次无需/不再重复尝试。
   */
  async function autoEnsureTeam(exec) {
    if (!isLeadAgent(exec, teamService())) return null
    const key = sessionKey(exec)
    if (autoEnsureTried.has(key)) return null
    autoEnsureTried.add(key)
    const r = await buildPersistentTeam(exec)
    return r
  }

  // ── kexi_ensure_team：会话开始幂等预置原生持久团队（v1.4.0）──────────────
  // 主理人会话第一步调用一次，把数脉/指北/望潮/守拙创建为 continuable 队友 →
  // 直接显示在会话页智能体团队面板。已存在则跳过（v1.4.6 起 kexi_run 也会自动兜底）。
  const ensureTool = mkTool('kexi_ensure_team',
    '会话开始时调用一次：幂等创建 K析研判团五位持久成员（数脉→指北→望潮→守拙→证伪）为原生智能体团队队友，使其直接显示在会话页智能体团队面板；已存在的成员自动跳过。返回当前团队名册与每位成员状态。无参数。（v1.4.6 起 kexi_run 也会自动幂等兜底，模型忘记调用不会导致面板缺人。）',
    { required: [], properties: {} },
    'kexi_ensure_team',
    async (args, exec) => {
      const r = await buildPersistentTeam(exec)
      if (!r.ok) return { ok: false, error: String(r.error || '(未知)'), out_files: [] }
      const out = { ok: true, digest: r.digest, out_files: [] }
      if (r.state === 'created' || r.state === 'exists' || r.state === 'not-lead') {
        // v1.5.2：send_message 的 target 只认**成员代号**（shuma/zhibei/wangchao/shouzhuo/zhengfu），
        // 传中文名会报 `active teammate "数脉" not found`（2026-09-29 实测，
        // 主理人因此白费一轮 list_agents 才发现）。这里把映射直接写进指引。
        out.next = '持久团队已就绪。派活时 send_message 的 target 必须用成员代号：'
          + 'shuma=数脉(取数) → zhibei=指北(指标) → wangchao=望潮(情景) → shouzhuo=守拙(风控) → zhengfu=证伪(唱反调)，'
          + '按此顺序派活；**证伪必须最后派**（他攻击前面三位的结论，前提是他们已出结论）；'
          + '成员结论权威，不代写不篡改。'
      }
      return out
    })
  ctx.effect(() => ctx.tools.register(ensureTool), 'kexi:tool:ensure-team')

  // ── kexi_cex：CEX 账户工具（v1.6.0 实盘链路）───────────────────────────
  // 为什么单独建工具而不是复用 kexi_run(mode=script)：
  //   ① **安全**：手写 argv 让模型能任意拼参数。实盘下单时，参数拼错一个
  //      （比如市场写成 spot 而以为是合约、方向弄反）就是真实的资金损失。
  //      结构化参数把拼装权收回代码。
  //   ② **权限不可翻转**：自主级别只从 host 写的 autonomy.json 读，
  //      **本工具不暴露任何能改变它的参数**。模型想开实盘只能请用户去面板点。
  //   ③ **可审计**：每次写操作都带 reason，并落订单意图日志（应对 37% 回合失败率）。
  const cexTool = mkTool('kexi_cex',
    'CEX 账户操作：读余额/持仓、查挂单、下单/撤单、看订单意图日志。\n'
    + '⚠ 自主级别（只读/提议+确认/受限自动/全自动）**由用户在设置面板决定，模型无权修改**——'
    + '本工具不提供任何改变它的参数。被级别拦下时会把 blocked_by 与提示如实返回，不要重试绕过。\n'
    + '⚠ 下单前若拿不准账户现状，先 action=journal 看有没有"已落意图但无结果"的未决订单'
    + '（上一轮可能崩在中途），并先 action=all_positions 读全部持仓。\n'
    + '⚠ 每次下单必须填 reason（会进审计日志）与 idem_seq（同一次决策的重试传相同值，避免重复下单）。',
    {
      required: ['action'],
      properties: {
        action: {
          type: 'string',
          enum: ['health', 'accounts', 'balances', 'positions', 'all_positions', 'cost_basis',
                 'open_orders', 'order', 'cancel', 'journal'],
          description: 'health=体检(不发起交易请求) / accounts=已配置账户 / balances=余额 / '
            + 'positions=单账户持仓 / all_positions=多交易所聚合持仓 / open_orders=未成交挂单 / '
            + 'cost_basis=现货成本价（由历史订单推导，含覆盖范围标注）/ '
            + 'order=下单 / cancel=撤单 / journal=订单意图日志(崩溃后对账)',
        },
        exchange: { type: 'string', description: 'binance / okx / gate / mexc（all_positions 可省略=全部）' },
        label: { type: 'string', description: '账户标签，缺省用设置里的默认标签（支持同所多账户）' },
        symbols: { type: 'string', description: 'cost_basis 用：交易对列表（空格分隔，如 "PYTH-USDT MOVE-USDT"）' },
        market: { type: 'string', enum: ['spot', 'usdtm', 'coinm'], description: '现货或合约市场' },
        account_type: { type: 'string', description: '余额口径：spot / usdtm / coinm / unified' },
        symbol: { type: 'string', description: '交易对，如 BTCUSDT' },
        side: { type: 'string', enum: ['buy', 'sell'] },
        qty: { type: 'number', description: '下单数量（合约按张）' },
        price: { type: 'number', description: '限价单价格；市价单留空' },
        order_type: { type: 'string', description: 'MARKET(默认) / LIMIT' },
        notional_usd: { type: 'number', description: '名义美元额，风控按它算仓位占比——**强烈建议填**' },
        stop_loss: { type: 'number', description: '止损价。受限自动档**必填**，否则一律拒绝' },
        take_profit: { type: 'number', description: '止盈价（挂单成功后自动挂）' },
        leverage: { type: 'integer', description: '杠杆倍数（合约）' },
        order_id: { type: 'string', description: '撤单时填交易所返回的订单号；留空则撤该 symbol 全部' },
        reason: { type: 'string', description: '下单理由，进审计日志。**必填**，要具体（如"周线共振回踩MA20"）' },
        idem_seq: { type: 'string', description: '幂等序号：同一次决策重试时传相同值，防止重复下单' },
        limit: { type: 'integer', description: 'journal 的条数上限' },
      },
    },
    'kexi_cex',
    async (args, exec) => {
      const store = cexStoreDirGuess()
      const outDir = workspaceCwd ? join(workspaceCwd, 'kexi_out') : join(process.cwd(), 'kexi_out')
      try { mkdirSync(outDir, { recursive: true }) } catch { /* 目录建不了也不该阻断只读操作 */ }
      const A = args || {}
      const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? String(v) : null)
      const push = (arr, k, v) => { if (v !== null && v !== undefined && String(v) !== '') arr.push(k, String(v)) }

      // ⚠ v1.9.11：argv[0] 必须是**绝对路径**，不能是裸文件名。
      //   实测（v1.9.10 拿到真实报错后）：`can't open file '...backend\dsh\node_mod...'`
      //   —— Python 按 **cwd** 解析裸文件名，而这里传的 cwd 是
      //   `workspaceCwd || process.cwd()`（= dsh 包的 lib 目录），**脚本不在那儿**。
      //   本文件 L214 的注释早就写着正确做法是「传 cwd = scriptsDir」，
      //   但 cex 这一处漏了。改用绝对路径，从此与 cwd 无关。
      const CEX_SCRIPT = join(HERE, 'skills', 'crypto-market-analysis', 'scripts', 'cex_adapter.py')
      const argv = [CEX_SCRIPT, '--store-dir', store]
      let action = String(A.action || 'health')

        if (action === 'all_positions') {
          argv.push('all-positions')
          if (A.market) push(argv, '--markets', A.market)
        } else if (action === 'open_orders') {
          // v1.9.12：模型在真实会话里点名的工具层 bug ——
          //   「open_orders 报参数错误（open_orders vs 脚本要求的 open-orders）」。
          //   根因：action 名用下划线、CLI 子命令用连字符，而这条分支原先落在
          //   else 里直接 push(action)，原样推了带下划线的名字。
          //   同源问题还有两处：(1) 被错误塞进 positions 那条 --market（本子命令
          //   用的是 --markets nargs）；(2) 默认值 usdtm 对现货挂单是错的 ——
          //   用户那笔 PYTH 正是现货单，默认查合约市场会查不到，
          //   而「查不到挂单」会让人误判成「没有挂单占用」。
          argv.push('open-orders')
          if (A.exchange) push(argv, '--exchange', A.exchange)
          if (A.label) push(argv, '--label', A.label)
          if (A.symbol) push(argv, '--symbol', A.symbol)
          if (A.market) push(argv, '--markets', A.market)
          else push(argv, '--markets', 'spot', 'usdtm')
        } else if (action === 'cost_basis') {
          // v1.9.16：现货成本价（历史订单推导）。同样要过连字符映射——
          // 否则会掉进 else 原样 push('cost_basis')，重蹈 open_orders 覆辙。
          argv.push('cost-basis')
          if (A.exchange) push(argv, '--exchange', A.exchange)
          if (A.label) push(argv, '--label', A.label)
          if (A.symbols) push(argv, '--symbols', ...(Array.isArray(A.symbols) ? A.symbols : [A.symbols]))
      } else if (action === 'health' || action === 'accounts' || action === 'journal') {
        argv.push(action)
        if (action === 'journal') push(argv, '--limit', num(A.limit) || '20')
      } else {
        if (!A.exchange) {
          return { ok: false, error: `action=${action} 必须指定 exchange`, out_files: [] }
        }
        argv.push(action)
        push(argv, '--exchange', A.exchange)
        if (A.label) push(argv, '--label', A.label)
        if (action === 'balances') push(argv, '--account-type', A.account_type || 'spot')
          if (action === 'positions') push(argv, '--market', A.market || 'usdtm')
        if (action === 'order') {
          if (!A.symbol || !A.side) {
            return { ok: false, error: '下单必须给 symbol 与 side(buy/sell)', out_files: [] }
          }
          if (!A.reason) {
            // 不硬拦，但把缺失如实标出来——审计日志里"没理由的单"是事后追责的噩梦
          }
          push(argv, '--symbol', A.symbol)
          push(argv, '--side', A.side)
          push(argv, '--qty', num(A.qty))
          push(argv, '--price', num(A.price))
          push(argv, '--order-type', A.order_type || 'MARKET')
          push(argv, '--market', A.market || 'spot')
          push(argv, '--notional-usd', num(A.notional_usd))
          push(argv, '--stop-loss', num(A.stop_loss))
          push(argv, '--take-profit', num(A.take_profit))
          push(argv, '--leverage', num(A.leverage))
          push(argv, '--idem-seq', A.idem_seq)
        }
        if (action === 'cancel') {
          if (!A.symbol) return { ok: false, error: '撤单必须给 symbol', out_files: [] }
          push(argv, '--symbol', A.symbol)
          push(argv, '--order-id', A.order_id)
          push(argv, '--idem-seq', A.idem_seq)
        }
      }

      const r = await runPython(ctx, exec, argv, workspaceCwd || process.cwd())
      // ⚠ v1.9.10：**必须看 r.ok / r.error**。
      //   runPython 失败时返回的是 `{ ok:false, exitCode, python, error, digest }`
      //   ——**根本没有 stdout 字段**。旧代码只读 `r.stdout`，于是
      //   `lastJsonLine(undefined)` → null → `parsed = {}`，
      //   再由 renderCexDigest 把空结果渲染成「未配置任何凭据」。
      //   **那 900 字符的真实报错（r.error）一直躺在返回值里被扔掉。**
      //   这是「不可观测的前提等于没有前提」的典型：拿不到证据时不要编一个。
      if (r && r.ok === false) {
        const res = { ok: false, error: String(r.error || 'CEX 脚本运行失败') }
        res.digest = `**CEX 脚本运行失败**（${r.python || 'python'}，退出码 ${r.exitCode ?? '?'}）\n`
          + `原因：${clip(String(r.error || '(无 stderr)'), 600)}`
          + `\n〔凭据目录 …${String(store || '').slice(-44)}〕`
          + '\n⚠ 这**不是**"未配置账户"——是脚本没跑起来，按上面的真实报错处理。'
        res.out_files = []
        return res
      }
      const parsed = lastJsonLine(r.stdout) || {}
      const outFile = join(outDir, `cex_${action}_${Date.now()}.json`)
      try { writeFileSync(outFile, JSON.stringify(parsed, null, 2), 'utf8') } catch { /* 落盘失败不阻断 */ }

      // ⚠⚠ v1.9.4：**绝不能返回带 `undefined` 值的属性**。
      //   实测（用户会话 session-0d90495b）：`kexi_cex` 连续 5 次调用里 4 次
      //   报 `tool "kexi_cex" returned invalid output: value is not lossless JSON`，
      //   只有那一次返回了**错误字符串**的调用成功了。
      //   原因：`{ error: undefined }` 在 JS 里是**存在的属性**，只是值为 undefined。
      //   `JSON.stringify` 会悄悄丢掉它（所以本地测不出来），
      //   但框架要求**无损**序列化——遇到 undefined 直接拒绝整个返回值。
      //   于是「成功」比「失败」更容易炸，这解释了那个反直觉的比例。
      //
      //   修法：**按需赋值**，值不存在就干脆不设这个键。
      const res = { ok: parsed.ok === true }
      if (parsed.error) res.error = parsed.error
      else if (parsed.ok === false) res.error = 'CEX 操作未成功（详见 out_files）'
      // ⚠ v1.9.9：把**实际用的凭据目录**和 **Python 的 stderr** 放进摘要。
      //   排查「未配置任何凭据」时，我在黑盒里猜了好几轮 DSH_HOME 到底是哪个值
      //   ——猜错方向就只能瞎改。工具自己报出来，比我猜可靠。
      //   stderr 同样关键：此前 Python 失败时摘要只有「失败」两个字，
      //   **等于把唯一的诊断线索丢掉了**。
      const dirTail = store ? store.slice(Math.max(0, store.length - 44)) : '(未知)'
      const stderrTail = String(r.stderr || '').trim().slice(-240)
      res.digest = `〔凭据目录 …${dirTail}〕\n${renderCexDigest(action, parsed, A)}`
        + (stderrTail ? `\n⚠ Python stderr: ${stderrTail}` : '')
      res.out_files = [outFile]
      if (parsed.blocked_by) {
        res.next = `被【${parsed.blocked_by}】拦截：${parsed.hint || '按提示处理，不要重试绕过'}`
      } else if (parsed.pending_confirmation) {
        res.next = '订单草案已生成但**未发送**——请用户在设置面板确认'
      }
      return res
    })
  ctx.effect(() => ctx.tools.register(cexTool), 'kexi:tool:cex')

  /** 把 CEX 脚本的返回值压成 ≤700 字符的紧凑摘要（token 纪律）。 */
  function renderCexDigest(action, d, A) {
    if (!d || typeof d !== 'object') return 'CEX 无返回'
    // ⚠ v1.9.10：**空结果绝不能被渲染成一个具体原因**。
    //   实测踩到：`cex_accounts_*.json` 落盘全是 `{}`——说明 Python 压根没输出 JSON
    //   （`lastJsonLine(r.stdout)` 什么都没解析到）。而旧代码里 `d.accounts || []`
    //   为空就返回「**未配置任何凭据**」——那是一个**凭空捏造的原因**：
    //   用户会照着去重新配密钥，而真因是脚本没跑起来。
    //   **不可观测的前提等于没有前提**：拿不到证据时要说"拿不到证据"。
    if (!Object.keys(d).length) {
      return '**CEX 脚本没有返回任何 JSON**（不是"未配置账户"）——'
        + '请看下方 Python stderr；常见原因是脚本路径/工作目录不对，或 Python 启动就失败'
    }
    if (d.autonomy && action === 'health') {
      return `CEX 体检 · 自主级别【${d.autonomy.level}】· 已配置账户 ${d.configured_count || 0} 个 · `
        + `可用适配器 ${(d.adapters || []).join('/')}`
        + (d.autonomy.note ? `　※ ${d.autonomy.note}` : '')
    }
    if (action === 'accounts') {
      const a = d.accounts || []
      if (!a.length) return `CEX 账户：未配置任何凭据（${d.note || ''}）`
      return `CEX 账户 ${a.length} 个：` + a.map((x) => `${x.exchange}/${x.label}(${x.api_key_masked || '***'})`).join('、')
    }
    if (action === 'balances') {
      return `${d.exchange}/${d.label} 余额（${d.account_type}）· 净值约 $${d.net_usd ?? '—'}`
    }
    if (action === 'positions') {
      if (d.error) return `${d.exchange}/${d.label} 持仓读取失败：${d.error}`
      return `${d.exchange}/${d.label} ${d.market} 持仓 ${d.count} 笔 · 未实现盈亏 $${d.unrealized_pnl_total ?? 0}`
    }
    if (action === 'all_positions') {
      return `聚合持仓 ${d.count} 笔（${d.accounts} 个账户）· 未实现盈亏 $${d.unrealized_pnl_total ?? 0}`
        + (d.errors && d.errors.length ? `　⚠ ${d.errors.length} 个账户取数失败（见 out_files）` : '')
    }
    if (action === 'order' || action === 'cancel') {
      const o = d.order || {}
      const head = `${d.sent ? '⚡ 已发送' : (d.pending_confirmation ? '📋 草案待确认' : '⛔ 未发送')} `
        + `${d.exchange}/${d.label} ${o.symbol || A.symbol || ''} ${o.side || ''}`
        + (o.qty ? ` ${o.qty}` : '') + (o.notional_usd ? ` ($${o.notional_usd})` : '')
      if (d.error) return `${head}\n原因：${d.error}${d.hint ? `\n提示：${d.hint}` : ''}`
      const rk = (d.risk && d.risk.reasons) || []
      return `${head}\n风控：${rk.length ? rk.join('；') : '通过'}${d.message ? `\n${d.message}` : ''}`
    }
    if (action === 'journal') {
      const un = d.unresolved || []
      return `订单日志 ${(d.recent || []).length} 条 · 未决 ${un.length} 笔`
        + (un.length ? `　⚠ 有 ${un.length} 笔已落意图但无结果，下单前请先核对` : '')
    }
    return `CEX ${action}: ${d.ok ? '成功' : (d.error || '失败')}`
  }

  /**
   * 凭据目录：与 host 侧 cexStoreDir() 保持同一套定位规则。
   *
   * ⚠ v1.9.8：host 与 preset 的 `process.env.DSH_HOME` **可能不是同一个值**。
   *   实测（用户会话 session-eb9b6472）：host 侧指向 `…\DSH Desktop\dsh-home`
   *   （凭据就在那儿），preset 侧却指向 `…\DSH Desktop\backend\dsh`，
   *   于是 preset 把错误目录传给 `--store-dir`，Python 找不到任何凭据，
   *   工具回「**未配置任何凭据**」——看起来像用户没配账户，**实际是路径分叉**。
   *
   *   修法：**不猜，逐个候选目录找真正含凭据的那个**。
   *   判据是**存在 `*.dpapi` 文件**（真正有凭据的标志），而不是"目录存在"。
   *   一个都没有时仍返回首选，保持旧行为、让报错指向预期位置。
   */
  function cexStoreDirGuess() {
    const cands = []
    const push = (p) => { try { if (p) cands.push(p) } catch (e) { /* 忽略 */ } }
    const h = process.env.DSH_HOME
    if (h && String(h).trim()) push(join(String(h).trim(), 'kexi-cex'))
    try { push(join(homedir(), '.dsh', 'kexi-cex')) } catch (e) { /* 忽略 */ }
    try {
      const appData = process.env.APPDATA
      if (appData) push(join(appData, 'DSH Desktop', 'dsh-home', 'kexi-cex'))
    } catch (e) { /* 忽略 */ }
    for (const d of cands) {
      try {
        if (existsSync(d) && readdirSync(d).some((f) => /\.dpapi$/.test(f))) return d
      } catch (e) { /* 读不了就试下一个 */ }
    }
    return cands[0] || join(process.cwd(), 'kexi-cex')
  }

  // ── systemPrompt: token 纪律 + 路由策略（紧凑，一屏内）─────────────────────
  const policy = [
    'kexi:policy — K析研判团执行纪律：',
    '0) 团队就位（每个会话第一步，只做一次）：先调用 kexi_ensure_team —— 它会幂等创建五位持久成员（数脉→指北→望潮→守拙→证伪）为原生智能体团队队友，使其直接显示在会话页智能体团队面板；之后再开始任务。',
    '1) 数据铁律：只用已收盘K线（脚本已内置停更拦截 last_bar_age_days）；绝不凭记忆报价，一切数字来自 kexi_run/成员返回的落盘文件。',
    '2) token 纪律：大 JSON 不回对话——引用时只用 digest 或 compact_report 摘要（~700字符）；明细让成员或用户直接读 kexi_out/ 文件；给成员的 send_message 用 ≤3 行（任务+输入路径+输出要求），不重复角色说明。',
    '1) 默认团队协作：研判/选币/复盘/风控类请求，用 send_message 依次派活——'
      + 'target 传成员代号 shuma(数脉·取数) → zhibei(指北·指标) → wangchao(望潮·情景) → shouzhuo(守拙·风控) → zhengfu(证伪·唱反调)'
      + '（**target 必须是英文代号，传中文名会报 active teammate not found**）；'
      + '成员结论为权威、不代写不篡改；仅对极简单的速答才主理人亲自 kexi_run(mode=pipeline) 出看板，并如实说明走了捷径。',
    '1c) **红队强制（v1.5.9）**：守拙管风险闸门、望潮管情景推演，但**没有人专门攻击结论的前提**——这是编排的真实空缺。'
      + '故深研派活时**证伪必须最后派、且必须真派**：把望潮三情景与守拙评级原文发给 zhengfu，'
      + '要求他输出①最脆弱前提（标可观测/不可观测）②backtest.py 基础率（含样本数）③反事实压力测试④撤回条件。'
      + '**不允许把证伪的输出当"又一个风控意见"处理，也不允许因为"预计没问题"就不派**——'
      + '他回答"未发现致命漏洞但基础率仅 N 例"同样是有效产出。'
      + '主理人汇报时必须**如实转述证伪的反对意见**；若证伪的反对与望潮结论冲突，'
      + '**两条都要呈现给用户并下调整体置信度**，不得只留顺耳的那条。',
    '1b) **先快答、再深研（v1.5.3，最高优先级纪律）**：实测单币团队全流程 **17 分钟**，'
      + '而同一套分析脚本串起来只要 **~1 秒**（fetch 505ms + 指标/位置/多周期/入场/画像 ≈ 400ms）——'
      + '99.9% 的时间耗在模型编排往返而非计算，**结果出来时币价已经涨了几个点**。因此：'
      + '①任何"XX 币怎么样/能不能买/什么价进"的问题，**第一步必须先 kexi_run(mode=fast, symbol=XXX 或 symbols=A,B,C)** '
      + '拿到秒级结论（评级/位置/多周期/入场动作+价位/失效位/追高嫌疑/画像/三情景），**先把可执行价位交给用户**；'
      + '②只有当用户明确要"深度论证/成员交叉验证/多币对比/完整看板"时，才升级到派活团队或 mode=pipeline；'
      + '③升级时把 fast_analysis.json 直接喂给成员，**不要重算**（同一脚本口径，重算只会更慢且可能口径分叉）；'
      + '④对话里必须如实标注结论来源是"快答（确定性脚本）"还是"团队深研（成员交叉验证）"，不得含糊。',
    '4) 交付：报告 JSON 先 kexi_validate --fix 过契约，再 kexi_dashboard 出 HTML，用 present 交付文件卡片；文本报告按五段式（结论/关键位/三情景/风险/免责），免责声明必附。',
    '5) 失败处理：脚本报 error 就如实降级（换源/缩周期/告知缺口），禁止编造数字补位。',
    '5b) 数据源诚实纪律（B5/B6）：①外部新闻面需要时先用 kexi_news 探测一次，不可用则显式标记「新闻面缺失」快速跳过；②2026 年 X/Twitter 无可用免费读源（B6），已使用 Alternative.me 恐惧贪婪指数与 CMC 热搜作为情绪替代，并向用户明示该限制；③大额资金流入（B5）由 DefiLlama 稳定币流向（datasources/keepalive）覆盖。',
    '6) 本地 CLI 可作为团队成员（可选，用户可开关）：agy/codebuddy/mimo 这些本地 CLI 既能做 kexi_cli 交叉验证，也可在用户「把 CLI 纳入团队成员」开启时作为团队一员承担子任务；用 kexi_cli(action=list) 探测、action=run 派发。CLI 结论是参考意见而非权威——与脚本数据冲突时以脚本数据为准；用户未启用或无可用 CLI 时如实说明并单模型继续，禁止假装有 CLI 协助。成员/CLI 的每一步都会出现在实时进度弹窗与团队名册里。',
    '7) 选币质量与实操经验反哺（v1.5.0）：①位置与多周期同看：必须给出 90 日区间位置与距高点回撤，周/月线逆向时判定为弱反弹而非反转，高位且已放量多日（拥挤度）严厉扣分；②事前可证伪：三情景必须严格带价格区间（[range_low, range_high]）与基准价，禁止只报概率不给区间；③止盈止损三法交叉验证（ATR/结构位/均线法）+ 底仓分批建仓；④风控结论反向约束名单：宁缺毋滥，极高危标的坚决剔除不凑数。',
    '7b) 入场时机与币种画像（v1.5.1）：①综合评估后必须给入场/出场时机——用 entry_plan.py 出四类动作（立即入场/回调至X企稳反弹入场/抄底分批点位/放量突破Y补仓）+ 失效位，追高嫌疑（pos_90≥80/crowded/stretched）时禁止"立即入场"建议；②币种行为画像必须给——用 coin_profile.py（配 BTC/ETH 参照）说明该币习惯（震荡拉盘/快速拉盘震荡出货/缓慢拉盘快速出货/趋势行情）、顺大盘还是背离大盘（r/beta）、走大饼还是以太行情、以及 BTC-ETH 比价风格；③入场环境四条件量化判定：放量增长/高换手/量升大盘稳/资金流出减少（稳定币净增发转正），不满足时降级为"等回调/等突破"。',
    '7c) 消息面高比重与定时监控（v1.5.1）：①行情、世界局势、大额资金、社媒消息面在研判中占高比重——望潮/守拙结论必须引用消息面证据（news_watch.json 的世界局势/大额资金话题）；②消息面获取走三层：news_watch.py 特殊通道（RSSHub Telegram：吴说/Cointelegraph/WatcherGuru/WhaleAlert）→ 常规通道（BlockBeats/金色财经/币安公告）→ 全灭时显式标「消息面缺失」不阻塞；③长任务/盯盘场景启动 news_watch.py --loop 定时拉取（默认 30 分钟），新增条目经 kexi_notify 通知；④暗网数据源需付费/Tor 超出能力边界——不假装有暗网监控，暗网事件经正规快讯渠道覆盖并向用户明示。',
    '8) CEX 实盘与保活铁律（v1.5.0）：①密钥仅本地 Windows DPAPI 当前用户加密存储不上云，严禁开通提现权限；②所有实盘交易默认 DRY_RUN=True，保守档硬性风控（单笔上限/杠杆/止损必填/HARD_CEILING）代码级硬拦截，任何真实下单必须经用户确认；③保活监控（keepalive/kexi_notify）默认 notify_only 仅通报并请求确认，落盘渠道强制开启绝不丢事件。',
    '8b) **CEX 账户操作纪律（v1.6.0）**：①读余额/持仓/聚合持仓一律用 kexi_cex（action=balances/positions/all_positions），'
      + '**不要用 kexi_run 手写 cex_adapter.py 的 argv**——参数拼错就是真实资金损失；'
      + '②**自主级别由用户在设置面板决定，模型无权修改**：只读/提议+确认/受限自动/全自动。被级别拦下时'
      + '如实转述 blocked_by 与提示，**严禁重试、严禁找别的路绕过**（那是在欺骗用户的安全设置）；'
      + '③下单前**必须**先 action=journal 看有没有"已落意图但无结果"的未决单（本项目实测 37% 回合失败率，'
      + '上一轮可能崩在中途导致"单已成交但没记住"，不核对就重下会变成重复下单），再 action=all_positions 读全部持仓；'
      + '④每次下单必须填 reason（进审计日志）与 idem_seq（同一次决策重试传相同值）；'
      + '⑤受限自动档**必须给 stop_loss**，无止损的单一律拒绝——方向可以猜，仓位不能赌；'
      + '⑥CEX 相关结论必须区分"这是链上公开数据"还是"这是你的真实账户数据"，不要混为一谈；'
      + '⑦被风控（cex_risk）拦截时如实转述拦截原因，**不要换个参数再试**试图绕过。',
    '9) 批量与产物溯源纪律（v1.5.2）：①**禁止手写 Python 跑分析**——多标的批量一律用 kexi_run(mode=script, script=…, batch={items, args:["--input","kexi_out/{item}_1d_klines.json","--out","kexi_out/{item}_1d_indicators.json"], concurrency:4})，一次调用跑完 N 个标的（2026-09-29 实测：因缺批量能力，成员手写 4 个 Python 脚本，其中 1 个 SyntaxError 崩掉整轮返工）；②批量结果自带脏数据闸门，被标 ⚠ 的（停更/样本不足）按铁律直接剔除，**不要写脚本重试掩盖**；③引用任何产物前先 kexi_run(mode=files) 看本轮/历史分组——只引用【本轮】，【历史】是上一轮及更早的文件（实测主理人曾把上一轮的 zhibei_final20.json 当本轮产物反复排查）。',
  ].join('\n')
  ctx.effect(() => ctx.systemPrompt.section({
    name: 'kexi:policy',
    order: (typeof ctx.systemPrompt.getSectionOrder === 'function' ? ctx.systemPrompt.getSectionOrder('TOOL_SUBAGENT') : null) ?? 2810,
    text: policy,
  }), 'kexi:systemPrompt:policy')
}
