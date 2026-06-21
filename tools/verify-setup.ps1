# Shim - forwards to config-guardian.ps1 -Quick. Removable next major.
$guardian = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'config-guardian.ps1'
& $guardian -Quick -CheckOnly
exit $LASTEXITCODE
