# K析研判团 · 历史实操战绩档案（Track Record）

> 用途：把每一次研判的**预测**与事后**实际走势**对齐存盘，形成可回测、可迭代、可提炼技能的闭环。
> 建档日期：2026-09-29 · 建档时点行情：2026-09-28 20:45 UTC（Binance serverTime 实测对齐）
> 数据源：`data-api.binance.vision`（本机 `api.binance.com` / `fapi.binance.com` 为 **HTTP 451 地区封锁**，见下文「数据源约束」）

---

## 1. 目录结构

```
track-record/
├── README.md              # 本文件：口径、schema、评分规则
├── ledger.jsonl           # 主台账：一行一条预测（机器可读，追加式）
├── runs/                  # 每次运行的原始快照（不可变）
│   ├── runA-10coin-20260927.md
│   ├── runB-pyth-20260928.md
│   └── runC-top20-20260927.md
├── score/                 # 评分产物（可由脚本重算覆盖）
│   ├── scoreboard.md      # 人类可读排行榜
│   └── score-<date>.json  # 某次快照的机读评分
└── scripts/
    └── score_track_record.py   # 从 ledger.jsonl + 实时行情重算战绩
```

**设计原则**：`runs/` 是不可变的事实层（原始预测，永不改写）；`ledger.jsonl` 是结构化的可计算层；`score/` 是可丢弃可重算的派生层。任何一次修订只能追加新记录，不能原地改历史预测——否则无法诚实评估。

---

## 2. 预测记录 Schema（ledger.jsonl 每行一个 JSON 对象）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `run_id` | string | ✅ | 运行唯一标识，如 `runC-top20-20260927` |
| `run_date` | string | ✅ | 预测作出日（`YYYY-MM-DD`，以基准 bar 为准，非会话日期） |
| `baseline_ts_utc` | string | ✅ | 基准收盘时间戳（ISO8601 UTC），**评分起算点** |
| `horizon_days` | int | ✅ | 预测视界（本项目统一 7 日） |
| `score_due_utc` | string | — | 到期时间，= `baseline_ts_utc + horizon_days` |
| `symbol` | string | ✅ | 统一符号，Binance 现货格式（如 `ALGOUSDT`） |
| `tier` | enum | ✅ | `formal`（正式入选）/ `watch`（补涨观察）/ `excluded`（调出，记反事实）/ `avoid`（明确回避） |
| `direction` | enum | ✅ | `bull` / `bear` / `neutral` |
| `entry_price` | float | ✅ | 基准锚价（报告落盘值，禁止事后回填） |
| `entry_price_asof` | string | ✅ | 该价的 bar 时间，因 1d/4h 口径可能不同 |
| `score_at_pick` | float | — | 指北综合分 |
| `confidence` | enum | — | `中高`/`中`/`低` |
| `risk_grade` | enum | — | `极高`/`高`/`中`/`低` |
| `pess_base_opt` | [f,f,f] | — | 望潮三情景概率（悲观/基准/乐观），和应为 1.00 |
| `opt_range` | [f,f] | — | 乐观区间（价格） |
| `base_range` | [f,f] | — | 基准区间 |
| `pess_range` | [f,f] | — | 悲观区间 |
| `falsification` | float | — | 证伪线（日线收盘跌破即判逻辑失效） |
| `atr_pct` | float | — | ATR%（风险度量基准） |
| `adv20_usd` | float | — | 20 日均额（流动性） |
| `notes` | string | — | 备注（特殊提示、数据缺口） |

### 评分结果字段（由脚本计算后追加，不覆盖原字段）

| 字段 | 说明 |
|---|---|
| `score_ts_utc` | 评分时刻 |
| `current_price` | 评分时价 |
| `ret_pct` | 相对 `entry_price` 的收益率（%） |
| `hit_zone` | 实际落点属于 `opt`/`base`/`pess` 哪一个区间（仅到期或已可判定时） |
| `falsified` | bool：期间日线收盘是否已跌破证伪线 |
| `max_dd_pct` | 期间最大回撤（需 K 线，脚本拉取） |
| `verdict` | `correct` / `partial` / `wrong` / `pending` |

---

## 3. 评分规则（必须显式、可争议）

