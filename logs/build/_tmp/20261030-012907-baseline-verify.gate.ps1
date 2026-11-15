$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath 'C:\Users\lcl\Desktop\DSH插件开发\K析研判团\dist\baseline-07cd884'
node scripts/verify.mjs
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 0 }
exit $code