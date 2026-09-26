# CLH / CLT Minimum-V1 boundary

CLH is the active provider-neutral coordination kernel. Its current product
surface is durable Request, Goal, Decision, Run, Lease, authority/resource,
bundle, status/outcome, repository verification, validation, audit, and bounded
handoff/admission behavior.

CLT is the sole active starter, bootstrap, and distribution product. CLT owns
transactional local generation and recovery, path/concurrency safety,
compatibility locks and provenance, starter configuration and AGENTS guidance,
and upgrade/migration planning. Neither product creates remote repositories by
default.

The old CLH derived-repository renderer and template assets remain only as a
frozen v0.2/v0.3 local compatibility window. They do not define v5 product
ownership and receive no new bootstrap features. The CLH remote/bootstrap PR
workflow is removed in v5; active generation moves to CLT. Historical files and
release evidence are not rewritten.

This is the Minimum-V1 E1 boundary, not complete E3 genericization. Removal of
the frozen compatibility module, if ever required, needs a separately versioned
migration after CLT adoption evidence exists.
