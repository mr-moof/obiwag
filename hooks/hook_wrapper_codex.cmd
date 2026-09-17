@echo off
rem Obi Wag Codex hook wrapper - graceful degradation
rem Usage: hook_wrapper_codex.cmd <hook_name>
rem
rem Sets the Codex platform marker, then delegates to the normal hook script.
rem Always exits 0 so hook failures do not block Codex tool use.

set "OBI_PLATFORM=codex"

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