**核心原则：不给自己留解释空间。** 概率分布型预测的评分必须用「实际落点命中哪个区间」判定，不能靠事后叙述。

1. **区间命中制（主判据）**
   - 实际价落入 `opt_range` → 乐观情景命中
   - 落入 `base_range` → 基准情景命中
   - 落入 `pess_range` → 悲观情景命中
   - 极端超出三区间 → `outside`（模型未覆盖尾部的次数要单独统计，这是尾部风险盲区的直接度量）

2. **Brier 分数（概率校准）**，对三情景做 one-vs-rest：
   - 命中情景 `BS = (p_hit - 1)² + Σ_{非命中} p_j²`
   - 越低越好（完美 = 0）。**只有 Brier 才能区分「给 56% 中了」和「给 34% 也中了」**，单看命中率会高估能力。

3. **证伪线纪律**：`falsified=true` 即该标的研判**判错**，无论最终收益是否为正。跌破证伪线后反弹获利属于运气，不计入正确。

4. **分层统计**：`formal` / `watch` / `avoid` **必须分开算**。
   - 把 `avoid`（明确回避）的标的算进组合收益会污染结果。
   - 若 `avoid` 标的实际大涨，要单独记为「过度规避成本」。

5. **基准对照**：每轮必须同期记录 **BTC 收益率**作为 beta 基准，以及**等权随机 N 币**的期望。
   - **超额收益 = 组合收益 − BTC 收益**。若组合跑不过 BTC，则「选币能力」不成立，只是承担了更高 beta。
   - 当前尚无对照组，这是档案的**已知缺口**（见 §6）。

6. **未到期处理**：`horizon_days` 未满时 `verdict = pending`，**禁止用中期收益提前宣布胜负**。中期快照可记录，但标记 `interim=true` 且不进汇总。

---

## 4. 数据源约束（实测，2026-09-29）

| 端点 | 状态 | 用途 |
|---|---|---|
| `https://data-api.binance.vision` | ✅ 可用 | **首选**行情源 |
| `https://api.binance.com` | ❌ HTTP 451 地区封锁 | — |
| `https://fapi.binance.com` | ❌ HTTP 451 地区封锁 | — |
| `https://api2/api3/api4.binance.com` | ✅ 可用 | 降级镜像 |

> ⚠️ **评分脚本必须走 `data-api.binance.vision`**。历史两次运行（Run A / Run C）都因 `api.binance.com` 451 而被迫降级，这个坑会持续存在。

---

## 5. 已归档运行索引

| run_id | 日期 | 任务 | 标的数 | 状态 |
|---|---|---|---|---|
| `runA-10coin-20260927` | 2026-09-27 | 选 10 只走势较好的币 | 10（正式 4 + 观察 6） | 已评分（中期） |
| `runB-pyth-20260928` | 2026-09-28 | PYTH 单币研判 | 1 | 已评分（中期） |
| `runC-top20-20260927` | 2026-09-27 | 挑选 20 个近期看涨币 | 20 | 已评分（中期） |

> 三次运行的基准 bar 均为 **2026-09-27 日线**，7 日视界到期日 **2026-10-04**。
> 因此当前所有评分都是 **interim（中期）**，最终判定须等到 2026-10-04。

---

## 6. 已知缺口（诚实披露）

1. **无对照组 / 无回测基线**：尚未记录「同期随机等权 N 币」与「BTC 持有」的收益，因此**无法区分选币 alpha 与市场 beta**。待办：脚本加入 BTC 基准列。
2. **样本量极小（3 轮 / 31 条预测）**：任何胜率统计都不具统计显著性。**在样本 < 30 轮之前，禁止把胜率当作能力证据。**
3. **仅 1d/4h 技术面**：三次运行的消息面/链上资金面**全部缺失**（新闻通道 401/被拦、`kexi_news` schema bug），因此无法评估「基本面信息是否改善选币」。
4. **运行间的预测相互重叠**：Run A 与 Run C 有 10 个标的重叠且基准相同，**不能当作 2 个独立样本**。
5. **未记录「期间是否触及止损/失效位」**：需拉 K 线补算 `max_dd_pct` 与 `falsified`。

---

*本档案由开发侧智能体建立。原始预测文本保留在 `runs/`，未经任何美化或事后修正。*