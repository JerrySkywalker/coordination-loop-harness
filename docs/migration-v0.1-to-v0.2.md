# Migrating from v0.1.0 to v0.2.0

v0.2.0 preserves `init-run`, `validate`, `render-attach`, and the lease command
group, but strengthens authorization.

## Status

New runs use `coord.status.v2`, `generation: 1`, and an empty `history`. Existing
v1 status files remain schema-valid but must be explicitly migrated before using
`clh status transition`.

Replace `READY` with `ADMITTED`. Add:

```json
{
  "schema_version": "coord.status.v2",
  "generation": 1,
  "history": []
}
```

Keep all other required status properties.

## Decisions and leases

Lease acquisition and expansion no longer accept a non-empty decision path as
authorization. Create a `coord.decision.v2` Markdown/JSON pair with:

- a contiguous positive sequence;
- `ACCEPTED` or `MERGED` status;
- `authorized_actions` containing `lease:acquire` or `lease:expand`;
- the exact lease id and generation;
- the SHA-256 of its Markdown companion.

New `coord.repo-set-lease.v2` operations additionally bind the complete
canonical lease candidate in `lease_candidate_sha256`. V2 release uses a
generation-advanced terminal candidate, a distinct directly chained release
decision, and a repository-relative outcome SHA-256. Existing v1 lease
semantics remain unchanged, including historical Decision sequencing,
case-sensitive `.git` suffix handling, and coordination self-write replacement.
The latter now receives exact live Git/common-dir, clean-worktree,
filesystem-identity, and Git publication-guard validation.

New v2 decisions use a non-empty, unique canonical scope covering every lease
resource. A strict scope superset is valid, but extra authorization entries do
not add resources to the lease. Repository fields use suffix-free `owner/name`
syntax and may preserve display case; conflict and decision identities are
casefolded, while the active writer must exactly match its WRITE entry's
serialized spelling. Canonical candidates reject all floating-point values and
integers outside `[-(2^53-1), 2^53-1]`.

Goal 07 tightened the previously unaccepted v2 custody draft before its first
release. Draft v2 records without `release_decision_ref`, `release_authority`,
and `outcome_sha256`, with unknown schema versions, noncanonical repository
aliases, unsafe integers/floats, or now-denied path namespaces are intentionally
invalid. V2 paths use a drive-qualified Windows or single-root POSIX grammar;
control characters, empty, dot, parent, trailing-dot/space, Windows reserved-device,
Windows-invalid component characters (`<`, `>`, `:`, `"`, `|`, `?`, and `*`),
UNC/device, double-root, and POSIX-backslash spellings are denied. Review and
migrate such local custody records explicitly; the harness does not infer
terminal authority, inherit the process working directory, silently widen an
old draft record, or fall through to v1 mutation behavior.
Every v2 acquire, replacement, and release mutation now requires an explicit
repository root. Repository-relative decision and outcome references reject
control characters, trailing-dot and Windows reserved-device components as well as dot traversal;
v2 consumers apply the same rule to every decision predecessor reference.
V2 terminal observation requires an explicit repository root to verify its
decision and outcome; it never uses the ambient working directory.

## Bundle sealing

After all durable objects are stable, run:

```bash
clh bundle seal --root . --run-id EXAMPLE-001
clh bundle verify --root . --run-id EXAMPLE-001
```

Re-sealing is an explicit operation after an intentional durable change.

## Derived repositories

Active starter, bootstrap, and distribution ownership moves to CLT for
Minimum-V1. CLH retains only its provider-neutral durable coordination kernel
and frozen compatibility interfaces.

Run `clh bootstrap-repository --dry-run` first. Render-once and derived-owned
files are preserved, active run directories are never overwritten, and template
sync remains plan-only in v0.2.0.
