[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

throw @"
DISABLED_BY_COORDINATION_LOOP_V5_REMOTE_REPOSITORY_GOVERNANCE

This legacy CLH publication helper is retained only as a fail-closed path.
Coordination Loop v5 treats remote repository creation as a durable external lifecycle mutation.

Remote GitHub repository creation is DENY_BY_DEFAULT and is not authorized by source-write,
push, PR, bootstrap, or ordinary Goal authority. Subagents are never permitted to create/fork/
archive/delete/transfer remote repositories.

CLT owns future bootstrap/distribution behavior and also does not create remote repositories by
default. If a future release genuinely requires a new remote repository, use a separately
Owner-controlled lifecycle process with explicit durable authority outside this helper.
"@
