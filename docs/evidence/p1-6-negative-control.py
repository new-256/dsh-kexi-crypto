#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-6 负对照（体检 2026-10-30 §P1-6 / 规则 1「禁降级兜底」）。

## 要证明的事

体检结论：两个测试把仓库外的开发机产出当基线，**基线不存在时静默缩分母**
（`test-stop-plan.py` 整段不注册断言）或**静默换合成基线**（`test-veto-disagreement.py`
`except: d = None`），于是断言在干净机器/CI 上等于未验证。

本文件证明：

  · 负对照 1：改造前 + 基线缺失 → 静默缩分母 / 静默换基线（复现体检缺陷）
        stop-plan 报 7/7（分母从 9 缩到 7，看不出少了两条）
        veto      换成**已过期**的内置合成契约 → 8/11 红，报错指向 data_quality
                  （2026-10-30 实测更正体检口径：不是"假绿"，是"诊断错位"——
                   人看到像报告字段有 bug，实际是基线文件根本没在）
  · 负对照 2：改造后 + 基线缺失 → 明确 FAIL 并打印期望路径（分母不缩水）
        stop-plan 报 7/9 且两条 FAIL 带「期望路径」
        veto      直接「前置条件：真实基线不可读」+ 期望路径 + 取得方式
  · 负对照 3：改造后 + 基线损坏（不是合法 JSON）→ 同样 FAIL（不是"读不到就当过"）
  · 负对照 4：改造后不存在"逃逸开关"——旧参数 `--allow-synthetic-baseline` 传入
        也不会复活任何兜底，仍然前置条件 FAIL（不留隐藏放行通道）

## 怎么做到"真跑"

`git archive HEAD` 导出**仓库外的独立干净副本**（稳贝 = 改造前代码），把副本里两个
写死的开发机路径替换成一个不存在的目录（模拟"干净机器/CI"），再与工作区当前版本
在同一台机器、同一分钟内对照运行。替换次数会自检：不是 1 次就报错退出，
防止"没替换成功 → 基线其实还在 → 两边都绿"这种自欺结论。

用法：C:\\Python313\\python.exe docs/evidence/p1-6-negative-control.py
退出码：0 = 全部负对照符合预期；1 = 不符合预期（修复失效）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.environ.get('KEXI_PYTHON') or sys.executable
RUN_TIMEOUT = 180
MISSING_DIR = r'Z:\kexi-nonexistent-baseline'

# 副本里要替换的写死路径字面量（从 HEAD 原文件里原样出现）
OLD_STOP_PATH = r"K = r'C:\Users\lcl\Desktop\K析工作文件夹\kexi_out'"
NEW_STOP_PATH = r"K = r'%s'" % MISSING_DIR
OLD_VETO_PATH = r"REAL = r'C:\Users\lcl\Desktop\K析工作文件夹\kexi_out\PYTHUSDT_1d_report.json'"
NEW_VETO_PATH = r"REAL = r'%s\PYTHUSDT_1d_report.json'" % MISSING_DIR

results = []


def expect(label, ok, detail=''):
    results.append((label, bool(ok), detail))
    print('  %s  %s' % ('✓' if ok else '✗', label))
    if not ok:
        print('       → %s' % detail.replace('\n', '\n         '))


