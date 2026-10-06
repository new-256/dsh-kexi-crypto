#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.6.4 回归：目标会话实跑暴露的两个真 bug。

取证来源：会话 session-6f909e6a（"现阶段新用户应该立即建仓还"）跑完后逐节点取证，
两个故障都**有实证**，不是推测：

  ① kexi_dashboard 崩在 `Error: p.includes is not a function`
     —— `abs()` 对非字符串参数调用 .includes。JSON Schema 只是声明、运行时不强制，
     模型传了非字符串就抛原始 TypeError，模型拿到无从纠正。
     连锁后果：主理人改用 kexi_run 跑 dashboard.py 绕路，而那条路返回 `out`
     而不是 `out_files`，成果登记不上 —— 一次类型错误炸出两个故障。

  ② 看板生成了（market_report.html, 75890 B, 15:56:13）但卡片「最终成果」永远空着
     —— 懒发现的文件名过滤只认含 dashboard/看板 的 html，
     `dashboard.py --out` 的自定义名被整个漏掉。
     （同目录 latest.json 指向昨天 01:17 的 market_dashboard.html，被 freshness
       正确拒绝——所以不是"挂错旧看板"，是"什么都没挂"。）
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.join(HERE, '..', 'home-plugin', 'dsh-kexi-crypto', 'lib', 'index.mjs')
PLUGIN = os.path.join(HERE, '..', 'preset', 'kexi-crypto', 'kexi-plugin.mjs')
checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


for p in (HOST, PLUGIN):
    if not os.path.exists(p):
        print('缺少文件：%s' % p)
        sys.exit(1)
host = open(HOST, encoding='utf-8').read()
pl = open(PLUGIN, encoding='utf-8').read()

# ══════════════ ① kexi_dashboard 参数防御 ══════════════
m = re.search(r"'kexi_dashboard',\s*\n\s*async \(args, exec\) => \{(.*?)\n      \}\)", pl, re.S)
ck('① 能定位 kexi_dashboard 处理函数', m is not None, '结构变了，请同步本测试')
body = m.group(1) if m else ''

ck('① 有 strOr 类型守卫（非字符串一律当缺省）',
   re.search(r"const strOr = \(v\) => \(typeof v === 'string' \? v : ''\)", body) is not None, '')
ck('① abs() 不再直接对入参调用 .includes',
   re.search(r"const abs = \(p0\) => \{\s*\n\s*const q = strOr\(p0\)", body) is not None,
   'abs() 仍在对未清洗的入参调用 .includes —— 模型传非字符串仍会崩')
ck('① 旧的裸 lambda 已不存在', "const abs = (p) => p && (p.includes" not in body, '')
# 必填参数类型错时必须**明确回报实际类型**，让模型能自我纠正
ck('① input 类型错时回报实际收到的类型',
   'input 必须是字符串路径' in body and 'Array.isArray(args.input)' in body, '')
ck('① input 缺失时给出可照抄的示例',
   'kexi_dashboard 缺 input' in body and 'kexi_out/market_report.json' in body, '')
for opt in ('klines', 'portfolio', 'title'):
    ck('① 可选参数 %s 经 strOr 清洗' % opt,
       re.search(r'strOr\(args\.%s\)' % opt, body) is not None, '')
ck('① 默认输出名用清洗后的 inpRaw 推导（不再对 args.input 直接 .split）',
   "args.input.split" not in body, '仍对原始 args.input 调用 .split')

# ══════════════ ② 成果扫描放宽 ══════════════
ck('② 扫描接受任意 .html（自定义 --out 名也能登记）',
   re.search(r"if \(!/\\\.html\?\$/i\.test\(name\)\) continue", host) is not None
   or "if (!/\\.html?$/i.test(name)) continue" in host, '')
ck('② 旧的窄过滤已移除',
   '/dashboard.*\\.html?$/i.test(name)' not in host, '仍只认 dashboard/看板 文件名')
ck('② 安全性改由 freshness 把关（且该检查仍在）',
   'artifactIsFresh(b, full)' in host, '')
ck('② latest.json 指向陈旧看板时仍走扫描兜底（不被 return 截断）',
   re.search(r"const marked = readLatestArtifact\(b\)\s*\n\s*if \(marked\) \{[^}]*return", host) is not None, '')

# ══════════════ 诚实性：不能为了"总能显示"而牺牲正确性 ══════════════
ck('③ 放宽后仍必须按 mtime 取最新（不能随便挂一个）',
   re.search(r"if \(!best \|\| st\.mtimeMs > best\.mtimeMs\)", host) is not None, '')
ck('③ 仍校验是文件（防目录同名）', 'if (!st.isFile()) continue' in host, '')
ck('③ 权威标记优先于扫描（latest.json 有效时不猜）',
   'const marked = readLatestArtifact(b)' in host, '')

# ══════════════ ④ 不能无条件承诺"完整看板在最终成果" ══════════════
# 实测：结论被截断在 1200 字（第 4 点断在半句），而那一轮看板没登记成功，
# 「最终成果」是空的 —— "完整内容在那边"成为落空指引，两头都拿不到完整结论。
cl = open(os.path.join(HERE, '..', 'home-plugin', 'dsh-kexi-crypto', 'lib', 'client.js'),
          encoding='utf-8').read()
ck('④ 评估页按是否有看板给不同指引', re.search(r"artifacts\.length\s*\n\s*\? react", cl) is not None, '')
ck('④ 无看板时不再承诺"完整看板在最终成果"',
   '本轮未产出离线看板' in cl, '')
ck('④ 截断时才提示"已截断"（靠结尾省略号判断，不靠猜）',
   re.search(r"endsWith\(['\"]…['\"]\)", cl) is not None, '')
ck('④ 无看板且未截断时明说"以上即完整结论"',
   '以上即完整结论' in cl, '')

# ══════════════ 汇总 ══════════════
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-session-findings: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
