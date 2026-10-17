"""Thin local STDIO MCP facade over CLH lease custody primitives."""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from .leases import ProtectedCustodyVerifier, acquire, lease_candidate_sha256, observe, release
from .util import _fsync_directory, canonical_json_bytes, load_json, require_safe_id


def _result(*, identity: dict[str, Any], disposition: str, **payload: Any) -> dict[str, Any]:
    return {"product_owner": "CLH", "identity": identity, "disposition": disposition, **payload}


def _refusal(*, identity: dict[str, Any], reason: str) -> dict[str, Any]:
    return _result(identity=identity, disposition="REFUSED", refusal_reason=reason)


def _repository_file(path_text: str, repo_root: Path) -> Path:
    """Resolve a declared custody document without accepting paths outside CLH state."""
    path = Path(path_text).resolve()
    try:
        path.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("Custody document must be within CLH repository root") from exc
    return path


class _ReconciliationRequired(RuntimeError):
    pass


def create_server(
    lock_root: Path, repo_root: Path, *, staging_root: Path | None = None
) -> MCPServer:
    """Create a transport adapter; lease validation and state stay owned by CLH."""
    custody_verifier = ProtectedCustodyVerifier(lock_root, repo_root)
    lock_root = lock_root.resolve()
    repo_root = repo_root.resolve()
    server = MCPServer("coordination-loop-clh", version="1", title="CLH MCP facade")
    staging = (staging_root or Path(tempfile.gettempdir())).resolve()
    if staging == repo_root or repo_root in staging.parents:
        raise ValueError("MCP candidate staging must be outside the repository")
    domain = hashlib.sha256((str(lock_root) + "\0" + str(repo_root)).encode()).hexdigest()[:24]
    prefix = f"clh-mcp-{domain}-"

    @contextmanager
    def candidate_snapshot(document: dict[str, Any]):
        # The fence is bound to the fixed authority root, so changing a
        # process temp/staging directory cannot hide an unresolved operation.
        lock_root.mkdir(parents=True, exist_ok=True)
        pending = lock_root / ".mcp-operation.pending"
        try:
            pending.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise _ReconciliationRequired("CLH MCP operation requires reconciliation") from exc
        directory = None
        snapshot = None
        metadata = pending / "operation.json"
        preserve_fence = False
        try:
            _fsync_directory(lock_root)
            if any(staging.glob(prefix + "*")):
                preserve_fence = True
                raise _ReconciliationRequired("MCP candidate staging requires reconciliation")
            directory = Path(tempfile.mkdtemp(prefix=prefix, dir=staging))
            _fsync_directory(staging)
            snapshot = directory / "candidate.json"
            with metadata.open("xb") as handle:
                handle.write(
                    canonical_json_bytes(
                        {
                            "candidate_sha256": lease_candidate_sha256(document),
                            "snapshot_path": str(snapshot),
                            "automatic_reclaim": False,
                        }
                    )
                )
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_directory(pending)
            with snapshot.open("xb") as handle:
                handle.write(canonical_json_bytes(document))
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_directory(directory)
            yield snapshot
        finally:
            if not preserve_fence:
                try:
                    if snapshot is not None:
                        snapshot.unlink(missing_ok=True)
                    if directory is not None:
                        directory.rmdir()
                        _fsync_directory(staging)
                    metadata.unlink(missing_ok=True)
                    pending.rmdir()
                    _fsync_directory(lock_root)
                except OSError as exc:
                    raise _ReconciliationRequired(
                        "MCP candidate cleanup requires reconciliation"
                    ) from exc

    def state(lease_id: str) -> dict[str, Any]:
        target = lock_root / f"{lease_id}.lease.json"
        try:
            if not target.exists() and not target.is_symlink():
                return {"ownership_status": "ABSENT"}
            result = observe(lease_id, lock_root, repo_root=repo_root)
            if result["ownership_status"] == "UNKNOWN_FAIL_CLOSED":
                return result
            # Observation validates entry identity; bytes bind before/after
            # reconciliation without manufacturing an effective custody grant.
            result["document_sha256"] = lease_candidate_sha256(load_json(target))
            return result
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"ownership_status": "UNKNOWN_FAIL_CLOSED", "findings": [str(exc)]}

    def uncertain(identity: dict[str, Any], reason: str, custody: dict[str, Any]):
        return _result(
            identity=identity,
            disposition="UNCERTAIN",
            refusal_reason=reason,
            custody=custody,
            automatic_retry=False,
        )

    def mutation_failure(identity, reason, lease_id, before, *, orphan=False):
        after = state(lease_id)
        if not orphan and after == before and after["ownership_status"] != "UNKNOWN_FAIL_CLOSED":
            return _refusal(identity=identity, reason=reason)
        return uncertain(identity, reason, after)

    @server.tool(name="inspect_authority", structured_output=True)
    def inspect_authority(lease_id: str) -> dict[str, Any]:
        try:
            authority = observe(lease_id, lock_root, repo_root=repo_root)
            if authority["ownership_status"] == "UNKNOWN_FAIL_CLOSED":
                return uncertain({"lease_id": lease_id}, "Custody is unknown", authority)
        except OSError as exc:
            return uncertain(
                {"lease_id": lease_id}, str(exc), {"ownership_status": "UNKNOWN_FAIL_CLOSED"}
            )
        except (TypeError, ValueError) as exc:
            return _refusal(identity={"lease_id": lease_id}, reason=str(exc))
        return _result(identity={"lease_id": lease_id}, disposition="OK", authority=authority)

    @server.tool(name="inspect_custody", structured_output=True)
    def inspect_custody(lease_id: str) -> dict[str, Any]:
        try:
            custody = observe(lease_id, lock_root, repo_root=repo_root)
            if custody["ownership_status"] == "UNKNOWN_FAIL_CLOSED":
                return uncertain({"lease_id": lease_id}, "Custody is unknown", custody)
        except OSError as exc:
            return uncertain(
                {"lease_id": lease_id}, str(exc), {"ownership_status": "UNKNOWN_FAIL_CLOSED"}
            )
        except (TypeError, ValueError) as exc:
            return _refusal(identity={"lease_id": lease_id}, reason=str(exc))
        return _result(identity={"lease_id": lease_id}, disposition="OK", custody=custody)

    @server.tool(name="verify_custody", structured_output=True)
    def verify_custody(
        lease_id: str, expected_generation: int, serialized_authority: str
    ) -> dict[str, Any]:
        try:
            custody = custody_verifier.verify(
                lease_id, expected_generation, serialized_authority.encode("utf-8")
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return _refusal(identity={"lease_id": lease_id}, reason=str(exc))
        return _result(
            identity={"lease_id": lease_id}, disposition="PROTECTED_SNAPSHOT_ONLY", custody=custody
        )

    @server.tool(name="admit_writer", structured_output=True)
    def admit_writer(candidate_path: str) -> dict[str, Any]:
        identity = {"candidate_path": candidate_path}
        lease_id = None
        before = None
        entered = False
        try:
            candidate = _repository_file(candidate_path, repo_root)
            expected = load_json(candidate)
            if expected.get("schema_version") != "coord.repo-set-lease.v2":
                raise ValueError("MCP writer admission requires a v2 lease candidate")
            lease_id = require_safe_id(expected["lease_id"], "lease_id")
            expected_digest = lease_candidate_sha256(expected)
            before = state(lease_id)
            if before["ownership_status"] == "UNKNOWN_FAIL_CLOSED":
                return uncertain(identity, "Existing custody requires reconciliation", before)
            with candidate_snapshot(expected) as snapshot:
                entered = True
                lease_path = acquire(snapshot, lock_root, repo_root=repo_root)
            admitted = load_json(lease_path)
            if (
                admitted.get("lease_id") != lease_id
                or lease_candidate_sha256(admitted) != expected_digest
            ):
                return uncertain(identity, "Published candidate identity mismatch", state(lease_id))
            authority = state(lease_id)
            if authority["ownership_status"] != "ACTIVE":
                return uncertain(identity, "Published custody is not settled ACTIVE", authority)
            serialized_authority = canonical_json_bytes(admitted).decode("utf-8")
        except _ReconciliationRequired as exc:
            return mutation_failure(identity, str(exc), lease_id, before, orphan=True)
        except (OSError, RuntimeError, KeyError, TypeError, ValueError) as exc:
            if entered:
                return mutation_failure(identity, str(exc), lease_id, before)
            return _refusal(identity=identity, reason=str(exc))
        return _result(
            identity={"lease_id": lease_id},
            disposition="OK",
            lease_path=str(lease_path),
            authority=authority,
            serialized_authority=serialized_authority,
        )

    @server.tool(name="release_writer", structured_output=True)
    def release_writer(
        lease_id: str, expected_generation: int, terminal_candidate_path: str
    ) -> dict[str, Any]:
        identity = {"lease_id": lease_id, "expected_generation": expected_generation}
        entered = False
        before = None
        try:
            require_safe_id(lease_id, "lease_id")
            terminal_candidate = _repository_file(terminal_candidate_path, repo_root)
            expected = load_json(terminal_candidate)
            if expected.get("lease_id") != lease_id:
                raise ValueError("Terminal candidate lease identity mismatch")
            before = state(lease_id)
            if before["ownership_status"] == "UNKNOWN_FAIL_CLOSED":
                return uncertain(identity, "Existing custody requires reconciliation", before)
            with candidate_snapshot(expected) as snapshot:
                entered = True
                lease_path = release(
                    lease_id,
                    lock_root,
                    expected_generation=expected_generation,
                    candidate_path=snapshot,
                    repo_root=repo_root,
                )
            custody = state(lease_id)
            if custody["ownership_status"] != "TERMINAL_RELEASED":
                return uncertain(identity, "Published release requires reconciliation", custody)
        except _ReconciliationRequired as exc:
            return mutation_failure(identity, str(exc), lease_id, before, orphan=True)
        except (OSError, RuntimeError, KeyError, TypeError, ValueError) as exc:
            if entered:
                return mutation_failure(identity, str(exc), lease_id, before)
            return _refusal(identity=identity, reason=str(exc))
        return _result(
            identity=identity, disposition="OK", lease_path=str(lease_path), custody=custody
        )

    return server


def main() -> None:
    import os

    lock_root = Path(os.environ["CLH_MCP_LOCK_ROOT"])
    repo_root = Path(os.environ["CLH_MCP_REPOSITORY_ROOT"])
    create_server(lock_root, repo_root).run("stdio")


if __name__ == "__main__":
    main()
