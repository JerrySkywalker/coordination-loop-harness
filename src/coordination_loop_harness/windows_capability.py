"""One local, single-connection Windows pipe for an authenticated source handoff.

This is an opt-in native primitive, never used by CLI/MCP.  A frame is evidence
only while the caller holds this pipe and has verified the other process against
Operator startup pins.  Serialized frames are never bearer capabilities.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import threading
import time
from contextlib import ExitStack, contextmanager
from ctypes import wintypes as w
from dataclasses import dataclass

from .local_time import LocalDeadline, WindowsHostClock
from .util import canonical_json_bytes, sha256_bytes
from .windows_custody import SYSTEM, check_observer
from .windows_publication import RESULT_CLAIM, RESULT_HANDOFF, RESULT_LIMIT, RESULT_PATH
from .windows_security import WindowsBoundaryHold, WindowsSecurityReader, is_account_sid
from .windows_writer import WindowsWriterBinding, WindowsWriterInspector

MAX_FRAME = 65536
IO_WAIT_MS = 1000
FRAME_WAIT_SECONDS = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value
_abandoned_io: list[tuple[object, object, object, object]] = []
TARGET_KEYS = frozenset(
    {"repository", "canonical_path", "worktree_root", "branch_ref", "exact_sha"}
)
CLAIM_KEYS = frozenset(
    {
        "schema_version",
        "intent_id",
        "generation",
        "lease_id",
        "lease_sha256",
        "boot_id",
        "deadline_interrupt",
        "target",
        "worker_sid",
        "scratch_sid",
    }
)
HANDOFF_KEYS = frozenset(
    {
        "schema_version",
        "request_sha256",
        "decision_ref",
        "decision_sha256",
        "issuer_pid",
        "issuer_creation",
        "client_pid",
        "client_creation",
        "boot_id",
        "deadline_interrupt",
        "lease_sha256",
        "generation",
        "target",
        "custody_resolved",
        "verification",
        "runtime_gate",
    }
)
CHECKPOINT_ORDER = ("resume", "effect_publication", "receipt", "dag_ack")
UINT64_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def parse_interrupt_deadline(value: object) -> int:
    if type(value) is not str or len(value) > 20 or not UINT64_DECIMAL.fullmatch(value):
        raise WindowsBoundaryHold("HOLD_NATIVE_DEADLINE_ENCODING_INVALID")
    number = int(value)
    if not 0 < number < 2**64:
        raise WindowsBoundaryHold("HOLD_NATIVE_DEADLINE_RANGE_INVALID")
    return number


def binding_wire(binding: WindowsWriterBinding) -> dict:
    return binding.__dict__ | {"participant_creation": str(binding.participant_creation)}


def parse_native_handle(value: object) -> int:
    if type(value) is not str or not UINT64_DECIMAL.fullmatch(value) or len(value) > 20:
        raise WindowsBoundaryHold("HOLD_NATIVE_HANDLE_ENCODING_INVALID")
    number = int(value)
    if not 0 < number < 2 ** (ctypes.sizeof(w.HANDLE) * 8) or number == INVALID_HANDLE:
        raise WindowsBoundaryHold("HOLD_NATIVE_HANDLE_RANGE_INVALID")
    return number


@dataclass(frozen=True)
class ProcessPin:
    """Trusted startup pin; a claim cannot select or replace this identity."""

    pid: int
    creation: int
    sid: str

    def __post_init__(self) -> None:
        if (
            type(self.pid) is not int
            or self.pid < 1
            or type(self.creation) is not int
            or not 0 < self.creation < 2**64
            or not isinstance(self.sid, str)
            or not self.sid
        ):
            raise WindowsBoundaryHold("HOLD_PROCESS_PIN_INVALID")


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", w.DWORD), ("descriptor", w.LPVOID), ("inherit", w.BOOL)]


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("internal", ctypes.c_size_t),
        ("internal_high", ctypes.c_size_t),
        ("offset", w.DWORD),
        ("offset_high", w.DWORD),
        ("event", w.HANDLE),
    ]


def _filetime(value: w.FILETIME) -> int:
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


class WindowsPipe:
    """Owned first-instance endpoint; no listener loop, daemon, or Worker launch."""

    def __init__(self, handle: int, *, server: bool, name: str) -> None:
        if os.name != "nt" or type(handle) is not int or handle in (0, INVALID_HANDLE):
            raise WindowsBoundaryHold("HOLD_NATIVE_PIPE_HANDLE_REQUIRED")
        self.handle, self.server, self.name = handle, server, name
        self._closed = False
        self._peer_process = None
        self._io_lock = threading.RLock()
        self._reader = WindowsSecurityReader()
        self._k = self._reader.k
        for function, args, result in (
            ("GetNamedPipeClientProcessId", [w.HANDLE, ctypes.POINTER(w.ULONG)], w.BOOL),
            ("GetNamedPipeServerProcessId", [w.HANDLE, ctypes.POINTER(w.ULONG)], w.BOOL),
            ("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            ("GetProcessTimes", [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
            ("GetProcessId", [w.HANDLE], w.DWORD),
            ("CreateEventW", [w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR], w.HANDLE),
            ("WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD),
            ("CancelIoEx", [w.HANDLE, ctypes.POINTER(_Overlapped)], w.BOOL),
            (
                "GetOverlappedResult",
                [w.HANDLE, ctypes.POINTER(_Overlapped), ctypes.POINTER(w.DWORD), w.BOOL],
                w.BOOL,
            ),
            (
                "ReadFile",
                [w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(_Overlapped)],
                w.BOOL,
            ),
            (
                "WriteFile",
                [w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(_Overlapped)],
                w.BOOL,
            ),
            ("ConnectNamedPipe", [w.HANDLE, ctypes.POINTER(_Overlapped)], w.BOOL),
        ):
            native = getattr(self._k, function)
            native.argtypes, native.restype = args, result

    @staticmethod
    def _name(name: str) -> str:
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"\\\\\.\\pipe\\CoordinationLoop-[A-Za-z0-9_-]+", name)
            or len(name) > 200
        ):
            raise WindowsBoundaryHold("HOLD_FIXED_LOCAL_PIPE_NAME_REQUIRED")
        return name

    @classmethod
    def first_server(cls, name: str, *, client_sid: str) -> WindowsPipe:
        """Explicit Operator action: one local first instance with a protected DACL."""
        name = cls._name(name)
        if os.name != "nt" or not is_account_sid(client_sid):
            raise WindowsBoundaryHold("HOLD_FIXED_CLIENT_SID_REQUIRED")
        k, a = (
            ctypes.WinDLL("kernel32", use_last_error=True),
            ctypes.WinDLL("advapi32", use_last_error=True),
        )
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            w.LPCWSTR,
            w.DWORD,
            ctypes.POINTER(w.LPVOID),
            ctypes.POINTER(w.DWORD),
        ]
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
        k.CreateNamedPipeW.argtypes = [
            w.LPCWSTR,
            w.DWORD,
            w.DWORD,
            w.DWORD,
            w.DWORD,
            w.DWORD,
            w.DWORD,
            ctypes.POINTER(_SecurityAttributes),
        ]
        k.CreateNamedPipeW.restype = w.HANDLE
        k.CloseHandle.argtypes, k.CloseHandle.restype = [w.HANDLE], w.BOOL
        k.LocalFree.argtypes, k.LocalFree.restype = [w.HANDLE], w.HANDLE
        descriptor = w.LPVOID()
        # Client may read/write data but cannot create another pipe instance
        # (FILE_APPEND_DATA / FILE_CREATE_PIPE_INSTANCE is deliberately absent).
        sddl = f"O:SYG:SYD:P(A;;GA;;;SY)(A;;0x00100083;;;{client_sid})"
        if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None
        ):
            raise WindowsBoundaryHold("HOLD_PIPE_DESCRIPTOR_UNAVAILABLE")
        try:
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            # DUPLEX, OVERLAPPED, FIRST_PIPE_INSTANCE; byte pipe, local clients only.
            handle = k.CreateNamedPipeW(
                name,
                0x00000003 | 0x40000000 | 0x00080000,
                0x00000008,
                1,
                MAX_FRAME + 4,
                MAX_FRAME + 4,
                IO_WAIT_MS,
                ctypes.byref(attributes),
            )
            if handle in (None, 0, INVALID_HANDLE):
                raise WindowsBoundaryHold("HOLD_FIRST_PIPE_INSTANCE_UNAVAILABLE")
            try:
                return cls(int(handle), server=True, name=name)
            except Exception:
                k.CloseHandle(handle)
                raise
        finally:
            k.LocalFree(descriptor)

    @classmethod
    def client(cls, name: str) -> WindowsPipe:
        """Future CLF caller still must independently pin and verify the server."""
        name = cls._name(name)
        if os.name != "nt":
            raise WindowsBoundaryHold("HOLD_WINDOWS_PIPE_UNAVAILABLE")
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
        k.CreateFileW.restype = w.HANDLE
        k.CloseHandle.argtypes, k.CloseHandle.restype = [w.HANDLE], w.BOOL
        # Explicit rights omit FILE_CREATE_PIPE_INSTANCE; identification SQOS.
        handle = k.CreateFileW(name, 0x00100083, 0, None, 3, 0x40000000 | 0x00110000, None)
        if handle in (None, 0, INVALID_HANDLE):
            raise WindowsBoundaryHold("HOLD_PIPE_CLIENT_CONNECTION_UNAVAILABLE")
        try:
            return cls(int(handle), server=False, name=name)
        except Exception:
            k.CloseHandle(handle)
            raise

    def close(self) -> None:
        with self._io_lock:
            if not self._closed:
                self._closed = True
                self._k.CloseHandle(self.handle)

    def __enter__(self) -> WindowsPipe:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _operation(self, function: str, buffer: object | None = None, size: int = 0) -> int:
        """One bounded overlapped operation. Uncertain cancellation closes the pipe."""
        with self._io_lock:
            return self._locked_operation(function, buffer, size)

    def _locked_operation(self, function: str, buffer: object | None, size: int) -> int:
        if self._closed:
            raise WindowsBoundaryHold("HOLD_PIPE_CLOSED")
        if len(_abandoned_io) >= 8:
            self.close()
            raise WindowsBoundaryHold("HOLD_PIPE_CANCELLATION_BACKLOG")
        event = self._k.CreateEventW(None, True, False, None)
        if not event:
            self.close()
            raise WindowsBoundaryHold("HOLD_PIPE_EVENT_UNAVAILABLE")
        overlap = _Overlapped()
        overlap.event = event
        done = w.DWORD()
        completion_uncertain = False
        try:
            if function == "ConnectNamedPipe":
                ok = self._k.ConnectNamedPipe(self.handle, ctypes.byref(overlap))
            else:
                ok = getattr(self._k, function)(
                    self.handle, buffer, size, ctypes.byref(done), ctypes.byref(overlap)
                )
            if not ok:
                error = ctypes.get_last_error()
                if function == "ConnectNamedPipe" and error == 535:  # ERROR_PIPE_CONNECTED
                    return 0
                if error != 997:  # ERROR_IO_PENDING
                    raise WindowsBoundaryHold("HOLD_PIPE_IO_FAILED")
                if self._k.WaitForSingleObject(event, IO_WAIT_MS) != 0:
                    self._k.CancelIoEx(self.handle, ctypes.byref(overlap))
                    self.close()
                    # Do not free an OVERLAPPED while its completion is uncertain.
                    if self._k.WaitForSingleObject(event, IO_WAIT_MS) != 0:
                        completion_uncertain = True
                        _abandoned_io.append((overlap, event, buffer, done))
                        raise WindowsBoundaryHold("HOLD_PIPE_CANCELLATION_UNCERTAIN")
                    raise WindowsBoundaryHold("HOLD_PIPE_IO_TIMEOUT")
                if not self._k.GetOverlappedResult(
                    self.handle, ctypes.byref(overlap), ctypes.byref(done), False
                ):
                    raise WindowsBoundaryHold("HOLD_PIPE_IO_FAILED")
            return done.value
        except Exception:
            self.close()
            raise
        finally:
            if not completion_uncertain:
                self._k.CloseHandle(event)

    def connect(self) -> None:
        if not self.server:
            raise WindowsBoundaryHold("HOLD_SERVER_HANDLE_REQUIRED")
        self._operation("ConnectNamedPipe")

    def require_server_self(self, pin: ProcessPin) -> None:
        if not self.server:
            raise WindowsBoundaryHold("HOLD_SERVER_HANDLE_REQUIRED")
        actual = w.ULONG()
        if (
            not self._k.GetNamedPipeServerProcessId(self.handle, ctypes.byref(actual))
            or actual.value != pin.pid
        ):
            raise WindowsBoundaryHold("HOLD_PIPE_SERVER_OWNERSHIP_MISMATCH")

    def read_frame(self) -> bytes:
        cutoff = time.monotonic() + FRAME_WAIT_SECONDS

        def exact(size: int) -> bytes:
            chunks = bytearray()
            while len(chunks) < size:
                if time.monotonic() >= cutoff:
                    self.close()
                    raise WindowsBoundaryHold("HOLD_PIPE_FRAME_TIMEOUT")
                buffer = ctypes.create_string_buffer(size - len(chunks))
                count = self._operation("ReadFile", buffer, len(buffer))
                if count < 1 or count > size - len(chunks):
                    raise WindowsBoundaryHold("HOLD_PIPE_DISCONNECTED")
                chunks.extend(buffer.raw[:count])
            return bytes(chunks)

        length = int.from_bytes(exact(4), "big")
        if not 0 < length <= MAX_FRAME:
            self.close()
            raise WindowsBoundaryHold("HOLD_PIPE_FRAME_BOUND")
        return exact(length)

    def write_frame(self, data: bytes) -> None:
        if type(data) is not bytes or not 0 < len(data) <= MAX_FRAME:
            raise WindowsBoundaryHold("HOLD_PIPE_FRAME_BOUND")
        frame = len(data).to_bytes(4, "big") + data
        offset = 0
        cutoff = time.monotonic() + FRAME_WAIT_SECONDS
        while offset < len(frame):
            if time.monotonic() >= cutoff:
                self.close()
                raise WindowsBoundaryHold("HOLD_PIPE_FRAME_TIMEOUT")
            chunk = ctypes.create_string_buffer(frame[offset:])
            count = self._operation("WriteFile", chunk, len(frame) - offset)
            if count < 1:
                self.close()
                raise WindowsBoundaryHold("HOLD_PIPE_DISCONNECTED")
            offset += count

    @contextmanager
    def authenticated_peer(self, pin: ProcessPin):
        with self._io_lock:
            with self._authenticated_peer_locked(pin):
                yield

    @contextmanager
    def _authenticated_peer_locked(self, pin: ProcessPin):
        """Keep a process handle across IO and recheck kernel pipe association."""
        if self._closed:
            raise WindowsBoundaryHold("HOLD_PIPE_CLOSED")
        pid = w.ULONG()
        query = (
            self._k.GetNamedPipeClientProcessId
            if self.server
            else self._k.GetNamedPipeServerProcessId
        )
        if not query(self.handle, ctypes.byref(pid)) or pid.value != pin.pid:
            raise WindowsBoundaryHold("HOLD_PIPE_PEER_PID_MISMATCH")
        # QUERY_LIMITED_INFORMATION | SYNCHRONIZE for the liveness waits.
        # Only the producer also needs DUP_HANDLE on the pinned client.
        access = 0x1000 | 0x00100000 | (0x0040 if self.server else 0)
        process = self._k.OpenProcess(access, False, pid.value)
        if not process:
            raise WindowsBoundaryHold("HOLD_PIPE_PEER_PROCESS_UNAVAILABLE")
        try:
            created, exited, kernel, user = (w.FILETIME() for _ in range(4))
            if (
                self._k.GetProcessId(process) != pin.pid
                or not self._k.GetProcessTimes(
                    process,
                    ctypes.byref(created),
                    ctypes.byref(exited),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                )
                or _filetime(created) != pin.creation
            ):
                raise WindowsBoundaryHold("HOLD_PIPE_PEER_CREATION_MISMATCH")
            token = self._reader.token_security(process)
            if token.user != pin.sid or token.token_type != 1:
                raise WindowsBoundaryHold("HOLD_PIPE_PEER_PRIMARY_TOKEN_MISMATCH")
            if self.server:
                check_observer(token, SYSTEM)
            if self._k.WaitForSingleObject(process, 0) != 258:  # WAIT_TIMEOUT: still running
                raise WindowsBoundaryHold("HOLD_PIPE_PEER_NOT_RUNNING")
            self._peer_process = process if self.server else None
            yield
            after = w.ULONG()
            if (
                not query(self.handle, ctypes.byref(after))
                or after.value != pin.pid
                or self._k.WaitForSingleObject(process, 0) != 258
            ):
                raise WindowsBoundaryHold("HOLD_PIPE_PEER_CHANGED")
        finally:
            self._peer_process = None
            self._k.CloseHandle(process)

    @contextmanager
    def duplicate_peer_query_handles(self, participant: int, job: int):
        """Copy only query rights from the authenticated, retained client."""
        if not self.server or self._closed or not getattr(self, "_peer_process", None):
            raise WindowsBoundaryHold("HOLD_AUTHENTICATED_CLIENT_HANDLE_TABLE_REQUIRED")
        self._k.DuplicateHandle.argtypes = [
            w.HANDLE,
            w.HANDLE,
            w.HANDLE,
            ctypes.POINTER(w.HANDLE),
            w.DWORD,
            w.BOOL,
            w.DWORD,
        ]
        self._k.DuplicateHandle.restype = w.BOOL
        self._k.GetCurrentProcess.restype = w.HANDLE
        self._k.CloseHandle.argtypes, self._k.CloseHandle.restype = [w.HANDLE], w.BOOL
        copies = []
        try:
            for source, access in ((participant, 0x1000), (job, 0x0004)):
                duplicate = w.HANDLE()
                if (
                    not self._k.DuplicateHandle(
                        self._peer_process,
                        source,
                        self._k.GetCurrentProcess(),
                        ctypes.byref(duplicate),
                        access,
                        False,
                        0,
                    )
                    or not duplicate.value
                ):
                    raise WindowsBoundaryHold("HOLD_CLIENT_QUERY_HANDLE_DUPLICATION_FAILED")
                copies.append(int(duplicate.value))
            yield tuple(copies)
        finally:
            for handle in reversed(copies):
                self._k.CloseHandle(handle)


def require_process_self(pin: ProcessPin) -> None:
    """Native current-process primary token and creation, never payload claims."""
    if type(pin) is not ProcessPin or os.name != "nt" or pin.pid != os.getpid():
        raise WindowsBoundaryHold("HOLD_NATIVE_PROCESS_PIN_REQUIRED")
    reader = WindowsSecurityReader()
    token = reader.token_security()
    if token.user != pin.sid or token.token_type != 1:
        raise WindowsBoundaryHold("HOLD_NATIVE_PRIMARY_TOKEN_MISMATCH")
    k = reader.k
    k.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    k.GetProcessTimes.restype = w.BOOL
    created, exited, kernel, user = (w.FILETIME() for _ in range(4))
    if (
        not k.GetProcessTimes(
            k.GetCurrentProcess(),
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        or _filetime(created) != pin.creation
    ):
        raise WindowsBoundaryHold("HOLD_NATIVE_PROCESS_CREATION_MISMATCH")


def require_system_self(pin: ProcessPin) -> None:
    """The distinct issuer path permits only an actual SYSTEM primary token."""
    if type(pin) is not ProcessPin or pin.sid != SYSTEM:
        raise WindowsBoundaryHold("HOLD_NATIVE_SYSTEM_ISSUER_REQUIRED")
    require_process_self(pin)


def require_client_boot_deadline(request: dict) -> None:
    """Native client-side boot/expiry check; CLF still owns wake containment."""
    now = WindowsHostClock().read()
    if now.boot_id != request["boot_id"] or now.interrupt_after >= parse_interrupt_deadline(
        request["deadline_interrupt"]
    ):
        raise WindowsBoundaryHold("HOLD_CLIENT_BOOT_OR_DEADLINE_MISMATCH")


def require_native_client_deadline(deadline: LocalDeadline, pin: ProcessPin, request: dict) -> None:
    read = deadline._read
    if (
        type(getattr(read, "__self__", None)) is not WindowsHostClock
        or getattr(read, "__func__", None) is not WindowsHostClock.read
        or deadline.intent.anchor.process_id != f"{pin.pid}:{pin.creation}"
        or deadline.intent.anchor.boot_id != request["boot_id"]
        or deadline.intent.deadline != parse_interrupt_deadline(request["deadline_interrupt"])
    ):
        raise WindowsBoundaryHold("HOLD_NATIVE_CLIENT_DEADLINE_REQUIRED")


class WindowsHandoffSession:
    """Live, single-use client channel; metadata alone cannot advance a stage."""

    def __init__(
        self,
        stack: ExitStack,
        pipe: WindowsPipe,
        response: dict,
        request: dict,
        client_pin: ProcessPin,
        worker_sid: str,
        scratch_sid: str,
        deadline: LocalDeadline,
    ) -> None:
        self._stack, self._pipe = stack, pipe
        self.response = json.loads(canonical_json_bytes(response))
        self._request = json.loads(canonical_json_bytes(request))
        self._handoff_digest = sha256_bytes(canonical_json_bytes(response))
        self._client_pin, self._binding = client_pin, None
        self._worker_sid, self._scratch_sid = worker_sid, scratch_sid
        self._client_deadline = deadline
        self._owner_thread = threading.get_ident()
        self._next = 0
        self._bound_handles = None
        self._closed = False
        self._result_contract = request["schema_version"] == RESULT_CLAIM
        self._published_result = None

    def close(self) -> None:
        if threading.get_ident() != self._owner_thread:
            self._client_deadline.invalidate("HOLD_HANDOFF_SESSION_THREAD_CHANGED")
            raise WindowsBoundaryHold("HOLD_HANDOFF_SESSION_THREAD_CHANGED")
        if not self._closed:
            self._closed = True
            if self._next < len(CHECKPOINT_ORDER):
                self._client_deadline.invalidate("HOLD_HANDOFF_SESSION_LOST")
            self._stack.close()

    def __enter__(self) -> WindowsHandoffSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def pre_spawn_watchdog(self) -> None:
        if threading.get_ident() != self._owner_thread or self._closed or self._binding is not None:
            self.close()
            raise WindowsBoundaryHold("HOLD_HANDOFF_CHECKPOINT_ORDER")
        try:
            require_process_self(self._client_pin)
            require_client_boot_deadline(self._request)
            self._pipe.write_frame(
                canonical_json_bytes(
                    {
                        "schema_version": "clh.windows-local-checkpoint.v1",
                        "stage": "pre_spawn_watchdog",
                        "handoff_sha256": self._handoff_digest,
                    }
                )
            )
            expected = canonical_json_bytes(
                {
                    "schema_version": "clh.windows-local-checkpoint-ack.v1",
                    "stage": "pre_spawn_watchdog",
                    "handoff_sha256": self._handoff_digest,
                }
            )
            if self._pipe.read_frame() != expected:
                raise WindowsBoundaryHold("HOLD_HANDOFF_CHECKPOINT_ACK_MISMATCH")
            require_client_boot_deadline(self._request)
        except Exception:
            self.close()
            raise

    def bind(
        self, binding: WindowsWriterBinding, *, participant_handle: int, job_handle: int
    ) -> dict:
        """CLF calls after suspended creation and Job assignment, before resume."""
        if threading.get_ident() != self._owner_thread or self._closed or self._binding is not None:
            self.close()
            raise WindowsBoundaryHold("HOLD_HANDOFF_BIND_ORDER")
        try:
            if (
                type(binding) is not WindowsWriterBinding
                or type(participant_handle) is not int
                or type(job_handle) is not int
                or (binding.issuer_sid, binding.worker_sid, binding.scratch_sid)
                != (SYSTEM, self._worker_sid, self._scratch_sid)
                or (binding.intent_id, binding.generation, binding.lease_sha256)
                != (
                    self._request["intent_id"],
                    self._request["generation"],
                    self._request["lease_sha256"],
                )
            ):
                raise WindowsBoundaryHold("HOLD_FIXED_WRITER_BINDING_REQUIRED")
            participant_native = parse_native_handle(str(participant_handle))
            job_native = parse_native_handle(str(job_handle))
            require_process_self(self._client_pin)
            require_client_boot_deadline(self._request)
            snapshot = WindowsWriterInspector(binding, self._client_deadline).observe(
                participant_handle=participant_native, job_handle=job_native
            )
            self._pipe.write_frame(
                canonical_json_bytes(
                    {
                        "schema_version": "clh.windows-local-bind.v1",
                        "handoff_sha256": self._handoff_digest,
                        "binding": binding_wire(binding),
                        "participant_handle": str(participant_native),
                        "job_handle": str(job_native),
                    }
                )
            )
            expected = canonical_json_bytes(
                {
                    "schema_version": "clh.windows-local-checkpoint-ack.v1",
                    "stage": "bind",
                    "handoff_sha256": self._handoff_digest,
                }
            )
            if self._pipe.read_frame() != expected:
                raise WindowsBoundaryHold("HOLD_HANDOFF_BIND_ACK_MISMATCH")
            require_client_boot_deadline(self._request)
            self._binding = binding
            self._bound_handles = (participant_handle, job_handle)
            return snapshot
        except Exception:
            self.close()
            raise

    def checkpoint(self, stage: str, *, participant_handle: int, job_handle: int) -> dict:
        if threading.get_ident() != self._owner_thread:
            self._client_deadline.invalidate("HOLD_HANDOFF_SESSION_THREAD_CHANGED")
            raise WindowsBoundaryHold("HOLD_HANDOFF_SESSION_THREAD_CHANGED")
        if (
            self._closed
            or self._binding is None
            or type(participant_handle) is not int
            or type(job_handle) is not int
            or (participant_handle, job_handle) != self._bound_handles
            or self._next >= len(CHECKPOINT_ORDER)
            or stage not in ("watchdog", CHECKPOINT_ORDER[self._next])
        ):
            self.close()
            raise WindowsBoundaryHold("HOLD_HANDOFF_CHECKPOINT_ORDER")
        try:
            require_process_self(self._client_pin)
            require_client_boot_deadline(self._request)
            # Native owned-handle observation is a source snapshot, not a runtime grant.
            observation = dict(participant_handle=participant_handle, job_handle=job_handle)
            post_effect = self._result_contract and self._next >= 2
            if self._result_contract and (post_effect or stage == "effect_publication"):
                observation["terminal"] = True
            snapshot = WindowsWriterInspector(self._binding, self._client_deadline).observe(
                **observation
            )
            self._pipe.write_frame(
                canonical_json_bytes(
                    {
                        "schema_version": "clh.windows-local-checkpoint.v1",
                        "stage": stage,
                        "handoff_sha256": self._handoff_digest,
                        "binding": binding_wire(self._binding),
                    }
                )
            )
            expected = {
                "schema_version": "clh.windows-local-checkpoint-ack.v1",
                "stage": stage,
                "handoff_sha256": self._handoff_digest,
            }
            raw_ack = self._pipe.read_frame()
            if post_effect:
                try:
                    ack = json.loads(raw_ack)
                    result = ack["published_result"]
                    if (
                        type(result) is not dict
                        or set(result) != {"path", "size", "sha256"}
                        or result["path"] != RESULT_PATH
                        or type(result["size"]) is not int
                        or not 0 <= result["size"] <= RESULT_LIMIT
                        or type(result["sha256"]) is not str
                        or re.fullmatch("[0-9a-f]{64}", result["sha256"]) is None
                        or (self._published_result is not None and result != self._published_result)
                    ):
                        raise ValueError("invalid or changed fixed-result observation")
                    expected["published_result"] = result
                except (ValueError, KeyError, TypeError) as exc:
                    raise WindowsBoundaryHold("HOLD_RESULT_ACK_MISMATCH") from exc
            if raw_ack != canonical_json_bytes(expected):
                raise WindowsBoundaryHold("HOLD_HANDOFF_CHECKPOINT_ACK_MISMATCH")
            require_client_boot_deadline(self._request)
            if post_effect:
                self._published_result = dict(result)
                snapshot["published_result"] = dict(result)
            if stage != "watchdog":
                self._next += 1
            if stage == "dag_ack":
                self.close()
            return snapshot
        except Exception:
            self.close()
            raise


def request_source_handoff(
    name: str,
    *,
    issuer_pin: ProcessPin,
    client_pin: ProcessPin,
    request: dict,
    worker_sid: str,
    scratch_sid: str,
    deadline: object,
) -> WindowsHandoffSession:
    """Future CLF-side primitive: authenticate issuer on the connected pipe.

    The returned metadata is not a transferable authorization or runtime PASS.
    The caller must obtain issuer_pin and the expected request from its own
    trusted Operator startup channel, never from this response or a Worker.
    """
    if (
        type(issuer_pin) is not ProcessPin
        or type(client_pin) is not ProcessPin
        or issuer_pin.sid != SYSTEM
        or not is_account_sid(client_pin.sid)
        or type(request) is not dict
        or set(request) != CLAIM_KEYS
        or request.get("schema_version") not in ("clh.windows-local-claim.v1", RESULT_CLAIM)
        or any(
            type(request.get(key)) is not str or not request[key]
            for key in ("intent_id", "lease_id", "lease_sha256", "boot_id")
        )
        or not re.fullmatch("[0-9a-f]{64}", request["lease_sha256"])
        or type(request.get("generation")) is not int
        or request["generation"] < 1
        or type(request.get("deadline_interrupt")) is not str
        or type(request.get("target")) is not dict
        or request.get("worker_sid") != worker_sid
        or request.get("scratch_sid") != scratch_sid
        or set(request["target"]) != TARGET_KEYS
        or any(type(value) is not str or not value for value in request["target"].values())
    ):
        raise WindowsBoundaryHold("HOLD_TRUSTED_SERVER_PIN_AND_REQUEST_REQUIRED")
    parse_interrupt_deadline(request["deadline_interrupt"])
    if (
        not is_account_sid(worker_sid)
        or not is_account_sid(scratch_sid)
        or worker_sid == scratch_sid
        or client_pin.sid in (worker_sid, scratch_sid)
        or type(deadline) is not LocalDeadline
        or (deadline.intent.intent_id, deadline.intent.generation, deadline.intent.lease_sha256)
        != (request["intent_id"], request["generation"], request["lease_sha256"])
    ):
        raise WindowsBoundaryHold("HOLD_FIXED_WRITER_BINDING_REQUIRED")
    require_process_self(client_pin)
    require_native_client_deadline(deadline, client_pin, request)
    require_client_boot_deadline(request)
    raw = canonical_json_bytes(request)
    stack = ExitStack()
    try:
        pipe = stack.enter_context(WindowsPipe.client(name))
        stack.enter_context(pipe.authenticated_peer(issuer_pin))
        pipe.write_frame(raw)
        response_raw = pipe.read_frame()
        try:
            response = json.loads(response_raw)
            canonical_response = canonical_json_bytes(response)
        except (ValueError, UnicodeError, TypeError) as exc:
            raise WindowsBoundaryHold("HOLD_HANDOFF_FRAME_INVALID") from exc
        if (
            response_raw != canonical_response
            or type(response) is not dict
            or set(response) != HANDOFF_KEYS
            or response.get("schema_version") != (
                RESULT_HANDOFF if request["schema_version"] == RESULT_CLAIM
                else "clh.windows-local-handoff.v1"
            )
            or response.get("request_sha256") != sha256_bytes(raw)
            or type(response.get("decision_ref")) is not str
            or not response["decision_ref"]
            or type(response.get("decision_sha256")) is not str
            or not re.fullmatch("[0-9a-f]{64}", response["decision_sha256"])
            or type(response.get("issuer_pid")) is not int
            or response.get("issuer_pid") != issuer_pin.pid
            or type(response.get("issuer_creation")) is not str
            or response.get("issuer_creation") != str(issuer_pin.creation)
            or type(response.get("client_pid")) is not int
            or response["client_pid"] != client_pin.pid
            or type(response.get("client_creation")) is not str
            or response["client_creation"] != str(client_pin.creation)
            or response.get("boot_id") != request.get("boot_id")
            or type(response.get("deadline_interrupt")) is not str
            or response["deadline_interrupt"] != request.get("deadline_interrupt")
            or response.get("lease_sha256") != request.get("lease_sha256")
            or type(response.get("generation")) is not int
            or response["generation"] != request.get("generation")
            or response.get("target") != request.get("target")
            or response.get("custody_resolved") is not False
            or response.get("verification") != "WINDOWS_CLH_SOURCE_SNAPSHOT_ONLY"
            or response.get("runtime_gate") != "G_L1_ROOT_AND_CONSUMER_BOUNDARY_UNQUALIFIED"
        ):
            raise WindowsBoundaryHold("HOLD_HANDOFF_BINDING_MISMATCH")
        require_process_self(client_pin)
        require_client_boot_deadline(request)
        digest = sha256_bytes(response_raw)
        pipe.write_frame(
            canonical_json_bytes(
                {"schema_version": "clh.windows-local-ack.v1", "handoff_sha256": digest}
            )
        )
        return WindowsHandoffSession(
            stack, pipe, response, request, client_pin, worker_sid, scratch_sid, deadline
        )
    except Exception:
        stack.close()
        deadline.invalidate("HOLD_HANDOFF_SESSION_LOST")
        raise
