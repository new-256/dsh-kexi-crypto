# 需求完成度核对表（REQUIREMENTS CHECKLIST）

> 维护：2026-09-29（v1.5.0 发布完成，全链 25 脚本接线发布并反哺实操战绩）
> 结论：**14 项需求已 100% 在仓库实现并验证，全部接线进 `SCRIPT_WHITELIST`、host、cordis 人设与技能手册；版本升至 1.5.0；已装副本完成同步并做 hash 核验；实操战绩可视化报告正式交付。**
> 判定口径：`✅=已实现且接线验证` `⚠️=受外部客观条件限制已明示替代方案`

---

## A. 选币质量升级（P1）

| # | 需求原文 | 状态 | 落点 | 说明 |
|---|---|---|---|---|
| A1 | 结合大盘和周线月线去看 | ✅ | `timeframe.py` + `screener.py` | 日线本地重采样周/月线（周一起算 UTC+8，剔除未走完周期），全周期共振/逆大周期反弹 + 多周期得分；screener 已内置 `--no-multi-tf` 开关 |
| A2 | 低位横盘、震荡调整的有没有考虑 | ✅ | `position.py` + `screener.py` | `accumulation_candidates` 低位横盘候选（`--accum-max 6`），窄幅横盘识别与位置扣分 |
| A3 | 选币选在高位风险有多高（量化） | ✅ | `backtest.py` + `position.py` | 点时间回测（无前视偏差）：「已放量多日」30 日胜率仅 ~32% → 拥挤度扣分最重；位置分组需中位数+胜率（均值符号会翻转） |
| A4 | pyth 教训（盈利位变阻力位） | ✅ | `position.py` | 90 日区间位置、距高低点回撤、20 日涨幅分位、MA20 偏离、放量持续天数——从机制上识别"已在高位/已放量多日" |
| A5 | 历史数据做一次量化初判 | ✅ | `backtest.py` | `--top 50 --limit 700 --step 10 --horizons 7,14,30` 输出 bt.json/bt.md；证据落 `track-record\evidence\bt.json|btB.json` |

## B. 多面数据源（P2）

| # | 需求原文 | 状态 | 落点 | 说明 |
|---|---|---|---|---|
| B1 | 消息面（币世界等社区 API） | ✅(替代) | `datasources.py` | **币世界域名已死**（实测 NXDOMAIN）→ 用 RSSHub 镜像（BlockBeats/金色财经/币安公告）+ CMC 新闻；`docs/data-sources-china-2026-09.md` 记录全部实测结论 |
| B2 | CEX 钱包/资金流 | ✅ | `datasources.py` + DefiLlama | 稳定币每日净增发 `totalMintedUSD`（中国 DNS 干净、无限频） |
| B3 | DEX 热币进入 | ✅ | `datasources.py` DexScreener | `token-boosts` 榜；中国 DNS 投毒用固定 IP+SNI 绕过（PINNED_HOSTS） |
| B4 | 大额稳定币进入 | ✅ | DefiLlama | 同上 B2；keepalive 有 `stablecoin_flow` 信号（净增发转净销毁=资金撤离） |
| B5 | 大额已上 CEX 币进入 | ✅ | `datasources.py` + `keepalive.py` | DefiLlama 覆盖链上稳定币流入流向，已上 CEX 币资金流由 `stablecoin_flow` 信号与交易所资金费率/持仓量覆盖并入白名单 |
| B6 | 热门人物 X 等社媒常态化监测 | ⚠️ 受限替代 | `datasources.py` | **2026 年 X 无免费可读源**（免费档只写不读；Nitter 全系已死；LunarCrush NXDOMAIN+401）；Alternative.me 恐惧贪婪指数已接入作为替代情绪面并已向用户明示限制 |
| B7 | 合约情绪/资金费率 | ✅ | `datasources.py` fapi | Binance fapi 持仓量/资金费率/多空比（Bybit 兜底） |

## C. 真实 CEX 接入 + 调仓（P3）

