"""Operator-fixed Windows custody candidate reusing the CLH v2 Lease/Decision chain.

Never selected by the public MCP server. It cannot issue runtime authority, set
ACLs, install a Root, launch a process, or qualify a Worker from a caller boolean.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .leases import (
    ProtectedCustodyVerifier,
    _assert_lease_entry_unchanged,
    _lease_directory_entry,
    _require_unexpired_acquisition,
    _validate_lease,
    _validate_writer_binding,
    _verify_bound_lease_decision,
)
from .local_time import LocalDeadline
from .util import (
    canonical_json_bytes,
    ensure_within,
    is_safe_json_integer,
    load_json,
    require_safe_id,
    sha256_bytes,
    sha256_file,
)
from .windows_security import (
    FileSecurity,
    TokenSecurity,
    WindowsBoundaryHold,
    WindowsSecurityReader,
)

SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
# File data/append/EA/attributes, directory delete-child, DELETE, WRITE_DAC,
# WRITE_OWNER, generic all/write and MAXIMUM_ALLOWED (unknown effective rights).
MUTATION_MASK = 0x116 | 0x40 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000 | 0x2000000
MANIFEST = "compatibility/repo-set-lease.v2/artifact-manifest.json"


def _digest(value: str) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


@dataclass(frozen=True)
class WindowsIssuerConfig:
    """Trusted Operator startup config; never accepted from MCP/request payloads."""

    repo_root: Path
    lock_root: Path
    issuer_sid: str
    producer_manifest_sha256: str

    def __post_init__(self):
        for root in (self.repo_root, self.lock_root):
            if (
                not isinstance(root, Path)
                or not root.is_absolute()
                or ".." in root.parts
                or str(root).startswith("\\")
            ):
                raise WindowsBoundaryHold("HOLD_FIXED_LOCAL_ROOT_REQUIRED")
        # First candidate supports the actual SYSTEM principal only. A SID's
        # account-shaped syntax cannot prove it names a user rather than a group.
        if self.issuer_sid != SYSTEM:
            raise WindowsBoundaryHold("HOLD_FIXED_SYSTEM_ISSUER_REQUIRED")
        if not _digest(self.producer_manifest_sha256):
            raise WindowsBoundaryHold("HOLD_PRODUCER_DIGEST_REQUIRED")


def check_observer(token: TokenSecurity, issuer_sid: str, *, restricted_writer=False) -> None:
    trusted = {issuer_sid, SYSTEM, ADMINISTRATORS}
    if token.user in trusted or trusted.intersection(token.grant_capable_groups):
        raise WindowsBoundaryHold("HOLD_SAME_OR_PRIVILEGED_PRINCIPAL")
    if set(token.privileges) - {"SeChangeNotifyPrivilege"}:
        raise WindowsBoundaryHold("HOLD_PRIVILEGED_TOKEN")
    if restricted_writer and (
        not token.restricting_sids
        or trusted.intersection(token.restricting_sids)
        or token.token_type != 1
    ):
        raise WindowsBoundaryHold("HOLD_RESTRICTED_WRITER_TOKEN_UNPROVEN")


def check_file_security(
    evidence: FileSecurity,
    issuer_sid: str,
    *,
    require_protected: bool,
) -> None:
    trusted = {issuer_sid, SYSTEM, ADMINISTRATORS}
    if evidence.owner not in trusted or (require_protected and not evidence.protected_dacl):
        raise WindowsBoundaryHold("HOLD_ROOT_OWNER_OR_PROTECTED_DACL_UNPROVEN")
    if not evidence.aces or not _digest(evidence.descriptor_sha256):
        raise WindowsBoundaryHold("HOLD_DACL_EVIDENCE_MISSING")
    for ace in evidence.aces:
        if ace.kind not in (0, 1) or ace.flags & ~0x1F:
            raise WindowsBoundaryHold("HOLD_UNSUPPORTED_ACE")
        if ace.kind == 0 and ace.mask & MUTATION_MASK and ace.sid not in trusted:
            raise WindowsBoundaryHold("HOLD_NON_OPERATOR_MUTATION_ACCESS")


class WindowsProtectedCustodyVerifier(ProtectedCustodyVerifier):
    """Read-only snapshot only. The base verifier retains its unconditional gate.

    Root/parent ACLs and identities are inventoried twice around the existing v2
    validation. This cannot prove atomic issuance, actual denied writes, or child
    containment; those obligations stay at the separate G-L1-ROOT Owner gate.
    """

    def __init__(self, config: WindowsIssuerConfig, deadline: LocalDeadline) -> None:
        super().__init__(config.lock_root, config.repo_root)
        self._config = config
        self._deadline = deadline
        self._reader = WindowsSecurityReader()

    def _protected_tree(self, root: Path) -> dict[str, tuple]:
        if root not in (self._repo_root, self._lock_root):
            raise WindowsBoundaryHold("HOLD_CALLER_ROOT_FORBIDDEN")
        check_observer(self._reader.token_security(), self._config.issuer_sid)
        inventory = {}

        def check(path: Path, inside: bool):
            metadata = path.lstat()
            if metadata.st_file_attributes & 0x400:
                raise WindowsBoundaryHold("HOLD_REPARSE_OR_HARDLINK")
            evidence = self._reader.file_security(path)
            check_file_security(evidence, self._config.issuer_sid, require_protected=inside)
            inventory[str(path)] = (evidence, metadata.st_dev, metadata.st_ino)
            if len(inventory) > 4096:
                raise WindowsBoundaryHold("HOLD_AUTHORITY_INVENTORY_BOUND")
            return metadata

        try:
            for parent in reversed(root.parents):
                check(parent, False)
            check(root, True)
            if not root.is_dir():
                raise WindowsBoundaryHold("HOLD_AUTHORITY_ROOT_NOT_DIRECTORY")
            pending = [root]
            while pending:
                parent = pending.pop()
                with os.scandir(parent) as contents:
                    for entry in contents:
                        classified = entry.stat(follow_symlinks=False)
                        path = parent / entry.name
                        observed = check(path, True)
                        if (classified.st_dev, classified.st_ino) != (
                            observed.st_dev,
                            observed.st_ino,
                        ):
                            raise WindowsBoundaryHold("HOLD_AUTHORITY_ENTRY_CHANGED")
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(path)
        except OSError as exc:
            raise WindowsBoundaryHold("HOLD_AUTHORITY_TRAVERSAL_FAILED") from exc
        return inventory

    def _verify_issuer(self) -> None:
        expected = {
            "schema_version": "clh-windows-issuance-root.v1",
            "product_owner": "CLH",
            "issuer_sid": self._config.issuer_sid,
            "policy": "OPERATOR_FIXED_CLH_ONLY_ISSUANCE",
            "producer_manifest_sha256": self._config.producer_manifest_sha256,
        }
        if (
            load_json(self._repo_root / "CLH-ISSUANCE-ROOT.json") != expected
            or sha256_file(self._repo_root / MANIFEST) != self._config.producer_manifest_sha256
        ):
            raise WindowsBoundaryHold("HOLD_FIXED_CLH_ISSUER_OR_PRODUCER_MISMATCH")

    def verify(self, lease_id: str, expected_generation: int, serialized: bytes) -> dict:
        intent = self._deadline.intent
        try:
            lease_id = require_safe_id(lease_id, "lease_id")
            if not is_safe_json_integer(expected_generation) or expected_generation < 1:
                raise WindowsBoundaryHold("HOLD_POSITIVE_SAFE_GENERATION_REQUIRED")
            if type(serialized) is not bytes or not serialized:
                raise WindowsBoundaryHold("HOLD_EXACT_SERIALIZED_CUSTODY_REQUIRED")
            if (
                expected_generation != intent.generation
                or sha256_bytes(serialized) != intent.lease_sha256
            ):
                raise WindowsBoundaryHold("HOLD_LOCAL_INTENT_LEASE_BINDING_MISMATCH")
            self._deadline.check("admission", retained_intent=intent)
            before_locks = self._protected_tree(self._lock_root)
            before_repo = self._protected_tree(self._repo_root)
            self._verify_issuer()
            if (self._lock_root / ".repo-set-admission.mutex").exists():
                raise WindowsBoundaryHold("HOLD_AUTHORITY_MUTATION_IN_PROGRESS")
            observed = _lease_directory_entry(
                self._lock_root,
                label="protected current lease",
                expected_name=f"{lease_id}.lease.json",
            )
            if observed is None:
                raise WindowsBoundaryHold("HOLD_CURRENT_PROTECTED_LEASE_ABSENT")
            path, identity = observed
            with path.open("rb") as handle:
                metadata = os.fstat(handle.fileno())
                if (metadata.st_dev, metadata.st_ino) != identity[:2]:
                    raise WindowsBoundaryHold("HOLD_CURRENT_LEASE_IDENTITY_CHANGED")
                raw_current = handle.read()
            current = load_json(path, expected_identity=identity[:2])
            if raw_current != serialized or canonical_json_bytes(current) != serialized:
                raise WindowsBoundaryHold("Serialized custody differs from current protected state")
            if (
                current.get("lease_id") != lease_id
                or current.get("generation") != expected_generation
            ):
                raise WindowsBoundaryHold("HOLD_CURRENT_LEASE_ID_OR_GENERATION_MISMATCH")
            if (
                current.get("schema_version") != "coord.repo-set-lease.v2"
                or current.get("state") != "ACTIVE"
            ):
                raise WindowsBoundaryHold("HOLD_ACTIVE_V2_LEASE_REQUIRED")
            self._verify_lineage(current)
            if len([item for item in current["repositories"] if item["mode"] == "WRITE"]) != 1:
                raise WindowsBoundaryHold("HOLD_EXACTLY_ONE_WRITER_REQUIRED")
            _validate_writer_binding(current)
            _assert_lease_entry_unchanged(
                self._lock_root, path, identity, label="protected current lease"
            )
            # Include the additional Windows Git check in the same bracket.
            if self._protected_tree(self._lock_root) != before_locks:
                raise WindowsBoundaryHold("HOLD_AUTHORITY_CHANGED_DURING_WRITER_CHECK")
            if self._protected_tree(self._repo_root) != before_repo:
                raise WindowsBoundaryHold("HOLD_AUTHORITY_CHANGED_DURING_WRITER_CHECK")
            _require_unexpired_acquisition(current)
            self._deadline.check("pre_spawn", retained_intent=intent)
            return {
                "lease_id": lease_id,
                "generation": expected_generation,
                "state": "ACTIVE",
                "lease_sha256": sha256_bytes(serialized),
                "custody_resolved": False,
                "verification": "WINDOWS_CLH_SOURCE_SNAPSHOT_ONLY",
                "time_assurance": "LOCAL_BOOT_BOUND",
                "authenticated_utc": False,
                "runtime_gate": "G_L1_ROOT_AND_CONSUMER_BOUNDARY_UNQUALIFIED",
            }
        except Exception:
            self._deadline.invalidate("HOLD_CUSTODY_CONTINUITY_LOST")
            raise

    def _verify_lineage(self, current: dict) -> None:
        """Reuse published validators; retain the same archived-candidate invariants.

        leases.py is itself hash-bound by the accepted producer artifact manifest.
        This Windows adapter cannot change those immutable published bytes merely
        to add an OS-specific issuer hook. No schema/manifest change is required.
        """
        next_candidate = current
        for generation in range(current["generation"], 0, -1):
            if generation == current["generation"]:
                candidate = current
            else:
                archive = ensure_within(
                    self._lock_root / "lease-history" / current["lease_id"] / f"{generation}.json",
                    self._lock_root,
                    label="protected historical candidate",
                    must_exist=True,
                )
                candidate = load_json(archive)
                next_decision = load_json(self._repo_root / next_candidate["decision_ref"])
                if (
                    candidate.get("lease_id") != current["lease_id"]
                    or candidate.get("generation") != generation
                    or candidate.get("state") != "ACTIVE"
                    or candidate.get("schema_version") != "coord.repo-set-lease.v2"
                    or next_decision.get("previous_decision_ref") != candidate.get("decision_ref")
                ):
                    raise WindowsBoundaryHold("HOLD_HISTORICAL_CANDIDATE_ANCESTRY_MISMATCH")
                for field in (
                    "schema_version",
                    "run_id",
                    "owner",
                    "created_utc",
                    "coordination_repository",
                ):
                    if candidate.get(field) != next_candidate.get(field):
                        raise WindowsBoundaryHold("HOLD_HISTORICAL_REPLACEMENT_IDENTITY_CHANGED")
            _validate_lease(
                candidate,
                self._repo_root,
                allow_coordination_self_write=True,
                require_native_paths=True,
            )
            decision_path = ensure_within(
                self._repo_root / candidate["decision_ref"],
                self._repo_root,
                label="protected issuance Decision",
                must_exist=True,
            )
            _verify_bound_lease_decision(
                candidate,
                self._repo_root,
                decision_path,
                action="lease:acquire" if generation == 1 else "lease:expand",
                error_label="Protected candidate Decision",
                sequence_label="Protected candidate",
            )
            next_candidate = candidate
