# Workflow

1. Materialize the request and plan.
2. Audit active leases and prepare the smallest candidate repo set.
3. Merge an owner decision authorizing the phase.
4. Atomically acquire the lease.
5. Create isolated worktrees and bind exact base SHAs.
6. Render, review, and manually attach the Implementer prompt.
7. Implement one repository at a time.
8. Run exact-head validation and open a pull request.
9. Use a separate Auditor after the Implementer stops.
10. Merge, update durable status, and release or expand the lease.

An issue comment or chat message is not an execution authorization unless it is materialized into
the coordination repository according to the project's owner-gate policy.
