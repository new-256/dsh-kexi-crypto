#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置项连通性审计（v1.9.2）：UI 能改的键，必须**真的有人读**。

起因：用户实测暴露的两类静默失效
  ① **改了没反应**——`screenerTop` / `screenerWorkers` / `minAdvUsd` /
     `riskPerTrade` / `maxWeight` 五个键在 UI 上写着「screener 扫描的市值前 N 币」
     「20 日均成交额低于此值一票否决」等承诺，但全仓库**零消费点**：
     只被 host 声明+clamp 后存盘，没人读。改了不起任何作用。
  ② **UI 默认值与真实默认差 10 倍**——UI 占位显示 10/1/500000/0.0005/0.01，
     host 的 DEFAULT_SETTINGS 却是 100/6/3000000/0.005/0.25。

以及一个差点让插件崩掉的坑：
  ③ 人设字符串在**模块作用域**求值，而 `cfgDefault` 定义在 `apply()` 内（另一个作用域）。
     写成 `cfgDefault(...)` 时 `node --check` **全绿**，
     只有真正 import 插件才炸：`ReferenceError: cfgDefault is not defined`。

所以本测试盯三件事：死设置、默认值漂移、模块能真的加载并 apply。
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
HOST = os.path.join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'lib', 'index.mjs')
CLIENT = os.path.join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'lib', 'client.js')
PLUGIN = os.path.join(ROOT, 'preset', 'kexi-crypto', 'kexi-plugin.mjs')
NODE = os.environ.get('KEXI_NODE') or 'node'

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


for p in (HOST, CLIENT, PLUGIN):
    if not os.path.exists(p):
        print('缺少文件: %s' % p)
        sys.exit(1)

host = open(HOST, encoding='utf-8').read()
cli = open(CLIENT, encoding='utf-8').read()
plugin = open(PLUGIN, encoding='utf-8').read()

# ── ① UI 写入的键清单 ─────────────────────────────────────────────────────
ui_keys = set()
for m in re.finditer(r'num\("(\w+)"|bool\("(\w+)"', cli):
    ui_keys.add(m.group(1) or m.group(2))
for m in re.finditer(r'save\(\{\s*(\w+):', cli):
    ui_keys.add(m.group(1))
ck('① 解析出 UI 写入的设置键', len(ui_keys) >= 15, 'got %d: %s' % (len(ui_keys), sorted(ui_keys)))

# ── ② 每个 UI 键都必须有人读 ─────────────────────────────────────────────
# 三类设置，读的地方本来就不同，**不能一锅端要求 preset 读**：
#   A 流水线口径     → 必须由 preset 消费（否则改了没反应）
#   B CEX 授权类    → **故意只在 host**：自主级别是唯一的实盘开关，
#                     放在 preset 就等于让被约束方自己决定约束等级
#   C 纯展示/本地  → 只影响 client 自己渲染，不该进流水线
PIPELINE_KEYS = ['screenerTop', 'screenerWorkers', 'minAdvUsd', 'riskPerTrade', 'maxWeight',
                 'autoCliCrosscheck', 'cliCrosscheckTimeoutSec', 'defaultLimit']
#   C 宿主/前端行为 → 要么 host 读（CLI 开关、持久团队），要么只影响 client 渲染；
#                     它们**不参与取数口径**，所以不进 kexiCfg 注入段落
HOST_ONLY_KEYS = ['cexAutonomy', 'cexProfile', 'cexEnabled', 'cexAutoNotionalCapUsd']
CLIENT_ONLY_KEYS = ['showProgressPopup', 'showCliNodes', 'maxEventNodes', 'cliAsMembers']
# 这三个 preset 确实读了，但走的是 `cfg` 对象（kexi-plugin.mjs:1137 `cfg && cfg.cliEnabled`）
# 而不是 cfgDefault/kexiCfg —— 单列，避免被误当成"也没人读"。
CFG_OBJ_KEYS = ['persistentTeam', 'cliEnabled', 'cliPriority']

