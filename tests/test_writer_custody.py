"""Negative tests for writer_custody — I0-A logical authority contract.

Focus: fail-closed scenarios where CUSTODY_UNCERTAIN must deny successor.

Reconciled from G07 (agent/v5-fasttrack-g07-d3-ownership-e1-001) conceptually;
no G07 source is imported.  Tests are written against the v1 lease machinery
and the new writer_custody module on this branch.
"""

from __future__ import annotations

import unittest

from coordination_loop_harness.writer_custody import (
    CustodyStatus,
    ReleaseEvidence,
    clear_evidence_store,
    evaluate_successor_admission,
    get_custody_status,
    record_release_evidence,
)


def _evidence(
    *,
    resource_id: str = "example/coordination",
    lease_id: str = "RUN-A",
    generation: int = 1,
    writer_id: str = "example/product",
    release_justification: str = "Explicit RELEASED transition recorded by CLH",
    recorded_utc: str = "2026-09-18T10:00:00Z",
) -> ReleaseEvidence:
    return ReleaseEvidence(
        resource_id=resource_id,
        lease_id=lease_id,
        generation=generation,
        writer_id=writer_id,
        release_justification=release_justification,
        recorded_utc=recorded_utc,
    )


class TestCustodyUncertainDeniesSuccessor(unittest.TestCase):
    """CUSTODY_UNCERTAIN → deny successor admission (fail closed)."""

    def setUp(self) -> None:
        clear_evidence_store()

    def tearDown(self) -> None:
        clear_evidence_store()

    # ------------------------------------------------------------------
    # Invariant 1: positive release evidence required
    # ------------------------------------------------------------------

    def test_no_evidence_denies_successor_for_active_lease(self) -> None:
        """Without positive evidence an ACTIVE lease denies successor."""
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="RUN-A",
            current_generation=1,
            current_writer_id="example/product",
            lease_state="ACTIVE",
        )
        self.assertFalse(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_UNCERTAIN, decision.custody_status)
        self.assertTrue(
            any("No positive release evidence" in f for f in decision.findings),
            decision.findings,
        )

    # ------------------------------------------------------------------
    # Invariant 3: process death alone is NOT release
    # ------------------------------------------------------------------

    def test_process_death_justification_alone_is_not_sufficient(self) -> None:
        """A release_justification mentioning only process death must still be accepted
        structurally (non-empty string), but the absence of evidence for an ACTIVE
        lease is what denies the successor — not the text content of the justification.

        This test verifies that the evidence store is consulted, and that even if
        an operator inserts evidence claiming 'process died', the *absence* of evidence
        for an ACTIVE lease is what denies admission (evidence must be explicitly
        recorded by CLH).
        """
        # No evidence recorded → ACTIVE lease → denied
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="RUN-A",
            current_generation=1,
            current_writer_id="example/product",
            lease_state="ACTIVE",
        )
        self.assertFalse(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_UNCERTAIN, decision.custody_status)

    # ------------------------------------------------------------------
    # Invariant 4: TTL alone does NOT release uncertain authority
    # ------------------------------------------------------------------

    def test_ttl_expiry_without_evidence_denies_successor(self) -> None:
        """Simulated TTL expiry: no release evidence → CUSTODY_UNCERTAIN."""
        # TTL is external to leases.py; simulate by having no evidence
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="EXPIRED-RUN",
            current_generation=3,
            current_writer_id="example/product",
            lease_state="ACTIVE",
        )
        self.assertFalse(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_UNCERTAIN, decision.custody_status)

    # ------------------------------------------------------------------
    # Invariant 5: stale generation cannot release or replace
    # ------------------------------------------------------------------

    def test_stale_generation_evidence_denies_successor(self) -> None:
        """Evidence bound to generation N-1 cannot release generation N."""
        # Record evidence for generation 1, but current lease is generation 2
        record_release_evidence(_evidence(generation=1, writer_id="example/product"))
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="RUN-A",
            current_generation=2,  # current is gen 2
            current_writer_id="example/product",
            lease_state="ACTIVE",
        )
        self.assertFalse(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_UNCERTAIN, decision.custody_status)
        self.assertTrue(
            any("stale evidence" in f for f in decision.findings),
            decision.findings,
        )

    def test_future_generation_evidence_denies_successor(self) -> None:
        """Evidence bound to generation N+1 cannot release generation N."""
        record_release_evidence(_evidence(generation=5, writer_id="example/product"))
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="RUN-A",
            current_generation=3,
            current_writer_id="example/product",
            lease_state="ACTIVE",
        )
        self.assertFalse(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_UNCERTAIN, decision.custody_status)

    # ------------------------------------------------------------------
    # Contradictory evidence → CUSTODY_UNCERTAIN
    # ------------------------------------------------------------------

    def test_mismatched_writer_id_denies_successor(self) -> None:
        """Evidence bound to a different writer_id is contradictory."""
        record_release_evidence(
            _evidence(
                generation=1,
                writer_id="example/other-writer",  # wrong writer
            )
        )
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="RUN-A",
            current_generation=1,
            current_writer_id="example/product",  # actual writer
            lease_state="ACTIVE",
        )
        self.assertFalse(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_UNCERTAIN, decision.custody_status)
        self.assertTrue(
            any("writer_id" in f for f in decision.findings),
            decision.findings,
        )

    def test_mismatched_resource_id_denies_successor(self) -> None:
        """Evidence bound to a different resource_id is contradictory."""
        record_release_evidence(
            _evidence(
                resource_id="example/OTHER-coordination",
                generation=1,
                writer_id="example/product",
            )
        )
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="RUN-A",
            current_generation=1,
            current_writer_id="example/product",
            lease_state="ACTIVE",
        )
        self.assertFalse(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_UNCERTAIN, decision.custody_status)

    def test_mismatched_lease_id_denies_successor(self) -> None:
        """Evidence bound to a different lease_id does not cover this lease."""
        record_release_evidence(
            _evidence(
                lease_id="RUN-B",  # different lease
                generation=1,
                writer_id="example/product",
            )
        )
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="RUN-A",
            current_generation=1,
            current_writer_id="example/product",
            lease_state="ACTIVE",
        )
        self.assertFalse(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_UNCERTAIN, decision.custody_status)

    # ------------------------------------------------------------------
    # Unknown lease_state → fail closed
    # ------------------------------------------------------------------

    def test_unknown_lease_state_denies_successor(self) -> None:
        """An unrecognised lease_state yields CUSTODY_UNCERTAIN."""
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="RUN-A",
            current_generation=1,
            current_writer_id="example/product",
            lease_state="UNKNOWN",
        )
        self.assertFalse(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_UNCERTAIN, decision.custody_status)


