[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Repository = "JerrySkywalker/coordination-loop-harness",
    [string]$Description = "A repository template for human-supervised ChatGPT Web and Codex CLI development loops.",
    [string]$Source = (Resolve-Path (Join-Path $PSScriptRoot ".."))
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required."
}
gh auth status --hostname github.com
if ($LASTEXITCODE -ne 0) { throw "Authenticate gh before publishing." }

Push-Location $Source
try {
    if (-not (Test-Path .git)) {
        git init -b main
        git add --all
        git commit -m "Initial coordination loop harness"
    }
    git rev-parse --verify HEAD *> $null
    if ($LASTEXITCODE -ne 0) { throw "A committed HEAD is required before publishing." }
    $dirty = @(git status --porcelain)
    if ($dirty.Count -gt 0) { throw "The repository must be clean before publishing." }
    if (git remote get-url origin 2>$null) {
        throw "An origin remote already exists. Review it manually instead of recreating the repository."
    }

    if ($PSCmdlet.ShouldProcess($Repository, "Create public repository, push main, and mark it as a template")) {
        gh repo create $Repository --public --source . --remote origin --push --description $Description
        if ($LASTEXITCODE -ne 0) { throw "GitHub repository creation failed." }
        gh api --method PATCH "repos/$Repository" `
            -F is_template=true `
            -F has_issues=true `
            -F has_wiki=false `
            -F delete_branch_on_merge=true `
            -f description="$Description" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Repository settings update failed." }
        Write-Host "repository=$Repository"
        Write-Host "visibility=public"
        Write-Host "template_repository=true"
    }
}
finally {
    Pop-Location
}