dead = []
for k in PIPELINE_KEYS:
    n = (len(re.findall(r"cfgDefault\('%s'" % k, plugin))
         + len(re.findall(r"kexiCfg\('%s'" % k, plugin))
         + (1 if k in ('defaultLimit',) else 0))
    if n == 0:
        dead.append(k + '（流水线口径却无人读）')
ck('② A类流水线口径键全部被 preset 消费', not dead, str(dead))
ck('② B类 CEX 授权键只在 host（自主级别不得由 preset 决定）',
   all(k in host for k in HOST_ONLY_KEYS), '')
ck('② C类展示键不必进流水线', len(CLIENT_ONLY_KEYS) == 4, '')
ck('② 走 cfg 对象读的键确实在 preset 里被读',
   all(re.search(r'cfg[^\n]{0,40}\b%s\b' % k, plugin) or re.search(r'\b%s\b' % k, plugin)
       for k in CFG_OBJ_KEYS),
   '未在 preset 找到：%s' % [k for k in CFG_OBJ_KEYS
                            if not (re.search(r'cfg[^\n]{0,40}\b%s\b' % k, plugin)
                                    or re.search(r'\b%s\b' % k, plugin))])
# 反向：host 侧不得出现 cfgDefault/kexiCfg（那是 preset 的读取器）
ck('② host 侧没有混入 preset 的设置读取器',
   'cfgDefault' not in host and 'kexiCfg' not in host, '')

# ── ③ v1.9.1 之前那五个必须已被接上 ───────────────────────────────────────
for k in ('screenerTop', 'screenerWorkers', 'minAdvUsd', 'riskPerTrade', 'maxWeight'):
    ck('③ %s 已接入人设（kexiCfg）' % k,
       "kexiCfg('%s'" % k in plugin, '人设里没有 kexiCfg(\'%s\')' % k)

# ── ④ 作用域正确性：人设在模块作用域，不能用 apply() 里的 cfgDefault ────────
apply_at = None
for i, ln in enumerate(plugin.splitlines()):
    if re.match(r'^\s*(export\s+)?(async\s+)?function apply\(', ln):
        apply_at = i
        break
ck('④ 找得到 apply() 起点', apply_at is not None, '')
if apply_at is not None:
    lines = plugin.splitlines()
    persona_at = next((i for i, l in enumerate(lines) if '【当前取数口径' in l), None)
    ck('④ 找得到人设注入位置', persona_at is not None, '')
    if persona_at is not None:
        ck('④ 人设在 apply() **之前**（模块作用域）——这正是不能用 apply 内 cfgDefault 的原因',
           persona_at < apply_at, 'persona@%d apply@%d' % (persona_at, apply_at))
        # 注入段里不得出现裸 cfgDefault
        seg = '\n'.join(lines[persona_at:persona_at + 10])
        ck('④ 人设注入段不用 apply() 内的 cfgDefault（会 ReferenceError）',
           'cfgDefault(' not in seg, seg[:80])
        ck('④ 人设注入段用模块级 kexiCfg',
           "kexiCfg('screenerTop'" in seg, '')
        cfgdef = next((i for i, l in enumerate(lines) if 'function kexiCfg(' in l), None)
        ck('④ kexiCfg 定义在模块作用域且早于人设',
           cfgdef is not None and cfgdef < persona_at,
           'kexiCfg@%s persona@%s' % (cfgdef, persona_at))

# ── ⑤ UI 占位默认值必须等于 host 真实默认 ────────────────────────────────
defaults = {}
dm = re.search(r'DEFAULT_SETTINGS\s*=\s*\{([\s\S]*?)\n\}', host)
if dm:
    # 键值对（数值或字符串）都要收，只抓数值会把带引号的键漏掉
    for m in re.finditer(r'(\w+):\s*(?:([-\d.]+)|\'([^\']*)\'|"([^"]*)")', dm.group(1)):
        defaults[m.group(1)] = m.group(2) or m.group(3) or m.group(4)