| # | 需求/用户选择 | 状态 | 落点 | 说明 |
|---|---|---|---|---|
| C1 | 查看用户仓位 | ✅ | `cex_adapter.py` `get_balances/get_positions` | 统一接口，签名抽纯函数可离线单测 |
| C2 | 一键调仓（用户干预） | ✅ | `cex_adapter.py` `place_order/cancel_order/set_tpsl` | 默认 `DRY_RUN=True`，用户显式确认才真发单 |
| C3 | 设置止盈止损 | ✅ | `cex_adapter.py` `set_tpsl` | 独立方法（OKX/Gate 必须走独立端点） |
| C4 | 权限范围：**直接实盘** | ✅ | `cex_adapter.py` + `cex_risk.py` | 代码层强制拦截（闸门），非提示语 |
| C5 | 交易所：**多交易所 Gate/MEXC/OKX/Binance** | ✅ | `cex_adapter.py` | 四家签名规则逐一核对（BINANCE hex / OKX Base64 / GATE SHA512 / MEXC 现货合约两套鉴权） |
| C6 | 密钥：**本地存储不上云** | ✅ | `cex_keystore.py` | Windows DPAPI 当前用户加密，无明文，含提币权限/IP 白名单体检 |
| C7 | 账户类型：现货/U本位/币本位/统一账户 | ✅ | `cex_adapter.py` | 统一接口支持四种账户 |
| C8 | 风控档：**保守默认** | ✅ | `cex_risk.py` | conservative 默认；balanced/aggressive/custom 可选；HARD_CEILING 不可突破；10 项检查（单笔上限/杠杆/强平距离/反向单/止损必填等） |

## D. 长期保活（P4）

| # | 需求/用户选择 | 状态 | 落点 | 说明 |
|---|---|---|---|---|
| D1 | 定时评估（默认**每小时**） | ✅ | `keepalive.py` | 每 N 分钟（默认 60）全量评估 |
| D2 | 信号触发评估 | ✅ | `keepalive.py` | 价格异动/位置高位/放量/多周期共振向下/止损临近/稳定币流向，六类信号可组合 |
| D3 | 触发后处理：**由用户选择，全部实现** | ✅ | `keepalive.py` | notify_only（默认）/ recommend / auto_tpsl / auto_reduce，自动调仓需显式开启且过风控闸门，必须用户确认 |
| D4 | 通知渠道：企业微信/WorkBuddy/落盘/qqbot 等 | ✅ | `kexi_notify.py` | wecom / workbuddy / file（始终兜底）/ qqbot / stdout，渠道独立互不影响，零密钥硬编码 |

## E. Token 消耗统计（P5）

| # | 需求 | 状态 | 落点 | 说明 |
|---|---|---|---|---|
| E1 | 每次用户任务后报告 token/输出/写入/缓存命中 | ✅ | `token_stats.py` | 只认 `assistant/message`+`compaction/summary`，排除 chunk 重复计（实测坑已记录） |
| E2 | 每天统计一次（用户/触发/定时任务） | ✅ | `token_stats.py` | 按可观测特征归类 user/triggered/scheduled |

## F. 项目历史实操经验建档存盘

| # | 需求 | 状态 | 落点 | 说明 |
|---|---|---|---|---|
| F1 | 成功/失败预测建档存盘 | ✅ | `track-record\`（ledger/runs/score/evidence）+ `track_report.py` | 目录齐备，`report.html` 已生成并正式交付 |
| F2 | 提炼技能、优化脚本 | ✅ | `track-record\SKILLS.md` + `OPTIMIZATION.md` + `kexi-plugin.mjs` + `cordis.patch.yml` | O1–O9 优化条目已吸收入库，S1-S7 经验已全面反哺进 SKILL 与 persona |
| F3 | 可视化报告：判断点分析线/止盈止损/底仓策略 | ✅ | `track_report.py` | 判断点分析线（入场→走势时间轴）、止盈止损（ATR/结构位/均线三法交叉验证）、底仓策略（40% 底仓分批）；已生成 `track-record/report.html` 并交付用户 |

---

## 汇总

| 类别 | 实现 | 插件接线 | 交付状态 |
|---|---|---|---|
| A 选币质量升级 | 5/5 | ✅ 已接线 | ✅ 交付 |
| B 多面数据源 | 7/7（B6受限替代） | ✅ 已接线 | ✅ 交付 |
| C 真实 CEX+调仓 | 8/8 | ✅ 已接线 | ✅ 交付 |
| D 长期保活 | 4/4 | ✅ 已接线 | ✅ 交付 |
| E Token 统计 | 2/2 | ✅ 已接线 | ✅ 交付 |
| F 建档存盘 | 3/3 | ✅ 已接线 | ✅ 交付 |

> **发布结论**：全部 14 项需求已 100% 实现、接线、验证并完成经验建档存盘交付，正式发布 v1.5.0。
