// scripts/verify.mjs — 打包自检：契约静态校验（安装前跑，也在 CI/开发循环里跑）
// 用法: node scripts/verify.mjs [bundleRoot]
// 检查: package.json dsh 接线 / locale 形状 / 图标尺寸 / patch YAML 可解析与结构 /
//       !!js 标量在 (baseUrl,ctx,process) 下可求值 / exports 子路径文件存在 /
//       node --check 各 .mjs,.js / 技能 frontmatter 合规。
import { readFileSync, existsSync, statSync, readdirSync, rmSync } from 'node:fs'
import { join, dirname, resolve, relative } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { execFileSync } from 'node:child_process'

const ROOT = resolve(process.argv[2] || dirname(dirname(fileURLToPath(import.meta.url))))
const problems = []
const notes = []
function ok(cond, msg) { if (!cond) problems.push(msg) }

// ── 1. package.json ─────────────────────────────────────────────────────────
const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'))
ok(pkg.name === 'dsh-kexi-crypto', `包名应为 dsh-kexi-crypto，实际 ${pkg.name}`)
ok(pkg.dsh?.bundle?.patch === './home-plugin/dsh-kexi-crypto/cordis.patch.yml', 'dsh.bundle.patch 路径不对')
ok(pkg.dsh?.client?.platform === 'web', 'dsh.client.platform 应为 web')
ok(pkg.main === './home-plugin/dsh-kexi-crypto/lib/index.mjs', 'main 应指向 host index.mjs')
ok(pkg.exports?.['./client'] === './home-plugin/dsh-kexi-crypto/lib/client.js', 'exports["./client"] 不对')
ok(pkg.exports?.['./kexi-plugin'] === './preset/kexi-crypto/kexi-plugin.mjs', 'exports["./kexi-plugin"] 不对')
ok(pkg.exports?.['./locale/*.json'], 'exports 缺 ./locale/*.json（卡片文案会静默消失）')
ok(pkg.exports?.['./package.json'], 'exports 缺 ./package.json')
ok(pkg.icon && existsSync(join(ROOT, pkg.icon.replace(/^\.\//, ''))), 'icon 文件不存在')
for (const [sub, rel] of Object.entries(pkg.exports || {})) {
  if (typeof rel === 'string' && !rel.includes('*')) {
    ok(existsSync(join(ROOT, rel.replace(/^\.\//, ''))), `exports["${sub}"] 指向缺失文件 ${rel}`)
  }
}
notes.push(`package.json ${pkg.name}@${pkg.version}`)

// ── 1b. 版本兜底常量必须与 package.json 一致（v1.6.4）────────────────────
// host 读不到 package.json 时会退回 PLUGIN_VERSION_FALLBACK。若它与真实版本漂移，
// 用户会看到**过期但看起来正常**的版本号——比显示 unknown 更糟（unknown 至少可疑）。
// 所以这里断言二者相等：改了 package.json 却忘了改常量，测试立刻变红。
{
  const hostSrc = readFileSync(join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/index.mjs'), 'utf8')
  const m = hostSrc.match(/PLUGIN_VERSION_FALLBACK\s*=\s*'([^']+)'/)
  ok(!!m, 'host 缺 PLUGIN_VERSION_FALLBACK（读不到 package.json 时无兜底版本）')
  ok(m && m[1] === pkg.version,
    `PLUGIN_VERSION_FALLBACK(${m ? m[1] : '?'}) ≠ package.json(${pkg.version}) —— 卡片会显示过期版本号`,
    '请同步更新 index.mjs 的 PLUGIN_VERSION_FALLBACK')
  ok(/version:\s*PLUGIN_VERSION/.test(hostSrc), 'host 未把 version 暴露给前端')
  ok(/name === 'dsh-kexi-crypto'/.test(hostSrc),
    'host 未按 name 匹配上溯查找 package.json（安装副本里 lib/ 在包根下 3 层，硬 join 会读不到）')
  const cli = readFileSync(join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/client.js'), 'utf8')
  ok(/className:\s*"kexi-ver"/.test(cli), 'client 未在卡片上渲染版本徽标（kexi-ver）')
  ok(/\.kexi-ver\{/.test(cli), 'client 缺 kexi-ver 样式')
}

// ── 2. icon ≤256 KiB ────────────────────────────────────────────────────────
const iconPath = join(ROOT, pkg.icon.replace(/^\.\//, ''))
const iconBytes = statSync(iconPath).size
ok(iconBytes <= 256 * 1024, `icon 超过 256 KiB：${iconBytes}`)
notes.push(`icon ${iconBytes} B`)

// ── 3. locale 形状 ──────────────────────────────────────────────────────────
for (const lang of ['en', 'zh']) {
  const p = join(ROOT, 'locale', `${lang}.json`)
  ok(existsSync(p), `缺 locale/${lang}.json`)
  if (existsSync(p)) {
    const j = JSON.parse(readFileSync(p, 'utf8'))
    ok(typeof j.meta?.title === 'string' && j.meta.title.length > 0, `locale/${lang}.json meta.title 缺失`)
    ok(typeof j.meta?.description === 'string' && j.meta.description.length > 0, `locale/${lang}.json meta.description 缺失`)
  }
}

// ── 4. patch YAML ───────────────────────────────────────────────────────────
const patchPath = join(ROOT, pkg.dsh.bundle.patch.replace(/^\.\//, ''))
const patchText = readFileSync(patchPath, 'utf8')
// 动态加载 js-yaml（harness 依赖，开发机在 backend/dsh/node_modules）
let yaml = null
for (const cand of [
  process.env.DSH_BACKEND || 'C:\\Users\\lcl\\AppData\\Roaming\\DSH Desktop\\backend\\dsh',
]) {
  const p = join(cand, 'node_modules', 'js-yaml', 'index.js')
  if (existsSync(p)) { yaml = await import(pathToFileURL(p).href); break }
}
if (yaml) {
  const jsScalars = []
  const type = new yaml.default.Type('tag:yaml.org,2002:js', {
    kind: 'scalar',
    resolve: (data) => typeof data === 'string',
    construct(data) { jsScalars.push(String(data)); return { __jsExpr: String(data) } },
  })
  const schema = yaml.default.JSON_SCHEMA.extend([type])
  let doc
  try {
    doc = yaml.default.load(patchText, { schema })
  } catch (e) {
    problems.push(`patch YAML 解析失败: ${e.message}`)
  }
  if (doc) {
    ok(Array.isArray(doc) && doc.length === 2, `patch 应为 2 个顶层 insert 序列，实际 ${(Array.isArray(doc) && doc.length) ?? 'not-list'}`)
    const rows = (doc || []).flatMap((ins) => ins.insert || [])
    const host = rows.find((r) => r.id === 'kexi-dashboard')
    const preset = rows.find((r) => r.id === 'preset-kexi-crypto')
    ok(host?.name === 'dsh-kexi-crypto', 'host 行 (kexi-dashboard) 缺失或 name 不是裸包名')
    ok(preset?.name === '@deepseek-ai/dsh-agent-preset', 'preset 声明行 name 错误')
    if (preset) {
      const cfg = preset.config
      ok(cfg.id === 'kexi-crypto', 'config.id 应为 kexi-crypto')
      ok(typeof cfg.name === 'string', 'config.name 必须是纯字符串（对象会导致挂载失败）')
      ok(cfg.order >= 5, 'order 应 ≥5')
      const plugins = cfg.plugins || []
      const ids = plugins.map((p) => p.id)
      for (const need of ['persona', 'agent-instructions', 'tool-bash', 'tool-pwsh', 'tool-fs', 'tool-fs-search', 'tool-jobs', 'skill-filesystem', 'tool-skill', 'tool-goal', 'planning', 'compaction', 'delegation', 'tool-present', 'tool-ask-user', 'tool-todo', 'tool-web', 'kexi-plugin']) {
        ok(ids.includes(need), `plugins 缺行: ${need}`)
      }
      ok(plugins.find((p) => p.id === 'kexi-plugin')?.name === 'dsh-kexi-crypto/kexi-plugin', 'kexi-plugin 行须用裸子路径名')
      const del = plugins.find((p) => p.id === 'delegation')
      const members = (del?.config || []).filter((c) => c.config?.toolName && c.config.toolName !== 'subagent')
      ok(members.length === 5, `delegation 组内应恰好 5 个角色行（v1.5.9 加证伪红队），实际 ${members.length}`)
      for (const m of members) {
        ok(typeof m.config.persona === 'string' && m.config.persona.length > 50, `角色 ${m.config.toolName} persona 缺失`)
        ok(m.config.maxDepth === 1, `角色 ${m.config.toolName} 应 maxDepth: 1（D9：0 会让首次委派即被拒）`)
        ok(Array.isArray(m.config.toolFilter?.deny), `角色 ${m.config.toolName} 应有 toolFilter.deny`)
      }
      notes.push(`plugins ${plugins.length} 行 / 成员 ${members.map((m) => m.config.toolName).join(', ')}`)

      // ── 5a. customSkillDirs 结构形态（v1.1.0 事故：列表项 + 返回数组 → [["path"]]）──
      // 必须把 !!js 挂在 **值位置**（解析为 JsExpr 对象）；若写成列表项，
      // 解析结果会是 [JsExpr]，求值后即嵌套数组，schemastery 报
      // "$.customSkillDirs[0] expected string but got <path>"。
      const sf = plugins.find((p) => p.id === 'skill-filesystem')
      const csd = sf?.config?.customSkillDirs
      if (csd && typeof csd === 'object' && csd.__jsExpr) {
        notes.push('customSkillDirs 为值位置 !!js（形态正确）')
      } else if (Array.isArray(csd)) {
        problems.push(`customSkillDirs 不得写成列表项（会嵌套成 [["path"]]）；应把 !!js 放在值位置。实际: ${
          JSON.stringify(csd).slice(0, 80)}`)
      } else {
        problems.push(`customSkillDirs 形态异常: ${JSON.stringify(csd)?.slice(0, 80)}`)
      }
    }
    // ── 5. !!js 标量求值冒烟 ────────────────────────────────────────────────
    // ⚠ v1.1.0 事故修正：host 补丁里 baseUrl = 补丁所在目录 URL；
    // 但 **preset 行内**的 baseUrl 是 profile 根（实测 dsh-home/profiles/<name>/），
    // 与包内目录无关。故同时也用 profile 根做一遍求值——这正是嵌套数组 bug 与
    // 相对路径错锚当初逃过校验的原因。
    const fakeBaseUrl = new URL('.', pathToFileURL(join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'cordis.patch.yml'))).href
    const profileBaseUrls = [
      'file:///C:/Users/lcl/AppData/Roaming/DSH%20Desktop/dsh-home/profiles/web/',
      new URL('.', pathToFileURL(join(ROOT, 'package.json'))).href,
    ]
    let jsOk = 0
    for (const src of jsScalars) {
      try {
        // 真实 loader: new Function("ctx","expr",`with(ctx){return eval(expr)}`)
        // ctx 携带 baseUrl；process 走全局兜底。用 with 复现同一作用域。
        const fn = new Function('ctx', 'expr', 'with (ctx) { return eval(expr) }')
        const val = fn({ baseUrl: fakeBaseUrl }, src)
        if (src.includes('fileURLToPath') || src.includes('customSkillDirs') || src.includes('skills')) {
          // 【类型形态】customSkillDirs 必须是 string[]（曾因 `- !!js` 列表项返回数组
          // 导致 [["path"]] 嵌套，schemastery 报 "expected string but got <path>"）
          if (Array.isArray(val)) {
            for (const [i, el] of val.entries()) {
              if (typeof el !== 'string') problems.push(`customSkillDirs[${i}] 应为 string，实际 ${Array.isArray(el) ? 'array(嵌套!)' : typeof el}`)
              else if (!el) problems.push(`customSkillDirs[${i}] 为空字符串（会被 resolve 成 cwd）`)
            }
          } else if (typeof val !== 'string') {
            problems.push(`技能根表达式应返回 string[]，实际 ${typeof val}`)
          }
          // 【路径可达性】在每种 baseUrl 场景下都必须命中真实存在的目录
          for (const bu of [fakeBaseUrl, ...profileBaseUrls]) {
            let v2
            try { v2 = fn({ baseUrl: bu }, src) } catch (e) { problems.push(`!!js 在 baseUrl=${bu.slice(0, 48)}… 下求值失败: ${e.message}`); continue }
            const dir = Array.isArray(v2) ? v2[0] : v2
            if (typeof dir === 'string' && dir && !existsSync(dir)) {
              problems.push(`!!js 技能根在 baseUrl=${bu.slice(0, 40)}… 下不存在: ${dir}`)
            }
          }
          const dir = Array.isArray(val) ? val[0] : val
          if (typeof dir === 'string' && existsSync(dir)) notes.push(`skills root OK: ${relative(ROOT, dir)}`)
        }
        jsOk++
      } catch (e) {
        problems.push(`!!js 求值失败 [${src.slice(0, 60)}…]: ${e.message}`)
      }
    }
    notes.push(`!!js 标量 ${jsOk}/${jsScalars.length} 可求值（含 profile 根 baseUrl 场景）`)
  }
} else {
  notes.push('警告: 未找到 js-yaml，跳过 patch YAML 结构校验（可设 DSH_BACKEND 环境变量）')
}

// ── 6. 实时可视化 / CLI 编排 / 设置面板 接线检查（v1.2.0）───────────────────
// 这三项是 v1.2.0 的新增能力，静态接线错了会静默失效（UI 不报错，只是没东西）。
// 用源码文本断言把关键契约钉住：
//   需求1 — host 必须订阅 session/event（活动流唯一权威源）并暴露 /activity；
//   需求2 — kexi-plugin 必须注册 kexi_cli，且**不得**硬引用 CLI 桥包名
//           （预设里硬引用会让卸载桥的用户整个预设加载失败）；
//   需求3 — host 暴露 /settings 且能落盘；client 注册 settings.plugin.item。
{
  const hostSrc = readFileSync(join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'lib', 'index.mjs'), 'utf8')
  const clientSrc = readFileSync(join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'lib', 'client.js'), 'utf8')
  const pluginSrc = readFileSync(join(ROOT, 'preset', 'kexi-crypto', 'kexi-plugin.mjs'), 'utf8')

  // 剥掉注释后的**代码视图**。字面量断言必须用它，否则会被注释里
  // 「解释这个断言为什么存在」的说明文字自己匹配上——实测踩过：
  // 注释里写了 `.enc.json` 说明为什么要禁它，断言就把注释当成了违规证据。
  const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1 ')
  const hostCode = stripComments(hostSrc)

  // 需求 1：活动流
  ok(/ctx\.on\(\s*['"]session\/event['"]/.test(hostSrc), 'host 未订阅 session/event（实时进度弹窗会没有数据）')
  // 断言只看真实的 path: 字段——反证实测过：用 includes() 会被文件头注释里的
  // 同一个路径字符串喂饱，路由改名后仍通过（假阳性）。
  const hostRoutes = hostSrc.split('\n')
    .map((l) => l.match(/^\s*path:\s*['"]([^'"]+)['"]/))
    .filter(Boolean).map((m) => m[1])
  ok(hostRoutes.includes('/kexi-dashboard/activity'), `host 未注册 /kexi-dashboard/activity 路由（已注册：${hostRoutes.join(', ')}）`)
  ok(hostRoutes.includes('/kexi-dashboard/settings'), `host 未注册 /kexi-dashboard/settings 路由（已注册：${hostRoutes.join(', ')}）`)
  ok(hostRoutes.includes('/kexi-dashboard/status'), 'host 未注册 /kexi-dashboard/status 路由（研判灯数据源）')
  for (const ev of ['tool/call', 'tool/result', 'turn/start', 'turn/end', 'step/start']) {
    ok(hostSrc.includes(`'${ev}'`) || hostSrc.includes(`"${ev}"`), `活动流未处理 ${ev} 事件`)
  }
  // 函数声明形态（不能只 test /describeTool/ —— 反证过：describeToolXX 与调用点
  // 都会匹配，函数被改名后仍通过）
  ok(/function\s+describeTool\s*\(/.test(hostSrc), 'host 缺 describeTool 函数（工具名不会翻译成可读动作）')
  // client 侧同样只认 fetch("...") 调用位置，避免注释喂饱断言
  const clientFetches = (clientSrc.match(/fetch\(\s*["']([^"']+)["']/g) || []).join(' ')
  ok(clientFetches.includes('/kexi-dashboard/activity'), `client 未轮询 /activity（进度弹窗无数据源；实际 fetch: ${clientFetches || '无'}）`)
  ok(clientFetches.includes('/kexi-dashboard/status'), 'client 未轮询 /status（研判灯无数据源）')
  ok(clientFetches.includes('/kexi-dashboard/settings'), 'client 设置卡未读写 /settings')
  ok(clientSrc.includes('ProgressPopup'), 'client 缺进度弹窗组件')
  ok(clientSrc.includes('kexi-tl') && clientSrc.includes('kexi-node'), 'client 缺时间线节点样式')

  // 需求 2：本地 CLI 编排
  ok(/['"]kexi_cli['"]/.test(pluginSrc), 'kexi-plugin 未注册 kexi_cli 工具')
  ok(/availableClis/.test(pluginSrc), 'kexi_cli 缺 availableClis 运行时探测')
  // v1.2.1：CLI 交叉验证自动接进 kexi_run 流水线
  ok(/\bautoCliCrosscheck\b/.test(pluginSrc), 'kexi_run 未读 autoCliCrosscheck 设置（自动交叉验证开关）')
  ok(/rp\.crosscheck\s*=\s*crosscheck/.test(pluginSrc), '交叉验证结论未回写 report.json（rp.crosscheck = crosscheck）')
  ok(/xchkLine/.test(pluginSrc), 'digest 未附带 CLI 复核行（xchkLine）')
  ok(/withTimeout/.test(pluginSrc), '交叉验证缺 withTimeout（CLI 挂死会拖垮流水线）')
  ok(pluginSrc.includes('独立复核员'), '交叉验证 prompt 缺独立复核角色设定')
  // 用词边界，避免 cliPriorityX 这类子串误匹配（反证实测过：无 \b 时漏检）
  ok(/\bcliPriority\b/.test(pluginSrc) && /\bcliEnabled\b/.test(pluginSrc), 'kexi_cli 未读取设置里的 CLI 优先级/开关')
  // 反向断言：不得硬引用桥包 —— 运行时探测才是正确形态。
  // 只看真实的 name: 字段（注释里提到包名属于说明文字，不是接线）。
  const patchNames = patchText.split('\n')
    .map((l) => l.match(/^\s*name:\s*['"]?([^'"\s#]+)['"]?/))
    .filter(Boolean).map((m) => m[1])
  for (const bad of ['agy-first-bridge', 'codebuddy-first-bridge', 'mimo-first-bridge']) {
    ok(!patchNames.some((n) => n.includes(bad)), `patch 的 name: 硬引用了 ${bad}（卸载该桥会导致整个预设加载失败；应走 kexi_cli 运行时探测）`)
  }
  // 诚实降级 + 不静默换源
  ok(pluginSrc.includes('不要谎称') || pluginSrc.includes('谎称'), 'kexi_cli 缺"不得谎称 CLI 参与"的纪律')
  ok(/指定.*不可用|当前不可用/.test(pluginSrc), 'kexi_cli 缺"指定 CLI 不可用时报错"（否则会静默换源、结论误标来源）')

  // 需求 3：设置面板
  ok(/writeFileSync/.test(hostSrc) && /kexi-settings\.json/.test(hostSrc), 'host 设置未落盘到 kexi-settings.json')
  ok(clientSrc.includes('settings.plugin.item'), 'client 未注册设置面板卡（settings.plugin.item）')
  ok(/clamp\(/.test(hostSrc), 'host 设置缺数值边界收敛（面板脏值会落盘）')
  // v1.2.1：新设置项接线
  ok(/autoCliCrosscheck/.test(hostSrc) && /cliCrosscheckTimeoutSec/.test(hostSrc), 'host 缺 v1.2.1 新设置默认值（autoCliCrosscheck/cliCrosscheckTimeoutSec）')

  // 人设默认=团队协作（v1.3.0 起）。v1.4.0 起措辞升级为"用 send_message 给持久成员派活"，
  // 旧的"必须按顺序委派成员"已替换——断言同时接受两种历史措辞，并要求点出 send_message。
  ok(/必须按顺序用 send_message 给持久成员派活/.test(patchText), '主理人人设未强制"默认团队协作、必须按顺序用 send_message 给持久成员派活"（否则模型默认单兵跑命令）')
  ok(!/简单看盘一步[\s\S]{0,12}kexi_run/.test(patchText), '主理人人设仍保留旧的"简单看盘一步 kexi_run 即可"默认（与团队形态冲突）')
  ok(/六人智能体团队/.test(patchText), '主理人人设未点明这是"六人智能体团队"（用户预期：主理人 + 五位成员）')

  // v1.4.0：① 四位成员升级为原生持久团队（解决"智能体团队面板只看到当前会话一个"）
  ok(/kexi_ensure_team/.test(patchText) && /kexi_ensure_team/.test(pluginSrc), '缺 kexi_ensure_team（会话第一步幂等预置持久成员的接线）')
  const tmBlock = (pluginSrc.match(/const TEAM_MEMBERS = \[([\s\S]*?)\n\]/) || [])[1] || ''
  for (const tn of ['shuma', 'zhibei', 'wangchao', 'shouzhuo', 'zhengfu']) {
    ok(tmBlock.includes(`name: '${tn}'`), `TEAM_MEMBERS 缺持久成员 ${tn}`)
  }
  // v1.5.9：证伪（Red Team）必须真的"攻击结论"，而不是又一个风控副本。
  // 编排空缺是真实的——守拙管闸门、望潮管推演，没人专门拆结论的前提。
  ok(/与守拙的区别|你与守拙的区别/.test(pluginSrc), '证伪人设缺「与守拙的区别」纪律（否则会退化成第二个风控官）')
  ok(/最脆弱的前提|隐含前提/.test(pluginSrc), '证伪人设缺「拆前提」职责（攻击结论的隐含假设）')
  ok(/基础率/.test(pluginSrc), '证伪人设缺「历史基础率」职责（用 backtest.py 量化反驳）')
  ok(/反事实|压力测试/.test(pluginSrc), '证伪人设缺「反事实压力测试」职责')
  ok(/撤回/.test(pluginSrc), '证伪人设缺「撤回条件」职责（给出可观察的改口触发点）')
  ok(/不得输出与望潮相同的结论|价值全在/.test(pluginSrc), '证伪人设缺「不得与望潮结论雷同」纪律')
  ok(/zhengfu=证伪|zhengfu\(证伪/.test(pluginSrc), 'kexi_ensure_team 指引缺证伪代号（send_message target 必须用代号）')
  // 红队必须最后派：他要攻击前面三位的结论，前提是他们已出结论
  ok(/证伪必须最后派|zhengfu\(证伪/.test(pluginSrc), '缺「证伪最后派」编排次序铁律')
  // 名册/映射必须五处同步，否则证伪在面板上显示不出中文名
  for (const [label, src] of [['host MEMBER_CN', hostSrc], ['host 委派映射', hostSrc], ['client TEAM', clientSrc]]) {
    ok(/falsification_challenger/.test(src), `${label} 缺 falsification_challenger（证伪无法在 UI 显示为中文名）`)
  }
  ok(/zhengfu: '证伪'|"zhengfu"/.test(hostSrc + clientSrc), '名册缺 zhengfu 键（证伪不会出现在团队面板）')
  ok(/member-falsifier/.test(patchText) && /falsification_challenger/.test(patchText),
    'cordis.patch.yml 缺 member-falsifier 委派行（按需委派通道拿不到证伪）')
  // spawn 为 continuable（fresh + provider spawn）才能显示在原生面板且可续跑
  ok(/provider:\s*'spawn'/.test(pluginSrc) && /context:\s*'fresh'/.test(pluginSrc), '持久成员未按 fresh + provider spawn 创建（否则不显示/不可续跑）')
  // 幂等：创建前先 listMembers 跳过已存在，避免每次会话重复 spawn
  ok(/listMembers/.test(pluginSrc) && /spawnTeammate/.test(pluginSrc), 'kexi_ensure_team 缺 listMembers/spawnTeammate（幂等预置）')
  // 持久团队开关关闭时诚实走按需路径
  ok(/persistentTeam === false/.test(pluginSrc), 'kexi_ensure_team 未尊重 persistentTeam 开关')

  // v1.4.0：② 新设置项 persistentTeam / cliAsMembers（默认值 + host 接线）
  ok(/persistentTeam:\s*true/.test(hostSrc) && /cliAsMembers:\s*true/.test(hostSrc), 'host DEFAULT_SETTINGS 缺 persistentTeam/cliAsMembers 默认 true')
  ok(/"persistentTeam"/.test(clientSrc) && /"cliAsMembers"/.test(clientSrc), '设置面板缺 persistentTeam/cliAsMembers 开关')

  // v1.4.0：③ 进度弹窗名册追加启用的 CLI 成员卡（仅当 cliAsMembers 开启）
  ok(/const CLI_TEAM = \[/.test(clientSrc), 'client 缺 CLI_TEAM（把本地 CLI 作为团队成员展示）')
  ok(/kexi-member-cli/.test(clientSrc), 'client 缺 CLI 外部成员样式 kexi-member-cli')
  ok(/cliAsMembers/.test(hostSrc), 'host /activity 未下发 cliAsMembers（名册无法决定是否显示 CLI 卡）')

  // v1.3.0：② 设置在插件内部直达（弹窗齿轮 → SettingsPopup），不再只依赖外部设置页
  ok(/function SettingsPopup/.test(clientSrc), 'client 缺独立设置模态 SettingsPopup（用户在插件内找不到设置）')
  ok(/kexi-pop-gear/.test(clientSrc), '进度弹窗缺齿轮按钮（用户无法从弹窗打开设置）')
  ok(/onSettings/.test(clientSrc), '进度弹窗未接 onSettings 回调')

  // v1.3.0：③ 团队名册面板（四位成员 + 谁在干活）
  ok(/function TeamRoster/.test(clientSrc), 'client 缺团队名册 TeamRoster')
  // 把检查限定在 TEAM 定义块内，逐个核对四位成员工具名（不假定彼此间隔字符数）。
  const teamBlock = (clientSrc.match(/const TEAM = \[([\s\S]*?)\]\s*;/) || [])[1] || ''
  for (const tn of ['market_data_specialist', 'technical_indicators', 'trend_forecaster',
                     'risk_assessor', 'falsification_challenger']) {
    ok(teamBlock.includes(tn), `团队名册 TEAM 缺成员 ${tn}`)
  }

  // v1.3.0：④ host 工作目录从 session.header.cwd 读（dsh-session 无 get cwd()）
  ok(/function sessionCwd/.test(hostSrc) && /header[^;]{0,30}\.cwd/.test(hostSrc), 'host 缺 sessionCwd（工作目录在 header.cwd，直接读 session.cwd 恒为空）')
  // subagent 事件真实签名是 (identity, parent) 两个参数
  ok(/ctx\.on\(\s*['"]subagent\/start['"]\s*,\s*\(\s*identityOrPayload\s*,\s*parent\s*\)/.test(hostSrc), 'subagent/start 处理未按真实 (identity, parent) 双参数签名')
  ok(clientSrc.includes('autoCliCrosscheck'), '设置面板缺 autoCliCrosscheck 开关')
  // 关键兼容约束：本机 DSH 已移除 client settingsScope，强注入会白屏
  ok(/exports\.inject\s*=\s*\[\s*["']slots["']\s*\]/.test(clientSrc), 'client inject 必须恰好是 ["slots"]（注入 settingsScope 会导致本机 DSH 白屏）')

  // D9 防回归（2026-09-28 实测事故）：maxDepth 是"绝对深度上限"而非"子代可再派层数"。
  // dsh-subagent 判定 childDepth = delegationDepthOf(parent)+1 > maxDepth 即拒。
  // 主理人 depth=0 派成员 childDepth=1：maxDepth:0 → 连第一次委派都被拒
  // （"subagent depth 1 exceeds maxDepth 0"，四位成员全部不可用）；
  // maxDepth:1 → 成员可被派、成员再派时 childDepth=2>1 被拦（原意保住）。
  // 只认值位置的 maxDepth 行（注释里的 "maxDepth 0" 是历史教训记录，不算）。
  const memberDepths = patchText.split('\n')
    .map((l) => l.match(/^\s*maxDepth:\s*(\d+|provider-managed)\s*$/))
    .filter(Boolean).map((m) => m[1])
  ok(memberDepths.length === 5, `成员行应有 5 个 maxDepth（实际 ${memberDepths.length}）`)
  ok(memberDepths.every((v) => v === '1'), `成员 maxDepth 必须=1（实际 [${memberDepths.join(', ')}]）——0 会让首次委派即被拒（D9）`)

  // D10 防回归：CLI 成功文本字段是 response（三家桥 buildResult 均如此），
  // 必须在提取链最前面；漏了它会退成存整个 JSON、agreement 永远 unclear。
  ok(/raw\.response \|\| raw\.digest/.test(pluginSrc) || /raw\.response\s*\|\|/.test(pluginSrc), 'CLI 结果提取链缺 raw.response（D10：真实桥文本在 response，漏了会存整个 JSON、agreement 恒为 unclear）')

  // v1.4.1：turn/step 节点必须在对应 end 事件关闭（否则永久 running → 会话永久 busy，
  // 新任务进度被淹没、弹窗 0→N 跳变不再触发）。
  ok(/kind === 'step' && b0\.nodes\[i\]\.status === 'running'/.test(hostSrc), 'step/end 未关闭 running 的「模型思考中」节点')
  ok(/kind === 'turn' && b0\.nodes\[i\]\.status === 'running'/.test(hostSrc), 'turn/end 未关闭 running 的「新一轮开始」节点')
  ok(/BUSY_FRESH_MS/.test(hostSrc), 'host busy 判定缺 BUSY_FRESH_MS 卡死保护')
  // v1.4.2：弹窗不自动弹出——只由用户点状态灯手动唤出。
  // 自动弹出的全部机制（busyPrimeRef/busySeenRef/autoRef/autoOpenOnRun）必须移除，
  // 防止重新引入自动弹窗；setOpenProg 只允许出现在用户点击回调里。
  ok(!/busyPrimeRef|busySeenRef|autoRef|autoOpenOnRun/.test(clientSrc), 'client 仍残留自动弹窗机制（弹窗应仅由用户点状态灯唤出）')
  ok(!/autoOpenOnRun/.test(hostSrc), 'host 仍残留 autoOpenOnRun 设置')
  ok(/只更新活动数据，由用户点状态灯手动唤出/.test(clientSrc), 'client tick2 未注明"仅更新、不自动弹"')

  // v1.4.3：最终看板成果在 K析页面直接可见。
  ok(/function recordArtifact/.test(hostSrc), 'host 缺 recordArtifact（看板产物登记）')
  ok(/\/kexi-dashboard\/artifact/.test(hostSrc), 'host 缺 /artifact 看板服务路由')
  ok(/recordArtifact\(session, null, payload\)/.test(hostSrc), 'tool/result 未用结构化 payload 调用 recordArtifact')
  ok(/function extractToolPayload/.test(hostSrc), 'host 缺 extractToolPayload（从 message.content 提取结构化结果）')
  ok(/toolCallId/.test(hostSrc), 'host 未从 message.toolCallId 取 callId（真实事件无顶层 callId）')
  ok(/kexi-result-frame/.test(clientSrc) && /kexi-tabs/.test(clientSrc), 'client 缺成果标签/内嵌 iframe')
  ok(/\.map\(function \(s\) \{ return s\.artifact; \}\)/.test(clientSrc), 'client 未从会话收集 artifact')
  // 安全：artifact 只能按已登记 id 取，不能让请求方指定文件路径（防路径穿越）。
  ok(/searchParams\.get\('id'\)/.test(hostSrc) && !/searchParams\.get\('path'\)/.test(hostSrc), 'artifact 路由不应接受 path 参数（只认登记 id）')

  // v1.4.5：惰性看板发现兜底（成果登记曾两次因事件字段假设偏差失败 → 用户跑完看不到成果）。
  ok(/function discoverArtifact/.test(hostSrc), 'host 缺 discoverArtifact（按工作目录惰性发现看板）')
  ok(/if \(!b\.artifactId\) discoverArtifact\(b\)/.test(hostSrc), '/activity 未在无成果会话上调用 discoverArtifact')
  ok(/function registerArtifactFor/.test(hostSrc), 'host 缺统一的 registerArtifactFor 登记入口')

  // v1.4.6：① 权威产物标记 latest.json（目录里同族看板并存时按 mtime 会猜错权威）。
  ok(/LATEST_SCHEMA\s*=\s*'kexi\.latest\/1'/.test(pluginSrc), '插件缺 latest.json 权威产物标记常量（LATEST_SCHEMA）')
  ok(/function writeLatest/.test(pluginSrc), '插件缺 writeLatest（kexi_run/kexi_dashboard 成功渲染即登记权威产物）')
  ok(/writeLatest\(root, \{/.test(pluginSrc), 'kexi_run pipeline 未调用 writeLatest 登记权威产物')
  ok(/function readLatestArtifact/.test(hostSrc), 'host 缺 readLatestArtifact（懒发现优先读 latest.json）')
  ok(/const marked = readLatestArtifact\(b\)/.test(hostSrc), 'discoverArtifact 未优先读 latest.json（仍只按 mtime 猜）')
  ok(/kexi_out['"]\s*,\s*'latest\.json'|'kexi_out\/latest\.json'/.test(hostSrc), 'host 缺 kexi_out/latest.json 标记路径')

  // v1.4.6：② 团队拉起确定性兜底（建队原先只靠提示词第 0 步，模型跳过则面板静默缺人）。
  ok(/async function buildPersistentTeam/.test(pluginSrc), '插件缺 buildPersistentTeam（建队逻辑抽成普通函数，工具与兜底共用）')
  ok(/async function autoEnsureTeam/.test(pluginSrc), '插件缺 autoEnsureTeam（首次研判调用时确定性自动建队兜底）')
  ok(/autoEnsureTeam\(exec\)/.test(pluginSrc), 'kexi_run 未接入 autoEnsureTeam 兜底')
  ok(/isLeadAgent/.test(pluginSrc), 'autoEnsureTeam 缺 isLeadAgent 判定（成员会话不建队，避免 TEAM_LEAD_REQUIRED）')

  // v1.4.6：③ 新闻面单次探测 + 降级标记（外部检索 401/被拦时禁止反复尝试）。
  ok(/function probeNewsOnce/.test(pluginSrc), '插件缺 probeNewsOnce（新闻通道单次探测）')
  ok(/news_available/.test(pluginSrc), 'kexi_news 缺 news_available 返回（降级标记）')
  ok(/新闻面缺失/.test(pluginSrc), 'kexi_news 缺「新闻面缺失」显式标记文案（成员据此跳过）')
  ok(/禁止反复尝试/.test(pluginSrc), 'kexi_news/policy 缺"禁止反复尝试联网检索"纪律')
  ok(/news_status\.json/.test(pluginSrc), 'kexi_news 未落盘 news_status.json（同 cwd 成员共享结论）')

  // v1.5.1：① 团队动向卡片（team/* + subagent/catalog + assistant/message 事件接线）。
  // 取证（session-6d282c27，2026-09-29）：这些事件经 session/event 广播但原先被
  // switch 忽略 → 卡片全员待命、无团队动向。断言用真实事件名（引号包裹）钉住接线。
  for (const ev of ['team/member', 'team/message/queued', 'team/message/delivered', 'subagent/catalog', 'assistant/message']) {
    ok(hostSrc.includes(`'${ev}'`), `活动流未处理 ${ev} 事件（v1.5.1 团队动向卡片会空转）`)
  }
  ok(/function rosterUpdate/.test(hostSrc) && /function rosterOf/.test(hostSrc), 'host 缺团队名册状态函数（rosterOf/rosterUpdate）')
  ok(/const MEMBER_CN = \{/.test(hostSrc), 'host 缺 MEMBER_CN 成员中文名映射')
  ok(/team = \{/.test(hostSrc) && /members: Object\.values\(b\.team\.members\)/.test(hostSrc), '/activity 未暴露团队名册（team.members）')
  ok(/evaluation/.test(hostSrc) && /b\.evaluation/.test(hostSrc), '/activity 未暴露评估结论（evaluation）')
  // client 侧渲染
  ok(/function memberNote/.test(clientSrc), 'client 缺 memberNote（成员动向文本）')
  ok(/function evaluationOf/.test(clientSrc), 'client 缺 evaluationOf（评估结论提取）')
  ok(/kexi-member-note/.test(clientSrc), 'client 缺成员动向样式 kexi-member-note')
  ok(/kexi-eval-text/.test(clientSrc) && /kexi-eval-title/.test(clientSrc), 'client 缺评估结论视图样式（kexi-eval-*）')
  ok(/评估结论/.test(clientSrc), 'client 缺「评估结论」标签页')
  // send_message 等团队编排工具的翻译
  ok(/send_message: \(\)/.test(hostSrc), 'describeTool 缺 send_message 翻译（派活节点只显示英文工具名）')

  // v1.5.1：② 入场时机 / 币种画像 / 消息面监控 三脚本接线。
  for (const s of ['entry_plan.py', 'coin_profile.py', 'news_watch.py']) {
    ok(pluginSrc.includes(`'${s}'`), `SCRIPT_WHITELIST 缺 ${s}（kexi_run 无法调用）`)
  }
  ok(/入场时机必须给/.test(pluginSrc), '指北人设缺"入场时机必须给"职责（entry_plan 四类动作）')
  ok(/币种行为画像必须给/.test(pluginSrc), '指北人设缺"币种行为画像"职责（coin_profile）')
  ok(/消息面.{0,8}占高比重|在研判中占有高比重/.test(pluginSrc), '数脉/政策缺"消息面占高比重"纪律')
  ok(/news_watch\.py --loop/.test(pluginSrc), 'policy 缺 news_watch.py --loop 定时监控指引')
  ok(/追高嫌疑/.test(pluginSrc) && /禁止/.test(pluginSrc), 'policy 缺追高嫌疑禁止立即入场纪律')

  // v1.5.2：① kexi_run 子进程 CWD 必须是会话工作区（不是脚本目录）。
  // 取证（2026-09-29 23:3x 会话）：cwd=scriptsDir + 裸脚本名 →
  // 相对 --out 全部 FileNotFoundError，news_watch 静默把产物写进插件安装目录。
  ok(!/runPython\(ctx, exec, argv, scriptsDir\)/.test(pluginSrc), 'runPython 仍以 scriptsDir 为 cwd（产物会落进插件安装目录）')
  ok(/runPython\(ctx, exec, argv, root\)/.test(pluginSrc) || /runPython\(ctx, exec, argv, workspaceRoot\(ctx, exec\)\)/.test(pluginSrc),
    'runPython 未改用会话工作区作为 cwd')
  ok(/function absolutizeArgs/.test(pluginSrc) && /function derivePathFlags/.test(pluginSrc),
    '缺相对路径参数绝对化（派活指令普遍写 kexi_out/xxx.json）')
  ok(/join\(scriptsDir, script\)/.test(pluginSrc) || /join\(SKILL_SCRIPTS, script\)/.test(pluginSrc),
    '脚本未以绝对路径调用（依赖 cwd 找脚本是旧病根）')

  // v1.5.2：② tool/call 的 arguments 是 JSON 字符串，必须解析（否则标签全丢参数）
  ok(/typeof a === 'string'/.test(hostSrc) && /JSON\.parse\(a\)/.test(hostSrc),
    'describeTool 未解析 JSON 字符串参数（工具标签会退化，如"派活给 成员"）')

  // v1.5.2：③ 等待团队期间不得显示空闲（wait_agent 可静默 10 分钟）
  ok(/MEMBER_LIVE_MS/.test(hostSrc) && /function memberSessionBusy/.test(hostSrc),
    'host 缺成员跨会话存活探测（成员在自己会话干活，主理人名册却显示待命）')
  ok(/awaitingTeam/.test(hostSrc), 'host 未把"等待成员回报"计入 busy（等待期卡片显示无响应）')

  // v1.5.2：④ 陈旧产物不得挂到本会话（上一轮看板被当本次成果）
  ok(/function artifactIsFresh/.test(hostSrc), 'host 缺产物新鲜度校验（会把上一轮看板挂到本会话）')
  ok(/createdAt: Date\.now\(\)/.test(hostSrc), '会话桶缺 createdAt（无法判定产物是否本次产出）')

  // v1.5.2：⑤ 派活指引必须写明 target 用成员代号（中文名会报 active teammate not found）
  ok(/shuma=数脉/.test(pluginSrc) || /shuma\(数脉/.test(pluginSrc),
    'kexi_ensure_team/policy 未写明 send_message 的 target 用英文代号（实测中文名直接报错）')

  // v1.5.2：⑥ 批量执行能力（消除"成员手写 Python"这一根源）
  ok(/async function runBatch/.test(pluginSrc), 'kexi_run 缺批量执行 runBatch（成员会绕开工具层手写 Python）')
  ok(/\{item\}/.test(pluginSrc), '批量参数模板缺 {item} 占位支持')
  ok(/itemsFromFile/.test(pluginSrc), '批量缺 itemsFromFile（从清单文件读标的）')
  ok(/BATCH_MAX_ITEMS/.test(pluginSrc), '批量缺数量上限（防一次刷爆交易所限流）')
  // 批量脏数据闸门：脚本本身不会因停更/样本不足失败，必须由插件兜住
  ok(/function qualityOf/.test(pluginSrc) && /停更/.test(pluginSrc) && /样本仅/.test(pluginSrc),
    '批量缺脏数据闸门（停更/样本不足会被当正常结果混进候选池）')

  // v1.5.2：⑦ 本轮产物溯源（解决跨运行来源混淆）
  ok(/function readRunMarker/.test(pluginSrc) && /function writeRunMarker/.test(pluginSrc),
    'kexi_run 缺本轮产物标记（readRunMarker/writeRunMarker）')
  ok(/mode === 'files'/.test(pluginSrc), 'kexi_run 缺 mode=files（本轮/历史产物清单）')
  ok(/本轮/.test(pluginSrc) && /历史/.test(pluginSrc), 'mode=files 未按本轮/历史分组输出')
  // 人设与政策必须真的教成员用批量、禁止手写
  ok(/禁止手写 Python/.test(pluginSrc), 'policy 缺"禁止手写 Python 跑分析"纪律')
  ok(/batch/.test(pluginSrc) && /kexi_run\(mode=files\)/.test(pluginSrc), '人设/政策未教成员用批量与 mode=files 溯源')
  ok(/Workflow L/.test(readFileSync(join(ROOT, 'preset', 'kexi-crypto', 'skills', 'kexi-team-orchestration', 'SKILL.md'), 'utf8')),
    '编排手册缺 Workflow L（多标的批量复核）')

  // v1.5.3：秒级快研（解决"结果出来币价都涨了几个点"）
  ok(/fast_analysis\.py/.test(pluginSrc), 'SCRIPT_WHITELIST 缺 fast_analysis.py')
  ok(/mode === 'fast'/.test(pluginSrc), 'kexi_run 缺 mode=fast（秒级确定性快研）')
  ok(/'fast'/.test((pluginSrc.match(/enum: \[[^\]]*\]/) || [''])[0]), 'kexi_run mode 枚举缺 fast')
  // 纪律：必须写明"先快答再深研"+ 真实耗时，否则模型仍会默认走 17 分钟团队流程
  ok(/先快答/.test(pluginSrc) || /先快答/.test(patchText), 'policy 缺"先快答、再深研"纪律（提速的关键）')
  ok(/17 ?分钟/.test(pluginSrc) || /17 ?分钟/.test(patchText),
    '未向模型说明团队流程的真实耗时（无量化依据它不会改变行为）')
  {
    const faPath = join(ROOT, 'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts', 'fast_analysis.py')
    ok(existsSync(faPath), 'fast_analysis.py 不存在')
    if (existsSync(faPath)) {
      const fa = readFileSync(faPath, 'utf8')
      // 必须复用现有模块，否则快答与团队流程口径分叉 → 出现"两个结论"的信任灾难
      ok(/import entry_plan/.test(fa) && /import coin_profile/.test(fa) && /import position/.test(fa),
        'fast_analysis 未复用现有分析模块（会与团队流程口径分叉）')
      ok(/def build_scenarios/.test(fa), 'fast_analysis 缺三情景构建（快答也必须事前可证伪）')
      ok(/indicators\.py/.test(fa), 'fast_analysis 未通过同一脚本取指标（口径一致性）')
    }
  }

  // ── v1.6.0：CEX 实盘链路的**安全性质**（这一组守的是钱）────────────────
  // 静态断言只负责"发现明显的改坏"，真正的行为验证在
  // scripts/test-cex-live-safety.py（42 项，含 fail-closed 与幂等）。
  {
    const caPath = join(ROOT, 'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts', 'cex_adapter.py')
    const ksPath = join(ROOT, 'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts', 'cex_keystore.py')
    ok(existsSync(caPath) && existsSync(ksPath), 'CEX 脚本缺失')
    if (existsSync(caPath) && existsSync(ksPath)) {
      const ca = readFileSync(caPath, 'utf8')
      const ks = readFileSync(ksPath, 'utf8')

      ok(/def read_autonomy/.test(ca), 'cex_adapter 缺 read_autonomy（自主级别是唯一实盘开关）')
      ok(/AUTONOMY_FILE\s*=\s*"autonomy\.json"/.test(ca), 'cex_adapter 未约定 autonomy.json 路径')
      ok(/def autonomy_allows/.test(ca), 'cex_adapter 缺自主级别判定函数')
      // fail-closed：三条异常路径都必须退回 readonly
      const failClosed = (ca.match(/"level":\s*"readonly"/g) || []).length
      ok(failClosed >= 3,
        `自主级别读取的异常分支不足（readonly 兜底出现 ${failClosed} 次，应覆盖 missing/invalid/error 三条）`)
      // ⚠ 关键：CLI 不得暴露任何能翻转权限的参数
      const addArg = ca.split('\n').filter((l) => l.includes('add_argument(') || l.includes('add_parser(')).join('\n')
      ok(!/--autonomy/.test(addArg), 'cex_adapter CLI 暴露了 --autonomy —— 模型可借此翻转实盘权限')
      ok(!/--dry-run|--dry_run|--live/.test(addArg), 'cex_adapter CLI 暴露了 --dry-run/--live 翻转实盘开关')
      // 幂等：应对 37% 回合失败率导致的重复下单
      ok(/def journal_append/.test(ca) && /def journal_unresolved/.test(ca),
        'cex_adapter 缺订单意图日志（37% 回合失败率下无法防重复下单）')
      ok(/def _order_id/.test(ca), 'cex_adapter 缺稳定意图 id（幂等重试的基础）')
      ok(/--idem-seq/.test(addArg), 'cex_adapter CLI 缺 --idem-seq（重试无法幂等）')
      // 受限自动档必须强制止损
      ok(/if not args\.stop_loss/.test(ca), '受限自动档未强制 stop_loss（无止损的单一律拒绝这条防线没了）')
      ok(/class BaseAdapter/.test(ca) && /def place_order_checked/.test(ca),
        'cex_adapter 缺 place_order_checked（风控闸门必须挂在下单必经之路上）')
      // 凭据桥接：两侧必须指向同一目录约定
      ok(/KEXI_CEX_STORE_DIR/.test(ks), 'cex_keystore 未支持 KEXI_CEX_STORE_DIR（与 host 面板对不上）')
      ok(/DSH_HOME/.test(ks), 'cex_keystore 未回退到 $DSH_HOME/kexi-cex（host 凭据目录）')
      ok(/label="main"/.test(ks),
        'cex_keystore 的 label 默认值必须是 "main"（与 host 的 cexDefaultLabel 一致，'
        + '不一致会静默读不到面板配好的密钥）')
    }
    // host 侧：autonomy.json 是唯一权限源，且必须由面板写
    ok(/function syncAutonomyFile/.test(hostSrc),
      'host 缺 syncAutonomyFile（不写 autonomy.json → Python 侧永远 fail-closed 到只读）')
    ok(/const CEX_AUTONOMY = \{/.test(hostSrc), 'host 缺 CEX_AUTONOMY 级别定义')
    ok(/readonly[\s\S]{0,400}confirm[\s\S]{0,400}limited[\s\S]{0,400}full/.test(hostSrc),
      'CEX_AUTONOMY 必须含 readonly/confirm/limited/full 四级')
    // 凭据落盘必须是裸二进制 DPAPI（与 Python load_keys 逐字对应）
    ok(/WriteAllBytes/.test(hostCode) && /\.dpapi`/.test(hostCode),
      'host 未按 {ex}.{label}.dpapi 裸二进制写凭据（Python 侧解不开，等于 CEX 是死的）')
    // ⚠ v1.9.1：这条原来是一刀切的 `!/ToBase64String/`，**过宽且已误报**。
    //   真正要禁的是「base64 包住**落盘文件**」——那正是 v1.5.5 死链的根因
    //   （Python 侧按裸 DPAPI 字节流读，拿到 base64 信封必然解不开）。
    //   但 base64 作为 **PowerShell→Node 的 stdout 传输编码**是必要的：
    //   PS 5.1 的 [Console]::Out 走控制台代码页（中文系统 GBK），
    //   直接吐 UTF-8 文本会把中文打坏（实测「仓位」→「仓?」）。
    //   所以改成**精确判定**：不得用 base64 写文件、不得再有 .enc.json 信封。
    //   （判定用 hostCode：注释里为解释本条而写的字面量不算证据。）
    ok(/WriteAllBytes/.test(hostCode) && !/WriteAllText[\s\S]{0,120}ToBase64String/.test(hostCode)
       && !/ToBase64String[\s\S]{0,80}WriteAllText/.test(hostCode),
      'host 在用 base64 包装落盘凭据（v1.5.5 死链根因：Python 按裸流读会解不开）')
    ok(!/\.enc\.json/.test(hostCode), 'host 仍有 {ex}.enc.json 信封（应直接写裸 {ex}.{label}.dpapi）')
    // stdout 传输通道用 base64 是**对的**，钉住它别被人当重复编码删掉
    ok(/ToBase64String/.test(hostCode) && /'base64'[\s\S]{0,40}toString\('utf8'\)/.test(hostCode),
      '解密出参应经 base64 传输（绕开 PS 5.1 控制台代码页）后由 Node 按 utf-8 解')
    // 结构化工具取代手写 argv
    ok(/mkTool\('kexi_cex'/.test(pluginSrc), '缺 kexi_cex 结构化工具（CEX 只能手写 argv 对实盘太危险）')
    ok(/action[\s\S]{0,200}all_positions/.test(pluginSrc), 'kexi_cex 缺多交易所聚合持仓动作')
    ok(/下单前\*\*必须\*\*先 action=journal/.test(pluginSrc),
      'kexi_cex 政策缺"下单前必须先查未决订单"（37% 失败率下防重复下单）')
    ok(/严禁重试、严禁找别的路绕过/.test(pluginSrc),
      'kexi_cex 政策缺"被自主级别拦下不得绕过"（否则等于教模型绕过用户的安全设置）')
  }

  notes.push('v1.2.1 新能力接线检查（活动流 / kexi_cli / 设置面板 / D9 depth / D10 response）')
  notes.push('v1.5.1 接线检查（团队动向卡片 / 入场时机 / 币种画像 / 消息面监控）')
  notes.push('v1.5.2 接线检查（脚本 CWD / 参数解析 / 等待期 busy / 产物新鲜度 / 派活 target / 批量执行 / 产物溯源）')
  notes.push('v1.5.3 接线检查（秒级快研 mode=fast / 先快答再深研纪律）')
}

// ── 7. node --check 所有 js/mjs ─────────────────────────────────────────────
function walkJs(dir, out = []) {
  if (!existsSync(dir)) return out
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.name === 'node_modules' || e.name === '__pycache__' || e.name === '.scratch') continue
    const p = join(dir, e.name)
    if (e.isDirectory()) walkJs(p, out)
    else if (/\.(mjs|js)$/.test(e.name)) out.push(p)
  }
  return out
}
for (const f of walkJs(ROOT)) {
  try { execFileSync(process.execPath, ['--check', f], { stdio: 'pipe' }) }
  catch (e) { problems.push(`node --check 失败: ${relative(ROOT, f)}\n${e.stderr}`) }
}
notes.push('node --check 全部通过')

// ── 8. 技能 frontmatter 合规 ────────────────────────────────────────────────
const skillsRoot = join(ROOT, 'preset', 'kexi-crypto', 'skills')
if (existsSync(skillsRoot)) {
  const NAME_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
  for (const d of readdirSync(skillsRoot, { withFileTypes: true })) {
    if (!d.isDirectory()) continue
    const f = join(skillsRoot, d.name, 'SKILL.md')
    ok(existsSync(f), `技能目录 ${d.name} 缺 SKILL.md`)
    if (!existsSync(f)) continue
    ok(NAME_RE.test(d.name), `技能目录名不合规(kebab-case): ${d.name}`)
    const text = readFileSync(f, 'utf8')
    ok(text.startsWith('---'), `${d.name}/SKILL.md 缺 frontmatter`)
    const fm = text.startsWith('---') ? text.split('---', 3)[1] : ''
    ok(/^name:\s*/m.test(fm), `${d.name}: frontmatter 缺 name`)
    ok(/^description:\s*\S/m.test(fm), `${d.name}: frontmatter 缺 description`)
    const nm = (fm.match(/^name:\s*(.+)$/m) || [])[1]?.trim()
    ok(nm === d.name, `${d.name}: frontmatter name(${nm}) 与目录不一致`)
    ok(!/[a-z]+[A-Z][a-zA-Z]*:/.test(fm), `${d.name}: frontmatter 含 camelCase 键（会被整条丢弃）`)
  }
  notes.push('技能 frontmatter 合规')
}

// ── 报告 ────────────────────────────────────────────────────────────────────
// 打包卫生：prepack 时清掉 __pycache__（smoke/自测/本脚本 py_compile 都会生成）。
function prunePycache(dir) {
  if (!existsSync(dir)) return
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (!e.isDirectory()) continue
    const p = join(dir, e.name)
    if (e.name === '__pycache__') { rmSync(p, { recursive: true, force: true }); notes.push('pruned ' + relative(ROOT, p)) }
    else if (e.name !== 'node_modules' && e.name !== '.git') prunePycache(p)
  }
}
prunePycache(ROOT)
console.log(notes.map((n) => '  ✓ ' + n).join('\n'))
if (problems.length) {
  console.error('\n发现问题:')
  for (const p of problems) console.error('  ✗ ' + p)
  process.exit(1)
}
console.log(`\nverify: PASS (${Object.keys(pkg).length} 顶层键 / ${patchText.split('\n').length} 行 patch)`)
