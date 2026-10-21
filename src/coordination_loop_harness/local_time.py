"""Source-only L1 elapsed deadlines; no UTC, persistence, renewal or runtime grant.

The trusted Operator must retain this object in its own process, protect the
intent, and poll it. A successful check cannot contain a Worker or authorize an
effect. CLF owns containment and must revalidate before publishing staged effects.
"""

from __future__ import annotations

import ctypes
import os
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass

TICKS_PER_SECOND = 10_000_000
SOURCE = "WINDOWS_INTERRUPT_PRECISE_BOOT_GUID_V1"
CHECKPOINTS = frozenset(
    {
        "admission",
        "pre_spawn",
        "watchdog",
        "resume",
        "effect_publication",
        "receipt",
        "dag_ack",
    }
)


class LocalTimeHold(ValueError):
    """A latched loss of local continuity, never an invitation to re-anchor."""


@dataclass(frozen=True)
class HostSample:
    boot_id: str
    process_id: str
    interrupt_before: int
    unbiased: int
    interrupt_after: int
    source: str = SOURCE

    def validate(self) -> None:
        try:
            boot = uuid.UUID(self.boot_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise LocalTimeHold("HOLD_BOOT_ID_UNPROVEN") from exc
        if not boot.int or str(boot) != self.boot_id or not self.process_id:
            raise LocalTimeHold("HOLD_PROCESS_OR_BOOT_WITNESS_MISSING")
        ticks = (self.interrupt_before, self.unbiased, self.interrupt_after)
        if any(type(tick) is not int or not 0 <= tick < 2**64 for tick in ticks):
            raise LocalTimeHold("HOLD_COUNTER_INVALID")
        if (
            self.source != SOURCE
            or not self.unbiased <= self.interrupt_after
            or not 0 <= self.interrupt_after - self.interrupt_before <= TICKS_PER_SECOND // 50
        ):
            raise LocalTimeHold("HOLD_SOURCE_OR_SAMPLE_UNCERTAIN")


class WindowsHostClock:
    """Read-only native candidate. Unsupported boot ABI fails closed, no fallback.

    NtQuerySystemInformation class 90 is an internal Windows ABI. Its GUID is
    independent of tick/wall time; deployment must qualify this exact OS build.
    No reboot/suspend qualification is inferred from a successful native read.
    """

    def __init__(self) -> None:
        try:
            self._initialize()
        except (OSError, AttributeError) as exc:
            raise LocalTimeHold("HOLD_NATIVE_TIME_WITNESS_UNAVAILABLE") from exc

    def _initialize(self) -> None:
        if os.name != "nt":
            raise LocalTimeHold("HOLD_WINDOWS_TIME_BACKEND_UNAVAILABLE")
        from ctypes import wintypes as w

        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._realtime = ctypes.WinDLL("api-ms-win-core-realtime-l1-1-1", use_last_error=True)
        self._nt = ctypes.WinDLL("ntdll", use_last_error=True)
        self._realtime.QueryInterruptTimePrecise.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
        self._realtime.QueryInterruptTimePrecise.restype = None
        self._realtime.QueryUnbiasedInterruptTimePrecise.argtypes = [
            ctypes.POINTER(ctypes.c_ulonglong)
        ]
        self._realtime.QueryUnbiasedInterruptTimePrecise.restype = None
        self._kernel.GetCurrentProcess.argtypes = []
        self._kernel.GetCurrentProcess.restype = w.HANDLE
        self._kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
        self._kernel.GetProcessTimes.restype = w.BOOL
        self._nt.NtQuerySystemInformation.argtypes = [w.ULONG, w.LPVOID, w.ULONG, w.PULONG]
        self._nt.NtQuerySystemInformation.restype = w.LONG

    def _boot(self) -> str:
        # GUID (16), FIRMWARE_TYPE (4), padding (4), BootFlags (8).
        data = ctypes.create_string_buffer(32)
        size = ctypes.c_ulong()
        status = self._nt.NtQuerySystemInformation(90, data, len(data), ctypes.byref(size))
        if status != 0 or size.value != 32:
            raise LocalTimeHold("HOLD_BOOT_ABI_UNAVAILABLE")
        boot = uuid.UUID(bytes_le=data.raw[:16])
        if not boot.int:
            raise LocalTimeHold("HOLD_BOOT_ID_UNPROVEN")
        return str(boot)

    def _process(self) -> str:
        from ctypes import wintypes as w

        creation, exit_time, kernel, user = (w.FILETIME() for _ in range(4))
        if not self._kernel.GetProcessTimes(
            self._kernel.GetCurrentProcess(),
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise LocalTimeHold("HOLD_PROCESS_WITNESS_UNAVAILABLE")
        identity = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        if not identity:
            raise LocalTimeHold("HOLD_PROCESS_WITNESS_UNAVAILABLE")
        return f"{os.getpid()}:{identity}"

    def read(self) -> HostSample:
        try:
            boot = self._boot()
            before, unbiased, after = (ctypes.c_ulonglong() for _ in range(3))
            self._realtime.QueryInterruptTimePrecise(ctypes.byref(before))
            self._realtime.QueryUnbiasedInterruptTimePrecise(ctypes.byref(unbiased))
            self._realtime.QueryInterruptTimePrecise(ctypes.byref(after))
            if self._boot() != boot:
                raise LocalTimeHold("HOLD_BOOT_CHANGED_DURING_READ")
            sample = HostSample(boot, self._process(), before.value, unbiased.value, after.value)
            sample.validate()
            return sample
        except (OSError, AttributeError) as exc:
            raise LocalTimeHold("HOLD_NATIVE_TIME_WITNESS_UNAVAILABLE") from exc


@dataclass(frozen=True)
class LocalIntent:
    """An in-process retained intent, not a serialized authority credential."""

    intent_id: str
    generation: int
    lease_sha256: str
    anchor: HostSample
    deadline: int


class LocalDeadline:
    """Nonrenewable <=600s cap, with permanent HOLD on any uncertainty.

    Construction is trusted source preparation, not Owner/Lease admission.
    There is deliberately no restore, renew, deserialize or replacement API.
    """

    def __init__(
        self,
        read: Callable[[], HostSample],
        *,
        intent_id: str,
        generation: int,
        lease_sha256: str,
        duration_seconds: int,
        shorter_cap: int | None = None,
    ) -> None:
        if (
            not isinstance(intent_id, str)
            or not intent_id
            or type(generation) is not int
            or generation < 1
            or type(duration_seconds) is not int
            or not 1 <= duration_seconds <= 600
            or not isinstance(lease_sha256, str)
            or len(lease_sha256) != 64
            or any(c not in "0123456789abcdef" for c in lease_sha256)
        ):
            raise LocalTimeHold("HOLD_INTENT_INVALID")
        anchor = read()
        anchor.validate()
        deadline = anchor.interrupt_before + duration_seconds * TICKS_PER_SECOND
        if shorter_cap is not None:
            if type(shorter_cap) is not int or shorter_cap <= anchor.interrupt_after:
                raise LocalTimeHold("HOLD_LOCAL_CAP_INVALID_OR_EXPIRED")
            deadline = min(deadline, shorter_cap)
        if deadline >= 2**64:
            raise LocalTimeHold("HOLD_COUNTER_OVERFLOW")
        self._intent = LocalIntent(intent_id, generation, lease_sha256, anchor, deadline)
        self._read = read
        self._last = anchor
        self._hold: str | None = None
        self._issuer_claim_consumed = False
        self._lock = threading.Lock()

    @property
    def intent(self) -> LocalIntent:
        return self._intent

    def __getstate__(self):
        raise TypeError("Local deadlines cannot be persisted/restored")

    def invalidate(self, reason: str = "HOLD_ROOT_OR_WATCHDOG_LOST") -> None:
        with self._lock:
            if self._hold is None:
                self._hold = (
                    reason
                    if isinstance(reason, str) and reason.strip()
                    else "HOLD_ROOT_OR_WATCHDOG_LOST"
                )

    def consume_issuer_claim(self) -> None:
        """Burn the retained intent across all issuer objects, including failures."""
        with self._lock:
            if self._issuer_claim_consumed:
                raise LocalTimeHold("HOLD_ISSUER_INTENT_ALREADY_ATTEMPTED")
            self._issuer_claim_consumed = True

    def check(self, checkpoint: str, *, retained_intent: LocalIntent) -> HostSample:
        with self._lock:
            if self._hold is not None:
                raise LocalTimeHold(self._hold)
            try:
                if checkpoint not in CHECKPOINTS or retained_intent != self._intent:
                    raise LocalTimeHold("HOLD_INTENT_CHANGED_OR_UNKNOWN_CHECKPOINT")
                now = self._read()
                now.validate()
                anchor, last = self._intent.anchor, self._last
                if (now.boot_id, now.process_id, now.source) != (
                    anchor.boot_id,
                    anchor.process_id,
                    anchor.source,
                ):
                    raise LocalTimeHold("HOLD_BOOT_PROCESS_OR_SOURCE_CHANGED")
                if now.interrupt_before < last.interrupt_after or now.unbiased < last.unbiased:
                    raise LocalTimeHold("HOLD_COUNTER_REGRESSION")
                # Long gaps and a changed sleep bias cannot prove wake containment.
                # 20ms sampling uncertainty is NOT a clock/expiry grace period.
                tolerance = TICKS_PER_SECOND // 50
                bias_low = now.interrupt_before - now.unbiased
                bias_high = now.interrupt_after - now.unbiased
                origin_low = anchor.interrupt_before - anchor.unbiased
                origin_high = anchor.interrupt_after - anchor.unbiased
                if (
                    now.interrupt_after - last.interrupt_before > 5 * TICKS_PER_SECOND
                    or bias_low > origin_high + tolerance
                    or bias_high < origin_low - tolerance
                ):
                    raise LocalTimeHold("HOLD_SUSPEND_OR_WATCHDOG_CONTINUITY_UNPROVEN")
                if now.interrupt_after >= self._intent.deadline:
                    raise LocalTimeHold("HOLD_LOCAL_DEADLINE_EXPIRED")
                self._last = now
                return now
            except Exception as exc:
                self._hold = (
                    str(exc)
                    if isinstance(exc, LocalTimeHold) and str(exc)
                    else "HOLD_TIME_READ_FAILED"
                )
                raise LocalTimeHold(self._hold) from exc