def run(argv, repo, env_extra=None):
    """运行一个测试，带硬超时（规则 12）。"""
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'KEXI_PYTHON': PY}
    env.pop('KEXI_REAL_DIR', None)
    env.update(env_extra or {})
    try:
        r = subprocess.run([PY] + argv, cwd=repo, capture_output=True, text=True,
                           timeout=RUN_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return 124, '超时 %ds（规则 12 判负）' % RUN_TIMEOUT
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def export_head(dest):
    """git archive HEAD 导出改造前的干净副本（改造前代码的真来源）。"""
    os.makedirs(dest, exist_ok=True)
    tar_path = os.path.join(os.path.dirname(dest), 'head.tar')
    r = subprocess.run(['git', 'archive', '--format=tar', '-o', tar_path, 'HEAD'],
                       cwd=REPO, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise SystemExit('git archive HEAD 失败: %s' % (r.stderr or '')[-300:])
    r = subprocess.run(['tar', '-xf', tar_path, '-C', dest],
                       cwd=REPO, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise SystemExit('tar 解包失败: %s' % (r.stderr or '')[-300:])
    return dest


def patch_literal(path, old, new):
    """把副本里的写死路径换成不存在的目录；必须恰好替换 1 次。"""
    with open(path, encoding='utf-8') as f:
        body = f.read()
    n = body.count(old)
    if n != 1:
        raise SystemExit('路径字面量在 %s 里出现 %d 次（期望 1）——对照组构造失败，'
                         '禁止在未成功构造对照组的情况下给结论' % (os.path.basename(path), n))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(body.replace(old, new))


def main():
    tmp = tempfile.mkdtemp(prefix='kexi-p16-nc-')
    head = export_head(os.path.join(tmp, 'head'))
    patch_literal(os.path.join(head, 'scripts', 'test-stop-plan.py'),
                  OLD_STOP_PATH, NEW_STOP_PATH)
    patch_literal(os.path.join(head, 'scripts', 'test-veto-disagreement.py'),
                  OLD_VETO_PATH, NEW_VETO_PATH)

    print('\n── 负对照 1：改造前（HEAD 干净副本）+ 基线缺失 ─────────────────')
    old_stop_exit, old_stop = run(['scripts/test-stop-plan.py'], head)
    old_veto_exit, old_veto = run(['scripts/test-veto-disagreement.py'], head)
    print(old_stop.strip())
    print(old_veto.strip().splitlines()[0] if old_veto.strip() else '(无输出)')
    print('─' * 72)
    expect('改造前 stop-plan：基线缺失 → 分母缩成 7/7 仍 PASS（静默缩分母）',
           old_stop_exit == 0 and '7/7 通过' in old_stop
           and '真实 PYTH' not in old_stop,
           'exit=%d\n%s' % (old_stop_exit, old_stop[-400:]))
    expect('改造前 veto：缺基线 → 静默换过期合成基线，8/11 红且报错指向 '
           'data_quality（诊断错位，绝不提示"基线缺失"）',
           old_veto_exit == 1 and '8/11 通过' in old_veto
           and '期望路径' not in old_veto and '前置条件' not in old_veto
           and 'data_quality 缺少必需字段' in old_veto,
           'exit=%d\n%s' % (old_veto_exit, old_veto[-500:]))

    print('\n── 负对照 2：改造后 + 同一台机器、同一分钟、同样缺失 ───────────')
    new_stop_exit, new_stop = run(['scripts/test-stop-plan.py'], REPO,
                                  {'KEXI_REAL_DIR': MISSING_DIR})
    new_veto_exit, new_veto = run(['scripts/test-veto-disagreement.py'], REPO,
                                  {'KEXI_REAL_DIR': MISSING_DIR})
    print(new_stop.strip())
    print(new_veto.strip())
    print('─' * 72)
    expect('改造后 stop-plan：分母仍为 9，缺基线 → 7/9 + exit 1',
           new_stop_exit == 1 and '7/9 通过' in new_stop,
           'exit=%d\n%s' % (new_stop_exit, new_stop[-600:]))
    expect('改造后 stop-plan：两条 FAIL 都打印期望路径与取得方式',
           new_stop.count('期望路径:') == 2 and 'KEXI_REAL_DIR' in new_stop,
           new_stop[-700:])
    expect('改造后 veto：缺基线 → 「前置条件：真实基线不可读」+ exit 1（不假装跑过）',
           new_veto_exit == 1 and '前置条件：真实基线不可读' in new_veto
           and '期望路径:' in new_veto and '11/11 通过' not in new_veto,
           'exit=%d\n%s' % (new_veto_exit, new_veto[-700:]))

    # ── 负对照 3：基线文件存在但内容损坏 ──────────────────────────────────
    broken_dir = os.path.join(tmp, 'broken')
    os.makedirs(broken_dir, exist_ok=True)
    for name in ('PYTHUSDT_1d_klines.json', 'PYTHUSDT_1d_report.json'):
        with open(os.path.join(broken_dir, name), 'w', encoding='utf-8') as f:
            f.write('{ 这不是合法 JSON')
    brk_stop_exit, brk_stop = run(['scripts/test-stop-plan.py'], REPO,
                                  {'KEXI_REAL_DIR': broken_dir})
    brk_veto_exit, brk_veto = run(['scripts/test-veto-disagreement.py'], REPO,
                                  {'KEXI_REAL_DIR': broken_dir})
    print('\n── 负对照 3：基线文件损坏（不是合法 JSON）──────────────────────')
    print(brk_veto.strip().splitlines()[0] if brk_veto.strip() else '(无输出)')
    print('─' * 72)
    expect('改造后 stop-plan：基线损坏 → 仍 7/9 FAIL（不是"读不到就当过"）',
           brk_stop_exit == 1 and '7/9 通过' in brk_stop and '基线不可用' in brk_stop,
           'exit=%d\n%s' % (brk_stop_exit, brk_stop[-500:]))
    expect('改造后 veto：基线损坏 → 前置条件 FAIL 并打印原因',
           brk_veto_exit == 1 and '前置条件：真实基线不可读' in brk_veto
           and '原因:' in brk_veto,
           'exit=%d\n%s' % (brk_veto_exit, brk_veto[-600:]))

    # ── 负对照 4：不许留"逃逸开关"把兜底复活 ──────────────────────────────
    esc_exit, esc_out = run(['scripts/test-veto-disagreement.py',
                            '--allow-synthetic-baseline'], REPO,
                           {'KEXI_REAL_DIR': MISSING_DIR})
    print('\n── 负对照 4：传入旧逃逸参数 --allow-synthetic-baseline 也不得复活兜底 ──')
    print(esc_out.strip())
    print('─' * 72)
    expect('改造后 veto：合成基线已删除，旧逃逸参数无效 → 仍然前置条件 FAIL',
           esc_exit == 1 and '前置条件：真实基线不可读' in esc_out
           and '11/11 通过' not in esc_out and '使用合成基线' not in esc_out,
           'exit=%d\n%s' % (esc_exit, esc_out[-700:]))

    # ── 正对照：真实基线在场时两条都要绿（证明 fail-loud 没误伤本机）──────
    ok_stop_exit, ok_stop = run(['scripts/test-stop-plan.py'], REPO)
    ok_veto_exit, ok_veto = run(['scripts/test-veto-disagreement.py'], REPO)
    print('\n── 正对照：本机真实基线在场 ───────────────────────────────────')
    print('  %s / %s' % (ok_stop.strip().splitlines()[1] if ok_stop.strip() else '?',
                         ok_veto.strip().splitlines()[1] if ok_veto.strip() else '?'))
    print('─' * 72)
    expect('真实基线在场：stop-plan 9/9 绿、veto 11/11 绿（fail-loud 不误伤）',
           ok_stop_exit == 0 and '9/9 通过' in ok_stop
           and ok_veto_exit == 0 and '11/11 通过' in ok_veto,
           'stop exit=%d veto exit=%d\n%s\n%s'
           % (ok_stop_exit, ok_veto_exit, ok_stop[-300:], ok_veto[-300:]))

    shutil.rmtree(tmp, ignore_errors=True)

    print('\n' + '=' * 72)
    bad = [r for r in results if not r[1]]
    print('P1-6 负对照：%d/%d 符合预期' % (len(results) - len(bad), len(results)))
    print('=' * 72)
    for label, okv, detail in results:
        print('  %s %s' % ('✓' if okv else '✗', label))
        if not okv:
            print('      → %s' % detail.replace('\n', '\n        '))
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
