<#
  run-all-tests.ps1 — 一键跑完整回归套件

  新会话上手第一件事就跑这个。任何改动后都必须全绿再同步。

  用法：
    powershell -ExecutionPolicy Bypass -File scripts\run-all-tests.ps1
    powershell -ExecutionPolicy Bypass -File scripts\run-all-tests.ps1 -Quick   # 跳过耗时长的

  v1.9.23：加防卡死超时（用户规则 12）。
    · -TotalTimeoutMin：总预算，超了就不再开新步骤并直接红（默认 0 = 不限）
    · 逐步骤硬超时本轮未交付（child-process 退出码传播存在假绿风险，宁缺勿假）
    · 非 -Quick 时自动联动 scripts/_hw-monitor.ps1 记录 CPU/内存/显存（用户规则 7）
  v1.9.23（回归团接入）：慢速段新增 `check-preset-mount.mjs` 真实引导挂载验收。
    · 它自带内部超时与最小 profile 隔离，外层不再套 child-process 超时（避免"谁超时"二义）
    · 返回码 0=PASS / 1=FAIL / **2=引导环境不可用 → 显式 SKIP**（不计通过数、不算失败）
    · 整轮仍须由外层执行器（logs/build/run-gate.ps1）提供强制超时兜底
#>
param(
  [switch]$Quick,
  [int]$TotalTimeoutMin = 0
)
$started = Get-Date

$ErrorActionPreference = 'Continue'
# 统一 UTF-8 输出：PS 5.1 默认按 ANSI 写管道，中文与 ✅/✗ 会被拆坏
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$repo = Split-Path -Parent $PSScriptRoot
$py   = if ($env:KEXI_PYTHON) { $env:KEXI_PYTHON } else { 'C:\Python313\python.exe' }

if (-not (Test-Path $py)) {
  Write-Host "找不到 Python: $py（可用 `$env:KEXI_PYTHON 指定）" -ForegroundColor Red
  exit 1
}

$results = @()
# 返回码语义（v1.9.23 起支持「显式 SKIP」）：
#   -SkipCode 指定的返回码表示**环境不可用**而非代码缺陷（例如 check-preset-mount 的 2 =
#   引导环境不可用）。这种情况必须打成 **SKIP** 并单独计数——既不能算 PASS（那是假绿），
#   也不能算 FAIL（会让环境缺失被误报成回归失败）。默认 -999 表示不启用。
function Run-Step($name, $cmd, [int]$SkipCode = -999) {
  Write-Host ""
  Write-Host "=== $name ===" -ForegroundColor Cyan
  # 总超时闸：超预算就不再开新步骤（否则"总超时"只是日志里的一句空话）
  if ($TotalTimeoutMin -gt 0 -and ((Get-Date) - $started).TotalMinutes -ge $TotalTimeoutMin) {
    $script:results += [pscustomobject]@{ Name = $name; Ok = $false; Code = -1; Tail = ("总超时 {0} 分钟已到，未执行" -f $TotalTimeoutMin) }
    Write-Host ("  FAIL  总超时 {0} 分钟已到，跳过剩余步骤（最后执行的是上一条）" -f $TotalTimeoutMin) -ForegroundColor Red
    return
  }
  # 直接执行（逐步骤硬超时本轮未交付：child-process 方案的退出码传播有假绿风险，
  # 宁缺勿假；总超时闸在每步之前生效，可防止"整轮无限挂起"）
  $out = & $cmd 2>&1 | ForEach-Object { $_.ToString() }
  $code = $LASTEXITCODE
  $tail = ($out | Select-Object -Last 3) -join ' | '
  if ($code -eq $SkipCode) {
    $script:results += [pscustomobject]@{ Name = $name; Ok = $null; Code = $code; Tail = $tail }
    # 显式打印跳过原因：环境不可用必须可见，绝不静默当通过
    Write-Host ("  SKIP  环境不可用（exit {0}），不计入通过数" -f $code) -ForegroundColor Yellow
    $out | Select-Object -Last 10 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkYellow }
    return
  }
  $ok = ($code -eq 0)
  $script:results += [pscustomobject]@{ Name = $name; Ok = $ok; Code = $code; Tail = $tail }
  if (-not $ok) { $out | Select-Object -Last 25 | ForEach-Object { Write-Host "  $_" -ForegroundColor Red } }
  Write-Host ("  {0}  {1}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $tail)
}

