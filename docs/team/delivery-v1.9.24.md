# 交付说明 · dsh-kexi-crypto v1.9.24（面向用户）

> **时间基准**：互联网北京时间，来源 `https://timeapi.io/api/Time/current/zone?timeZone=Asia/Shanghai`（本机记录时 2026-11-15 17:22:08 +08:00）；
> 门禁执行器另交叉校验 `f.m.suning.com`。`worldtimeapi.org` 本机 SSL 握手失败，未使用。
> **数字与门禁原文以 `logs/build/gate-20261115-v1924.md` 为准**；门禁三件套（命令 / 输出摘要 / 日志路径）齐全项方计入 PASS；
> 未执行 / 待复核项如实标注 UNVERIFIED，**不宣称超出证据范围的交付完成**。
> **更新（2026-11-15 17:29 北京时间，Lead 修订）**：GitHub 已推送（cedae59，见 §7）；npm 处于「暂存（staged）」状态、**未公开**，见 §7 与 §5 补充。

**交付头信息**

```
版本          ：1.9.24（package.json / CHANGELOG / README / HANDOFF 四处一致）
runId         ：RUN-20261115-V1924
冻结号        ：FREEZE-20261115-V1924
基线提交      ：07cd884（HEAD 未变，未 commit）→ 交付为工作区状态
记录时间      ：2026-11-15 17:22:08 +08:00
时间来源      ：timeapi.io（主，门禁侧另交叉校验苏宁接口）
交付团队      ：K析研判团（Lead 核验交办 · 修复团 xiufu-tuan · 回归团 huigui-tuan · 沟通团 goutong-tuan）
```

---

## 1. 现象

用户在 DSH 桌面版插件管理页看到插件 **`dsh-kexi-crypto`「加载失败」**。

- 发现方：用户；根因定位方：Lead（本 session 口径：DSH 桌面版插件管理页加载失败）。
- 复现方式：打开 DSH 桌面版 → 插件管理页 → `dsh-kexi-crypto` 显示「加载失败」（重启动作见 §5）。

## 2. 根因

插件补丁（`cordis.patch.yml` delegation 组）声明了上游 DSH **0.2.2-rc.3** 已删除的子路径插件
**`dsh-tool-subagent-control/list-agents`**（该子路径导出已不存在，`list_agents` 工具早已并入主入口 `tool-subagent-control`）。
loader 报 never started → 整个 `kexi-crypto` 预设被判 broken → 插件「加载失败」。

- 直接证据：`check-preset-mount.mjs` 在 DSH 0.2.2-rc.3 上真实引导，挂载树中 `kexi-crypto` + 5 个成员预设均为 clean
  （详见 §4 证据块⑤）。
- 该失效行为纯冗余声明，删除后无功能损失（`list_agents` 由主入口注册）。

## 3. 修复

| # | 修复动作 | 结果 |
|---|---|---|
| 1 | 删除 `cordis.patch.yml` 中失效的 `tool-subagent-list-agents` 声明行（修复团 xiufu-tuan 完成） | ✅ 已完成；`- id: tool-subagent-list-agents` 声明行 0 命中（另有说明性注释记录删除原因，非 ACTIVE 行） |
| 2 | 版本 1.9.23 → 1.9.24（package.json / CHANGELOG / README / HANDOFF 四处一致） | ✅ verify.mjs 断言通过：`✓ 版本四处一致 v1.9.24` |
| 3 | 全仓 `list-agents` 残留核查 | ✅ 安装副本 reader 回核对：ACTIVE 声明行 0 命中，仅 4 处说明性注释 |
| 4 | 同步安装副本（不出 dist 包） | ✅ 见 §4 末块：首跑发现死文件 → 留证删除 → `-CheckOnly` 复证「168 文件 差异 0 / 多余 0」→ exit 0；安装副本 `version = 1.9.24` |

- 工作区 diff 指纹（门禁前/后一致）：`e6c6bd0578a333c90666dcd51a16f7be784764a1`
- **未提交 git**（纪律：本轮不提交）；R8 死文件处置见 §6。

## 4. 验证（门禁结果如实引用）

门禁报告原文：`logs/build/gate-20261115-v1924.md`。本轮 **7/7 项 PASS，全量回归 36/36**，
与 v1.9.23 不同，本轮无 FAIL 未修复项。

