"""writer_custody — CLH logical writer-authority contract (I0-A, LEAF-04).

# Trust boundary: logical authority ≠ physical OS write capability

CLH grants **logical** writer authority through the lease/generation/resource
machinery in ``leases.py``.  Physical OS-level write access is independent:
a process may hold a filesystem handle to a repository even after its CLH
writer authority has expired or been superseded.  CLH does not OS-revoke that
handle; authority is determined solely by the CLH lease state, not by the OS.

Key invariants (from I0-SHARED-INTERFACE-CONTRACT §2, Rules F1):

1. Positive release evidence is required before successor admission.
2. CUSTODY_UNCERTAIN → deny successor (fail closed).
3. Process death alone does NOT release authority.
4. TTL alone does NOT release uncertain authority.
5. Stale generation cannot release or replace the current writer.
6. Only CLH mutates writer authority (CLF/CLE may supply evidence packages).

# Reconciled G07 concepts (read-only; G07 branch not merged)

G07 (agent/v5-fasttrack-g07-d3-ownership-e1-001) introduced ``release_authority``
(NORMAL / STALE_RECOVERY), ``ownership_status`` observation
(STALE_ACTIVE, TERMINAL_RELEASED, UNKNOWN_FAIL_CLOSED), and a two-field
positive-release-evidence requirement (``release_decision_ref`` + ``outcome_ref``
bound to the exact lease generation and writer).

The concepts reconciled here use the same terminology as G07 for consistency,
scoped to the v1 lease machinery on this branch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Custody status
# ---------------------------------------------------------------------------


class CustodyStatus(StrEnum):
    """CLH-visible custody classification for one writer generation.

    CLH consults this status before admitting a successor writer.  Any status
    other than ``CUSTODY_RELEASED`` must cause successor admission to fail
    closed.
    """

    #: The generation is ACTIVE; no positive release evidence has been
    #: recorded.  Normal in-progress state.
    CUSTODY_HELD = "CUSTODY_HELD"

    #: Positive release evidence has been recorded by CLH for the exact
    #: generation.  Successor admission is permitted (subject to overlap
    #: checks).
    CUSTODY_RELEASED = "CUSTODY_RELEASED"

    #: Release cannot be positively established (evidence missing,
    #: contradictory, or bound to the wrong resource/generation/writer).
    #: Successor writer admission MUST fail closed.
    CUSTODY_UNCERTAIN = "CUSTODY_UNCERTAIN"


# ---------------------------------------------------------------------------
# Release evidence record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseEvidence:
    """Structured positive-release evidence bound to one CLH writer generation.

    Every field is required; an absent or mismatched field yields
    ``CUSTODY_UNCERTAIN`` (see ``evaluate_successor_admission``).

    Reconciled from G07's terminal-release record:  G07 binds
    ``release_decision_ref`` + ``outcome_ref`` + ``release_authority``
    (NORMAL / STALE_RECOVERY) to the exact lease generation.  This record
    is the v1-compatible minimum version of that binding.
    """

    #: Stable identifier for the coordination resource (maps to CLH
    #: ``coordination_repository`` / lock-root namespace).
    resource_id: str

    #: Lease identifier as written to the lock-root file.
    lease_id: str

    #: The *current* generation at the moment of release.  Must equal the
    #: generation stored in the active lease; a stale value yields
    #: ``CUSTODY_UNCERTAIN``.
    generation: int

    #: The writer being released.  Must equal ``active_writer_repository``
    #: in the active lease record.
    writer_id: str

    #: Human-readable or machine-readable release justification.  Non-empty
    #: string required; "process died" or "TTL expired" alone are NOT
    #: sufficient (see module-level invariants 3 & 4).
    release_justification: str

    #: UTC timestamp string when evidence was recorded (ISO-8601 / ``Z``
    #: suffix, same convention as lease timestamps).  Required non-empty.
    recorded_utc: str

    #: Additional evidence fields (e.g. outcome_ref, decision_ref).
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# In-process evidence store (one CLH instance owns this store)
# ---------------------------------------------------------------------------

# Maps (resource_id, lease_id, generation) → ReleaseEvidence.
# Only CLH may write to this store (see invariant 6 above).
_EVIDENCE_STORE: dict[tuple[str, str, int], ReleaseEvidence] = {}


def record_release_evidence(evidence: ReleaseEvidence) -> None:
    """Record positive release evidence for one writer generation.

    This is the **only** function that may insert into the evidence store.
    CLF/CLE may *supply* an ``evidence`` package; they do not call this
    function directly — CLH validates and then calls it.

    Raises ``ValueError`` if the evidence fields are structurally invalid
    (empty strings, non-positive generation, etc.).  Does NOT check against
    a live lease file; that validation belongs in the acquire/replace/release
    path in ``leases.py``.
    """
    _validate_evidence(evidence)
    key = (evidence.resource_id, evidence.lease_id, evidence.generation)
    _EVIDENCE_STORE[key] = evidence


def _validate_evidence(evidence: ReleaseEvidence) -> None:
    """Raise ``ValueError`` for structurally invalid evidence."""
    if not isinstance(evidence.resource_id, str) or not evidence.resource_id.strip():
        raise ValueError("ReleaseEvidence.resource_id must be a non-empty string")
    if not isinstance(evidence.lease_id, str) or not evidence.lease_id.strip():
        raise ValueError("ReleaseEvidence.lease_id must be a non-empty string")
    if not isinstance(evidence.generation, int) or evidence.generation < 1:
        raise ValueError("ReleaseEvidence.generation must be a positive integer")
    if not isinstance(evidence.writer_id, str) or not evidence.writer_id.strip():
        raise ValueError("ReleaseEvidence.writer_id must be a non-empty string")
    justification = evidence.release_justification
    if not isinstance(justification, str) or not justification.strip():
        raise ValueError(
            "ReleaseEvidence.release_justification must be a non-empty string; "
            "process death or TTL expiry alone are not sufficient"
        )
    if not isinstance(evidence.recorded_utc, str) or not evidence.recorded_utc.strip():
        raise ValueError("ReleaseEvidence.recorded_utc must be a non-empty string")


# ---------------------------------------------------------------------------
# Successor admission evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdmissionDecision:
    """Result of ``evaluate_successor_admission``."""

    #: Whether a successor may be admitted.
    permitted: bool

    #: The custody status that drove the decision.
    custody_status: CustodyStatus

    #: Human-readable findings (empty when ``permitted`` is True).
    findings: list[str] = field(default_factory=list)


def evaluate_successor_admission(
    resource_id: str,
    lease_id: str,
    current_generation: int,
    current_writer_id: str,
    lease_state: str,
) -> AdmissionDecision:
    """Evaluate whether a successor writer may be admitted for ``lease_id``.

    This function is the **gate** that must be consulted by ``acquire`` and
    ``replace`` before admitting a new writer for a resource that already
    has (or had) an active generation.

    Args:
        resource_id: Coordination resource identifier.
        lease_id: The lease whose writer is being replaced.
        current_generation: Generation stored in the current active lease.
        current_writer_id: ``active_writer_repository`` of the current lease.
        lease_state: ``"ACTIVE"`` or ``"RELEASED"`` as stored in the lease.

    Returns:
        ``AdmissionDecision`` with ``permitted=True`` only when custody is
        positively established as released.

    Custody rules applied (fail-closed for anything other than RELEASED):

    * If ``lease_state`` is ``"RELEASED"`` **and** the lease record's own
      generation CAS already incremented past ``current_generation``, the
      lease machinery in ``leases.py`` already enforced positive release.
      This function additionally checks the evidence store for belt-and-
      suspenders validation.

    * If ``lease_state`` is ``"ACTIVE"``, the prior generation must have
      a matching ``ReleaseEvidence`` record; otherwise ``CUSTODY_UNCERTAIN``.

    * Missing or contradictory evidence → ``CUSTODY_UNCERTAIN`` → deny.

    * Process death alone / TTL alone → evidence absent → ``CUSTODY_UNCERTAIN``.

    * Stale generation in evidence (generation != current_generation) →
      evidence does not cover this generation → ``CUSTODY_UNCERTAIN``.
    """
    findings: list[str] = []

    # A RELEASED lease's own CAS increment is treated as in-band positive
    # evidence (the release() function in leases.py performs the CAS and
    # sets state=RELEASED atomically).  Still require an evidence record
    # if one was explicitly registered.
    if lease_state == "RELEASED":
        key = (resource_id, lease_id, current_generation)
        ev = _EVIDENCE_STORE.get(key)
        if ev is not None:
            # Belt-and-suspenders: validate the stored evidence matches.
            if ev.writer_id != current_writer_id:
                findings.append(
                    f"Evidence writer_id '{ev.writer_id}' does not match "
                    f"current writer '{current_writer_id}'"
                )
                return AdmissionDecision(
                    permitted=False,
                    custody_status=CustodyStatus.CUSTODY_UNCERTAIN,
                    findings=findings,
                )
        return AdmissionDecision(permitted=True, custody_status=CustodyStatus.CUSTODY_RELEASED)

    # ACTIVE lease: require explicit evidence.
    if lease_state != "ACTIVE":
        findings.append(f"Unrecognised lease_state '{lease_state}'")
        return AdmissionDecision(
            permitted=False,
            custody_status=CustodyStatus.CUSTODY_UNCERTAIN,
            findings=findings,
        )

    key = (resource_id, lease_id, current_generation)
    ev = _EVIDENCE_STORE.get(key)

    if ev is None:
        # Check whether stale evidence exists (different generation, same lease/resource).
        stale_keys = [
            k
            for k in _EVIDENCE_STORE
            if k[0] == resource_id and k[1] == lease_id and k[2] != current_generation
        ]
        if stale_keys:
            stale_gens = sorted(k[2] for k in stale_keys)
            findings.append(
                f"Stale evidence exists for generation(s) {stale_gens} but not for "
                f"current generation {current_generation}; "
                f"stale evidence cannot release or replace the current writer "
                f"(resource='{resource_id}' lease='{lease_id}')."
            )
        else:
            findings.append(
                f"No positive release evidence found for "
                f"resource='{resource_id}' lease='{lease_id}' generation={current_generation}. "
                "Process death and TTL expiry alone do not constitute release evidence."
            )
        return AdmissionDecision(
            permitted=False,
            custody_status=CustodyStatus.CUSTODY_UNCERTAIN,
            findings=findings,
        )

    # Validate evidence binding.
    if ev.writer_id != current_writer_id:
        findings.append(
            f"Evidence writer_id '{ev.writer_id}' does not match "
            f"current writer '{current_writer_id}'"
        )
    if ev.generation != current_generation:
        findings.append(
            f"Evidence generation {ev.generation} does not match "
            f"current generation {current_generation} (stale evidence cannot release)"
        )
    if ev.resource_id != resource_id:
        findings.append(
            f"Evidence resource_id '{ev.resource_id}' does not match "
            f"requested resource '{resource_id}'"
        )
    if ev.lease_id != lease_id:
        findings.append(
            f"Evidence lease_id '{ev.lease_id}' does not match requested lease '{lease_id}'"
        )

    if findings:
        return AdmissionDecision(
            permitted=False,
            custody_status=CustodyStatus.CUSTODY_UNCERTAIN,
            findings=findings,
        )

    return AdmissionDecision(permitted=True, custody_status=CustodyStatus.CUSTODY_RELEASED)


def get_custody_status(
    resource_id: str,
    lease_id: str,
    generation: int,
    writer_id: str,
    lease_state: str,
) -> CustodyStatus:
    """Return the custody status for one writer generation (read-only query).

    Convenience wrapper around ``evaluate_successor_admission`` that returns
    only the ``CustodyStatus`` without the full admission decision.
    """
    decision = evaluate_successor_admission(
        resource_id=resource_id,
        lease_id=lease_id,
        current_generation=generation,
        current_writer_id=writer_id,
        lease_state=lease_state,
    )
    return decision.custody_status


def clear_evidence_store() -> None:
    """Remove all entries from the in-process evidence store.

    Intended for use in tests only.  Production CLH instances do not call
    this function; the store persists for the lifetime of the CLH process.
    """
    _EVIDENCE_STORE.clear()
