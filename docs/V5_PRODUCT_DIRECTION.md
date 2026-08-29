# CLH v5 Product Direction

Status: **candidate current-facing product direction pending independent Program conformance audit**.

## Role

Coordination Loop Harness (CLH) is the provider-neutral durable coordination kernel of the four-product Coordination Loop family.

CLH owns portable representations and validation semantics for durable coordination concerns such as Goal, Decision, Run, Lease, authority, budget, repository/resource identity, bundles, status/outcome, and related exact-head handoff/admission primitives.

CLH answers: **what work is authorized, under what durable constraints, and how can that state be verified and handed off safely?**

CLH does not own the authoritative development DAG (CLE), execution/provider/session lifecycle (CLF), or starter/distribution product (CLT).

## V5 invariants

- Product topology is exactly `CLH + CLE + CLF + CLT`.
- CLH is provider-neutral: Codex, DeepSeek Harness, OpenCode, Claude Code, Hermes, and future runtimes are external.
- JDG/DGF are not CLH product dependencies or Coordination Loop product members.
- SkyBridge is a frozen historical precursor and must not be accessed, supported, migrated, wrapped, tested, or used as a donor in v5.
- CLH must not start coding agents or own provider process/session state.
- Cross-product behavior uses versioned serialized contracts/artifacts, never another repository's source tree.
- Remote repository creation is not implied by coordination/source authority.
- Immutable historical objects are not rewritten solely to satisfy future schemas.

## Current strengths retained

V5 preserves the useful CLH foundation already present at current main, including strict durable schemas, bundle sealing/verification, repository verification, lease generation/decision mechanics, Harness Model/Profile Pack formats, and safe coordination tooling.

Provider-specific positioning or historical usage examples do not define the v5 architecture.

## Primary v5 work

1. Keep generic coordination contracts/versioning stable and focused.
2. Evolve repository/resource lease semantics toward shared-read/exclusive-write behavior that can support at most one writer per product repository without imposing an unnecessary global writer lock.
3. Preserve fail-closed exact repository/head/worktree validation for writer admission.
4. Define stale/reconciliation semantics for resource ownership without automatic authority widening.
5. Publish producer-owned schema/canonicalization/conformance artifacts for cross-product CLH contracts that require consumers.
6. Reduce active scaffold/distribution ownership in CLH as CLT becomes the unambiguous bootstrap/distribution product.
7. Remove current-facing ChatGPT-Web/Codex-only product positioning while retaining historical documentation where needed for provenance.

## CLH / CLT boundary

CLH owns coordination contracts and validation. CLT owns the starter/distribution experience.

Existing bootstrap/template functionality in CLH is migration debt, not a reason to collapse CLH and CLT or make CLT secondary. The transition must be versioned and tested, but v5 is free to remove accidental historical interfaces when no current product need justifies compatibility.

## Multi-agent development rule

CLH itself must eventually express/resource-check the semantics needed by the Program operating model, but current v5 development begins conservatively: one product writer total until the relevant producer/resource gates are proven, then at most one writer per product repository.

CLH must never treat spawning an agent as granting that agent additional authority. Child scope always remains a subset of the parent durable envelope.