**证据块① 静态接线 verify**
```
[PASS] verify.mjs
  命令：pwsh -NoProfile -File logs\build\run-gate.ps1 -Name verify-v1924 -Command "node scripts/verify.mjs" -TimeoutSec 600 -RunId RUN-20261115-V1924 -Freeze FREEZE-20261115-V1924
  摘要：verify: PASS (12 顶层键 / 631 行 patch)；✓ 版本四处一致 v1.9.24；✓ plugins 18 行 / 委派工具 1 个 / 成员预设 5 个
  日志：logs/build/20261115-171426-verify-v1924.out.log（err 0 字节）
```

**证据块② 预设契约**
```
[PASS] test-preset-contract.mjs
  命令：pwsh -NoProfile -File logs\build\run-gate.ps1 -Name preset-contract-v1924 -Command "node scripts/test-preset-contract.mjs" -TimeoutSec 300 -RunId RUN-20261115-V1924 -Freeze FREEZE-20261115-V1924
  摘要：PRESET-CONTRACT: PASS
  日志：logs/build/20261115-171446-preset-contract-v1924.out.log（err 0 字节）
[PASS] test-preset-roster.mjs
  命令：pwsh -NoProfile -File logs\build\run-gate.ps1 -Name preset-roster-v1924 -Command "node scripts/test-preset-roster.mjs" -TimeoutSec 300 -RunId RUN-20261115-V1924 -Freeze FREEZE-20261115-V1924
  摘要：PRESET-ROSTER: PASS（dispose 可逆 / 可重新安装）
  日志：logs/build/20261115-171449-preset-roster-v1924.out.log（err 0 字节）
```

**证据块③ 行为回归**
```
[PASS] test-runtime.mjs
  命令：pwsh -NoProfile -File logs\build\run-gate.ps1 -Name runtime-v1924 -Command "node scripts/test-runtime.mjs" -TimeoutSec 900 -RunId RUN-20261115-V1924 -Freeze FREEZE-20261115-V1924
  摘要：RUNTIME: ALL PASS  (178 通过 / 0 失败)
  日志：logs/build/20261115-171436-runtime-v1924.out.log（err 0 字节）
[PASS] test-v1922-fixes.mjs
  命令：pwsh -NoProfile -File logs\build\run-gate.ps1 -Name v1922-fixes-v1924 -Command "node scripts/test-v1922-fixes.mjs" -TimeoutSec 300 -RunId RUN-20261115-V1924 -Freeze FREEZE-20261115-V1924
  摘要：v1.9.22 回归：PASS（104 项）
  日志：logs/build/20261115-171456-v1922-fixes-v1924.out.log（err 0 字节）
```

**证据块④ 全量回归**
```
[PASS] run-all-tests.ps1（-Quick 未使用）
  命令：pwsh -NoProfile -File logs\build\run-gate.ps1 -Name full-regression-v1924 -Command "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-all-tests.ps1" -TimeoutSec 1900 -RunId RUN-20261115-V1924 -Freeze FREEZE-20261115-V1924
  摘要：全部通过：36 / 36（elapsed 160.4s / exit 0；外层超时闸 1900s 未触发）
  日志：logs/build/20261115-171523-full-regression-v1924.out.log（err 0 字节）
  资源监控：logs/build/20261115-171523-full-regression-v1924.hw.csv（另由脚本自身落 %TEMP%\kexi-hw\hw-20261115-171523.csv）
```

**证据块⑤ 真实引导挂载（本轮修复的直接验证）**
```
[PASS] check-preset-mount.mjs
  命令：pwsh -NoProfile -File logs\build\run-gate.ps1 -Name preset-mount-v1924 -Command "node scripts/check-preset-mount.mjs" -TimeoutSec 300 -RunId RUN-20261115-V1924 -Freeze FREEZE-20261115-V1924
  摘要：MOUNT: 真调用 mount() 2 次（kexi-crypto、kexi-member-technical）；
        MOUNT: 委派行配置 toolName=subagent allowedAgentPresets=["kexi-member-market-data","kexi-member-technical","kexi-member-forecaster","kexi-member-risk","kexi-member-falsifier"]；
        PRESET-MOUNT: PASS（退出码 0 = 全部断言通过；elapsed 10.3s）
  关键断言：✓ kexi-dashboard 已激活（roster 过滤器已安装）/ ✓ clean ×6（kexi-crypto + 5 成员预设非 broken）/
        ✓ 挂载树里委派行恰好 1 行且 ACTIVE（fiberState=2）/ ✓ 挂载树里 5 个旧工具名 0 命中 / ✓ 真实工具面里 5 个旧工具名 0 命中
  执行环境：DSH 0.2.2-rc.3（check-preset-mount 默认后端 `%APPDATA%\DSH Desktop\backend\dsh`）
  日志：logs/build/20261115-171502-preset-mount-v1924.out.log
```

