[CmdletBinding()]
param([string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")))
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

python -m unittest discover -s (Join-Path $Root "tests") -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
clh validate --root $Root
exit $LASTEXITCODE
