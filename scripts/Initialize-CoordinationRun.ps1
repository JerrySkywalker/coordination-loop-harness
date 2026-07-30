[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][string]$RequestedBy,
    [Parameter(Mandatory = $true)][string]$Objective,
    [Parameter(Mandatory = $true)][string[]]$Repository,
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot ".."))
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$argsList = @(
    "init-run", "--root", $Root,
    "--run-id", $RunId,
    "--title", $Title,
    "--requested-by", $RequestedBy,
    "--objective", $Objective
)
foreach ($item in $Repository) {
    $argsList += @("--repository", $item)
}
& clh @argsList
exit $LASTEXITCODE
