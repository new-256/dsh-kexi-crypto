# K析研判团 v1.9.22 修复结果审计

- 依据诊断：`docs/diagnosis-session-44da005c.md`（session-44da005c，459 事件 / 49.8 分钟全程跟踪）
- 修复方案：`docs/repair-plan-v1.9.22.md`
- 基线版本：`dsh-kexi-crypto@1.9.21` → **`1.9.22`**
- 完成时间：2026-10-18（北京时间）
- 验收结论：**完整测试链 5/5 通过 + 现场事故端到端复现验证通过**

---

## 一、修复清单与实证对照

### D1 · 符号规范化（P0 根因）

| 项目 | 内容 |
|---|---|
| 缺陷 | 裸符号 `BTC` 从不补计价后缀，三条下游路径同时失败 |
| 修法 | 新增 `preset/kexi-crypto/lib/symbol-normalize.mjs` + `kline_utils.normalize_symbol()`，三处入口统一走唯一真源 |
| 接线点 | `kexi-plugin.mjs` 的 `mode=fast` 与 `mode=pipeline`、`fast_analysis.py`、`fetch_klines.py` |
| 关键设计 | **回显改写**：补后缀必须报告给调用方（`note` + digest + warnings），禁止静默改写 |
| 边界处理 | 纯计价币 `USD`/`USDT` 直接报错；`BTC`/`ETH`/`BNB` 本身是标的，正确补后缀 |
| 实测 | `BTC` → `BTCUSDT`，4.4 秒成功出结果 |

**端到端复现（现场同款 20 裸符号）：**
```
修复前：20/20 全部取数失败，elapsed_ms=119636
修复后：19/20 成功，elapsed_ms=22818，failed=["TONUSDT"]
```

### D2 · 真实成败语义（P0）

| 项目 | 内容 |
|---|---|
| 缺陷 | `fast_analysis.py` 恒返回 `{"ok": true}` + `return 0`，全失败也"成功" |
| 修法 | `ok = 没有任何一行失败`；全失败 `exit 3` + `ok:false` + `failed_symbols`；部分失败 `ok:true` 但 `degraded:true` |
| 插件侧 | 读脚本自身 `ok` 而非只看 exit code；向上透传 `degraded` / `failed_symbols` |
| 实测 | 假币 `ZZZFAKE,QQQFAKE` → `ok:false, exit=3` ✅ |

### D3 · 取数性能预算（P1）

| 项目 | 内容 |
|---|---|
| 缺陷 | 错误符号跑满 `5 host × 3 retry`，单币 10s、20 币 120s；K 线链路无任何预算 |
| 修法三层 | ① HTTP 400/451 **短路整条 host 链**<br>② `http_get` 支持 `deadline`，重试前检查<br>③ `fast_analysis --time-budget`（默认 90s），超预算标「未分析·超预算」而非静默跳过 |
| 追加修复 | BTC/ETH **参照币给 3s 轻预算**——它失败本就无关紧要，却会吃掉全局预算（这是 57s 的真正大头，定位靠 HTTP 逐请求插桩） |
| 实测 | 假币 2 个：`57.7s → 3.9s`（14.6×）；20 币全量：`119.6s → 22.9s` |

### D10 · 落盘快照滞后（P0，本轮新发现）

| 项目 | 内容 |
|---|---|
| 缺陷 | digest 显示数据新鲜，但写盘文件内容止于 10-05；5 币被误判停更 |
| 修法 | `fetch_klines.py` 写盘后**回读校验**：比对文件与内存的 bars 数量及末根时间戳，不符 `exit 4` |
| 附带 | 输出 `self_check` 字段供上层机械复核 |
| 实测 | `BTCUSDT` 落盘 → `self_check.ok=true, bars=119, last_closed_open=2026-10-17T08:00` ✅ |

### D4 · 产物语义校验（P1）

| 项目 | 内容 |
|---|---|
| 缺陷 | `_position.json` 与 `_indicators.json` SHA256 完全相同，工具层零校验 |
| 修法 | `kexi-plugin.mjs` 新增 `ARTIFACT_SIGNATURES` + `semanticMismatch()`：从实际字段反推数据种类，与文件名比对 |
| 关键设计 | **不自动改名**（改名会让调用方找不到文件），只标 `semantic_mismatch` |
| 实测 | 现场事故样本被正确抓出：`claims position but is indicators`；正确配对/无法识别内容均不误报 ✅ |

### D5 · CoinGecko 币表泛化（P1）

| 项目 | 内容 |
|---|---|
| 缺陷 | `CG_IDS` 硬编码 15 项且是唯一来源，表外币必然失败 |
| 修法 | 降级为**速查缓存**；未命中查 `/coins/list` 全量币表，缓存 7 天 |
| 歧义处理 | 同名多候选取市值最高者并把歧义写进 note；拿不到市值就列出候选**拒绝猜测** |
| 拆分 | 按用户规则 26 拆为独立模块 `cg_registry.py`（`fetch_klines.py` 658→538 行） |
| 实测 | `JUPUSDT→jupiter-exchange-solana`、`TIAUSDT→celestia`、`INJUSDT→injective-protocol`（三者均不在原 15 项白名单）；币表 22339 项；不存在的币明确报错 ✅ |

