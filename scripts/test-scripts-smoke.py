#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全脚本冒烟测试（v1.9.7）——"所有脚本都能正常实现"的直接证据。

## 为什么需要它

本项目踩过太多次「文档/白名单说能做、实际跑不通」：
  · `kexi_cex` 的 action 枚举写着 open_orders，cex_adapter 根本没这子命令
  · `regime.py` / `position_doctor.py` 建好了却不在 SCRIPT_WHITELIST，模型调不到
  · `test-settings-wiring.py` 的 ⑦ 段追加失败（锚点写错），断言一次都没跑却报全绿

**光比对"声明"和"实现"是不够的——必须真的把每个脚本跑起来。**
所以这里对 scripts/ 下每个 .py 做一次最小可执行冒烟：
  · 语法能编译
  · `--help` 能退出 0（argparse 配置没坏）
  · 模块能被 import 而不抛（顶层没有语法外的崩溃）
  · 声明了必填参数时，**缺参数应报"用法错误"而不是堆栈崩溃**
    （堆栈崩溃 = 入口代码有问题，模型看到的是 traceback 而不是可操作提示）

## 边界（说清不做什么）

- **不联网**：默认用 --fixtures / 本地文件，凡需要网络的脚本本测试只验到
  "入口完好"。联网能力由各自的专项测试与实跑负责。
