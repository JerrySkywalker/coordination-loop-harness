# Command Reference

All commands emit JSON for success/failure data and return nonzero for validation
or authorization failures.

## Durable runs

- `clh init-run` creates the request, plan, goal, manifest, status, and outcome.
- `clh bundle seal` writes `runs/<run-id>/bundle-seal.json`.
- `clh bundle verify` rejects missing, extra, changed, unhashed, non-UTF-8, symlinked,
  reparse-point, or out-of-root durable objects.
- `clh validate` validates schemas, durable JSON, publishable secret patterns,
  Markdown links, and the attach no-launch contract.

## Binding and authorization

- `clh repository verify` checks local Git identity in offline/read-only mode by
  default. Use `--no-offline` for the optional `gh` check, which parses and binds
  `nameWithOwner` and the `github.com` repository URL.
- `clh decision verify` requires an accepted/merged v2 decision that explicitly
  authorizes the requested action and has a complete, contiguous, bound predecessor
  chain back to sequence 1. V2 lease consumers add
  `--require-candidate-digest`; generic verification keeps historical v1 lease
  decisions structurally compatible and is not by itself v2 lease authority.
- `clh bind-goal` writes a local-only Bound Goal package. Its default state root is
  `.coord-local`.
- `clh render-attach` preserves the v0.1 durable attach renderer.

## Lifecycle

- `clh status transition` enforces the legal transition graph and optimistic
  generation. The timestamp is an explicit input.
- `clh blocker evaluate` normalizes blocker data and emits a stable fingerprint,
  recurrence, retry, and escalation result.
- `clh audit record` writes bound Markdown/JSON for exact-head or exact-main audits.
- `clh audit verify` verifies schema, hash binding, finding counts, and result
  semantics: PASS has no findings, while FAIL and BLOCKED require evidence.

## Repository-set leases

- `clh lease inspect` is non-mutating.
- `clh lease acquire` requires a verified `lease:acquire` decision.
- `clh lease replace` requires `lease:expand` authorization and the next generation.
- `clh lease release` keeps the legacy v1 outcome-reference form. V2 requires
  `--candidate` and `--repo-root`; the exact terminal candidate advances the
  optimistic generation and carries a directly chained release decision plus
  the repository-relative outcome hash.
- `clh lease list` reads a local lock root.
- `clh lease observe` reports `ACTIVE`, `STALE_ACTIVE`, `TERMINAL_RELEASED`, or
  `UNKNOWN_FAIL_CLOSED` without reclaiming or mutating a lease. Use
  `--repo-root` to validate v2 decision and outcome lineage.

## Frozen local compatibility

The following v0.2/v0.3 local interfaces are retained only for compatibility.
They receive no new bootstrap features; active v5 starter/bootstrap/distribution
behavior belongs to CLT. CLH has no active bootstrap PR workflow.

- `clh bootstrap-repository` verifies the template checkout or GitHub template tree
  before recording provenance, then renders ownership-classified files. Use
  `--dry-run` for a machine-readable plan. Active runs fail closed unless
  `--safe-mode preserve-active` is explicit.
- `clh template sync-plan` compares template output without modifying the derived
  repository. Recorded managed-file hashes distinguish `safe-update` from
  `conflict`. Supply the target `--template-version` and `--template-sha`;
  automatic application is deferred.

Run `clh <command> --help` or `clh <group> <command> --help` for all arguments.