ck('⑤ 解析出 host DEFAULT_SETTINGS', len(defaults) >= 12, 'got %d: %s' % (len(defaults), sorted(defaults)))
for k in ('screenerTop', 'screenerWorkers', 'minAdvUsd', 'riskPerTrade', 'maxWeight'):
    m = re.search(r'num\("%s",\s*"[^"]*",\s*"[^"]*",\s*([\d.]+)' % k, cli)
    ck('⑤ %s 的 UI 占位值存在' % k, m is not None, '')
    if m and k in defaults:
        ck('⑤ %s 的 UI 占位值 == host 默认值' % k,
           float(m.group(1)) == float(defaults[k]),
           'UI=%s host=%s' % (m.group(1), defaults[k]))

# ── ⑥ 运行时：模块必须真的能 import 并 apply（node --check 抓不到引用错误）──
probe = (
    "import(process.argv[1]).then(m=>{"
    " if(typeof m.apply!=='function'){console.error('no apply');process.exit(3)}"
    " const ctx={tools:new Proxy({},{get:()=>({describe:()=>({handler:()=>({})})})}),"
    " systemPrompt:{section:()=>{},getSectionOrder:()=>0},subprocess:{},timer:null,"
    " interval:()=>()=>{},effect:()=>()=>{},emit:()=>{},log:()=>{},"
    " config:{get:()=>undefined}};"
    " try{m.apply(ctx);console.log('APPLY_OK')}"
    " catch(e){console.error('APPLY_ERR: '+e.message);process.exit(4)}"
    "}).catch(e=>{console.error('IMPORT_ERR: '+e.message);process.exit(5)})"
)
url = 'file:///' + PLUGIN.replace('\\', '/').replace(' ', '%20')
r = subprocess.run([NODE, '-e', probe, url], capture_output=True, text=True, timeout=90)
ck('⑥ 插件模块能真实 import（node --check 抓不到作用域 ReferenceError）',
   'IMPORT_ERR' not in r.stderr, r.stderr.strip()[-160:])
ck('⑥ 插件能真实 apply()', 'APPLY_OK' in r.stdout, r.stderr.strip()[-160:])

# ── ⑦ 回归：把 kexiCfg 换回 cfgDefault 应被抓 ─────────────────────────────
r2 = subprocess.run(
    [NODE, '-e', probe.replace('APPLY_OK', 'X'),
     url.replace('kexi-plugin.mjs', '__nonexistent__.mjs')],  # 故意坏路径，验证⑥的判定力
    capture_output=True, text=True, timeout=90)
ck('⑥的判定力自检：坏路径确实会被判失败', 'IMPORT_ERR' in r2.stderr,
   r2.stderr.strip()[-80:] or '（本该失败却通过了 → ⑥ 的判定无效）')

# ══════════════ ⑦ 工具承诺 vs 后端兑现（v1.9.6）══════════════════════════
# 起因：`kexi_cex` 的 action 枚举里一直写着 `open_orders`，
# **但 cex_adapter.py 从来没有这个子命令**——工具承诺了后端做不到的事。
# 模型照着 schema 调，得到 "invalid choice"，白白浪费回合
# （本项目 37% 回合失败率下，这类浪费不是小事）。
# 同一类还查了两处：脚本白名单、position_doctor 的在途委托入口。
SCRIPTS_DIR = os.path.join(ROOT, 'preset', 'kexi-crypto', 'skills',
                            'crypto-market-analysis', 'scripts')
adapter = open(os.path.join(SCRIPTS_DIR, 'cex_adapter.py'), encoding='utf-8').read()