**证据块⑥ 安装副本同步与读回核对（task-3）**
```
[首跑 FAIL → 已处置 → 复证 PASS] sync-install.ps1（不带 -Pack）
  首跑：elapsed 1.5s / exit 1
    摘要：已同步。校验：检查 168 个文件，差异 0，多余 1（安装副本多余: home-plugin\dsh-kexi-crypto\kexi-member-presets.yml）
    日志：logs/build/20261115-171919-sync-install-v1924.out.log
  根因：v1.9.23 时期已删除的死文件 `kexi-member-presets.yml`（size 19830 B、mtime 2026-10-18 20:04:48、
        sha256 275C344011CFB45E338EBA3F958DD36F28AE51107D6DBDE9CE8FC1D4AF612AFF，与 v1.9.23 留证文件逐字节一致）疑似由
        DSH 0.2.2-rc.3 升级（2026-11-13）或插件管理器按保留 mtime 的缓存重解压带回。
  处置：先留证（logs/build/removed-installed-kexi-member-presets-v1924.yml，sha256 一致）→ 再删除该死文件 → 未改任何业务代码。
  复证（-CheckOnly）：
    命令：pwsh -NoProfile -File logs\build\run-gate.ps1 -Name sync-checkonly-v1924 -Command "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sync-install.ps1 -CheckOnly" -TimeoutSec 600 -RunId RUN-20261115-V1924 -Freeze FREEZE-20261115-V1924
    末行：校验：检查 168 个文件，差异 0，多余 0；✅ 源与安装副本完全一致（elapsed 0.9s / exit 0）
    日志：logs/build/20261115-172030-sync-checkonly-v1924.out.log
[PASS] 安装副本读回核对
  version = 1.9.24（== 仓库版本）
  cordis.patch.yml 中 `- id: tool-subagent-list-agents` 声明行 0 命中；
  delegation 组声明行：580 行 `- id: tool-subagent-control`、587 行 `- id: tool-subagent`；
  安装副本文件总数 168（与 CheckOnly 检查数一致）
```

> **不计入通过数的项（如实保留 UNVERIFIED / 待复核）**
> - **A3 stub LLM 真实委派：UNVERIFIED**。与 v1.9.23 同一卡点（`dsh-agent-loop` 单轮驱动方法名/契约在构建产物中无可读签名；
>   `llm.stream()` 事件 schema 未知），本轮未重查、未补测，按 v1.9.23 结论原样保留。
>   替代证据边界同 v1.9.23：check-preset-mount 真 mount ×2 + 挂载树委派行断言 + assert-label a–e（v1.9.23 已留证）。
>   **未覆盖的仍是「真实模型循环内的委派」这一环。**
> - **GUI 界面段：UNVERIFIED（待 Lead/用户界面复核）**。浏览器桥未连接，回归团无权使用浏览器工具；
>   本报告只证明引导挂载链路 clean，不证明用户 GUI 状态。复核清单见 §5。
> - `test-scripts-smoke.py` 2 项 INFO（backtest.py / new_listing.py 无参运行 >25s 挂起）：未验证项，如实保留，不影响 36/36。

## 5. 用户需做的重启步骤

1. **重启 DSH 桌面端**（插件与 `cordis.patch.yml` 变更必须重启才生效）。
2. **刷新浏览器页面**（前端资源有缓存，不刷新看不到新界面）。
3. 重启并刷新后，按以下清单自查（任一不符即视为未生效，请反馈）：
   - ① 插件管理页 `dsh-kexi-crypto` 的「加载失败」消失；
   - ② 智能体预设选择器中只有 1 个「K析研判团」入口，看不到 `kexi-member-*` 成员预设；
   - ③ 设置面板可打开，标题栏状态灯不消失；
   - ④ console 0 条 React hooks 报错；
   - ⑤ 委派节点显示「委派 指北·技术指标」（成员中文名，非英文 id）。
4. 若重启后仍异常，请先怀疑**缓存污染**（死文件 `kexi-member-presets.yml` 可能再现，见 §6 R8），再联系团队。

## 6. 残余风险

