#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-1 负对照（体检 2026-10-30 §P1-1 / 规则 1「禁降级兜底」）。

## 这个文件回答什么问题

体检结论是「`test-scripts-smoke.py` 的 451/403 门禁随机红绿，因为签名表漏了 451」。
要证明修复有效，光"新版本跑一次是绿的"不够——那是正对照。必须证明：

  · 负对照 1：改造前的脚本 + 真实 451 异常串 → 仍然 FAIL（复现体检的红）
  · 负对照 2：改造后的脚本 + 真实 451 异常串 → SKIP（显式打印 + 计入汇总），不 FAIL
  · 负对照 3：**业务异常混在网络串里**（`TypeError: HTTP Error 451 …`）→ 改造后
    仍必须 FAIL（证明 451 豁免没有把真缺陷一起放水）
  · 负对照 4：SKIP 率超阈值 → 改造后整步 FAIL（证明 SKIP 不会退化成永远绿）
  · 负对照 5：同一脚本连续 3 次 SKIP → 改造后整步 FAIL
  · 负对照 6：连续计数文件损坏 → 改造后整步 FAIL（保护不允许静默失效）

## 怎么做到"真跑"而不是对答案

构造**隔离影子仓库**（临时目录，结构与仓库同形）：`scripts/` 放被测脚本本体，
`preset/kexi-crypto/skills/crypto-market-analysis/scripts/` 放极小的被测样本，
`preset/kexi-crypto/kexi-plugin.mjs` 放白名单。被测脚本用绝对路径推导 SCRIPTS，
形状对了就会原样跑完 ①~⑧，**不修改任何源码、不注入桩、不改常量**。

改造前版本取自 `git show HEAD:scripts/test-scripts-smoke.py`（仓库内真内容），
不是凭记忆重写的近似物；改造后版本取工作区当前文件。样本脚本的异常串取自
体检报告记录的**真实运行时异常原文**（`RuntimeError: HTTP Error 451: ` 等）。

用法：C:\\Python313\\python.exe docs/evidence/p1-1-negative-control.py
退出码：0 = 全部负对照符合预期；1 = 有负对照不符合预期（说明修复失效）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.environ.get('KEXI_PYTHON') or sys.executable
SMOKE_REL = os.path.join('scripts', 'test-scripts-smoke.py')
RUN_TIMEOUT = 180          # 每个影子冒烟进程的硬超时（规则 12，禁止无限挂起）

# ── 影子样本：每个样本都是"能从任何 cwd 独立跑起来的真脚本"────────────────
_STUB_HEAD = '''\
import argparse
import json
import sys


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='out.json')
    p.add_argument('--fixtures')
    p.add_argument('--positions')
    p.add_argument('--regime')
    return p.parse_args(argv)
'''

STUB_OK = _STUB_HEAD + '''

if __name__ == '__main__':
    main(sys.argv[1:])
'''

STUB_OUTPUT = _STUB_HEAD + '''

if __name__ == '__main__':
    _a = main(sys.argv[1:])
    _doc = {'schema': 'kexi.fixture/1'}
    with open(_a.out, 'w', encoding='utf-8') as _f:
        json.dump(_doc, _f)
    print(json.dumps(_doc, separators=(',', ':')))
'''

# 真实运行时异常原文（体检报告 §P1-1 记录：new_listing.py / screener.py 的根异常）
STUB_NET451 = _STUB_HEAD + '''

if __name__ == '__main__':
    main(sys.argv[1:])
    raise RuntimeError('HTTP Error 451: ')
'''

STUB_NET403 = _STUB_HEAD + '''

if __name__ == '__main__':
    main(sys.argv[1:])
    raise RuntimeError('/api/v3/ticker/24hr failed after 2 retries: HTTP Error 403: Forbidden')
'''

STUB_SSL = _STUB_HEAD + '''

if __name__ == '__main__':
    main(sys.argv[1:])
    raise RuntimeError('<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred>')
'''

# 真业务缺陷：与网络无关，必须永远 FAIL
STUB_TYPEERROR = _STUB_HEAD + '''

if __name__ == '__main__':
    main(sys.argv[1:])
    raise TypeError("unsupported operand type(s) for +: 'NoneType' and 'int'")
'''

STUB_KEYERROR = _STUB_HEAD + '''

if __name__ == '__main__':
    main(sys.argv[1:])
    raise KeyError('data')
'''

# 最重要的一条：业务异常**串里带着**网络字样。若签名表"见 451 就放水"，
# 这里就会从 FAIL 偷渡成 SKIP —— 那才是真正的降级兜底。
STUB_TYPEERROR_WITH_NET_NOISE = _STUB_HEAD + '''

if __name__ == '__main__':
    main(sys.argv[1:])
    raise TypeError('HTTP Error 451: 接口返回体缺少 data 字段，不能直接下标取值')
'''

FAKE_PLUGIN = '''// 影子白名单（仅为让 test-scripts-smoke.py 的 ④ 段有真源可查）
export const SCRIPT_WHITELIST = new Set([
%s
])
'''


