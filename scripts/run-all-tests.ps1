<#
  run-all-tests.ps1 — 一键跑完整回归套件

  新会话上手第一件事就跑这个。任何改动后都必须全绿再同步。

  用法：
    powershell -ExecutionPolicy Bypass -File scripts\run-all-tests.ps1
    powershell -ExecutionPolicy Bypass -File scripts\run-all-tests.ps1 -Quick   # 跳过耗时长的
#>
param([switch]$Quick)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$py   = if ($env:KEXI_PYTHON) { $env:KEXI_PYTHON } else { 'C:\Python313\python.exe' }

if (-not (Test-Path $py)) {
  Write-Host "找不到 Python: $py（可用 `$env:KEXI_PYTHON 指定）" -ForegroundColor Red
  exit 1
}

$results = @()
function Run-Step($name, $cmd, $script) {
  Write-Host ""
  Write-Host "=== $name ===" -ForegroundColor Cyan
  $out = & $cmd 2>&1 | ForEach-Object { $_.ToString() }
  $code = $LASTEXITCODE
  $tail = ($out | Select-Object -Last 3) -join ' | '
  $ok = ($code -eq 0)
  $script:results += [pscustomobject]@{ Name = $name; Ok = $ok; Code = $code; Tail = $tail }
  if (-not $ok) { $out | Select-Object -Last 25 | ForEach-Object { Write-Host "  $_" -ForegroundColor Red } }
  Write-Host ("  {0}  {1}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $tail)
}

Write-Host "仓库: $repo"
Write-Host "Python: $py  (-Quick: $($Quick.IsPresent))"

# ── 1. 静态接线检查 + node --check ──
Run-Step 'verify.mjs (静态接线 + 语法)' { node (Join-Path $repo 'scripts\verify.mjs') } $PSScript

# ── 2. 行为回归（150 项）──
Run-Step 'test-runtime.mjs (行为回归)' { node (Join-Path $repo 'scripts\test-runtime.mjs') } $PSScript

# ── 3. 本轮新增的针对性回归 ──
Run-Step 'test-card-props.mjs (卡片传值)' { node (Join-Path $repo 'scripts\test-card-props.mjs') } $PSScript
Run-Step 'test-card-kexi-filter.py (卡片只显 kexi 相关)' { & $py (Join-Path $repo 'scripts\test-card-kexi-filter.py') } $PSScript
Run-Step 'test-team-graph.py (团队关系图)' { & $py (Join-Path $repo 'scripts\test-team-graph.py') } $PSScript
Run-Step 'test-graph-render-rules.py (图渲染规则)' { & $py (Join-Path $repo 'scripts\test-graph-render-rules.py') } $PSScript
Run-Step 'test-session-findings.py (实跑取证回归)' { & $py (Join-Path $repo 'scripts\test-session-findings.py') } $PSScript
Run-Step 'test-regime.py (市场状态判定)' { & $py (Join-Path $repo 'scripts\test-regime.py') } $PSScript
Run-Step 'test-regime-log.py (regime 对账台账)' { & $py (Join-Path $repo 'scripts\test-regime-log.py') } $PSScript
Run-Step 'test-cex-credential-schema.py (CEX 凭据字段一致性)' { & $py (Join-Path $repo 'scripts\test-cex-credential-schema.py') } $PSScript
Run-Step 'test-position-doctor.py (仓位体检)' { & $py (Join-Path $repo 'scripts\test-position-doctor.py') } $PSScript
Run-Step 'test-dpapi-roundtrip.mjs (DPAPI 凭据往返实测)' { node (Join-Path $repo 'scripts\test-dpapi-roundtrip.mjs') } $PSScript
Run-Step 'test-settings-wiring.py (设置连通性)' { & $py (Join-Path $repo 'scripts\test-settings-wiring.py') } $PSScript
Run-Step 'test-scripts-smoke.py (全脚本冒烟)' { & $py (Join-Path $repo 'scripts\test-scripts-smoke.py') } $PSScript
Run-Step 'test-frontend-backend-parity.mjs (前后端字段匹配)' { node (Join-Path $repo 'scripts\test-frontend-backend-parity.mjs') } $PSScript
Run-Step 'test-cex-tool-serialization.mjs (工具返回值序列化)' { node (Join-Path $repo 'scripts\test-cex-tool-serialization.mjs') } $PSScript

Run-Step '成果登记运行期探针' { node (Join-Path $repo 'scripts\_artifact-runtime-probe.mjs') } $PSScript
Run-Step 'test-runtime-bugs.mjs (现场取证 bug)' { node (Join-Path $repo 'scripts\test-runtime-bugs.mjs') } $PSScript
Run-Step 'test-hooks-order.mjs (React Hooks 顺序)' { node (Join-Path $repo 'scripts\test-hooks-order.mjs') } $PSScript
Run-Step 'test-veto-disagreement.py (硬风控否决/分歧度)' { & $py (Join-Path $repo 'scripts\test-veto-disagreement.py') } $PSScript
Run-Step 'test-stop-plan.py (可执行止损)' { & $py (Join-Path $repo 'scripts\test-stop-plan.py') } $PSScript
Run-Step 'test-futures-fallback.py (合约情绪 OKX 兜底)' { & $py (Join-Path $repo 'scripts\test-futures-fallback.py') } $PSScript
Run-Step 'test-fast-confidence.py (快答置信度 + 三情景区间)' { & $py (Join-Path $repo 'scripts\test-fast-confidence.py') } $PSScript
Run-Step 'test-prediction-log.py (决策留痕与事后对账)' { & $py (Join-Path $repo 'scripts\test-prediction-log.py') } $PSScript
if (Test-Path (Join-Path $repo 'scripts\test-derivs-crosssource.py')) {
  Run-Step 'test-derivs-crosssource.py (跨所跨源矛盾检测)' { & $py (Join-Path $repo 'scripts\test-derivs-crosssource.py') } $PSScript
}
if (Test-Path (Join-Path $repo 'scripts\test-unlock-fetch.py')) {
  Run-Step 'test-unlock-fetch.py (解锁日历免费源)' { & $py (Join-Path $repo 'scripts\test-unlock-fetch.py') } $PSScript
}
Run-Step 'test-cex-live-safety.py (CEX 实盘安全性质)' { & $py (Join-Path $repo 'scripts\test-cex-live-safety.py') } $PSScript

# ── 4. 端到端（较慢，-Quick 跳过）──
if (-not $Quick) {
  Run-Step 'test_batch.mjs (批量 + files 端到端)' { node (Join-Path $repo 'scripts\test_batch.mjs') } $PSScript
  Run-Step 'test_runmarker.mjs (本轮标记时序)' { node (Join-Path $repo 'scripts\test_runmarker.mjs') } $PSScript
  Run-Step 'test_cwd_fix.mjs (脚本 CWD)' { node (Join-Path $repo 'scripts\test_cwd_fix.mjs') } $PSScript
  Run-Step 'Python 自测 (test_all.py 19 组)' { & $py (Join-Path $repo 'preset\kexi-crypto\skills\crypto-market-analysis\tests\test_all.py') } $PSScript
}

# ── 汇总 ──
$pass = @($results | Where-Object { $_.Ok }).Count
$fail = @($results | Where-Object { -not $_.Ok }).Count
Write-Host ""
Write-Host "=" * 62
if ($fail -eq 0) {
  Write-Host "全部通过：$pass / $($results.Count)" -ForegroundColor Green
  Write-Host "下一步：powershell -ExecutionPolicy Bypass -File scripts\sync-install.ps1 -Pack"
  exit 0
}
Write-Host "通过 $pass / 失败 $fail" -ForegroundColor Red
$results | Where-Object { -not $_.Ok } | ForEach-Object { Write-Host "  FAIL: $($_.Name)" -ForegroundColor Red }
exit 1
