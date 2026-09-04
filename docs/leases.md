# Repository-set leases

A lease protects repositories and their execution surfaces. Overlap checks include:

- repository identity (`owner/name`);
- canonical checkout and worktree paths;
- repository-qualified branch refs;
- extra local scopes;
- coordination repository identity;
- infrastructure scope strings.

Admission uses an atomic mutex directory and create-new lease file. The harness never deletes a
stale mutex automatically. Lease expansion uses a full replacement candidate, a required decision
reference, and `expected_generation`.

In v0.2 the reference is not sufficient by itself. Acquisition requires a verified
`lease:acquire` action; expansion requires `lease:expand`, the exact lease id, and the candidate
generation in an accepted or merged v2 decision.

## Coordination self-write finalization

The coordination repository remains excluded from ordinary product leases.  A
`replace` operation alone may make it the sole `WRITE` repository to record a
durable final outcome.  That narrow candidate must be `ACTIVE`, bind the exact
coordination repository path, worktree, and SHA, match
`active_writer_repository`, keep every other repository `READ`, and pass the
normal owner-decision, generation, overlap, local-scope, and infrastructure
checks. It must use v2; legacy v1 metadata cannot request this exception.
`acquire` never permits it.

This is a cooperative lock. Every writer must use the same shared lock root and respect it. It is
not a consensus system for mutually untrusted machines.

## V2 per-repository ownership

New Coordination Loop per-repository writers use `coord.repo-set-lease.v2`.
V2 preserves exclusive mutable resources while allowing READ/READ repository,
path, branch, and shared Program observations. Any access pair containing WRITE
conflicts. Writer admission verifies the exact origin, branch, HEAD, clean
worktree, full resource decision scope, and absence of `index.lock` before
publication. All serialized paths are absolute, and replacement preserves the
lease identity/version while directly chaining its accepted decision.

Expired ACTIVE ownership is reported as `STALE_ACTIVE`, remains conflicting,
and is never automatically reclaimed. See
[`REPOSITORY_OWNERSHIP_V2.md`](REPOSITORY_OWNERSHIP_V2.md) for the complete
Minimum-V1 contract. V1 leases retain the conservative behavior above.
