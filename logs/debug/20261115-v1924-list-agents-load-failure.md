# v1.9.24 排障结案：DSH 0.2.2-rc.3 下插件 GUI「加载失败」（list-agents 子路径行失效）

- 时间：2026-11-15 17:12（北京时间，来源 timeapi.io `Asia/Shanghai`）
- 排障人：修复团（xiufu-tuan）
- 版本：`dsh-kexi-crypto` 1.9.23 → **1.9.24**
- 关联任务：共享任务板 task-1；门禁由回归团 task-2 负责

## 1. 复现步骤

1. DSH 0.2.1-alpha.5 于 2026-11-13 升级至 **0.2.2-rc.3**。
2. 启动 DSH 后，插件 `dsh-kexi-crypto`（已装 1.9.22）在 GUI 显示「加载失败」。
3. 静态复现（lead 实测，可信）：
   `node scripts/check-preset-mount.mjs`
   → 输出「✗ BROKEN kexi-crypto」+「mount(kexi-crypto) 抛错」+
   「真调用 mount() 次数应为 2，实际 1」，其余断言全绿。

## 2. 原始报错全文（lead 取证，原样保留）

```
tool-subagent-list-agents ... never started
```

（cordis loader 报该行 never started，整个 `kexi-crypto` 预设被判 BROKEN。）

## 3. 定位

- 文件：`home-plugin/dsh-kexi-crypto/cordis.patch.yml`，`delegation` 组
  （删除前约 574-575 行）：

  ```yaml
  - id: tool-subagent-list-agents
    name: '@deepseek-ai/dsh-tool-subagent-control/list-agents'
  ```

- 上游 `@deepseek-ai/dsh-tool-subagent-control`（DSH 0.2.2-rc.3）的
  `package.json` `exports` 仅剩 `.` / `./src/*` / `./package.json` / `./locale/*.json`，
  **`./list-agents` 子路径已不存在**。
- 上游主入口 `src/index.ts` 已统一注册全部四个工具：
  `send_message` / `interrupt_agent` / `wait_agent` / `list_agents`。
  故 `list_agents` 功能由补丁中上方的 `tool-subagent-control` 行覆盖，
  该子路径行纯属失效冗余。

## 4. 根因

**环境/上游适配问题（实现侧的失效冗余声明）**：插件补丁沿用了 0.2.1 时代
`@deepseek-ai/dsh-tool-subagent-control/list-agents` 子路径，DSH 0.2.2 移除该
子路径导出后，cordis loader 无法启动该行 → 整组委派预设 BROKEN → GUI「加载失败」。
不是模型行为问题，不是本插件逻辑缺陷，是**补丁与上游 exports 漂移**。

## 5. 处置（已修）

- 删除 `cordis.patch.yml` 的 `tool-subagent-list-agents` 行；
  delegation 注释块补充 v1.9.24 说明（中文，与上下文风格一致）。
- 版本 1.9.23 → 1.9.24：`package.json`（version + description 顶部 v1.9.24 段）、
  `CHANGELOG.md`（顶部新增 v1.9.24 条目）、`README.md`（版本行 + v1.9.24 提示）、
  `HANDOFF.md`（最后更新行 + v1.9.24 必读段）、`lib/index.mjs` 的
  `PLUGIN_VERSION_FALLBACK` 同步为 `1.9.24`。
- 未触碰：已安装副本（dsh-home 下任何文件）、`deps/`、`dist/`、git 历史（未 commit）。
- 修复后自检（本任务范围内）：
  - `node scripts/verify.mjs` → 见结案摘要（exit 0）
  - `node scripts/check-preset-mount.mjs` → 见结案摘要（预期 `PRESET-MOUNT: PASS`）

## 6. 残留与备注

- 全仓 `list-agents` 残留：仅剩历史文档
  `dsh-plugin-format-research.md`（L329，历史取证记录）与
  `docs/repair-plan-v1.9.23.md`（L187，历史方案原文）——按「历史记录保留原样」处理；
  二者均不被 `verify.mjs` / `check-preset-mount.mjs` 等门禁当作断言依据（门禁只读
  补丁、源码与四处版本号）。
- 安装副本仍是 1.9.22（或 1.9.23，取决于 task-3 之前的状态），**需回归团 task-2 门禁
  全绿后由 task-3 同步**；本任务不碰安装副本。
- 本文件按 `LOGS-INDEX.md` 规范（调试结案记录进 `logs/debug/`）；文件名按任务卡
  指定 `YYYYMMDD-v1924-list-agents-load-failure.md`（与目录 README 的
  `YYYYMMDD-HHMM-主题.md` 惯例略有出入，以任务卡为准）。
