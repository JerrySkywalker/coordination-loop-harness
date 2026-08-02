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

## v0.2 integrity layers

1. Strict schemas reject undeclared durable-object properties.
2. Companion Markdown and JSON are bound by SHA-256.
3. A sealed Run Bundle inventories every durable run object and fails on drift.
4. Bound Goal export combines the durable goal with read-only Git evidence and optional lease data.
5. Privileged transitions consume verified owner decisions, not reference strings.
6. Local leases and raw evidence remain outside the sealed durable plane.

Template bootstrap is a separate derived-repository plane. Ownership classes determine whether a
file is template-managed, rendered once, derived-owned, or template-source-only. Synchronization
stops at a non-mutating plan.