- **不替代专项测试**：这里只答"能不能跑起来"，不答"算得对不对"。
"""
import ast
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'preset', 'kexi-crypto', 'skills',
                       'crypto-market-analysis', 'scripts')
PY = os.environ.get('KEXI_PYTHON') or sys.executable

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


if not os.path.isdir(SCRIPTS):
    print('缺少 scripts 目录')
    sys.exit(1)

pyfiles = sorted(f for f in os.listdir(SCRIPTS) if f.endswith('.py'))
tmp = tempfile.mkdtemp(prefix='kexi-smoke-')

# 这些不是"可被 kexi_run 调用的技能脚本"，跳过（避免误报）
SKIP = {'test_all.py'}
targets = [f for f in pyfiles if f not in SKIP]
ck('发现脚本文件', len(targets) >= 30, '%d 个: %s' % (len(targets), len(targets)))

# ── ① 每个脚本：语法可编译 ────────────────────────────────────────────────
bad_syntax = []
for f in targets:
    p = os.path.join(SCRIPTS, f)
    try:
        ast.parse(open(p, encoding='utf-8').read(), filename=f)
    except SyntaxError as e:
        bad_syntax.append('%s: %s' % (f, e))
ck('① 全部脚本语法可编译', not bad_syntax, '; '.join(bad_syntax[:3]))

# ── ② 每个脚本：--help 退出 0 ────────────────────────────────────────────
bad_help = []
for f in targets:
    r = subprocess.run([PY, os.path.join(SCRIPTS, f), '--help'],
                       capture_output=True, text=True, timeout=60,
                       cwd=tmp, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    # argparse 的 -h 正常退出 0；模块没有 main() 也会正常退出 0（打印用法或什么都不做）
    if r.returncode != 0:
        bad_help.append('%s(=%d) %s' % (f, r.returncode, (r.stderr or '')[-90:].replace('\n', ' ')))
ck('② 全部脚本 --help 正常退出（argparse 没坏）', not bad_help, '; '.join(bad_help[:3]))

# ── ③ 每个脚本：缺必填参数时报**用法错误**而非堆栈崩溃 ────────────────────
# 模型调用时的典型错误是漏传参数。堆栈崩溃对模型毫无指导意义，
# 干净的 argparse 报错（usage: ... error: the following arguments are required）
# 才能让它自我纠正。
#
# ⚠ 还要单独盯「无参运行会不会挂住」：模型漏传参数时如果脚本**不是快速报错退出**
# 而是去联网/进死循环，它会一直等到超时——那 60 秒就是白白烧掉的回合预算
# （本项目 37% 回合失败率的背景下，这类浪费很贵）。
# ⚠ 网络快速失败必须与代码堆栈分开（2026-10-17 实测）：screener.py /
#   new_listing.py 的参数全有默认值、无 required，无参运行 = 按设计去 Binance
#   发现标的。断网/SSL 被中断时它们不挂起，而是在重试后快速抛出带 URL 与网络
#   原因的 RuntimeError（"... failed after N retries: <urlopen error [SSL: ...]>"）
#   ——这是诚实的网络错误转录，生产路径 kexi_run 会捕获 stderr 如实呈现。
#   只看 traceback 根异常行是否命中【标准库网络层签名】；TypeError/KeyError/
#   AttributeError/NameError 等业务代码错误仍然硬失败，绝不用"可能是网络"放水。
import re as _re  # noqa: E402

# ── 异常三分类（P1-1，体检 2026-10-30）────────────────────────────────────
# 体检实测：同一份代码 10 分钟内三次运行两红一绿，唯一变量是 Binance 那一刻
# 返回 451 还是 SSL 中断——因为 451/403 不在签名表里，被判成"代码堆栈崩溃"。
# 门禁非确定性 = 团队开始"习惯性忽略红" = 门禁失效，与"测试全绿线上失效"等价。
# 故改成三分类，且三者都有出口：
#   FAIL：业务代码异常——**永不放水**，即使串里同时出现网络字样也硬失败；
#   SKIP：受限网络签名——显式打印脚本名 + 具体原因，计入汇总，并受 ⑦⑧ 阈值约束
#         （SKIP 一旦无阈值就会退化成"新的永远绿"）；
#   INFO：无参挂起（backtest/news_watch 等）——保持原状，见下方说明。
# 业务类名只收"网络不可能产生"的那些：ValueError/OSError 会从解析与 IO 层冒出，
# 拿它们当业务铁证会把真网络失败误判成红，故不入表。
_BIZ_SIG = _re.compile(
    r'\b(?:TypeError|KeyError|AttributeError|NameError|UnboundLocalError'
    r'|ImportError|ModuleNotFoundError|SyntaxError|IndentationError|TabError'
    r'|IndexError|ZeroDivisionError|RecursionError|NotImplementedError'
    r'|AssertionError|StopIteration)\b')
# 受限网络签名：含本机两种真实形态 451（Binance）/403（Bybit），见 HANDOFF §7。
_NET_SIG = _re.compile(
    r'urlopen error|\[SSL:|Connection(?:Reset|Refused|Aborted)?Error'
    r'|Connection (?:reset|refused)|timed? ?out|Failed to establish a new connection'
    r'|getaddrinfo failed|Name or service not known|Temporary failure in name resolution'
    r'|nodename nor servname|RemoteDisconnected|Max retries exceeded'
    r'|HTTP Error (?:451|403|429|5\d\d)', _re.IGNORECASE)
# SKIP 率阈值：超过它就整步失败。取 20% 是因为 40 个脚本里只有
# new_listing/screener/wallet_watch 等少数会裸跑联网，真到 8 个以上说明
# "环境受限"已经吃掉太多覆盖面，必须人工裁决而不是继续放绿。
SKIP_RATIO_MAX = 0.20
# 同一脚本连续 N 次运行都 SKIP → 整步失败（防止长期 451/403 被当成常态）。
SKIP_STREAK_MAX = 3


def _traceback_root(out):
    """取 traceback 的根异常行（最后一行非空文本）。"""
    tail = out[out.rfind('Traceback (most recent call last)'):]
    lines = [l.strip() for l in tail.splitlines() if l.strip()]
    return lines[-1] if lines else ''


def _classify(root):
    """把根异常行分成 ('SKIP', 原因) 或 ('FAIL', 原因)。

    业务异常优先判定：网络失败被包进 TypeError/KeyError 时仍然是代码缺陷，
    不允许因为串里还带着 "timed out" 就降级成 SKIP。
    """
    if _BIZ_SIG.search(root):
        return 'FAIL', root[:100]
    if _NET_SIG.search(root):
        m = _re.search(r'HTTP Error (\d{3})', root)
        if m:
            return 'SKIP', 'HTTP %s' % m.group(1)
        # 没有 HTTP 码的网络层错误：用命中的签名片段说明是哪一类
        m = _re.search(r'\[SSL:[^\]]*\]|urlopen error|Max retries exceeded'
                       r'|getaddrinfo failed|Name or service not known'
                       r'|timed? ?out|Connection (?:reset|refused)'
                       r'|Connection(?:Reset|Refused|Aborted)?Error', root, _re.IGNORECASE)
        return 'SKIP', (m.group(0) if m else 'network').strip()
    return 'FAIL', root[:100]


stack_trace = []
skips = []          # [(脚本名, 原因)] —— 显式记录，绝不静默丢弃
hung = []
for f in targets:
    try:
        r = subprocess.run([PY, os.path.join(SCRIPTS, f)],
                           capture_output=True, text=True, timeout=25,
                           cwd=tmp, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    except subprocess.TimeoutExpired:
        hung.append(f)
        continue
    if r.returncode == 0:
        continue                      # 无必填参数，正常退出
    out = (r.stdout or '') + (r.stderr or '')
    if 'Traceback (most recent call last)' in out:
        kind, reason = _classify(_traceback_root(out))
        if kind == 'SKIP':
            skips.append((f, reason))
            print('  SKIP  受限网络 → %s（%s）' % (f, reason))
        else:
            stack_trace.append('%s → %s' % (f, reason))
ck('③ 缺参数时是干净的 argparse 报错，不是 Python 堆栈',
   not stack_trace, '堆栈崩溃: %s' % stack_trace[:4])
# ⚠ 无参挂起降级为 **INFO**（保持 v1.9.7 原判定）：
#   backtest / news_watch 等在 main() 里会先联网，裸跑进入长等待（挂起）。
#   流水线从不裸调它们（都有必填 --input/--symbol 或 kexi_run 结构化参数），
#   所以这不是正常使用路径的缺陷，只是「模型万一漏传参数」时多烧一次超时。
#   反之**快速抛出的网络层错误不再沉默**：它是 SKIP，要打印、计数、受阈值管。
if hung:
    print('  INFO  无参运行会挂起(>25s)的脚本: %s' % ', '.join(hung))
    print('        流水线从不裸调它们，但模型漏参时会多烧一次超时。')
if skips:
    print('  SKIP  受限网络（裸跑联网 + 当前网络受限，非代码堆栈）: %s'
          % ', '.join('%s → %s' % (s, r) for s, r in skips))
    print('        流水线从不裸调它们；SKIP 计入下方汇总并受 ⑦⑧ 阈值约束。')

# ── ⑦ 未验证率阈值：SKIP 与挂起都必须计数，不得退化成"永远绿" ──────────────
# 挂起（TimeoutExpired）**不属于** SKIP 豁免：它连"缺参应快速报错"都没证明，
# 所以和 SKIP 一样是"未验证"。二者合计超阈值即整步失败——环境再差也不能让
# 门禁在"大部分脚本其实没验到"的情况下继续报绿。
_total = len(targets)
_unverified = len(skips) + len(hung)
_ratio = (_unverified / _total) if _total else 0.0
ck('⑦ 未验证率（SKIP+挂起）≤ %d%%（SKIP/挂起不得退化成永远绿）'
   % int(SKIP_RATIO_MAX * 100),
   _ratio <= SKIP_RATIO_MAX,
   '未验证 %d/%d = %.1f%%（阈值 %.0f%%）: SKIP=[%s] 挂起=[%s]'
   % (_unverified, _total, _ratio * 100, SKIP_RATIO_MAX * 100,
      ', '.join('%s → %s' % (s, r) for s, r in skips) or '无',
      ', '.join(hung) or '无'))

# ── ⑧ 连续 SKIP 检测：同一脚本连续 N 次运行都 SKIP → 整步失败 ──────────────
# 计数落盘到 docs/evidence/（scripts 之外，不写用户目录）。文件损坏/不可写
# 一律按失败处理——这个保护本身不允许静默失效（规则 1）。
_STREAK_PATH = (os.environ.get('KEXI_SMOKE_STREAK_FILE')
                or os.path.join(HERE, '..', 'docs', 'evidence',
                                'smoke-skip-streak.json'))


def _load_streaks(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError('计数文件应为 {脚本名: 连续SKIP次数} 的 JSON 对象')
    return {str(k): int(v) for k, v in doc.items()}


def _save_streaks(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


try:
    _prev = _load_streaks(_STREAK_PATH)
    _skip_names = set(s for s, _ in skips)
    _cur = {}
    for _name in sorted(set(_prev) | _skip_names):
        _n = (_prev.get(_name, 0) + 1) if _name in _skip_names else 0
        if _n:
            _cur[_name] = _n
    _save_streaks(_STREAK_PATH, _cur)
    _hot = sorted(k for k, v in _cur.items() if v >= SKIP_STREAK_MAX)
    ck('⑧ 没有脚本连续 %d 次运行都 SKIP' % SKIP_STREAK_MAX, not _hot,
       '连续 SKIP 计数: %s（记录文件 %s）'
       % (', '.join('%s×%d' % (k, _cur[k]) for k in sorted(_cur)) or '无',
          _STREAK_PATH))
except Exception as _e:                                    # noqa: BLE001
    ck('⑧ 没有脚本连续 %d 次运行都 SKIP' % SKIP_STREAK_MAX, False,
       '连续 SKIP 计数不可用（禁止静默放弃该保护）: %s（%s）'
       % (_STREAK_PATH, _e))

# ── ④ 白名单内脚本必须真的可执行（不是只写在名单里）─────────────────────
plugin = open(os.path.join(HERE, '..', 'preset', 'kexi-crypto', 'kexi-plugin.mjs'),
              encoding='utf-8').read()
import re                                                   # noqa: E402
ws = re.search(r'SCRIPT_WHITELIST\s*=\s*new Set\(\[([\s\S]*?)\]\)', plugin)
wl = set(re.findall(r"'([\w.-]+\.py)'", ws.group(1))) if ws else set()
missing_file = [w for w in sorted(wl) if not os.path.exists(os.path.join(SCRIPTS, w))]
ck('④ 白名单里的脚本文件都存在（没有指向不存在的文件）',
   not missing_file, '缺失: %s' % missing_file)
not_in_wl = sorted(set(targets) - wl)
ck('④ 目录里的技能脚本都在白名单内（否则模型调不到）',
   not not_in_wl, '白名单外: %s' % not_in_wl)

# ── ⑤ 关键脚本能以离线夹具真跑出结果 ──────────────────────────────────────
def synth(n, drift, seed):
    import random
    rnd = random.Random(seed)
    px, rows = 100.0, []
    t0 = 1_700_000_000_000
    for i in range(n):
        px *= (1 + drift + rnd.gauss(0, 0.004))
        rows.append([t0 + i * 86_400_000, px, px * 1.01, px * 0.99, px, 1000.0])
    return rows


fx = os.path.join(tmp, 'fx')
os.makedirs(fx, exist_ok=True)
with open(os.path.join(fx, 'BTCUSDT.json'), 'w', encoding='utf-8') as f:
    json.dump(synth(120, 0.004, 1), f)
uni = {'S%02d' % i: synth(60, 0.005 if i % 2 else -0.005, 10 + i) for i in range(22)}
with open(os.path.join(fx, 'breadth.json'), 'w', encoding='utf-8') as f:
    json.dump(uni, f)
pos = [{"exchange": "okx", "label": "main", "market": "spot", "symbol": "BTC-SPOT",
        "side": "long", "size": 1.0, "entry_price": 100.0, "mark_price": 120.0,
        "unrealized_pnl": 20.0, "leverage": 1, "liq_price": None, "margin_mode": "spot"}]
with open(os.path.join(tmp, 'pos.json'), 'w', encoding='utf-8') as f:
    json.dump({"ok": True, "positions": pos, "count": 1}, f)
with open(os.path.join(tmp, 'reg.json'), 'w', encoding='utf-8') as f:
    json.dump({"regime": "上行期", "score": 0.6, "confidence": 0.7, "coverage": 0.7,
               "criteria": [], "invalidation": ["x"], "degraded": []}, f)

OFFLINE_RUNS = [
    ('regime.py', ['--fixtures', fx, '--out', os.path.join(tmp, 'r1.json')], 'r1.json'),
    ('position_doctor.py', ['--positions', os.path.join(tmp, 'pos.json'),
                            '--regime', os.path.join(tmp, 'reg.json'),
                            '--out', os.path.join(tmp, 'd1.json')], 'd1.json'),
]
for script, argv, outfile in OFFLINE_RUNS:
    r = subprocess.run([PY, os.path.join(SCRIPTS, script)] + argv,
                       capture_output=True, text=True, timeout=120, cwd=tmp,
                       env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    ok = r.returncode == 0 and os.path.exists(os.path.join(tmp, outfile))
    ck('⑤ %s 能离线跑出结果' % script, ok,
       (r.stderr or r.stdout)[-140:].replace('\n', ' '))
    if ok:
        try:
            doc = json.load(open(os.path.join(tmp, outfile), encoding='utf-8'))
            ck('⑤ %s 输出含 schema' % script, isinstance(doc.get('schema'), str),
               str(doc.get('schema')))
        except Exception as e:
            ck('⑤ %s 输出是合法 JSON' % script, False, str(e))

# ── ⑥ 每个脚本的 stdout 末行是紧凑 JSON（lastJsonLine 依赖它）─────────────
# 流水线靠"stdout 最后一行是 JSON"来取摘要。脚本若多打日志到末尾就会失效。
not_json_tail = []
for script, argv, _out in OFFLINE_RUNS:
    r = subprocess.run([PY, os.path.join(SCRIPTS, script)] + argv,
                       capture_output=True, text=True, timeout=120, cwd=tmp,
                       env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    lines = [l for l in (r.stdout or '').strip().splitlines() if l.strip()]
    if not lines:
        not_json_tail.append(script + '(无输出)')
        continue
    try:
        json.loads(lines[-1])
    except Exception:
        not_json_tail.append('%s 末行非 JSON: %s' % (script, lines[-1][:70]))
ck('⑥ stdout 末行是紧凑 JSON（lastJsonLine 可解析）', not not_json_tail,
   '; '.join(not_json_tail[:2]))

# ── 汇总 ──────────────────────────────────────────────────────────────────
# 汇总行必须自解释：SKIP（受限网络）与挂起（INFO，未验证）都要出现在同一行里，
# 否则"11/11 通过"会把真实覆盖面缺口藏起来（体检报告 §P1-1/§P1-6 同一教训）。
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
_summary = 'test-scripts-smoke: %d/%d 通过' % (len(checks) - len(fails), len(checks))
if skips:
    _summary += ', %d 项 SKIP（受限网络：%s）' % (
        len(skips), ', '.join('%s → %s' % (s, r) for s, r in skips))
if hung:
    _summary += ', %d 项 INFO 无参挂起（未验证：%s）' % (len(hung), ', '.join(hung))
print(_summary)
sys.exit(1 if fails else 0)
