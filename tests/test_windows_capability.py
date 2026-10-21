"""SOURCE_TEST: mocked Windows kernel primitives, never a runtime qualification."""

import ctypes
import json
import threading
import unittest
from contextlib import nullcontext
from ctypes import wintypes as w
from types import SimpleNamespace
from unittest import mock

from coordination_loop_harness.local_time import HostSample, LocalDeadline, LocalTimeHold
from coordination_loop_harness.util import canonical_json_bytes, sha256_bytes
from coordination_loop_harness.windows_capability import (
    INVALID_HANDLE,
    ProcessPin,
    WindowsPipe,
    _SecurityAttributes,
    parse_interrupt_deadline,
    parse_native_handle,
    request_source_handoff,
    require_client_boot_deadline,
)
from coordination_loop_harness.windows_custody import SYSTEM
from coordination_loop_harness.windows_security import TokenSecurity, WindowsBoundaryHold
from coordination_loop_harness.windows_writer import WindowsWriterBinding

PIN = ProcessPin(43, 133_800_000_000_000_002, "S-1-5-21-100-200-300-1001")
SERVER = ProcessPin(42, 133_800_000_000_000_001, SYSTEM)
NAME = r"\\.\pipe\CoordinationLoop-source-test"


class PeerKernelTests(unittest.TestCase):
    def pipe(
        self,
        *,
        pid=43,
        creation=PIN.creation,
        sid=PIN.sid,
        token_type=1,
        groups=(),
        privileges=(),
        restricting=(),
    ):
        def query(_handle, output):
            ctypes.cast(output, ctypes.POINTER(w.ULONG))[0] = pid
            return 1

        def times(_handle, created, *_rest):
            value = ctypes.cast(created, ctypes.POINTER(w.FILETIME))[0]
            value.dwLowDateTime = creation & 0xFFFFFFFF
            value.dwHighDateTime = creation >> 32
            return 1

        pipe = WindowsPipe.__new__(WindowsPipe)
        pipe.handle = 123
        pipe.server = True
        pipe.name = NAME
        pipe._closed = False
        pipe._io_lock = threading.RLock()
        pipe._reader = SimpleNamespace(
            token_security=lambda _handle: TokenSecurity(
                sid, groups, restricting, privileges, token_type
            )
        )
        pipe._k = SimpleNamespace(
            GetNamedPipeClientProcessId=query,
            OpenProcess=lambda *_args: 777,
            GetProcessId=lambda _handle: pid,
            GetProcessTimes=times,
            WaitForSingleObject=lambda *_args: 258,
            CloseHandle=mock.Mock(),
        )
        return pipe

    def test_peer_process_access_is_minimal_and_wait_capable(self):
        for server, pin, access in ((True, PIN, 0x101040), (False, SERVER, 0x101000)):
            with self.subTest(server=server):
                pipe = self.pipe(pid=pin.pid, creation=pin.creation, sid=pin.sid)
                pipe.server = server
                pipe._k.GetNamedPipeServerProcessId = pipe._k.GetNamedPipeClientProcessId
                pipe._k.OpenProcess = mock.Mock(return_value=777)
                with pipe.authenticated_peer(pin):
                    pass
                pipe._k.OpenProcess.assert_called_once_with(access, False, pin.pid)
                pipe._k.CloseHandle.assert_called_once_with(777)

    def test_failed_or_signaled_peer_wait_refuses_and_closes_process(self):
        for waits in ((0,), (0xFFFFFFFF,), (258, 0), (258, 0xFFFFFFFF)):
            with self.subTest(waits=waits):
                pipe = self.pipe()
                pipe._k.WaitForSingleObject = mock.Mock(side_effect=waits)
                with self.assertRaisesRegex(WindowsBoundaryHold, "PEER_(NOT_RUNNING|CHANGED)"):
                    with pipe.authenticated_peer(PIN):
                        pass
                self.assertIsNone(pipe._peer_process)
                pipe._k.CloseHandle.assert_called_once_with(777)

    def test_kernel_pid_creation_and_primary_token_are_all_required(self):
        pipe = self.pipe()
        with pipe.authenticated_peer(PIN):
            pass
        pipe._k.CloseHandle.assert_called_once_with(777)
        for change in (
            {"pid": 44},
            {"creation": PIN.creation + 1},
            {"sid": "S-1-5-21-100-200-300-9999"},
            {"token_type": 2},
            {"groups": ("S-1-5-32-544",)},
            {"privileges": ("SeDebugPrivilege",)},
            {"restricting": ("S-1-5-18",)},
        ):
            with self.subTest(change=change):
                with self.assertRaises(WindowsBoundaryHold):
                    with self.pipe(**change).authenticated_peer(PIN):
                        self.fail("wrong kernel identity entered authenticated scope")

    def test_query_only_handles_come_from_authenticated_client_and_close(self):
        pipe = self.pipe()
        copied = []

        def duplicate(source_process, source, target_process, result, access, inherit, options):
            copied.append((source_process, source, target_process, access, inherit, options))
            ctypes.cast(result, ctypes.POINTER(w.HANDLE))[0] = 900 + len(copied)
            return 1

        pipe._k.DuplicateHandle = mock.Mock(side_effect=duplicate)
        pipe._k.GetCurrentProcess = mock.Mock(return_value=888)
        with self.assertRaisesRegex(WindowsBoundaryHold, "AUTHENTICATED_CLIENT"):
            with pipe.duplicate_peer_query_handles(11, 12):
                pass

        failed = self.pipe()
        failed_copied = []

        def fail_second(*args):
            failed_copied.append(args)
            if len(failed_copied) == 2:
                return 0
            ctypes.cast(args[3], ctypes.POINTER(w.HANDLE))[0] = 903
            return 1

        failed._k.DuplicateHandle = mock.Mock(side_effect=fail_second)
        failed._k.GetCurrentProcess = mock.Mock(return_value=888)
        with failed.authenticated_peer(PIN):
            with self.assertRaisesRegex(WindowsBoundaryHold, "DUPLICATION_FAILED"):
                with failed.duplicate_peer_query_handles(11, 12):
                    pass
        self.assertEqual(
            failed._k.CloseHandle.call_args_list[-2:], [mock.call(903), mock.call(777)]
        )
        with pipe.authenticated_peer(PIN):
            with pipe.duplicate_peer_query_handles(11, 12) as handles:
                self.assertEqual(handles, (901, 902))
        self.assertEqual(
            copied,
            [
                (777, 11, 888, 0x1000, False, 0),
                (777, 12, 888, 0x0004, False, 0),
            ],
        )
        self.assertEqual(
            pipe._k.CloseHandle.call_args_list[-3:],
            [mock.call(902), mock.call(901), mock.call(777)],
        )
        with self.assertRaisesRegex(WindowsBoundaryHold, "AUTHENTICATED_CLIENT"):
            with pipe.duplicate_peer_query_handles(11, 12):
                pass

    def test_first_server_is_single_local_noninheritable_and_rejects_squatting(self):
        captured = {}

        def descriptor(sddl, _version, output, _size):
            captured["sddl"] = sddl
            ctypes.cast(output, ctypes.POINTER(w.LPVOID))[0] = 1234
            return 1

        def create(name, flags, mode, count, *_args):
            attributes = ctypes.cast(_args[-1], ctypes.POINTER(_SecurityAttributes))[0]
            captured.update(
                name=name,
                flags=flags,
                mode=mode,
                count=count,
                inherit=bool(attributes.inherit),
            )
            return 555

        k = SimpleNamespace(
            CreateNamedPipeW=mock.Mock(side_effect=create),
            LocalFree=mock.Mock(),
            CloseHandle=mock.Mock(),
        )
        a = SimpleNamespace(
            ConvertStringSecurityDescriptorToSecurityDescriptorW=mock.Mock(side_effect=descriptor)
        )
        with (
            mock.patch(
                "coordination_loop_harness.windows_capability.os", SimpleNamespace(name="nt")
            ),
            mock.patch(
                "coordination_loop_harness.windows_capability.ctypes.WinDLL",
                side_effect=lambda name, **_kwargs: k if name == "kernel32" else a,
                create=True,
            ),
            mock.patch.object(WindowsPipe, "__init__", return_value=None),
        ):
            WindowsPipe.first_server(NAME, client_sid=PIN.sid)
        self.assertEqual(k.CloseHandle.argtypes, [w.HANDLE])
        self.assertEqual(captured["name"], NAME)
        self.assertTrue(captured["flags"] & 0x00080000)
        self.assertTrue(captured["flags"] & 0x40000000)
        self.assertEqual(captured["mode"] & 0x8, 0x8)
        self.assertEqual(captured["count"], 1)
        self.assertFalse(captured["inherit"])
        self.assertIn("D:P", captured["sddl"])
        self.assertIn(f"0x00100083;;;{PIN.sid}", captured["sddl"])
        k.LocalFree.assert_called_once()
        k.CreateNamedPipeW.side_effect = lambda *_args: INVALID_HANDLE
        with (
            mock.patch(
                "coordination_loop_harness.windows_capability.os", SimpleNamespace(name="nt")
            ),
            mock.patch(
                "coordination_loop_harness.windows_capability.ctypes.WinDLL",
                side_effect=lambda name, **_kwargs: k if name == "kernel32" else a,
                create=True,
            ),
            self.assertRaisesRegex(WindowsBoundaryHold, "FIRST_PIPE_INSTANCE"),
        ):
            WindowsPipe.first_server(NAME, client_sid=PIN.sid)

        full_handle = 0x123456789
        k.CreateNamedPipeW.side_effect = lambda *_args: full_handle
        with (
            mock.patch(
                "coordination_loop_harness.windows_capability.os", SimpleNamespace(name="nt")
            ),
            mock.patch(
                "coordination_loop_harness.windows_capability.ctypes.WinDLL",
                side_effect=lambda name, **_kwargs: k if name == "kernel32" else a,
                create=True,
            ),
            mock.patch.object(
                WindowsPipe, "__init__", side_effect=RuntimeError("constructor failed")
            ),
            self.assertRaisesRegex(RuntimeError, "constructor failed"),
        ):
            WindowsPipe.first_server(NAME, client_sid=PIN.sid)
        k.CloseHandle.assert_called_once_with(full_handle)

    def test_peer_replacement_during_exchange_is_refused_and_handle_closed(self):
        pipe = self.pipe()
        calls = 0

        def changing(_handle, output):
            nonlocal calls
            calls += 1
            ctypes.cast(output, ctypes.POINTER(w.ULONG))[0] = 43 if calls == 1 else 44
            return 1

        pipe._k.GetNamedPipeClientProcessId = changing
        with self.assertRaisesRegex(WindowsBoundaryHold, "PEER_CHANGED"):
            with pipe.authenticated_peer(PIN):
                pass
        pipe._k.CloseHandle.assert_called_once_with(777)

    def test_close_waits_for_outstanding_io(self):
        pipe = self.pipe()
        entered, release = threading.Event(), threading.Event()

        def blocked(*_args):
            entered.set()
            release.wait(2)
            return 0

        with mock.patch.object(pipe, "_locked_operation", side_effect=blocked):
            io = threading.Thread(target=lambda: pipe._operation("ReadFile"))
            io.start()
            self.assertTrue(entered.wait(2))
            closer = threading.Thread(target=pipe.close)
            closer.start()
            self.assertFalse(pipe._closed)
            release.set()
            io.join(2)
            closer.join(2)
        self.assertTrue(pipe._closed)
        pipe._k.CloseHandle.assert_called_once_with(pipe.handle)


