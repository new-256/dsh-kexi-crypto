# 修复审计 · v1.9.20 设置面板在 DSH 0.2.x 永久不可见

- 修复时间：2026-10-18 00:40（北京时间）
- 触发：用户报「插件失效」，要求「先看目录 → 找问题 → 评估 → 再更新修复」
- 修复人：lead（含前置会话的 v1.9.19 修复：persona `{{cwd}}` 未注册变量）

## 一、现象与取证

| 面 | 证据 | 结论 |
|---|---|---|
| host 半 | `include:kexi-dashboard` enabled/fiberPhase:active；`/kexi-dashboard/status\|activity\|settings\|cex` 四端点 HTTP 200；status 返回 `version:"1.9.19", presetActive:false`；/cex 显示 OKX 已配置、autonomy=limited | 正常 |
| preset 半 | persona-prefix 仅含已注册变量 `{{model}}`；桩 ctx 实驱动 `apply()`：7 个 kexi_* 工具 + `kexi:policy` section 全部注册成功 | v1.9.19 修复有效 |
| client 半 | 实时 Slot 树（cordis inspect）存在 `conversation.session.header.utilities`，**不存在 `settings.plugin.item`** | **唯一真实缺陷** |

### 根因

`lib/client.js` 设置卡只注册键控槽位 `settings.plugin.item`。该槽位是 DSH ≤0.1.5
旧插件设置页规范，**0.1.6 重组插件页时移除**（workbuddy-auth 客户端注释、
web-search-panel 多候选落座代码双向佐证；当前宿主 0.2.1-alpha.5 后端包与
profile 包全文检索均无该槽位声明）。

落座三步全灭且全程静默：`register` 抛错 → `slots.inject` 等待永不声明的槽位
→ 20×500ms 重试后放弃，只 `console.warn`。灯与进度弹窗正常（用的是另一个
仍存在的槽位），所以外观像「插件时好时坏」，实际是**设置入口（CEX 凭据 /
自主级别 / 取数口径 / CLI 开关）在 0.2.x 从未出现过**。

## 二、修复方案

1. 新增 `seatFirst(scope, candidates, Component, label)`：按候选顺序
   `register`，**第一个成功者生效，绝不重复落座**；首轮全败再对每个候选挂
   `slots.inject`，最后 1.5s/6s 定时兜底（节奏对齐 web-search-panel）。
2. 设置卡候选槽位：
   - `plugins.row.config`（0.1.7+，key=`dsh-kexi-crypto#kexi-dashboard`，
     key 的行 id 必须与 cordis.patch.yml 的 `- id: kexi-dashboard` 一致）
   - `plugins.bundle.config`（0.1.6+，key=`dsh-kexi-crypto`）
   - `settings.plugin.item`（≤0.1.5 旧宿主兼容，新版永不触发）
3. 常量 `KEXI_PKG_NAME` / `KEXI_ROW_ID` 落字面量并加交叉注释。
4. 版本 1.9.19 → 1.9.20（package.json、PLUGIN_VERSION_FALLBACK、
   package.json description、CHANGELOG）。

## 三、防回归

| 测试 | 内容 |
|---|---|
| `scripts/test-settings-seat.cjs`（新增，12 断言） | 桩 window/document/React 加载真实 client.js，模拟 4 种宿主槽位集：0.2.x（首选 row.config 且只落一个、key 正确）/0.1.6（落 bundle.config）/≤0.1.5（落旧槽）/全缺（零注册、三槽全挂 inject、不崩） |
| `scripts/verify.mjs` | +4 断言：两个新槽位出现、旧槽位保留、row.config key 格式、必须走 seatFirst |
| `scripts/_build-settings-i18n-harness.mjs` | 抓卡顺序改为 row.config → bundle.config → 旧槽 |
| `run-all-tests.ps1` | 接入新测试；顺手修分隔线 `"="*62` 未展开的旧瑕疵 |

## 四、过程中另处理两项

1. **run-all-tests.ps1 编码事故（本次编辑引入，立即修复）**：edit 工具把原
   UTF-8-with-BOM 文件存成无 BOM，Windows PowerShell 5.1 按 GBK 解码中文 →
   字符串终结符解析失败、整个套件未跑。已转回 BOM 并 `[scriptblock]::Create`
   验证。其余被编辑文件 git HEAD 本就无 BOM，不受影响。
2. **test-scripts-smoke.py 判定盲区（环境暴露，非业务回归）**：screener.py /
   new_listing.py 参数全有默认值，无参即按设计联网发现标的；当前网络对
   Binance/CoinGecko 全部 SSL EOF（系统级网络问题），它们快速抛出带 URL 的
   网络层 RuntimeError，旧逻辑一律记为「堆栈崩溃」。改为只放行**根异常行命中
   标准库网络层签名**的情形（INFO 如实记录），TypeError/KeyError 等业务堆栈
   仍硬失败。分类器经正反样本校验；业务代码零改动。
3. **cex_adapter.py v1.9.18 修复补测试**：`place_order_checked` 收到档位名
   字符串未解析成 dict（两笔 $40 卖出因此 TypeError）的修复此前无回归覆盖，
   在 test-cex-live-safety.py 补 ⑦ 组 7 断言（42→49），并以「回退修复」验证
   断言确能复现旧 TypeError。

## 五、验证记录

- 全量 `run-all-tests.ps1`：32/32 套件通过（verify 12 键/411 行、runtime 160、
  settings-wiring 108、settings-seat 12、cex-live-safety 49、Python test_all
  19 组等）。
- 安装副本同步后 `-CheckOnly` 哈希 0 差异；npm pack dry-run 见下。
- 遗留：① 当前系统网络对外 SSL 全断，无法跑真实行情端到端；
  ② 最终视觉验收需重启 DSH + 刷新浏览器后在「插件 → dsh-kexi-crypto 行配置」
  确认设置卡渲染（client 包只在启动时加载）。
