# 交接文档 · HANDOFF

> 面向**接手这个项目的新会话/新人**。先读完 §1–§4 再动手。
> 最后更新：2026-09-30，版本 **v1.9.6**
> 版本演进见 `CHANGELOG.md`；面向使用者的说明见 `README.md` / `INSTALL.md`。

---

## 1. 这是什么

`dsh-kexi-crypto`（K析研判团）—— DSH 桌面端插件，preset id `kexi-crypto`。

一个**加密货币趋势研判团队**：6 个角色（主理人 K析 + 数脉 / 指北 / 望潮 / 守拙 / **证伪**），
外加可选的本地 CLI（agy / codebuddy / mimo）做交叉复核。

**它做什么**：给数字资产做技术面 + 消息面 + 行为画像研判，输出可执行的
入场时机、失效位、三情景概率、风险定级，并以离线 HTML 看板交付。
**v1.6.0 起可选接入 CEX 真实账户**：读余额/持仓，以及在用户显式授权后下单。

**边界（v1.6.0 已修订）**：
- ⚠ **实盘能力现在是真实存在的**（用户 2026-09-30 明确要求）。这推翻了 v1.5.x
  时期"不碰任何真实资金"的红线。**唯一的闸门是设置面板里的「自主级别」**：
  `readonly`（默认）/ `confirm` / `limited` / `full`。见 §9.8、§10.2。
- **AI 无法自行提升自主级别**。级别只写在 host 侧 `autonomy.json`，Python 只读，
  且 `cex_adapter.py` 的 CLI **不提供任何能改写它的参数**——这一点是刻意设计：
  模型本来就能通过 `kexi_run(mode=script)` 手写 argv 传参，若权限来自命令行，
  多写一个 `--autonomy full` 就绕过了。
- 任何异常（文件缺失/损坏/级别非法/读取失败）→ 一律 **fail-closed 回 readonly**。
  **"权限读不出来"绝不能等于"放行实盘"。**
- 仍**不碰钱包、不碰私钥、无提现能力**、不涉及链上交易执行
- 暗网数据源无能力（需付费 API/Tor），**任何时候都不得假装有**
- 2026 年 X/Twitter 无免费读源，社媒面走 RSSHub Telegram 镜像

**仓库根**：`C:\Users\lcl\Desktop\DSH插件开发\K析研判团`
**安装副本**：`%APPDATA%\DSH Desktop\dsh-home\profiles\web\node_modules\dsh-kexi-crypto`

---

## 2. 目录地图

```
home-plugin/dsh-kexi-crypto/
  cordis.patch.yml      ← 插入 host 路由 + 5 人团队人设（persona）
  lib/index.mjs         ← HOST：/activity /status /settings /cex 路由、卡片数据源
  lib/client.js         ← CLIENT：状态灯 + 三个弹窗 + 设置面板（浏览器内 React）
preset/kexi-crypto/
  kexi-plugin.mjs       ← PRESET：全部 kexi_* 工具定义、脚本白名单、policy
  skills/
    crypto-market-analysis/    ← 34 个 Python 脚本（零第三方依赖，只用标准库）
    kexi-team-orchestration/  ← SKILL.md：Workflow A–L、20 条铁律
scripts/                ← 13 个验证/测试脚本（进包）
tools/session-forensics/← 会话取证工具（不进包，开发用，见其 README）
```

---

## 3. 架构关键点（最容易搞错的地方）

### 3.1 host 与 preset 的能力是**不对称**的

| | 有 web 路由 | 有 subprocess（能跑 Python） |
|---|---|---|
| `lib/index.mjs`（host） | ✅ | ❌ 只有 `ctx.effect/get/inject/on` |
| `kexi-plugin.mjs`（preset） | ❌ | ✅ 有 `runPython` |

**推论**：
- 需要 Python 的逻辑 → 只能在 preset（`kexi_run` 的各个 mode）
- 需要浏览器直接访问的端点 → 只能在 host
- **CEX 凭据加密**因此放在 host，用 `node:child_process` 调 PowerShell 的
  `[ProtectedData]::Protect`（Windows DPAPI），不走 cex_keystore.py

### 3.2 工具不要在 host/preset 重复声明

**绝对不能**。`package.json` 的 `dsh.client.platform=web` 已让浏览器自动发现
`lib/client.js`；再在 `cordis.patch.yml` 加一行 `file://` 指向同一插件会创建
两个 ESM 实例 → 同名 `provide` 崩溃。`cordis.patch.yml` 顶部注释写了这条。

### 3.3 会话日志与成员 id

- 每个会话（含每位成员）一个目录：
  `%APPDATA%\DSH Desktop\dsh-home\sessions\--<cwd转义>--\<sid>\session.v4.jsonl.zstd`
- 读法见 `tools/session-forensics/README.md`
- **`member.id` === 该成员自己的 session id** —— 这是跨会话查成员活跃度的依据
- ⚠️ `tool/call` 事件的 `data.arguments` 是 **JSON 字符串**不是对象（踩过，见 §6.1）

---

## 4. 标准工作流（每次改动后必须走完）

```powershell
# 1) 跑全量回归（12 组，应全绿）
powershell -ExecutionPolicy Bypass -File scripts\run-all-tests.ps1

# 2) 同步到安装副本 + 校验哈希 + npm pack 预演
powershell -ExecutionPolicy Bypass -File scripts\sync-install.ps1 -Pack

# 3) 告知用户：需要重启 DSH + 刷新浏览器
```

> 只校验不复制：`-CheckOnly`　　跑快些（跳过端到端）：`run-all-tests.ps1 -Quick`
> Python 路径可覆盖：`$env:KEXI_PYTHON`

---

## 5. kexi_run 的五种模式（工具面主干）

| mode | 用途 | 耗时 |
|---|---|---|
| `fast` | **秒级确定性快研**。`symbol` 或 `symbols`（逗号分隔）。可选 `source: auto\|binance\|coingecko`。**输出含 `confidence`（高/中/低 + 逐条扣分理由）；低置信时自动收回「立即入场」** | 单币 ~1.4s，6 币 ~3s |
| `pipeline` | 一键全链：fetch→indicators→assemble→validate→dashboard | ~30s |
| `script` | 单脚本；带 `batch:{items,args(含{item}),concurrency}` 即批量 | 视脚本 |
| `files` | 本轮/历史产物清单（本轮以 `kexi_out/.kexi_run.json` 为准） | 瞬时 |
| （缺省） | 有 `batch` → `script`；有 `symbols` → `fast`；否则 `pipeline` | — |

