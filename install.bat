@echo off
REM ============================================================
REM  Content Triage Console - installer wrapper.
REM  Keep this file ASCII-only (see start.bat for why).
REM
REM  Flow:
REM    1. find a usable Python (3.10+); if found, use it
REM    2. otherwise bootstrap one with uv (downloads a portable
REM       Python into .bootstrap\, touches nothing system-wide)
REM  The real installer is install.py, which can print Chinese
REM  correctly because Python reads its source as UTF-8.
REM ============================================================
setlocal
set "HERE=%~dp0"

REM --- 1. look for a Python that is 3.10 or newer -------------------
set "PYCMD="
for %%V in (3.12 3.11 3.13 3.10) do (
  if not defined PYCMD (
    py -%%V -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1 && set "PYCMD=py -%%V"
  )
)
if not defined PYCMD (
  py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1 && set "PYCMD=py -3"
)
if not defined PYCMD (
  python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1 && set "PYCMD=python"
)

REM --- 2. got one? use it. otherwise bootstrap ----------------------
if defined PYCMD (
  echo Using %PYCMD%
  %PYCMD% "%HERE%install.py" %*
  set "RC=%ERRORLEVEL%"
) else (
  echo.
  echo   No usable Python found ^(need 3.10 or newer^).
  echo   A portable one will be downloaded automatically - nothing
  echo   gets installed system-wide.
  echo.
  powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%bootstrap.ps1" %*
  set "RC=%ERRORLEVEL%"
  if not defined RC set "RC=1"
)

echo.
pause
exit /b %RC%
