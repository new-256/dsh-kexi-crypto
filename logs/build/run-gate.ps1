<#
  logs/build/run-gate.ps1 — 可复现门禁执行器（回归团 · v1.9.23 出厂门禁）

  为什么需要它：
    仓库里的测试脚本自己不带超时：node/python 一旦卡在等网络或等事件循环，整条门禁
    就会无限期挂起（规则 12 明令禁止）。同时规则 7 要求测试期同步记录硬件占用。
    这两件事如果每次靠人手写，迟早会漏；所以固化成一条命令。

  能力：
    1) 给任意命令强制超时：超时即强杀整棵进程树（不只是父进程），
       并打印「卡死位置（残留子进程）+ 最后日志」；
    2) 同步启动 scripts\_hw-monitor.ps1 记录 CPU/RAM/VRAM 到 CSV（规则 7）；
    3) stdout / stderr 全文落 logs/build/，每次执行追加一条 JSON 索引记录，
       报告可直接引用这些路径作为证据。

  用法：
    pwsh -NoProfile -File logs\build\run-gate.ps1 -Name verify -Command "node scripts/verify.mjs" -TimeoutSec 900
    pwsh -NoProfile -File logs\build\run-gate.ps1 -Name runtime -Command "node scripts/test-runtime.mjs" -TimeoutSec 1800 -WorkDir dist\baseline-07cd884

  退出码：0 = 命令成功；124 = 超时被强杀；其它 = 命令自身退出码（原样透传，绝不吞掉）。
#>
param(
  [Parameter(Mandatory = $true)][string]$Name,      # 门禁项短名，用于日志文件名
  [Parameter(Mandatory = $true)][string]$Command,   # 要执行的命令行（在 WorkDir 下执行）
  [int]$TimeoutSec = 1800,                          # 强制超时，默认 30 分钟
  [string]$WorkDir = '',                            # 默认仓库根
  [string]$LogDir = '',                             # 默认本脚本所在目录 logs\build
  [string]$RunId = '',                              # 可选，写进索引便于报告对账
  [string]$Freeze = '',                             # 可选，冻结号
  [switch]$NoHwMonitor                              # 仅调试本脚本时用；正式门禁必须记录硬件
)

$ErrorActionPreference = 'Stop'

# ── 路径解析：本脚本固定放在 <repo>\logs\build\，仓库根是上两级 ──────────────
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $repo 'package.json'))) {
  throw "仓库根识别失败: $repo（期望包含 package.json）"
}
if (-not $LogDir) { $LogDir = $PSScriptRoot }
if (-not $WorkDir) { $WorkDir = $repo }

# WorkDir 允许相对路径，最终一律解析成绝对路径——子进程的工作目录必须明确，
# 否则「脚本在哪个目录下找文件」会变成不可复现的隐性变量。
if (-not [System.IO.Path]::IsPathRooted($WorkDir)) {
  $WorkDir = Join-Path $repo $WorkDir
}
if (-not (Test-Path -LiteralPath $WorkDir)) { throw "WorkDir 不存在: $WorkDir" }
$WorkDir = (Resolve-Path -LiteralPath $WorkDir).Path

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$tmpDir = Join-Path $LogDir '_tmp'
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

$stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
$base   = Join-Path $LogDir ("{0}-{1}" -f $stamp, $Name)
$outLog = "$base.out.log"
$errLog = "$base.err.log"
$hwCsv  = "$base.hw.csv"
$gatePs = Join-Path $tmpDir ("{0}-{1}.gate.ps1" -f $stamp, $Name)

# 「当前批次时间戳指针」：仓库 .gitignore 已约定 logs/**/.current-ts 不入库，
# 同批日志可以靠它互相定位（门禁 → 硬件 CSV → 报告），而不必把每批日志名硬编码。
[System.IO.File]::WriteAllText((Join-Path $LogDir '.current-ts'), $stamp, (New-Object System.Text.UTF8Encoding($false)))

