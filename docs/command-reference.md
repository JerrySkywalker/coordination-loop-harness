# Command Reference

All commands emit JSON for success/failure data and return nonzero for validation
or authorization failures.

## Durable runs

- `clh init-run` creates the request, plan, goal, manifest, status, and outcome.
- `clh bundle seal` writes `runs/<run-id>/bundle-seal.json`.
- `clh bundle verify` rejects missing, extra, changed, or unhashed durable objects.
- `clh validate` validates schemas, durable JSON, publishable secret patterns,
  Markdown links, and the attach no-launch contract.

## Binding and authorization

- `clh repository verify` checks local Git identity in offline/read-only mode by
  default. Use `--no-offline` for the optional `gh` metadata check.
- `clh decision verify` requires an accepted/merged v2 decision that explicitly
  authorizes the requested action.
- `clh bind-goal` writes a local-only Bound Goal package. Its default state root is
  `.coord-local`.
- `clh render-attach` preserves the v0.1 durable attach renderer.

## Lifecycle

- `clh status transition` enforces the legal transition graph and optimistic
  generation. The timestamp is an explicit input.
- `clh blocker evaluate` normalizes blocker data and emits a stable fingerprint,
  recurrence, retry, and escalation result.
- `clh audit record` writes bound Markdown/JSON for exact-head or exact-main audits.
- `clh audit verify` verifies schema, hash binding, finding counts, and result.

## Repository-set leases

- `clh lease inspect` is non-mutating.
- `clh lease acquire` requires a verified `lease:acquire` decision.
- `clh lease replace` requires `lease:expand` authorization and the next generation.
- `clh lease release` uses an optimistic generation and outcome reference.
- `clh lease list` reads a local lock root.

## Derived repositories

- `clh bootstrap-repository` renders ownership-classified files. Use `--dry-run`
  for a machine-readable plan. Active runs fail closed unless
  `--safe-mode preserve-active` is explicit.
- `clh template sync-plan` compares template output without modifying the derived
  repository. Supply the target `--template-version` and `--template-sha`;
  automatic application is deferred.

Run `clh <command> --help` or `clh <group> <command> --help` for all arguments.
