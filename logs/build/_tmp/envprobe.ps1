'PSModulePath=[' + $env:PSModulePath + ']'
if (Get-Command Get-FileHash -ErrorAction SilentlyContinue) { 'FILECMD=OK' } else { 'FILECMD=MISSING' }
'PSHOME=[' + $PSHOME + ']'
'EXECPOLICY=' + (Get-ExecutionPolicy).ToString()
'LANGMODE=' + $ExecutionContext.SessionState.LanguageMode.ToString()
'PATH_HAS_PSHOME=' + ($env:PATH -like '*WindowsPowerShell*')