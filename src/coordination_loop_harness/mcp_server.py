"""Thin local STDIO MCP facade over CLH lease custody primitives."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from .leases import acquire, lease_candidate_sha256, observe, release
from .util import canonical_json_bytes, load_json


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


def create_server(lock_root: Path, repo_root: Path) -> MCPServer:
    """Create a transport adapter; lease validation and state stay owned by CLH."""
    lock_root = lock_root.resolve()
    repo_root = repo_root.resolve()
    server = MCPServer("coordination-loop-clh", version="1", title="CLH MCP facade")

    @server.tool(name="inspect_authority", structured_output=True)
    def inspect_authority(lease_id: str) -> dict[str, Any]:
        try:
            authority = observe(lease_id, lock_root, repo_root=repo_root)
        except (OSError, TypeError, ValueError) as exc:
            return _refusal(identity={"lease_id": lease_id}, reason=str(exc))
        return _result(identity={"lease_id": lease_id}, disposition="OK", authority=authority)

    @server.tool(name="inspect_custody", structured_output=True)
    def inspect_custody(lease_id: str) -> dict[str, Any]:
        try:
            custody = observe(lease_id, lock_root, repo_root=repo_root)
        except (OSError, TypeError, ValueError) as exc:
            return _refusal(identity={"lease_id": lease_id}, reason=str(exc))
        return _result(identity={"lease_id": lease_id}, disposition="OK", custody=custody)

    @server.tool(name="admit_writer", structured_output=True)
    def admit_writer(candidate_path: str) -> dict[str, Any]:
        identity = {"candidate_path": candidate_path}
        try:
            candidate = _repository_file(candidate_path, repo_root)
            expected = load_json(candidate)
            if expected.get("schema_version") != "coord.repo-set-lease.v2":
                raise ValueError("MCP writer admission requires a v2 lease candidate")
            expected_digest = lease_candidate_sha256(expected)
            # The core opens its candidate path independently. Give it a private
            # snapshot so a caller cannot swap the public path between reads.
            descriptor, snapshot_name = tempfile.mkstemp(
                prefix=".clh-mcp-candidate-", suffix=".json", dir=repo_root
            )
            snapshot = Path(snapshot_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(canonical_json_bytes(expected))
                    handle.flush()
                    os.fsync(handle.fileno())
                lease_path = acquire(snapshot, lock_root, repo_root=repo_root)
            finally:
                # A failed cleanup must not hide a lease that was already created.
                with suppress(OSError):
                    snapshot.unlink(missing_ok=True)
            lease_id = lease_path.name.removesuffix(".lease.json")
            admitted = load_json(lease_path)
            if admitted.get("lease_id") != lease_id:
                raise ValueError("Acquired lease record identity does not match its path")
            if lease_candidate_sha256(admitted) != expected_digest:
                raise ValueError("Acquired lease record does not match the admitted candidate")
            authority = observe(lease_id, lock_root, repo_root=repo_root)
            serialized_authority = canonical_json_bytes(admitted).decode("utf-8")
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
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
        try:
            terminal_candidate = _repository_file(terminal_candidate_path, repo_root)
            lease_path = release(
                lease_id,
                lock_root,
                expected_generation=expected_generation,
                candidate_path=terminal_candidate,
                repo_root=repo_root,
            )
            custody = observe(lease_id, lock_root, repo_root=repo_root)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return _refusal(identity=identity, reason=str(exc))
        return _result(
            identity=identity,
            disposition="OK",
            lease_path=str(lease_path),
            custody=custody,
        )

    return server


def main() -> None:
    import os

    lock_root = Path(os.environ["CLH_MCP_LOCK_ROOT"])
    repo_root = Path(os.environ["CLH_MCP_REPOSITORY_ROOT"])
    create_server(lock_root, repo_root).run("stdio")


if __name__ == "__main__":
    main()