# ── 生成子进程包装脚本 ──────────────────────────────────────────────────────
# 为什么要绕一层文件而不是 Start-Process -ArgumentList "node ..."：
#   命令行里带引号/重定向/管道时，-ArgumentList 的转义规则极易出错，
#   出错的表现是「参数被吃掉但仍以 0 退出」——比崩溃更危险。写进文件零转义歧义。
# 模板用单引号 here-string（完全不插值）+ 占位符替换，而不是双引号插值：
#   双引号 here-string 里每个 $ 都要反引号转义，漏一个就把本机变量值烧进子脚本。
#   本人第一次自测就踩到了（$env:PSModulePath 被外层展开，命令直接语法错误）。
$workDirEsc = $WorkDir.Replace("'", "''")
$gateTemplate = @'
$ErrorActionPreference = 'Continue'

# 环境归一（必读，别删）：本进程由 Start-Process 启动时会**原样继承上游 PowerShell 7 的
# PSModulePath**，其中 c:\program files\powershell\7\Modules\Microsoft.PowerShell.Utility
# 排在 5.1 同名模块之前；5.1 加载 Core 版模块失败，Get-FileHash 等核心 cmdlet 直接退化成
# CommandNotFound。症状极隐蔽：sync-install.ps1 的哈希对账不是报环境错，而是「算不出哈希」，
# 行为上每个文件都成了差异。这里显式归一为 5.1 的三个标准目录，并在下面硬断言。
$env:PSModulePath = @(
  (Join-Path $env:USERPROFILE 'Documents\WindowsPowerShell\Modules'),
  (Join-Path $env:ProgramFiles 'WindowsPowerShell\Modules'),
  (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\Modules')
) -join ';'

# 硬断言：核心 cmdlet 缺失立刻炸（exit 97），绝不带着残缺工具链跑出「看起来正常」的结论
foreach ($coreCmd in @('Get-FileHash', 'Get-ChildItem', 'Get-CimInstance')) {
  if (-not (Get-Command $coreCmd -ErrorAction SilentlyContinue)) {
    Write-Host "GATE-PRECONDITION-FAIL: 核心 cmdlet 不可用 -> $coreCmd"
    exit 97
  }
}

Set-Location -LiteralPath '__WORKDIR__'
__COMMAND__
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 0 }
exit $code
'@
$gateBody = $gateTemplate.Replace('__WORKDIR__', $workDirEsc).Replace('__COMMAND__', $Command)
[System.IO.File]::WriteAllText($gatePs, $gateBody, (New-Object System.Text.UTF8Encoding($true)))

# ── 同步启动硬件监控（规则 7）───────────────────────────────────────────────
$hwProc = $null
$monitorPath = Join-Path $repo 'scripts\_hw-monitor.ps1'
if (-not $NoHwMonitor) {
  if (-not (Test-Path $monitorPath)) {
    throw "硬件监控脚本缺失: $monitorPath（规则 7 要求测试期同步监控，不允许静默跳过）"
  }
  # 监控窗口比门禁超时多留 2 分钟，避免门禁刚好卡在末尾时 CSV 先断
  $minutes = [int]([math]::Ceiling($TimeoutSec / 60.0)) + 2
  $hwProc = Start-Process -FilePath 'powershell' `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $monitorPath, '-Out', $hwCsv, '-Minutes', "$minutes") `
    -PassThru -WindowStyle Hidden
  # 等 CSV 出现表头再开跑——否则「有 CSV」可能只是文件被创建、还没写入
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 250
    if ((Test-Path $hwCsv) -and ((Get-Item $hwCsv).Length -gt 0)) { break }
  }
  if (-not (Test-Path $hwCsv)) { throw "硬件监控未能启动（CSV 未生成）: $hwCsv" }
}