**核心纪律（policy 1b）**：任何"XX 币怎么样/能不能买"**第一步先 `mode=fast`**，
先把可执行价位给用户；只有明确要深度论证才升级到团队协作，且**不重算**
（把 `fast_analysis.json` 直接喂给成员）。

**红队纪律（policy 1c，v1.5.9）**：深研时**证伪必须最后派且必须真派**——
守拙管闸门、望潮管推演，**没有人专门攻击结论前提**是真实的编排空缺。
主理人**不得把红队意见当"又一个风控意见"消音**；反对与望潮冲突时两条都呈现。

**留痕纪律（policy 15）**：出结论后立刻 `prediction_log.py record`，7 天后 `reconcile` 对账。

---

## 6. 血泪教训（不要重蹈）

### 6.1 前端问题不是"刷新一下就好"

- `tool/call` 的 `arguments` 是 JSON 字符串。早期 host 只认对象 → 卡片上所有
  工具标签退化成"派活给 成员"、不显示币种。当时回归测试传的是对象，
  所以**测试全绿而线上失效**。
- `ProgressPopup` 曾收到 `useState` 元组 `et`（`[值,setter]`）而非 `act`
  → `sessions` 恒 undefined → 卡片永远"暂无活动"。**这就是"卡片不工作"长期
  无解的真凶**，与鉴权/数据/字段名都无关。
- v1.5.5 加 CEX 面板时把 5 个 hook 放在 `SettingsCard` 的**早退 return 之后**
  → 违反 Rules of Hooks → 点"设置"后整棵组件树卸载，**状态灯一起消失**。
  → `scripts/test-hooks-order.mjs` 会机械扫描这类违规。

### 6.2 失败必须可见

曾有两处"静默失败"让排查绕远路：
- 前端 `fetch(...).then(r => r.ok ? r.json() : null).catch(()=>{})`
  —— 任何 401/网络错都表现为"没活动"
- 批量工具返回 `{error: undefined}` → DSH 的 lossless JSON 校验拒收整个返回值，
  **批量明明跑完了却只显示报错**

现在：非 2xx 会显示红色横幅；`scripts/test-runtime-bugs.mjs` 守这条线。

### 6.3 数据降级路径是 bug 温床

Binance 对本机 451（见 §7）时降级到 CoinGecko（22 根、4 天粒度、无成交量）。这一路暴露：
- ADV20 算不出却判成"流动性不足" —— **方向完全相反的错误结论**
- 缓存判据用 `last_bar_age_days<=1`，盘中几乎必然失效
  → 丢弃磁盘上 239 根好数据去用 22 根
- 降级取数**覆盖**已有好数据 —— 一次限流毁掉本地数据集，成员却只看到"取数成功"
- 取数失败时标的从报告里消失（"分析 0 币"却无任何说明）
- 数据已判"不可用"却仍输出可执行止损/评级 —— **精确的垃圾比没有更危险**

### 6.4 argparse 与 JSON 的经典坑

- 重复 `--symbol A --symbol B` → 后者覆盖前者，**只剩最后一个**。
  批量必须用逗号分隔的 `--symbols`。
- `fetch_binance` 返回 `(klines, warnings)`、`dedupe`/`drop_open_bar` 返回
  `(结果, 计数)` —— 都是元组，误当纯结果会炸。

### 6.5 写测试要先证明它能变红

`test-hooks-order.mjs` 第一版永远绿——它把嵌套回调里的 `if(x) return y`
误判成函数早退。**注入违规验证它确实会失败**这一步是必须的。

---

## 7. 环境事实（会反复遇到）

| 事实 | 影响 |
|---|---|
| **Binance 本机可达性随 Clash 出口节点轮换而变**（2026-09-30 实测：6 轮重复探测 binance_spot 3/6、binance_fapi 3/6 成功；失败表现为 `SSL UNEXPECTED_EOF_WHILE_READING`）——原记录的"稳定 451"已不再成立，但**换成时好时坏**，同样不可依赖 | 合约情绪/资金费率/持仓量降级到 OKX；日线取数降级到 CoinGecko。**单次取数失败 ≠ 接口坏了**，必须重试（`derivs_sentiment.py` 的 `fetch_with_retry` 已内置） |
| **OKX 记录需更正**：原记"对 Python 稳定 403（CloudFront 地区拦截）"，2026-09-30 实测 3/3 全通；反而 Binance `openInterestHist` 间歇 SSL 失败 | 跨源对比成为刚需：见 §12.4 #3 |
| 解决：Clash 切到**非受限出口**（美/欧/新）。无需 VPN/专线——**本项目不涉及资金与下单** | 切回后 Binance 主源成功率上升，多空比也会回来 |
| **插件端口每次 DSH 重启都会变**（实测 52028 / 61478 / 47896 / 54799） | 调试前先探端口 |
| GUI 需 token，匿名访问返回 **401** | 无法用无头浏览器直接看页面 |
| `PI_AI_ERROR: Provider returned an empty response` —— 上游 provider 空响应，实测**约 37% 回合失败率**，与回合长度无关 | 团队会自动起新回合自愈，但每次失败损失数分钟；**非插件问题，插件无法修** |
| PowerShell 5.1 读无 BOM 的 `.ps1` 会把中文/✅ 拆坏 | 所有 `.ps1` 必须写 UTF-8 **with BOM**。⚠ **用编辑工具改 `.ps1` 后务必回查 BOM 是否还在**——本次改 `run-all-tests.ps1` 时丢了 BOM，直接导致全套测试无法启动、报一堆莫名其妙的 ParserError |
| npm 把 notice 写 stderr，PS 5.1 在 `$ErrorActionPreference='Stop'` 下会中断 | 局部放宽，见 `sync-install.ps1` |
| `Set-Content -Encoding UTF8` 会加 BOM，破坏 JSON | 用 Python 的 `utf-8` 写 JSON |

---

## 8. 测试地图（`scripts/`，15 组）

