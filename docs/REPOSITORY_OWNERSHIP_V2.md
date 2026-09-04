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
Infrastructure identities may contain `/`, but must be non-empty and have no
surrounding whitespace; their conflict identity is case-normalized.
Every v2 repository path, worktree path, and local scope uses a portable,
absolute grammar so its identity cannot change with a process working
directory. The serialized grammar admits a drive-qualified Windows path or a
single-root POSIX path. It rejects control characters, empty, dot, parent,
trailing-dot/space, and Windows reserved device components as well as UNC and device namespaces,
double-leading separators, and backslashes in POSIX paths because those
spellings cannot be given one bounded cross-host identity. A mutating operation
additionally requires every declared path to use the current host's native
dialect.
Read-only observation keeps the portable grammar so another host can still
report ownership; that classification is not mutation admission.
Serialized repository fields use suffix-free `owner/name` syntax and may
preserve display case. Conflict and decision-scope identities are casefolded;
`active_writer_repository` must nevertheless exactly match the serialized
spelling of the sole WRITE repository so terminal reconstruction is stable.

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
  (UTF-8, duplicate object keys, every floating-point value, and integers
  outside the interoperable range `[-(2^53-1), 2^53-1]` rejected; sorted object
  keys, non-ASCII characters emitted directly, no insignificant whitespace,
  and one trailing LF), so access mode, owner, expiry, paths, branch, and exact
  SHA cannot change after authorization;
- `canonical_path` and `worktree_root` are exact Git roots with the declared
  origin identity, belong to the same canonical Git common directory, and
  retain the same filesystem identities throughout verification;
- the writer is on the exact `branch_ref` and `exact_sha`;
- the writer worktree has no tracked or untracked changes;
- no exact-worktree Git index, HEAD, common configuration, worktree
  configuration, worktree administrative, packed-ref, or active-branch lock
  already exists;
- no active lease has a conflicting repository, path, branch, local, or
  infrastructure resource.

Every v2 acquire, replacement, and release mutation requires an explicitly
supplied repository root. Mutation never derives decision or outcome authority
from the process working directory. V1 retains its historical root discovery.

The generic `coord.decision.v2` schema remains structurally compatible with
historical v1 lease decisions that predate candidate digests. Schema validity
alone is therefore not v2 lease authority: every v2 acquire, replacement, and
release consumer must require a non-null digest and compare it with the exact
canonical candidate. Missing, null, or mismatched digests fail closed.

Metadata is not treated as proof of the live worktree. Inside the common
admission mutex, CLH exclusively creates the Git lock files for the index,
HEAD, common `config`, per-worktree `config.worktree`, the worktree
administrative `locked` marker, packed refs, and active branch. Each lock has a
unique marker and captured filesystem identity. CLH then re-verifies the exact
common-directory and worktree identities, scans overlaps, rechecks the complete
lock set and marker contents, and publishes the lease before releasing those
guards. Every derived lock path is contained within the exact worktree or
common Git metadata root. A mismatch fails before the lease file is published.
An interrupted or replaced guard intentionally remains fail-closed for manual
inspection; it is never reclaimed from PID or elapsed time. Lease files and
admission mutexes remain local state outside Git.

The transient admission mutex directory is intentionally empty. Normal release
removes only that empty directory; it never enumerates or unlinks children. A
replaced identity or unexpected child leaves the mutex fail-closed for manual
inspection.

Lease JSON is first flushed in a same-directory temporary file. POSIX
publication uses an atomic no-replace hard link followed by directory `fsync`;
Windows uses `MoveFileExW` with `MOVEFILE_WRITE_THROUGH`, omitting replacement
authority for create-new. Replacement uses one native atomic rename after the
complete temporary file is flushed, so readers see an old or complete-new
record. A post-publication durability error reports failure even when the target
may already contain the complete new record, so recovery inspects the exact
target instead of inferring rollback.

Git-native guards cover cooperating Git operations. Direct filesystem changes
by a process that ignores both Git and the shared CL lease protocol are outside
this cooperative-lock boundary. The bracketed snapshot still detects changes
that occur during normal verification.

Decision scope entries must be non-empty, unique canonical strings using the
same identities as overlap evaluation: suffix-free lowercase `owner/name` for
repositories, an absolute canonical path for path resources,
`owner/name:refs/heads/branch` for branches, and the case-normalized resource
string for infrastructure. The coordination repository is also named as
`owner/name`. The decision scope may be a strict superset of the candidate's
resource set, but extra envelope entries neither widen nor reserve resources in
the candidate lease. Blank, duplicate, alias, or noncanonical entries fail
closed.

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
Repository-relative decision and outcome references reject absolute paths,
control characters, dot-segment traversal, trailing-dot components, and Windows
reserved device components so the same spelling cannot resolve to another file
on Windows.
Because `coord.decision.v2` remains structurally compatible with historical v1
decisions, v2 lease consumers apply those rules to every
`previous_decision_ref` in the verified predecessor chain.

Acquire, replacement, and release revalidate their complete decision evidence
immediately before atomic publication. A decision JSON, Markdown companion, or
predecessor changed during guarded admission therefore fails before the lease
record is created or replaced.

On Windows, a reader handle that does not share delete access makes the native
replacement fail while preserving the complete old record. Callers reconcile
the exact target and retry after the reader releases it; no partial target is
treated as success.

Only a record whose schema, lifecycle, release lineage, candidate digest,
outcome content, declared lease id, and canonical filename all verify is
classified and skipped as `TERMINAL_RELEASED`. V2 lineage and outcome proof
also require an explicit repository root; observation never falls back to the
process working directory.
Every other terminal-looking record is `UNKNOWN_FAIL_CLOSED` and remains
potentially active for overlap checks. It blocks only resources that can be
parsed and positively overlap the candidate. An opaque invalid JSON record has
no provable resource set and never becomes a global lock, but its canonical
filename still reserves that exact `lease_id`. A parseable mismatched record
also retains its declared identity and resources conservatively. It remains
available for explicit operator inspection. Missing, null, malformed, or
future schema versions are never silently treated as v1 during replacement or
release.

## Compatibility

- `coord.repo-set-lease.v1` keeps its conservative all-overlap behavior.
- Historical v1 coordination-repository self-write replacement remains
  compatible, including its original decision sequencing and repository-name
  normalization. It now receives the same exact live repository/common-dir,
  clean-worktree, filesystem-identity, and Git-guard checks; `acquire` still
  cannot introduce a coordination self-write.
- New v2 repository fields use suffix-free `owner/name` syntax with
  case-preserving serialization and casefolded canonical identity.
  Conservative overlap scanning recognizes legacy and pre-acceptance aliases
  without changing the stored meaning of a valid v1 lease.
- New per-repository writer claims use v2 and its exact compatibility artifact.
- Pre-acceptance v2 custody drafts missing the Goal 07 terminal fields, using
  now-invalid path or repository aliases, or carrying an unknown schema are not
  auto-upgraded. They remain fail-closed until an explicit reviewed migration
  supplies a fresh candidate and valid decision chain.
- The compatibility artifact includes exact overlap, canonical candidate
  digest, schema lifecycle, and terminal release vectors. Consumers pin the
  serialized artifact and manifest digest rather than importing CLH source.
- CLH owns validation and ownership semantics; CLT may pin and validate the
  serialized contract but does not copy CLH runtime implementation.
- DGF RepoHealth Writer Lease is not part of this execution path.
