# Changelog

## Unreleased

- Added the versioned `coord.repo-set-lease.v2` shared-read/exclusive-write
  contract with exact writer admission and fail-closed stale ownership
  observation while preserving v1 semantics.
- Bound v2 decisions to canonical lease candidates; added exact duplicate-id
  refusal, Git-native admission guards, stable repository snapshots,
  crash-safe create-new publication, and independently authorized terminal
  candidates with hashed outcomes and distinct stale-recovery authority. The
  canonical JSON boundary now rejects duplicate keys, every floating-point
  value, and integers outside the interoperable safe range.
- Closed Goal 07 ownership aliases with suffix-free v2 repository identities,
  bounded portable path syntax plus native mutation admission, strict canonical
  decision scopes, exact filename/declared-id handling, explicit terminal
  repository roots, and default-deny unknown or malformed lease schemas.
- Bound repository-set lease scanning, observation, listing, replacement, release,
  and mutation to exact case-sensitive ordinary single-link directory entries;
  symlink, reparse, hardlink, identity-swap, Windows case aliases, and mixed-case
  lease suffixes fail closed while safely readable exact resource claims remain
  reserved.
- Made CLH admission reserve lease-id filenames by portable casefold across
  active and terminal records, prevented invalid v2 relative scopes from
  inheriting ambient cwd, and sealed explicit READ/READ plus bidirectional
  READ/WRITE conformance vectors. The portable overlap corpus now carries
  complete stored and candidate documents, including executable terminal
  identity/resource-release and invalid-relative-path cases, plus the exact
  repository-root file map needed to reproduce terminal validation.
- Required explicit repository roots for every v2 mutation, exact serialized
  active-writer matching, nonblank infrastructure identities, portable
  repository-relative references, v2-aware origin normalization, and executable
  old-or-complete-new replacement recovery coverage.
- Made v2 observation run the full repository-identity validator, denied
  ambiguous absolute resource and decision-lineage path spellings including
  cross-host-invalid path-component characters, and revalidated decision
  evidence immediately before atomic lease publication.
  Branch-shaped substrings in opaque infrastructure identities no longer make
  otherwise valid resources impossible to authorize.
- Bound writer admission to one Git common directory and stable filesystem
  identities while guarding common/per-worktree configuration, worktree admin,
  packed refs, HEAD, index, and the active branch through publication. Legacy
  v1 coordination self-write behavior remains compatible under the same live
  writer checks.
- Removed the active CLH derived-repository workflow and froze the legacy local
  renderer as a compatibility-only surface; CLT now owns active bootstrap and
  distribution behavior.

## 0.3.0

- Added generic Harness Model and Profile Pack validation contracts while
  preserving v0.2.1 repository and CLI compatibility.

## 0.2.1 - 2026-08-02

- Fixed derived-repository provenance validation to use canonical GitHub REST template metadata
  instead of the incomplete `workflow_dispatch` event snapshot.
- Kept exact template-commit tree binding and made API errors, missing provenance, source
  mismatches, and tree mismatches fail closed.
- Refused a repeated bootstrap dispatch while an earlier bootstrap Draft pull request remains
  open, preventing duplicate derived-state branches.
- Added regression coverage for genuine Template derivation and copied-tree non-Template
  repositories.

## 0.2.0 - 2026-07-30

- Added deterministic sealed Run Bundles with Markdown/JSON SHA-256 binding.
- Added local-only Bound Goal, coordinator manifest, and attach export.
- Added offline repository verification and optional fakeable GitHub verification.
- Added verified v2 decisions, optimistic status transitions, and deterministic blockers.
- Added exact-head/exact-main audit record and verification lifecycle.
- Added ownership-aware derived-repository bootstrap and non-mutating template sync plans.
- Added a repository-scoped workflow that opens a Draft bootstrap pull request.
- Added a synthetic Wave7-style parity replay and expanded CI to Windows, Ubuntu, and macOS.
- Documented v0.1 migration and explicit parity limitations.

## 0.1.0 - 2026-07-30

- Initial public template.
- Durable run bundle schemas and generator.
- Atomic cooperative repository-set lease admission.
- Lease replacement with owner-decision and generation gates.
- Prompt rendering without process launch.
- Bilingual English and Simplified Chinese documentation.
- Cross-platform unit tests and GitHub Actions CI.
