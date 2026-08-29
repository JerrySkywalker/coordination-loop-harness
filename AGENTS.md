# Agent instructions for Coordination Loop Harness

- CLH is the provider-neutral durable coordination kernel in the fixed four-product topology `CLH + CLE + CLF + CLT`.
- CLH owns durable coordination contracts/validation such as Goal, Decision, Run, Lease, authority, budget, repository/resource identity, bundles, and handoff/admission primitives.
- CLH does not own the authoritative DAG/scheduler (CLE), execution/provider/session lifecycle (CLF), or starter/distribution product (CLT).
- Do not add provider-specific execution, process launch, worker management, or hidden background-agent startup to CLH.
- Do not define the product around ChatGPT Web, Codex, DeepSeek Harness, OpenCode, Claude Code, Hermes, or another agent runtime.
- JDG and DGF are not product members or required CLH dependencies.
- Do not access, depend on, wrap, migrate, test against, or preserve compatibility with SkyBridge. Historical references may remain immutable history only.
- Do not create a fifth shared-protocol repository; CLH-owned protocols remain published from CLH.
- Preserve `additionalProperties: false` in durable object schemas unless a reviewed versioned migration requires otherwise.
- Keep local leases, transient state, raw evidence, credentials, logs, and secret-bearing config outside Git.
- Lease/resource expansion must remain decision/generation bound and must never widen authority automatically.
- Writer admission must verify exact repository identity/head/worktree/dirty state rather than trust a directory name.
- Tests use temporary local repositories/directories by default. Remote GitHub repository creation is not authorized by ordinary source/test work.
- Subagents never receive remote repository lifecycle authority.
- Production mutation remains disabled by default.
- Read `docs/V5_PRODUCT_DIRECTION.md` before v5 implementation work.
