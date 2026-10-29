@echo off
setlocal
cd /d "%~dp0"
if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" (
  "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" touchchart.py %*
) else (
  python touchchart.py %*
)
if errorlevel 1 pause
