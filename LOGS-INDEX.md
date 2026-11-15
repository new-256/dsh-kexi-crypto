# 项目日志索引 · LOGS-INDEX

本文件是 K析研判团 的**日志总入口**。找任何阶段的过程记录，先看这里。

## 1. 阶段日志目录

| 目录 | 用途 | 谁往里写 | 生命周期 |
|---|---|---|---|
| [`logs/arch/`](logs/arch/README.md) | **架构与方案**：设计取舍、接口取证、影响面、被否决的方案 | 预设团 / Lead | 方案定稿后归档，不再改 |
| [`logs/build/`](logs/build/README.md) | **编译与门禁**：语法检查、门禁脚本输出、测试报告、硬件监控 CSV、出厂记录。入口：[`run-gate.ps1`](logs/build/run-gate.ps1)（超时+监控+全文落盘）与批次索引 [`gate-index.jsonl`](logs/build/gate-index.jsonl) | 回归团 / 预设团 | 只追加，不改写 |
| [`logs/debug/`](logs/debug/README.md) | **调试与取证**：复现步骤、原始报错全文、定位与根因、处置结论 | 体检团 / 各团 | 结案后归档 |

每个目录内各有一份 `README.md` 说明该目录的写入规范，**写日志前先读它**。

## 2. 相关但不是「阶段日志」的目录

| 目录 | 性质 | 说明 |
|---|---|---|
| [`docs/build-logs/`](docs/build-logs/) | **历史目录（v1.9.22 及更早）** | 旧体系的编译/监控留痕（`test-*.log`、`hw-monitor-*.csv`）。**保留只读，不再新增**；新日志一律进 `logs/build/`。 |
| [`docs/evidence/`](docs/evidence/) | 证据材料 | 缺陷现场快照、修复前后对照（不是按阶段划分的日志） |
| [`docs/`](docs/) | 正式交付文档 | `repair-plan-*.md`、`repair-result-*.md`、`audit-*.md` 等定稿文件 |
| `deps/` | 依赖清单 | 见 [`deps/MANIFEST.md`](deps/MANIFEST.md) |
| `docs/team/` | 团队协作与用户进度 | 见 [`docs/team/progress.md`](docs/team/progress.md)；交付说明骨架见 [`docs/team/delivery-template.md`](docs/team/delivery-template.md) |
| `dist/` | 安装包出厂产物 | `*.tgz` + 校验值记录 |

**分界口诀**：过程留痕在 `logs/`，定稿交付在 `docs/`，可安装产物在 `dist/`。

## 3. 命名与时间约定

- 日志文件：`YYYYMMDD-HHMMSS-主题.md|log|csv`；无秒级必要用 `YYYYMMDD-主题.md`。
- **时间一律取互联网北京时间**，禁止用本机系统时间冒充（用户规则 24）。
  当前可用源（2026-10-30 实测）：
  - 主源：`https://timeapi.io/api/Time/current/zone?timeZone=Asia/Shanghai` ✅
  - 备源：`https://f.m.suning.com/api/ct.do`（返回 epoch 毫秒，需 +8 时区换算）✅
  - `https://worldtimeapi.org/api/timezone/Asia/Shanghai` ❌ 本机 SSL 握手失败，取不到时**如实标注来源不可用**，不得改用本地时间。
- 日志内的相对路径以仓库根 `K析研判团/` 为基准。

## 4. 硬性纪律（摘自用户规则，直接适用）

1. 测试运行必须与**硬件资源监控**同步启动，CSV 落 `logs/build/`（规则 7）。
2. 任何测试先设**防卡死超时**，超时即强杀并输出卡死位置与最后日志（规则 12）。
3. 编译/运行日志出现 **Warning / Error 立即修正**，不得积压（规则 11）。
4. 编译错误**全文输出**并归档成文档（规则 28）。
5. 修改必须先出方案、修完再审计存档（规则 29）。

## 5. 维护责任

- 新建阶段目录或改变某目录用途时，**同步更新本文件**，否则视为未完成。
- 本文件由**沟通团**维护（当前负责人：goutong-tuan）。
- **目录说明文件归沟通团，不算越界**：`logs/arch/README.md`、`logs/build/README.md`、
  `logs/debug/README.md` 三份属「目录说明」，随本索引一起维护。它们**只写规范、不写内容**——
  `logs/build/` 里的门禁日志归回归团，`logs/debug/` 里的排障记录归体检团，各自负责。
  后续如遇写入范围争议，以本条为准（Lead 2026-10-30 裁定）。
- **入库策略见 [`.gitignore`](.gitignore) 顶部注释**：`logs/`、`deps/`、`docs/team/` 明确入库（审计依据）；
  `dist/` 整目录不入库；唯一例外 `logs/**/.current-ts`（门禁执行器的批次时间戳指针，每次运行重写、不含审计信息）。
- 最近一次更新：2026-10-30 01:32:42（北京时间，来源 timeapi.io + 苏宁时间接口交叉校验）
