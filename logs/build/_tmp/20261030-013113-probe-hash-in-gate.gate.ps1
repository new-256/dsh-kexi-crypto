$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath 'C:\Users\lcl\Desktop\DSH插件开发\K析研判团'
powershell -NoProfile -Command "if (Get-Command Get-FileHash -EA SilentlyContinue) { 'IN-GATE: Get-FileHash OK' } else { 'IN-GATE: Get-FileHash MISSING' }; 'PSModulePath=' + $env:PSModulePath"
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 0 }
exit $code