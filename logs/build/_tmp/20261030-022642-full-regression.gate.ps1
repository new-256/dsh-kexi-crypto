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

Set-Location -LiteralPath 'C:\Users\lcl\Desktop\DSH插件开发\K析研判团'
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-all-tests.ps1
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 0 }
exit $code