def _git_show_old_smoke():
    """改造前版本：直接取 HEAD 里的真实内容（不是重写近似物）。"""
    r = subprocess.run(['git', 'show', 'HEAD:' + SMOKE_REL.replace('\\', '/')],
                       cwd=REPO, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise SystemExit('无法从 HEAD 取改造前版本: %s' % (r.stderr or '')[-300:])
    return r.stdout


def build_shadow(root, smoke_source, samples):
    """按仓库同形结构铺一个隔离影子树；返回被测脚本绝对路径。"""
    scripts_dir = os.path.join(root, 'preset', 'kexi-crypto', 'skills',
                               'crypto-market-analysis', 'scripts')
    os.makedirs(scripts_dir, exist_ok=True)
    for name, body in samples.items():
        with open(os.path.join(scripts_dir, name), 'w', encoding='utf-8') as f:
            f.write(body)
    plugin_dir = os.path.join(root, 'preset', 'kexi-crypto')
    with open(os.path.join(plugin_dir, 'kexi-plugin.mjs'), 'w', encoding='utf-8') as f:
        f.write(FAKE_PLUGIN % '\n'.join("  '%s'," % n for n in sorted(samples)))
    target = os.path.join(root, 'scripts', 'test-scripts-smoke.py')
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'w', encoding='utf-8') as f:
        f.write(smoke_source)
    return target


def run_shadow(smoke_path, env_extra=None, cwd=None):
    """跑一次影子冒烟；返回 (exit, 合并输出)。带硬超时，超时即判负。"""
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    env.pop('KEXI_SMOKE_STREAK_FILE', None)
    env.update(env_extra or {})
    try:
        r = subprocess.run([PY, smoke_path], capture_output=True, text=True,
                           timeout=RUN_TIMEOUT, cwd=cwd or os.path.dirname(smoke_path),
                           env=env)
    except subprocess.TimeoutExpired:
        return 124, '影子冒烟超时 %ds（规则 12 判负）' % RUN_TIMEOUT
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def _pad(samples, total=35):
    """把影子脚本数补到接近仓库真实规模（40 个左右）。

    原因：被测脚本有一条 `发现脚本文件 >= 30` 的结构性断言——那是防止
    "目录扫空 → 全部检查空转通过"的护栏，不能因为影子树小而让它假红。
    补齐后影子树的规模、白名单形状、检查项数与真实仓库一致。
    """
    out = dict(samples)
    i = 0
    while len(out) < total:
        out['pad%02d.py' % i] = STUB_OK
        i += 1
    return out


results = []


def expect(label, ok, detail):
    results.append((label, bool(ok), detail))
    print('  %s  %s' % ('✓' if ok else '✗', label))
    if not ok:
        print('       → %s' % detail.replace('\n', '\n         '))