### D6/D7/D8/D9 · 编排层（P2）

| 编号 | 修法 | 文件 |
|---|---|---|
| D8 | Workflow D 触发词补「选币/挑 N 个/给我推荐几个/买什么好」 | `SKILL.md` |
| D7 | 新增 **Workflow O 仓位体检**（「仓位诊断」此前在 A~N 中无归属） | `SKILL.md` |
| D6 | 快速通道由纯自然语言改为**四条可操作判据**；新增「建了队就必须派活」 | `SKILL.md` + `kexi-plugin.mjs` 规则 |
| D9 | 新增「等待期不要空转」：`wait_agent` 超时后 `list_agents` + 并行推进，连续 2 次超时须通报 | `SKILL.md` + 规则 |
| 附 | 标题「五大标准工作流」→「A~O 全量枚举」（标题与内容不符会让人低估覆盖面） | `SKILL.md` |

---

## 二、文件改动

| 文件 | 类型 | 行数变化 |
|---|---|---|
| `preset/kexi-crypto/lib/symbol-normalize.mjs` | **新增** | 130 行 |
| `preset/kexi-crypto/skills/.../scripts/cg_registry.py` | **新增** | 197 行 |
| `scripts/test-v1922-fixes.mjs` | **新增** | 回归测试 90 项 |
| `docs/repair-plan-v1.9.22.md` | **新增** | 修复方案 |
| `docs/repair-result-v1.9.22.md` | **新增** | 本文件 |
| `kline_utils.py` | 修改 | +100 行（`normalize_symbol`） |
| `fetch_klines.py` | 修改 | 658 → **538 行**（拆出 D5 + 加校验） |
| `fast_analysis.py` | 修改 | +约 90 行（真实 ok / 预算 / 规范） |
| `kexi-plugin.mjs` | 修改 | +约 90 行（规范化接入 / 语义校验 / 编排规则） |
| `SKILL.md` | 修改 | +约 90 行（Workflow O / 判据 / 等待纪律） |
| `home-plugin/.../index.mjs` | 修改 | 版本常量同步 |
| `package.json` | 修改 | 1.9.21 → 1.9.22 + CHANGELOG |

---

## 三、验收结果

```
[PASS] verify                       1797ms  verify: PASS (12 顶层键 / 661 行 patch)
[PASS] test-runtime                  524ms  RUNTIME: ALL PASS  (160 通过 / 0 失败)
[PASS] test-v1922-fixes              241ms  v1.9.22 回归：PASS（90 项）
[PASS] test:py (test_all)           1866ms  🎉 全部自测试验均 100% 顺利通过！
[PASS] test-scripts-smoke         178037ms  test-scripts-smoke: 11/11 通过
```

**完整测试链 5/5 全部通过。**

端到端实测（现场同款 20 裸符号场景）：19/20 成功，22.9 秒，`degraded:true` 如实标出唯一失败的 TONUSDT。

---

## 四、验收过程中被测试抓住的问题（已修）

1. **`HTTP 451` 未短路** —— 首版只对 400 短路，本机 Binance 451 地域拦截仍会走完 host 链。补 451 分支。
2. **参照币吃掉全局预算** —— `BTC/ETH` 参照走完整 10s deadline，网络抖动时 2 个参照币就耗掉 20s。改 3s 轻预算，57.7s → 3.9s。
3. **`fetch_klines.py` 658 行超限** —— 触发用户规则 26，拆出 `cg_registry.py` 后 538 行。
4. **版本常量不同步** —— `PLUGIN_VERSION_FALLBACK` 仍为 1.9.21，被 verify 拦下（这正是它存在的意义：卡片会显示过期版本号）。

---

## 五、未做（按用户决定或需另议）

| 项 | 原因 |
|---|---|
| S4 手写 Python 的官方复核入口 | 方案里列为可选增强。当前 `kexi_run(mode=script)` 已能覆盖大部分场景，是否需要一个专门的 `verify_numbers.py` 需用户确认 |
| 编排层代码级硬拦截 | 明确不做：加"建队未派活就报错"的硬拦截会掩盖模型真实行为，违反用户规则 1（缺陷须第一时间暴露）。改为把判据写清楚 + 提供工具入口 |
| D6 的实际效果验证 | 编排层改动是提示词层，真实效果需在**下一次真实会话**中观察。本审计只保证"规则文本已正确写入且被 verify 断言"，不宣称"派活率一定提升" |

---

## 六、诚实说明

- D1/D2/D3/D4/D5/D10/D11 为**代码级修复**，有机械断言与实测数据支撑。
- D6/D7/D8/D9 为**提示词层修复**，测试只能断言"文本已正确写入"，**无法断言模型行为一定改变**。真实效果需后续会话观察。
- 本次修复**未**引入任何自动降级或容错兜底；所有失败路径均返回明确错误或如实标记，符合用户规则 1。
- `cg_registry` 的动态解析依赖网络；离线时会用 7 天内缓存，无缓存则明确报错而非猜测。
