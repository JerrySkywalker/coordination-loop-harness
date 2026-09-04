# Repository ownership and lease v2

`coord.repo-set-lease.v2` is the CLH-owned Minimum-V1 contract for bounded
per-repository writers. It replaces the global-overlap behavior of v1 for new
Coordination Loop work without changing the meaning of an existing v1 lease.

## Access model

V2 applies shared-read/exclusive-write rules to repository identities, bound
repository paths, worktree paths, and branch references:

| Existing access | Candidate access | Result |
| --- | --- | --- |
| READ | READ | admitted when every other resource is disjoint |
| READ | WRITE | conflict |
| WRITE | READ | conflict |
| WRITE | WRITE | conflict |

The `coordination_repository` is an implicit READ observation for an ordinary
v2 product lease. Multiple CLH, CLE, CLF, or CLT writers may therefore observe
the same Program repository without turning that observation into a global
writer lock. A coordination self-write replacement remains an exclusive WRITE
and conflicts with active observations of that repository.

`local_scopes` and `infrastructure_scopes` are exclusive mutable reservations.
They never become shared merely because repository observations are shared.
Overlapping parent/child paths conflict whenever either path is WRITE.

Each ACTIVE lease still permits zero or one writer. Across a common lock root,
the intended upper bound is one writer for each product repository and any
number of mutually compatible read-only leases. Producer/consumer protocol
edges are external serialization barriers and are not weakened by this access
model.

## Writer admission

Before an ACTIVE v2 writer lease is created, CLH verifies all of the following
twice around atomic admission:

- the accepted decision authorizes the exact lease id and generation and its
  scope names the active writer repository;
- `canonical_path` and `worktree_root` are exact Git roots with the declared
  origin identity;
- the writer is on the exact `branch_ref` and `exact_sha`;
- the writer worktree has no tracked or untracked changes;
- no exact-worktree Git `index.lock` exists;
- no active lease has a conflicting repository, path, branch, local, or
  infrastructure resource.

Metadata is not treated as proof of the live worktree. A mismatch fails before
the lease file is published. Lease files and admission mutexes remain local
state outside Git.

## Failure and terminal observation

V2 records `heartbeat_utc` and `expires_utc`. `lease observe` classifies a live
ACTIVE lease as `ACTIVE`, an expired but unreleased lease as `STALE_ACTIVE`, and
a released lease as `TERMINAL_RELEASED`.

Staleness is observation, not authority. A `STALE_ACTIVE` lease continues to
block overlapping writers. CLH never deletes, steals, or automatically reclaims
it. A root coordinator failure therefore fails closed until a separately
authorized recovery resolves the ownership record. Normal completion uses the
generation-checked `lease release` operation and preserves the terminal file.
A malformed terminal record is treated as potentially active for overlap
checks, but it blocks only resources that positively overlap the candidate.

## Compatibility

- `coord.repo-set-lease.v1` keeps its conservative all-overlap behavior.
- New per-repository writer claims use v2 and its exact compatibility artifact.
- CLH owns validation and ownership semantics; CLT may pin and validate the
  serialized contract but does not copy CLH runtime implementation.
- DGF RepoHealth Writer Lease is not part of this execution path.
