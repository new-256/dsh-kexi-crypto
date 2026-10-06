#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.6.1 回归：研判卡片只显示**本会话的 kexi 相关**活动。

现场问题（用户 2026-09-30 报）："即使没有调用团队进行任务，其他的任务流程进度，
也会在 K析卡片中展示"。

根因不在显示层，在数据层——旧实现把**所有**会话活动都灌进卡片：
  ① 每次 kexi_run 都会 autoEnsureTeam → 5 个 `team/member` 节点
     （"数脉 已就位"…），status='ok' 长期留在时间线里，**没派活也满屏像团队进度**；
  ② `turn/start` 每个回合都推节点（纯聊天也算）；
  ③ `tool/call` 无任何过滤——改文件/跑 git/读网页全进时间线，
     经 describeTool 配中文描述后**看起来就像任务流程**；
  ④ `/activity` 返回所有 TTL 内会话，别人的进度也堆这张卡里。

本测试用源码静态扫描钉住修复后的不变量（不跑浏览器——GUI 需 token 无法访问，
静态断言是这里能做到的最强验证；真正的运行期确认仍需用户重启后看一眼）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.join(HERE, '..', 'home-plugin', 'dsh-kexi-crypto', 'lib', 'index.mjs')
CLIENT = os.path.join(HERE, '..', 'home-plugin', 'dsh-kexi-crypto', 'lib', 'client.js')

checks = []


def ck(name, cond, extra=''):
    checks.append((name, bool(cond), extra))


for p in (HOST, CLIENT):
    if not os.path.exists(p):
        print('缺少文件：%s' % p)
        sys.exit(1)
host = open(HOST, encoding='utf-8').read()
client = open(CLIENT, encoding='utf-8').read()

# ── ① kexi 相关性判定必须存在，且是**白名单** ──────────────────────────
ck('① host 有 isKexiTool 判定', 'function isKexiTool' in host, '')
ck('① host 有 isKexiNode 判定', 'function isKexiNode' in host, '')
ck('① 用白名单 KEXI_TOOLS 而非黑名单（漏进来的少才是对的）',
   'const KEXI_TOOLS = new Set' in host, '')
for t in ('kexi_run', 'kexi_cex', 'kexi_ensure_team',
          'market_data_specialist', 'falsification_challenger',
          'send_message', 'wait_agent'):
    ck('① 白名单含关键工具 %s' % t, ("'%s'" % t) in host, '')
ck('① 白名单不含 write/edit（写文件不是研判活动）',
   "'write'" not in host.split('const KEXI_TOOLS')[1].split('])')[0], '')

# ── ② 非 kexi 活动必须被排除 ──────────────────────────────────────────
ck('② turn 节点标 kexi:false（纯聊天不算研判进度）',
   re.search(r"kind:\s*'turn'[^\n]*kexi:\s*false", host) is not None,
   'turn 节点未标 kexi:false')
ck('② step 节点标 kexi:false', re.search(r"kind:\s*'step'[^\n]*kexi:\s*false", host) is not None, '')
ck('② tool/call 调用时用 isKexiTool 打标',
   re.search(r"kind:\s*'tool'[^\s]*[\s\S]{0,320}?kexi:\s*isKexiTool", host) is not None,
   'tool 节点未按工具名打 kexi 标')

# ── ③ 会话级过滤：只显示当前会话 / 有 kexi 活动的会话 ──────────────────
ck('③ /activity 支持 sessionId 查询参数', "searchParams.get('sessionId')" in host, '')
ck('③ 按 sessionId 过滤会话', re.search(r"only\s*\?\s*b\.id\s*===\s*only", host) is not None, '')
ck('③ 无 sessionId 时只显示有 kexi 节点的会话',
   re.search(r"filter\(\(b\)\s*=>\s*b\.nodes\.some\(isKexiNode\)\)", host) is not None,
   '缺"只显示有 kexi 活动的会话"兜底')
# ⚠ 顺序陷阱：节点缓冲里混着非 kexi 噪音，若先 slice 再 filter，
# 真正的研判节点会被挤出窗口（用户看到的反而是"没有活动"）。
ck('③ 节点级先 filter(isKexiNode) 再 slice(-limit)',
   re.search(r"b\.nodes\.filter\(isKexiNode\)\.slice\(-limit\)", host) is not None,
   '节点 slice 发生在 kexi 过滤之前')
