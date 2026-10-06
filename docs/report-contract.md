# 报告 JSON 契约 · `kexi.report/1`

> 权威实现：`preset/kexi-crypto/skills/crypto-market-analysis/scripts/validate_report.py`。
> 本文与该脚本逐条对齐；样例见 `tests/sample_report.json` 与实测产物 `kexi_out/<SYMBOL>_1d_report.json`。

研判报告是全包唯一「硬契约」对象：`assemble_report.py` 产出它、`validate_report.py` 校验/归一它、
`dashboard.py` 渲染它、`compact_report.py` 压缩它。成员结论只覆写其中 `trend` / `risk` / `verdict` 三块，
量化字段（`indicators` / `data_quality` / `symbol.price`）必须由脚本从真实 K 线生成，**禁止手填**。

## 顶层 11 键（全必填）

`schema_version` · `disclaimer` · `generated_at` · `as_of` · `symbol` · `verdict` · `data_quality` · `indicators` · `trend` · `risk` · `report_text`

| 字段路径 | 类型 | 约束（validate 实际行为） | 级别 |
|----------|------|--------------------------|------|
| `schema_version` | string | 必须 == `"kexi.report/1"` | error |
| `disclaimer` | string | 非空免责声明 | warn(空) |
| `generated_at` | string | ISO 8601 生成时间 | 必填 |
| `as_of` | string | 数据基准时间（末根收盘口径，如 `2026-09-26 08:00 UTC+8`） | 必填 |
| `symbol` | object | `name`/`code`/`interval`/`source` 为字符串；`price` 为数值 | error(缺/类型) |
| `symbol.price` | number | 严禁字符串数值 / NaN / Infinity / bool | error |
| `data_quality` | object | 见下「data_quality 子字段」 | error |
| `indicators` | object | 见下「indicators 子字段」（可为 null，缺则跳过） | error |
| `trend` | object | `direction`/`strength`/`confidence` 枚举；`scenarios` 数组 | error |
| `risk` | object | `level` 枚举；`warnings` 数组 | error |
| `verdict` | object | `headline` 字符串（≤50 字）；`key_levels` 数组 | error |
| `report_text` | string | 五段式终稿正文 | 必填 |

### data_quality 子字段（8 项全必填）

`interval_actual` · `bars` · `dropped_open_bar` · `gaps` · `duplicates_removed` · `last_bar_age_days` · `credibility` · `notes`

| 规则 | 级别 |
|------|------|
| `credibility` ∈ {可用, 部分可用, 不可用} | error |
| `bars`/`dropped_open_bar`/`duplicates_removed` 为数值（null 记"数据不足"） | warn(null)/error(类型) |
| `gaps` 为数组 | error |
| `interval_actual` != `symbol.interval`（数据源降级换粒度） | **warn**（须在结论声明真实周期） |
| 日线 `last_bar_age_days > 3`（周线 > 10） | **error** — 停更拦截（D4），标的数据不可用 |

### indicators 子字段

- `ma`: {ma5, ma10, ma20, ma60} 均数值；`ma_alignment`: 字符串
- `macd`: {dif, dea, hist} 数值 + `state`（缺 state warn）或 null
- `rsi`: {rsi6, rsi12, rsi14} 数值 + `state`（缺 warn）
- `volume_ratio`: 数值；`volatility`: {atr14, atr_pct, ann_vol, max_dd ...} 数值
- `support_resistance`: {support:[...], resistance:[...]}，每项字典含价位与依据字段
- `volume_profile`（G4）：`available=false`（无成交量源，如 CoinGecko）或含
  `point_of_control`{price,vol,pct}（POC 最大筹码峰）、`value_area`{va_high,va_low,covers_pct}
  （70% 价值区）、`hvn[]`（高量节点，强支撑/阻力）、`lvn[]`（低量真空区，易快速穿越）
  与 `bins[]`{price,vol,pct,tag}（看板横向直方图数据；tag∈POC/HVN/LVN/normal）

### trend / risk / verdict 枚举（中文定值）

| 字段 | 枚举 |
|------|------|
| `trend.direction` | 多头 / 空头 / 震荡 |
| `trend.strength` | 强 / 中 / 弱 |
| `trend.confidence` | 高 / 中 / 低 |
| `trend.scenarios[].name` | 基准 / 乐观 / 悲观（建议） |
| `trend.scenarios[].probability` | 0–1；三者和接近 1.0（>2 或 <0 error，偏离 1.0 超 0.15 warn） |
| `risk.level` | 低 / 中 / 高 / 极高（守拙定级，宁高勿低） |
| `risk.warnings` | 建议 3–6 条（否则 warn）；禁空话 |
| `verdict.headline` | ≤50 字（超长 warn） |

## 数值口径铁则

- `is_number`：None→warn、bool→error、字符串数字→error（"84433.1" 被拒）、NaN/Infinity→error、其他类型→error。
- 价格/指标全部经 `kline_utils.rn()` 自适应精度（低价币不归零），报告侧不再二次四舍五入。
- K 线格式：`[[ts_ms, open, high, low, close, volume], ...]`，时间戳毫秒，全链仅用**已收盘** bar（`drop_open_bar`）。

## 生命周期与覆写

```
assemble_report.py --klines K --indicators I --out R [--trend-json T --risk-json X --verdict-json V --unlock-json U]
  ├─ 量化字段取 K/I 真实值（含 indicators.volume_profile 筹码峰）
  ├─ trend/risk/verdict 生成规则草稿（draft:true；warnings 恒≥3）
  ├─ 若给了 --*-json（成员结论）：{...draft, ...override} 浅合并并将 draft 置 false
  └─ --unlock-json（G7 unlock_schedule 输出）：risk_flags 追加进 risk.warnings，
        risk_level_90d 更高则上调 risk.level（不覆写成员结论，只追加风险旗）
        ↓
validate_report.py --input R --fix --out R2   ← 闸门；--fix 仅安全归一（补默认/裁剪），不改判读数字
        ↓
dashboard.py --input R2 --klines K [--portfolio P] --out D.html   ← 自包含 HTML（无外链）
compact_report.py --input R2 --max-chars 900   ← 回对话的紧凑摘要（大 JSON 永不进上下文）
```

## 失败即停

`validate_report.py` 任一 error → 退出码非 0 + JSON `{"ok":false,"errors":[...]}`。
`kexi_*` 工具与成员据此阻断交付，禁止"带 error 硬出报告"。规则草稿报告本身即 `errors=[] warnings=[]`（E2E 实测）。
