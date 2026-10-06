# 安装 · INSTALL

适用：DSH ≥ 0.1.7-rc（Web GUI / profile `web`）。本包是标准 npm bundle，
经 `dsh plugin` CLI 安装，无需手工改任何宿主配置。

## 1. 前置条件

| 项 | 要求 | 检查 |
|----|------|------|
| DSH | ≥ 0.1.7-rc，且使用 `web` profile（本包 client 是浏览器模块） | `dsh --version` |
| Python | ≥ 3.9，**仅需标准库**（脚本链零第三方依赖，不需要 pip install） | `python --version` |
| 网络 | Binance / CoinGecko 公共 API 可达（国内环境建议自备代理） | `kexi_run` 返回 `source` 字段可确认实际数据源 |

## 2. 安装

```powershell
# 方式 A：从本地开发目录安装（开发迭代用，改文件后 dsh restart 即生效）
dsh plugin --profile web add "C:\Users\lcl\Desktop\DSH插件开发\K析研判团"

# 方式 B：打包为 tgz 分发安装
npm pack                                   # 产出 dsh-kexi-crypto-1.0.0.tgz
dsh plugin --profile web add .\dsh-kexi-crypto-1.0.0.tgz

dsh restart
```

验证四件事（任一失败见 §6 排错）：

1. 新会话的预设下拉里出现「**K析研判团**」（描述：加密货币趋势研判 5 人专家团）；
2. 在该预设会话里问"有哪些技能可用"，能看到 `crypto-market-analysis`、
   `kexi-team-orchestration`、`token-budget-discipline`；
3. 该预设会话标题栏出现「K析」状态灯（未研判时显示"K析 就绪"）；
4. 说"研判一下 ETH"，灯变 ⟳ 并逐步推进，最终落盘 `kexi_out/` 并交付看板。

也可先跑静态自检（开发机改包后必做）：

```powershell
node scripts/verify.mjs     # 期望最后一行: verify: PASS
```

## 3. Python 解释器指定

`kexi-plugin.mjs` 按以下顺序探测解释器（命中即用）：

1. 环境变量 `KEXI_PYTHON`（推荐显式设置，最高优先级）
2. `C:\Python313\python.exe`（开发机实测路径）
3. `python`（PATH）
4. `py`（Windows launcher）

```powershell
# 持久化（当前用户）
[System.Environment]::SetEnvironmentVariable('KEXI_PYTHON','D:\envs\trading\python.exe','User')
# 然后重启 DSH 使宿主进程继承
```

成员/主理人在 `kexi_*` 工具不可用时也会用 `pwsh` 直接跑脚本（SKILL.md 的 CLI 手册），
那条路径走 shell 里的 `python`——同样建议保证 PATH 里有解释器。

## 4. 升级

```powershell
dsh plugin --profile web remove dsh-kexi-crypto
dsh plugin --profile web add "C:\Users\lcl\Desktop\DSH插件开发\K析研判团"   # 或新 tgz
dsh restart
```

`kexi_out/` 在工作区内，升级不清除历史产物。用户层个性化配置（见 §5）不受升级影响。

### v1.5.0 升级要点（重大功能升级与历史战绩交付，务必重启）

从旧版本（v1.4.x）升级至 **v1.5.0**：
- **全链 25 脚本白名单接线**：11 个新脚本已全部并入 `SCRIPT_WHITELIST`，包括选币位置画像 (`position.py`)、多周期 (`timeframe.py`)、回测 (`backtest.py`)、多面数据源 (`datasources.py`)、CEX 密钥库与实盘风控 (`cex_keystore.py`/`cex_risk.py`/`cex_adapter.py`)、保活与通知 (`keepalive.py`/`kexi_notify.py`)、Token 统计 (`token_stats.py`)、战绩可视化报告 (`track_report.py`)。
- **安全与风控**：CEX 密钥仅保存在当前 Windows 用户的 DPAPI 本地加密存储中，严禁开通提币权限；所有下单操作默认 `DRY_RUN=True`，保守档硬性风控闸门拦截，任何真实资金操作必须用户显式确认。
- **实操战绩建档交付**：生成 `track_report.html` 单文件自包含离线报告（判断点分析线、三法交叉止盈止损、底仓分批策略），并在 `track-record/` 归档。
- **必须重启 DSH Desktop**：更新安装或同步副本后，必须彻底重启 DSH 桌面端使全新 bundle 与 preset patch 生效。
- **自检**：
  ```powershell
  node scripts/verify.mjs
  node scripts/test-runtime.mjs
  ```

