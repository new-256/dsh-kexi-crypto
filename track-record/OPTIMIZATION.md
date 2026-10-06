# K析研判团 · 脚本优化建议（基于战绩档案的实证）

> 建档时间 2026-09-29 · 依据：`track-record/` 三次运行战绩 + 脚本源码走查
> **重要前提**：`position.py` / `timeframe.py` / `screener.py` / `datasources.py`
> 在 **2026-09-29 04:37–04:54** 被并发修改（v1.5.0 在途），
> **本文件只提建议，未改动这些脚本**，以免与在途工作冲突。

---

## O1 · 【P0】新增 `record_run.py`：运行结束自动写台账

**问题**：三次运行的历史战绩**全靠人工事后翻产物**才建起来，
且 Run B 因未落盘基准价而无法精确评分。

**建议**：新增脚本，在流水线末尾自动追加 `track-record/ledger.jsonl`：

```
record_run.py --run-id <id> --report <top20_report.json> --out-dir <kexi_out>
```

从已有产物自动抽取：`symbol / entry_price / score / confidence / risk_grade / pbo /
三情景区间 / falsification / atr_pct / adv20_usd`，并写入 `baseline_ts_utc`。

**验收**：跑完一轮后 `ledger.jsonl` 自动多出 N 行，且**事后必能算出 `ret_pct`**。

---

## O2 · 【P0】报告契约补 `entry_price` 与 `asof`（`validate_report.py`）

**问题**：PYTH 的 verdict json **没有基准价**；Run C 第 11-20 位**没有价格区间**。

**建议**：在 `validate_report.py` 增加**硬校验**：

| 校验项 | 规则 | 违反后果 |
|---|---|---|
| `entry_price` | 每个标的必填，float > 0 | 校验 FAIL |
| `entry_price_asof` | 必填，ISO8601 | 校验 FAIL |
| 三情景区间 | `pess_range` / `base_range` / `opt_range` **三项全必填** | 校验 FAIL |
| 概率和 | `pbo` 求和 = 1.00（容差 0.01） | 校验 FAIL |
| 区间单调性 | `pess_hi ≤ base_lo` 且 `base_hi ≤ opt_lo` | 校验 FAIL |

> 现有 `validate_report.py` 已存在（21KB）。**只需追加这 5 条**，
> 就能把"事前不可证伪"变成"提交时即被拦"。

---

## O3 · 【P1】位置闸门：`hard_pos` 对「极高危」默认开启

**现状**（走查 `screener.py:231-321`）：
```python
pos = position.analyze_position(klines)
if pos and position_weight:
    pos_penalty, pos_reasons = position.position_penalty(pos)
    vetoes.extend(position.position_vetoes(pos, hard_pos=hard_pos,
                                          hard_stretch=hard_stretch))
```
`hard_pos` / `hard_stretch` **默认关闭**——位置维度**只扣分、不否决**。

**证据支持**：Run C 的 "极高危 3 档"（HBAR/PUMP/DASH）**没有任何一个被拦下**，
20 币照样交付；而团队事后对 ONDO 的定向风险判断被验证正确（-12.78%，全池最差）。

**建议**：`hard_pos` 对 `risk_grade == "极高"` 的标的**默认 True**。
高位 ≠ 必跌，但对**已判定极高危**的标的，硬否决的期望收益为正。

> ⚠️ **必须先回测再上线**——见 O6。不要凭逻辑直接改实盘行为。

---

## O4 · 【P1】`S3` 闸门：风控结论回流名单

**问题**：`assemble_report.py` 把望潮/守拙的结论**只写进报告文本**，
不参与名单生成。导致 headline 写「追高风险大于补涨机会」、名单却全是看涨标的。

**建议**：在 assemble 阶段引入**名单收缩规则**：

```
若 组合风险等级 == "高" 且 整体置信度 == "中低":
    正式名单数量 = min(请求数, 通过位置闸门的数量)
    禁止用"观察层"凑数 —— 观察层不计入交付数量
```

**依据**：Run A 正式 4 币均值 **-0.49%** vs 观察 6 币 **-3.27%**，
「宁缺毋滥」在**该轮**看起来有效（`[未验证]`，n 太小，见 `SKILLS.md` U6）。

---

## O5 · 【P1】评分脚本加入随机对照组

