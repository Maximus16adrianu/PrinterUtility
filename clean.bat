@echo off
setlocal
cd /d "%~dp0"

if exist ".venv" (
  rmdir /s /q ".venv"
)

for /d /r "%~dp0" %%D in (__pycache__) do (
  if exist "%%D" rmdir /s /q "%%D"
)

del /s /q "%~dp0*.pyc" "%~dp0*.pyo" 2>nul

echo Cleaned local install and Python cache files.