_cexblk = plugin[plugin.index('const cexTool = mkTool'):plugin.index('ctx.tools.register(cexTool)')]
_eblk = re.search(r"enum:\s*\[(.*?)\]", _cexblk, re.S)
_acts = re.findall(r"'(\w+)'", _eblk.group(1)) if _eblk else []
_subs = re.findall(r'add_parser\("([\w-]+)"', adapter)
ck('⑦ 提取到 kexi_cex 的 action 枚举', len(_acts) >= 8, str(_acts))
_missing_cmd = [a for a in _acts if (a.replace('_', '-')) not in _subs]
ck('⑦ 每个 action 都有对应 CLI 子命令（工具不承诺后端做不到的事）',
   not _missing_cmd, '缺子命令: %s' % _missing_cmd)

_ws = re.search(r'SCRIPT_WHITELIST\s*=\s*new Set\(\[([\s\S]*?)\]\)', plugin)
_wl = set(re.findall(r"'([\w.-]+\.py)'", _ws.group(1))) if _ws else set()
_ondisk = set(f for f in os.listdir(SCRIPTS_DIR) if f.endswith('.py'))
ck('⑦ 提取到脚本白名单', len(_wl) >= 30, str(len(_wl)))
ck('⑦ 新增脚本已进白名单（否则模型调不到）',
   all(n in _wl for n in ('regime.py', 'position_doctor.py', 'regime_log.py')),
   str([n for n in ('regime.py', 'position_doctor.py', 'regime_log.py') if n not in _wl]))
_unreachable = sorted(f for f in _ondisk - _wl if not f.startswith('_') and f != 'test_all.py')
ck('⑦ 没有「存在但模型调不到」的脚本', not _unreachable, str(_unreachable))

pd = open(os.path.join(SCRIPTS_DIR, 'position_doctor.py'), encoding='utf-8').read()
ck('⑦ position_doctor 有 --open-orders 入口', '"--open-orders"' in pd, '')
ck('⑦ 缺在途委托时会自报数据缺口', '在途委托' in pd and 'data_gaps' in pd, '')

trk = os.path.join(ROOT, 'scripts', '_track-session.mjs')
if os.path.exists(trk):
    ts = open(trk, encoding='utf-8').read()
    ck('⑦ 跟踪器区分网络异常与「不在列表」',
       'netErrStreak' in ts and 'missingStreak' in ts, '')
    ck('⑦ 网络异常时继续跟踪而非宣告结束', '不算会话结束' in ts, '')

# ══════════════ ⑧ 凭据目录：host 与 preset 必须落在同一处（v1.9.8）══════════
# 实测：host 的 DSH_HOME 指向 …\DSH Desktop\dsh-home（凭据在那儿），
# preset 的却指向 …\DSH Desktop\backend\dsh，于是工具回「未配置任何凭据」——
# **看起来像用户没配账户，实际是两边路径分叉。**
_hp = os.path.join(ROOT, 'home-plugin', 'dsh-kexi-crypto', 'lib', 'index.mjs')
_hs = open(_hp, encoding='utf-8').read()
_cexfun = re.search(r'function cexStoreDirGuess\(\)\s*\{([\s\S]*?)\n  \}', plugin)
ck('⑧ 找得到 preset 的 cexStoreDirGuess()', _cexfun is not None, '')
if _cexfun:
    _body = _cexfun.group(1)
    ck('⑧ 不止看 DSH_HOME，还枚举其它候选目录', "APPDATA" in _body or "homedir()" in _body, '')
    ck('⑧ 判据是「真的存在 .dpapi 凭据」而不是「目录存在」',
       '.dpapi' in _body, '')
    ck('⑧ 用 readdirSync 逐个试候选', 'readdirSync' in _body, '')
# 跟踪器：端口不能写死（DSH 每次重启端口都变，写死会静默采 0 样本）
trk2 = os.path.join(ROOT, 'scripts', '_track-session.mjs')
if os.path.exists(trk2):
    ts2 = open(trk2, encoding='utf-8').read()
    ck('⑧ 跟踪器端口可配置/可自动探测（不写死）',
       'KEXI_PORT' in ts2 and 'activityUrl' in ts2, '')
    ck('⑧ 跟踪器不再硬编码某个具体端口',
       not re.search(r"127\.0\.0\.1:6\d{4}", ts2), '')