| 脚本 | 守住什么 |
|---|---|
| `verify.mjs` | 静态接线：人设/policy/工具参数/白名单/技能 frontmatter + `node --check` |
| `test-runtime.mjs` | 行为回归主套件（160 项） |
| `test-card-props.mjs` | 卡片传值 + 轮询失败可见性 |
| `test-card-kexi-filter.py` | **卡片只显 kexi 相关**（33 项：白名单、fail-closed、建队合并、sessionId） |
| `test-team-graph.py` | **团队关系图**（46 项，其中 10 项运行期：真起 host 喂事件看真图） |
| `test-graph-render-rules.py` | **图渲染规则**（16 项：SVG camelCase、key 唯一、viewBox 宽度、按列宽折行、过滤成员子会话） |
| `test-session-findings.py` | **实跑取证回归**（21 项：kexi_dashboard 参数防御、成果扫描放宽、评估页不承诺空出口） |
| `_artifact-runtime-probe.mjs` | **成果登记运行期探针**（6 项，用复刻现场的真实目录跑真 host；含"不得误挂旧看板"反向约束） |
| `_track-session.mjs` | **会话跟踪器**（`node _track-session.mjs <sessionId> [间隔秒] [上限分钟] [日志]`；完成判定用"连续 3 次非忙且无新节点"） |
| `test-regime.py` | **市场状态判定**（45 项，合成 K 线全程离线：形态→状态、判据区分力、四条诚实性约束） |
| `regime.py` | **市场状态判定引擎**（四判据加权；概率标签、置信度封顶 0.85、失效条件必非空） |
| `test-regime-log.py` | **regime 对账台账**（25 项：口径先定后看、前视偏差、no_data 不算错、待定不提前评分、坏行不废表） |
| `regime_log.py` | **regime 对账台账**（`record` / `reconcile`；口径常量先定后看、样本不足不给命中率） |
| `test-cex-credential-schema.py` | **CEX 凭据字段一致性**（26 项，防 host↔Python 规则漂移；含 state 数组误用的静默失效防护） |
| `test-position-doctor.py` | **仓位体检**（45 项：沉默原则、市场状态不单独构成减仓、导入前越权检查、草案 reduce_only） |
| `position_doctor.py` | **仓位体检引擎**（七规则；只诊断不下单；权重=持仓内部占比） |
| `test-dpapi-roundtrip.mjs` | **DPAPI 凭据往返实测**（28 项：真实加解密、明文不落盘、中文不乱码；⑦ 组抽出 index.mjs 自己的脚本真跑） |
| `_build-settings-i18n-harness.mjs` | **设置面板汉化夹具**（真 React 渲染 SettingsCard，供浏览器扫裸英文） || `test-runtime-bugs.mjs` | 现场取证 bug：无 `undefined` 返回、`--symbols` 批量、mode 缺省推断 |
| `test-hooks-order.mjs` | React Hooks 不得出现在早退之后（按括号深度判定） |
| `test-veto-disagreement.py` | 硬风控一票否决 + 分歧度阈值（含不误杀反例） |
| `test-stop-plan.py` | 可执行止损三法交叉 + 拒绝硬凑 + 量纲自洽 |
| `test-futures-fallback.py` | 合约情绪 OKX 兜底 + `_f()` 语义 + 跨所量纲 |
| `test-fast-confidence.py` | **快答置信度分级 + 三情景区间结构**（90 项） |
| `test-prediction-log.py` | **决策留痕与事后对账**（56 项） |
| `test-derivs-crosssource.py` | **跨所跨源矛盾检测**（88 项） |
| `test-unlock-fetch.py` | 解锁日历供应量校验（32 项） |
| `test_batch.mjs` / `test_runmarker.mjs` / `test_cwd_fix.mjs` | 批量端到端 / 本轮标记时序 / 脚本 CWD |

`run-all-tests.ps1` 一键全跑（**16 步全绿**才算过）；`sync-install.ps1` 同步 + 哈希校验。

> ⚠ 改 `run-all-tests.ps1` 后**务必确认 UTF-8 BOM 还在**（见 §7 末行）。

---

## 9. 已知未闭环的问题

1. **卡片是否真的好了，需要用户在重启后确认**。代码侧已修（传值 + hooks + 失败可见）
   并有回归测试，但**我无法访问用户浏览器**（GUI 需 token、浏览器桥未连接），
   所以"用户视角修好了"尚未被证实过。→ 下一步第一件事就是问用户。
2. **Binance 可达性随节点轮换而变**（不是"已解决"，见 §7）。工作区里 BTC/ETH 的
   日线目前是 239 根正规粒度（`binance`/`cache(binance)`），但**不保证下次还在**。
   已缓解：快答的 `confidence` 会因降级源自动降级并收回「立即入场」。
3. **`PI_AI_ERROR` 37% 失败率**是上游 provider 问题，插件无法修。
4. **多 Agent 是否真的优于单次快答** —— v1.5.9 已打通**决策留痕与事后对账**
   （`prediction_log.py`），但**台账刚建立、尚无已过窗口的样本**。
   `prediction_log.py reconcile` 现在只会报 "reconciled: 0, pending: N"——
   **在积累出足够样本之前，仍然不得宣称"团队比快答好"**。
   （重要限定：消融对比的 identical 项几乎全是 `shared_engine`，因为快答 import 的
   就是团队用的同一批脚本，**逐位一致是同代码同数据的必然结果，不是团队的功劳**。）
5. **免费源拿不到可靠的交易所净流入**：`wallet_watch.py` 实测 7 链扫描正常，
   但 CEX 地址标签在 Blockscout 上缺失，种子地址命中 0 笔，`direction` 全为
   `unknown`。真正的聪明钱监控需 Nansen/Dune 付费源——**不要为了让它"看起来
   能用"而放宽匹配规则**。
6. **⚠ 待用户决策：2Z 解锁日历总量口径错误**。registry 登记 `total_supply=1e9`，
   但 CoinGecko（9,998,069,911）与 Binance Alpha（10,000,000,000）都说 **100 亿，
   偏差 −90%**。当前"高冲击"预警很可能是这个错误造成的**假警报**。
   `unlock_schedule.py --verify-supply` 会把它顶成 warning，但**未擅自改数据**。
7. **解锁日历无免 key 明细源**（探测结论，见 `docs/unlock-source-probe-2026-09-30.md`）：
   12+ 个源全部试过，DefiLlama 已转 402 付费墙、其余要么 401/403 要么只有汇总供应量。
   **日期与数量 100% 靠人工录入**——不要为了"补全日历"而放宽标准或编造。