### v1.2.1 升级要点（D9 修复，务必重启）

若你从 v1.2.0 及更早升级，**本版本修的是"成员委派通道不可用"**：

- 症状：会话里出现「四位成员委派通道不可用（subagent depth 1 exceeds maxDepth 0）」，
  主理人只能自己驱动工具链。
- 根因：`maxDepth` 是**绝对深度上限**（`dsh-subagent` 里 `childDepth = 父 depth + 1`），
  成员行配 `0` 时主理人（depth 0）派成员 `childDepth=1 > 0` → 首次委派即被拒。
- 修复：成员行改 `maxDepth: 1`（成员可被派、成员再派时 childDepth=2>1 被拦）。
- **必须重启 DSH**：这是 patch（组合层）改动，`patchReload: live` 只热更已组合
  bundle 的补丁编辑，成员行深度预算在组合时算定，重启才生效。
- 自检：`dsh restart` 后开新会话让它委派任一成员（如"让指北算一下 BTC 指标"），
  不再出现 depth 报错即已修复；也可 `node scripts/verify.mjs` 看到
  `成员 maxDepth 必须=1` 断言通过。

## 5. 个性化 / 用户层覆盖

补丁是分层合成的：**bundle 层（本包）→ 用户层（后写覆盖先写）**。
不要直接改安装进 node_modules 的包文件（升级即丢），覆盖放用户层：

```
%APPDATA%\DSH Desktop\dsh-home\cordis.patch.yml     ← 用户层（同 id 整行替换）
```

常用覆盖示例——换主理人人设文案：复制本包
`home-plugin/dsh-kexi-crypto/cordis.patch.yml` 里整个 `- insert:` 的
`preset-kexi-crypto` 块，改 `config.plugins` 中 `persona.config.prefix`，
写入用户层文件（行 id `preset-kexi-crypto` 相同即整体替换 bundle 层声明）。

其他常被覆盖的项：`tool-result-pruner` 阈值（pruner 组在 `compaction` 组内，需整组复制覆盖）、
成员 persona 文案、`tool-present.maxFiles`。

## 6. 卸载

```powershell
dsh plugin --profile web remove dsh-kexi-crypto
dsh restart
```

## 7. 排错 10 条

1. **预设列表没有「K析研判团」**：忘 `dsh restart`；或装到了别的 profile（`--profile web` 必须显式对得上）；看 DSH 启动日志有无 `cordis.patch.yml` 解析报错（YAML 缩进/`!!js` 语法）。
2. **灯完全不存在（连普通会话有活动时也不出）**：灯由 host `webServer` 路由提供，TUI-only 进程没有 webServer 属预期；Web GUI 下检查 DevTools 网络里 `/kexi-dashboard/status` 是否 404（404=host 半没挂上，看日志 `kexi-dashboard`）。
3. **灯出现但恒"就绪"从不 ⟳**：preset 插件行没挂上——`verify.mjs` 检查 `kexi-plugin` 行的裸名 `dsh-kexi-crypto/kexi-plugin` 能否被 Node 解析（包必须经 `dsh plugin add` 安装而非裸拷贝）。
4. **技能缺失（skill 列表没有 crypto-market-analysis）**：`customSkillDirs` 的 `!!js` 表达式用
   `createRequire(baseUrl)` 解析 `dsh-kexi-crypto/package.json` 再拼 `preset/kexi-crypto/skills`——
   **包名锚定**，整包改名/换盘符/移动目录都仍然有效（`baseUrl` 在 host 行是补丁目录、在 preset 行是
   profile 根，两种场景都命中，见 D8）。⚠ 若在 YAML 里把 `!!js` 写成**列表项**（`- !!js` 且表达式返回数组），
   值会嵌套成 `[["path"]]`，schemastery 报 `expected string but got <path>` 并整行加载失败；
   必须写在**值位置**且返回 `string[]`。跑 `node scripts/verify.mjs` 应打印 `skills root OK` 且
   `customSkillDirs 为值位置 !!js（形态正确）`。
