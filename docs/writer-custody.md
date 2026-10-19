# Writer Custody Contract (I0-A, LEAF-04)

_Source preparation only; authenticated successor authority remains HOLD_

This older proposal is integrated with the accepted main without changing published `leases.py`, its protocol binding, or the public MCP Gate. The process-local observations below cannot authenticate a Lease/Decision. `evaluate_successor_admission()` always denies authority, including for caller-supplied `RELEASED` state and structurally matching evidence. Runtime integration requires separately admitted authenticated custody and preserves the existing Gate.

## Purpose

This document records the minimum logical authority contract that makes
explicit how unresolved native writer custody prevents successor writer
admission in the Coordination Loop Harness (CLH).

It is the deliverable of train leaf **LEAF-04 (I0-A)** of
`CL-CURSOR-24H-I0-I2-TRAIN-20260918-001`, implementing requirements from
`I0-SHARED-INTERFACE-CONTRACT §2 (Rules F1)`.

---

## Trust boundary: logical ≠ physical

CLH grants **logical** writer authority only.  It does not OS-revoke a
process's existing filesystem handle to a repository.  A process may
physically write to a repository path after its CLH authority has ended.
Authority is determined solely by the CLH lease state and generation stored
in the lock root — never by OS-level access control.

```
           CLH authority boundary
           ┌───────────────────────┐
           │ lease_id              │
           │ generation (CAS)      │  ← CLH owns; fail closed on stale
           │ active_writer_repo    │
           │ state: ACTIVE/RELEASED│
           └───────────────────────┘
                     ≠
           OS write access (filesystem handles, ACLs)
           ← NOT revoked by CLH; NOT evidence of authority
```

---

## Invariants (from I0-SHARED-INTERFACE-CONTRACT §2, Rules F1)

| # | Rule |
|---|------|
| 1 | **Positive release evidence required** before successor admission. |
| 2 | `CUSTODY_UNCERTAIN` → **deny successor** (fail closed). |
| 3 | **Process death alone** does NOT release authority. |
| 4 | **TTL alone** does NOT release uncertain authority. |
| 5 | **Stale generation** cannot release or replace the current writer. |
| 6 | **Only CLH** mutates writer authority; CLF/CLE supply evidence packages. |

---

## Positive release evidence

Actual successor admission requires the accepted Lease/Decision verification and positive settlement of prior writer custody. A caller-provided state string or an in-process `ReleaseEvidence` record is not that proof. Structural checks on resource, lease, generation and writer identities only reject inconsistent observations; they confer no authority. Missing authentication remains `CUSTODY_UNCERTAIN` and admission is denied.

### Evidence that is NOT sufficient alone

- Process death (SIGKILL, crash, OOM kill)
- TTL / `expires_utc` expiry
- Clean working tree or empty lock directory
- Absence of a running process
- Any OS-observable state not recorded by CLH

---

## API surface (`writer_custody.py`)

### `CustodyStatus` (enum)

| Value | Meaning |
|-------|---------|
| `CUSTODY_HELD` | Lease ACTIVE; no positive release evidence. |
| `CUSTODY_RELEASED` | Reserved for future authenticated classification; not returned by this helper. |
| `CUSTODY_UNCERTAIN` | Evidence missing or contradictory; deny successor. |

### `ReleaseEvidence` (dataclass)

Structured evidence bound to `resource_id / lease_id / generation / writer_id`.
Validated on insert by `record_release_evidence()`.

### `record_release_evidence(evidence)`

Observation-only write path. Structural validation does not establish issuer identity or authoritative release.

### `evaluate_successor_admission(...)`

Unqualified observation helper; never a substitute for the accepted admission Gate.
Returns `AdmissionDecision(permitted, custody_status, findings)`.

### `get_custody_status(...)`

Read-only convenience wrapper returning `CustodyStatus`.

### `clear_evidence_store()`

Test-only utility.  Not called in production.

---

## Integration with `leases.py`

The current published `leases.py` and its tests are retained exactly. Their Lease/Decision validation and custody behavior remain authoritative. This helper is not consulted by those mutation paths and cannot authorize their bypass.

Future authenticated integration is unadmitted and must not be inferred from this source merge.

---

## G07 concept reconciliation (read-only)

The following concepts from `agent/v5-fasttrack-g07-d3-ownership-e1-001`
are reconciled into this module's terminology.  The G07 branch was **not
merged**; only the conceptual mapping is recorded here.

| G07 concept | This module |
|---|---|
| `release_authority: NORMAL / STALE_RECOVERY` | Encoded in `release_justification` field of `ReleaseEvidence` |
| `ownership_status: STALE_ACTIVE` | Mapped to `CUSTODY_UNCERTAIN` (no positive evidence) |
| `ownership_status: TERMINAL_RELEASED` | Mapped to `CUSTODY_RELEASED` |
| `ownership_status: UNKNOWN_FAIL_CLOSED` | Mapped to `CUSTODY_UNCERTAIN` |
| Terminal release requires `release_decision_ref + outcome_ref` | Represented in `ReleaseEvidence.extra` dict |
| `observe()` returning `automatic_reclaim=False` for stale | Invariants 3 & 4: process death / TTL alone do not release |
