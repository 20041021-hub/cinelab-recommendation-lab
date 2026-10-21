@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PATHEXT=.COM;.EXE;.BAT;.CMD;%PATHEXT%"
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS_EXE%" goto missingps
if not exist "%~dp0student-tools.ps1" goto missinghelper
pushd "%~dp0..\.."
if errorlevel 1 goto missingroot
"%PS_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0student-tools.ps1" -Action environment
set "RESULT=%ERRORLEVEL%"
popd
goto finish
:missingps
echo [FAIL] Windows PowerShell missing. Contact your teacher.
set "RESULT=10"
goto finish
:missinghelper
echo [FAIL] student-tools.ps1 missing. Extract the complete teacher package.
set "RESULT=20"
goto finish
:missingroot
echo [FAIL] Cannot access project folder. Extract the complete teacher package.
set "RESULT=20"
:finish
echo.
echo Exit code: %RESULT%
echo Save a screenshot if FAIL is shown. Press any key to close.
pause >nul
exit /b %RESULT%
