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

from .local_time import LocalDeadline, LocalIntent
from .windows_custody import SYSTEM, _digest, check_observer
from .windows_security import (
    TokenSecurity,
    WindowsBoundaryHold,
    WindowsSecurityReader,
    is_account_sid,
)


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


@dataclass(frozen=True)
class WindowsWriterBinding:
    """Operator-retained identity, fixed before CLF resumes the participant."""

    issuer_sid: str
    worker_sid: str
    scratch_sid: str
    participant_pid: int
    participant_creation: int
    intent_id: str
    generation: int
    lease_sha256: str

    def __post_init__(self):
        if (
            self.issuer_sid != SYSTEM
            or not is_account_sid(self.worker_sid)
            or not is_account_sid(self.scratch_sid)
            or self.worker_sid == self.scratch_sid
            or type(self.participant_pid) is not int
            or self.participant_pid < 1
            or type(self.participant_creation) is not int
            or self.participant_creation < 1
            or not isinstance(self.intent_id, str)
            or not self.intent_id
            or type(self.generation) is not int
            or self.generation < 1
            or not _digest(self.lease_sha256)
        ):
            raise WindowsBoundaryHold("HOLD_FIXED_WRITER_BINDING_REQUIRED")


def check_writer_snapshot(
    token: TokenSecurity,
    job: JobSnapshot,
    binding: WindowsWriterBinding,
    intent: LocalIntent,
    *,
    terminal=False,
) -> dict:
    """Deterministic candidate checks. Never an authority/actual denial proof."""
    check_observer(token, binding.issuer_sid, restricted_writer=True)
    if (
        token.user != binding.worker_sid
        or token.restricting_sids != (binding.scratch_sid,)
        or (job.participant_pid, job.participant_creation)
        != (
            binding.participant_pid,
            binding.participant_creation,
        )
        or (intent.intent_id, intent.generation, intent.lease_sha256)
        != (
            binding.intent_id,
            binding.generation,
            binding.lease_sha256,
        )
    ):
        raise WindowsBoundaryHold("HOLD_WRITER_OR_LEASE_INTENT_IDENTITY_MISMATCH")
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
    if terminal and (job.participant_exit != 0 or job.active_count != 0):
        raise WindowsBoundaryHold("HOLD_RESULT_REQUIRES_NATIVE_TERMINAL_WRITER")
    # Zero active processes + parent exit still says nothing about child exits,
    # inherited writable handles, startup atomicity or deny-outside-scratch.
    return {
        "verification": "WINDOWS_WRITER_SOURCE_SNAPSHOT_ONLY",
        "custody_resolved": False,
        "runtime_gate": "ATOMIC_START_ACL_DENIAL_AND_DESCENDANT_TERMINALS_UNQUALIFIED",
    }


class WindowsWriterInspector:
    """Inspect already-owned native handles; no launcher, installer or watchdog."""

    def __init__(self, binding: WindowsWriterBinding, deadline: LocalDeadline) -> None:
        if os.name != "nt":
            raise WindowsBoundaryHold("HOLD_WINDOWS_SECURITY_BACKEND_UNAVAILABLE")
        self._reader = WindowsSecurityReader()
        self._binding = binding
        self._deadline = deadline
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

    def observe(self, *, participant_handle: int, job_handle: int, terminal=False) -> dict:
        """CLF retains the handles; concurrent changes are conservatively refused."""
        if not participant_handle or not job_handle:
            self._deadline.invalidate("HOLD_OWNED_PROCESS_OR_JOB_HANDLE_LOST")
            raise WindowsBoundaryHold("HOLD_OWNED_PROCESS_AND_JOB_HANDLES_REQUIRED")
        intent = self._deadline.intent
        self._deadline.check("watchdog", retained_intent=intent)
        try:
            result = self._observe(participant_handle, job_handle, intent, terminal=terminal)
            self._deadline.check("watchdog", retained_intent=intent)
            return result
        except Exception:
            self._deadline.invalidate("HOLD_WRITER_OR_JOB_CONTINUITY_LOST")
            raise

    def _observe(
        self, participant_handle: int, job_handle: int, intent: LocalIntent, *, terminal=False
    ) -> dict:
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
        return check_writer_snapshot(token, snapshot, self._binding, intent, terminal=terminal)