ck('③ 会话级过滤发生在排序与 map 之前',
   re.search(r"\.filter\(\(b\)\s*=>\s*b\.nodes\.some\(isKexiNode\)\)[\s\S]{0,200}?\.sort\(", host)
   is not None, '会话级 kexi 过滤不在 sort 之前')

# ── ④ 建队节点必须合并成一条 ──────────────────────────────────────────
ck('④ 建队节点 kind 改为 team_build', "kind: 'team_build'" in host, '')
ck('④ 旧的逐成员 team_member 节点已移除',
   "kind: 'team_member'" not in host, 'team_member 仍在逐个建节点')
ck('④ 有合并窗口常量', 'TEAM_BUILD_MERGE_MS' in host, '')
ck('④ 合并逻辑把成员名累加进 members',
   re.search(r"recent\.members\s*=\s*\(recent\.members\s*\|\|\s*\[\]\)\.concat", host) is not None, '')
ck('④ 合并节点 detail 展示成员名单', 'recent.detail = recent.members.join' in host, '')
ck('④ 合并窗口有合理上限（spawn 五个成员通常几秒内完成）',
   re.search(r'TEAM_BUILD_MERGE_MS\s*=\s*\d+\s*\*\s*1000', host) is not None, '')

# ── ⑤ busy 判定不能被过滤破坏（v1.5.2 的回归防线）────────────────────
# turn 节点被 kexi:false 滤掉了，但 v1.5.2 解决的正是
# "wait_agent 静默等 10 分钟、期间无事件"——那时唯一活着的信号就是未关闭的 turn。
# 若 turnOpen 改成读过滤后的 nodes，团队真在跑时卡片反而显示空闲（把旧 bug 请回来）。
ck('⑤ turnOpen 从完整缓冲 b.nodes 读取（不从过滤后的 nodes）',
   re.search(r"const turnOpen = b\.nodes\.some", host) is not None,
   'turnOpen 读了过滤后的 nodes → 等待团队时会误显示空闲')
ck('⑤ 保留 v1.5.2 的 await_agent 空窗说明注释',
   'wait_agent' in host and '静默' in host, '')

# ── ⑥ 前端带上 sessionId ─────────────────────────────────────────────
ck('⑥ 前端请求带 sessionId 查询参数', 'sessionId=" + encodeURIComponent(sessionId)' in client, '')
ck('⑥ sessionId 进 useEffect 依赖（切会话后重新拉）',
   re.search(r"\}, \[sessionId\]\);", client) is not None,
   '切会话后不会重新拉取，会显示上个会话的残留')
ck('⑥ 取不到 sessionId 时仍能工作（退化为按 kexi 活动过滤）',
   re.search(r'\?\s*\(""\s*\+\s*"\?sessionId=', client) is not None
   or 'sessionId ? ("?sessionId=' in client, '')

# ── ⑦ 不得为了"看起来有活动"而伪造/放宽 ─────────────────────────────
# 这类问题最常见的错误修法是"空了就随便显示点什么"——那是在骗用户。
ck('⑦ 空态提示明确说明"纯聊天不计入"，而不是暗示用户去派活',
   '纯聊天与普通工具调用' in client, '空态文案未澄清口径')
ck('⑦ 空态不再诱导"说一句研判一下 ETH"（那是旧文案，与新口径矛盾）',
   '研判一下 ETH」试试' not in client, '')
ck('⑦ 没有把 isKexiNode 退化成"全都算相关"',
   "if (n.kexi === true) return true" in host and "if (n.kexi === false) return false" in host, '')

# ── 汇总 ─────────────────────────────────────────────────────────────
fails = [c for c in checks if not c[1]]
for name, okv, extra in checks:
    if not okv:
        print('  FAIL  %s   %s' % (name, extra))
print('test-card-kexi-filter: %d/%d 通过' % (len(checks) - len(fails), len(checks)))
sys.exit(1 if fails else 0)
