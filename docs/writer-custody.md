# Writer Custody Contract (I0-A, LEAF-04)

_CLH logical writer-authority trust boundary_

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

A successor writer may be admitted only when one of the following holds:

1. **Explicit RELEASED lease transition**: `release()` in `leases.py` has
   been called with the correct `expected_generation`, atomically setting
   `state=RELEASED` and `active_writer_repository=None`.  This is the
   primary release path.

2. **Owner-attested release package**: A `ReleaseEvidence` record has been
   registered via `writer_custody.record_release_evidence()`, bound to:
   - `resource_id` (coordination resource)
   - `lease_id`
   - `generation` (exact match; stale generation evidence fails closed)
   - `writer_id` (must equal `active_writer_repository`)

Absent or contradictory evidence → `CUSTODY_UNCERTAIN` → deny successor.

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
| `CUSTODY_RELEASED` | Positive evidence recorded; successor permitted. |
| `CUSTODY_UNCERTAIN` | Evidence missing or contradictory; deny successor. |

### `ReleaseEvidence` (dataclass)

Structured evidence bound to `resource_id / lease_id / generation / writer_id`.
Validated on insert by `record_release_evidence()`.

### `record_release_evidence(evidence)`

CLH-only write path.  CLF/CLE may supply evidence packages; CLH validates
and calls this function.

### `evaluate_successor_admission(...)`

Gate function consulted before `acquire()` or `replace()` admits a new writer.
Returns `AdmissionDecision(permitted, custody_status, findings)`.

### `get_custody_status(...)`

Read-only convenience wrapper returning `CustodyStatus`.

### `clear_evidence_store()`

Test-only utility.  Not called in production.

---

## Integration with `leases.py`

`leases.py` enforces the stale-generation CAS via the `expected_generation`
parameter on `replace()` and `release()`.  It does not currently consult
`writer_custody.evaluate_successor_admission()` automatically; callers that
admit a successor to an ACTIVE lease must consult custody explicitly.

Future work (out of scope for LEAF-04): integrate
`evaluate_successor_admission` into the `replace()` admission mutex path.

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