8. **⚠ v1.6.0：实盘链路刚打通，尚未在真实账户上跑过一笔**。以下都是**未验证**的，
   请当成"代码写完了"而不是"验证过了"：
   - 四家适配器的签名逻辑是 v1.5.0 写的，此后**从未对着真实账户发过请求**。
     `test-cex-live-safety.py` 覆盖的是**门禁与安全性质**（fail-closed、幂等、
     凭据桥接），**不覆盖签名正确性**——签名错了会表现为交易所返回签名错误，
     不是被本地拦下。
   - `kexi_cex` 的面板与工具**未经用户浏览器验证**（GUI 需 token，我无法访问）。
   - ⚠ **`confirm` 档与设计意图有已知偏差**：设计是"在面板点确认才发送"，
     但**待确认订单队列的 UI 尚未实现**。当前 `confirm` 档直接把草案返回给
     模型，模型转述给你后，你**需要自行把自主级别切到 `limited`/`full`
     才会真的发出去**。这是已知未完成项，**不是伪装成已完成**。
     要做到真正的"面板点一下就发"，还需新增待确认队列 + 批准接口。
   - **首次启用建议路径**：只读档跑通 `health` → 确认能读到自己真实持仓 →
     再放到 `confirm` 观察草案是否合理 → 最后才考虑 `limited`。
9. **订单意图日志只解决"单笔订单"的幂等，不解决"多步序列"的幂等**。日志能回答
   "这一笔发出去没有"，但"下单→挂止损→确认→撤单"这样的多步序列中途崩溃，
   仍会停在中间态。`journal` 的 `unresolved` 是给**人工**核对的，不是自动恢复。
10. **⚠ v1.6.1：卡片过滤改动了 `/activity` 的返回内容，依赖旧格式的外部消费者
    会受影响**。`nodes` 现在只含 kexi 相关节点、会话列表只含有 kexi 活动的会话、
    新增 `sessionId` 查询参数。插件内部已同步（名册、成果、评估结论都跟着走
    过滤后的 sessions），但**如果你写过自己的脚本读这个接口，需要跟着改**。
11. **⚠ v1.6.2：`b.memberIds` 存中文名、`b.memberCodes` 存英文代号，两张表各有主
    不可混用**。`memberIds` 由 `subagent/catalog` 维护，`team/message` 的标签翻译
    依赖它；`memberCodes` 由 `team/member` 维护，关系图连线依赖它。
    混写的后果已实测：卡片标签变成「主理人 → shuma 派活」，关系图的边接不上节点
    （`edgeTo` 退化成 `'mem-shum'` 这种截断 childId）。**新增映射时先确认该放哪张表。**
12. **⚠ v1.6.2：工作分类读的是成员 tool 节点的 `script` 字段**（kexi_run 的 arguments），
    不是工具名。成员跑的是 `kexi_run` 包装器，工具名恒相同、没有区分度——
    只按工具名分类会让所有成员都落进「其他工作」。**新增 kexi_run 的 script 模式时，
    要在 `WORK_CATS` 里补上对应脚本名**，否则新脚本会静默归到"其他"。
13. **⚠ v1.6.2：SVG 属性在 React 里必须 camelCase**。写成 `stroke-width` /
    `fill-opacity` / `stroke-dasharray` **React 只警告不报错，属性被静默丢弃**。
    已踩过一次：区分"实测/推测"的虚线圈因此消失，而那正是关系图的核心诚实性设计。
    改任何图/图表代码后，务必用 `scripts/test-graph-render-rules.py` 扫一遍。
14. **⚠ v1.6.2：数据层测试全绿 ≠ 功能能用**。关系图第一次交付时 46 项数据测试
    （含 10 项运行期）全绿，真浏览器一渲染就报 15 个 React 错误 + 3 个视觉问题。
    client.js 是无构建步骤注入的、拿不到 DSH 的 React，静态断言覆盖不到 DOM 行为。
    **改动任何"纯渲染"代码（关系图、时间线、图表）后，用
    `node scripts/_build-graph-harness.mjs` 生成 `.kexi-test-tmp/graph-harness.html`，
    用 `python -m http.server` 起服务后用浏览器打开 `#main` / `#empty` / `#one` 三个场景，
    并用 `getBoundingClientRect` 测重叠/溢出——不要只靠肉眼看截图。**
15. **⚠ v1.6.4：评估"插件做得对不对"必须等会话跑完，中途快照只能当"截至此刻"**。
    曾据中途快照写下"证伪成员从未被派活，角色分配有缺口"，持续跟踪后发现它
    最终**确实被派活了**——是把时序差异误报成了设计缺陷。
    评估工具：`node scripts/_track-session.mjs <sessionId> [间隔秒] [上限分钟] [日志]`，
    完成判定用复合条件（连续 3 次"非忙且无新节点"），因为 `wait_agent` 静默窗口内
    busy 也会掉，单看 busy=false 会误判。
16. **⚠ v1.6.4：host 插件代码在进程启动时加载，刷新浏览器不生效**。客户端文件
    从磁盘读（刷新即更新），服务端逻辑在内存里（必须重启 DSH）。于是会出现
    **"界面看着是新的、服务端行为是旧的"**——本次排查中 v1.6.1/1.6.2/1.6.3 三个
    版本全部没加载。卡片标题栏现在印版本号（v1.6.4）就是为了让这件事一眼可辨。
    改完代码**必须重启 DSH** 并确认徽标版本号已变，再下结论说"修好了"。
17. **⚠ v1.6.4：安装副本里 `lib/` 在包根下 3 层**（`<pkg>/home-plugin/dsh-kexi-crypto/lib/`），
    源树里包根却是仓库根。读 package.json 一律**逐级上溯按 name 匹配**，
    不要写死 `join(dirname, '..')`。
18. **⚠ v1.6.4：模型会传错参数类型，JSON Schema 不在运行时强制**。
    实测 `kexi_dashboard` 收到非字符串参数后崩在 `p.includes is not a function`，
    模型拿到原始 TypeError 无从纠正，只好改用 `kexi_run` 绕路——而那条路返回
    `out` 而非 `out_files`，成果登记不上，**一次类型错误连锁炸出两个故障**。
    新增工具的每个参数都要过 `strOr()` 之类的守卫；必填参数类型错时**回报实际类型**，
    好让模型能自我纠正。**单个节点的报错要看不出全貌——两个故障相隔一分钟，
    靠时间线串起来才看得出因果。**
