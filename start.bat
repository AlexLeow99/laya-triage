@echo off
REM ============================================================
REM  Content Triage Console - launcher
REM  Keep this file ASCII-only. cmd.exe reads .bat files in the
REM  OEM codepage, so non-ASCII text here gets mangled and is
REM  executed as commands. Chinese docs live in the .md files.
REM ============================================================
setlocal
set "HERE=%~dp0"
set "PY=%HERE%.venv\Scripts\python.exe"

REM transformers probes for TensorFlow at import; if TF is installed
REM its abseil runtime can deadlock model construction on Windows.
set USE_TF=0

if not exist "%PY%" (
  echo.
  echo   [X] Not installed yet.
  echo.
  echo       Please double-click  install.bat  first.
  echo.
  pause
  exit /b 1
)

REM The server opens the browser by itself once the port is ready.
REM Set LAYA_NO_BROWSER=1 to suppress that.
"%PY%" "%HERE%app\server.py"

REM Only pause if the server exited with an error, so a normal
REM Ctrl+C close does not leave a stray window behind.
if errorlevel 1 pause
