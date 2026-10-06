#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.6.2 回归（补）：React 渲染层规则——静态断言能查、但必须写死的那几条。

背景：v1.6.2 的关系图第一次交付时，46 项数据层测试全绿、10 项运行期测试也绿，
**但真浏览器一渲染就报 15 个 React 错误**。原因是三条只在渲染时暴露的规则：

  ① SVG 属性必须 **camelCase**。写 `stroke-width` / `fill-opacity` / `stroke-dasharray`
     时 React 会警告并**直接丢弃该属性**——不报错、不断言，属性静默消失。
     后果：描边宽度没了、透明填充没了、区分"实测/推测"的虚线圈没了，
     而"区分实测与推测"是这张图的核心诚实性设计。
  ② 同一父节点下的 children **key 必须唯一**。重名会让 React 丢节点/行为未定义。
  ③ viewBox 宽度要贴近容器实际宽度，否则等比缩放会把设计字号放大/缩小到读不出来。

前两条属于"写错了不报错"的类型——只有把属性/节点真正渲染进 DOM 才查得到，
所以这里用静态扫描把危险写法钉死（浏览器端的几何重叠检测见 Playwright 手工核验）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(HERE, '..', 'home-plugin', 'dsh-kexi-crypto', 'lib', 'client.js')
checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


if not os.path.exists(CLIENT):
    print('缺少文件：%s' % CLIENT)
    sys.exit(1)
src = open(CLIENT, encoding='utf-8').read()

# 只看 TeamGraph 函数体，别把整个文件都算进来
m = re.search(r'function TeamGraph\(props\) \{(.*?)\n    \}\n', src, re.S)
ck('① 能定位到 TeamGraph 函数体', m is not None, '正则未匹配，TeamGraph 结构可能变了')
body = m.group(1) if m else ''

# ══════════════ ① SVG 属性必须 camelCase ══════════════
# 抓 createElement 里的 kebab-case SVG 属性：'stroke-width' / "fill-opacity" / 'stroke-dasharray'
kebab_svg = re.findall(r"""['"](stroke-width|fill-opacity|stroke-dasharray|stroke-linecap|stroke-linejoin|fill-rule|clip-rule|text-anchor|font-size|stop-color|stop-opacity)['"]\s*:""", body)
ck('① TeamGraph 内无 kebab-case SVG 属性（React 会静默丢弃）',
   not kebab_svg, '发现: ' + ', '.join(sorted(set(kebab_svg))))

for camel in ('strokeWidth:', 'fillOpacity:', 'strokeDasharray:'):
    ck('① 使用 camelCase 属性 %s' % camel, camel in body, '')

# ══════════════ ② children key 必须唯一 ══════════════
key_block = re.search(r'const kids = \[(.*?)\n        \];', body, re.S)
ck('② 能定位 kids 数组', key_block is not None, '')
keys = re.findall(r'key:\s*"([^"]+)"', key_block.group(1)) if key_block else []
dup = sorted({k for k in keys if keys.count(k) > 1})
ck('② kids 内 key 无重复', not dup, '重复 key: ' + ', '.join(dup))
ck('② 圆盘与分类文字用了不同 key（曾都是 "c"，React 会丢节点）',
   'key: "disc"' in body and 'key: "cat"' in body, '')

# ══════════════ ③ viewBox 宽度贴近容器，避免缩放后字号失真 ══════════════
w = re.search(r'const W = (\d+), H = (\d+);', body)
ck('③ 能取到 viewBox 宽高', w is not None, '')
if w:
    W = int(w.group(1))
    ck('③ viewBox 宽度 ≥ 600（容器约 600-900px；写 360 会被放大 2.4 倍导致文字撞车）',
       W >= 600, '实际 W=%d' % W)
    ck('③ SVG 有 min-width 兜底（窄窗口不缩到看不清，实测 0.57 倍时字只剩 4.8px）',
       '.kexi-graph{width:100%;min-width:' in src, '窄窗口会把图缩到读不出来')
    ck('③ 窄容器可横向滚动而不是硬缩', '.kexi-graph-wrap{margin:6px 0 2px;overflow-x:auto}' in src, '')

# ══════════════ ④ 文字按列宽折行，而不是硬截固定字数 ══════════════
ck('④ 有按列宽折行的逻辑（硬截 18 字在 5 人排布下仍会撞车）',
   'wrapAct' in body and 'colW' in body, '')
ck('④ 折行宽度由 colW 推导', re.search(r'per\s*=\s*Math\.max\([^\)]*colW', body) is not None, '')

# ══════════════ ⑤ 成员子会话不得单独成块 ══════════════
# 成员自己跑 kexi_run 会通过服务端的 kexi 过滤，于是并排冒出 5 个
# 「尚未组建团队」——真浏览器截图里才看出来的噪音。
ck('⑤ 关系图页过滤掉成员子会话', 'isMemberSession' in src, '')
ck('⑤ 过滤后为空时给的是解释而不是空白',
   '成员工作已并入主会话的关系图' in src, '')

# ══════════════ 汇总 ══════════════
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-graph-render-rules: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
