# K析研判团 v1.9.22 修复方案

- 依据：`docs/diagnosis-session-44da005c.md`（459 事件全程跟踪诊断）
- 方案制定：2026-10-18 17:55（北京时间）
- 基线版本：`dsh-kexi-crypto@1.9.21`
- 目标版本：`1.9.22`

---

## 零、方案总原则

1. **不引入自动降级或容错兜底**（用户规则 1）。所有修改必须让真实缺陷**第一时间暴露**：
   要么返回明确失败，要么在返回值里把问题说清楚。禁止「静默修正后假装成功」。
2. **泛用优先，禁止针对样例硬编码**（用户规则 3）。所有映射/白名单必须可扩展或动态获取。
3. **单文件 ≤600 行**（用户规则 26）。`kexi-plugin.mjs` 当前 1778 行，本次新增逻辑
   **不并入该文件**，改为新增独立模块 `preset/kexi-crypto/lib/symbol-normalize.mjs` 等。
4. **每阶段完成后编译/校验**（用户规则 16）。
5. **测试文件入专用目录**（用户规则 17）：新增测试放 `scripts/`（项目既有测试目录）。

---

## 一、缺陷与修法逐项对应

### P0-D1 符号规范化（根因）

**问题**：`BTC` 不补 `USDT`，三条下游取数路径全灭（CG_IDS 无键 / Binance 400 / OKX 51001）。
实测 `fetch_binance('BTC')` 9.38s 失败 vs `('BTCUSDT')` 0.61s 成功。
会话内二次验证：`seq 78` 裸符号 20 币全失败 → `seq 153` 改 `BTCUSDT` 后成功。

**修法**：新增独立模块 `preset/kexi-crypto/lib/symbol-normalize.mjs`，导出
`normalizeSymbol(raw)` → `{ symbol, changed, note }`：

- 去空格、转大写、去 `/` `_` `-` 分隔符
- 若已是合法计价对（以 `USDT/USDC/BUSD/USD/BTC/ETH` 结尾且基币非空）→ 原样返回
- 否则补 `USDT` 后缀，`changed=true`，`note` 说明改写内容
- 空串/非法字符 → `{ error }`，**不猜**

Python 侧同步新增 `normalize_symbol()` 到 `kline_utils.py`（已被多数脚本导入），
供 `fetch_klines.py` / `fast_analysis.py` 调用。

**关键约束**：规范化必须在**发请求之前**完成，且改写结果要**回显给调用方**
（写进 `warnings` 与返回值），不得静默改写——否则用户以为他传的 `BTC` 生效了。

**接入点**：
| 位置 | 现状 | 改为 |
|---|---|---|
| `kexi-plugin.mjs:775-780` | `.toUpperCase().trim()` | `normalizeSymbol` + 收集改写提示 |
| `kexi-plugin.mjs:849` pipeline | 同上 | 同上 |
| `fast_analysis.py:820-823` | `.strip().upper()` | `normalize_symbol()` |
| `fetch_klines.py:280` | `args.symbol.upper()` | `normalize_symbol()` |

---

### P0-D2 虚假成功

**问题**：`fast_analysis.py:894-897` 恒打印 `{"ok": true}` 并 `return 0`，即使所有币取数失败。
`kexi-plugin.mjs:799-803` 只看 exit code，得到 `ok:true` 就向上传递成功。

**修法**：
- `fast_analysis.py`：`ok` 改为**真实判定** —— 所有 row 的 `verdict.rating` 都不是
  `取数失败`/`数据不可用` 才为 `true`；全部失败时 `sys.exit(3)`。
- 新增 `degraded` 字段（部分失败）+ `failed_symbols` 列表。
- `kexi-plugin.mjs` fast 分支：读取 parsed JSON 的 `ok`，若为 `false` 或 exit code 非 0，
  **返回 `ok:false` 并把失败原因写进 `error`**，同时在 `digest` 里保留人读摘要。

**不引入兜底**：部分成功时仍返回 `ok:true`，但 `degraded:true` 且 `digest` 明确列出失败币，
让调用方必须看见。

---

### P0-D10 kexi_run 落盘快照滞后

**问题**（本轮成员实证）：digest 显示数据新鲜，但写入 `kexi_out/` 的 JSON 内容止于 10-05。
5 个币因此被误判「停更 13 天」。

