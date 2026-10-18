"""Read-only Windows security observations. No token, ACL, Job or process creation.

These are snapshots for SOURCE_PREPARED custody, not effective containment proof.
Native errors, unrecognized ACEs and path ambiguity are refusals.
"""

from __future__ import annotations

import ctypes
import os
import re
from contextlib import contextmanager
from ctypes import wintypes as w
from dataclasses import dataclass
from pathlib import Path

from .util import sha256_bytes


class WindowsBoundaryHold(ValueError):
    pass


def is_account_sid(sid: str) -> bool:
    if not isinstance(sid, str):
        return False
    match = re.fullmatch(r"S-1-5-21-([1-9]\d*|0)-([1-9]\d*|0)-([1-9]\d*|0)-([1-9]\d*|0)", sid)
    return bool(match and all(int(part) < 2**32 for part in match.groups()))


@dataclass(frozen=True)
class Ace:
    kind: int
    flags: int
    mask: int
    sid: str


@dataclass(frozen=True)
class FileSecurity:
    owner: str
    protected_dacl: bool
    aces: tuple[Ace, ...]
    identity: tuple
    descriptor_sha256: str


@dataclass(frozen=True)
class TokenSecurity:
    user: str
    grant_capable_groups: tuple[str, ...]
    restricting_sids: tuple[str, ...]
    privileges: tuple[str, ...]
    token_type: int = 1


class _SidAttributes(ctypes.Structure):
    _fields_ = [("Sid", w.LPVOID), ("Attributes", w.DWORD)]


class _Luid(ctypes.Structure):
    _fields_ = [("LowPart", w.DWORD), ("HighPart", w.LONG)]


class _LuidAttributes(ctypes.Structure):
    _fields_ = [("Luid", _Luid), ("Attributes", w.DWORD)]


class _FileInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", w.DWORD),
        ("creation", w.FILETIME),
        ("access", w.FILETIME),
        ("write", w.FILETIME),
        ("volume", w.DWORD),
        ("size_high", w.DWORD),
        ("size_low", w.DWORD),
        ("links", w.DWORD),
        ("index_high", w.DWORD),
        ("index_low", w.DWORD),
    ]


