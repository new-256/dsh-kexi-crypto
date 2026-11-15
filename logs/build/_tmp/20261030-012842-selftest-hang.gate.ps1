$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath 'C:\Users\lcl\Desktop\DSH插件开发\K析研判团'
node -e "console.log('about to hang'); setTimeout(()=>console.log('never'),120000)"
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 0 }
exit $code