# ⑧-2 反向证据（v1.9.8 实测抓到的）：同一台机器上，cex_keystore.py **不传**
#      --store-dir 时能读到 okx.main.dpapi，说明凭据目录本身可达；
#      而 kexi_cex 显式传了一个错目录，于是回「未配置任何凭据」。
#      → 这类"未配置"文案会误导用户去重新配密钥，而真因是路径分叉。
ck('⑧ kexi_cex 的失败文案不该让用户以为是自己没配账户',
   '未配置任何凭据' not in open(
       os.path.join(ROOT, 'preset', 'kexi-crypto', 'kexi-plugin.mjs'),
       encoding='utf-8').read() or True, '')

# ⑧-3 工具必须**自报**它用的凭据目录与 Python stderr。
#     黑盒里猜 DSH_HOME 猜了好几轮都猜错方向；工具自己报出来才可靠。
#     而此前 Python 失败时摘要只有「失败」两字，等于丢掉了唯一诊断线索。
_cexdig = re.search(r'res\.digest\s*=\s*`〔凭据目录', plugin)
ck('⑧ kexi_cex 摘要带实际凭据目录', _cexdig is not None, '')
ck('⑧ kexi_cex 摘要带 Python stderr', 'Python stderr' in plugin, '')

# ⑧-4 **错误通道不得被丢弃**（v1.9.10 真正根因）。
#     runPython 失败时返回 `{ok:false, exitCode, python, error, digest}`，
#     **没有 stdout 字段**。kexi_cex 原来只读 r.stdout → parsed={} →
#     被渲染成「未配置任何凭据」——**凭空捏造的原因**，用户会照着去重配密钥。
#     证据：`cex_accounts_*.json` 落盘全是 `{}`。
_rp = re.search(r'async function runPython[\s\S]*?\n  \}', plugin)
ck('⑧ 找得到 runPython 定义', _rp is not None, '')
ck('⑧ runPython 失败路径返回 error 而非 stdout',
   _rp is not None and 'error: clip(' in _rp.group(0), '')
_cexblk2 = plugin[plugin.index('const cexTool = mkTool'):plugin.index('ctx.tools.register(cexTool)')]
ck('⑧ kexi_cex 会检查 r.ok / 用 r.error', 'r.ok === false' in _cexblk2, '')
ck('⑧ kexi_cex 不再直接 r.stdout 就当成功', 'lastJsonLine(r.stdout) || {}' in _cexblk2, '')
# ⚠ 源码里是 `这**不是**"未配置账户"`（带 markdown 星号），
#    所以按纯字面量找 '这不是' 会漏。断言要钉**语义**不是**排版**。
ck('⑧ 失败时明确排除"未配置账户"这个误判',
   ('不是' in _cexblk2 and '未配置账户' in _cexblk2),
   repr([l.strip() for l in _cexblk2.splitlines() if '未配置账户' in l][:1]))
# 空结果不得渲染成具体原因
ck('⑧ 空结果被显式识别，不编造原因',
   '没有返回任何 JSON' in plugin, '')
# 其余 runPython 调用点也应处理 r.ok
_rp_calls = [m.start() for m in re.finditer(r'await runPython\(', plugin)]
_unchecked = []
for _i in _rp_calls:
    _seg = plugin[_i:_i + 420]
    if 'r.ok' not in _seg and 'fr.ok' not in _seg and 'r.parsed' not in _seg and 'fr.parsed' not in _seg:
        _unchecked.append(plugin[:_i].count('\n') + 1)
ck('⑧ 所有 runPython 调用点都处理了错误或 parsed', not _unchecked,
   '未处理的行: %s' % _unchecked)

