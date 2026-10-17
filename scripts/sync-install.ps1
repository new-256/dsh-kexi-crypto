<#
  sync-install.ps1 — 一键同步到已安装副本并做哈希一致性校验

  为什么需要这个脚本：
    之前每次改完代码都要手写一长串 Copy-Item + Get-FileHash 比对逻辑，
    容易漏目录、漏文件、忘记清 __pycache__，或者复制成嵌套目录
    （Copy-Item -Recurse 指向已存在目录会产生 <dir>\<dir>）。
    这里把它固化成可复用的一条命令。

  用法：
    powershell -ExecutionPolicy Bypass -File scripts\sync-install.ps1
    powershell -ExecutionPolicy Bypass -File scripts\sync-install.ps1 -CheckOnly
    powershell -ExecutionPolicy Bypass -File scripts\sync-install.ps1 -Pack

  退出码：0=一致，1=有差异（会列出具体文件）
#>
param(
  [switch]$CheckOnly,   # 只校验不复制
  [switch]$Pack         # 同步后跑 npm pack --dry-run
)

$ErrorActionPreference = 'Stop'

# 源目录：本脚本位于 <repo>\scripts\，仓库根是上一级
$repo    = Split-Path -Parent $PSScriptRoot
$default = Join-Path $env:APPDATA 'DSH Desktop\dsh-home\profiles\web\node_modules\dsh-kexi-crypto'
$dst     = if ($env:KEXI_INSTALL_DIR) { $env:KEXI_INSTALL_DIR } else { $default }

# 与 npm pack 的 files 字段保持一致——漏一个目录就发版失败
$dirs = @('home-plugin', 'preset', 'locale', 'docs', 'scripts', 'track-record')
$rootFiles = @('icon.svg', 'README.md', 'INSTALL.md', 'CHANGELOG.md', 'HANDOFF.md', 'package.json')

if (-not (Test-Path $repo))      { Write-Host "找不到仓库根: $repo" -ForegroundColor Red; exit 1 }

# DSH 运行时 HMR watcher 会在写入瞬间打开 cordis.patch.yml 等文件，直接
# Copy-Item -Force 偶发/持续报 "being used by another process"。NTFS 上
# 被打开的文件仍可重命名，所以统一走「复制到同卷临时文件 → Move-Item 原子
# 替换」，并对瞬时锁做有限重试。重试用尽仍失败就抛错（绝不静默跳过，
# 否则哈希校验会与未更新的旧文件对账而误判一致）。
function Copy-KexiFile {
  param([string]$Src, [string]$DstFile, [int]$Attempts = 6)
  $dstDir = Split-Path -Parent $DstFile
  if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
  $tmp = Join-Path $dstDir (".{0}.{1}.tmp" -f (Split-Path -Leaf $DstFile), [guid]::NewGuid().ToString('N').Substring(0,8))
  $lastErr = $null
  for ($i = 1; $i -le $Attempts; $i++) {
    try {
      Copy-Item $Src $tmp -Force -ErrorAction Stop
      Move-Item $tmp $DstFile -Force -ErrorAction Stop
      return
    } catch {
      $lastErr = $_
      if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
      if ($i -lt $Attempts) { Start-Sleep -Milliseconds 350 }
    }
  }
  throw "复制失败（$Attempts 次重试）: $Src → $DstFile : $($lastErr.Exception.Message)"
}

Write-Host "源: $repo"
Write-Host "目: $dst"

if (-not $CheckOnly) {
  # 清源目录的 __pycache__（verify.mjs 也会做，但同步前先清更干净）
  Get-ChildItem $repo -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch 'node_modules' } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

  New-Item -ItemType Directory -Force -Path $dst | Out-Null

  foreach ($d in $dirs) {
    $s = Join-Path $repo $d
    if (-not (Test-Path $s)) { continue }
    $t = Join-Path $dst $d
    New-Item -ItemType Directory -Force -Path $t | Out-Null
    # 逐文件原子替换（Copy-Item -Recurse 在目标文件被 HMR watcher 占用时整批失败）
    Get-ChildItem $s -Recurse -File | Where-Object { $_.FullName -notmatch '__pycache__' } | ForEach-Object {
      $rel = $_.FullName.Substring($s.Length + 1)
      Copy-KexiFile -Src $_.FullName -DstFile (Join-Path $t $rel)
    }
  }
  foreach ($f in $rootFiles) {
    $s = Join-Path $repo $f
    if (Test-Path $s) { Copy-KexiFile -Src $s -DstFile (Join-Path $dst $f) }
  }
  Get-ChildItem $dst -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host "已同步。" -ForegroundColor Green
} else {
  Write-Host "仅校验，不复制。"
}

# ── 哈希一致性校验 ────────────────────────────────────────────────
$diff = 0; $n = 0; $missing = @(); $changed = @()
foreach ($d in $dirs) {
  $s = Join-Path $repo $d
  if (-not (Test-Path $s)) { continue }
  Get-ChildItem $s -Recurse -File | Where-Object { $_.FullName -notmatch '__pycache__' } | ForEach-Object {
    $rel = $_.FullName.Substring($repo.Length + 1)
    $t   = Join-Path $dst $rel
    $n++
    if (-not (Test-Path $t)) { $diff++; $missing += $rel }
    elseif ((Get-FileHash $_.FullName -Algorithm MD5).Hash -ne (Get-FileHash $t -Algorithm MD5).Hash) {
      $diff++; $changed += $rel
    }
  }
}
foreach ($f in $rootFiles) {
  $s = Join-Path $repo $f
  if (-not (Test-Path $s)) { continue }
  $n++
  $t = Join-Path $dst $f
  if (-not (Test-Path $t)) { $diff++; $missing += $f }
  elseif ((Get-FileHash $s -Algorithm MD5).Hash -ne (Get-FileHash $t -Algorithm MD5).Hash) {
    $diff++; $changed += $f
  }
}
# 多余文件（已装副本有、源没有）
$extra = @()
Get-ChildItem $dst -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -notmatch '__pycache__' } | ForEach-Object {
    $rel = $_.FullName.Substring($dst.Length + 1)
    if (-not (Test-Path (Join-Path $repo $rel))) { $extra += $rel }
  }

Write-Host ""
Write-Host "校验：检查 $n 个文件，差异 $diff，多余 $($extra.Count)"
if ($missing.Count) { Write-Host "缺失于安装副本:"; $missing | Select-Object -First 20 | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow } }
if ($changed.Count) { Write-Host "内容不一致:"; $changed | Select-Object -First 20 | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow } }
if ($extra.Count)   { Write-Host "已装副本多余:";   $extra   | Select-Object -First 20 | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow } }

if ($Pack -and -not $CheckOnly) {
  Write-Host ""
  Write-Host "=== npm pack --dry-run ==="
  Push-Location $repo
  # npm 把 notice 写到 **stderr**，而 Windows PowerShell 5.1 会把原生命令的
  # stderr 输出当成错误（NativeCommandError）；在 $ErrorActionPreference='Stop'
  # 下这会直接中断脚本。所以这里**局部**放宽偏好，跑完再恢复。
  $prevPref = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $packOut = & npm pack --dry-run 2>&1 | ForEach-Object { $_.ToString() }
  } finally {
    $ErrorActionPreference = $prevPref
  }
  $packOut | Select-String -Pattern 'version:|total files|package size' |
    ForEach-Object { Write-Host "  $($_.ToString().Trim())" }
  Pop-Location
}

if ($diff -gt 0 -or $extra.Count -gt 0) { exit 1 }
Write-Host "✅ 源与安装副本完全一致" -ForegroundColor Green
exit 0
