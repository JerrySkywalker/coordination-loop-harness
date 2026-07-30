# Wave7 Capability Parity Matrix

This document maps the proven coordination capabilities required by
`CLH-V020-001` to the reusable Coordination Loop Harness protocol.

The matrix does **not** claim full behavioral, operational, or infrastructure
parity with any private Wave7 environment. `Implemented` means the capability is
available in the public, synthetic, repository-local v0.2.0 harness.
`Intentionally generalized` means the proven behavior is represented without
private repository names, endpoints, credentials, or production coupling.

## Classification

| Wave7 capability | v0.1.0 baseline | v0.2.0 disposition | Public harness interpretation |
| --- | --- | --- | --- |
| Request | Partial | Implemented | Strict durable request JSON with a human-readable companion. |
| Plan | Partial | Implemented | Strict phase plan with repository and gate declarations. |
| Goal | Partial | Implemented | Strict role/phase/write-boundary contract. |
| Manifest | Partial | Implemented | Deterministic inventory plus sealed durable-file hashes. |
| Status | Partial | Implemented | Legal state transitions, history, generation checks, and privileged gates. |
| Outcome | Partial | Implemented | Strict final result and exact-head summary. |
| Decisions | Schema only | Implemented | Schema, sequence, authorization, lease-generation, and Markdown binding verification. |
| Audits | Schema only | Implemented | Record and verify exact-head/exact-main results with asserted versus verified independence. |
| Markdown/JSON hash binding | Missing | Implemented | SHA-256 companion binding with deterministic UTF-8 handling. |
| Exact repository binding | Missing | Implemented | Git root, canonical path, origin, branch, input SHA, cached origin ref, and worktree checks. |
| Bound Goal | Missing | Implemented | Local-only export of the execution contract and exact repository evidence. |
| Coordinator manifest | Partial | Implemented | Local-only machine-readable manifest emitted with a Bound Goal. |
| Attach prompt | Partial | Implemented | Reviewable local prompt generated from the bound contract. |
| No automatic Codex launch | Implemented | Implemented | Python and PowerShell paths only render files; no process launch is performed. |
| Local evidence boundary | Partial | Implemented | Raw evidence and leases remain ignored or external and are excluded from sealed bundles. |
| Owner authorization | Reference only | Implemented | Requested actions require a verified accepted/merged decision when privileged. |
| Blockers and escalation | Missing | Implemented | Stable fingerprint, recurrence, retry limit, and deterministic escalation flags. |
| Single writer | Implemented | Implemented | Exactly one active write repository is enforced per active lease. |
| Repository-set leases | Partial | Implemented | Overlap, decision authorization, and optimistic lease generation are enforced. |
| Sealed Run Bundle | Missing | Implemented | Missing, extra, changed, or unhashed durable objects fail verification. |
| Repository verification | Missing | Implemented | Offline/read-only local verification with optional fakeable `gh` live checks. |
| Status transition engine | Missing | Implemented | `PLANNED`, `ADMITTED`, `RUNNING`, `BLOCKED`, `OWNER_ACTION_REQUIRED`, `COMPLETE`, and `ABORTED`. |
| Derived repository bootstrap | Missing | Intentionally generalized | Idempotent ownership-aware rendering for any synthetic derived repository. |
| Template synchronization | Missing | Intentionally generalized | Non-mutating conflict and safe-update planning; automatic apply is excluded. |
| GitHub bootstrap workflow | Missing | Intentionally generalized | Repository-local workflow dispatch creates a bootstrap branch and Draft PR only. |
| Wave7 parity replay | Missing | Intentionally generalized | Secret-free fixture uses temporary repositories and fake GitHub tooling. |
| Production deployment/apply | Not present | Not applicable | The harness is a coordination protocol; production apply remains denied. |
| Automatic template sync apply | Not present | Deferred | v0.2.0 produces a complete plan but never silently applies it. |
| Process-independence proof | Self-declared only | Deferred | The harness records asserted and externally verified properties separately. |
| Distributed consensus locking | Not present | Deferred | Cooperative shared-filesystem leases remain the documented trust boundary. |

## Release-candidate evidence

The v0.2.0 classifications are backed by the synthetic parity replay and the
repository validation suite:

- bundle verification exercises unchanged, changed, extra, and missing objects;
- temporary Git repositories cover origin, branch, local ref, cached origin ref,
  detached worktree, dirty/untracked classification, and a fake `gh` command;
- the parity fixture covers Decision, Status, Blocker, Bound Goal, attach, Audit,
  no-launch, and secret-free behavior;
- bootstrap tests cover all four ownership classes, idempotence, active-run
  fail-closed behavior, dry-run, sync planning, and the workflow contract;
- package build and a clean Python 3.12 wheel installation pass.

The `Deferred` and `Not applicable` rows remain explicit limitations. This
release candidate does not claim full behavioral, infrastructure, or process
isolation parity with a private Wave7 deployment.
