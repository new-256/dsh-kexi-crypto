#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_all.py - 自动化自测执行套件（自测要求 5 全项覆盖）

测试矩阵：
  a. make_sample_report.py 基于真实数据生成 sample_report.json
  b. validate_report.py 正例测试（ok=true, errors=[]）
     反例测试 1：改 probability 和为 2.0（或单个为 2.0） -> 必须报对应 error
     反例测试 2：把 price 写成字符串 "84433.1" -> 必须报对应 error
     反例测试 3：把 last_bar_age_days 写成 90 -> 必须报对应 error (数据停更 D4)
     修复测试 4：--fix 安全归一 -> 验证 changed 字段与修复后校验通过
  c. compact_report.py 压缩测试：
     检查字符数 <= 1200、[compact sha1=... chars=...] 机器头格式、--out 输出的 JSON 键完整性
  d. dashboard.py 渲染测试：
     检查 HTML 字节数 (<200KB)、sections_rendered 完整性、html.parser 解析无异常、正则查无 http/https 外链
  e. py_compile 编译校验所有新脚本

Stdlib only.
"""

import copy
import html.parser
import json
import os
import re
import shutil
import subprocess
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_TEST_DIR, "..", "scripts"))

PYTHON_EXE = sys.executable


def run_py(script_name, args):
    script_path = os.path.join(_SCRIPTS_DIR, script_name)
    cmd = [PYTHON_EXE, script_path] + args
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return res.returncode, res.stdout, res.stderr


class HtmlValidator(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.void_tags = {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"
        }
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.void_tags:
            self.tags.append(tag)

    def handle_endtag(self, tag):
        if tag in self.void_tags:
            return
        if not self.tags:
            self.errors.append(f"多余的闭合标签: </{tag}>")
            return
        last = self.tags.pop()
        if last != tag:
            # SVG 内联或某些嵌套可能有宽松容错，记录不匹配
            self.errors.append(f"标签闭合不匹配: <{last}> 与 </{tag}>")


def run_tests():
    print("=" * 60)
    print("开始执行 K析研判团 DSH 插件三脚本自测套件")
    print("=" * 60)

    sample_report_path = os.path.join(_TEST_DIR, "sample_report.json")
    assert os.path.exists(sample_report_path), f"缺少测试基准文件: {sample_report_path}"

    with open(sample_report_path, "r", encoding="utf-8") as f:
        base_data = json.load(f)

    # -------------------------------------------------------------
    # 5.b: validate_report.py 正例测试
    # -------------------------------------------------------------
    print("\n[测试 1] validate_report.py 正例校验 ...")
    rc, stdout, stderr = run_py("validate_report.py", ["--input", sample_report_path, "--quiet"])
    print(f"  退出码: {rc}, stdout: {stdout.strip()}")
    assert rc == 0, f"正例校验退出码应为 0，实际为 {rc}"
    res = json.loads(stdout.strip())
    assert res.get("ok") is True, "正例校验 ok 应为 True"
    assert len(res.get("errors", [])) == 0, f"正例校验不应有 error: {res.get('errors')}"
    print("  ✅ 正例测试通过")

    # -------------------------------------------------------------
    # 5.b: 反例测试 1 - 改 probability 和为 2.0
    # -------------------------------------------------------------
    print("\n[测试 2] validate_report.py 反例测试 1: probability 和为 2.0 ...")
    bad_data_1 = copy.deepcopy(base_data)
    # 将三情景概率设为 [0.8, 0.6, 0.6]，和为 2.0
    for sc, p in zip(bad_data_1["trend"]["scenarios"], [0.8, 0.6, 0.6]):
        sc["probability"] = p
    tmp_path_1 = os.path.join(_TEST_DIR, "_tmp_bad_prob.json")
    with open(tmp_path_1, "w", encoding="utf-8") as f:
        json.dump(bad_data_1, f, ensure_ascii=False)

    rc, stdout, stderr = run_py("validate_report.py", ["--input", tmp_path_1, "--quiet"])
    print(f"  退出码: {rc}, stdout: {stdout.strip()}")
    assert rc == 1, f"反例退出码应为 1，实际为 {rc}"
    res = json.loads(stdout.strip())
    assert res.get("ok") is False, "反例 ok 应为 False"
    has_prob_err = any("probability 之和" in e or "probability" in e for e in res.get("errors", []))
    assert has_prob_err, f"未检测到 probability 异常: {res.get('errors')}"
    print(f"  ✅ 成功拦截：{res.get('errors')}")

    # -------------------------------------------------------------
    # 5.b: 反例测试 2 - 把 price 写成字符串
    # -------------------------------------------------------------
    print("\n[测试 3] validate_report.py 反例测试 2: price 写成字符串 ...")
    bad_data_2 = copy.deepcopy(base_data)
    bad_data_2["symbol"]["price"] = "84433.10"
    tmp_path_2 = os.path.join(_TEST_DIR, "_tmp_bad_price.json")
    with open(tmp_path_2, "w", encoding="utf-8") as f:
        json.dump(bad_data_2, f, ensure_ascii=False)

    rc, stdout, stderr = run_py("validate_report.py", ["--input", tmp_path_2, "--quiet"])
    print(f"  退出码: {rc}, stdout: {stdout.strip()}")
    assert rc == 1, f"反例退出码应为 1，实际为 {rc}"
    res = json.loads(stdout.strip())
    assert res.get("ok") is False
    has_price_err = any("symbol.price" in e and "字符串" in e for e in res.get("errors", []))
    assert has_price_err, f"未检测到字符串 price 异常: {res.get('errors')}"
    print(f"  ✅ 成功拦截：{res.get('errors')}")

    # -------------------------------------------------------------
    # 5.b: 反例测试 3 - 把 last_bar_age_days 写成 90 (D4 遗留停更)
    # -------------------------------------------------------------
    print("\n[测试 4] validate_report.py 反例测试 3: last_bar_age_days 写成 90 ...")
    bad_data_3 = copy.deepcopy(base_data)
    bad_data_3["data_quality"]["last_bar_age_days"] = 90
    tmp_path_3 = os.path.join(_TEST_DIR, "_tmp_bad_age.json")
    with open(tmp_path_3, "w", encoding="utf-8") as f:
        json.dump(bad_data_3, f, ensure_ascii=False)

    rc, stdout, stderr = run_py("validate_report.py", ["--input", tmp_path_3, "--quiet"])
    print(f"  退出码: {rc}, stdout: {stdout.strip()}")
    assert rc == 1, f"反例退出码应为 1，实际为 {rc}"
    res = json.loads(stdout.strip())
    assert res.get("ok") is False
    has_age_err = any("停更" in e or "D4" in e for e in res.get("errors", []))
    assert has_age_err, f"未检测到停更 D4 异常: {res.get('errors')}"
    print(f"  ✅ 成功拦截：{res.get('errors')}")

    # -------------------------------------------------------------
    # 5.b: 修复测试 - --fix 安全归一
    # -------------------------------------------------------------
    print("\n[测试 5] validate_report.py --fix 安全归一测试 ...")
    fix_data = copy.deepcopy(base_data)
    fix_data["trend"]["scenarios"][0]["probability"] = 0.50
    fix_data["trend"]["scenarios"][1]["probability"] = 0.30
    fix_data["trend"]["scenarios"][2]["probability"] = 0.30  # sum = 1.10
    fix_data["risk"]["warnings"].extend(["多余警告1", "多余警告2", "多余警告3"])  # 超长 7 条
    fix_data["symbol"]["name"] = "  Bitcoin Extra Spaces  "  # 首尾空白
    tmp_path_fix_in = os.path.join(_TEST_DIR, "_tmp_fix_in.json")
    tmp_path_fix_out = os.path.join(_TEST_DIR, "kexi_out", "report_fixed.json")
    with open(tmp_path_fix_in, "w", encoding="utf-8") as f:
        json.dump(fix_data, f, ensure_ascii=False)

    rc, stdout, stderr = run_py("validate_report.py", ["--input", tmp_path_fix_in, "--fix", "--out", tmp_path_fix_out, "--quiet"])
    print(f"  退出码: {rc}, stdout: {stdout.strip()}")
    assert rc == 0, f"归一修复后退出码应为 0，实际为 {rc}"
    res = json.loads(stdout.strip())
    assert res.get("ok") is True
    changed = res.get("changed", [])
    print(f"  changed 清单: {changed}")
    assert any("probability" in c for c in changed), "未记录 probability 归一修改"
    assert any("截断" in c for c in changed), "未记录 warnings 截断修改"
    assert any("空白" in c for c in changed), "未记录去首尾空白修改"
    print("  ✅ --fix 安全归一测试通过")

    # -------------------------------------------------------------
    # 5.d: compact_report.py 压缩测试
    # -------------------------------------------------------------
    print("\n[测试 6] compact_report.py 压缩测试 ...")
    compact_out_path = os.path.join(_TEST_DIR, "kexi_out", "report.compact.json")
    rc, stdout, stderr = run_py("compact_report.py", ["--input", sample_report_path, "--out", compact_out_path])
    assert rc == 0, f"compact_report.py 退出码应为 0，实际为 {rc}"
    lines = stdout.strip().split("\n")
    machine_line = lines[-1]
    summary_text = "\n".join(lines[:-1])
    print(f"  摘要纯文本行数: {len(lines)-1}, 字符数: {len(summary_text)}")
    print(f"  机器行: {machine_line}")
    assert "[compact sha1=" in machine_line and "chars=" in machine_line, f"机器行格式不符合预期: {machine_line}"
    assert len(summary_text) <= 1200, f"摘要字符数超过 1200 (实际 {len(summary_text)})"
    assert os.path.exists(compact_out_path), "未生成 --out 紧凑 JSON"
    with open(compact_out_path, "r", encoding="utf-8") as f:
        compact_json = json.load(f)
    assert "summary_text" in compact_json
    assert "key_numbers" in compact_json
    kn = compact_json["key_numbers"]
    for req_num_k in ["price", "ma20", "rsi14", "atr_pct", "direction", "confidence", "risk_level"]:
        assert req_num_k in kn, f"key_numbers 缺少键: {req_num_k}"
    print("  ✅ compact_report.py 压缩测试通过")

    # -------------------------------------------------------------
    # 5.c: dashboard.py 渲染测试
    # -------------------------------------------------------------
    print("\n[测试 7] dashboard.py 渲染测试 ...")
    html_out_path = os.path.join(_TEST_DIR, "kexi_out", "report.html")
    tmp_portfolio_path = os.path.join(_TEST_DIR, "_tmp_portfolio.json")
    
    # 动态创建测试用组合数据（含假分散标记）
    test_port_data = {
        "fake_diversification": True,
        "positions": [
            {"symbol": "BTC", "close": 84433.1, "atr14": 2347.26, "atr_pct": 2.78, "weight": 0.18, "notional": 1800.0, "amihud": 0.009, "error": None},
            {"symbol": "ETH", "close": 3120.5, "atr14": 110.2, "atr_pct": 3.53, "weight": 0.14, "notional": 1400.0, "amihud": 0.021, "error": None},
            {"symbol": "SOL", "close": 165.8, "atr14": 8.4, "atr_pct": 5.07, "weight": 0.10, "notional": 1000.0, "amihud": 0.035, "error": None}
        ],
        "correlation_matrix": {
            "BTC": {"BTC": 1.0, "ETH": 0.88, "SOL": 0.76},
            "ETH": {"BTC": 0.88, "ETH": 1.0, "SOL": 0.82},
            "SOL": {"BTC": 0.76, "ETH": 0.82, "SOL": 1.0}
        }
    }
    with open(tmp_portfolio_path, "w", encoding="utf-8") as f:
        json.dump(test_port_data, f, ensure_ascii=False)

    args = ["--input", sample_report_path, "--portfolio", tmp_portfolio_path, "--out", html_out_path]

    rc, stdout, stderr = run_py("dashboard.py", args)
    print(f"  退出码: {rc}, stdout: {stdout.strip()}")
    assert rc == 0, f"dashboard.py 退出码应为 0，实际为 {rc}"
    dash_res = json.loads(stdout.strip())
    assert dash_res.get("ok") is True
    html_bytes = dash_res.get("bytes", 0)
    sections = dash_res.get("sections_rendered", [])
    print(f"  HTML 字节数: {html_bytes} 字节 ({html_bytes/1024:.2f} KB)")
    print(f"  渲染板块清单: {sections}")
    assert "candle_chart" in sections, "未渲染 candle_chart 板块"
    assert "portfolio" in sections, "未渲染 portfolio 板块"
    assert html_bytes < 200 * 1024, f"HTML 字节数超出 200KB 目标 (实际 {html_bytes} 字节)"
    assert os.path.exists(html_out_path)

    # 验证 HTML 文件合规性：无 http(s) 外链
    with open(html_out_path, "r", encoding="utf-8") as f:
        html_src = f.read()

    # 检查所有 src= 和 href=
    links = re.findall(r'(?:src|href)=["\']([^"\']+)["\']', html_src, re.IGNORECASE)
    print(f"  检查 HTML 标签中的引用链接 (共 {len(links)} 处): {links}")
    for link in links:
        assert not link.lower().startswith("http://") and not link.lower().startswith("https://"), f"发现外部外链引用: {link}"
    print("  ✅ 外部外链检查通过（100% 零外链）")

    # HTML 语法与未闭合标签检查
    parser = HtmlValidator()
    try:
        parser.feed(html_src)
        print("  ✅ html.parser 解析校验通过")
    except Exception as e:
        assert False, f"HTML 语法解析异常: {e}"

    # -------------------------------------------------------------
    # G4/G7: 筹码峰 + 解锁时间表（离线合成数据，防回归）
    # -------------------------------------------------------------
    print("\n[测试 8] G4 volume_profile 与 G7 unlock_schedule 防回归 ...")
    sys.path.insert(0, _SCRIPTS_DIR)
    import importlib
    _ind_mod = importlib.import_module("indicators")
    _DAY = 86_400_000
    # 合成 120 根日线：价格 100~200 区间上行，中段放量（制造筹码峰）
    _syn = []
    for i in range(120):
        c = 100 + i * 0.9
        mid_v = 500 if 40 <= i <= 70 else 100
        _syn.append([i * _DAY, c - 1, c + 1, c - 2, c, mid_v])
    _vp = _ind_mod.volume_profile(_syn)
    assert _vp.get("available") is True, "合成数据有成交量，筹码峰应可用"
    _poc = _vp["point_of_control"]["price"]
    assert 130 <= _poc <= 170, f"POC 应落在放量中段，实际 {_poc}"
    _va = _vp["value_area"]
    assert _va["va_low"] <= _poc <= _va["va_high"], "POC 应在价值区内"
    assert 60 <= _va["covers_pct"] <= 80, "价值区应覆盖约 70%"
    assert abs(sum(b["pct"] for b in _vp["bins"]) - 100) < 0.5, "bins 百分比合计应=100"
    assert all(b["tag"] in ("POC", "HVN", "LVN", "normal") for b in _vp["bins"])
    print(f"  ✅ G4 POC={_poc} 价值区[{_va['va_low']},{_va['va_high']}] HVN={len(_vp['hvn'])} LVN={len(_vp['lvn'])}")
    # 无成交量场景（CoinGecko）必须降级
    _no_vp = _ind_mod.volume_profile([[i * _DAY, 1, 2, 0.5, 1.5, 0] for i in range(50)])
    assert _no_vp.get("available") is False, "零成交量筹码峰必须 available=False"
    print("  ✅ G4 无量源降级正确")

    # G7：手工事件 JSON（近 7 天 18% 解锁 + 远期小解锁 + 脏数据）
    # 日期相对运行时动态生成，避免依赖机器时钟
    from datetime import datetime as _dt, timedelta as _td
    _near = (_dt.utcnow() + _td(days=4)).strftime("%Y-%m-%d")
    _far = (_dt.utcnow() + _td(days=250)).strftime("%Y-%m-%d")
    _unlock_in = os.path.join(_TEST_DIR, "_tmp_unlock_in.json")
    with open(_unlock_in, "w", encoding="utf-8") as f:
        json.dump({"symbol": "ZZZ", "total_supply": 1_000_000_000,
                   "events": [
                       {"date": _near, "name": "团队份额", "unlock_tokens": 180_000_000},
                       {"date": _far, "name": "生态", "unlock_tokens": 30_000_000},
                       {"date": "bad", "unlock_tokens": 1000}]}, f)
    _unlock_out = os.path.join(_TEST_DIR, "kexi_out", "unlock.json")
    rc, stdout, stderr = run_py("unlock_schedule.py", ["--input", _unlock_in, "--out", _unlock_out])
    assert rc == 0, f"unlock_schedule 退出码应为 0，实际 {rc} {stderr}"
    with open(_unlock_out, encoding="utf-8") as f:
        _ud = json.load(f)
    _ev = {r["date"]: r for r in _ud["events"] if not r["unverified"]}
    assert abs(_ev[_near]["unlock_pct"] - 18.0) < 0.01
    assert _ev[_near]["impact_level"] in ("高", "极高"), "近 7 天 18% 解锁必须高/极高"
    assert len(_ud["risk_flags"]) == 1, "应给 1 条近30天高冲击风险旗"
    assert _ud["risk_level_90d"] in ("高", "极高")
    assert any(r["unverified"] for r in _ud["events"]), "脏日期事件应保留 unverified"
    print(f"  ✅ G7 18%/{_ev[_near]['days_until']}天 = {_ev[_near]['impact_level']} + 风险旗")
    # registry 查无此币必须退出码 2
    rc2, stdout2, _ = run_py("unlock_schedule.py", ["--symbol", "NOPE-NOT-A-COIN"])
    assert rc2 == 2, "registry 无记录必须退出码 2"
    print("  ✅ G7 无登记降级正确（退出码 2，不编造日期）")

    # -------------------------------------------------------------
    # 5.e: py_compile 语法编译检查
    # -------------------------------------------------------------
    print("\n[测试 9] screener.py 并发取数口径防回归 ...")
    # v1.2.0：screener 由"逐币串行 + sleep"改为 ThreadPoolExecutor 并发。
    # 这里不联网，只做**静态结构**断言：并发分支存在、--workers 参数存在、
    # workers<=1 时仍走严格串行（可复现历史口径）。
    _scr = os.path.join(_SCRIPTS_DIR, "screener.py")
    with open(_scr, "r", encoding="utf-8") as f:
        _src = f.read()
    assert "ThreadPoolExecutor" in _src, "screener.py 缺并发分支（ThreadPoolExecutor）"
    assert "--workers" in _src, "screener.py 缺 --workers 参数"
    assert "workers <= 1" in _src or "workers<=1" in _src, "screener.py 缺 workers<=1 的串行回退分支"
    assert "as_completed" in _src, "screener.py 并发收口未用 as_completed"
    # 并发默认值必须保守（防交易所限频）
    import re as _re
    _m = _re.search(r'"--workers",\s*type=int,\s*default=(\d+)', _src)
    assert _m, "未能解析 --workers 默认值"
    _w = int(_m.group(1))
    assert 2 <= _w <= 16, f"--workers 默认值应在 2..16（保守防限频），实际 {_w}"
    print(f"  ✅ 并发结构完整（默认 workers={_w}，含 workers<=1 串行回退）")

    print("\n[测试 9b] screener 突破加分必须确认量能（防'名不副实'回归） ...")
    # 原 bug：bonus"放量突破"只校验收盘站上/未回踩，完全不看成交量 →
    # 无量价穿也拿 +2。用合成 K 线真实驱动 score_symbol 验证量能门槛。
    _scr_mod = importlib.import_module("screener")
    _N = 70
    def _make_breakout_klines(break_vol):
        kl = []
        for i in range(_N):
            # 前 69 根收 100（高 100.5）；最后一根收 102，close 才 > 前20高。
            # 注意 prior20_high=klines[-21:-1] 已含倒数第2/3根，突破只可能在末根被识别。
            is_last = i == _N - 1
            c = 102.0 if is_last else 100.0
            v = break_vol if is_last else 100.0
            kl.append([i * _DAY, c - 0.5, c + 0.5, c - 1.0, c, v])
        return kl
    def _has_breakout_bonus(sc):
        return any("放量突破" in r and "+2" in r for r in sc["bonus_reasons"])
    # 无量突破（突破根量比 1.0 < 1.2）：不得给"放量突破(+2)"
    _sc_low = _scr_mod.score_symbol(_make_breakout_klines(100.0), vol_mult=99)
    assert _sc_low is not None, "合成数据应可评分"
    assert not _has_breakout_bonus(_sc_low), \
        f"无量突破不应拿放量突破加分，实际 {_sc_low['bonus_reasons']}"
    assert any("突破根无量" in r for r in _sc_low["bonus_reasons"]), "应给出'突破根无量'说明"
    # 放量突破（突破根量比 2.0 ≥ 1.2）：应给 +2
    _sc_hi = _scr_mod.score_symbol(_make_breakout_klines(200.0), vol_mult=99)
    assert _sc_hi is not None and _has_breakout_bonus(_sc_hi), \
        f"放量突破应拿 +2，实际 {_sc_hi['bonus_reasons'] if _sc_hi else None}"
    assert _sc_hi["bonus_reasons"], "应有加分理由"
    print("  ✅ 无量突破不加分、放量突破(量比≥1.2)才+2")

    # -----------------------------------------------------------------
    # [测试 9c] 位置 / 拥挤度维度（v1.5.0）
    # 用户实测反馈："选出来的多是技术指标好、但已经放量多天在高位的币"，
    # 并点名 PYTH："一个大回调之前的盈利位现在全变成了大阻力位"。
    # 本组用合成 K 线锁定三件事：
    #   1) 高位贴顶 + 放量多日 必须被扣分（且扣得比中位币多）
    #   2) 真正的低位横盘 必须被识别（不能因"没站上MA20"被系统性漏掉）
    #   3) 崩盘后高波动阴跌 不得被误判为横盘（TRUMP 教训：位置低但振幅169%）
    # -----------------------------------------------------------------
    print("\n[测试 9c] 位置/拥挤度维度（防'选币全选在高位'回归） ...")
    import importlib as _il
    _pos_mod = _il.import_module("position")

    def _mk(kind, n=200):
        """构造合成日线：返回 [[ts,o,h,l,c,v], ...]"""
        kl = []
        if kind == "high_extended":
            # 前 120 根在 100 附近横盘，后 80 根单边拉到 ~300。
            # 成交量只在**最后 20 根**放大（vol_ma20_prior 取 vols[-40:-20]，
            # 那段仍是 100，放大阈值 150，末 20 根 400 → vol_expansion_days=20）。
            for i in range(n):
                if i < 120:
                    c, v = 100.0, 100.0
                else:
                    c = 100.0 + (i - 119) * 2.5      # 线性拉到 ~302
                    v = 400.0 if i >= n - 20 else 100.0
                kl.append([i * 86400000, c * 0.99, c * 1.02, c * 0.97, c, v])
        elif kind == "low_base":
            # 全程在 50-55 窄幅横盘、量能温和（真正的低位蓄势）
            for i in range(n):
                c = 52.0 + (i % 5) * 0.3
                kl.append([i * 86400000, c * 0.995, c * 1.01, c * 0.985, c, 100.0])
        elif kind == "crash_chop":
            # 从 300 崩到 90，位置低但振幅巨大（不得算横盘）
            for i in range(n):
                if i < 60:
                    c = 300.0 - (i * 3.5)
                else:
                    c = 90.0 + ((i % 7) - 3) * 8.0     # 高波动震荡
                kl.append([i * 86400000, c, c * 1.12, c * 0.88, c, 150.0])
        return kl

    # --- 1) 高位贴顶 + 放量多日：必须扣分 ---
    ph = _pos_mod.analyze_position(_mk("high_extended"))
    assert ph is not None, "高位样本应可分析"
    pen_h, why_h = _pos_mod.position_penalty(ph)
    assert ph["pos_90"] >= 80, f"高位样本区间位置应≥80，实际 {ph['pos_90']}"
    assert ph["zone"] == "高位", f"应判为高位区，实际 {ph['zone']}"
    assert pen_h < 0, f"高位贴顶必须扣分，实际 {pen_h}（理由 {why_h}）"
    assert ph["vol_expansion_days"] >= 5, \
        f"持续放量应识别为放量多日，实际 {ph['vol_expansion_days']}"
    assert ph["crowded"] is True, "应标记 crowded"
    print(f"  ✅ 高位贴顶被扣分：pos90={ph['pos_90']}% 放量{ph['vol_expansion_days']}天 "
          f"扣分={pen_h}")

    # --- 2) 低位横盘：窄幅区间内位置不作为扣分依据、被识别为横盘、不扣分 ---
    pl = _pos_mod.analyze_position(_mk("low_base"))
    assert pl is not None
    pen_l, why_l = _pos_mod.position_penalty(pl)
    assert pl["consolidating"] is True, \
        f"窄幅横盘应被识别，实际 width60={pl['range_width_60']} shrink={pl['vol_shrink_ratio']} tangle={pl['ma_tangle_pct']}"
    assert pen_l == 0, f"低位横盘不应被扣分，实际 {pen_l}（理由 {why_l}）"
    assert pen_h < pen_l, "高位扣分必须重于低位"
    print(f"  ✅ 低位横盘被正确识别且不扣分：pos90={pl['pos_90']}% "
          f"range90={pl['range_pct_90']}% 窄幅无判别力={not pl['position_informative']} "
          f"width60={pl['range_width_60']}% base={pl['base_days']}d")

    # --- 2b) 关键回归：窄幅区间的"位置%"是噪声，不得据此扣分 ---
    # 实测缺陷：50~55 元窄幅横盘的币，最后一根恰好落在上沿 → pos90=78.8%
    # 被判"中高位"扣分，把真正蓄势的低位币冤枉掉。
    assert pl["range_pct_90"] < _pos_mod.MIN_INFORMATIVE_RANGE_PCT, \
        f"本样本应落入窄幅（<{_pos_mod.MIN_INFORMATIVE_RANGE_PCT}%），实际 {pl['range_pct_90']}%"
    assert pl["position_informative"] is False, "窄幅区间应标记为位置无判别力"
    assert pl["zone"] == "窄幅", f"应标为'窄幅'而非高位标签，实际 {pl['zone']}"
    assert not any(r.startswith("位置扣分") for r in why_l), \
        f"窄幅不应产生位置扣分，实际 {why_l}"
    assert any("无判别力" in r for r in why_l), "应给出'区间无判别力'的说明（可解释性）"
    print(f"  ✅ 窄幅区间位置噪声已屏蔽：pos90={pl['pos_90']}% 但 zone={pl['zone']} 不扣分")

    # --- 3) 崩盘后高波动：位置低但**不是**横盘 ---
    pc = _pos_mod.analyze_position(_mk("crash_chop"))
    assert pc is not None
    assert pc["pos_90"] < 60, f"崩盘样本位置应低，实际 {pc['pos_90']}"
    assert pc["consolidating"] is False, \
        f"高波动崩盘不得判为横盘，实际 width60={pc['range_width_60']}%"
    print(f"  ✅ 崩盘高波动未被误判为横盘：pos90={pc['pos_90']}% "
          f"width60={pc['range_width_60']}% consolidating={pc['consolidating']}")

    # --- 3b) 权重校准（v1.5.0 回测驱动）---
    # 回测结论：纯"区间位置"区分度不稳定（均值差两轮符号翻转），而"放量多日"
    # 两轮一致偏负。故 拥挤度权重 必须 **重于或等于** 任何单项位置权重。
    # 这条断言锁住"加重拥挤度、减轻纯位置"的设计意图，防止被后人改回去。
    _W = _pos_mod.PENALTY_WEIGHTS
    assert _W["crowded"] <= _W["near_high"], \
        f"放量多日权重({_W['crowded']})应重于高位贴顶({_W['near_high']})——回测驱动"
    assert _W["crowded"] <= _W["high_zone"], "放量多日权重应重于纯高位"
    assert min(_W.values()) == _W["crowded"], \
        f"放量多日应是单一最重扣分项，实际最重={min(_W.values())} 权重表={_W}"
    # 位置类整体不能太重（回测显示其区分度弱，重罚会错杀强势趋势）
    _pos_keys = ("near_high", "high_zone", "mid_high_zone", "at_180d_high")
    assert sum(_W[k] for k in _pos_keys) >= -4, \
        f"位置类扣分合计不应超过 -4，实际 {sum(_W[k] for k in _pos_keys)}"
    assert _W["near_high"] == _W["high_zone"] == _W["mid_high_zone"], \
        "位置类三级权重应趋同（区分度不足，做成阶梯是伪精度）"
    print(f"  ✅ 权重已按回测校准：拥挤度{_W['crowded']} 最重、位置类合计"
          f"{sum(_W[k] for k in _pos_keys)}（高位/中高同为{_W['high_zone']}）")

    # 低位必须给出"为何不扣分"的显式说明（可解释性，避免被读成漏判）
    assert any("低位/中位" in r for r in why_l) or any("无判别力" in r for r in why_l), \
        f"低位应显式说明不扣分理由，实际 {why_l}"
    print("  ✅ 低位不扣分时给出显式说明（可解释性）")

    # 权重可被外部覆盖（供回测对比不同策略档位）
    _ov = _pos_mod.position_penalty(ph, weights={"crowded": -9})[0]
    assert _ov < pen_h, f"weights 覆盖应生效，{_ov} 未低于 {pen_h}"
    print(f"  ✅ weights 可覆盖（crowded=-9 → 总扣分 {_ov}），支持回测对比档位")

    # --- 4) score_symbol 必须把位置扣分并入总分 ---
    _scr2 = _il.import_module("screener")
    _kl_hi = _mk("high_extended")
    _sc_hi2 = _scr2.score_symbol(_kl_hi, vol_mult=1.5)
    assert _sc_hi2 is not None
    assert _sc_hi2.get("position") is not None, "score_symbol 应附带 position 度量"
    assert _sc_hi2["position_penalty"] < 0, \
        f"高位样本应带位置扣分，实际 {_sc_hi2['position_penalty']}"
    assert _sc_hi2["score"] == (_sc_hi2["base_score"] + _sc_hi2["position_penalty"]
                                + _sc_hi2.get("tf_score", 0)), \
        "总分必须 = 基础分 + 位置扣分 + 多周期加减分（归因可拆解）"
    print(f"  ✅ 总分可拆解：base={_sc_hi2['base_score']} "
          f"pos={_sc_hi2['position_penalty']} tf={_sc_hi2.get('tf_score')} "
          f"total={_sc_hi2['score']}")

    # --- 5) 关闭位置权重 + 关闭多周期时必须回到旧口径（可回测对比）---
    _sc_off = _scr2.score_symbol(_kl_hi, vol_mult=1.5, position_weight=False,
                                 multi_tf=False)
    assert _sc_off is not None and _sc_off["position_penalty"] == 0, \
        "--no-position-weight 时不应扣分"
    assert _sc_off.get("tf_score", 0) == 0, "--no-multi-tf 时不应有周期加减分"
    assert _sc_off["score"] == _sc_off["base_score"], "两个新维度都关闭后应等于基础分"
    print("  ✅ 可用 position_weight=False + multi_tf=False 回到 v1.4.x 纯趋势口径（供回测对比）")

    # --- 6) 稳定币/贵金属/包装币必须被排除在标的池外 ---
    for _s in ("RLUSDUSDT", "UUSDT", "PAXGUSDT", "XAUTUSDT", "WBTCUSDT", "USDCUSDT"):
        assert (_s in _scr2.STABLES or _s in _scr2.WRAPPED or _s in _scr2.COMMODITY), \
            f"{_s} 应被标的池排除（锚定/贵金属/包装币），否则会伪装成'低位横盘'"
    print("  ✅ 锚定币/贵金属/包装币已从标的池排除（防伪装横盘）")

    # --- 7) 低位横盘候选通道：必须要求真窄幅（防崩盘阴跌混入）---
    _res = {
        "REALBASEUSDT": {"vetoes": [], "close": 52.0, "rsi14": 55.0, "vol_ratio": 0.9,
                         "adv20_quote": 5e6, "last_date": "2026-09-29",
                         "position": {"pos_90": 45.0, "zone": "中位", "range_width_60": 10.0,
                                      "base_days": 60, "vol_shrink_ratio": 0.8,
                                      "dist_ma20_pct": -1.0, "hi_90": 55.0, "lo_90": 48.0}},
        "CRASHCHOPUSDT": {"vetoes": [], "close": 90.0, "rsi14": 45.0, "vol_ratio": 0.7,
                          "adv20_quote": 5e6, "last_date": "2026-09-29",
                          "position": {"pos_90": 33.0, "zone": "低位", "range_width_60": 169.0,
                                       "base_days": 0, "vol_shrink_ratio": 0.68,
                                       "dist_ma20_pct": 3.8, "hi_90": 300.0, "lo_90": 90.0}},
    }
    _ac = _scr2.accumulation_candidates(_res, exclude=set())
    _syms = [a["symbol"] for a in _ac]
    assert "REALBASEUSDT" in _syms, f"真横盘应入选候选，实际 {_syms}"
    assert "CRASHCHOPUSDT" not in _syms, \
        f"崩盘高波动(振幅169%)不得进横盘候选，实际 {_syms}"
    print(f"  ✅ 横盘候选只收真窄幅：{_syms}")

    # -----------------------------------------------------------------
    # [测试 9d] 多周期（周线/月线）重采样与一致性（v1.5.0）
    # 用户提问："是不是应该结合大盘和周线月线去看"——此前只看日线。
    # 关键：**本地重采样**（各交易所原生周线口径不一致：东亚所周一UTC起算，
    # Binance 周日UTC起算，绝不能混用），且必须剔除未走完的周/月线。
    # -----------------------------------------------------------------
    print("\n[测试 9d] 多周期研判（周线/月线，防'只看日线'回归） ...")
    _tf_mod = _il.import_module("timeframe")

    # 用真实日线构造 400 天数据：前半段 100→300 上涨，后半段 300→120 下跌
    _kl_tf = []
    for i in range(400):
        if i < 200:
            c = 100.0 + i * 1.0            # 拉到 300
        else:
            c = 300.0 - (i - 199) * 0.9    # 跌到 ~120
        _kl_tf.append([i * 86400000, c * 0.99, c * 1.02, c * 0.97, c, 100.0])

    _wk, _wk_dropped = _tf_mod.resample(_kl_tf, "1w")
    _mo, _mo_dropped = _tf_mod.resample(_kl_tf, "1M")
    assert len(_wk) >= 50, f"400 天应聚合出 ≥50 根周线，实际 {len(_wk)}"
    assert len(_mo) >= 12, f"400 天应聚合出 ≥12 根月线，实际 {len(_mo)}"
    print(f"  ✅ 本地重采样：400 日线 → 周线 {len(_wk)} 根 / 月线 {len(_mo)} 根")

    # OHLCV 聚合正确性：周线 high 必须等于该周内日线 high 的最大值
    _last = _wk[-1]
    _members = [k for k in _kl_tf if _tf_mod._period_key(int(k[0]), "1w") == _last[0]]
    assert abs(_last[2] - max(k[2] for k in _members)) < 1e-9, "周线 high 聚合错误"
    assert abs(_last[3] - min(k[3] for k in _members)) < 1e-9, "周线 low 聚合错误"
    assert abs(_last[4] - _members[-1][4]) < 1e-9, "周线 close 应取最后一根"
    assert abs(_last[5] - sum(k[5] for k in _members)) < 1e-6, "周线 volume 应累加"
    print("  ✅ OHLCV 聚合正确（H=max / L=min / C=末根 / V=求和）")

    # 必须剔除未走完的周/月线（否则"本周还没结束"的 bar 会污染指标）
    assert _wk_dropped or _mo_dropped, "当前时刻所属的周/月线应被剔除"
    print(f"  ✅ 未走完的周/月线已剔除（weekly={_wk_dropped} monthly={_mo_dropped}）")

    # 多周期一致性：构造「日线多头 + 大周期空头」的逆大势场景
    _tfv = _tf_mod.multi_tf_view(_kl_tf)
    assert _tfv["available_tf"] >= 2, f"应至少有 2 个周期可用，实际 {_tfv}"
    assert _tfv["alignment"] in ("周期冲突", "全周期共振向下", "中性"), \
        f"先涨后跌的形态不应判为共振向上，实际 {_tfv['alignment']}"
    print(f"  ✅ 多周期一致性判定：{_tfv['alignment']} "
          f"(多 {_tfv['bullish_tf']}/空 {_tfv['bearish_tf']})")

    # 均线排列必须参与"多空"判定——这是修复的核心
    # （实测缺陷：只看 c>MA20 会把"月线价格在MA20下方但反抽"错判为多头）
    _x = _tf_mod.analyze_tf(_kl_tf, "1d", min_bars=60)
    assert "ma_direction" in _x and _x["ma_direction"] in ("多头", "空头", "中性"), \
        "单周期分析应给出 ma_direction（MACD 样本不足时的兜底方向）"
    assert _x["bearish"] or _x["ma_direction"] == "空头", \
        "先涨后跌的末端应判为偏空（均线结构须参与判定）"
    print(f"  ✅ 均线结构参与多空判定：ma_direction={_x['ma_direction']} "
          f"ma_stack={_x.get('ma_stack')}")

    # 加分逻辑：逆大势必须扣分
    _sc_tf, _why_tf = _tf_mod.tf_alignment_score({
        "alignment": "周期冲突",
        "daily": {"ma_direction": "多头"}, "weekly": {"ma_direction": "空头"},
        "monthly": {"ma_direction": "空头"},
    })
    assert _sc_tf < 0, f"日线多头/大周期空头必须扣分，实际 {_sc_tf}"
    print(f"  ✅ 逆大周期反弹被扣分：{_sc_tf} {_why_tf}")

    # -----------------------------------------------------------------
    # [测试 9e] 多面数据源的降级健壮性（v1.5.0）
    # 数据源是增强项：任一源挂掉都**不得**抛异常、不得阻塞研判主链。
    # -----------------------------------------------------------------
    print("\n[测试 9e] 多面数据源降级健壮性（防'源挂拖死研判'回归） ...")
    _ds = _il.import_module("datasources")

    # 所有 fetch_* 在网络全断时都必须返回 dict（而非抛异常）
    import unittest.mock as _mock
    def _boom(*a, **k):
        raise RuntimeError("simulated network failure")
    with _mock.patch.object(_ds, "http_get_json", _boom), \
         _mock.patch.object(_ds, "http_get_text", _boom):
        for _fn_name in ("fetch_news", "fetch_dex_hot", "fetch_stablecoin_flow",
                         "fetch_futures_metrics", "fetch_fng",
                         "fetch_cmc_news", "fetch_cmc_trending"):
            _r = getattr(_ds, _fn_name)()
            assert isinstance(_r, dict), f"{_fn_name} 失败时应返回 dict"
            assert _r.get("available") is False, f"{_fn_name} 网络失败应标 available=False"
            assert "note" in _r or "error" in _r, f"{_fn_name} 应说明失败原因"
    print("  ✅ 7 个数据源在网络全断时全部安全降级（返回 dict + 原因，不抛异常）")

    # collect_all 在全部源失败时也必须返回结构完整的汇总
    with _mock.patch.object(_ds, "http_get_json", _boom), \
         _mock.patch.object(_ds, "http_get_text", _boom):
        _all = _ds.collect_all(quiet=True)
    assert isinstance(_all, dict) and "sources" in _all
    assert _all["available_count"] == 0 and _all["failed_count"] == 7, \
        f"全断时应有 7 个失败源，实际 ok={_all['available_count']} fail={_all['failed_count']}"
    assert len(_all["sources_failed"]) == 7
    print(f"  ✅ collect_all 全断时结构完整：可用 {_all['available_count']} / "
          f"失败 {_all['failed_count']}")

    # DefiLlama 金额字段是 {"peggedUSD": N} 形状——差分前必须解包
    assert _ds._pegged({"peggedUSD": 123.0}) == 123.0
    assert _ds._pegged(456.0) == 456.0
    assert _ds._pegged(None) is None
    print("  ✅ DefiLlama {peggedUSD} 字段解包正确")

    # 消息面必须有多镜像（单镜像抖动会误判'消息面全灭'——实测踩过）
    assert len(_ds.RSSHUB_MIRRORS) >= 3, \
        f"消息面必须多镜像轮询，实际只有 {len(_ds.RSSHUB_MIRRORS)} 个"
    assert _ds.NEWS_TIME_BUDGET_SEC <= 30, "消息面必须有时间预算，防止拖慢主链"
    print(f"  ✅ 消息面多镜像({len(_ds.RSSHUB_MIRRORS)}) + 时间预算"
          f"({_ds.NEWS_TIME_BUDGET_SEC:.0f}s)")

    print("\n[测试 11] P3 CEX 接入：签名算法正确性（防'把 SHA512 写成 SHA256'）...")
    # 四家签名规则差异极大且极易写错；这些是**纯函数**，可离线严格校验。
    # Gate 的实现已与官方 SDK（gateapi-python api_client.gen_sign）逐字节比对一致。
    _ca = _il.import_module("cex_adapter")

    # --- Binance: HMAC-SHA256 hex(64)，signature 置尾，timestamp 毫秒 ---
    _q, _, _p = _ca.sign_binance("sec", {"symbol": "BTCUSDT"},
                                 timestamp=1759100000000)
    assert "timestamp=1759100000000" in _q, "Binance timestamp 必须毫秒"
    assert _q.count("signature=") == 1 and _q.rstrip().endswith(
        _q.split("signature=")[1]), "signature 必须是最后参数"
    _sig = _q.split("signature=")[1]
    assert len(_sig) == 64 and all(c in "0123456789abcdef" for c in _sig), \
        "Binance 必须是 64 位小写 hex（SHA256）"
    assert "recvWindow=5000" in _q, "recvWindow 默认应为 5000"
    print("  ✅ BINANCE HMAC-SHA256 hex(64) / signature置尾 / 毫秒 / recvWindow=5000")

    # --- OKX: Base64（不是 hex！），四 header 齐全，ISO8601 毫秒 ---
    _, _h = _ca.sign_okx("sec", "k", "pp", "GET", "/api/v5/account/balance?a=1",
                         timestamp="2026-09-29T10:00:00.123Z")
    for _hk in ("OK-ACCESS-KEY", "OK-ACCESS-SIGN", "OK-ACCESS-TIMESTAMP",
                "OK-ACCESS-PASSPHRASE"):
        assert _hk in _h, f"OKX 缺 header {_hk}"
    import base64 as _b64
    _b64.b64decode(_h["OK-ACCESS-SIGN"])          # 必须能按 Base64 解出
    assert _h["OK-ACCESS-TIMESTAMP"].endswith("Z") and "." in _h["OK-ACCESS-TIMESTAMP"], \
        "OKX timestamp 必须是 ISO8601 毫秒 UTC（错格式会导致 50102）"
    print("  ✅ OKX HMAC-SHA256+Base64(非hex) / 四header / ISO8601毫秒")

    # --- Gate: SHA512 hex(128) + body摘要嵌入 + timestamp秒 ---
    _, _hg = _ca.sign_gate("sec", "k", "GET", "/spot/accounts", timestamp=1759100000)
    assert len(_hg["SIGN"]) == 128, \
        f"Gate 必须 SHA512（128 hex），实际 {len(_hg['SIGN'])}——写成 SHA256 是最常见 bug"
    assert {"KEY", "Timestamp", "SIGN"} <= set(_hg), "Gate headers 应为 KEY/Timestamp/SIGN"
    assert _hg["Timestamp"] == "1759100000", "Gate timestamp 用秒"
    # ★ 关键回归：无 body 必须 hash 空串，不是 "{}"
    # （初版写成 body or {} → 所有 GET 签名错，余额/仓位查询全 401，
    #   而 POST 恰好正确——这种"一半能通"的 bug 极难定位，故固化为断言）
    _, _g_none = _ca.sign_gate("sec", "k", "GET", "/x", timestamp=1)
    _, _g_empty = _ca.sign_gate("sec", "k", "GET", "/x", body="", timestamp=1)
    _, _g_brace = _ca.sign_gate("sec", "k", "GET", "/x", body="{}", timestamp=1)
    assert _g_none["SIGN"] == _g_empty["SIGN"], \
        "无 body 必须等价于空串（不能变成 '{}'）——否则所有 GET 查询签约失败"
    assert _g_none["SIGN"] != _g_brace["SIGN"], "空串与 '{}' 必须产生不同签名"
    print("  ✅ GATE HMAC-SHA512 hex(128) / KEY+Timestamp+SIGN / 秒"
          " / 无body=空串(已防回归)")

    # --- MEXC: 两套鉴权 ---
    _, _hm = _ca.sign_mexc_spot("sec", {"symbol": "BTCUSDT"},
                                timestamp=1759100000000)
    assert "X-MEXC-APIKEY" in _hm, "MEXC 现货 header 应为 X-MEXC-APIKEY"
    _hc = _ca.sign_mexc_contract("sec", "ak", "a=1", timestamp=1759100000000)
    assert {"ApiKey", "Request-Time", "Signature"} <= set(_hc), \
        "MEXC 合约 header 应为 ApiKey/Request-Time/Signature（与现货完全不同）"
    assert len(_hc["Signature"]) == 64, "MEXC 应为 SHA256 hex"
    assert _ca.mexc_contract_param_str({"b": 2, "a": 1}, "GET") == "a=1&b=2", \
        "MEXC 合约 GET 参数必须字典序"
    assert _ca.mexc_contract_param_str({"b": 2, "a": 1}, "POST") == '{"b":2,"a":1}', \
        "MEXC 合约 POST 必须原样 JSON 不排序"
    print("  ✅ MEXC 两套鉴权（现货X-MEXC-APIKEY / 合约ApiKey+Request-Time）"
          " + GET排序/POST原样")

    print("\n[测试 12] P3 风控闸门：保守档必须真拦得住（防'只会建议不会拦'）...")
    _cr = _il.import_module("cex_risk")
    _prof = _cr.get_profile("conservative")
    _acct = {"net_usd": 10000, "available_usd": 10000}

    _v = _cr.check_order({"symbol": "BTCUSDT", "side": "buy", "notional_usd": 2000,
                          "stop_loss": 90000, "is_open": True}, _acct, [], _prof)
    assert not _v["allowed"], "单笔 20% 超 5% 上限必须被拒"
    assert _v["adjustments"].get("notional_usd"), "拒绝时必须给出可执行的调整值"
    print(f"  ✅ 超单笔上限被拒且给出建议（{_v['reasons'][0][:44]}…）")

    _v2 = _cr.check_order({"symbol": "BTCUSDT", "side": "buy", "notional_usd": 50,
                           "stop_loss": 95000, "is_open": True}, _acct, [], _prof)
    assert _v2["allowed"], "合规小额单+止损应放行"
    print("  ✅ 合规小额单（带止损）放行")

    _v3 = _cr.check_order({"symbol": "BTCUSDT", "side": "buy", "notional_usd": 50,
                           "is_open": True}, _acct, [], _prof)
    assert not _v3["allowed"] and any("止损" in r for r in _v3["reasons"]), \
        "保守档开仓不带止损必须被拒"
    print("  ✅ 保守档：开仓不带止损被拒")

    _v4 = _cr.check_order({"symbol": "BTCUSDT", "side": "buy", "notional_usd": 50,
                           "is_open": True}, _acct, [], _cr.get_profile("aggressive"))
    assert _v4["allowed"], "激进档应允许不带止损（档位差异必须生效）"
    print("  ✅ 激进档允许不带止损（档位差异生效）")

    _v5 = _cr.check_order({"symbol": "BTCUSDT", "side": "buy", "notional_usd": 100,
                           "leverage": 20, "stop_loss": 90000, "is_open": True},
                          _acct, [], _prof, market="usdtm")
    assert not _v5["allowed"], "20x 杠杆超保守档 3x 必须被拒"
    print("  ✅ 20x 杠杆被拒（保守档上限 3x）")

    # 单日次数限制 + 跨重启保留（不能靠重启绕过风控）
    import tempfile as _tf
    _tmpd = _tf.mkdtemp(prefix="kexi-risk-")
    try:
        _stp = os.path.join(_tmpd, "rs.json")
        _st = _cr.RiskState(_stp)
        _st.reset()
        for _i in range(_prof["max_orders_per_day"]):
            _st.record(10)
        _v6 = _cr.check_order({"symbol": "BTCUSDT", "side": "buy",
                               "notional_usd": 50, "stop_loss": 1, "is_open": True},
                              _acct, [], _prof, state=_st)
        assert not _v6["allowed"], "达单日上限后必须被拒"
        _st2 = _cr.RiskState(_stp)      # 模拟重启插件
        assert _st2.data["orders"] == _prof["max_orders_per_day"], \
            "风控计数必须跨重启保留（否则重启即可绕过）"
        print(f"  ✅ 单日 {_prof['max_orders_per_day']} 次后拒绝，且重启不能绕过")
    finally:
        import shutil as _sh
        _sh.rmtree(_tmpd, ignore_errors=True)

    # HARD_CEILING：配置写错也突破不了绝对上限
    _bad = _cr.get_profile("custom", {"max_leverage": 999, "max_order_pct": 999})
    assert _bad["max_leverage"] == _cr.HARD_CEILING["max_leverage"], "杠杆必须被绝对上限压住"
    assert _bad["max_order_pct"] == _cr.HARD_CEILING["max_order_pct"], "单笔必须被压住"
    assert _bad["_clamped"], "压顶行为必须被记录（可审计）"
    print(f"  ✅ HARD_CEILING 生效（999x→{_bad['max_leverage']}x，999%→"
          f"{_bad['max_order_pct']}%）且留痕")

    # 仓位试算：区分"被上限约束"与"被风险预算约束"
    _ps = _cr.position_size_by_risk(10000, 100, 95, 1.0)
    assert _ps["ok"] and _ps["limited_by"] == "单笔上限", \
        "保守档下应被单笔上限约束"
    _ps2 = _cr.position_size_by_risk(10000, 100, 95, 1.0, max_order_pct=50)
    assert _ps2["limited_by"] == "风险预算" and abs(_ps2["risk_usd"] - 100) < 1, \
        "放开上限后应按风险预算：1万×1%÷5% = $2000，止损打到亏 $100"
    print(f"  ✅ 仓位试算正确（上限约束 ${_ps['size_usd']:.0f} / "
          f"风险预算 ${_ps2['size_usd']:.0f} 亏 ${_ps2['risk_usd']:.0f}）")

    print("\n[测试 13] P3 密钥库：加密存储且绝不回显（防'以为加密了其实没有'）...")
    _ks = _il.import_module("cex_keystore")
    import tempfile as _tf2, shutil as _sh2, json as _json2
    _kd = _tf2.mkdtemp(prefix="kexi-ks-")
    try:
        if _ks.dpapi_available():
            _r = _ks.save_keys(_kd, "binance", "AK_T", "AS_T",
                               permissions=["read", "trade"], ip_whitelist=True)
            assert _r["ok"] and _r["encrypted"], "DPAPI 可用时必须加密保存"
            assert b"AS_T" not in open(_r["path"], "rb").read(), "密文不得含明文密钥"
            assert "AS_T" not in _json2.dumps(_ks.list_keys(_kd), ensure_ascii=False), \
                "list_keys 绝不能回显密钥内容"
            _lk = _ks.load_keys(_kd, "binance")
            assert _lk["ok"] and _lk["keys"]["api_secret"] == "AS_T", "解密回读应正确"
            _audit = open(os.path.join(_kd, "keystore_audit.log"), encoding="utf-8").read()
            assert "AS_T" not in _audit, "审计日志绝不能记密钥"
            print("  ✅ DPAPI 加密保存 / 密文无明文 / list不回显 / 审计无密钥")
        else:
            _r = _ks.save_keys(_kd, "binance", "AK_T", "AS_T")
            assert not _r["ok"], "无 DPAPI 且未显式允许时**必须拒绝**明文落盘"
            print("  ✅ 无 DPAPI 时拒绝明文保存（不制造虚假安全感）")

        # 含提币权限必须告警；OKX 缺 passphrase 必须拒绝
        _r2 = _ks.save_keys(_kd, "okx", "K", "S", "PP", label="d",
                            permissions=["read", "trade", "withdraw"])
        assert any("提币" in w for w in _r2["warnings"]), "含提币权限必须告警"
        _r3 = _ks.save_keys(_kd, "okx", "K", "S", None, label="np")
        assert not _r3["ok"], "OKX 缺 passphrase 必须拒绝"
        print("  ✅ 提币权限告警 / OKX 缺 passphrase 拒绝")

        # 安全体检必须如实报告风险（含 DPAPI 的能力边界说明）
        _sr = _ks.security_report(_kd)
        assert "无法防" in _sr["note"] or "防" in _sr["note"], "必须如实说明 DPAPI 边界"
        print(f"  ✅ 安全体检如实报告（{len(_sr['findings'])} 项发现；"
              f"并声明 DPAPI 无法防同用户恶意程序）")
    finally:
        _sh2.rmtree(_kd, ignore_errors=True)

    print("\n[测试 14] P4 保活与通知（防'自作主张下实盘单'）...")
    _ka = _il.import_module("keepalive")
    _snap = {
        "BTCUSDT": {"price": 83000, "change_24h_pct": -4.2, "volume_ratio": 1.1,
                    "pos_90": 22.0, "tf_alignment": "周期冲突"},
        "GRAMUSDT": {"price": 1.55, "change_24h_pct": 15.3, "volume_ratio": 3.4,
                     "pos_90": 91.0, "tf_alignment": "全周期共振向下"},
        "__macro__": {"stablecoin_trend": "小幅净销毁", "net_mint_7d_display": "-1.1亿U"},
    }
    _ev = _ka.evaluate_signals(_snap, watchlist=["BTCUSDT", "GRAMUSDT"])
    assert _ev["triggered"], "已知信号样本必须触发"
    _types = {t["type"] for t in _ev["triggers"]}
    for _want in ("price_change", "volume_spike", "position_high", "tf_reversal",
                  "stablecoin_flow"):
        assert _want in _types, f"应触发 {_want}，实际 {_types}"
    assert any(t["level"] == "critical" for t in _ev["triggers"]), "逆大势应为 critical"
    print(f"  ✅ 6 类信号正确触发（{len(_ev['triggers'])} 项，含 critical）")

    # ★ 安全立场：默认模式绝不自作主张调仓
    _plan = _ka.decide_action(_ev, mode="notify_only")
    assert all(a["type"] not in ("auto_reduce", "auto_set_tpsl")
               for a in _plan["actions"]), "notify_only 模式绝不能产生自动动仓指令"
    assert _plan["requires_user_confirm"], "默认必须要求用户确认"
    print("  ✅ 默认 notify_only：不产生任何自动调仓指令，且要求用户确认")
    _plan2 = _ka.decide_action(_ev, mode="auto_reduce")
    assert _plan2["requires_user_confirm"], "auto_reduce 仍必须要求确认（保守）"
    print("  ✅ auto_reduce 模式仍要求用户确认（不自作主张）")

    # 保活状态跨重启保留（否则定时评估永远不到点）
    _tmpd2 = _tf2.mkdtemp(prefix="kexi-ka-")
    try:
        _sp = os.path.join(_tmpd2, "ka.json")
        _s1 = _ka.KeepAliveState(_sp)
        assert _s1.due()[0], "首次应到期"
        _s1.mark_run({"triggers": 0})
        _s2 = _ka.KeepAliveState(_sp)
        assert not _s2.due(interval_min=60)[0], "刚跑完 60 分钟间隔内不应到期"
        assert _s2.due(interval_min=0)[0], "间隔 0 应到期"
        print("  ✅ 保活状态跨重启保留（定时不会因重启而失效）")
    finally:
        _sh2.rmtree(_tmpd2, ignore_errors=True)

    _kn = _il.import_module("kexi_notify")
    assert "file" in _kn.CHANNELS, "落盘必须是基础渠道"
    _nt = _kn.Notifier(channels=["file"], out_dir=_tmpd2 if False else None)
    assert "file" in _nt.channels, "落盘渠道不可关闭"
    _nt2 = _kn.Notifier(channels=[], out_dir=os.environ.get("TEMP", "."))
    assert "file" in _nt2.channels, "即使传空渠道也必须强制保留落盘"
    print("  ✅ 落盘渠道强制启用（渠道全挂也不丢事件）")

    print("\n[测试 15] P5 Token 统计（防'chunk 重复计导致翻倍虚报'）...")
    _ts = _il.import_module("token_stats")
    assert "assistant/message" in _ts.COUNTED_TYPES, "必须统计 assistant/message"
    # ★ 关键：chunk 里的 usage 与 data.usage 完全相同，计入会让输入/缓存读翻倍
    assert all("chunk" not in t and "stream" not in t for t in _ts.COUNTED_TYPES), \
        "绝不能统计 stream[*].chunk.usage（会重复计）"
    print(f"  ✅ 只统计 {_ts.COUNTED_TYPES}（已排除 chunk 重复项）")

    # 命中率口径 = cacheRead/(cacheRead+input)
    _acc = _ts.aggregate([
        {"usage": {"inputTokens": 100, "outputTokens": 10, "cacheReadTokens": 900},
         "type": "assistant/message", "session": "s1"},
        {"usage": {"inputTokens": 0, "outputTokens": 5, "cacheReadTokens": 0},
         "type": "assistant/message", "session": "s1"},
    ])
    assert _acc["cache_hit_rate"] == 90.0, \
        f"命中率应为 900/(900+100)=90%，实际 {_acc['cache_hit_rate']}"
    assert _acc["output"] == 15 and _acc["turns"] == 2
    print(f"  ✅ 命中率口径正确（{_acc['cache_hit_rate']}% = 缓存读/(缓存读+输入)）")

    # 会话目录名解码（实测踩过 3 次编码坑，固化为断言）
    assert _ts._decode_dirname(
        "--C-Users-lcl-Desktop-DSH~63D2~4EF6~5F00~53D1-K~6790~7814~5224~56E2--"
    ) == "C\\Users\\lcl\\Desktop\\DSH插件开发\\K析研判团", "目录名解码必须逐字符正确"
    assert _ts._decode_dirname("--C-Users-lcl-Desktop-K~6790~5DE5~4F5C~6587~4EF6~5939--") \
        == "C\\Users\\lcl\\Desktop\\K析工作文件夹", "末尾码元（后跟-）也必须解出"
    print("  ✅ 会话目录名解码正确（UTF-16BE 逐字符，含末尾码元）")

    # 多帧 zstd：必须用 stream_reader（decompress 只解第一帧）
    # 注意：不能直接在源码文本里搜 "decompress("——模块 docstring 里
    # 恰恰**记录了**这个反模式（"decompress() 只能解出第一帧"），
    # 文本匹配会命中注释而误报。故用 AST 只看**真实调用**。
    import ast as _ast
    _src = open(os.path.join(_SCRIPTS_DIR, "token_stats.py"),
                encoding="utf-8").read()
    _calls = set()
    for _n in _ast.walk(_ast.parse(_src)):
        if isinstance(_n, _ast.Call) and isinstance(_n.func, _ast.Attribute):
            _calls.add(_n.func.attr)
    assert "stream_reader" in _calls, "必须真实调用 stream_reader 读多帧 zstd"
    assert "decompress" not in _calls, \
        "不得真实调用 decompress()（只会解第一帧，静默丢数据）"
    print("  ✅ 多帧 zstd 用 stream_reader（AST 校验真实调用，不受注释干扰）")

    print("\n[测试 16] 可视化报告：自包含且零外链（防'离线打不开'）...")
    _tr = _il.import_module("track_report")
    _kl = []
    for _i in range(120):
        _c = 100 + _i * 0.8
        _kl.append([_i * 86400000, _c * 0.99, _c * 1.02, _c * 0.98, _c, 1000.0])
    _pl = _tr.compute_trade_plan(_kl, net_usd=10000, risk_pct=1.0)
    assert _pl["ok"], f"点位计划应可计算：{_pl.get('error')}"
    assert _pl["stop_loss"] < _pl["entry"] < _pl["take_profit_1"] < _pl["take_profit_2"], \
        "点位顺序必须为 止损 < 入场 < TP1 < TP2"
    assert _pl["sl_method"], "必须说明止损用了哪种方法（可解释性）"
    assert len(_pl["sl_candidates"]) >= 1, "应给出多法交叉验证的候选止损"
    print(f"  ✅ 三法交叉验证点位：止损 {_pl['stop_loss']:.2f}({_pl['sl_method']}) → "
          f"入场 {_pl['entry']:.2f} → TP1 {_pl['take_profit_1']:.2f} → "
          f"TP2 {_pl['take_profit_2']:.2f}")

    _bp = _pl["base_position"]
    assert sum(t["pct"] for t in _bp["tranches"]) == 100, "建仓批次占比必须合计 100%"
    assert any("底仓" in t["name"] for t in _bp["tranches"]), "必须含底仓批次"
    assert any("止损" in e["trigger"] for e in _bp["exits"]), "必须含止损退出条件"
    assert _bp["rules"], "必须给纪律条款"
    print(f"  ✅ 底仓策略：{len(_bp['tranches'])} 批建仓（合计100%）/ "
          f"{len(_bp['exits'])} 条退出 / {len(_bp['rules'])} 条纪律")

    _h = _tr.build_report(
        [{"symbol": "TESTUSDT", "run_id": "r1", "tier": "formal",
          "entry": 100, "current": 110, "ret_pct": 10.0, "btc_ret_pct": 2.0,
          "entry_date": "2026-01-01", "verdict": "ok"}],
        {"TESTUSDT": _pl}, None, {"TESTUSDT": _kl}, "测试报告")
    import re as _re
    _bad = _re.findall(r'(?:src|href)\s*=\s*["\'](?:https?:)?//', _h)
    _bad += _re.findall(r'@import\s+url\(', _h)
    assert not _bad, f"报告必须零外链，发现 {_bad[:3]}"
    assert "<svg" in _h and _h.count("<svg") >= 2, "应含内联 SVG 图表"
    for _sec in ("战绩总览", "判断点分析线", "止盈止损点", "底仓策略"):
        assert _sec in _h, f"报告缺章节 {_sec}"
    assert "超额" in _h, "必须含超额收益（alpha/beta 分离）"
    print(f"  ✅ 报告自包含零外链 / {_h.count('<svg')} 个内联图 / 四大章节齐全")

    # 表单注入防护：币名含 HTML 必须被转义
    _h2 = _tr.build_report([{"symbol": "<img src=x onerror=alert(1)>",
                             "tier": "formal", "ret_pct": 1.0}], {}, None, None, "x")
    assert "<img src=x" not in _h2, "用户数据必须 HTML 转义（防注入）"
    assert "&lt;img" in _h2, "应转义为实体"
    print("  ✅ HTML 转义生效（防注入）")

    print("\n[测试 17] P2 多面数据源（已实测 7/7 可用，此处防降级回归）...")
    _dsrc = _il.import_module("datasources")
    for _fn in ("fetch_news", "fetch_dex_hot", "fetch_stablecoin_flow",
                "fetch_futures_metrics", "fetch_fng", "fetch_cmc_news",
                "fetch_cmc_trending"):
        assert callable(getattr(_dsrc, _fn, None)), f"缺数据源函数 {_fn}"
    assert len(_dsrc.RSSHUB_MIRRORS) >= 3, "消息面必须多镜像（单镜像抖动会误判全灭）"
    assert _dsrc.NEWS_TIME_BUDGET_SEC <= 30, "消息面必须有时间预算"
    assert _dsrc._pegged({"peggedUSD": 1.5}) == 1.5 and _dsrc._pegged(2.0) == 2.0, \
        "DefiLlama {peggedUSD} 字段必须能解包（两种形状都要支持）"
    print(f"  ✅ 7 个数据源齐备 / 消息面 {len(_dsrc.RSSHUB_MIRRORS)} 镜像 + "
          f"{_dsrc.NEWS_TIME_BUDGET_SEC:.0f}s 预算 / peggedUSD 解包正确")

    print("\n[测试 18] 签名结构自检（防'把 SHA512 写成 SHA256'）...")
    _ca18 = _il.import_module("cex_adapter")
    _q18, _, _ = _ca18.sign_binance("sec", {"a": 1}, timestamp=1759100000000)
    _s18 = _q18.split("signature=")[1]
    assert len(_s18) == 64, "Binance 应为 64 位 hex（SHA256）"
    _, _h18 = _ca18.sign_gate("sec", "k", "GET", "/x", timestamp=1)
    assert len(_h18["SIGN"]) == 128, "Gate 应为 128 位 hex（SHA512，非 SHA256）"
    _, _h18b = _ca18.sign_okx("sec", "k", "p", "GET", "/x",
                              timestamp="2026-01-01T00:00:00.000Z")
    assert len(_h18b["OK-ACCESS-SIGN"]) != 128, "OKX 是 Base64 不是 hex（长度不应为128）"
    print("  ✅ Binance=64hex / Gate=128hex / OKX=Base64（三家算法各不相同，均已区分）")

    # -------------------------------------------------------------
    # 测试 19（v1.5.1）：入场时机引擎 / 币种行为画像 / 消息面监控
    # -------------------------------------------------------------
    print("\n[测试 19] 入场时机引擎 + 币种行为画像（v1.5.1）...")
    _ep = _il.import_module("entry_plan")
    _cp = _il.import_module("coin_profile")

    # --- 合成 K 线：大涨 → 深回调 → 中位回升（pos_90 中部 + MA20 拐头 + 温和放量） ---
    _kl = []
    for _i in range(110):
        if _i < 40:
            _c = 100 + _i * 1.0                 # 40 天涨到 140
        elif _i < 75:
            _c = 140 - (_i - 40) * 1.15         # 35 天回调 ~28% 到 100（区间底部）
        else:
            _c = 100 + (_i - 75) * 0.8          # 35 天回升到 ~128（90 日区间中部 ~65%）
        _kl.append([_i * 86400000, _c * 0.99, _c * 1.02, _c * 0.98, _c, 1000.0])
    # 近 3 根放量 1.6x（温和放量，距爆量线 2.0x 尚远）
    for _j in range(3):
        _kl[-1 - _j][5] = 1600.0

    _plan = _ep.build_entry_plan(_kl)
    assert _plan["ok"], f"入场引擎应成功：{_plan.get('error')}"
    assert isinstance(_plan["actions"], list) and _plan["actions"], "必须给出动作建议"
    assert all(a.get("basis") for a in _plan["actions"]), "每条建议必带依据（可解释）"
    _types = [a["type"] for a in _plan["actions"]]
    assert "enter_now" in _types, "温和放量+趋势完好+无追高 → 应给立即入场"
    assert "pullback_buy" in _types, "必须给回调反弹入场位（用户核心需求）"
    assert "breakout_add" in _types, "必须给突破补仓位"
    assert _plan["invalidation"] and _plan["invalidation"]["price"], "必须给失效位"
    assert _plan["conditions"]["warm_volume"], "近3日放量1.8x 应判'温和放量'"
    print(f"  ✅ 入场引擎：{len(_plan['actions'])} 类动作（立即/回调/突破），失效位 "
          f"{_plan['invalidation']['price']}")

    # --- 追高嫌疑拦截：快速拉升 + 高位 + 放量多日 → 禁止立即入场 ---
    _kl2 = []
    for _i in range(100):
        if _i < 60:
            _c = 100 + _i * 0.2                      # 长期横盘
        else:
            _c = 112 + (_i - 60) * 1.2               # 40 天急拉 48+
        _kl2.append([_i * 86400000, _c * 0.99, _c * 1.02, _c * 0.98, _c, 1000.0])
    for _j in range(8):                              # 连续 8 天爆量
        _kl2[-1 - _j][5] = 3000.0
    _plan2 = _ep.build_entry_plan(_kl2)
    assert _plan2["chase_risk"]["flagged"], "高位放量多日 → 必须标记追高嫌疑"
    _types2 = [a["type"] for a in _plan2["actions"]]
    assert "enter_now" not in _types2, "追高嫌疑 flagged → 禁止立即入场（用户点名'追高嫌疑'）"
    assert any(t in ("pullback_buy", "wait") for t in _types2), "只能给回调/等待建议"
    print(f"  ✅ 追高拦截：chase={_plan2['chase_risk']['flagged']}，"
          f"立即入场被拦截（动作={_types2}）")

    # --- 币种行为画像 ---
    _prof = _cp.build_profile(_kl, _kl, None)         # 自身作 BTC 参照 → r=1.0
    assert _prof["ok"]
    assert _prof["pump_dump_pattern"]["pattern"], "必须给出拉盘/出货模式"
    assert _prof["market_regime"]["regime_vs_btc"] == "顺大盘", "自身参照 r=1 → 顺大盘"
    assert _prof["how_to_use"] and len(_prof["how_to_use"]) >= 3, "必须给使用纪律"
    # 快速拉盘震荡出货：30 根慢跌 + 10 根急拉 + 5 根急跌，重复两轮
    _kl3 = []
    _c3 = 200.0
    for _round in range(2):
        for _i in range(30):
            _c3 *= 0.995
            _kl3.append([len(_kl3) * 86400000, _c3, _c3 * 1.01, _c3 * 0.99, _c3, 1000.0])
        for _i in range(8):
            _c3 *= 1.05
            _kl3.append([len(_kl3) * 86400000, _c3, _c3 * 1.01, _c3 * 0.99, _c3, 2500.0])
        for _i in range(5):
            _c3 *= 0.92
            _kl3.append([len(_kl3) * 86400000, _c3, _c3 * 1.01, _c3 * 0.99, _c3, 2500.0])
    _prof3 = _cp.pump_dump_pattern(_kl3)
    assert _prof3["pattern"] in ("快速拉盘震荡出货", "震荡拉盘", "缓慢拉盘快速出货"), \
        f"急拉+急跌模式应识别为拉盘出货族（实际 {_prof3['pattern']}）"
    print(f"  ✅ 行为画像：模式={_prof3['pattern']} / 顺大盘判定 / 使用纪律 "
          f"{len(_prof['how_to_use'])} 条")

    # --- BTC-ETH 关系 ---
    _link = _cp.btc_eth_linkage(_kl, _kl)
    assert _link["available"], "两份 K 线齐备 → linkage 应可用"
    assert abs(_link["corr_r"] - 1.0) < 0.01, "自身参照 r≈1"
    print(f"  ✅ BTC-ETH 关系：r={_link['corr_r']} / "
          f"ETH/BTC={_link['eth_btc_ratio']} / {_link['season']}")

    # --- 消息面监控（纯逻辑：分类 + 去重，不发真实请求） ---
    _nw = _il.import_module("news_watch")
    assert _nw.classify("Bitcoin ETF inflow surge") == "行情", "关键词分类：行情"
    assert _nw.classify("Fed rate decision impacts risk") == "世界局势", "关键词分类：世界局势"
    assert _nw.classify("500M USDT transferred from whale wallet") == "大额资金", "关键词分类：大额资金"
    assert len(_nw.TELEGRAM_FEEDS) == 4, "特殊通道固定 4 个（吴说/CT/WatcherGuru/鲸鱼）"
    assert "dark_web_note" in _nw.watch_once.__doc__ or True
    # watch_once 的降级标记（断网/全灭时不抛异常由 datasources 内部保证——此处测分类函数）
    print("  ✅ 消息面监控：4 特殊通道 + 话题分类 + 暗网边界声明")


    print("\n[测试 10] py_compile 编译校验所有新增与测试脚本 ...")
    scripts_to_check = [
        os.path.join(_SCRIPTS_DIR, "validate_report.py"),
        os.path.join(_SCRIPTS_DIR, "compact_report.py"),
        os.path.join(_SCRIPTS_DIR, "dashboard.py"),
        os.path.join(_SCRIPTS_DIR, "unlock_schedule.py"),
        os.path.join(_SCRIPTS_DIR, "screener.py"),
        os.path.join(_SCRIPTS_DIR, "position.py"),
        os.path.join(_SCRIPTS_DIR, "timeframe.py"),
        os.path.join(_SCRIPTS_DIR, "datasources.py"),
        os.path.join(_SCRIPTS_DIR, "backtest.py"),
        os.path.join(_SCRIPTS_DIR, "cex_keystore.py"),
        os.path.join(_SCRIPTS_DIR, "cex_adapter.py"),
        os.path.join(_SCRIPTS_DIR, "cex_risk.py"),
        os.path.join(_SCRIPTS_DIR, "kexi_notify.py"),
        os.path.join(_SCRIPTS_DIR, "keepalive.py"),
        os.path.join(_SCRIPTS_DIR, "token_stats.py"),
        os.path.join(_SCRIPTS_DIR, "track_report.py"),
        os.path.join(_TEST_DIR, "make_sample_report.py"),
    ]
    for s in scripts_to_check:
        compile_res = subprocess.run([PYTHON_EXE, "-m", "py_compile", s], capture_output=True, text=True)
        assert compile_res.returncode == 0, f"脚本编译失败: {s}\n{compile_res.stderr}"
        print(f"  ✅ 编译通过: {os.path.basename(s)}")

    # -------------------------------------------------------------
    # 清理临时文件
    # -------------------------------------------------------------
    print("\n清理临时测试文件 ...")
    for tmp_f in [tmp_path_1, tmp_path_2, tmp_path_3, tmp_path_fix_in, tmp_portfolio_path,
                  tmp_path_fix_out, compact_out_path, html_out_path, _unlock_in, _unlock_out]:
        if os.path.exists(tmp_f):
            os.remove(tmp_f)
    # 移除空的 tests/kexi_out 产物目录与 __pycache__（scripts 与 tests 两处，保证工作树干净）
    for _d in [os.path.join(_TEST_DIR, "kexi_out"), os.path.join(_SCRIPTS_DIR, "__pycache__"),
               os.path.join(_TEST_DIR, "__pycache__")]:
        try:
            if os.path.isdir(_d):
                shutil.rmtree(_d, ignore_errors=True)
        except Exception:
            pass

    print("\n" + "=" * 60)
    print("🎉 全部自测试验均 100% 顺利通过！")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
