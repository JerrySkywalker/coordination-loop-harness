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

Create-new publication first flushes a same-directory temporary JSON file, then uses the platform's
atomic no-replace primitive. A failed publication never falls back to writing the final path
directly. If directory durability fails after publication, callers must inspect the exact target;
the operation reports failure rather than guessing rollback.
On Windows, both create-new and replacement publication use the native
`MOVEFILE_WRITE_THROUGH` flag; create-new omits replacement authority.

In v0.2 the reference is not sufficient by itself. Acquisition requires a verified
`lease:acquire` action; expansion requires `lease:expand`, the exact lease id, and the candidate
generation in an accepted or merged v2 decision. V2 also requires the decision to bind the complete
canonical candidate SHA-256 and use a sequence equal to that generation.
The generic decision schema deliberately remains compatible with historical v1
lease decisions, so schema validity alone is not v2 lease authority. A v2
operation always performs the cross-document digest comparison.
Canonical v2 JSON rejects duplicate object keys and non-finite numbers. It is
encoded as UTF-8 with sorted object keys, non-ASCII characters emitted
directly, compact separators, and exactly one trailing LF.

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
worktree, and full resource decision scope. Final admission holds the Git index,
HEAD, config, and active-branch locks across a stable snapshot recheck, overlap
scan, and atomic lease publish. All serialized paths are absolute, and
replacement preserves the lease identity/version while directly chaining its
accepted decision. Duplicate lease ids conflict independently of resource
identity.

Expired ACTIVE ownership is reported as `STALE_ACTIVE`, remains conflicting,
and is never automatically reclaimed. A v2 terminal candidate must be bound by
a directly chained release decision and hashed outcome. Ordinary completion
uses `lease:release`; an already expired lease requires the distinct
`lease:release-stale` authority. Invalid terminal evidence is
`UNKNOWN_FAIL_CLOSED` and remains blocking for parsed overlapping resources. An
opaque record also preserves the exact lease id encoded by its canonical
filename, without becoming a machine-wide lock. See
[`REPOSITORY_OWNERSHIP_V2.md`](REPOSITORY_OWNERSHIP_V2.md) for the complete
Minimum-V1 contract. V1 leases retain the conservative behavior above.
