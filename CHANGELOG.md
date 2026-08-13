# Changelog

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
