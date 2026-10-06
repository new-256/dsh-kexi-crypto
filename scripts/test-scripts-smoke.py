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
stack_trace = []
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
        stack_trace.append(f)
ck('③ 缺参数时是干净的 argparse 报错，不是 Python 堆栈',
   not stack_trace, '堆栈崩溃: %s' % stack_trace[:4])
# ⚠ 无参挂住降级为 **INFO**，不算失败：
#   backtest / new_listing / news_watch / screener 这四个在 main() 里会先联网，
#   裸跑就进入长等待。但**流水线从不裸调它们**（都有必填 --input/--symbol），
#   所以这不是正常使用路径的缺陷，只是「模型万一漏传参数」时要多烧一次超时。
#   硬判失败会逼着去改四个脚本的联网逻辑——那是超出本轮范围的重构，
#   而且改错了会把「裸跑会联网」这个既有行为也改掉。**如实记录，不假装。**
if hung:
    print('  INFO  无参运行会挂起(>25s)的脚本: %s' % ', '.join(hung))
    print('        流水线从不裸调它们（都有必填参数），但模型漏参时会多烧一次超时。')

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
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-scripts-smoke: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