| 编号 | 风险 | 处置 |
|---|---|---|
| R1 | A3 真实验证缺口仍在（真实模型循环内委派未跑通） | 如实保留 UNVERIFIED；补测入口见 v1.9.23 门禁报告 §4 |
| R2 | GUI「加载失败」是否真消失未经界面验证 | 需用户重启 DSH 后按 §5 清单确认；门禁只证明引导挂载链路 clean |
| R3 | 工作区 diff 指纹含 v1.9.23 WIP + v1.9.24 修复两部分（HEAD 仍 07cd884） | 冻结以磁盘字节为准；**已于 2026-11-15 17:26 北京时间 commit `cedae59` 并推送 origin/main（见 §7）**；此前"未 commit"表述作废 |
| R4 | `cordis.patch.yml` 中仍有 2 处说明性注释提及 `tool-subagent-list-agents`（562/567 行，`#` 注释，记录删除原因） | 非 ACTIVE 声明行；权威判据为 check-preset-mount 挂载树 0 命中（已 PASS） |
| R5 | `test-scripts-smoke.py` 2 项 INFO（backtest.py / new_listing.py 无参挂起 >25s） | 未验证项，如实保留 |
| R6 | `check-preset-mount` 依赖宿主后端可用 | 本轮 exit 0；若未来不可用，按 SKIP 显式打印，不计入 PASS |
| R7 | 全量回归 verify 步骤自动清理 `__pycache__`（pruned） | verify.mjs 既有行为，未改变追踪文件指纹（前后 `e6c6bd05…` 一致）；如需零副作用请告知维护者 |
| R8 | **安装副本死文件 `kexi-member-presets.yml` 再现**：v1.9.23 删除并复证 0 多余后再次出现（mtime 2026-10-18、sha256 275C… 与 v1.9.23 留证逐字节一致） | 本次已再留证（`logs/build/removed-installed-kexi-member-presets-v1924.yml`）后删除并 CheckOnly 复证 0 多余。**根因未完全查明**：疑似 DSH 0.2.2-rc.3 升级（2026-11-13）或插件管理器按缓存重解压所致；若 DSH 后续再重装插件，死文件可能再次出现，重启后若插件管理页仍异常需排查缓存污染 |

---

## 7. 出厂包与 GitHub / npm 发布记录（2026-11-15 17:29 北京时间补记）

**出厂安装包**

| 项 | 值 |
|---|---|
| 绝对路径 | `C:\Users\lcl\Desktop\DSH插件开发\K析研判团\dist\dsh-kexi-crypto-1.9.24.tgz` |
| 文件名 | `dsh-kexi-crypto-1.9.24.tgz` |
| 字节数 | 1 068 871 |
| SHA-256 | `78D059854C4A52CDD0EB4F521180E41CF3F9BFAB31DC33DC714B5D7F4F751CCB` |
| 条目数 | 168 |
| runId / 冻结号 | `RUN-20261115-V1924` / `FREEZE-20261115-V1924` |
| 标准安装验证 | 临时目录 `npm install <tgz>`（非 npm link / 非 file: 引用）成功：version=1.9.24、6 个关键入口 `node --check` 全过、exports 解析一致（2026-11-15） |

**GitHub**

- 仓库 `new-256/dsh-kexi-crypto`（public），commit `cedae59aa2d834e38e2ce505ad0931e375d06c80` 已推送至 `main`（ls-remote 复核一致）。
- 提交信息如实注明：本提交同时承载 v1.9.23 未提交工作与 v1.9.24 修复。

**npm（重要更正：插件报告 ≠ 实际公开）**

- `dsh-kexi-crypto` 在 npm 上的最近公开版本仍为 **1.9.17**（`registry.npmjs.org` 实测，2026-11-15）；`latest` 指向 1.9.17。
- 仓库推送权限插件（`repo_push_access`）对 `dsh-kexi-crypto` 报告「发布鉴权通过」并返回 `npm.published: true`；其后
  注册表 404、`npm publish` 直接报 `E409 Cannot publish over previously staged version "1.9.24"`。
  即：插件执行的是 npm 的**暂存发布（stage publish）**，1.9.24 处于「待 2FA/人工确认」的暂存状态，**尚未对外可见**。
- `npm --userconfig %USERPROFILE%\.npmrc stage list` 返回空（暂存归属插件自己的 token 创建，本机 token 侧不可见），
  `stage-id` 无法获取，`stage approve` 需要暂存创建方的 proof-of-presence（2FA/OTP），**本机无法代为完成**。
- 待办（用户）：登录 npm 网页 `https://www.npmjs.com/package/dsh-kexi-crypto` → Packages / Staged 处批准暂存的
  1.9.24；或用暂存创建方的账号/CLI 执行 `npm stage approve <stage-id>`。
- 结论：**GitHub 已同步；npm 为「已暂存、未公开」**。不得按"npm 已发布 1.9.24"对外宣称。

---

*骨架已定稿（2026-11-15 17:22 北京时间，来源 timeapi.io）；数字与门禁原文以 `logs/build/gate-20261115-v1924.md` 为准，如有出入以门禁报告为准并以 Lead 核对结论为最终口径。*
