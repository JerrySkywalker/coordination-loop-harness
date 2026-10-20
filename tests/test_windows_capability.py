"""SOURCE_TEST: mocked Windows kernel primitives, never a runtime qualification."""

import ctypes
import json
import unittest
from contextlib import nullcontext
from ctypes import wintypes as w
from types import SimpleNamespace
from unittest import mock

from coordination_loop_harness.local_time import HostSample
from coordination_loop_harness.util import canonical_json_bytes, sha256_bytes
from coordination_loop_harness.windows_capability import (
    INVALID_HANDLE,
    ProcessPin,
    WindowsPipe,
    _SecurityAttributes,
    request_source_handoff,
    require_client_boot_deadline,
)
from coordination_loop_harness.windows_custody import SYSTEM
from coordination_loop_harness.windows_security import TokenSecurity, WindowsBoundaryHold

PIN = ProcessPin(43, 133_800_000_000_000_002, "S-1-5-21-100-200-300-1001")
SERVER = ProcessPin(42, 133_800_000_000_000_001, SYSTEM)
NAME = r"\\.\pipe\CoordinationLoop-source-test"


class PeerKernelTests(unittest.TestCase):
    def pipe(self, *, pid=43, creation=PIN.creation, sid=PIN.sid, token_type=1):
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
        pipe._reader = SimpleNamespace(
            token_security=lambda _handle: TokenSecurity(sid, (), (), (), token_type)
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
        ):
            with self.subTest(change=change):
                with self.assertRaises(WindowsBoundaryHold):
                    with self.pipe(**change).authenticated_peer(PIN):
                        self.fail("wrong kernel identity entered authenticated scope")

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
                "coordination_loop_harness.windows_capability.ctypes.WinDLL",
                side_effect=lambda name, **_kwargs: k if name == "kernel32" else a,
            ),
            mock.patch.object(WindowsPipe, "__init__", return_value=None),
        ):
            WindowsPipe.first_server(NAME, client_sid=PIN.sid)
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
                "coordination_loop_harness.windows_capability.ctypes.WinDLL",
                side_effect=lambda name, **_kwargs: k if name == "kernel32" else a,
            ),
            self.assertRaisesRegex(WindowsBoundaryHold, "FIRST_PIPE_INSTANCE"),
        ):
            WindowsPipe.first_server(NAME, client_sid=PIN.sid)

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


class ClientHandoffTests(unittest.TestCase):
    def test_client_native_boot_and_deadline_check_refuses_changed_or_expired(self):
        boot = "12345678-1234-1234-1234-123456789abc"
        request = {"boot_id": boot, "deadline_interrupt": 200}
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
            "deadline_interrupt": 6000,
            "lease_sha256": "a" * 64,
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

        class Client:
            def __init__(self, raw, *, wrong_final=False):
                self.raw = raw
                self.wrong_final = wrong_final
                self.closed = False
                self.authenticated = False
                self.reads = 0
                self.writes = 0

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
                else:
                    assert raw == canonical_json_bytes(
                        {
                            "schema_version": "clh.windows-local-ack.v1",
                            "handoff_sha256": sha256_bytes(self.raw),
                        }
                    )
                self.writes += 1

            def read_frame(self):
                self.reads += 1
                if self.reads == 1:
                    return self.raw
                if self.wrong_final:
                    return b"{}\n"
                return canonical_json_bytes(
                    {
                        "schema_version": "clh.windows-local-final.v1",
                        "handoff_sha256": sha256_bytes(self.raw),
                    }
                )

        good = Client(canonical_json_bytes(response))
        with (
            mock.patch.object(WindowsPipe, "client", return_value=good),
            mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
            mock.patch("coordination_loop_harness.windows_capability.require_client_boot_deadline"),
        ):
            self.assertEqual(
                request_source_handoff(NAME, issuer_pin=SERVER, client_pin=PIN, request=request),
                response,
            )
        self.assertTrue(good.closed and good.authenticated)
        self.assertEqual((good.reads, good.writes), (2, 2))
        for change in (
            {"issuer_pid": 99},
            {"generation": True},
            {"deadline_interrupt": 6001},
            {"custody_resolved": True},
        ):
            with self.subTest(change=change):
                bad = Client(canonical_json_bytes(response | change))
                with (
                    mock.patch.object(WindowsPipe, "client", return_value=bad),
                    mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
                    mock.patch(
                        "coordination_loop_harness.windows_capability.require_client_boot_deadline"
                    ),
                ):
                    with self.assertRaises(WindowsBoundaryHold):
                        request_source_handoff(
                            NAME, issuer_pin=SERVER, client_pin=PIN, request=request
                        )
                self.assertTrue(bad.closed)
        malformed = Client(json.dumps(response).encode())
        with (
            mock.patch.object(WindowsPipe, "client", return_value=malformed),
            mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
            mock.patch("coordination_loop_harness.windows_capability.require_client_boot_deadline"),
        ):
            with self.assertRaises(WindowsBoundaryHold):
                request_source_handoff(NAME, issuer_pin=SERVER, client_pin=PIN, request=request)
        lost_final = Client(canonical_json_bytes(response), wrong_final=True)
        with (
            mock.patch.object(WindowsPipe, "client", return_value=lost_final),
            mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
            mock.patch("coordination_loop_harness.windows_capability.require_client_boot_deadline"),
        ):
            with self.assertRaisesRegex(WindowsBoundaryHold, "FINAL_MISMATCH"):
                request_source_handoff(NAME, issuer_pin=SERVER, client_pin=PIN, request=request)

    def test_pin_and_name_validation_rejects_bool_and_aliases(self):
        for pid, creation in ((True, 1), (1, False), (0, 1), (1, 0)):
            with self.subTest(pid=pid, creation=creation), self.assertRaises(WindowsBoundaryHold):
                ProcessPin(pid, creation, SYSTEM)
        for name in (r"\\server\pipe\CoordinationLoop-x", NAME + r"\extra", NAME + "?"):
            with self.subTest(name=name), self.assertRaises(WindowsBoundaryHold):
                WindowsPipe._name(name)


if __name__ == "__main__":
    unittest.main()
