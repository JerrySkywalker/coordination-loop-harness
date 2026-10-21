"""Operator-retained, one-shot CLH source producer for a future CLF consumer.

The only output is a bounded authenticated-channel handoff.  It cannot grant
protected runtime authority or be replayed as an offline JSON capability.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass

from .local_time import LocalDeadline, WindowsHostClock
from .util import canonical_json_bytes, ensure_within, sha256_bytes, sha256_file
from .windows_capability import (
    CHECKPOINT_ORDER,
    ProcessPin,
    WindowsPipe,
    binding_wire,
    parse_interrupt_deadline,
    parse_native_handle,
    require_system_self,
)
from .windows_custody import WindowsIssuerConfig, WindowsProtectedCustodyVerifier, sealed_decision
from .windows_publication import RESULT_CLAIM, RESULT_HANDOFF, ResultPublication
from .windows_security import WindowsBoundaryHold, is_account_sid
from .windows_writer import WindowsWriterBinding, WindowsWriterInspector


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
        worker_sid: str,
        scratch_sid: str,
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
            or not is_account_sid(worker_sid)
            or not is_account_sid(scratch_sid)
            or worker_sid == scratch_sid
            or client_pin.sid in (worker_sid, scratch_sid)
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
        self._worker_sid = worker_sid
        self._scratch_sid = scratch_sid

    def __getstate__(self):
        raise TypeError("Issuer state cannot be persisted/restored")

    def _expected_request(self, *, result=False) -> dict:
        intent = self._deadline.intent
        return {
            "schema_version": RESULT_CLAIM if result else "clh.windows-local-claim.v1",
            "intent_id": intent.intent_id,
            "generation": intent.generation,
            "lease_id": self._lease_id,
            "lease_sha256": intent.lease_sha256,
            "boot_id": intent.anchor.boot_id,
            "deadline_interrupt": str(intent.deadline),
            "target": self._target.as_dict(),
            "worker_sid": self._worker_sid,
            "scratch_sid": self._scratch_sid,
        }

    def claim_once(self) -> dict:
        """Explicit Operator call. Claim and any error consume the one attempt."""
        return self._claim_once(result=False)

    def claim_result_once(self) -> dict:
        """Opt-in fixed-result source contract; never selected by CLI/MCP."""
        return self._claim_once(result=True)

    def _claim_once(self, *, result: bool) -> dict:
        try:
            self._deadline.consume_issuer_claim()
        except Exception as exc:
            raise WindowsBoundaryHold("HOLD_ISSUER_INTENT_ALREADY_ATTEMPTED") from exc
        try:
            require_system_self(self._issuer_pin)
            require_native_deadline(self._deadline, self._issuer_pin)
            intent = self._deadline.intent
            if sha256_bytes(self._serialized) != intent.lease_sha256:
                raise WindowsBoundaryHold("HOLD_RETAINED_LEASE_CHANGED")
            self._deadline.check("admission", retained_intent=intent)
            lease = json.loads(self._serialized)
            decision = ensure_within(
                self._config.repo_root / lease["decision_ref"],
                self._config.repo_root,
                label="protected issuance Decision",
                must_exist=True,
            )
            with ExitStack() as stack:
                stack.enter_context(sealed_decision(decision))
                pipe = stack.enter_context(
                    WindowsPipe.first_server(self._pipe_name, client_sid=self._client_pin.sid)
                )
                pipe.require_server_self(self._issuer_pin)
                pipe.connect()
                with pipe.authenticated_peer(self._client_pin):
                    raw = pipe.read_frame()
                    if raw != canonical_json_bytes(self._expected_request(result=result)):
                        raise WindowsBoundaryHold("HOLD_EXACT_CLAIM_MISMATCH")
                    verifier = WindowsProtectedCustodyVerifier(
                        self._config, self._deadline, issuer_pin=self._issuer_pin
                    )
                    source = verifier.verify(self._lease_id, intent.generation, self._serialized)
                    writers = [item for item in lease["repositories"] if item["mode"] == "WRITE"]
                    if len(writers) != 1 or WriterTarget.from_lease(writers[0]) != self._target:
                        raise WindowsBoundaryHold("HOLD_FIXED_TARGET_MISMATCH")
                    publication = (
                        stack.enter_context(
                            ResultPublication(writers[0], publisher_sid=self._client_pin.sid)
                        ) if result else None
                    )
                    self._deadline.check("pre_spawn", retained_intent=intent)
                    response = {
                        "schema_version": (
                            RESULT_HANDOFF if result else "clh.windows-local-handoff.v1"
                        ),
                        "request_sha256": sha256_bytes(raw),
                        "decision_ref": lease["decision_ref"],
                        "decision_sha256": sha256_file(decision),
                        "issuer_pid": self._issuer_pin.pid,
                        "issuer_creation": str(self._issuer_pin.creation),
                        "client_pid": self._client_pin.pid,
                        "client_creation": str(self._client_pin.creation),
                        "boot_id": intent.anchor.boot_id,
                        "deadline_interrupt": str(intent.deadline),
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
                    verifier.verify(self._lease_id, intent.generation, self._serialized)
                    if publication is not None:
                        publication.check_clean()
                    # CLF now creates the participant suspended and assigns its
                    # Job. No participant identity was needed for admission.
                    pre_spawn_heartbeat = canonical_json_bytes(
                        {
                            "schema_version": "clh.windows-local-checkpoint.v1",
                            "stage": "pre_spawn_watchdog",
                            "handoff_sha256": handoff_digest,
                        }
                    )
                    while True:
                        raw_bind = pipe.read_frame()
                        if raw_bind != pre_spawn_heartbeat:
                            break
                        self._deadline.check("watchdog", retained_intent=intent)
                        verifier.verify(self._lease_id, intent.generation, self._serialized)
                        if publication is not None:
                            publication.check_clean()
                        pipe.write_frame(
                            canonical_json_bytes(
                                {
                                    "schema_version": "clh.windows-local-checkpoint-ack.v1",
                                    "stage": "pre_spawn_watchdog",
                                    "handoff_sha256": handoff_digest,
                                }
                            )
                        )
                    try:
                        bind = json.loads(raw_bind)
                        if raw_bind != canonical_json_bytes(bind):
                            raise ValueError("noncanonical bind")
                    except (ValueError, UnicodeError, TypeError) as exc:
                        raise WindowsBoundaryHold("HOLD_HANDOFF_BIND_FRAME_INVALID") from exc
                    if (
                        type(bind) is not dict
                        or set(bind)
                        != {
                            "schema_version",
                            "handoff_sha256",
                            "binding",
                            "participant_handle",
                            "job_handle",
                        }
                        or bind["schema_version"] != "clh.windows-local-bind.v1"
                        or bind["handoff_sha256"] != handoff_digest
                        or type(bind["binding"]) is not dict
                    ):
                        raise WindowsBoundaryHold("HOLD_HANDOFF_BIND_ORDER")
                    fields = bind["binding"]
                    if (
                        set(fields) != set(WindowsWriterBinding.__dataclass_fields__)
                        or type(fields.get("participant_pid")) is not int
                        or not 0 < fields["participant_pid"] < 2**32
                        or type(fields.get("participant_creation")) is not str
                    ):
                        raise WindowsBoundaryHold("HOLD_FIXED_WRITER_BINDING_REQUIRED")
                    creation = parse_interrupt_deadline(fields["participant_creation"])
                    try:
                        binding = WindowsWriterBinding(
                            **(fields | {"participant_creation": creation})
                        )
                    except (TypeError, ValueError) as exc:
                        raise WindowsBoundaryHold("HOLD_FIXED_WRITER_BINDING_REQUIRED") from exc
                    if (
                        binding_wire(binding) != fields
                        or (binding.issuer_sid, binding.worker_sid, binding.scratch_sid)
                        != (self._issuer_pin.sid, self._worker_sid, self._scratch_sid)
                        or (binding.intent_id, binding.generation, binding.lease_sha256)
                        != (intent.intent_id, intent.generation, intent.lease_sha256)
                    ):
                        raise WindowsBoundaryHold("HOLD_FIXED_WRITER_BINDING_REQUIRED")
                    participant = parse_native_handle(bind["participant_handle"])
                    job = parse_native_handle(bind["job_handle"])
                    handles = stack.enter_context(
                        pipe.duplicate_peer_query_handles(participant, job)
                    )
                    inspector = WindowsWriterInspector(binding, self._deadline)
                    inspector.observe(participant_handle=handles[0], job_handle=handles[1])
                    verifier.verify(self._lease_id, intent.generation, self._serialized)
                    if publication is not None:
                        publication.check_clean()
                    pipe.write_frame(
                        canonical_json_bytes(
                            {
                                "schema_version": "clh.windows-local-checkpoint-ack.v1",
                                "stage": "bind",
                                "handoff_sha256": handoff_digest,
                            }
                        )
                    )
                    for stage in CHECKPOINT_ORDER:
                        watchdog = canonical_json_bytes(
                            {
                                "schema_version": "clh.windows-local-checkpoint.v1",
                                "stage": "watchdog",
                                "handoff_sha256": handoff_digest,
                                "binding": binding_wire(binding),
                            }
                        )
                        expected = canonical_json_bytes(
                            {
                                "schema_version": "clh.windows-local-checkpoint.v1",
                                "stage": stage,
                                "handoff_sha256": handoff_digest,
                                "binding": binding_wire(binding),
                            }
                        )
                        while True:
                            received = pipe.read_frame()
                            if received == watchdog:
                                checked = "watchdog"
                            elif received == expected:
                                checked = stage
                            else:
                                raise WindowsBoundaryHold("HOLD_HANDOFF_CHECKPOINT_MISMATCH")
                            self._deadline.check(checked, retained_intent=intent)
                            post_effect = result and stage in ("receipt", "dag_ack")
                            if post_effect:
                                snapshot = verifier._verify_result(
                                    self._lease_id, intent.generation, self._serialized, publication
                                )
                            else:
                                snapshot = verifier.verify(
                                    self._lease_id, intent.generation, self._serialized
                                )
                                if publication is not None:
                                    publication.check_clean()
                            observation = dict(
                                participant_handle=handles[0], job_handle=handles[1]
                            )
                            if result and (post_effect or checked == "effect_publication"):
                                observation["terminal"] = True
                            inspector.observe(**observation)
                            self._deadline.check(checked, retained_intent=intent)
                            ack = {
                                "schema_version": "clh.windows-local-checkpoint-ack.v1",
                                "stage": checked,
                                "handoff_sha256": handoff_digest,
                            }
                            if post_effect:
                                ack["published_result"] = snapshot["published_result"]
                            pipe.write_frame(canonical_json_bytes(ack))
                            if checked == stage:
                                break
                    return response
        except Exception:
            self._deadline.invalidate("HOLD_ISSUER_CLAIM_FAILED")
            raise
