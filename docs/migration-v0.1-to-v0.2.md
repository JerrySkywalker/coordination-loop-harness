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
semantics remain unchanged.

Goal 07 tightened the previously unaccepted v2 custody draft before its first
release. Draft v2 records without `release_decision_ref`, `release_authority`,
and `outcome_sha256` are intentionally invalid. Review and migrate such local
custody records explicitly; the harness does not infer terminal authority or
silently widen an old draft record.

## Bundle sealing

After all durable objects are stable, run:

```bash
clh bundle seal --root . --run-id EXAMPLE-001
clh bundle verify --root . --run-id EXAMPLE-001
```

Re-sealing is an explicit operation after an intentional durable change.

## Derived repositories

Run `clh bootstrap-repository --dry-run` first. Render-once and derived-owned
files are preserved, active run directories are never overwritten, and template
sync remains plan-only in v0.2.0.