# ── 硬件监控（用户规则 7：测试期必须同步记录 CPU/GPU/内存/显存）──
$hwCsv = $null
$hwJob = $null
$hwScript = Join-Path $PSScriptRoot '_hw-monitor.ps1'
if (-not $Quick -and (Test-Path $hwScript)) {
  # ⚠ 不能写进 .kexi-test-tmp：test-runtime.mjs 启动时会 rmSync 整个目录，
  #   监控 CSV 被占着会让它 EPERM 失败（本轮实测踩到）。用 TEMP 隔离。
  $hwDir = Join-Path ([System.IO.Path]::GetTempPath()) 'kexi-hw'
  New-Item -ItemType Directory -Force -Path $hwDir | Out-Null
  $hwCsv = Join-Path $hwDir ("hw-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".csv")
  $hwMinutes = if ($TotalTimeoutMin -gt 0) { $TotalTimeoutMin + 2 } else { 30 }
  $hwJob = Start-Job -FilePath $hwScript -ArgumentList $hwCsv, $hwMinutes
  Write-Host "硬件监控已启动 → $hwCsv"
}

Write-Host "仓库: $repo"
Write-Host "Python: $py  (-Quick: $($Quick.IsPresent))"

# ── 1. 静态接线检查 + node --check ──
Run-Step 'verify.mjs (静态接线 + 语法)' { node (Join-Path $repo 'scripts\verify.mjs') }
Run-Step 'test-preset-contract.mjs (0.2.1 预设契约/真实 subagent schema)' { node (Join-Path $repo 'scripts\test-preset-contract.mjs') }
Run-Step 'test-preset-roster.mjs (内部成员预设隐藏/GUI roster)' { node (Join-Path $repo 'scripts\test-preset-roster.mjs') }

# ── 2. 行为回归（150 项）──
Run-Step 'test-runtime.mjs (行为回归)' { node (Join-Path $repo 'scripts\test-runtime.mjs') }
Run-Step 'test-v1922-fixes.mjs (v1.9.22 修复回归 104 项)' { node (Join-Path $repo 'scripts\test-v1922-fixes.mjs') }

# ── 3. 本轮新增的针对性回归 ──
Run-Step 'test-card-props.mjs (卡片传值)' { node (Join-Path $repo 'scripts\test-card-props.mjs') }
Run-Step 'test-card-kexi-filter.py (卡片只显 kexi 相关)' { & $py (Join-Path $repo 'scripts\test-card-kexi-filter.py') }
Run-Step 'test-team-graph.py (团队关系图)' { & $py (Join-Path $repo 'scripts\test-team-graph.py') }
Run-Step 'test-graph-render-rules.py (图渲染规则)' { & $py (Join-Path $repo 'scripts\test-graph-render-rules.py') }
Run-Step 'test-session-findings.py (实跑取证回归)' { & $py (Join-Path $repo 'scripts\test-session-findings.py') }
Run-Step 'test-regime.py (市场状态判定)' { & $py (Join-Path $repo 'scripts\test-regime.py') }
Run-Step 'test-regime-log.py (regime 对账台账)' { & $py (Join-Path $repo 'scripts\test-regime-log.py') }
Run-Step 'test-cex-credential-schema.py (CEX 凭据字段一致性)' { & $py (Join-Path $repo 'scripts\test-cex-credential-schema.py') }
Run-Step 'test-position-doctor.py (仓位体检)' { & $py (Join-Path $repo 'scripts\test-position-doctor.py') }
Run-Step 'test-dpapi-roundtrip.mjs (DPAPI 凭据往返实测)' { node (Join-Path $repo 'scripts\test-dpapi-roundtrip.mjs') }
Run-Step 'test-settings-wiring.py (设置连通性)' { & $py (Join-Path $repo 'scripts\test-settings-wiring.py') }
Run-Step 'test-scripts-smoke.py (全脚本冒烟)' { & $py (Join-Path $repo 'scripts\test-scripts-smoke.py') }
Run-Step 'test-frontend-backend-parity.mjs (前后端字段匹配)' { node (Join-Path $repo 'scripts\test-frontend-backend-parity.mjs') }
Run-Step 'test-cex-tool-serialization.mjs (工具返回值序列化)' { node (Join-Path $repo 'scripts\test-cex-tool-serialization.mjs') }

Run-Step '成果登记运行期探针' { node (Join-Path $repo 'scripts\_artifact-runtime-probe.mjs') }
Run-Step 'test-runtime-bugs.mjs (现场取证 bug)' { node (Join-Path $repo 'scripts\test-runtime-bugs.mjs') }
Run-Step 'test-hooks-order.mjs (React Hooks 顺序)' { node (Join-Path $repo 'scripts\test-hooks-order.mjs') }
Run-Step 'test-settings-seat.cjs (设置卡多槽位落座)' { node (Join-Path $repo 'scripts\test-settings-seat.cjs') }
Run-Step 'test-veto-disagreement.py (硬风控否决/分歧度)' { & $py (Join-Path $repo 'scripts\test-veto-disagreement.py') }
Run-Step 'test-stop-plan.py (可执行止损)' { & $py (Join-Path $repo 'scripts\test-stop-plan.py') }
Run-Step 'test-futures-fallback.py (合约情绪 OKX 兜底)' { & $py (Join-Path $repo 'scripts\test-futures-fallback.py') }
Run-Step 'test-fast-confidence.py (快答置信度 + 三情景区间)' { & $py (Join-Path $repo 'scripts\test-fast-confidence.py') }
Run-Step 'test-prediction-log.py (决策留痕与事后对账)' { & $py (Join-Path $repo 'scripts\test-prediction-log.py') }
if (Test-Path (Join-Path $repo 'scripts\test-derivs-crosssource.py')) {
  Run-Step 'test-derivs-crosssource.py (跨所跨源矛盾检测)' { & $py (Join-Path $repo 'scripts\test-derivs-crosssource.py') }
}
if (Test-Path (Join-Path $repo 'scripts\test-unlock-fetch.py')) {
  Run-Step 'test-unlock-fetch.py (解锁日历免费源)' { & $py (Join-Path $repo 'scripts\test-unlock-fetch.py') }
}
Run-Step 'test-cex-live-safety.py (CEX 实盘安全性质)' { & $py (Join-Path $repo 'scripts\test-cex-live-safety.py') }

# ── 4. 端到端（较慢，-Quick 跳过）──
if (-not $Quick) {
  Run-Step 'test_batch.mjs (批量 + files 端到端)' { node (Join-Path $repo 'scripts\test_batch.mjs') }
  Run-Step 'test_runmarker.mjs (本轮标记时序)' { node (Join-Path $repo 'scripts\test_runmarker.mjs') }
  Run-Step 'test_cwd_fix.mjs (脚本 CWD)' { node (Join-Path $repo 'scripts\test_cwd_fix.mjs') }
  Run-Step 'Python 自测 (test_all.py 19 组)' { & $py (Join-Path $repo 'preset\kexi-crypto\skills\crypto-market-analysis\tests\test_all.py') }

  # 真实引导挂载验收（v1.9.23 新增，回归团接入）：
  #   为什么放慢速段：它要**真实引导 profile**（最小 profile 隔离），约 9 秒，且依赖宿主
  #   后端可用——不是秒级纯静态检查，塞进快速段会拖慢每次改动后的循环。
  #   为什么不套 child-process 超时：脚本内部已自带超时语义与最小 profile 隔离，
  #   外层再包一层会造成「到底谁超时」的二义；整轮仍由 run-gate 外层超时兜住。
  #   返回码：0=PASS（真 mount 2 次 + roster 隐藏生效 + 委派行 1 行 ACTIVE + 5 旧工具名 0 命中）
  #          1=FAIL（真缺陷）；2=引导环境不可用 → 显式 SKIP（这是环境缺失，不是回归失败）
  Run-Step 'check-preset-mount.mjs (真实引导挂载/roster 隐藏/委派行唯一)' { node (Join-Path $repo 'scripts\check-preset-mount.mjs') } -SkipCode 2
}

# ── 汇总 ──
if ($hwJob) {
  Stop-Job $hwJob -ErrorAction SilentlyContinue
  Remove-Job $hwJob -Force -ErrorAction SilentlyContinue
  if ($hwCsv) { Write-Host "硬件监控 CSV：$hwCsv" }
  $hwJob = $null
}
$pass = @($results | Where-Object { $_.Ok -eq $true }).Count
$fail = @($results | Where-Object { $_.Ok -eq $false }).Count
$skip = @($results | Where-Object { $null -eq $_.Ok }).Count
Write-Host ""
Write-Host ("=" * 62)
if ($fail -eq 0) {
  # SKIP 不是通过：单独报出来，避免"全绿"掩盖环境缺失（规则 1：禁降级兜底）
  if ($skip -gt 0) {
    Write-Host "全部通过：$pass / $($results.Count)（另有 $skip 项 SKIP：环境不可用，未计入通过数）" -ForegroundColor Yellow
    $results | Where-Object { $null -eq $_.Ok } | ForEach-Object { Write-Host "  SKIP: $($_.Name)" -ForegroundColor Yellow }
  } else {
    Write-Host "全部通过：$pass / $($results.Count)" -ForegroundColor Green
  }
  Write-Host "下一步：powershell -ExecutionPolicy Bypass -File scripts\sync-install.ps1 -Pack"
  exit 0
}
Write-Host "通过 $pass / 失败 $fail（另有 $skip 项 SKIP）" -ForegroundColor Red
$results | Where-Object { $_.Ok -eq $false } | ForEach-Object { Write-Host "  FAIL: $($_.Name)" -ForegroundColor Red }
exit 1
