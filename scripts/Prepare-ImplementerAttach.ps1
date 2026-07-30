[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [string]$Lease,
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot ".."))
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$argsList = @("render-attach", "--root", $Root, "--run-id", $RunId)
if ($Lease) { $argsList += @("--lease", $Lease) }
& clh @argsList
Write-Host "process_started=false"
exit $LASTEXITCODE