**修法**：在 `kexi-plugin.mjs` 批量执行路径 `runBatch` 的 `qualityOf` 之外，新增
**写入后回读校验** `verifyWrittenFreshness(outPath, expectedFreshnessDays)`：

- 读回落盘 JSON，取 `last_closed_open` / `as_of` / `klines[-1][0]` 中可得的最新时间戳
- 与脚本 stdout 声明的 `last_bar_age_days` 比对
- 两者相差 > 0.5 天 → 记录 `stale_write` 标记进该行，`digest` 明确告警
- **不自动修复**，只如实报告（拒绝静默纠正）

同时 `fetch_klines.py` 写盘分支已存在「不覆盖更好数据」保护，需补上
**写出后回读确认**：写完立即 `json.load` 校验 `klines` 非空且末根时间等于内存中值，
不等则 `sys.exit(4)`。

---

### P1-D3 取数性能预算

**问题**：错误符号必然跑满 `5 host × 3 retry × (15s + 退避)`，单币 ~6s，20 币 ~120s。
对比 `datasources.py:145` 有 `NEWS_TIME_BUDGET_SEC=20`，K 线链路没有。

**修法**（两层）：
1. **本地短路**：符号非法（规范化失败）时**不发任何请求**，直接返回结构化错误。
   这是最有效的一层——本次事故的全部耗时都浪费在给不存在的符号发请求。
2. **全局预算**：`fast_analysis.py` 增加 `--time-budget`（默认 90s），
   每个币开始前检查 `elapsed > budget`，超预算则该币标记
   `budget_exhausted` 并**停止后续取数**，在 `degraded` 中如实报告。
   不做"跳过继续"——那会让用户拿到一个缺几个币却看似完整的名单。

附带优化：`fetch_binance` 对 **HTTP 400（Bad Request）** 直接终止整条 host 轮询链
（400 是参数错误，换 host 必然同样 400），这能立刻把 9.38s 降到 <1s。

---

### P1-D4 产物污染

**问题**：`indicators.py` 被传入 `--out ..._position.json`，工具层零校验，
静默写出指标 JSON 冒充 position 数据。

**修法**：在 `kexi-plugin.mjs` 的 `runBatch` 与 `mode=script` 路径加
**产物语义校验** `guardArtifactSemantics(script, outPath, parsedOutput)`：

- 从产出 JSON 的实际字段推导"这份数据是什么"（含 `pos_90`→position；
  含 `ma`/`rsi`→indicators；含 `entry_now`→entry_plan）
- 与 `--out` 文件名里的语义关键词比对
- 不匹配 → 该行标记 `semantic_mismatch`，`digest` 明确告警并给出正确文件名建议
- **不自动改名**（改名会让调用方找不到文件），只报错

---

### P1-D5 CG_IDS 泛化

**问题**：硬编码 15 币，白名单外币必然失败。

**修法**：
- 内置静态映射降级为**首选缓存**（命中即用，省一次请求）
- 未命中时查 CoinGecko `/coins/list`（一次请求 15k+ 币），结果缓存到
  `kexi_out/.cache/cg_coins_list.json`（TTL 7 天）
- 匹配策略：先按 `base`（去掉 USDT）精确匹配 `symbol` 字段，
  **多个候选取市值最高者**（`/coins/markets` 排序），仍不唯一则报错列出候选让调用方定夺
- 网络不可用时**不回退到"猜"**，返回明确错误

---

### P1-D11 质检缺口

**问题**：同一轮内两次同类误判（数脉误判停更、指北 17 币位置字段沿用旧口径），
全靠主理人手工复核兜住。

**修法**：把「关键数值必须可复核」落到工具层——

- `fetch_klines.py` 输出新增 `self_check` 字段：`{last_closed_open, as_of, bars, source}`，
  供上层机械比对
- `kexi-plugin.mjs` 的 `qualityOf` 已读 `bars`/`as_of`，本次补 `last_closed_open`
  并与「当前时间 − 周期」比对，识别"as_of 声称今天但末根其实是上个月"这类矛盾
- 该矛盾直接进 `flags`，与停更同级处理（会被剔除而非放行）

---

### P2 编排层

