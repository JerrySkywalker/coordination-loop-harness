"""Untrusted custody observations for source preparation (I0-A, LEAF-04).

This module never grants successor authority. Caller state and this process-local
store cannot authenticate a Lease/Decision or prove prior-writer settlement.
Current published lease verification and public runtime HOLD remain authoritative.
The historical contract discussion below describes intended concepts only.

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
    """Custody vocabulary for source preparation. The public evaluator returns only CUSTODY_UNCERTAIN until authenticated integration is separately admitted. No enum value itself grants authority."""

    #: The generation is ACTIVE; no positive release evidence has been
    #: recorded.  Normal in-progress state.
    CUSTODY_HELD = "CUSTODY_HELD"

    #: Reserved vocabulary for future authenticated integration; not an
    #: admission result produced by this observation helper.
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
    """Caller-supplied observation metadata, not authenticated release proof. Identity and string checks are structural only; neither this record nor its process-local storage establishes an issuer or settles actual prior-writer custody."""

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
# Process-local observations are untrusted and confer no issuer authority.
_EVIDENCE_STORE: dict[tuple[str, str, int], ReleaseEvidence] = {}


def record_release_evidence(evidence: ReleaseEvidence) -> None:
    """Record structurally valid caller observations in this process. This function does not authenticate an issuer, read a live protected Lease/Decision, or grant release or successor authority. Invalid fields raise ValueError."""
    _validate_evidence(evidence)
    key = (evidence.resource_id, evidence.lease_id, evidence.generation)
    _EVIDENCE_STORE[key] = evidence


def _validate_evidence(evidence: ReleaseEvidence) -> None:
    """Raise ``ValueError`` for structurally invalid evidence."""
    if not isinstance(evidence.resource_id, str) or not evidence.resource_id.strip():
        raise ValueError("ReleaseEvidence.resource_id must be a non-empty string")
    if not isinstance(evidence.lease_id, str) or not evidence.lease_id.strip():
        raise ValueError("ReleaseEvidence.lease_id must be a non-empty string")
    if type(evidence.generation) is not int or evidence.generation < 1:
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
    """Deny successor authority from unauthenticated caller observations. The accepted Lease/Decision verification and public runtime Gate remain authoritative and unchanged. Matching state strings and in-memory records are insufficient; all outcomes remain permitted=False and CUSTODY_UNCERTAIN. Existing mismatch diagnostics are retained for preparation only."""
    findings: list[str] = []

    # The caller-supplied RELEASED string is an unauthenticated observation.
    # This helper verifies no protected lease, Decision, or generation CAS;
    # even a structurally matching process-local record cannot grant authority.
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
        return AdmissionDecision(
            permitted=False,
            custody_status=CustodyStatus.CUSTODY_UNCERTAIN,
            findings=["AUTHENTICATED_CUSTODY_UNPROVEN: caller state and in-process evidence cannot authorize successor admission"],
        )

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

    return AdmissionDecision(
            permitted=False,
            custody_status=CustodyStatus.CUSTODY_UNCERTAIN,
            findings=["AUTHENTICATED_CUSTODY_UNPROVEN: caller state and in-process evidence cannot authorize successor admission"],
        )


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