5. **kexi_run 报「无法启动 Python」**：四个候选全失败——设 `KEXI_PYTHON`（§3）；确认路径无引号嵌套问题；WSL 内 Python **不可用**（宿主在 Windows 侧 spawn，需 Windows 版解释器）。
6. **fetch_klines 超时/HTTP 451/空数组**：Binance 地域限制或网络问题——工具会自动降级 CoinGecko；若返回里 `interval_actual` ≠ 请求粒度（如 4d），**接受但必须在结论里声明口径**；两源都挂则如实报告，勿手填数据。
7. **validate 报 `数据停更` error（last_bar_age_days > 3）**：这是 D4 防御在工作——该标的数据不可用，换标的；若确是网络时间异常（时钟错乱）先修系统时间。
8. **看板打不开/空白**：`dashboard.py` 只吃**通过契约**的 report.json（先 validate --fix）；确认打开的是 `.html` 不是 `.md`；文件应无任何外链（本包看板承诺完全离线，若出现外链说明输入报告被手工污染）。
9. **中文乱码（看板/报告）**：脚本全部 `encoding="utf-8"` 落盘；若编辑器按 GBK 打开会乱码——用 UTF-8 打开；PowerShell 旧版控制台显示乱码不影响文件内容。
10. **多会话并行灯混乱**：灯按**工作目录**分列（同名目录会合并显示最新一条），这是设计而非 bug；详情面板里每条带完整 cwd 可区分。
11. **进度弹窗不自动弹**（v1.4.2 起为设计行为）：弹窗**只由用户点标题栏状态灯手动唤出**，不随任务开跑自动弹出。若点了灯仍无节点，检查 `showProgressPopup` 是否被关（总开关），或当前没有"正在跑"的会话（`busySessions=0`）。
12. **进度弹窗有节点但不更新**（v1.2.0）：节点来自 host 的 `session/event` 订阅——若 DSH 版本该事件改名，会静默退化为只有研判灯（不影响研判本身）。检查 `/kexi-dashboard/activity` 返回 `sessions` 是否为空。
13. **`kexi_cli` 报没有可用 CLI**（v1.2.0）：这是**诚实降级**而非故障——对应 CLI 桥（agy/codebuddy/mimo first-bridge）没装，或已在设置面板里关掉。装好后新会话即生效；**不要**为了"让它有用"去谎报 CLI 参与。
14. **设置改动似乎没生效**（v1.2.0）：配置落盘在 `$DSH_HOME/kexi-settings.json`（不是包内）；数值有边界收敛（如 `maxEventNodes` 夹在 20–200），越界提交会被静默夹回；`kexi-plugin` 侧对设置文件有 30s 缓存，改完等一轮或重开会话。

## 8. 开发循环（改本包后快速验证）

```powershell
node scripts/verify.mjs                  # 静态契约 + v1.2.0 接线断言
node scripts/test-runtime.mjs            # 运行时行为回归（53 项：活动流/设置/kexi_cli）
& C:\Python313\python.exe preset\kexi-crypto\skills\crypto-market-analysis\tests\test_all.py   # 脚本链回归 9 组
node .scratch/smoke.mjs                  # 功能冒烟（host 路由+pipeline 真跑）  ※ .scratch 不随包分发
dsh restart                              # 本地路径安装时改完即生效
```

> **v1.2.0 起新增 bundle 行/依赖需要重启**：`patchReload: live` 只热更**已组合** bundle 的补丁
> **编辑**；新装的 bundle（或新增的依赖）要重启 DSH 进程才会进入内存配置。
> 改 `client.js` 则只需刷新浏览器（客户端 bundle 由 HMR/重载通道处理）。
