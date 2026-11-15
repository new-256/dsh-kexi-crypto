# tools/preset-probe — 预设/委派取证工具（开发用，**不进包**）

这个目录**不随 npm 包分发**（`package.json` 的 `files` 不含 `tools/`，与
`tools/session-forensics` 同定位）。放这里的都是"跑一次为某次结论取证"的脚本，
不该出现在用户机器上。

## roster-wrap.mjs

v1.9.23 设计 B（内部成员预设不出现在 GUI 选择器）的**真机可行性取证**：

```powershell
node tools/preset-probe/roster-wrap.mjs
```

做的事：用真实 `dsh-app-boot.runProfile` 引导 `web` profile → 拿真实
`agentPresets` 服务 → 按**浏览器路径**（typert 端点 `agentPresets/list` →
`implementation: remoteExportList` → `ctx.extend({invocation}).get(service)` →
`Reflect.get(receiver, implementation)`，与 `dsh-api-gateway` L1211-1213 同款）
读 roster → 安装 `lib/preset-roster.mjs` 的过滤器 → 再读一次。

断言：roster 恰好少 5 个 `kexi-member-*`；非 kexi 预设与相对顺序不变；
内部 `list()`/`readDocument()`/`resolve()` 完整；`mount()` 未被拦截；
形状/重复安装/被替换等负对照全部抛错；dispose 可逆。

⚠ 注意：它会拉起**完整 profile**（会连外部网关、把 GUI token 打进 stdout）。
体检 P2-7 要求做最小 profile 化 + token 脱敏后再接进门禁；在那之前只在需要
取证时手动跑，别接进 `run-all-tests.ps1`。

## 踩过的两个坑（别重犯）

1. `ctx.get()` 返回**作用域代理**而不是原始实例：直接在原始实例上 `defineProperty`
   然后比较"实例上的方法"，永远得到 `seesWrapper=false`——证明不了任何事。
2. 生成的 typert 描述符 `invoke()` 用的是**线名**（`list`），不是
   `implementation`（`remoteExportList`）；直接调它会绕过我们要验证的那层映射。
