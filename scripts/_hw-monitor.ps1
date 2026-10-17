# scripts/_hw-monitor.ps1 — 测试期独立硬件监控（规则7）
# 用法: powershell -File _hw-monitor.ps1 -Out <csv路径> [-Minutes 20]
param(
  [Parameter(Mandatory = $true)][string]$Out,
  [int]$Minutes = 20
)
$ErrorActionPreference = 'SilentlyContinue'
"ts,cpu_pct,ram_mb,vram_mb" | Out-File -FilePath $Out -Encoding utf8
$end = (Get-Date).AddMinutes($Minutes)
while ((Get-Date) -lt $end) {
  $cpu = -1
  try { $cpu = (Get-Counter '\Processor(_Total)\% Processor Time').CounterSamples.CookedValue } catch {}
  $os = Get-CimInstance Win32_OperatingSystem
  $ram = [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / 1024)
  $vram = 0
  try {
    $x = (& nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    if ($x) { $vram = [int]($x.Trim()) }
  } catch {}
  $line = "{0},{1},{2},{3}" -f (Get-Date -Format 'HH:mm:ss'), [math]::Round($cpu), $ram, $vram
  Out-File -FilePath $Out -Append -Encoding utf8 -InputObject $line
  Start-Sleep -Seconds 5
}
