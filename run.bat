@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); if ($p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }" >nul 2>&1
if not "%errorlevel%"=="0" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs; exit 0 } catch { Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('PrinterUtility needs administrator rights to access the Epson USB service interface.', 'PrinterUtility', 'OK', 'Warning') | Out-Null; exit 1 }"
  exit /b
)

if not exist ".venv\Scripts\pythonw.exe" (
  call install.bat
)
start "" ".venv\Scripts\pythonw.exe" -m printer_utility
