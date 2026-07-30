# Architecture

Coordination Loop Harness separates four planes:

1. **Conversation plane** — human owner and web architect discuss intent.
2. **Durable coordination plane** — tracked requests, plans, decisions, status, outcomes, and audits.
3. **Local execution plane** — worktrees, leases, raw logs, build output, and credentials.
4. **Infrastructure plane** — deployment surfaces that always require a separate apply gate.

A coordination repository may reserve a planned repository set, but an active lease should cover
only repositories needed by the current phase. A lease can be expanded only at a stopped phase
boundary, with a durable owner decision and optimistic generation checking.

The harness does not broker model API calls. It is deliberately a repository protocol plus a small
local CLI.
