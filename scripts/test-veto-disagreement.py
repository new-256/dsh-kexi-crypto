#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.5.5 回归：①硬风控一票否决 ②分歧度阈值（取自 AlphaAgent 设计的可验证部分）。"""
import json
import os
import subprocess
import sys
import tempfile

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                       'preset', 'kexi-crypto', 'skills', 'crypto-market-analysis', 'scripts')
VALIDATE = os.path.join(SCRIPTS, 'validate_report.py')
sys.path.insert(0, SCRIPTS)


REAL = os.path.join(os.environ.get('KEXI_REAL_DIR')
                    or r'C:\Users\lcl\Desktop\K析工作文件夹\kexi_out',
                    'PYTHUSDT_1d_report.json')

# ── 基线来源：只认真实产出，缺失时**必须红**（体检 P1-6 / 规则 1）──────────
# 旧实现 `except Exception: d = None` 后静默切到内置"最小契约"合成基线。
# 2026-10-30 负对照实测（docs/evidence/p1-6-negative-control-run.log）：
#   · 那份合成基线**已经过期**——它缺 validate_report.py 现在必需的
#     data_quality.interval_actual / duplicates_removed / last 等字段，
#     所以缺基线时旧版并不是"继续绿"，而是 8/11 红，且报错指向
#     "data_quality 缺少必需字段"——**诊断错位**：人看到的像是报告字段有 bug，
#     实际是"基线文件根本没在"。这仍是规则 1 的静默降级（换了基线不说）。
#   · 因此本轮直接**删掉合成基线**：它既不是设计内用途（永远红），又会转移诊断。
#     真实基线不可读 → 打印期望路径/原因/取得方式 → exit 1，不假装跑过。
REAL = os.path.join(os.environ.get('KEXI_REAL_DIR')
                    or r'C:\Users\lcl\Desktop\K析工作文件夹\kexi_out',
                    'PYTHUSDT_1d_report.json')


def load_real_baseline(path):
    """读真实产出基线；任何失败都抛异常，由调用方决定（禁止静默降级）。"""
    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or not isinstance(doc.get('verdict'), dict):
        raise ValueError('不是有效的 kexi 报告（缺 verdict 对象）')
    return doc


try:
    _REAL_DOC = load_real_baseline(REAL)
except Exception as _e:                                     # noqa: BLE001
    print('  FAIL  前置条件：真实基线不可读 —— 本条测试不能假装跑过（规则 1）')
    print('        期望路径: %s' % REAL)
    print('        原因: %s' % _e)
    print('        取得方式: 对 PYTHUSDT 产出日线报告（写盘目录即 kexi_out），'
          '或设置 KEXI_REAL_DIR 指向含 PYTHUSDT_1d_report.json 的目录。')
    print('        本测试**没有**合成基线兜底：旧的内置合成契约已与 validate_report.py '
          '的必需字段脱节，留着只会把"基线缺失"伪装成"报告字段有 bug"。')
    sys.exit(1)
print('  基线：真实 PYTH 产出 %s' % REAL)


def base_report():
    """以**真实产出**为基线（保证必填键齐全），再按用例改写相关字段。"""
    # 抹掉本轮真实结论，换成中性值，避免用例之间互相干扰
    d = json.loads(json.dumps(_REAL_DOC))
    v = d.get('verdict') or {}
    v['rating'] = '观察'
    v['headline'] = '测试用中性结论'
    d['verdict'] = v
    rk = d.get('risk') or {}
    rk['level'] = '中'
    rk.pop('veto', None)
    rk.pop('vetoed_symbols', None)
    d['risk'] = rk
    d.pop('entry_plan', None)
    d.pop('timeframe', None)
    v.pop('disagreement_score', None)
    return d


def run(rep):
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False)
        p = f.name
    try:
        r = subprocess.run([sys.executable, VALIDATE, '--input', p],
                           capture_output=True, text=True, timeout=60)
        out = r.stdout.strip().splitlines()
        payload = {}
        for ln in reversed(out):
            try:
                payload = json.loads(ln)
                break
            except Exception:
                pass
        return payload
    finally:
        os.unlink(p)


checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


# ── 基线：干净报告应通过 ──
r = run(base_report())
ck('基线干净报告无 error', r.get('errors') == [], str(r.get('errors'))[:110])
ck('基线不误报分歧警告', not any('分歧' in w for w in r.get('warnings', [])), str(r.get('warnings'))[:90])

# ── ① 否决：正面评级必须被拒 ──
rep = base_report()
rep['risk']['veto'] = True
rep['risk']['rationale'] = '流动性枯竭，禁止开仓'
rep['verdict']['rating'] = '可关注'
r = run(rep)
ck('① veto=True + 正面评级 → 报错', any('一票否决' in e for e in r.get('errors', [])), str(r.get('errors'))[:130])

# ── ① 否决：给 enter_now 必须被拒 ──
rep = base_report()
rep['risk']['veto'] = True
rep['verdict']['rating'] = '否决/不建议入场'
rep['entry_plan'] = {'actions': [{'type': 'enter_now', 'price': 100.0, 'label': '立即入场'}]}
r = run(rep)
ck('① veto=True + enter_now → 报错', any('enter_now' in e for e in r.get('errors', [])), str(r.get('errors'))[:130])

# ── ① 否决：一致时通过（不误杀）──
rep = base_report()
rep['risk']['veto'] = {'active': True, 'reason': 'RSI 极高 + 流动性薄', 'rule': 'R-09'}
rep['verdict']['rating'] = '否决/不建议入场'
r = run(rep)
ck('① veto + 评级一致 → 通过', r.get('errors') == [], str(r.get('errors'))[:110])

# ── ① 否决类型校验 ──
rep = base_report()
rep['risk']['veto'] = 'yes'
r = run(rep)
ck('① veto 类型非法 → 报错', any('risk.veto' in e for e in r.get('errors', [])), '')

# ── ② 分歧度：显式高分 + 正面评级 → 报错 ──
rep = base_report()
rep['verdict']['disagreement_score'] = 0.8
rep['verdict']['rating'] = '可关注'
r = run(rep)
ck('② 分歧 0.8 + 正面评级 → 报错', any('分歧' in e for e in r.get('errors', [])), str(r.get('errors'))[:130])

# ── ② 分歧度：高分但已降级 → 通过 ──
rep = base_report()
rep['verdict']['disagreement_score'] = 0.8
rep['verdict']['rating'] = '分歧观察（不入场）'
r = run(rep)
ck('② 分歧 0.8 + 已降级 → 通过', r.get('errors') == [], str(r.get('errors'))[:110])

# ── ② 分歧度：日线多头 vs 周线空头 自动推导 ──
rep = base_report()
rep['timeframe'] = {'daily_dir': '多头排列', 'weekly_dir': '空头排列', 'alignment': '背离'}
rep['verdict']['rating'] = '可关注'
r = run(rep)
ck('② 日线vs周线背离被自动识别', any('分歧' in e for e in r.get('errors', [])), str(r.get('errors'))[:130])

# ── ② 分歧度：结构化缺失时不误报 ──
rep = base_report()
rep['verdict']['rating'] = '观察'
r = run(rep)
ck('② 无结构化结论时不误报', not any('分歧' in e for e in r.get('errors', [])), '')

# ── ② 分歧度：低分不报警 ──
rep = base_report()
rep['verdict']['disagreement_score'] = 0.1
rep['verdict']['rating'] = '可关注'
r = run(rep)
ck('② 分歧 0.1 不误报', not any('分歧' in e for e in r.get('errors', [])), '')

ok = sum(1 for _, c, _ in checks if c)
print('=' * 66)
print(f'v1.5.5 硬风控否决 + 分歧度阈值：{ok}/{len(checks)} 通过（基线=真实产出）')
print('=' * 66)
for n, c, e in checks:
    print(f'  {"✓" if c else "✗"} {n}')
    if not c and e:
        print(f'      → {e}')
sys.exit(0 if ok == len(checks) else 1)
