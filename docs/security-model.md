# Security model

## Trust assumptions

- The human owner controls owner decisions.
- Writers cooperate with the same lock root.
- Git review and branch protection remain authoritative.
- Local evidence directories may contain sensitive data and are not published.

## Defended failures

- accidental overlapping repository writers;
- accidental worktree/path overlap;
- unreviewed lease expansion;
- attach scripts that launch a process;
- common secret-shaped values in durable run artifacts;
- production apply implied by source-code permission.

## Not defended

- malicious writers ignoring the lock root;
- compromised GitHub or local credentials;
- distributed races across machines without a shared atomic filesystem;
- complete secret detection;
- unsafe commands pasted manually by an owner.
