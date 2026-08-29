# Template repository guide

> **V5 status:** historical CLH template guidance retained for migration context. CLT is the current bootstrap/distribution product. Remote GitHub repository creation is `DENY_BY_DEFAULT` and is not an ordinary CLH/CLT/agent capability.

## Current v5 rule

- CLH does not create remote repositories.
- CLT owns future starter/bootstrap/distribution behavior.
- Source-write, push, or PR authority does not imply remote repository create/fork/archive/delete/transfer authority.
- Subagents never receive remote repository lifecycle authority.
- Tests use local temporary repositories/worktrees by default.
- `scripts/Publish-PublicTemplate.ps1` is disabled/fail-closed and is retained only so legacy callers cannot silently create remote resources.

If a future product/release genuinely requires a new remote repository, that lifecycle operation must be separately Owner-controlled with explicit durable authority outside ordinary CLH/CLT bootstrap execution.

## Historical v0.2 behavior

Earlier CLH releases used GitHub Template repositories and a repository-scoped `bootstrap-derived-repository.yml` workflow. That workflow acted only inside an already-existing repository, validated template provenance, created a dedicated branch, and opened a Draft PR. It did not create the top-level repository.

The immutable v0.2/v0.2.1 release history and provenance semantics remain historical evidence. They are not v5 remote-lifecycle authority and do not make CLH the long-term distribution owner.

`.coord-template.json` and related historical template locks continue to describe old template provenance where needed for migration/history. V5 CLH/CLT separation is governed by `docs/V5_PRODUCT_DIRECTION.md` and the Program Roadmap v5.