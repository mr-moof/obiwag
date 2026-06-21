@echo off
rem Obi Wag hook wrapper - graceful degradation (Issue #11)
rem Usage: hook_wrapper.cmd <hook_name>
rem
rem Prevents deadlock when Python hook scripts are missing or broken.
rem If the script doesn't exist or Python exits non-zero, outputs {}
rem and exits 0 so Claude Code does not block the operation.

set "HOOK_NAME=%~1"
set "SCRIPT=%~dp0%HOOK_NAME%.py"

if not exist "%SCRIPT%" (
    echo {}
    exit /b 0
)

python "%SCRIPT%" 2>nul
if errorlevel 1 (
    echo {}
    exit /b 0
)

exit /b 0
