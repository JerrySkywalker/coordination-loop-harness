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
Every v2 repository path, worktree path, and local scope is absolute so its
identity cannot change with a process working directory. Host path
canonicalization covers volume roots, POSIX roots, dot segments, separators,
and case-insensitive Windows identity.

Each ACTIVE lease still permits zero or one writer. Across a common lock root,
the intended upper bound is one writer for each product repository and any
number of mutually compatible read-only leases. Producer/consumer protocol
edges are external serialization barriers and are not weakened by this access
model.

## Writer admission

Before an ACTIVE v2 writer lease is created, CLH verifies all of the following
twice around atomic admission:

- the accepted decision authorizes the exact lease id and generation and its
  same verified snapshot scopes every repository observation, canonical path,
  worktree path, repository-qualified branch, local scope, infrastructure
  scope, and coordination repository reserved by the lease;
- the decision binds the complete candidate using SHA-256 over canonical JSON
  (UTF-8, duplicate object keys and non-finite numbers rejected, sorted object
  keys, non-ASCII characters emitted directly, no insignificant whitespace,
  and one trailing LF), so access mode, owner, expiry, paths, branch, and exact
  SHA cannot change after authorization;
- `canonical_path` and `worktree_root` are exact Git roots with the declared
  origin identity;
- the writer is on the exact `branch_ref` and `exact_sha`;
- the writer worktree has no tracked or untracked changes;
- no exact-worktree Git index, HEAD, configuration, or active-branch lock
  already exists;
- no active lease has a conflicting repository, path, branch, local, or
  infrastructure resource.

The generic `coord.decision.v2` schema remains structurally compatible with
historical v1 lease decisions that predate candidate digests. Schema validity
alone is therefore not v2 lease authority: every v2 acquire, replacement, and
release consumer must require a non-null digest and compare it with the exact
canonical candidate. Missing, null, or mismatched digests fail closed.

Metadata is not treated as proof of the live worktree. Inside the common
admission mutex, CLH exclusively creates the normal Git lock files for the
index, HEAD, common configuration, packed refs, and active branch, re-verifies
a bracketed repository snapshot, scans overlaps, and publishes the lease before
releasing those guards. Every derived lock path is contained within the exact
worktree or common Git metadata root. A mismatch fails before the lease file is
published. An
interrupted guard intentionally remains fail-closed for manual inspection; it
is never reclaimed from PID or elapsed time. Lease files and admission mutexes
remain local state outside Git.

The transient admission mutex directory is intentionally empty. Normal release
removes only that empty directory; it never enumerates or unlinks children. A
replaced identity or unexpected child leaves the mutex fail-closed for manual
inspection.

Lease JSON is first flushed in a same-directory temporary file. POSIX
publication uses an atomic no-replace hard link followed by directory `fsync`;
Windows uses `MoveFileExW` with `MOVEFILE_WRITE_THROUGH`, omitting replacement
authority for create-new. A durability error reports failure even when the
target may already have been published, so recovery inspects the exact target
instead of inferring rollback.

Git-native guards cover cooperating Git operations. Direct filesystem changes
by a process that ignores both Git and the shared CL lease protocol are outside
this cooperative-lock boundary. The bracketed snapshot still detects changes
that occur during normal verification.

Decision scope entries use the same canonical strings as overlap identity:
`owner/name` for repositories, an absolute canonical path for path resources,
`owner/name:refs/heads/branch` for branches, and the case-normalized resource
string for infrastructure. The coordination repository is also named as
`owner/name`.

Replacement preserves the lease schema version, run id, owner, creation time,
and coordination repository. Its decision must directly reference the current
lease decision, and only the exact canonical lease file is excluded from the
replacement overlap scan. A renamed duplicate with the same `lease_id` remains
a conflict even when all of its declared resources are otherwise disjoint.

## Failure and terminal observation

V2 records `heartbeat_utc` and `expires_utc`. `lease observe` classifies a live
ACTIVE lease as `ACTIVE`, an expired but unreleased lease as `STALE_ACTIVE`, and
a released lease as `TERMINAL_RELEASED`.

Staleness is observation, not authority. A `STALE_ACTIVE` lease continues to
block overlapping writers. CLH never deletes, steals, or automatically reclaims
it. A root coordinator failure therefore fails closed until a separately
authorized recovery resolves the ownership record.

V2 completion supplies an exact terminal candidate. It preserves the active
resource identity, advances generation by one, names a repository-relative
outcome file and its SHA-256, and points to a distinct accepted release
decision. That decision must bind the entire terminal candidate, directly
follow the active decision, match the terminal generation, and authorize
`lease:release`. If the lease is already expired at mutation time, the
candidate instead declares `STALE_RECOVERY` and the decision must authorize
`lease:release-stale`. Backdating a candidate cannot downgrade the authority
required by the live clock.

Only a record whose schema, lifecycle, release lineage, candidate digest, and
outcome content all verify is classified and skipped as `TERMINAL_RELEASED`.
Every other terminal-looking record is `UNKNOWN_FAIL_CLOSED` and remains
potentially active for overlap checks. It blocks only resources that can be
parsed and positively overlap the candidate. An opaque invalid JSON record has
no provable resource set and never becomes a global lock, but its canonical
filename still reserves that exact `lease_id`. It remains available for
explicit operator inspection.

## Compatibility

- `coord.repo-set-lease.v1` keeps its conservative all-overlap behavior.
- Coordination-repository self-write is a v2-only replacement so legacy v1
  metadata cannot bypass exact live writer binding.
- New per-repository writer claims use v2 and its exact compatibility artifact.
- Pre-acceptance v2 custody drafts missing the Goal 07 terminal fields are not
  auto-upgraded. They remain fail-closed until an explicit reviewed migration
  supplies a fresh candidate and valid decision chain.
- The compatibility artifact includes exact overlap, canonical candidate
  digest, schema lifecycle, and terminal release vectors. Consumers pin the
  serialized artifact and manifest digest rather than importing CLH source.
- CLH owns validation and ownership semantics; CLT may pin and validate the
  serialized contract but does not copy CLH runtime implementation.
- DGF RepoHealth Writer Lease is not part of this execution path.