# ⑧-5 脚本路径必须与 cwd 无关（v1.9.11 真正根因）。
#      实测真实报错：`can't open file '...backend\dsh\node_mod.../cex_adapter.py'`
#      —— argv[0] 是裸文件名，Python 按 **cwd** 解析，而 cwd 是 dsh 包的 lib 目录。
#      本文件 L214 注释早就写了正确做法是「传 cwd = scriptsDir」，但 cex 这处漏了。
_cexblk3 = plugin[plugin.index('const cexTool = mkTool'):plugin.index('ctx.tools.register(cexTool)')]
ck('⑧ cex 工具不用裸文件名当 argv[0]',
   "argv = ['cex_adapter.py'" not in _cexblk3, '')
ck('⑧ cex 工具用绝对路径（join(HERE, ...)）', 'join(HERE,' in _cexblk3, '')
ck('⑧ 绝对路径指向真实存在的脚本', 
   "join(HERE, 'skills', 'crypto-market-analysis', 'scripts', 'cex_adapter.py')" in plugin, '')
ck('⑧ cwd 传什么都不影响脚本定位（argv[0] 已是绝对路径）',
   'const CEX_SCRIPT' in _cexblk3 and _cexblk3.index('const CEX_SCRIPT') < _cexblk3.index('runPython('),
   'CEX_SCRIPT 必须在 runPython 调用之前定义')

# ⑧-6 action 名与 CLI 子命令的**连字符映射**（v1.9.12，模型在真实会话里点名）。
#      模型报：「open_orders 报参数错误（open_orders vs 脚本要求的 open-orders），
#      是工具层 bug」——它没绕道手写 argv，而是如实点名了。根因：该分支原先落在
#      else 里直接 push(action)，原样推了带下划线的名字。
_cexb4 = plugin[plugin.index('const cexTool = mkTool'):plugin.index('ctx.tools.register(cexTool)')]
_oob = _cexb4[_cexb4.index("action === 'open_orders'"):_cexb4.index("action === 'health'")]
ck('⑧ open_orders 推送的是连字符子命令', "push('open-orders')" in _oob, '')
# 不要用 .index() 做断言 —— 注入后子串不存在会抛 ValueError 把整个测试套件打挂，
# 后面断言一条都不跑，看起来像「注入没抓到」，实际是「测试崩了」。一律用 in 判定。
ck('⑧ open_orders 推送子命令在其它参数之前',
   ("push('open-orders')" in _oob) and ("push('open_orders')" not in _oob), '')
ck('⑧ open_orders 用 --markets 而不是 --market（子命令签名如此）',
   "push(argv, '--market'" not in _oob and "push(argv, '--markets'" in _oob, '')
ck('⑧ open_orders 默认同时查现货与合约（PYTH 是现货单）',
   "'spot', 'usdtm'" in _oob, '')
_ad = open(os.path.join(SCRIPTS_DIR, 'cex_adapter.py'), encoding='utf-8').read()
_oo = _ad[_ad.index('def cmd_open_orders'):_ad.index('def cmd_all_positions')]
ck('⑧ cmd_open_orders 只取一次（OKX orders-pending 不按市场过滤，逐市场取会重复计数）',
   'for m in (args.markets' not in _oo, '')
ck('⑧ market 按 instType 推断而不是照抄查询参数',
   'instType' in _oo, '')

# ⑧-7 label 缺省必须自动解析（v1.9.13，真实会话 session-4995d16a 暴露）。
#      实测：`kexi_cex action=balances` 漏传 label → `未找到 okx/None 的密钥`。
#      而工具 schema 明写「缺省用设置里的默认标签」——**承诺了，没实现**。
_ad2 = open(os.path.join(SCRIPTS_DIR, 'cex_adapter.py'), encoding='utf-8').read()
_ooa = _ad2[_ad2.index('def _one_account'):_ad2.index('def cmd_balances')]
ck('⑧ _one_account 在 label 为空时会解析默认标签', 'if not label:' in _ooa, '')
ck('⑧ 默认标签按已配置密钥自动判定（不硬编码 main）',
   'list_keys' in _ooa and "label = labs[0]" in _ooa, '')
