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
import time
from contextlib import contextmanager
from ctypes import wintypes as w
from dataclasses import dataclass

from .local_time import WindowsHostClock
from .util import canonical_json_bytes, sha256_bytes
from .windows_custody import SYSTEM
from .windows_security import WindowsBoundaryHold, WindowsSecurityReader, is_account_sid

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
            or self.creation < 1
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
        if not self._closed:
            self._closed = True
            self._k.CloseHandle(self.handle)

    def __enter__(self) -> WindowsPipe:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _operation(self, function: str, buffer: object | None = None, size: int = 0) -> int:
        """One bounded overlapped operation. Uncertain cancellation closes the pipe."""
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
        process = self._k.OpenProcess(0x1000, False, pid.value)
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
            if self._k.WaitForSingleObject(process, 0) != 258:  # WAIT_TIMEOUT: still running
                raise WindowsBoundaryHold("HOLD_PIPE_PEER_NOT_RUNNING")
            yield
            after = w.ULONG()
            if (
                not query(self.handle, ctypes.byref(after))
                or after.value != pin.pid
                or self._k.WaitForSingleObject(process, 0) != 258
            ):
                raise WindowsBoundaryHold("HOLD_PIPE_PEER_CHANGED")
        finally:
            self._k.CloseHandle(process)


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
    if now.boot_id != request["boot_id"] or now.interrupt_after >= request["deadline_interrupt"]:
        raise WindowsBoundaryHold("HOLD_CLIENT_BOOT_OR_DEADLINE_MISMATCH")


def request_source_handoff(
    name: str, *, issuer_pin: ProcessPin, client_pin: ProcessPin, request: dict
) -> dict:
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
        or request.get("schema_version") != "clh.windows-local-claim.v1"
        or any(
            type(request.get(key)) is not str or not request[key]
            for key in ("intent_id", "lease_id", "lease_sha256", "boot_id")
        )
        or not re.fullmatch("[0-9a-f]{64}", request["lease_sha256"])
        or type(request.get("generation")) is not int
        or request["generation"] < 1
        or type(request.get("deadline_interrupt")) is not int
        or request["deadline_interrupt"] < 1
        or type(request.get("target")) is not dict
        or set(request["target"]) != TARGET_KEYS
        or any(type(value) is not str or not value for value in request["target"].values())
    ):
        raise WindowsBoundaryHold("HOLD_TRUSTED_SERVER_PIN_AND_REQUEST_REQUIRED")
    require_process_self(client_pin)
    require_client_boot_deadline(request)
    raw = canonical_json_bytes(request)
    with WindowsPipe.client(name) as pipe:
        with pipe.authenticated_peer(issuer_pin):
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
                or response.get("schema_version") != "clh.windows-local-handoff.v1"
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
                or type(response.get("deadline_interrupt")) is not int
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
            if pipe.read_frame() != canonical_json_bytes(
                {"schema_version": "clh.windows-local-final.v1", "handoff_sha256": digest}
            ):
                raise WindowsBoundaryHold("HOLD_HANDOFF_FINAL_MISMATCH")
            require_process_self(client_pin)
            require_client_boot_deadline(request)
            return response
