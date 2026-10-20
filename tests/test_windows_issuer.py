"""SOURCE_TEST: synthetic pipe/OS boundary, real v2 Lease/Decision/Git fixture."""

import dataclasses
import json
import threading
import unittest
from contextlib import nullcontext
from unittest import mock

import test_windows_custody as custody_fixture

from coordination_loop_harness.local_time import LocalDeadline, LocalTimeHold
from coordination_loop_harness.util import canonical_json_bytes, sha256_bytes
from coordination_loop_harness.windows_capability import ProcessPin, binding_wire
from coordination_loop_harness.windows_custody import SYSTEM
from coordination_loop_harness.windows_issuer import (
    WindowsIssuer,
    WriterTarget,
    require_native_deadline,
)
from coordination_loop_harness.windows_security import WindowsBoundaryHold
from coordination_loop_harness.windows_writer import WindowsWriterBinding

CLIENT = ProcessPin(43, 133_800_000_000_000_002, "S-1-5-21-100-200-300-1001")
ISSUER = ProcessPin(42, 133_800_000_000_000_001, SYSTEM)
PIPE = r"\\.\pipe\CoordinationLoop-one-shot-source-test"


class FakePipe:
    def __init__(self, raw: bytes, *, fail=None):
        self.raw = raw
        self.fail = fail
        self.sent = []
        self.checkpoint_index = 0
        self.ack_read = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def require_server_self(self, pin):
        if self.fail == "server":
            raise WindowsBoundaryHold("HOLD_PIPE_SERVER_OWNERSHIP_MISMATCH")
        assert pin == ISSUER

    def connect(self):
        if self.fail == "connect":
            raise WindowsBoundaryHold("HOLD_PIPE_IO_TIMEOUT")

    def authenticated_peer(self, pin):
        if self.fail == "peer":
            raise WindowsBoundaryHold("HOLD_PIPE_PEER_CREATION_MISMATCH")
        assert pin == CLIENT
        return nullcontext()

    def read_frame(self):
        if self.fail == "read":
            raise WindowsBoundaryHold("HOLD_PIPE_DISCONNECTED")
        if self.sent and not self.ack_read:
            if self.fail == "ack":
                raise WindowsBoundaryHold("HOLD_PIPE_DISCONNECTED")
            self.ack_read = True
            return canonical_json_bytes(
                {
                    "schema_version": "clh.windows-local-ack.v1",
                    "handoff_sha256": sha256_bytes(self.sent[0]),
                }
            )
        if self.sent:
            from coordination_loop_harness.windows_capability import CHECKPOINT_ORDER

            return canonical_json_bytes(
                {
                    "schema_version": "clh.windows-local-checkpoint.v1",
                    "stage": CHECKPOINT_ORDER[self.checkpoint_index],
                    "handoff_sha256": sha256_bytes(self.sent[0]),
                    "binding": binding_wire(self.binding),
                }
            )
        return self.raw

    def write_frame(self, data):
        if self.fail == "write" or (self.fail == "final" and self.sent):
            raise WindowsBoundaryHold("HOLD_PIPE_DISCONNECTED")
        self.sent.append(data)
        if len(self.sent) > 1:
            self.checkpoint_index += 1