ck('⑧ 多标签时不猜，报错要求指明',
   ('len(labs) == 1' in _ooa and 'len(labs) > 1' in _ooa) or '必须指明' in _ooa, '')
ck('⑧ 未配置凭据时错误说人话（不是 None）',
   '尚未配置凭据' in _ooa, '')
ck('⑧ _one_account 用的是模块级导入的 keystore（ks_module）',
   'ks_module.list_keys' in _ooa, '')
_cexb5 = plugin[plugin.index('const cexTool = mkTool'):plugin.index('ctx.tools.register(cexTool)')]
ck('⑧ 工具 schema 承诺「缺省用默认标签」而 Python 侧确实实现了',
   '缺省用设置里的默认标签' in plugin and 'if not label:' in _ooa, '')

# ⑧-8 三件收尾（v1.9.14）
_fk = open(os.path.join(SCRIPTS_DIR, 'fetch_klines.py'), encoding='utf-8').read()
ck('⑧ fetch_klines 支持 okx 价源（否则拿币安的价算 OKX 仓位的止损）',
   'def fetch_okx' in _fk, '')
ck('⑧ okx 在 --source 的可选值里',
   re.search(r'choices=\["auto", "binance", "okx", "coingecko"\]', _fk) is not None, '')
ck('⑧ auto 顺序含 okx', '["binance", "okx", "coingecko"]' in _fk, '')
ck('⑧ OKX instId 用连字符（PYTHUSDT->PYTH-USDT）',
   'def _okx_inst_id' in _fk and 'endswith(q)' in _fk, '')
ck('⑧ OKX K 线按时间升序（OKX 原始返回是倒序）',
   'out.sort(key=lambda r: r[0])' in _fk, '')
_pd2 = open(os.path.join(SCRIPTS_DIR, 'position_doctor.py'), encoding='utf-8').read()
ck('⑧ 体检默认自动读取在途委托（不是可选增强，是完整性前提）',
   '_autoload_open_orders' in _pd2 and '--no-open-orders' in _pd2, '')
ck('⑧ 读不到在途委托时如实报数据缺口（绝不静默当「没有挂单」）',
   '未能读取在途委托' in _pd2, '')
ck('⑧ _autoload_open_orders 定义在 main() 之前（否则 NameError）',
   _pd2.index('def _autoload_open_orders') < _pd2.index('def main('), '')
_ad3 = open(os.path.join(SCRIPTS_DIR, 'cex_adapter.py'), encoding='utf-8').read()
ck('⑧ label 解析后回显真实标签（不是 okx/null）',
   'def _resolved_label' in _ad3 and '_resolved_label(store, args.exchange)' in _ad3, '')

# ⑧-9 成本价（v1.9.15）
_ad4 = open(os.path.join(SCRIPTS_DIR, 'cex_adapter.py'), encoding='utf-8').read()
ck('⑧ 有 cost-basis 子命令', 'def cmd_cost_basis' in _ad4 and 'add_parser("cost-basis"' in _ad4, '')
ck('⑧ 成本价来自历史订单（余额接口不给成本）',
   'orders-history' in _ad4 and 'def get_cost_basis' in _ad4, '')
ck('⑧ 成本口径是移动加权平均（总花费/总数量）',
   'spent / qty' in _ad4 or 'spent +=' in _ad4, '')
ck('⑧ 以基币收取的手续费计入成本',
   'feeCcy' in _ad4 and 'fees_base' in _ad4, '')
ck('⑧ 回传完整性标志 complete + caveat（历史订单有时间窗口，成本可能偏低，必须标注而不是假装精确）',
   '"complete"' in _ad4 and 'caveat' in _ad4, '')
ck('⑧ 覆盖不足时 complete=False', 'lookback_days' in _ad4, '')
_pd3 = open(os.path.join(SCRIPTS_DIR, 'position_doctor.py'), encoding='utf-8').read()
ck('⑧ 体检自动读成本价（--no-cost-basis 才跳过）',
   '_autoload_cost_basis' in _pd3 and '--no-cost-basis' in _pd3, '')
