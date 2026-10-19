# CLH Provider-First Authority and Integration Direction

Status: **candidate product direction for audit; docs only; does not supersede the accepted v5 CLH baseline until cross-repository architecture freeze.**

Date: 2026-09-08

## 1. Product role remains unchanged

CLH remains the provider-neutral durable coordination/authority kernel. It owns durable Goal, Decision, Run, Lease, authority, budget, repository/resource identity, bundle, status/outcome, and related validation semantics.

Provider-first interaction does not make a provider authoritative. CLH remains the mandatory substrate that defines what work is allowed, on which exact resources, under which bounded authority, and with which writer ownership.

## 2. Model adaptation must stay thin

Future Codex/Claude/OpenCode/etc. integration may use a small Skill/AGENTS layer, but that guidance must not implement CLH semantics.

A provider-specific Skill may teach the model to:

- discover the current coordination program;
- read current Goal/Decision/handoff context;
- request admitted authority/writer ownership;
- pass CLH authority handles to CLE/CLF;
- release/settle ownership at terminal boundaries.

The Skill must not define the actual Lease state machine, fencing generation, authority ceiling, or repository validation rules. Those remain mechanically enforced by CLH.

## 3. CLH should become MCP-capable without becoming MCP-dependent

CLH core domain APIs and serialized contracts remain transport-independent.

A thin MCP facade is recommended for attached provider use.

Candidate read resources:

```text
coord://program/current
coord://program/goals
coord://program/decisions
coord://program/runs
coord://program/handoffs
```

Candidate high-level tools:

```text
clh.admit_goal
clh.verify_authority
clh.acquire_writer
clh.release_writer
clh.record_decision
```

Exact names are intentionally not frozen by this document. The architecture requirement is that the facade calls CLH-owned domain operations and returns explicit durable references/handles rather than relying on provider conversation state.

## 4. Authority-handle chain

A provider should not fabricate or self-assert authority.

Recommended relationship:

```text
CLH admit Goal/resources
  ↓
authority_handle=A123
  ↓
CLE plan_next(authority=A123)
  ↓
WorkOrder W456
  ↓
CLF claim(work_order=W456, authority=A123)
```

CLE and CLF independently verify the referenced CLH authority/resource envelope as required by their own admission rules. A provider cannot bypass CLH by directly calling CLF with an unverified textual assertion.

## 5. CLH and durable program memory

Under the provider-first architecture, the durable coordination sidecar initialized by CLT is the natural place for persistent program memory such as requests, Goals, Decisions, runs, handoffs, exact repository identities, and compatibility metadata.

CLH owns the semantics/validation of the durable coordination artifacts it defines. CLT owns initializing the sidecar/distribution surface. CLH must not reclaim CLT's starter/distribution responsibilities merely because it reads or validates the resulting coordination memory.

## 6. Writer safety and process death

CLH must preserve the separation between writer authority and provider process liveness.

Standing direction:

```text
PROCESS_DEATH != AUTHORITY_RELEASE
TTL_EXPIRY != SAFE_WRITER_RELEASE
```

A Lease/writer record should carry explicit state and generation/fencing semantics so that:

- a persisted terminal lease is not treated as an active lock;
- an unexpectedly dead provider does not silently free uncertain authority;
- a stale provider cannot regain authority after a successor generation is admitted;
- timeout/TTL produces stale/reconciliation-required state rather than automatic replacement authority.

Detailed provider/process reconciliation belongs to CLF, but CLH must expose enough writer/Lease state for CLF/CLE/operator admission to fail closed.

## 7. Operator/TUI relationship

A future CLE-owned operator TUI may display CLH authority/writer state and invoke CLH-backed durable operator decisions through stable APIs. The TUI must not directly edit CLH files/databases or reinterpret Lease state.

Useful read projection includes:

- current Goal/Decision references;
- admitted authority scope;
- exact repository/resource identity;
- Lease/writer state;
- generation/fencing identity;
- terminal/stale/reconciliation-required classification.

## 8. Existing Goal-07 work remains relevant

The current Goal-07 repo-set/per-repository ownership work remains directly aligned with this provider-first direction. It should be audited for compatibility with the future attached/managed provider and TUI semantics, not discarded merely because the external integration model is being refined.

## 9. Non-goals

This direction does not make CLH:

- an AI frontend;
- a provider process manager;
- the authoritative DAG scheduler;
- a CLF replacement;
- an MCP-only product;
- a Skill runtime;
- a project-source template generator.

CLH stays the stable durable authority/coordination kernel behind replaceable integration surfaces.