19. **⚠ v1.6.4：成果扫描已放宽为"任意 .html"**（旧版只认文件名含 dashboard/看板，
    漏掉 `dashboard.py --out` 的自定义名）。**放宽的安全性完全由
    `artifactIsFresh`（必须晚于本会话开始）承担**——改这个过滤时不要顺手把
    freshness 一起放宽，否则会把上一轮的旧看板挂到当前会话。
21. **⚠ v1.7.0：`regime.py` 给的是概率标签，不是事实**。四类判据（趋势/广度/位置/拥挤度）
    加权合成；**不可观测的判据不���分**，权重重归一化并按覆盖率下调置信度；
    **置信度硬封顶 0.85**（常量 `CONFIDENCE_CAP`，别调高）；
    **失效条件永远非空且必须由实算均线值生成**。
    测试 `test-regime.py` 用**合成 K 线**、全程离线——判定器一旦依赖网络，
    它的回归测试就变成"看交易所当时心情"。加新判据时先加合成夹具。
22. **⚠ v1.7.0：位置分位刻意不给方向**（`criterion_position` 限幅 ±0.4）。
    `pos_90=95%` 在上行趋势里是健康的、在横盘里才是危险的；折成方向分会让判定器
    **在牛市顶部和熊市底部同时出错**。改这个限幅前先看 `test-regime.py` 的 ③ 组断言。
23. **⚠ 本包 `_load()` 的坑**：`d.get(key) or d.get("data") or (...)` 对**裸数组**输入
    会在 `.get` 上抛 AttributeError。`isinstance` 判断必须放在 `.get` 之前
    （`regime.py` 已修，其它脚本的同名 helper 仍有此坑）。
24. **⚠ v1.8.0：广度的两个实测局限，别当成"稳健指标"用**。
    样本按**成交额前 N** 取 → **系统性偏向赢家**（实测同一时点 N=30 得 97%、
    N=100 得 90%）。所以它反映「龙头是否领涨」而非「全市场参与」，
    识别不出"指数新高但多数币在跌"。且折算 `(p20-50)/25` 后**两端饱和**
    （90% 与 97% 都折成 1.0），强趋势中无额外分辨力。两点已写进 regime.json 的
    `criteria[].note`，会随输出一并给用户看。
25. **⚠ v1.8.0：`regime` 是市场级，`trend.direction` 是本币级，两者可以相反**
    （实测：market_regime=上行期 而 BTC trend.direction=震荡）。
    **绝不合并成一个字段**——混读会让"市场看多"被当成"这个币能买"。
    报告渲染里已明写「两者相反时以本币为准」。
26. **⚠ v1.8.0：regime 台账与 prediction_log 是两张表，不要合并**。
    后者是**逐币**（symbol、三情景区间），前者是**市场级状态**；
    同一时刻两者可以给出不同方向，合表会导致对账结论自相矛盾。
27. **⚠ v1.8.0：对账口径必须「先定后看」**（`regime_log.MATCH` 常量表）。
    看到结果再挑口径是对账的经典自欺。配套约束：样本 < `MIN_SAMPLE` 时
    只输出「样本不足，尚不能判断准不准」；无数据标 `no_data` **不计入命中率**
    （不算成"错"）；未到期的记录保持 `pending`，不许提前评分。
28. **⚠ v1.8.0：设置面板显示档位必须走 `autonomyLabel()` / `profileLabel()`**，
    不得直接插原始值——历史上就因此在中文界面里露出 `readonly`/`conservative`。
29. **⚠ v1.8.1：只有 OKX 需要 passphrase**，Binance/Gate/MEXC 只需 API Key + Secret Key。
    权威规则在 `cex_keystore.py:189`（`if ex == "okx" and not passphrase`）。
    设置面板的字段构成由 `index.mjs` 的 `CEX_CREDENTIALS` 经 `/cex credential_schema`
    下发——**这是第二份拷贝**，靠 `test-cex-credential-schema.py`（26 项）钉死一致性。
    **改任一处而不改另一处，那个测试会红。** 旧文案「仅 OKX/Gate 需要」里的 Gate 是错的。
30. **⚠ v1.8.1：React 里 `cxs` 是 state 数组不是值**。
    `const cxs = useState(null)` → 值是 `cexInfo = cxs[0]`。
    写成 `cxs.someField` 恒为 `undefined`：**条件渲染静默失效、告警永不触发**，
    `node --check` 与所有静态检查都抓不到，**只有真浏览器渲染才暴露**。
    踩过一次（schema 取不到 → 表单退回旧形态而界面无任何报错）。
31. **⚠ v1.8.1：host 存凭据不校验 passphrase 非空**——OKX 少填会「保存成功」，
    直到 Python 侧才拒，而那时用户已离开设置面板。客户端 `_cxMissing()` 先拦一道。
    注意「留空 = 不改动」，因此完整性告警只对**新账户**生效，否则会误报已配置的账户。
32. **⚠ 测试断言别用固定字符窗口定位文案**。实测：断言用「`填不完整` 之后 260 字符」
    找写死的「三样凭据齐全」，注入后 **26/26 假通过**。定位文案一律用全文件精确匹配。
33. **⚠ v1.9.0：`position_doctor.py` 从不下单**。只产出诊断 + 意见 + 订单草案，
    执行一律走 `cex_adapter` 的自主级别闸门。两道闸门分开放是有意的。
    `test-position-doctor.py` ⑧ 用**导入前源码检查**钉死（放在 import 之后的话，
    越权会让测试先崩在 import，报不出是哪条纪律被破）。
34. **⚠ v1.9.0：市场状态不得单独构成减仓理由**。regime 只用于**放大或抑制**已有问题，
    否则就成了"因为我说要跌所以卖"的循环论证。下行期 + 重仓山寨只能给 `review`
    （复核买入理由还成不成立），不能给 `reduce`。测试 ⑥ 钉这条。
    **支撑对冲腿豁免**：上行期持空头时，意见须提示「别因转多就平掉对冲」，否则敞口翻倍。