# ── 执行 + 强制超时 ────────────────────────────────────────────────────────
Write-Host ("===== GATE: {0} =====" -f $Name) -ForegroundColor Cyan
Write-Host ("cmd     : {0}" -f $Command)
Write-Host ("workdir : {0}" -f $WorkDir)
Write-Host ("timeout : {0}s" -f $TimeoutSec)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath 'powershell' `
  -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $gatePs) `
  -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
  -PassThru -NoNewWindow -WorkingDirectory $WorkDir

$timedOut = $false
$hungKids = @()
if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
  $timedOut = $true
  # 强杀前先抓残留子进程树——这才是「卡死位置」：光看父 powershell 毫无信息量
  try {
    $hungKids = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object { $_.ParentProcessId -eq $proc.Id } |
      Select-Object ProcessId, Name, CommandLine
  } catch {}
  & taskkill /PID $proc.Id /T /F 2>&1 | Out-Null
  if (-not $proc.WaitForExit(15000)) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  }
}
$sw.Stop()
$elapsed = [math]::Round($sw.Elapsed.TotalSeconds, 1)

# ── 收工：停掉硬件监控 ─────────────────────────────────────────────────────
if ($hwProc -and -not $hwProc.HasExited) {
  Stop-Process -Id $hwProc.Id -Force -ErrorAction SilentlyContinue
}

$code = if ($timedOut) { 124 } else { $proc.ExitCode }
$verdict = if ($timedOut) { 'TIMEOUT-KILLED' } elseif ($code -eq 0) { 'PASS' } else { 'FAIL' }

function Get-Tail([string]$Path, [int]$Count) {
  if (Test-Path -LiteralPath $Path) {
    return @(Get-Content -LiteralPath $Path -Tail $Count -ErrorAction SilentlyContinue)
  }
  return @()
}

