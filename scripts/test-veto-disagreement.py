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


REAL = r'C:\Users\lcl\Desktop\K析工作文件夹\kexi_out\PYTHUSDT_1d_report.json'


def base_report():
    """以**真实产出**为基线（保证必填键齐全），再按用例改写相关字段。"""
    try:
        with open(REAL, encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = None
    if d:
        # 抹掉本轮真实结论，换成中性值，避免用例之间互相干扰
        d = json.loads(json.dumps(d))
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
    # 兜底：仓库里的最小契约
    return {
        "schema_version": "kexi.report/1", "schema": "kexi.report/1",
        "generated_at": "2026-09-29T08:00:00+08:00",
        "disclaimer": "本报告不构成投资建议。",
        "report_text": "测试报告正文。",
        "symbol": {"name": "测试币", "code": "TESTUSDT", "price": 100.0},
        "as_of": "2026-09-29 08:00 UTC+8",
        "data_quality": {"bars": 239, "dropped_open_bar": 0, "gaps": []},
        "indicators": {"rsi": {"rsi14": 55.0}},
        "trend": {"state": "多头", "scenarios": [
            {"name": "基准", "probability": 0.5, "range_low": 95.0, "range_high": 105.0},
            {"name": "乐观", "probability": 0.3, "range_low": 105.0, "range_high": 112.0},
            {"name": "悲观", "probability": 0.2, "range_low": 88.0, "range_high": 95.0},
        ]},
        "risk": {"level": "中", "rationale": "测试", "warnings": ["a", "b", "c"],
                 "extreme_events": [], "data_gaps": []},
        "verdict": {"headline": "测试结论", "rating": "观察", "position_advice": "观望",
                    "scenarios_summary": "s", "key_levels": [
                        {"label": "支撑", "price": 95.0, "action": "跌破离场"}]},
    }


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
print(f'v1.5.5 硬风控否决 + 分歧度阈值：{ok}/{len(checks)} 通过')
print('=' * 66)
for n, c, e in checks:
    print(f'  {"✓" if c else "✗"} {n}')
    if not c and e:
        print(f'      → {e}')
sys.exit(0 if ok == len(checks) else 1)