35. **⚠ v1.9.0：权重口径 = 持仓内部结构占比，不是占账户净值比例**。
    只读 `positions` 不返回余额与稳定币持仓（合约账户里 USDT 不在 positionAmt 中），
    据此推净值会系统性低估。输出里必须自报口径，否则「BTC 占 62%」会被读成总资产 62%。
36. **⚠ v1.9.0：自主级别只决定是否附订单草案，不决定给不给诊断**。
    只读档同样要出完整意见——否则用户永远看不到问题。
37. **⚠ 本项目已连续三次栽在「测试夹具没让条件真正成立」**（v1.7 夹具口径错、
    v1.8.1 字符窗口断言假通过、v1.9.0 两处）。典型形态：夹具数值看着合理，
    但离阈值差得远，于是那条纪律**从未被测到**；或者断言只测了外层字段，
    内层从 `None`改成 `0` 因为 falsy 照样通过。
    **写完夹具必须问：这个条件真的成立吗？** 注入时也要确认注入**真的落盘了**——
    此前多次出现「注入没生效」被误判成「测试有盲区」。
38. **⚠ v1.9.1：`powershell.exe -Command -` 的含义是「从 stdin 读脚本执行」**，
    不是「先执行再从 stdin 读数据」。往 stdin 灌 JSON 明文 → PowerShell 当代码解析 →
    `At line:1 char:10 … Unexpected token ':"kexi.cexkeys/1"'`。
    **正确姿势：脚本走临时 `.ps1`（纯 ASCII）经 `-File` 执行，明文只走 stdin、绝不落盘。**
    脚本不走 argv（Windows 参数转义对内嵌引号与换行极脆）。
39. **⚠ v1.9.1：Windows `powershell.exe` 是 5.1（.NET Framework）**，
    `[System.Security.Cryptography.ProtectedData]` 默认**加载不到**，
    必须 `Add-Type -AssemblyName System.Security`。
    **本机 pwsh 7.x 能直接用——在 pwsh 里试通不代表 powershell.exe 能跑。**
40. **⚠ v1.9.1：PS 5.1 的 `[Console]::In/Out` 走控制台代码页**（中文系统 GBK/936），
    不是 UTF-8。跨进程传文本必须编码无关：
    入参 `OpenStandardInput().CopyTo()` 读裸字节，出参 `ToBase64String()` + Node 侧 utf-8 解。
41. **⚠ v1.9.1：base64 出参 ≠ base64 落盘**。`verify.mjs` 曾一刀切禁 `ToBase64String`，
    误报了正确的 stdout 传输编码。真正要禁的只有「base64 包住**落盘文件**」
    （Python 按裸 DPAPI 字节流读，拿到信封必然解不开）。
42. **⚠ 字面量断言必须用剥注释后的代码视图**——断言会把自己那段
    「解释为什么要禁 `.enc.json`」的注释当成违规证据（实测误报）。
43. **⚠ 跨进程/跨编码/跨 PowerShell 版本的东西，`node --check` 与字面量匹配全是绿的，
    必须真跑一次**。v1.6.0「实测往返：密文 416 字符、可还原」**是假的**——
    `script` 变量构造了却从没被写出，整条链一次都没跑通。
    `test-dpapi-roundtrip.mjs` 28 项，且 ⑦ 组从 index.mjs 源码**抽出它自己的脚本再真跑**
    （只测副本的话，改 index.mjs 接线它毫无反应——实测确认）。
44. **⚠ "注入没落盘却被误判成测试有盲区"已发生 5 次**，多数是 PowerShell 双引号串
    把 `$var` 展开掉了。**注入后必须先确认命中，再下结论。**
44. **⚠ v1.9.6：同一类问题不止一处——审计要成组做**。发现 `open_orders`
    承诺了后端没有之后，又查出 `regime.py` / `regime_log.py` / `position_doctor.py`
    **不在 `SCRIPT_WHITELIST` 里**（模型根本调不到）。**发现一个就必须把同类全查一遍**：
    action 枚举 ↔ CLI 子命令、脚本白名单 ↔ 目录实际文件、工具入参 ↔ 脚本 argparse。
    已固化为 `test-settings-wiring.py` ⑦ 组。
45. **⚠ 第 7 次栽在「新断言没真跑起来」**。这次更隐蔽：追加时用了
    `# ══════ 汇总 ══════` 作锚点，而文件里实际是 `# ── 汇总 ──`，
    **替换成了空操作**，于是测试仍是 31/31 全绿——我差点把"没生效"当成"没问题"。
    **判断新断言是否真跑：看项数有没有变**（31→40 才说明真的执行了）。
    追加段落前**必须先确认锚点字符串在文件里真实存在**。
46. **⚠ v1.6.4：评估页不得无条件承诺"完整看板在最终成果"**。实测结论被
    `clipStr` 截断在 1200 字，而那一轮看板恰好没登记成功——提示指向空标签页，
    用户两头都拿不到完整结论。出口存在才提，不存在就直说已截断。

---

## 10. 改代码时的注意事项

- **Python 脚本零第三方依赖**，只用标准库（`urllib.request` / `json` / `argparse` /
  `statistics` / `concurrent.futures`）
- 新增脚本必须加进 `kexi-plugin.mjs` 的 `SCRIPT_WHITELIST`，并在 `verify.mjs` 加断言
- `mkTool` 的 `TEXT_SCHEMA` 是 `additionalProperties: false`
  —— **新增返回字段必须同时声明**
- 工具返回值**不得含 `undefined`**（DSH lossless JSON 校验会整体拒收）
- 结论数字必须来自脚本落盘文件，**禁止凭记忆报价或编造**；取不到就写 `null` + `warnings`
- 人设在 `cordis.patch.yml`，纪律在 `kexi-plugin.mjs` 的 policy 数组
  ——**改人设要连带改 verify.mjs 断言**
- 跨所数据必须标 `provider` 并警告"绝对值不可横比"；跨所比较请统一用
  `open_interest_usd`（美元名义）

### 10.2 改 CEX/实盘相关代码时的红线（v1.6.0 起）

这块守的是钱，比上面任何一条都重：

- **不要给 `cex_adapter.py` 的 CLI 加任何能改变自主级别的参数。**
  模型能调 `kexi_run(mode=script)` 手写 argv 传任意参数，权限一旦来自命令行
  就等于没有。`verify.mjs` 已有断言守这条（`--autonomy` / `--dry-run` / `--live`）。