Write-Host ""
Write-Host ("elapsed : {0}s    exit: {1}    verdict: {2}" -f $elapsed, $code, $verdict) `
  -ForegroundColor $(if ($code -eq 0) { 'Green' } else { 'Red' })
Write-Host ("stdout  : {0}" -f $outLog)
Write-Host ("stderr  : {0}" -f $errLog)
Write-Host ("hw csv  : {0}" -f $hwCsv)

if ($timedOut) {
  Write-Host ""
  Write-Host ("!! 卡死被强杀：超过 {0}s 未返回，已 taskkill /T /F 整棵进程树" -f $TimeoutSec) -ForegroundColor Red
  Write-Host "--- 卡死位置（强杀前残留子进程）---" -ForegroundColor Yellow
  if ($hungKids -and @($hungKids).Count -gt 0) {
    $hungKids | ForEach-Object { Write-Host ("  PID {0}  {1}  {2}" -f $_.ProcessId, $_.Name, $_.CommandLine) }
  } else {
    Write-Host "  （未抓到残留子进程：可能卡在包装 powershell 内部，或子进程已随树退出）"
  }
}

if ($timedOut -or $code -ne 0) {
  Write-Host ""
  Write-Host "--- 最后日志：stdout 末 40 行 ---" -ForegroundColor Yellow
  Get-Tail $outLog 40 | ForEach-Object { Write-Host "  $_" }
  Write-Host "--- 最后日志：stderr 末 40 行 ---" -ForegroundColor Yellow
  Get-Tail $errLog 40 | ForEach-Object { Write-Host "  $_" }
}

# ── 互联网北京时间（规则 24）─────────────────────────────────────────────────
# 门禁索引必须带中国区真实时间，而不是本机时钟——本机时钟漂移会让「何时跑的哪条门禁」
# 失去可追溯性，且执行器所在机器的时区未必需可信。主源 timeapi.io，备源苏宁对时接口。
# 两源都拿不到就如实写 source_unavailable，**绝不用本机时间顶替**（宁可留空，不许造假）。
function Get-BeijingTime {
  $out = [ordered]@{
    beijing_time = ''
    epoch        = 0
    source       = 'source_unavailable'
    discrepancy  = ''
    local_clock  = (Get-Date).ToString('o')   # 仅供漂移参考，不作为时间依据
    skew_sec     = $null
  }
  $epochFromTimeapi = $null
  $epochFromSuning = $null

  # 主源：timeapi.io（返回该时区墙钟，无偏移；按 +08:00 固定偏移换 epoch）
  try {
    $r = Invoke-RestMethod 'https://timeapi.io/api/time/current/zone?timeZone=Asia%2FShanghai' -TimeoutSec 10
    if ($r.dateTime) {
      $naive = [datetime]::Parse($r.dateTime, [System.Globalization.CultureInfo]::InvariantCulture)
      $dto = [datetimeoffset]::new([datetime]::SpecifyKind($naive, 'Unspecified'), [timespan]::FromHours(8))
      $epochFromTimeapi = $dto.ToUnixTimeSeconds()
    }
  } catch {}

  # 备源：f.m.suning.com/api/ct.do → {"currentTime": <epoch 毫秒>}
  try {
    $r2 = Invoke-RestMethod 'http://f.m.suning.com/api/ct.do' -TimeoutSec 10
    if ($r2.currentTime) {
      $e = [long]$r2.currentTime
      if ($e -gt 100000000000) { $e = [long][math]::Floor($e / 1000) }  # 毫秒 → 秒
      $epochFromSuning = $e
    }
  } catch {}

  if ($null -ne $epochFromTimeapi -and $null -ne $epochFromSuning) {
    $out.source = 'timeapi.io+f.m.suning.com'
    $out.epoch = $epochFromTimeapi
    $delta = [math]::Abs($epochFromTimeapi - $epochFromSuning)
    if ($delta -gt 5) { $out.discrepancy = "两源相差 ${delta}s（取主源 timeapi.io）" }
  } elseif ($null -ne $epochFromTimeapi) {
    $out.source = 'timeapi.io'
    $out.epoch = $epochFromTimeapi
  } elseif ($null -ne $epochFromSuning) {
    $out.source = 'f.m.suning.com'
    $out.epoch = $epochFromSuning
  }

  if ($out.epoch -gt 0) {
    $bj = ([datetimeoffset]::FromUnixTimeSeconds($out.epoch)).ToOffset([timespan]::FromHours(8))
    $out.beijing_time = $bj.ToString('yyyy-MM-ddTHH:mm:sszzz')
    $out.skew_sec = [math]::Round(([datetimeoffset](Get-Date)).ToUnixTimeSeconds() - $out.epoch)
  }
  return $out
}

$bj = Get-BeijingTime
Write-Host ("beijing : {0}  (source: {1})" -f $(if ($bj.beijing_time) { $bj.beijing_time } else { 'source_unavailable' }), $bj.source)
if ($bj.discrepancy) { Write-Host ("          注意：{0}" -f $bj.discrepancy) -ForegroundColor Yellow }
if (-not $bj.beijing_time) { Write-Host '          警告：互联网时间不可达，索引中如实标记 source_unavailable（不用本机时间顶替）' -ForegroundColor Yellow }

# ── 索引记录：报告里的每一条 PASS/FAIL 都能顺着这里回到全文日志 ──────────────
$record = [ordered]@{
  ts            = (Get-Date).ToString('o')
  beijing_time  = $bj.beijing_time
  time_source   = $bj.source
  time_epoch    = $bj.epoch
  time_disc     = $bj.discrepancy
  local_clock   = $bj.local_clock
  clock_skew_s  = $bj.skew_sec
  name          = $Name
  command       = $Command
  workdir       = $WorkDir
  timeout_sec   = $TimeoutSec
  elapsed_sec   = $elapsed
  exit_code     = $code
  timed_out     = $timedOut
  verdict       = $verdict
  stdout        = $outLog
  stderr        = $errLog
  hw_csv        = $hwCsv
  runId         = $RunId
  freeze        = $Freeze
}
$record | ConvertTo-Json -Compress | Out-File -FilePath (Join-Path $LogDir 'gate-index.jsonl') -Append -Encoding utf8

if ($timedOut) { exit 124 }
exit $code