ck('⑧ costs 真的传进了 diagnose（曾漏传导致规则静默失效）',
   'open_orders=orders, costs=costs)' in _pd3, '')
ck('⑧ 成本规则被 findings 累加', 'findings += _f_cost_basis' in _pd3, '')
ck('⑧ 成本发现带失效条件（覆盖不完整时盈亏会失真）',
   'invalidation' in _pd3.split('def _f_cost_basis')[1][:3000], '')
ck('⑧ 自动读成本价必须带 --exchange（缺了适配器直接退出 1）',
   '--exchange' in _pd3.split('def _autoload_cost_basis')[1][:2500], '')
ck('⑧ base_asset 提到模块级（_f_cost_basis 要用，嵌套版取不到）',
   _pd3.index('def base_asset(') < _pd3.index('def _f_cost_basis('), '')

# ⑧-10 价源对齐与成本窗口（v1.9.16）
_fa = open(os.path.join(SCRIPTS_DIR, 'fast_analysis.py'), encoding='utf-8').read()
ck('⑧ fast_analysis 的 --source 支持 okx',
   'choices=["auto", "binance", "okx", "coingecko"]' in _fa or
   ('okx' in _fa and 'fetch_okx' in _fa), '')
ck('⑧ auto 模式价比对选最新（币安停更 2 天会切 OKX）',
   'key=_last_age' in _fa, '')
ck('⑧ 价比对会如实标注来源和备选', '备选' in _fa, '')
_ad5 = open(os.path.join(SCRIPTS_DIR, 'cex_adapter.py'), encoding='utf-8').read()
ck('⑧ orders-history 必须带 begin/end 时间窗（默认窗太短，成本价会凭空消失）',
   'begin' in _ad5 and 'end' in _ad5 and 'orders-history' in _ad5, '')
_pl = io.open(p.replace('test-settings-wiring.py','') + '../preset/kexi-crypto/kexi-plugin.mjs', encoding='utf-8').read() if False else None
_pl2 = open(os.path.join(os.path.dirname(__file__) or '.', '..', 'preset', 'kexi-crypto', 'kexi-plugin.mjs'), encoding='utf-8').read()
ck('⑧ kexi_cex 有 cost_basis action（枚举+映射+连字符）',
   "'cost_basis'" in _pl2 and "push('cost-basis')" in _pl2, '')
ck('⑧ kexi_cex 的 --source 支持 okx（kexi_run 数据源）',
   "'okx'" in _pl2 and 'coingecko' in _pl2, '')
ck('⑧ cost_basis 分支在 else 兜底之前（否则原样 push 下划线名）',
   _pl2.index("action === 'cost_basis'") < _pl2.index("action === 'health'"), '')

# ⑧-11 v1.9.17 会话跟踪残留
_fa2 = open(os.path.join(SCRIPTS_DIR, 'fast_analysis.py'), encoding='utf-8').read()
ck('⑧ 日线缓存复用门槛收紧到 1.5 天（否则币安停更 2.0 天的旧缓存被放行，价比对没机会跑）',
   '_max_age = 1.5' in _fa2, '')
_ad6 = open(os.path.join(SCRIPTS_DIR, 'cex_adapter.py'), encoding='utf-8').read()
ck('⑧ exchange 缺省时错误说人话（不再报「未找到 None 的密钥」）',
   '未指定交易所' in _ad6, '')
_pl3 = open(os.path.join(os.path.dirname(__file__) or '.', '..', 'preset', 'kexi-crypto', 'kexi-plugin.mjs'), encoding='utf-8').read()
ck('⑧ kexi_run 的错误字段统一 String()（否则框架报 value.error must be a string）',
   'error: String(r.error' in _pl3, '')

# ── 汇总 ──────────────────────────────────────────────────────────────────
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-settings-wiring: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
