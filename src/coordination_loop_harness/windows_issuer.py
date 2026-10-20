"""Operator-retained, one-shot CLH source producer for a future CLF consumer.

The only output is a bounded authenticated-channel handoff.  It cannot grant
protected runtime authority or be replayed as an offline JSON capability.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from .local_time import LocalDeadline, WindowsHostClock
from .util import canonical_json_bytes, sha256_bytes, sha256_file
from .windows_capability import ProcessPin, WindowsPipe, require_system_self
from .windows_custody import WindowsIssuerConfig, WindowsProtectedCustodyVerifier
from .windows_security import WindowsBoundaryHold


@dataclass(frozen=True)
class WriterTarget:
    repository: str
    canonical_path: str
    worktree_root: str
    branch_ref: str
    exact_sha: str

    @classmethod
    def from_lease(cls, item: dict) -> WriterTarget:
        return cls(
            *(
                item[key]
                for key in (
                    "repository",
                    "canonical_path",
                    "worktree_root",
                    "branch_ref",
                    "exact_sha",
                )
            )
        )

    def as_dict(self) -> dict:
        return {
            key: getattr(self, key)
            for key in ("repository", "canonical_path", "worktree_root", "branch_ref", "exact_sha")
        }


def require_native_deadline(deadline: LocalDeadline, issuer_pin: ProcessPin) -> None:
    """A fixture/caller read callback cannot become an issuer time witness."""
    if type(deadline) is not LocalDeadline:
        raise WindowsBoundaryHold("HOLD_NATIVE_ISSUER_DEADLINE_REQUIRED")
    read = deadline._read
    if (
        type(getattr(read, "__self__", None)) is not WindowsHostClock
        or getattr(read, "__func__", None) is not WindowsHostClock.read
        or deadline.intent.anchor.process_id != f"{issuer_pin.pid}:{issuer_pin.creation}"
    ):
        raise WindowsBoundaryHold("HOLD_NATIVE_ISSUER_DEADLINE_REQUIRED")


class WindowsIssuer:
    """One retained attempt, including failed validation or failed connection."""

    def __init__(
        self,
        *,
        config: WindowsIssuerConfig,
        deadline: LocalDeadline,
        issuer_pin: ProcessPin,
        client_pin: ProcessPin,
        pipe_name: str,
        lease_id: str,
        serialized_lease: bytes,
        target: WriterTarget,
    ) -> None:
        if type(issuer_pin) is not ProcessPin or type(client_pin) is not ProcessPin:
            raise WindowsBoundaryHold("HOLD_FIXED_PROCESS_PINS_REQUIRED")
        require_system_self(issuer_pin)
        require_native_deadline(deadline, issuer_pin)
        WindowsPipe._name(pipe_name)
        if (
            issuer_pin.sid != config.issuer_sid
            or client_pin.sid == issuer_pin.sid
            or type(serialized_lease) is not bytes
            or not serialized_lease
            or not isinstance(target, WriterTarget)
            or not isinstance(lease_id, str)
            or not lease_id
        ):
            raise WindowsBoundaryHold("HOLD_FIXED_ISSUER_CLIENT_AND_LEASE_REQUIRED")
        self._config = config
        self._deadline = deadline
        self._issuer_pin = issuer_pin
        self._client_pin = client_pin
        self._pipe_name = pipe_name
        self._lease_id = lease_id
        self._serialized = serialized_lease
        self._target = target
        self._lock = threading.Lock()
        self._attempted = False

    def __getstate__(self):
        raise TypeError("Issuer state cannot be persisted/restored")

    def _expected_request(self) -> dict:
        intent = self._deadline.intent
        return {
            "schema_version": "clh.windows-local-claim.v1",
            "intent_id": intent.intent_id,
            "generation": intent.generation,
            "lease_id": self._lease_id,
            "lease_sha256": intent.lease_sha256,
            "boot_id": intent.anchor.boot_id,
            "deadline_interrupt": intent.deadline,
            "target": self._target.as_dict(),
        }

    def claim_once(self) -> dict:
        """Explicit Operator call. Claim and any error consume the one attempt."""
        with self._lock:
            if self._attempted:
                raise WindowsBoundaryHold("HOLD_ISSUER_INTENT_ALREADY_ATTEMPTED")
            self._attempted = True
        try:
            require_system_self(self._issuer_pin)
            require_native_deadline(self._deadline, self._issuer_pin)
            intent = self._deadline.intent
            if sha256_bytes(self._serialized) != intent.lease_sha256:
                raise WindowsBoundaryHold("HOLD_RETAINED_LEASE_CHANGED")
            self._deadline.check("admission", retained_intent=intent)
            with WindowsPipe.first_server(self._pipe_name, client_sid=self._client_pin.sid) as pipe:
                pipe.require_server_self(self._issuer_pin)
                pipe.connect()
                with pipe.authenticated_peer(self._client_pin):
                    raw = pipe.read_frame()
                    if raw != canonical_json_bytes(self._expected_request()):
                        raise WindowsBoundaryHold("HOLD_EXACT_CLAIM_MISMATCH")
                    verifier = WindowsProtectedCustodyVerifier(
                        self._config, self._deadline, issuer_pin=self._issuer_pin
                    )
                    source = verifier.verify(self._lease_id, intent.generation, self._serialized)
                    lease = json.loads(self._serialized)
                    writers = [item for item in lease["repositories"] if item["mode"] == "WRITE"]
                    if len(writers) != 1 or WriterTarget.from_lease(writers[0]) != self._target:
                        raise WindowsBoundaryHold("HOLD_FIXED_TARGET_MISMATCH")
                    decision = self._config.repo_root / lease["decision_ref"]
                    self._deadline.check("pre_spawn", retained_intent=intent)
                    response = {
                        "schema_version": "clh.windows-local-handoff.v1",
                        "request_sha256": sha256_bytes(raw),
                        "decision_ref": lease["decision_ref"],
                        "decision_sha256": sha256_file(decision),
                        "issuer_pid": self._issuer_pin.pid,
                        "issuer_creation": str(self._issuer_pin.creation),
                        "client_pid": self._client_pin.pid,
                        "client_creation": str(self._client_pin.creation),
                        "boot_id": intent.anchor.boot_id,
                        "deadline_interrupt": intent.deadline,
                        "lease_sha256": intent.lease_sha256,
                        "generation": intent.generation,
                        "target": self._target.as_dict(),
                        "custody_resolved": False,
                        "verification": source["verification"],
                        "runtime_gate": "G_L1_ROOT_AND_CONSUMER_BOUNDARY_UNQUALIFIED",
                    }
                    response_raw = canonical_json_bytes(response)
                    handoff_digest = sha256_bytes(response_raw)
                    pipe.write_frame(response_raw)
                    expected_ack = canonical_json_bytes(
                        {
                            "schema_version": "clh.windows-local-ack.v1",
                            "handoff_sha256": handoff_digest,
                        }
                    )
                    if pipe.read_frame() != expected_ack:
                        raise WindowsBoundaryHold("HOLD_HANDOFF_ACK_MISMATCH")
                    self._deadline.check("pre_spawn", retained_intent=intent)
                    pipe.write_frame(
                        canonical_json_bytes(
                            {
                                "schema_version": "clh.windows-local-final.v1",
                                "handoff_sha256": handoff_digest,
                            }
                        )
                    )
                    self._deadline.check("pre_spawn", retained_intent=intent)
                    return response
        except Exception:
            self._deadline.invalidate("HOLD_ISSUER_CLAIM_FAILED")
            raise