class WindowsSecurityReader:
    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowsBoundaryHold("HOLD_WINDOWS_SECURITY_BACKEND_UNAVAILABLE")
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.a = ctypes.WinDLL("advapi32", use_last_error=True)
        signatures = (
            (self.k, "CloseHandle", [w.HANDLE], w.BOOL),
            (self.k, "LocalFree", [w.HLOCAL], w.HLOCAL),
            (self.k, "GetCurrentProcess", [], w.HANDLE),
            (
                self.k,
                "CreateFileW",
                [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE],
                w.HANDLE,
            ),
            (self.k, "GetFileInformationByHandle", [w.HANDLE, ctypes.POINTER(_FileInfo)], w.BOOL),
            (self.k, "GetFinalPathNameByHandleW", [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD], w.DWORD),
            (self.k, "GetDriveTypeW", [w.LPCWSTR], w.UINT),
            (
                self.a,
                "GetSecurityInfo",
                [w.HANDLE, w.DWORD, w.DWORD] + [ctypes.POINTER(w.LPVOID)] * 5,
                w.DWORD,
            ),
            (
                self.a,
                "GetSecurityDescriptorControl",
                [w.LPVOID, ctypes.POINTER(w.WORD), ctypes.POINTER(w.DWORD)],
                w.BOOL,
            ),
            (self.a, "GetSecurityDescriptorLength", [w.LPVOID], w.DWORD),
            (self.a, "ConvertSidToStringSidW", [w.LPVOID, ctypes.POINTER(w.LPWSTR)], w.BOOL),
            (self.a, "GetAce", [w.LPVOID, w.DWORD, ctypes.POINTER(w.LPVOID)], w.BOOL),
            (self.a, "OpenProcessToken", [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], w.BOOL),
            (
                self.a,
                "GetTokenInformation",
                [w.HANDLE, w.DWORD, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)],
                w.BOOL,
            ),
            (
                self.a,
                "LookupPrivilegeNameW",
                [w.LPCWSTR, ctypes.POINTER(_Luid), w.LPWSTR, ctypes.POINTER(w.DWORD)],
                w.BOOL,
            ),
        )
        for library, name, arguments, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def _require(ok, label: str) -> None:
        if not ok:
            raise WindowsBoundaryHold(f"HOLD_WINDOWS_{label}_UNAVAILABLE")

    def _sid(self, pointer) -> str:
        value = w.LPWSTR()
        self._require(self.a.ConvertSidToStringSidW(pointer, ctypes.byref(value)), "SID")
        try:
            return value.value
        finally:
            self.k.LocalFree(ctypes.cast(value, w.LPVOID))

    def file_security(self, path: Path) -> FileSecurity:
        path = Path(path)
        if not path.is_absolute() or path.drive.startswith("\\"):
            raise WindowsBoundaryHold("HOLD_LOCAL_ABSOLUTE_PATH_REQUIRED")
        if self.k.GetDriveTypeW(path.anchor) != 3:
            raise WindowsBoundaryHold("HOLD_FIXED_LOCAL_VOLUME_REQUIRED")
        # Open the reparse entry itself, read-only and without write/delete sharing.
        handle = self.k.CreateFileW(str(path), 0x20080, 1, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise WindowsBoundaryHold("HOLD_WINDOWS_FILE_OPEN_UNAVAILABLE")
        descriptor = w.LPVOID()
        try:
            info = _FileInfo()
            self._require(self.k.GetFileInformationByHandle(handle, ctypes.byref(info)), "FILE_ID")
            if info.attributes & 0x400 or (not info.attributes & 0x10 and info.links != 1):
                raise WindowsBoundaryHold("HOLD_REPARSE_OR_HARDLINK")
            final = ctypes.create_unicode_buffer(32768)
            length = self.k.GetFinalPathNameByHandleW(handle, final, len(final), 0)
            self._require(0 < length < len(final), "FINAL_PATH")
            if os.path.normcase(final.value.removeprefix("\\\\?\\")) != os.path.normcase(str(path)):
                raise WindowsBoundaryHold("HOLD_PATH_ALIAS")
            owner, group, dacl, sacl = (w.LPVOID() for _ in range(4))
            code = self.a.GetSecurityInfo(
                handle,
                1,
                5,
                ctypes.byref(owner),
                ctypes.byref(group),
                ctypes.byref(dacl),
                ctypes.byref(sacl),
                ctypes.byref(descriptor),
            )
            self._require(code == 0 and owner and dacl and descriptor, "DACL")
            control, revision = w.WORD(), w.DWORD()
            self._require(
                self.a.GetSecurityDescriptorControl(
                    descriptor, ctypes.byref(control), ctypes.byref(revision)
                ),
                "DESCRIPTOR",
            )
            # ACL header: revision/pad, size, AceCount, pad (8 bytes).
            count = ctypes.c_ushort.from_address(dacl.value + 4).value
            if count > 256:
                raise WindowsBoundaryHold("HOLD_ACL_BOUND_EXCEEDED")
            aces = []
            for index in range(count):
                pointer = w.LPVOID()
                self._require(self.a.GetAce(dacl, index, ctypes.byref(pointer)), "ACE")
                header = ctypes.string_at(pointer, 4)
                if header[0] not in (0, 1) or int.from_bytes(header[2:4], "little") < 16:
                    raise WindowsBoundaryHold("HOLD_UNSUPPORTED_ACE")
                mask = w.DWORD.from_address(pointer.value + 4).value
                aces.append(Ace(header[0], header[1], mask, self._sid(pointer.value + 8)))
            size = self.a.GetSecurityDescriptorLength(descriptor)
            self._require(20 <= size <= 65536, "DESCRIPTOR_SIZE")
            identity = (
                info.volume,
                info.index_high,
                info.index_low,
                info.attributes,
                info.links,
                info.size_high,
                info.size_low,
                info.creation.dwHighDateTime,
                info.creation.dwLowDateTime,
                info.write.dwHighDateTime,
                info.write.dwLowDateTime,
            )
            return FileSecurity(
                self._sid(owner),
                bool(control.value & 0x1000),
                tuple(aces),
                identity,
                sha256_bytes(ctypes.string_at(descriptor, size)),
            )
        finally:
            if descriptor:
                self.k.LocalFree(descriptor)
            self.k.CloseHandle(handle)

    def _token_data(self, token, kind):
        size = w.DWORD()
        self.a.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
        self._require(0 < size.value <= 65536, "TOKEN_SIZE")
        buffer = ctypes.create_string_buffer(size.value)
        self._require(
            self.a.GetTokenInformation(token, kind, buffer, size, ctypes.byref(size)), "TOKEN"
        )
        return buffer

    def token_security(self, process_handle=None) -> TokenSecurity:
        """Observe a real process token handle; no caller-supplied proof dictionary."""
        token = w.HANDLE()
        self._require(
            self.a.OpenProcessToken(
                process_handle or self.k.GetCurrentProcess(), 8, ctypes.byref(token)
            ),
            "TOKEN_OPEN",
        )
        try:
            return self.inspect_token(token)
        finally:
            self.k.CloseHandle(token)

    def inspect_token(self, token) -> TokenSecurity:
        """Inspect an existing token; the caller retains ownership of the handle."""
        user = self._token_data(token, 1)
        sid = self._sid(_SidAttributes.from_buffer(user).Sid)

        def groups(kind, enabled_only):
            buffer = self._token_data(token, kind)
            count = w.DWORD.from_buffer(buffer).value
            self._require(count <= 1024, "TOKEN_GROUP_BOUND")
            offset = ctypes.sizeof(w.DWORD)
            alignment = ctypes.alignment(_SidAttributes)
            offset = (offset + alignment - 1) // alignment * alignment
            self._require(
                offset + count * ctypes.sizeof(_SidAttributes) <= len(buffer),
                "TOKEN_GROUP_LAYOUT",
            )
            entries = (_SidAttributes * count).from_buffer(buffer, offset)
            return tuple(
                self._sid(item.Sid)
                for item in entries
                if (
                    # A disabled, non-deny-only SID can be re-enabled by the
                    # token holder. Do not mistake it for a restricted SID.
                    not enabled_only or not item.Attributes & 0x10
                )
            )

        buffer = self._token_data(token, 3)
        count = w.DWORD.from_buffer(buffer).value
        self._require(4 + count * ctypes.sizeof(_LuidAttributes) <= len(buffer), "PRIVILEGE_LAYOUT")
        entries = (_LuidAttributes * count).from_buffer(buffer, 4)
        privileges = []
        for item in entries:
            name, size = ctypes.create_unicode_buffer(256), w.DWORD(256)
            self._require(
                self.a.LookupPrivilegeNameW(
                    None, ctypes.byref(item.Luid), name, ctypes.byref(size)
                ),
                "PRIVILEGE_NAME",
            )
            # Disabled privileges remain relevant: a Worker may enable them.
            privileges.append(name.value)
        token_type = w.DWORD.from_buffer(self._token_data(token, 8)).value
        return TokenSecurity(sid, groups(2, True), groups(11, False), tuple(privileges), token_type)


@contextmanager
def restricted_writer_token(*, base_token: int, scratch_sid: str, issuer_sid: str):
    """Source-only factory for a future trusted Operator, never called implicitly.

    No account/ACL/process is created. The returned primary token has one
    restricting scratch SID and maximum privileges removed. A separate fixed
    Worker account and actual deny-outside-scratch proof remain deployment gates.
    The caller must not close the yielded handle; it closes on context exit.
    """
    from .windows_custody import ADMINISTRATORS, SYSTEM, check_observer

    reader = WindowsSecurityReader()
    if (
        not base_token
        or not is_account_sid(scratch_sid)
        or scratch_sid in {issuer_sid, SYSTEM, ADMINISTRATORS}
    ):
        raise WindowsBoundaryHold("HOLD_FIXED_WORKER_TOKEN_AND_SCRATCH_SID_REQUIRED")
    a, k = reader.a, reader.k
    a.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(w.LPVOID)]
    a.ConvertStringSidToSidW.restype = w.BOOL
    a.CreateRestrictedToken.argtypes = [
        w.HANDLE,
        w.DWORD,
        w.DWORD,
        w.LPVOID,
        w.DWORD,
        w.LPVOID,
        w.DWORD,
        ctypes.POINTER(_SidAttributes),
        ctypes.POINTER(w.HANDLE),
    ]
    a.CreateRestrictedToken.restype = w.BOOL
    sid, restricted = w.LPVOID(), w.HANDLE()
    reader._require(a.ConvertStringSidToSidW(scratch_sid, ctypes.byref(sid)), "SCRATCH_SID")
    try:
        restricting = _SidAttributes(sid, 0)
        # DISABLE_MAX_PRIVILEGE; one restricting SID, no WRITE_RESTRICTED flag.
        reader._require(
            a.CreateRestrictedToken(
                base_token,
                1,
                0,
                None,
                0,
                None,
                1,
                ctypes.byref(restricting),
                ctypes.byref(restricted),
            ),
            "RESTRICTED_TOKEN_CREATION",
        )
        # Inspect the actual returned token, not claims supplied by the launcher.
        # A token handle cannot be passed to OpenProcessToken: use the direct
        # token helper shared with ordinary process observation instead.
        evidence = reader.inspect_token(restricted)
        check_observer(evidence, issuer_sid, restricted_writer=True)
        if (
            evidence.restricting_sids != (scratch_sid,)
            or scratch_sid == evidence.user
            or scratch_sid in evidence.grant_capable_groups
        ):
            raise WindowsBoundaryHold("HOLD_SCRATCH_RESTRICTION_MISMATCH")
        yield restricted.value
    finally:
        if restricted:
            k.CloseHandle(restricted)
        k.LocalFree(sid)
