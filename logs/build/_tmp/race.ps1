if (Get-Command Get-FileHash -ErrorAction SilentlyContinue) { (Get-FileHash 'C:\Windows\win.ini' -Algorithm MD5).Hash.Substring(0,8) } else { 'MISSING' }