class ClientHandoffTests(unittest.TestCase):
    def test_client_native_boot_and_deadline_check_refuses_changed_or_expired(self):
        boot = "12345678-1234-1234-1234-123456789abc"
        request = {"boot_id": boot, "deadline_interrupt": "200"}
        for sample, accepted in (
            (HostSample(boot, "43:1", 100, 100, 100), True),
            (HostSample(boot, "43:1", 200, 200, 200), False),
            (
                HostSample("12345678-1234-1234-1234-123456789abd", "43:1", 100, 100, 100),
                False,
            ),
        ):
            with (
                self.subTest(sample=sample),
                mock.patch(
                    "coordination_loop_harness.windows_capability.WindowsHostClock",
                    return_value=SimpleNamespace(read=lambda sample=sample: sample),
                ),
            ):
                if accepted:
                    require_client_boot_deadline(request)
                else:
                    with self.assertRaises(WindowsBoundaryHold):
                        require_client_boot_deadline(request)

    def test_client_authenticates_server_and_rejects_detached_tamper(self):
        request = {
            "schema_version": "clh.windows-local-claim.v1",
            "intent_id": "one-shot",
            "lease_id": "LOCAL-T1",
            "generation": 1,
            "boot_id": "12345678-1234-1234-1234-123456789abc",
            "deadline_interrupt": "6000",
            "lease_sha256": "a" * 64,
            "worker_sid": "S-1-5-21-100-200-300-1002",
            "scratch_sid": "S-1-5-21-100-200-300-1003",
            "target": {
                "repository": "local/example",
                "canonical_path": "C:/example",
                "worktree_root": "C:/example",
                "branch_ref": "refs/heads/main",
                "exact_sha": "b" * 40,
            },
        }
        response = {
            "schema_version": "clh.windows-local-handoff.v1",
            "request_sha256": sha256_bytes(canonical_json_bytes(request)),
            "decision_ref": "decisions/example.json",
            "decision_sha256": "c" * 64,
            "issuer_pid": SERVER.pid,
            "issuer_creation": str(SERVER.creation),
            "client_pid": PIN.pid,
            "client_creation": str(PIN.creation),
            "boot_id": request["boot_id"],
            "deadline_interrupt": request["deadline_interrupt"],
            "lease_sha256": request["lease_sha256"],
            "generation": request["generation"],
            "target": request["target"],
            "custody_resolved": False,
            "verification": "WINDOWS_CLH_SOURCE_SNAPSHOT_ONLY",
            "runtime_gate": "G_L1_ROOT_AND_CONSUMER_BOUNDARY_UNQUALIFIED",
        }
        binding = WindowsWriterBinding(
            SYSTEM,
            "S-1-5-21-100-200-300-1002",
            "S-1-5-21-100-200-300-1003",
            55,
            133_800_000_000_000_003,
            "one-shot",
            1,
            "a" * 64,
        )
        sample = HostSample(request["boot_id"], "43:1", 100, 100, 100)
        deadline = LocalDeadline(
            lambda: sample,
            intent_id="one-shot",
            generation=1,
            lease_sha256="a" * 64,
            duration_seconds=600,
            shorter_cap=6000,
        )

        class Client:
            def __init__(self, raw, *, wrong_final=False):
                self.raw = raw
                self.wrong_final = wrong_final
                self.closed = False
                self.authenticated = False
                self.reads = 0
                self.writes = 0
                self.last_stage = None
                self.stage_writes = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.closed = True

            def authenticated_peer(self, pin):
                assert pin == SERVER
                self.authenticated = True
                return nullcontext()

            def write_frame(self, raw):
                if self.writes == 0:
                    assert raw == canonical_json_bytes(request)
                elif self.writes == 1:
                    assert raw == canonical_json_bytes(
                        {
                            "schema_version": "clh.windows-local-ack.v1",
                            "handoff_sha256": sha256_bytes(self.raw),
                        }
                    )
                else:
                    payload = json.loads(raw)
                    if payload["schema_version"] == "clh.windows-local-bind.v1":
                        self.last_stage = "bind"
                    else:
                        self.last_stage = payload["stage"]
                        if self.last_stage != "pre_spawn_watchdog":
                            assert (
                                self.last_stage
                                == ("resume", "effect_publication", "receipt", "dag_ack")[
                                    self.stage_writes
                                ]
                            )
                            self.stage_writes += 1
                self.writes += 1

            def read_frame(self):
                self.reads += 1
                if self.reads == 1:
                    return self.raw
                if self.wrong_final:
                    return b"{}\n"
                return canonical_json_bytes(
                    {
                        "schema_version": "clh.windows-local-checkpoint-ack.v1",
                        "stage": self.last_stage,
                        "handoff_sha256": sha256_bytes(self.raw),
                    }
                )

        good = Client(canonical_json_bytes(response))
        with (
            mock.patch.object(WindowsPipe, "client", return_value=good),
            mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
            mock.patch(
                "coordination_loop_harness.windows_capability.require_native_client_deadline"
            ),
            mock.patch("coordination_loop_harness.windows_capability.require_client_boot_deadline"),
            mock.patch(
                "coordination_loop_harness.windows_capability.WindowsWriterInspector"
            ) as inspector,
        ):
            with request_source_handoff(
                NAME,
                issuer_pin=SERVER,
                client_pin=PIN,
                request=request,
                worker_sid=binding.worker_sid,
                scratch_sid=binding.scratch_sid,
                deadline=deadline,
            ) as session:
                self.assertEqual(session.response, response)
                self.assertFalse(good.closed)
                session.pre_spawn_watchdog()
                session.bind(binding, participant_handle=1, job_handle=2)
                for stage in ("resume", "effect_publication", "receipt", "dag_ack"):
                    session.checkpoint(stage, participant_handle=1, job_handle=2)
            self.assertEqual(inspector.return_value.observe.call_count, 5)
        self.assertTrue(good.closed and good.authenticated)
        self.assertEqual((good.reads, good.writes), (7, 8))
        oversized = Client(canonical_json_bytes(response))
        with (
            mock.patch.object(WindowsPipe, "client", return_value=oversized),
            mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
            mock.patch(
                "coordination_loop_harness.windows_capability.require_native_client_deadline"
            ),
            mock.patch("coordination_loop_harness.windows_capability.require_client_boot_deadline"),
            mock.patch(
                "coordination_loop_harness.windows_capability.WindowsWriterInspector"
            ) as native,
        ):
            session = request_source_handoff(
                NAME,
                issuer_pin=SERVER,
                client_pin=PIN,
                request=request,
                worker_sid=binding.worker_sid,
                scratch_sid=binding.scratch_sid,
                deadline=deadline,
            )
            with self.assertRaisesRegex(WindowsBoundaryHold, "NATIVE_HANDLE_RANGE_INVALID"):
                session.bind(binding, participant_handle=2**64, job_handle=2)
            native.assert_not_called()
            self.assertTrue(oversized.closed)
        out_of_order = Client(canonical_json_bytes(response))
        with (
            mock.patch.object(WindowsPipe, "client", return_value=out_of_order),
            mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
            mock.patch(
                "coordination_loop_harness.windows_capability.require_native_client_deadline"
            ),
            mock.patch("coordination_loop_harness.windows_capability.require_client_boot_deadline"),
        ):
            session = request_source_handoff(
                NAME,
                issuer_pin=SERVER,
                client_pin=PIN,
                request=request,
                worker_sid=binding.worker_sid,
                scratch_sid=binding.scratch_sid,
                deadline=deadline,
            )
            with self.assertRaisesRegex(WindowsBoundaryHold, "CHECKPOINT_ORDER"):
                session.checkpoint("receipt", participant_handle=1, job_handle=2)
            self.assertTrue(out_of_order.closed)
            with self.assertRaisesRegex(WindowsBoundaryHold, "CHECKPOINT_ORDER"):
                session.checkpoint("resume", participant_handle=1, job_handle=2)
            with self.assertRaises(LocalTimeHold):
                deadline.check("watchdog", retained_intent=deadline.intent)
        for change in (
            {"issuer_pid": 99},
            {"generation": True},
            {"deadline_interrupt": "6001"},
            {"custody_resolved": True},
        ):
            with self.subTest(change=change):
                bad = Client(canonical_json_bytes(response | change))
                with (
                    mock.patch.object(WindowsPipe, "client", return_value=bad),
                    mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
                    mock.patch(
                        "coordination_loop_harness.windows_capability.require_native_client_deadline"
                    ),
                    mock.patch(
                        "coordination_loop_harness.windows_capability.require_client_boot_deadline"
                    ),
                ):
                    with self.assertRaises(WindowsBoundaryHold):
                        request_source_handoff(
                            NAME,
                            issuer_pin=SERVER,
                            client_pin=PIN,
                            request=request,
                            worker_sid=binding.worker_sid,
                            scratch_sid=binding.scratch_sid,
                            deadline=deadline,
                        )
                self.assertTrue(bad.closed)
        malformed = Client(json.dumps(response).encode())
        with (
            mock.patch.object(WindowsPipe, "client", return_value=malformed),
            mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
            mock.patch(
                "coordination_loop_harness.windows_capability.require_native_client_deadline"
            ),
            mock.patch("coordination_loop_harness.windows_capability.require_client_boot_deadline"),
        ):
            with self.assertRaises(WindowsBoundaryHold):
                request_source_handoff(
                    NAME,
                    issuer_pin=SERVER,
                    client_pin=PIN,
                    request=request,
                    worker_sid=binding.worker_sid,
                    scratch_sid=binding.scratch_sid,
                    deadline=deadline,
                )
        lost_final = Client(canonical_json_bytes(response), wrong_final=True)
        with (
            mock.patch.object(WindowsPipe, "client", return_value=lost_final),
            mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
            mock.patch(
                "coordination_loop_harness.windows_capability.require_native_client_deadline"
            ),
            mock.patch("coordination_loop_harness.windows_capability.require_client_boot_deadline"),
            mock.patch("coordination_loop_harness.windows_capability.WindowsWriterInspector"),
        ):
            with (
                request_source_handoff(
                    NAME,
                    issuer_pin=SERVER,
                    client_pin=PIN,
                    request=request,
                    worker_sid=binding.worker_sid,
                    scratch_sid=binding.scratch_sid,
                    deadline=deadline,
                ) as session,
                self.assertRaisesRegex(WindowsBoundaryHold, "BIND_ACK_MISMATCH"),
            ):
                session.bind(binding, participant_handle=1, job_handle=2)
                session.checkpoint("resume", participant_handle=1, job_handle=2)

    def test_native_deadline_decimal_range_and_invalid_forms(self):
        self.assertEqual(parse_interrupt_deadline(str(2**64 - 1)), 2**64 - 1)
        for value in (True, 1.0, 1, "00", "01", "-1", "+1", "0", str(2**64), "9" * 5000):
            with self.subTest(value=value), self.assertRaises(WindowsBoundaryHold):
                parse_interrupt_deadline(value)

    def test_native_handle_wire_rejects_lossy_and_invalid_values(self):
        self.assertEqual(parse_native_handle("123"), 123)
        for value in (True, 1, 1.0, "0", "01", "-1", "+1", str(2**64), "9" * 100):
            with self.subTest(value=value), self.assertRaises(WindowsBoundaryHold):
                parse_native_handle(value)

    def test_pin_and_name_validation_rejects_bool_and_aliases(self):
        for pid, creation in ((True, 1), (1, False), (0, 1), (1, 0), (1, 2**64)):
            with self.subTest(pid=pid, creation=creation), self.assertRaises(WindowsBoundaryHold):
                ProcessPin(pid, creation, SYSTEM)
        for name in (r"\\server\pipe\CoordinationLoop-x", NAME + r"\extra", NAME + "?"):
            with self.subTest(name=name), self.assertRaises(WindowsBoundaryHold):
                WindowsPipe._name(name)


if __name__ == "__main__":
    unittest.main()
