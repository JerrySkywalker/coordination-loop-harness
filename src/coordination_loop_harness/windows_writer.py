"""Provider-neutral read-only Worker/Job bridge for a future CLF launcher.

CLH never creates tokens/Jobs/processes. CLF must create the participant suspended
with a restricted primary token, assign the Job before resume, retain its own
process identity/terminal evidence, and stage effects behind actual filesystem
restrictions. These candidate observations cannot attest atomic creation.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes as w
from dataclasses import dataclass

from .windows_custody import check_observer
from .windows_security import TokenSecurity, WindowsBoundaryHold, WindowsSecurityReader


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong),
        ("job_time", ctypes.c_longlong),
        ("flags", w.DWORD),
        ("min_set", ctypes.c_size_t),
        ("max_set", ctypes.c_size_t),
        ("active_limit", w.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", w.DWORD),
        ("scheduling", w.DWORD),
    ]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimit),
        ("io", ctypes.c_ulonglong * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("times", ctypes.c_longlong * 4),
        ("faults", w.DWORD),
        ("total", w.DWORD),
        ("active", w.DWORD),
        ("terminated", w.DWORD),
    ]


class _ProcessList(ctypes.Structure):
    _fields_ = [
        ("assigned", w.DWORD),
        ("count", w.DWORD),
        ("pids", ctypes.c_size_t * 256),
    ]


@dataclass(frozen=True)
class JobSnapshot:
    participant_pid: int
    participant_creation: int
    participant_in_job: bool
    participant_exit: int
    limit_flags: int
    active_count: int
    active_pids: tuple[int, ...]


def check_writer_snapshot(token: TokenSecurity, job: JobSnapshot, issuer_sid: str) -> dict:
    """Deterministic candidate checks. Never an authority/actual denial proof."""
    check_observer(token, issuer_sid, restricted_writer=True)
    if (
        not job.participant_in_job
        or job.participant_pid <= 0
        or job.participant_creation <= 0
        or job.limit_flags & (0x800 | 0x1000)  # BREAKAWAY_OK / SILENT_BREAKAWAY_OK
        or not job.limit_flags & 0x2000  # KILL_ON_JOB_CLOSE
    ):
        raise WindowsBoundaryHold("HOLD_JOB_CONTAINMENT_UNPROVEN")
    if job.active_count != len(job.active_pids) or len(set(job.active_pids)) != len(
        job.active_pids
    ):
        raise WindowsBoundaryHold("HOLD_JOB_MEMBERSHIP_UNCERTAIN")
    if job.participant_exit == 259:  # STILL_ACTIVE
        if job.participant_pid not in job.active_pids:
            raise WindowsBoundaryHold("HOLD_PARTICIPANT_IDENTITY_MISSING")
    elif job.active_count:
        raise WindowsBoundaryHold("HOLD_DESCENDANTS_UNSETTLED")
    # Zero active processes + parent exit still says nothing about child exits,
    # inherited writable handles, startup atomicity or deny-outside-scratch.
    return {
        "verification": "WINDOWS_WRITER_SOURCE_SNAPSHOT_ONLY",
        "custody_resolved": False,
        "runtime_gate": "ATOMIC_START_ACL_DENIAL_AND_DESCENDANT_TERMINALS_UNQUALIFIED",
    }


class WindowsWriterInspector:
    """Inspect already-owned native handles; no launcher, installer or watchdog."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise WindowsBoundaryHold("HOLD_WINDOWS_SECURITY_BACKEND_UNAVAILABLE")
        self._reader = WindowsSecurityReader()
        self._kernel = self._reader.k
        signatures = (
            (
                "QueryInformationJobObject",
                [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID],
                w.BOOL,
            ),
            ("IsProcessInJob", [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL),
            ("GetProcessId", [w.HANDLE], w.DWORD),
            ("GetProcessTimes", [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
            ("GetExitCodeProcess", [w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        )
        for name, arguments, result in signatures:
            function = getattr(self._kernel, name)
            function.argtypes, function.restype = arguments, result

    def observe(self, *, participant_handle: int, job_handle: int, issuer_sid: str) -> dict:
        """CLF retains the handles; concurrent changes are conservatively refused."""
        if not participant_handle or not job_handle:
            raise WindowsBoundaryHold("HOLD_OWNED_PROCESS_AND_JOB_HANDLES_REQUIRED")
        require = self._reader._require
        token = self._reader.token_security(participant_handle)
        limits, accounting, members = _ExtendedLimit(), _Accounting(), _ProcessList()
        for kind, record in ((9, limits), (1, accounting), (3, members)):
            require(
                self._kernel.QueryInformationJobObject(
                    job_handle, kind, ctypes.byref(record), ctypes.sizeof(record), None
                ),
                "JOB_QUERY",
            )
        if members.count > 256 or members.assigned != members.count:
            raise WindowsBoundaryHold("HOLD_JOB_MEMBERSHIP_UNCERTAIN")
        contained, exit_code = w.BOOL(), w.DWORD()
        require(
            self._kernel.IsProcessInJob(participant_handle, job_handle, ctypes.byref(contained)),
            "JOB_MEMBERSHIP",
        )
        pid = self._kernel.GetProcessId(participant_handle)
        require(pid, "PROCESS_ID")
        creation, exit_time, kernel_time, user_time = (w.FILETIME() for _ in range(4))
        require(
            self._kernel.GetProcessTimes(
                participant_handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ),
            "PROCESS_TIMES",
        )
        require(
            self._kernel.GetExitCodeProcess(participant_handle, ctypes.byref(exit_code)),
            "PROCESS_EXIT",
        )
        snapshot = JobSnapshot(
            pid,
            (creation.dwHighDateTime << 32) | creation.dwLowDateTime,
            bool(contained),
            exit_code.value,
            limits.basic.flags,
            accounting.active,
            tuple(members.pids[: members.count]),
        )
        return check_writer_snapshot(token, snapshot, issuer_sid)
