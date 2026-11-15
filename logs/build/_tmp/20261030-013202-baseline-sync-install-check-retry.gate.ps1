$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath 'C:\Users\lcl\Desktop\DSH插件开发\K析研判团'
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sync-install.ps1 -CheckOnly
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 0 }
exit $code