**现状**：`track-record/scripts/score_track_record.py` **已实现 BTC 基准**
（`benchmark_baselines()`），可算超额收益。

**缺口**：仍**没有随机等权对照组**，因此只能说"跑没跑过 BTC"，
**不能证明"选币优于随机"**。

**建议**：在 `benchmark_baselines()` 中增加：
```python
# 对照组：同期从 Top300 中随机抽 N 币等权持有
# 重复 1000 次取收益分布，报告组合所处的分位数
```
**输出**：`组合收益在随机分布中的百分位`。这才是**选币 alpha 的直接证据**。

---

## O6 · 【P1】对 v1.5.0 新维度做历史回测

**问题**：`position.py` / `timeframe.py` **刚落地（今日 04:37/04:39），零回测**。

**建议**：用**已有落盘 K 线**（`kexi_out/tech/*.json`，25 币 × 1d/4h）跑历史窗口：

| 对比组 | 说明 |
|---|---|
| A 组 | 旧 `screener.py`（无位置/多周期） |
| B 组 | 新 `screener.py`（位置扣分） |
| C 组 | 新 + `hard_pos` 硬否决 |

**指标**：命中率 / 最大回撤 / 收益分位 / **与 BTC 的超额**。
**只有 B/C 显著优于 A，O3 才允许上线。**

> 这是 `docs/evaluation-20coin-run.md` §五·建议 5 的直接落地：
> 「先做**信号回测**，用数据判断动量信号在高位的真实胜率」。

---

## O7 · 【P2】`avoid` 标的单独记账

**问题**：HBAR 被标"极高危、直接回避"，实际 **+27.79%（全池最佳）**。
现有脚本不记录回避成本。

**建议**：报告产物中，`avoid` 标的**必须仍然落盘 `entry_price`**，
使评分脚本能计算**过度规避成本**。
（当前 `ledger.jsonl` 的 `tier` 字段已支持 `avoid`，**只差产物落盘**。）

---

## O8 · 【P2】数据源降级链固化为常量

**现状**：`datasources.py` 已实现降级链，但历史两次运行都是**临时发现 451 后改脚本**。

**建议**：把实测结论固化为脚本顶部常量并加注释：
```python
# 实测 2026-09-29：api.binance.com / fapi.binance.com 本机 HTTP 451 封锁
BINANCE_PRIMARY   = "https://data-api.binance.vision"
BINANCE_FALLBACKS = ["https://api2.binance.com", "https://api4.binance.com"]
```
**验收**：新成员会话不再需要"自主探测降级链"（20 币运行中数脉为此耗了 5 轮重试）。

---

## O9 · 【P3】增量重跑（Backlog #2，仍未做）

**来源**：`docs/process-retro-v1.4.5.md` §三·建议 2。
**现状**：补新闻面后**整链重跑**，浪费已落盘的取数/指标产物。

**建议**：阶段感知——输入只变新闻层时，只重跑「组装/风控/看板」下游。
**收益**：省 token 与时间（该次整链重跑发生在 95.4% 缓存命中下，绝对浪费约 83 万 tokens）。

---

## 优先级总览

| ID | 优先级 | 动作 | 依据技能 | 风险 |
|---|---|---|---|---|
| O1 | **P0** | 自动写台账 | S1 | 低（新增脚本） |
| O2 | **P0** | 契约强制 entry_price + 三区间 | S1, S2 | 低（加校验） |
| O3 | P1 | 极高危默认硬否决 | S3, S4 | **中（需回测）** |
| O4 | P1 | 风控结论回流名单 | S3 | **中（改行为）** |
| O5 | P1 | 随机对照组 | §0 | 低（只读统计） |
| O6 | P1 | v1.5.0 历史回测 | U2 | 低（离线） |
| O7 | P2 | avoid 记账 | S5 | 低 |
| O8 | P2 | 数据源常量化 | — | 低 |
| O9 | P3 | 增量重跑 | — | 低 |

**建议执行顺序**：O1 → O2 → O6 →（有回测结论后）O3/O4 → O5 → O7/O8/O9

> **P0 两项都是"只记录、不改交易行为"的安全改动**，可立即执行。
> **O3/O4 会改变选币结果，必须先过 O6 回测**。

---

*本文件仅提建议。实施前请与在途的 v1.5.0 工作合并，避免与 `screener.py`/`position.py` 冲突。*