- **不要把 `read_autonomy` 的任何异常分支改成"放行"**。缺文件 / 坏 JSON /
  级别非法 / 读取失败，四条路径**全部**必须退回 readonly。
  「权限读不出来」≠「可以放行」。
- **不要把 label 默认值从 `"main"` 改掉**。host 的 `cexDefaultLabel` 与
  `cex_keystore` 的 `label="main"` 必须一致，否则面板配好的密钥**静默读不到**
  （文件名对不上，不报错，只是"没配过"）。
- **凭据文件必须是 `{ex}.{label}.dpapi` 裸二进制 DPAPI**。不要为了"好看"改回
  base64 + JSON 信封——`cex_keystore.load_keys()` 解不开（这正是 v1.5.5 的死链）。
- **受限自动档的 `stop_loss` 强制检查不能删**。无止损的开仓在方向判断错误时
  等于没有风控。
- 下单路径必须走 `place_order_checked()`，不要调裸 `place_order()`——
  前者挂着 `cex_risk.check_order()` 与 `HARD_CEILING`，后者是绕过闸门的后门。
- **任何新增的写操作都要落订单意图日志**（先落 intent，再发请求，再落 result）。
  实测 37% 的模型回合失败率意味着"发了但没记住"是常态而非异常。

---

## 11. 用户偏好（沟通风格）

- 关注**实际可用性与提速**，反感"功能堆砌"
- 会直接指出"结果出来币价都涨了几个点"这类**体感问题**，期望用实测数据回应
- 期望**先快答、再深研**的分层，而不是所有问题都走完整团队
- 喜欢**基于实测的结论**（"我先量了瓶颈"），不接受"我觉得"
- 汇报时**先说结论和风险，再说细节**；坏消息要主动交代，不要藏

---

## 12. 外部方案《AlphaAgent v2.3》的吸纳情况（2026-09-30 逐条核对）

用户提供了 AlphaAgent v2.3 完整方案。那是**链上自主交易系统**的设计，
而本插件是**CEX 现货研究工具**——约 70% 的内容（钱包托管、MEV、执行路由、
Safe/Session Key、约束 RL、灰度扩资、财报）**结构性不适用**，不该硬纳入。
下表只核对概念上可迁移的部分，且以**实际文件**为准，不凭印象。

### 12.1 已吸纳（有实现，不是纸面承诺）

| 方案条目 | 本项目对应实现 | 位置 |
|---|---|---|
| 硬风控一票否决、不参与投票 | `risk.veto` 机制化：veto=true 却给正面评级或 `enter_now` → 契约校验直接拒收 | `validate_report.py` |
| R-20 多 Agent 分歧度超阈值 | `compute_disagreement()` 数值化分歧，≥0.5 强制降级「分歧观察（不入场）」 | `validate_report.py` |
| 钱包/信号衰减（滚动窗口而非全历史） | `pattern_decay()`：同一判据分别跑全历史与最近 90 根，识别模式是否已切换 + 近期冲高回落率 | `coin_profile.py` |
| R-27/R-28 市场级 F&G 情绪 regime | `fear_greed`（值 / 分类 / 7 日变化） | `derivs_sentiment.py` |
| Funding Rate / Open Interest（第一层情绪） | 含 30 日分位、拥挤度判定、`open_interest_usd` 跨所可比口径 | `derivs_sentiment.py`、`datasources.py` |
| R-32 稳定币供应 7 日变化 | `fetch_stablecoin_flow()` | `datasources.py` |
| R-09 流动性门槛 | ADV20 < $3M → 降级「流动性不足（不推荐）」；**无成交量数据时标「无法判定」而非误判为流动性差** | `fast_analysis.py` |
| R-21/R-22/R-23 FDV 与解锁压力 | `unlock_schedule.py`（逐事件 unlock_pct / days_until / impact_score）+ `portal_lookup.py` 的流通市值/FDV | 同左 |
| 消融实验（G3-M 的精神） | `ablation_compare.py` 四桶对比，并给每项标 `layer_origin` 区分 shared_engine / fast / team | 同左 |
| 先回测、再上线 | `backtest.py`（fwd_return / max_drawdown_fwd / 命中率） | 同左 |
| 复盘闭环 | `track_report.py` 战绩复盘 | 同左 |
| "情绪信号只作过滤器不作 alpha 来源" | 写入 policy：快答与团队**不因消息面改变评级方向**，只做否决 | `kexi-plugin.mjs` |
| 先回答有没有猎物 | `mode=fast` 秒级快答——1.4 秒给可执行价位，而不是 17 分钟后币已涨几个点 | 同左 |

### 12.2 部分吸纳（有骨架，能力受限——受限原因要如实说）

| 方案条目 | 现状 | 受限于 |
|---|---|---|
| 五类热点钱包 + 评分模型 + 群体信号 | `wallet_watch.py` 能扫 7 链大额转账（Blockscout，免 key），但**只有 CEX 方向判定** | Blockscout 对 CEX 地址**零实体标签**，种子地址实测命中 0 笔，`direction` 全为 `unknown`；五分类与滚动胜率需 Nansen/Dune 付费源 |
| 三层情绪指标体系 | **只有第一层**（市场级 F&G / Funding / OI / 稳定币） | 第二、三层需 LunarCrush/Santiment（$50–500/月）、Twitter API（$100/月） |
| KOL 操纵识别 | `new_listing.py` 有 `holder_concentration` | KOL 喊单时序（买入后喊单 vs 喊单后买入）需 Twitter 时间戳对齐，付费源 |
| 注意力迁移 | 仅有 `fetch_dex_hot()` 与新闻话题分布的粗信号 | 需社交提及量做分子，无法量化 |
| 门禁制度 Gate G0–G5 | 简化为"先快答 → 再深研"（policy 1b） | 无自动门禁状态机 |

### 12.3 结构性不适用（**不要为了"对齐方案"而硬加**）

- **R-01 蜜罐/貔貅/增发/黑名单**：针对 DEX 新池的可恶意合约，**CEX 现货标的不存在此风险**
- R-02 / R-10~R-13：滑点、Gas、交易频率、熔断——**无执行层**
- R-03~R-08、R-19：仓位上限、回撤、日亏限额、用户最大亏损——**无资金、无下单**
- R-14~R-18：授权签名、服务不可用、三方对账、模型漂移——无实盘执行
- MEV / Flashblocks / BSC PBS：链上执行路由
- Safe / ERC-4337 / Session Key / 冷热金库：钱包托管体系
- 约束 RL / PPO / 动作掩码：需要执行反馈闭环
- 影子交易 → 灰度 → 在线学习：需要真实成交
- Base launchpad 生态（Zora/Clanker/Flaunch/Mint Club）：链生态不同（我们分析 CEX 主流币）