class WindowsIssuerSourceTests(unittest.TestCase):
    def setUp(self):
        fixture = custody_fixture.WindowsCustodyChainTests(
            "test_real_v2_lease_decision_chain_is_reused_without_runtime_authority"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        fixture.verifier()
        self.native = mock.patch("coordination_loop_harness.windows_capability.require_system_self")
        self.native_issuer = mock.patch(
            "coordination_loop_harness.windows_issuer.require_system_self"
        )
        self.native_time = mock.patch(
            "coordination_loop_harness.windows_issuer.require_native_deadline"
        )
        self.reader = mock.patch("coordination_loop_harness.windows_custody.WindowsSecurityReader")
        self.tree = mock.patch(
            "coordination_loop_harness.windows_custody.WindowsProtectedCustodyVerifier._protected_tree",
            return_value={},
        )
        for patcher in (
            self.native,
            self.native_issuer,
            self.native_time,
            self.reader,
            self.tree,
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.target = WriterTarget.from_lease(fixture.candidate["repositories"][0])
        self.binding = WindowsWriterBinding(
            SYSTEM,
            "S-1-5-21-100-200-300-1002",
            "S-1-5-21-100-200-300-1003",
            55,
            133_800_000_000_000_003,
            "one-shot",
            1,
            sha256_bytes(fixture.serialized),
        )
        self.issuer = self.new_issuer()
        self.raw = canonical_json_bytes(self.issuer._expected_request())

    def test_fixture_clock_is_not_native_issuer_time_authority(self):
        with self.assertRaisesRegex(WindowsBoundaryHold, "NATIVE_ISSUER_DEADLINE"):
            require_native_deadline(self.fixture.deadline, ISSUER)

    def new_issuer(self, **changes):
        fields = dict(
            config=self.fixture.config,
            deadline=self.fixture.deadline,
            issuer_pin=ISSUER,
            client_pin=CLIENT,
            pipe_name=PIPE,
            lease_id="LOCAL-T1",
            serialized_lease=self.fixture.serialized,
            target=self.target,
            writer_binding=self.binding,
        )
        return WindowsIssuer(**(fields | changes))

    def claim(self, issuer=None, raw=None, *, fail=None):
        pipe = FakePipe(self.raw if raw is None else raw, fail=fail)
        pipe.binding = (issuer or self.issuer)._writer_binding
        with (
            mock.patch(
                "coordination_loop_harness.windows_issuer.WindowsPipe.first_server",
                return_value=pipe,
            ) as first,
            mock.patch(
                "coordination_loop_harness.windows_issuer.sealed_decision",
                return_value=nullcontext(),
            ),
        ):
            return (issuer or self.issuer).claim_once(), pipe, first

    def test_exact_real_chain_target_and_source_only_handoff(self):
        result, pipe, first = self.claim()
        self.assertFalse(result["custody_resolved"])
        self.assertEqual(result["target"], self.target.as_dict())
        self.assertEqual(result["generation"], 1)
        self.assertEqual(result["issuer_creation"], str(ISSUER.creation))
        self.assertEqual(json.loads(pipe.sent[0]), result)
        self.assertEqual(json.loads(pipe.sent[1])["handoff_sha256"], sha256_bytes(pipe.sent[0]))
        self.assertTrue(pipe.closed)
        first.assert_called_once_with(PIPE, client_sid=CLIENT.sid)
        with self.assertRaisesRegex(WindowsBoundaryHold, "ALREADY_ATTEMPTED"):
            self.issuer.claim_once()
        with self.assertRaisesRegex(WindowsBoundaryHold, "ALREADY_ATTEMPTED"):
            self.new_issuer().claim_once()

    def test_failed_claim_burns_intent_for_tamper_boolean_target_and_peer_loss(self):
        mutations = (
            {"generation": True},
            {"generation": 1.0},
            {"target": self.target.as_dict() | {"exact_sha": "f" * 40}},
            {"lease_sha256": "f" * 64},
            {"boot_id": "12345678-1234-1234-1234-123456789abd"},
            {"deadline_interrupt": True},
            {"deadline_interrupt": 1.0},
            {"deadline_interrupt": "01"},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                self.fixture.verifier()
                issuer = self.new_issuer()
                raw = json.dumps(
                    issuer._expected_request() | changes,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                with self.assertRaisesRegex(WindowsBoundaryHold, "CLAIM_MISMATCH"):
                    self.claim(issuer, raw)
                with self.assertRaisesRegex(WindowsBoundaryHold, "ALREADY_ATTEMPTED"):
                    issuer.claim_once()
        for fail in ("server", "connect", "peer", "read", "write", "ack", "final"):
            with self.subTest(fail=fail):
                self.fixture.verifier()
                issuer = self.new_issuer()
                with self.assertRaises(WindowsBoundaryHold):
                    self.claim(issuer, fail=fail)
                with self.assertRaisesRegex(WindowsBoundaryHold, "ALREADY_ATTEMPTED"):
                    issuer.claim_once()

    def test_wrong_target_multiwriter_and_lease_drift_fail_closed(self):
        wrong = dataclasses.replace(self.target, worktree_root=self.target.canonical_path)
        with self.assertRaisesRegex(WindowsBoundaryHold, "TARGET_MISMATCH"):
            self.claim(
                self.new_issuer(target=wrong),
                canonical_json_bytes(self.issuer._expected_request() | {"target": wrong.as_dict()}),
            )
        self.fixture.candidate["repositories"].append(
            dict(self.fixture.candidate["repositories"][0], repository="local/other")
        )
        self.fixture.write_candidate()
        self.fixture.verifier()
        self.binding = dataclasses.replace(
            self.binding, lease_sha256=sha256_bytes(self.fixture.serialized)
        )
        issuer = self.new_issuer()
        raw = canonical_json_bytes(issuer._expected_request())
        with self.assertRaises(ValueError):
            self.claim(issuer, raw)

    def test_root_boot_and_expiry_refuse_before_handoff_and_burn_attempt(self):
        anchor = self.fixture.deadline.intent.anchor
        cases = (
            ("root", anchor, 600),
            (
                "boot",
                dataclasses.replace(anchor, boot_id="12345678-1234-1234-1234-123456789abd"),
                600,
            ),
            (
                "expiry",
                dataclasses.replace(
                    anchor,
                    interrupt_before=anchor.interrupt_before + 10_000_000,
                    interrupt_after=anchor.interrupt_after + 10_000_000,
                    unbiased=anchor.unbiased + 10_000_000,
                ),
                1,
            ),
        )
        for label, later, duration in cases:
            with self.subTest(label=label):
                values = iter((anchor, later))
                guard = LocalDeadline(
                    lambda values=values: next(values),
                    intent_id="one-shot",
                    generation=1,
                    lease_sha256=sha256_bytes(self.fixture.serialized),
                    duration_seconds=duration,
                )
                if label == "root":
                    guard.invalidate("HOLD_ROOT_OR_WATCHDOG_LOST")
                issuer = self.new_issuer(deadline=guard)
                with self.assertRaises(LocalTimeHold):
                    self.claim(issuer)
                with self.assertRaisesRegex(WindowsBoundaryHold, "ALREADY_ATTEMPTED"):
                    issuer.claim_once()

    def test_concurrent_double_claim_has_one_winner(self):
        started = threading.Event()
        release = threading.Event()
        pipe = FakePipe(self.raw)
        pipe.binding = self.binding
        original = pipe.read_frame

        def blocking_read():
            started.set()
            release.wait(2)
            return original()

        pipe.read_frame = blocking_read
        outcomes = []

        def run():
            try:
                outcomes.append(self.issuer.claim_once())
            except Exception as exc:
                outcomes.append(exc)

        with (
            mock.patch(
                "coordination_loop_harness.windows_issuer.WindowsPipe.first_server",
                return_value=pipe,
            ),
            mock.patch(
                "coordination_loop_harness.windows_issuer.sealed_decision",
                return_value=nullcontext(),
            ),
        ):
            first = threading.Thread(target=run)
            first.start()
            self.assertTrue(started.wait(2))
            second = threading.Thread(target=run)
            second.start()
            second.join(2)
            release.set()
            first.join(60)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1, repr(outcomes))
        self.assertEqual(sum(isinstance(item, WindowsBoundaryHold) for item in outcomes), 1)


if __name__ == "__main__":
    unittest.main()