def main():
    old_src = _git_show_old_smoke()
    with open(os.path.join(REPO, SMOKE_REL), encoding='utf-8') as f:
        new_src = f.read()

    base = {'ok.py': STUB_OK, 'regime.py': STUB_OUTPUT,
            'position_doctor.py': STUB_OUTPUT}
    # 两种真实受限形态各一份（451 = Binance 现状，403 = Bybit 现状）
    net_samples = _pad(dict(base, **{'net451.py': STUB_NET451, 'net403.py': STUB_NET403,
                                     'netssl.py': STUB_SSL}))

    tmp = tempfile.mkdtemp(prefix='kexi-p11-nc-')

    # ── 负对照 1/2：同一份被测量、同一批样本，只换被测脚本版本 ──────────────
    old_root = os.path.join(tmp, 'old')
    old_smoke = build_shadow(old_root, old_src, net_samples)
    old_exit, old_out = run_shadow(old_smoke)

    new_root = os.path.join(tmp, 'new')
    new_smoke = build_shadow(new_root, new_src, net_samples)
    streak1 = os.path.join(tmp, 'streak-1.json')
    new_exit, new_out = run_shadow(new_smoke, {'KEXI_SMOKE_STREAK_FILE': streak1})

    print('\n── 负对照 1：改造前 + 真实 451/403/SSL 异常串 ─────────────────')
    print(old_out.strip())
    print('\n── 负对照 2：改造后 + 同一批异常串 ─────────────────────────────')
    print(new_out.strip())
    print('─' * 72)

    expect('改造前：451/403/SSL 被判成"堆栈崩溃"→ 整步 FAIL（复现体检的红）',
           old_exit == 1 and 'FAIL  ③' in old_out
           and 'HTTP Error 451' in old_out and '项 SKIP' not in old_out,
           'exit=%d\n%s' % (old_exit, old_out[-500:]))
    expect('改造后：同样输入 → exit 0，且 451/403/SSL 逐条显式 SKIP 并打印状态码',
           new_exit == 0
           and 'SKIP  受限网络 → net451.py（HTTP 451）' in new_out
           and 'SKIP  受限网络 → net403.py（HTTP 403）' in new_out
           and 'netssl.py' in new_out,
           'exit=%d\n%s' % (new_exit, new_out[-800:]))
    expect('改造后：汇总行含 SKIP 明细（禁止静默跳过）',
           '项 SKIP（受限网络：' in new_out and 'net451.py → HTTP 451' in new_out,
           new_out.strip().splitlines()[-1] if new_out.strip() else '(无输出)')

    # ── 负对照 3：业务异常必须永远 FAIL，包括"串里带网络字样"的情形 ─────────
    biz_samples = _pad(dict(net_samples, **{'biz_type.py': STUB_TYPEERROR,
                                          'biz_key.py': STUB_KEYERROR,
                                          'biz_noisy.py': STUB_TYPEERROR_WITH_NET_NOISE}))
    biz_root = os.path.join(tmp, 'biz')
    biz_smoke = build_shadow(biz_root, new_src, biz_samples)
    biz_exit, biz_out = run_shadow(
        biz_smoke, {'KEXI_SMOKE_STREAK_FILE': os.path.join(tmp, 'streak-3.json')})
    print('\n── 负对照 3：注入真业务异常（TypeError/KeyError/带网络字样的 TypeError）──')
    print(biz_out.strip())
    print('─' * 72)
    expect('改造后：TypeError / KeyError / "TypeError 内含 HTTP 451" 全部仍 FAIL',
           biz_exit == 1 and 'biz_type.py → TypeError' in biz_out
           and 'biz_key.py → KeyError' in biz_out
           and 'biz_noisy.py → TypeError' in biz_out,
           'exit=%d\n%s' % (biz_exit, biz_out[-900:]))

    # ── 负对照 4：SKIP 率超阈值 → 整步失败（SKIP 不得退化成永远绿）─────────
    many = dict(base)
    for i in range(10):
        many['net%02d.py' % i] = STUB_NET451
    many = _pad(many)
    many_root = os.path.join(tmp, 'many')
    many_smoke = build_shadow(many_root, new_src, many)
    many_exit, many_out = run_shadow(
        many_smoke, {'KEXI_SMOKE_STREAK_FILE': os.path.join(tmp, 'streak-4.json')})
    print('\n── 负对照 4：10/13 脚本 SKIP（未验证率 > 20% 阈值）──────────────')
    print(many_out.strip()[-1200:])
    print('─' * 72)
    expect('改造后：未验证率 10/35 = 28.6% > 20% → ⑦ 判 FAIL、整步 exit 1',
           many_exit == 1 and 'FAIL  ⑦' in many_out and '28.6%' in many_out,
           'exit=%d\n%s' % (many_exit, many_out[-800:]))

    # ── 负对照 5：同一脚本连续 3 次 SKIP → 整步失败 ────────────────────────
    streak5 = os.path.join(tmp, 'streak-5.json')
    runs = [run_shadow(new_smoke, {'KEXI_SMOKE_STREAK_FILE': streak5})[0]
            for _ in range(3)]
    last_exit, last_out = run_shadow(new_smoke, {'KEXI_SMOKE_STREAK_FILE': streak5})
    runs.append(last_exit)
    print('\n── 负对照 5：同一脚本连续 SKIP（第 1/2/3/4 次退出码 %s）──────────' % runs[:3])
    print(last_out.strip()[-900:])
    print('─' * 72)
    expect('改造后：连续 3 次 SKIP 起 → ⑧ 判 FAIL（第 3、4 次退出码为 1）',
           runs[0] == 0 and runs[1] == 0 and runs[2] == 1 and runs[3] == 1
           and 'FAIL  ⑧' in last_out,
           'runs=%s\n%s' % (runs, last_out[-800:]))
    os.remove(streak5)

    # ── 负对照 6：连续计数文件损坏 → 保护不允许静默失效 ───────────────────
    broken = os.path.join(tmp, 'streak-broken.json')
    with open(broken, 'w', encoding='utf-8') as f:
        f.write('{ 这不是 JSON')
    brk_exit, brk_out = run_shadow(new_smoke, {'KEXI_SMOKE_STREAK_FILE': broken})
    print('\n── 负对照 6：连续计数文件损坏 ─────────────────────────────────')
    print(brk_out.strip()[-700:])
    print('─' * 72)
    expect('改造后：计数文件损坏 → ⑧ 判 FAIL 并打印原因（不静默放弃保护）',
           brk_exit == 1 and 'FAIL  ⑧' in brk_out and '计数不可用' in brk_out,
           'exit=%d\n%s' % (brk_exit, brk_out[-800:]))

    shutil.rmtree(tmp, ignore_errors=True)

    print('\n' + '=' * 72)
    bad = [r for r in results if not r[1]]
    print('P1-1 负对照：%d/%d 符合预期' % (len(results) - len(bad), len(results)))
    print('=' * 72)
    for label, okv, detail in results:
        print('  %s %s' % ('✓' if okv else '✗', label))
        if not okv:
            print('      → %s' % detail.replace('\n', '\n        '))
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
