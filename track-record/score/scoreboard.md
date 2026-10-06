# K析研判团 · 战绩评分表（scoreboard）

> 评分时刻（UTC）：2026-09-28 21:03:36
> 行情源：`data-api.binance.vision`（api.binance.com 451 封锁）

## ⭐ BTC 基准对照（区分 alpha / beta 的关键）

| run_id | BTC 基准价 | BTC 现价 | BTC 收益% | tier | 组合均值% | **超额%** |
|---|---|---|---|---|---|---|
| runA-10coin-20260927 | 84472.0 | 83646.01 | -0.98 | formal | 0.5 | **1.48** |
| runA-10coin-20260927 | 84472.0 | 83646.01 | -0.98 | watch | -2.63 | **-1.65** |
| runB-pyth-20260928 | 83648.54 | 83648.55 | 0.0 | watch | 0.71 | **0.71** |
| runC-top20-20260927 | 84472.0 | 83648.54 | -0.97 | formal | -0.43 | **0.55** |

> **超额为负 = 组合跑不过只持有 BTC**。此时「选币能力」不成立，
> 收益完全由市场 beta 解释。这是本档案最重要的判据，优先于胜率。

## 分层汇总

| tier | 样本 | 均值收益% | 胜 | 胜率% | 均值Brier | 证伪数 | 情景落点分布 |
|---|---|---|---|---|---|---|---|
| formal | 24 | -0.27 | 7 | 29.2 | 0.4752 | 0 | {'base': 8, 'opt': 1, 'pess': 1, 'unknown': 14} |
| watch | 7 | -2.15 | 3 | 42.9 | 0.4376 | 0 | {'unknown': 6, 'base': 1} |

## 明细

| run_id | 币 | tier | 入场价 | 现价 | 收益% | 落点区间 | Brier | 已证伪 | 判定 |
|---|---|---|---|---|---|---|---|---|---|
| runA-10coin-20260927 | LINKUSDT | watch | 14.024 | 15.402 | 9.83 | unknown | None | False | pending |
| runA-10coin-20260927 | ALGOUSDT | formal | 0.1312 | 0.1367 | 4.19 | unknown | None | False | pending |
| runA-10coin-20260927 | RUNEUSDT | watch | 0.76 | 0.786 | 3.42 | unknown | None | False | pending |
| runA-10coin-20260927 | VIRTUALUSDT | formal | 0.841 | 0.8396 | -0.17 | unknown | None | False | pending |
| runA-10coin-20260927 | PUMPUSDT | formal | 0.005155 | 0.005137 | -0.35 | unknown | None | False | pending |
| runA-10coin-20260927 | GRAMUSDT | formal | 1.631 | 1.604 | -1.66 | unknown | None | False | pending |
| runA-10coin-20260927 | POLUSDT | watch | 0.12208 | 0.1169 | -4.24 | unknown | None | False | pending |
| runA-10coin-20260927 | BABYUSDT | watch | 0.01413 | 0.01325 | -6.23 | unknown | None | False | pending |
| runA-10coin-20260927 | DOTUSDT | watch | 1.268 | 1.18 | -6.94 | unknown | None | False | pending |
| runA-10coin-20260927 | ONDOUSDT | watch | 0.5877 | 0.5194 | -11.62 | unknown | None | False | pending |
| runB-pyth-20260928 | PYTHUSDT | watch | 0.07936 | 0.07992 | 0.71 | base | 0.4376 | False | pending |
| runC-top20-20260927 | HBARUSDT | formal | 0.09586 | 0.12339 | 28.72 | unknown | None | None | pending |
| runC-top20-20260927 | ALGOUSDT | formal | 0.1195 | 0.1368 | 14.48 | opt | 0.7592 | False | pending |
| runC-top20-20260927 | LINKUSDT | formal | 14.024 | 15.392 | 9.75 | unknown | None | False | pending |
| runC-top20-20260927 | RUNEUSDT | formal | 0.76 | 0.786 | 3.42 | unknown | None | False | pending |
| runC-top20-20260927 | NVDABUSDT | formal | 224.68 | 228.88 | 1.87 | base | 0.3624 | None | pending |
| runC-top20-20260927 | JSTUSDT | formal | 0.12644 | 0.12797 | 1.21 | base | 0.335 | None | pending |
| runC-top20-20260927 | VIRTUALUSDT | formal | 0.841 | 0.8392 | -0.21 | base | 0.3296 | False | pending |
| runC-top20-20260927 | PUMPUSDT | formal | 0.005155 | 0.005137 | -0.35 | base | 0.3624 | False | pending |
| runC-top20-20260927 | SEIUSDT | formal | 0.07903 | 0.07845 | -0.73 | base | 0.335 | False | pending |
| runC-top20-20260927 | GRAMUSDT | formal | 1.631 | 1.604 | -1.66 | base | 0.3552 | False | pending |
| runC-top20-20260927 | PLUMEUSDT | formal | 0.01876 | 0.01795 | -4.32 | unknown | None | None | pending |
| runC-top20-20260927 | POLUSDT | formal | 0.12208 | 0.1168 | -4.33 | base | 0.335 | False | pending |
| runC-top20-20260927 | SKYUSDT | formal | 0.08229 | 0.07852 | -4.58 | base | 0.3678 | None | pending |
| runC-top20-20260927 | JTOUSDT | formal | 0.5964 | 0.5657 | -5.15 | unknown | None | None | pending |
| runC-top20-20260927 | DASHUSDT | formal | 68.58 | 64.9 | -5.37 | unknown | None | None | pending |
| runC-top20-20260927 | BABYUSDT | formal | 0.01413 | 0.01325 | -6.23 | unknown | None | False | pending |
| runC-top20-20260927 | ATOMUSDT | formal | 1.886 | 1.758 | -6.79 | pess | 1.2102 | None | pending |
| runC-top20-20260927 | DOTUSDT | formal | 1.268 | 1.18 | -6.94 | unknown | None | False | pending |
| runC-top20-20260927 | LDOUSDT | formal | 0.4943 | 0.4464 | -9.69 | unknown | None | None | pending |
| runC-top20-20260927 | ONDOUSDT | formal | 0.5877 | 0.5192 | -11.66 | unknown | None | False | pending |

## 解读须知（勿跳过）

1. 全部样本 `horizon_days=7` 未满，**verdict 一律 pending**，当前数值仅为中期快照。
2. Run A 与 Run C 基准相同、标的重叠 10 个，**不是独立样本**，不可叠加当 2 轮。
3. 尚未加入「随机等权 N 币」对照组 → 只能对比 BTC，**不能证明选币优于随机**。
4. `hit_zone=unknown` 表示该项原始预测未落盘三情景区间（报告只给了部分币），非 0 命中。
5. `outside` 表示实际价超出三情景区间全部范围 = 模型尾部盲区，须单独计数观察。