> **判据**：若某条规则的触发条件是"要下单/要动钱/要签链上交易" → 不适用；
> 若是"要看数据/要判风险" → 适用且应纳入。

### 12.4 待办 → **v1.5.9 已全部处置**

> 2026-09-30 逐条处置完毕。1–4 已实现并有回归测试；5 经探测判定为**结构性做不到**，
> 改为做能做的部分并如实记录。6 维持不做。

| # | 事项 | 处置结果 | 位置 |
|---|---|---|---|
| **1** | 给团队加"唱反调"角色 | ✅ **已实现**。第五成员 `zhengfu`（证伪，委派工具 `falsification_challenger`），四项任务：拆前提（标可观测/不可观测）/ backtest 基础率 / 反事实压力测试 / 点破自欺。**防退化**：不得与望潮结论雷同；主理人不得把红队当风控意见消音，冲突时两条都呈现 | `kexi-plugin.mjs` TEAM_MEMBERS、`cordis.patch.yml` + `member-falsifier`、`client.js` 名册、`index.mjs` 映射、`SKILL.md` Workflow M + 铁律 13 |
| **2** | 决策留痕与事后校验闭环 | ✅ **链路已打通**。`prediction_log.py record` 落盘当时预测（三情景区间/评级/置信度/失效位/止损），`reconcile` 事后对账并出校准表（Brier/胜率/守住率）。**只追加不回填**，同日同标只留最保守一条 | `prediction_log.py`、`test-prediction-log.py`（56 项）、SKILL.md Workflow N + 铁律 15 |
| **3** | 跨所/跨源矛盾检测 | ✅ **已实现**。`--cross-source` 并列给出 Binance ↔ OKX 两套数字并标差异。⚠ 我在子代理实现基础上**改了一处设计**：跨所绝对额（未平仓美元名义）降级为 `informational` **不计入分歧清单**——两所体量差是结构性的（Binance $7.73B vs OKX $2.37B），25% 阈值几乎必然命中，会让清单恒非空、用户学会无视真正的分歧 | `derivs_sentiment.py`、`test-derivs-crosssource.py`（88 项） |
| **4** | 给快答加"结论置信度"分级 | ✅ **已实现**。`compute_confidence()` 输出 高/中/低 + 逐条扣分理由；**低置信自动收回「立即入场」** | `fast_analysis.py`、`test-fast-confidence.py`（90 项）、铁律 14 |
| **5** | 解锁日历接免费源 | ⚠ **探测结论：做不到**。12+ 免 key 源全试过，DefiLlama 已转 402 付费墙、其余 401/403 或只有汇总供应量，**没有任何源能给"日期+数量"的解锁明细**。因此**没有加 `--fetch`**（加了就是假日历），改为 `--verify-supply` 用汇总供应量**校验**手工录入的 `unlock_pct` 自洽性。矩阵存档 `docs/unlock-source-probe-2026-09-30.md`。⚠ 顺带查出 2Z 总量口径错 −90%（见 §9.6，**待用户决策**） | `unlock_schedule.py`、`test-unlock-fetch.py`（32 项） |
| **6** | 钱包五分类与评分 | **维持不做**——免费源拿不到标签，硬做只能靠猜测 | — |
| **★** | **（本次新发现）三情景区间结构错误** | ✅ **已修**。旧版用最深支撑当基准下沿，导致"基准"吞掉全部跌幅、"悲观"只剩 0.2% 宽的窄缝，且悲观概率反超基准成为众数档。修法：基准锚定现价、上下沿取「最近结构位」与「1× 期望波幅」较紧者；**基准恒为众数档**。必须先修——否则 #2 的对账闭环测的是个坏东西 | `fast_analysis.py build_scenarios`、`test-fast-confidence.py` |

### 12.5 自我证伪（长期，最高优先级但最难）

方案里最该被抄的不是任何功能，而是 §10.4 的消融实验精神：
> "不能证明费用后风险调整收益优于单模型，就不引入实盘。"

本项目当前的诚实状况（写在这里，别粉饰）：

- 快答（1.4 秒）与团队深研（17 分钟）的 19 项可计算数字**完全一致**，
  且已标注 `shared_engine`——因为快答 import 的就是团队用的同一批脚本。
  **这不是团队的功劳，是同代码同数据的必然结果。**
- 团队真正独有的 12 项：成员分歧仲裁、消息面证据、陈旧数据诚实标注、
  流动性一票否决、可证伪 trigger/falsify 链、置信度降级、多周期冲突识别，
  以及 v1.5.9 新增的**红队证伪**（拆前提 / 基础率 / 压力测试）。
- **v1.5.9 的进展**：#2 的预测留痕闭环**已打通**，从现在起每次出结论都会落盘，
  7 天后可自动对账。**但台账刚建立，此刻 `reconcile` 只会报 `reconciled: 0`**。
- **尚未回答**：这些独有能力，值不值得每次多花 15 分钟？
  **这需要时间积累样本，不是再写代码能解决的。**

**在 `reconciled` 样本数够之前（建议 ≥30 且覆盖多币多行情），不要宣称"团队比快答好"——
目前仍然没有证据支持这句话。**


---

## 0.2.0 升级兼容确认（2026-10-07 金标准验证）

- **本插件版本**: 1.9.17
- **目标运行时**: DSH 0.2.0-rc.2
- **验证方式**: 隔离目录安装 0.2.0-rc.2 全套依赖，用 dsh-app-boot@0.2.0-rc.2 官方 valuatePluginCompatibility 逻辑对本插件实跑
- **结论**: ✅ **PASS — 无需 version-exemption，DSH 更新后可正常加载启动**
- **关键事实**: 0.2.0 环境 react 为 18（>=18.2.0 <19），与本插件前端 peer 一致；本插件无阻塞性 @deepseek-ai/dsh peer 冲突
- 详见总台账：C:\Users\lcl\Desktop\DSH插件开发\插件版本管控与交接文档.md §6