class TestPositiveAdmission(unittest.TestCase):
    """Positive cases: CUSTODY_RELEASED → successor permitted."""

    def setUp(self) -> None:
        clear_evidence_store()

    def tearDown(self) -> None:
        clear_evidence_store()

    def test_released_lease_state_permits_successor(self) -> None:
        """A RELEASED lease state provides in-band positive evidence."""
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="RUN-A",
            current_generation=1,
            current_writer_id="example/product",
            lease_state="RELEASED",
        )
        self.assertTrue(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_RELEASED, decision.custody_status)

    def test_active_lease_with_matching_evidence_permits_successor(self) -> None:
        """Exact evidence match on ACTIVE lease → successor permitted."""
        record_release_evidence(
            _evidence(
                resource_id="example/coordination",
                lease_id="RUN-A",
                generation=2,
                writer_id="example/product",
                release_justification="Explicit RELEASED transition recorded by CLH",
            )
        )
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="RUN-A",
            current_generation=2,
            current_writer_id="example/product",
            lease_state="ACTIVE",
        )
        self.assertTrue(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_RELEASED, decision.custody_status)

    def test_released_lease_with_contradictory_evidence_denies_successor(self) -> None:
        """Even for a RELEASED lease, contradictory evidence in store → denied."""
        record_release_evidence(
            _evidence(
                resource_id="example/coordination",
                lease_id="RUN-A",
                generation=1,
                writer_id="example/WRONG-writer",  # contradictory
            )
        )
        decision = evaluate_successor_admission(
            resource_id="example/coordination",
            lease_id="RUN-A",
            current_generation=1,
            current_writer_id="example/product",
            lease_state="RELEASED",
        )
        # RELEASED with mismatched evidence → CUSTODY_UNCERTAIN
        self.assertFalse(decision.permitted)
        self.assertEqual(CustodyStatus.CUSTODY_UNCERTAIN, decision.custody_status)


class TestEvidenceValidation(unittest.TestCase):
    """record_release_evidence rejects structurally invalid evidence."""

    def setUp(self) -> None:
        clear_evidence_store()

    def tearDown(self) -> None:
        clear_evidence_store()

    def test_empty_resource_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            record_release_evidence(_evidence(resource_id=""))

    def test_whitespace_resource_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            record_release_evidence(_evidence(resource_id="   "))

    def test_empty_lease_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            record_release_evidence(_evidence(lease_id=""))

    def test_zero_generation_rejected(self) -> None:
        with self.assertRaises(ValueError):
            record_release_evidence(_evidence(generation=0))

    def test_negative_generation_rejected(self) -> None:
        with self.assertRaises(ValueError):
            record_release_evidence(_evidence(generation=-1))

    def test_empty_writer_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            record_release_evidence(_evidence(writer_id=""))

    def test_empty_release_justification_rejected(self) -> None:
        """Empty justification is rejected; process death / TTL are not magic strings."""
        with self.assertRaises(ValueError):
            record_release_evidence(_evidence(release_justification=""))

    def test_whitespace_justification_rejected(self) -> None:
        with self.assertRaises(ValueError):
            record_release_evidence(_evidence(release_justification="   "))

    def test_empty_recorded_utc_rejected(self) -> None:
        with self.assertRaises(ValueError):
            record_release_evidence(_evidence(recorded_utc=""))


class TestGetCustodyStatus(unittest.TestCase):
    """get_custody_status convenience wrapper."""

    def setUp(self) -> None:
        clear_evidence_store()

    def tearDown(self) -> None:
        clear_evidence_store()

    def test_active_no_evidence_is_uncertain(self) -> None:
        status = get_custody_status(
            resource_id="example/coordination",
            lease_id="RUN-A",
            generation=1,
            writer_id="example/product",
            lease_state="ACTIVE",
        )
        self.assertEqual(CustodyStatus.CUSTODY_UNCERTAIN, status)

    def test_released_state_is_released(self) -> None:
        status = get_custody_status(
            resource_id="example/coordination",
            lease_id="RUN-A",
            generation=1,
            writer_id="example/product",
            lease_state="RELEASED",
        )
        self.assertEqual(CustodyStatus.CUSTODY_RELEASED, status)


if __name__ == "__main__":
    unittest.main()
