$h = Get-FileHash 'C:\Windows\win.ini' -Algorithm MD5 -ErrorAction SilentlyContinue
if ($h) { 'HASH OK: ' + $h.Hash } else { 'HASH MISSING' }
exit 0