| 项 | 修法 | 文件 |
|---|---|---|
| D7 仓位诊断无工作流 | 新增 Workflow M「仓位体检」，触发词：仓位诊断/持仓分析/该不该减仓/仓位体检 | `SKILL.md` |
| D8 Workflow D 触发词缺失 | 补：选币/挑N个/给我推荐几个/买什么好 | `SKILL.md` |
| D6 未派活检测 | 规则第 0 步后追加：`kexi_run(mode=fast)` 单币问答可直接作答；**涉及选币/多币/仓位调整必须派活**，并给出判据 | `kexi-plugin.mjs` 规则文本 |
| D9 wait_agent 兜底 | 规则补充：`wait_agent` 超时后必须 `list_agents` 确认状态，连续 2 次超时须向用户通报进度而非静默重试 | `kexi-plugin.mjs` 规则文本 |
| S4 手写 Python | 新增 `kexi_run(mode=script, script="verify_numbers.py")` 官方复核入口，覆盖最高频的「独立复算」场景 | 新增脚本 + 白名单 |

> **编排层不写代码强制拦截**：`SKILL.md` 与规则文本是提示词层，加硬拦截会违反规则 1
> （掩盖真实行为）。改为**把判据写清楚 + 提供工具入口**，让模型有明确依据可循。

---

## 二、文件改动清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `preset/kexi-crypto/lib/symbol-normalize.mjs` | 新增 | JS 侧符号规范化 |
| `preset/kexi-crypto/skills/crypto-market-analysis/scripts/kline_utils.py` | 修改 | 加 `normalize_symbol()` |
| `scripts/fetch_klines.py` | 修改 | 符号规范化、400 短路、写后回读、self_check |
| `scripts/fast_analysis.py` | 修改 | 符号规范化、真实 ok、时间预算、degraded |
| `scripts/verify_numbers.py` | 新增 | 官方独立复核入口 |
| `preset/kexi-crypto/kexi-plugin.mjs` | 修改 | 接入规范化、产物语义校验、快照滞后校验、规则文本 |
| `preset/kexi-crypto/skills/kexi-team-orchestration/SKILL.md` | 修改 | Workflow M、D 触发词、派活判据 |
| `scripts/test-v1922-fixes.mjs` | 新增 | 本轮全部修复的回归测试 |
| `package.json` | 修改 | 版本 1.9.22 + CHANGELOG 段落 |
| `docs/diagnosis-session-44da005c.md` | 已有 | 诊断依据 |
| `docs/repair-plan-v1.9.22.md` | 新增 | 本文件 |
| `docs/repair-result-v1.9.22.md` | 新增 | 修复结果审计（完成后写） |

---

## 三、验收标准

每项修复必须有**可机械执行的断言**：

| 编号 | 验收断言 |
|---|---|
| D1 | `normalizeSymbol('BTC')` → `BTCUSDT` 且 `changed=true`；`normalizeSymbol('BTCUSDT')` → 原样且 `changed=false`；`normalizeSymbol('')` → error |
| D2 | 全失败时 `fast_analysis.py` exit code ≠ 0 且 `ok=false`；部分失败时 `ok=true` 但 `degraded=true` |
| D3 | 非法符号不发请求（用不可能成功的符号测，耗时应 < 100ms） |
| D4 | `indicators.py --out xxx_position.json` 被标记 `semantic_mismatch` |
| D5 | 白名单外币能通过 `/coins/list` 解析（离线测试用 mock） |
| D10 | 落盘文件末根时间与内存值不符时，`fetch_klines.py` exit 4 |
| D11 | `self_check` 字段存在且 `bars` 与 `klines.length` 一致 |
| 编排 | `SKILL.md` 含 Workflow M；D 触发词含「选币」 |

---

## 四、执行顺序

1. 阶段 A：符号规范化（D1）—— 影响面最广，先做
2. 阶段 B：真实成败语义（D2）+ 时间预算（D3）
3. 阶段 C：落盘校验（D10）+ 产物语义（D4）+ 质检（D11）
4. 阶段 D：CG_IDS 泛化（D5）
5. 阶段 E：编排层（D6/D7/D8/D9/S4）
6. 阶段 F：回归测试 + 全链验证 + 版本升级 + 审计存档

每阶段结束执行 `node scripts/verify.mjs`，失败即停下修复，不进入下一阶段。
