from __future__ import annotations

import os
import re
import secrets
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .decisions import verify_decision
from .repository import _repository_identity_snapshot, verify_repository
from .util import (
    admission_mutex,
    canonical_json_bytes,
    canonical_path,
    canonical_repo,
    canonical_repo_v2,
    canonical_scope,
    ensure_within,
    is_absolute_scope,
    is_native_absolute_scope,
    is_safe_json_integer,
    is_v2_absolute_scope,
    load_json,
    require_safe_id,
    sha256_bytes,
    sha256_file,
    utc_now,
    windows_device_scope_alias,
    write_json_atomic,
)
from .validation import repository_root, validate_document


@dataclass(frozen=True)
class Overlap:
    lease_id: str
    category: str
    value: str


_V2_SCHEMA = "coord.repo-set-lease.v2"
_V1_SCHEMA = "coord.repo-set-lease.v1"
_SUPPORTED_LEASE_SCHEMAS = {_V1_SCHEMA, _V2_SCHEMA}


def lease_candidate_sha256(data: dict[str, Any]) -> str:
    """Return the canonical digest used by a v2 owner decision."""

    return sha256_bytes(canonical_json_bytes(data))


def _timestamp(value: str, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical UTC timestamp") from exc
    if parsed.tzinfo != UTC:
        raise ValueError(f"{label} must be a canonical UTC timestamp")
    return parsed


def _merge_access(target: dict[str, str], value: str, mode: str) -> None:
    if target.get(value) == "WRITE" or mode == "WRITE":
        target[value] = "WRITE"
    else:
        target[value] = "READ"


def _combined_repository_access(sets: dict[str, dict[str, str]]) -> dict[str, str]:
    combined = dict(sets["repository"])
    for value, mode in sets["coordination_repository"].items():
        _merge_access(combined, value, mode)
    return combined


def _access_conflicts(left: str, right: str) -> bool:
    return left == "WRITE" or right == "WRITE"


def _validate_outcome_binding(data: dict[str, Any], repo_root: Path) -> None:
    outcome_ref = data.get("outcome_ref")
    if not isinstance(outcome_ref, str) or not outcome_ref or "\\" in outcome_ref:
        raise ValueError("A RELEASED v2 lease requires a repository-relative POSIX outcome_ref")
    if is_absolute_scope(outcome_ref):
        raise ValueError("A RELEASED v2 lease outcome_ref must be repository-relative")
    outcome_path = ensure_within(
        repo_root / outcome_ref,
        repo_root,
        label="lease outcome_ref",
        must_exist=True,
    )
    if not outcome_path.is_file():
        raise ValueError("A RELEASED v2 lease outcome_ref must identify a regular file")
    if data.get("outcome_sha256") != sha256_file(outcome_path):
        raise ValueError("Lease outcome SHA-256 binding mismatch")


def _terminal_record_is_valid(data: dict[str, Any], repo_root: Path | None = None) -> bool:
    schema_version = data.get("schema_version")
    if schema_version == _V1_SCHEMA:
        return _legacy_terminal_record_is_valid(data)
    if repo_root is None:
        return False
    try:
        if validate_document(data, repo_root):
            return False
        if schema_version == _V2_SCHEMA:
            _validate_lease(data, repo_root, allow_coordination_self_write=True)
            if data.get("state") != "RELEASED":
                return False
            _validate_terminal_release(data, repo_root)
            return True
    except (OSError, TypeError, ValueError):
        return False
    return False


def _legacy_record_is_valid(data: dict[str, Any]) -> bool:
    """Validate historical v1 structure without ambient schema authority."""

    required = {
        "schema_version",
        "lease_id",
        "run_id",
        "state",
        "generation",
        "created_utc",
        "owner",
        "coordination_repository",
        "repositories",
        "local_scopes",
        "infrastructure_scopes",
        "active_writer_repository",
        "decision_ref",
    }
    allowed = required | {"released_utc", "outcome_ref"}
    if not required.issubset(data) or not set(data).issubset(allowed):
        return False
    try:
        require_safe_id(data["lease_id"], "lease_id")
        require_safe_id(data["run_id"], "run_id")
    except (TypeError, ValueError):
        return False
    if (
        data.get("state") not in {"ACTIVE", "RELEASED"}
        or type(data.get("generation")) is not int
        or data["generation"] < 1
        or not isinstance(data.get("owner"), str)
        or not data["owner"]
        or not isinstance(data.get("created_utc"), str)
        or re.match(r"^\d{4}-\d{2}-\d{2}T", data["created_utc"]) is None
        or not _repository_name_is_structural(data.get("coordination_repository"))
        or not isinstance(data.get("decision_ref"), (str, type(None)))
        or not isinstance(data.get("released_utc"), (str, type(None)))
        or not isinstance(data.get("outcome_ref"), (str, type(None)))
    ):
        return False
    repositories = data.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        return False
    identities: list[str] = []
    writers: list[str] = []
    allowed_repository_keys = {
        "repository",
        "mode",
        "canonical_path",
        "worktree_root",
        "exact_sha",
    }
    for item in repositories:
        if (
            not isinstance(item, dict)
            or not {"repository", "mode"}.issubset(item)
            or not set(item).issubset(allowed_repository_keys)
            or not _repository_name_is_structural(item.get("repository"))
            or item.get("mode") not in {"READ", "WRITE"}
        ):
            return False
        for field in ("canonical_path", "worktree_root"):
            if item.get(field) is not None and not isinstance(item.get(field), str):
                return False
        exact_sha = item.get("exact_sha")
        if exact_sha is not None and (
            not isinstance(exact_sha, str) or re.fullmatch(r"[0-9a-f]{40}", exact_sha) is None
        ):
            return False
        identity = canonical_repo(item["repository"])
        identities.append(identity)
        if item["mode"] == "WRITE":
            writers.append(identity)
    if len(identities) != len(set(identities)):
        return False
    coordination = canonical_repo(data["coordination_repository"])
    active_writer = data.get("active_writer_repository")
    if data["state"] == "ACTIVE":
        if not isinstance(data.get("decision_ref"), str) or not data["decision_ref"]:
            return False
        if active_writer is None:
            if writers:
                return False
        elif (
            not isinstance(active_writer, str)
            or len(writers) != 1
            or canonical_repo(active_writer) != writers[0]
        ):
            return False
        if coordination in identities and writers != [coordination]:
            return False
    elif active_writer is not None:
        return False
    for field in ("local_scopes", "infrastructure_scopes"):
        values = data.get(field)
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) for value in values)
            or len(values) != len(set(values))
        ):
            return False
    return True


def _legacy_terminal_record_is_valid(data: dict[str, Any]) -> bool:
    return (
        _legacy_record_is_valid(data)
        and data.get("state") == "RELEASED"
        and isinstance(data.get("released_utc"), str)
        and bool(data["released_utc"])
        and isinstance(data.get("outcome_ref"), str)
        and bool(data["outcome_ref"])
    )


def _repository_name_is_structural(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[^/\s]+/[^/\s]+", value) is not None


def _active_leases(
    lock_root: Path,
    *,
    excluding_path: Path | None = None,
    repo_root: Path | None = None,
) -> list[tuple[Path, dict[str, Any], str]]:
    lock_root = canonical_path(lock_root)
    validation_root = repository_root(repo_root) if repo_root is not None else None
    excluded = canonical_path(excluding_path) if excluding_path is not None else None
    result: list[tuple[Path, dict[str, Any], str]] = []
    for discovered_path in sorted(lock_root.glob("*.lease.json")):
        inferred_id = discovered_path.name.removesuffix(".lease.json")
        try:
            path = ensure_within(
                discovered_path,
                lock_root,
                label="active lease file",
                must_exist=True,
            )
        except (OSError, TypeError, ValueError):
            result.append((discovered_path, {"state": "UNKNOWN_OPAQUE"}, inferred_id))
            continue
        if excluded is not None and path == excluded:
            continue
        try:
            if not path.is_file():
                raise ValueError("Lease record is not a regular file")
            data = load_json(path)
        except (OSError, TypeError, ValueError):
            # The canonical filename still supplies one exact identity even
            # when a torn record has no parseable resource set. Preserve that
            # lease-id conflict without inventing a machine-wide lock.
            result.append((path, {"state": "UNKNOWN_OPAQUE"}, inferred_id))
            continue
        if data.get("state") == "RELEASED" and data.get("schema_version") == _V1_SCHEMA:
            if data.get("lease_id") == inferred_id and _terminal_record_is_valid(data):
                continue
        if data.get("state") == "RELEASED" and data.get("schema_version") == _V2_SCHEMA:
            if (
                data.get("lease_id") == inferred_id
                and validation_root is not None
                and _terminal_record_is_valid(data, validation_root)
            ):
                continue
        result.append((path, data, inferred_id))
    return result


def _sets(lease: dict[str, Any]) -> dict[str, dict[str, str]]:
    shared_access = lease.get("schema_version") == _V2_SCHEMA
    repositories: dict[str, str] = {}
    paths: dict[str, str] = {}
    branches: dict[str, str] = {}
    raw_repositories = lease.get("repositories")
    repository_items = raw_repositories if isinstance(raw_repositories, list) else []
    for item in repository_items:
        if not isinstance(item, dict) or not isinstance(item.get("repository"), str):
            continue
        repository_identities = _repository_overlap_identities(item["repository"])
        mode = item.get("mode") if shared_access else "WRITE"
        if mode not in {"READ", "WRITE"}:
            mode = "WRITE"
        for identity in repository_identities:
            _merge_access(repositories, identity, mode)
        for key in ("canonical_path", "worktree_root"):
            if isinstance(item.get(key), str) and item[key].strip():
                for identity in _scope_overlap_identities(item[key]):
                    _merge_access(paths, identity, mode)
        branch_ref = item.get("branch_ref")
        if isinstance(branch_ref, str) and branch_ref.strip():
            for identity in repository_identities:
                _merge_access(branches, f"{identity}:{branch_ref.casefold()}", mode)
    raw_local_scopes = lease.get("local_scopes")
    local_scopes = raw_local_scopes if isinstance(raw_local_scopes, list) else []
    for item in local_scopes:
        if not isinstance(item, str) or not item.strip():
            continue
        for identity in _scope_overlap_identities(item):
            _merge_access(paths, identity, "WRITE")
    raw_infrastructure = lease.get("infrastructure_scopes")
    infrastructure_items = raw_infrastructure if isinstance(raw_infrastructure, list) else []
    infrastructure = {
        item.strip().casefold(): "WRITE"
        for item in infrastructure_items
        if isinstance(item, str) and item.strip()
    }
    coordination: dict[str, str] = {}
    if (
        isinstance(lease.get("coordination_repository"), str)
        and lease["coordination_repository"].strip()
    ):
        for coordination_repository in _repository_overlap_identities(
            lease["coordination_repository"]
        ):
            coordination_mode = repositories.get(coordination_repository, "READ")
            if not shared_access:
                coordination_mode = "WRITE"
            coordination[coordination_repository] = coordination_mode
    return {
        "repository": repositories,
        "path": paths,
        "branch": branches,
        "infrastructure": infrastructure,
        "coordination_repository": coordination,
    }


def _paths_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    left_prefix = left if left.endswith("/") else left + "/"
    right_prefix = right if right.endswith("/") else right + "/"
    return left.startswith(right_prefix) or right.startswith(left_prefix)


def _repository_overlap_identities(value: str) -> tuple[str, ...]:
    return tuple(sorted({canonical_repo(value), canonical_repo_v2(value)}))


def _scope_overlap_identities(value: str) -> tuple[str, ...]:
    """Return conservative host aliases for active-record overlap scanning."""

    identities: set[str] = set()
    try:
        identities.add(canonical_scope(value))
    except (OSError, TypeError, ValueError):
        pass
    raw = value.strip()
    device_alias = windows_device_scope_alias(raw)
    if device_alias is not None:
        try:
            identities.add(canonical_scope(device_alias))
        except (OSError, TypeError, ValueError):
            pass
    try:
        if os.name == "nt" and raw.startswith("/") and not raw.startswith("//"):
            identities.add(str(canonical_path(raw)).replace("\\", "/").casefold())
        elif os.name == "posix" and raw.startswith("//"):
            identities.add(str(canonical_path(raw)))
    except (OSError, TypeError, ValueError):
        pass
    return tuple(sorted(identity for identity in identities if identity))


def find_overlaps(
    candidate: dict[str, Any],
    lock_root: Path,
    *,
    excluding: str | None = None,
    excluding_path: Path | None = None,
    repo_root: Path | None = None,
) -> list[Overlap]:
    if excluding is not None:
        legacy_id = require_safe_id(excluding, "excluding lease_id")
        legacy_path = ensure_within(
            canonical_path(lock_root) / f"{legacy_id}.lease.json",
            canonical_path(lock_root),
            label="excluded lease file",
        )
        if excluding_path is not None and canonical_path(excluding_path) != legacy_path:
            raise ValueError("excluding and excluding_path identify different lease files")
        excluding_path = legacy_path
    candidate_sets = _sets(candidate)
    overlaps: list[Overlap] = []
    for _path, other, inferred_id in _active_leases(
        lock_root,
        excluding_path=excluding_path,
        repo_root=repo_root,
    ):
        other_sets = _sets(other)
        declared_id = other.get("lease_id")
        if not isinstance(declared_id, str):
            declared_id = None
        else:
            try:
                declared_id = require_safe_id(declared_id, "existing lease_id")
            except ValueError:
                declared_id = None
        lease_id = declared_id or inferred_id
        candidate_id = candidate.get("lease_id")
        if candidate_id in {inferred_id, declared_id}:
            overlaps.append(Overlap(lease_id, "lease_id", str(candidate_id)))
        candidate_repositories = _combined_repository_access(candidate_sets)
        other_repositories = _combined_repository_access(other_sets)
        for value in sorted(candidate_repositories.keys() & other_repositories.keys()):
            if _access_conflicts(candidate_repositories[value], other_repositories[value]):
                category = (
                    "coordination_repository"
                    if value in candidate_sets["coordination_repository"]
                    or value in other_sets["coordination_repository"]
                    else "repository"
                )
                overlaps.append(Overlap(lease_id, category, value))
        for category in ("branch", "infrastructure"):
            for value in sorted(candidate_sets[category].keys() & other_sets[category].keys()):
                if _access_conflicts(candidate_sets[category][value], other_sets[category][value]):
                    overlaps.append(Overlap(lease_id, category, value))
        for left, left_mode in sorted(candidate_sets["path"].items()):
            for right, right_mode in sorted(other_sets["path"].items()):
                if _paths_overlap(left, right) and _access_conflicts(left_mode, right_mode):
                    overlaps.append(Overlap(lease_id, "path", f"{left} <-> {right}"))
    return overlaps


def _validate_coordination_self_write(
    data: dict[str, Any], repositories: list[dict[str, Any]], coordination: str
) -> None:
    """Validate the narrow finalization exception for coordination repositories.

    A coordination repository is normally only the durable mailbox.  A phase
    replacement may make it the sole writer to record a final outcome, provided
    the candidate remains exactly bound and cannot reserve another write surface.
    """

    canonicalizer = (
        canonical_repo_v2 if data.get("schema_version") == _V2_SCHEMA else canonical_repo
    )
    coordination_entries = [
        item for item in repositories if canonicalizer(item["repository"]) == coordination
    ]
    if len(coordination_entries) != 1:
        raise ValueError(
            "Coordination self-write requires exactly one coordination repository entry"
        )
    coordination_entry = coordination_entries[0]
    if coordination_entry.get("mode") != "WRITE":
        raise ValueError("Coordination self-write requires the coordination repository to be WRITE")
    if (
        data.get("active_writer_repository") is None
        or canonicalizer(data["active_writer_repository"]) != coordination
    ):
        raise ValueError(
            "Coordination self-write requires active_writer_repository to match "
            "coordination_repository"
        )
    writers = [
        canonicalizer(item["repository"]) for item in repositories if item.get("mode") == "WRITE"
    ]
    if writers != [coordination]:
        raise ValueError(
            "Coordination self-write requires the coordination repository to be the sole WRITE "
            "repository"
        )
    if any(
        canonicalizer(item["repository"]) != coordination and item.get("mode") != "READ"
        for item in repositories
    ):
        raise ValueError("Coordination self-write requires every other repository to be READ")
    for binding in ("canonical_path", "worktree_root", "exact_sha"):
        value = coordination_entry.get(binding)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"Coordination self-write requires exact coordination repository binding: {binding}"
            )
    if data.get("state") != "ACTIVE":
        raise ValueError("Coordination self-write requires state=ACTIVE")


def _is_coordination_self_write(data: dict[str, Any]) -> bool:
    coordination = data.get("coordination_repository")
    repositories = data.get("repositories")
    if not isinstance(coordination, str) or not isinstance(repositories, list):
        return False
    canonicalizer = (
        canonical_repo_v2 if data.get("schema_version") == _V2_SCHEMA else canonical_repo
    )
    coordination_identity = canonicalizer(coordination)
    return any(
        isinstance(item, dict)
        and isinstance(item.get("repository"), str)
        and canonicalizer(item["repository"]) == coordination_identity
        and item.get("mode") == "WRITE"
        for item in repositories
    )


def _validate_v2_lifecycle(data: dict[str, Any]) -> None:
    generation = data.get("generation")
    if not is_safe_json_integer(generation) or generation < 1:
        raise ValueError("A v2 lease generation must be a positive safe integer")
    created = _timestamp(data.get("created_utc"), label="created_utc")
    heartbeat = _timestamp(data.get("heartbeat_utc"), label="heartbeat_utc")
    expires = _timestamp(data.get("expires_utc"), label="expires_utc")
    if heartbeat < created:
        raise ValueError("heartbeat_utc must not precede created_utc")
    if expires <= heartbeat:
        raise ValueError("expires_utc must be later than heartbeat_utc")
    state = data.get("state")
    if state not in {"ACTIVE", "RELEASED"}:
        raise ValueError("A v2 lease state must be ACTIVE or RELEASED")
    repositories = data.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise ValueError("A v2 lease requires at least one repository binding")
    writers: list[str] = []
    for item in repositories:
        if not isinstance(item, dict) or not isinstance(item.get("repository"), str):
            raise ValueError("A v2 lease contains an invalid repository binding")
        if item.get("mode") == "WRITE":
            writers.append(canonical_repo_v2(item["repository"]))
        for key in ("canonical_path", "worktree_root"):
            value = item.get(key)
            if value is not None and (
                not isinstance(value, str) or not is_v2_absolute_scope(value)
            ):
                raise ValueError(f"A v2 lease requires an absolute {key}")
            if value is not None:
                try:
                    canonical_scope(value)
                except (OSError, TypeError, ValueError) as exc:
                    raise ValueError(f"A v2 lease contains an invalid {key}: {exc}") from exc
    if len(writers) > 1:
        raise ValueError("A v2 lease cannot contain more than one WRITE repository")
    for value in data.get("local_scopes", []):
        if not isinstance(value, str) or not is_v2_absolute_scope(value):
            raise ValueError("A v2 lease requires absolute local_scopes")
        try:
            canonical_scope(value)
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(f"A v2 lease contains an invalid local_scope: {exc}") from exc
    active_writer = data.get("active_writer_repository")
    if state == "ACTIVE":
        terminal_fields = (
            "release_decision_ref",
            "release_authority",
            "released_utc",
            "outcome_ref",
            "outcome_sha256",
        )
        if any(data.get(field) is not None for field in terminal_fields):
            raise ValueError("An ACTIVE v2 lease cannot contain terminal release fields")
        if not data.get("decision_ref"):
            raise ValueError("An ACTIVE v2 lease requires decision_ref")
        if active_writer is None:
            if writers:
                raise ValueError(
                    "A lease with no active writer must not contain WRITE repositories"
                )
        elif (
            not isinstance(active_writer, str)
            or len(writers) != 1
            or canonical_repo_v2(active_writer) != writers[0]
        ):
            raise ValueError(
                "ACTIVE leases require exactly one WRITE repository matching "
                "active_writer_repository"
            )
    else:
        if data.get("generation", 0) < 2:
            raise ValueError("A RELEASED v2 lease generation must be at least 2")
        released_utc = data.get("released_utc")
        if (
            released_utc is None
            or not data.get("release_decision_ref")
            or data.get("release_authority") not in {"NORMAL", "STALE_RECOVERY"}
            or not data.get("outcome_ref")
            or not data.get("outcome_sha256")
        ):
            raise ValueError(
                "A RELEASED v2 lease requires release authority, decision, and outcome binding"
            )
        released = _timestamp(released_utc, label="released_utc")
        if released < heartbeat:
            raise ValueError("released_utc must not precede heartbeat_utc")
        if data.get("release_authority") == "NORMAL" and released > expires:
            raise ValueError("A NORMAL release cannot record a timestamp after expiry")
        if data.get("release_authority") == "STALE_RECOVERY" and released <= expires:
            raise ValueError("A STALE_RECOVERY release must record a timestamp after expiry")
        if active_writer is not None:
            raise ValueError("A RELEASED lease must not have an active writer")


def _git_path(root: Path, name: str) -> Path:
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-C",
            str(root),
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            name,
        ],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"git path verification failed: {detail}")
    return canonical_path(result.stdout.strip())


def _writer_branch_name(branch_ref: str) -> str:
    if not isinstance(branch_ref, str) or not branch_ref.startswith("refs/heads/"):
        raise ValueError("Writer branch_ref is not a valid local branch reference")
    branch = branch_ref.removeprefix("refs/heads/")
    ref_check = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if ref_check.returncode != 0:
        raise ValueError("Writer branch_ref is not a valid local branch reference")
    return branch


def _writer_branch_ref(data: dict[str, Any], writer: dict[str, Any]) -> str:
    branch_ref = writer.get("branch_ref")
    if isinstance(branch_ref, str):
        _writer_branch_name(branch_ref)
        return branch_ref
    if data.get("schema_version") != _V1_SCHEMA or not _is_coordination_self_write(data):
        raise ValueError("Writer branch_ref is not a valid local branch reference")
    writer_root = canonical_path(writer["worktree_root"], must_exist=True)
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(writer_root), "symbolic-ref", "--quiet", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode != 0:
        raise ValueError("Legacy coordination self-write requires an attached writer branch")
    branch_ref = result.stdout.strip()
    _writer_branch_name(branch_ref)
    return branch_ref


def _git_metadata_roots(root: Path) -> tuple[Path, ...]:
    roots: set[Path] = set()
    for arguments in (
        ("--absolute-git-dir",),
        ("--path-format=absolute", "--git-common-dir"),
    ):
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), "rev-parse", *arguments],
            check=False,
            capture_output=True,
            text=True,
            shell=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ValueError(f"git metadata root verification failed: {detail}")
        roots.add(canonical_path(result.stdout.strip(), must_exist=True))
    return tuple(sorted(roots, key=str))


def _path_within_any_root(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _writer_git_lock_paths(data: dict[str, Any]) -> tuple[Path, ...]:
    schema_version = data.get("schema_version")
    needs_binding = schema_version == _V2_SCHEMA or (
        schema_version == _V1_SCHEMA and _is_coordination_self_write(data)
    )
    if not needs_binding or data.get("state") != "ACTIVE":
        return ()
    writers = [item for item in data["repositories"] if item["mode"] == "WRITE"]
    if not writers:
        return ()
    writer = writers[0]
    writer_root = canonical_path(writer["worktree_root"], must_exist=True)
    # Validate the untrusted ref before passing it to `git --git-path`; Git
    # deliberately resolves `..` components and could otherwise point outside
    # its metadata directories.
    branch = _writer_branch_name(_writer_branch_ref(data, writer))
    metadata_roots = _git_metadata_roots(writer_root)
    names = (
        "index.lock",
        "HEAD.lock",
        "config.lock",
        "config.worktree.lock",
        "locked",
        "packed-refs.lock",
        f"refs/heads/{branch}.lock",
    )
    paths = {_git_path(writer_root, name) for name in names}
    for path in paths:
        if not _path_within_any_root(path, metadata_roots):
            raise ValueError(f"Git admission lock must stay within Git metadata roots: {path}")
    return tuple(sorted(paths, key=str))


@contextmanager
def _writer_git_admission_guard(
    data: dict[str, Any],
) -> Iterator[dict[Path, tuple[tuple[int, int], str]]]:
    """Hold Git-native mutation guards across final admission publication."""

    paths = _writer_git_lock_paths(data)
    handles: list[tuple[Path, Any, tuple[int, int], str]] = []
    created_directories: list[Path] = []
    body_error: BaseException | None = None
    try:
        for path in paths:
            missing: list[Path] = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            if not parent.is_dir():
                raise ValueError(f"Git lock parent is not a directory: {parent}")
            for directory in reversed(missing):
                try:
                    directory.mkdir()
                except FileExistsError as exc:
                    if not directory.is_dir():
                        raise ValueError(
                            f"Git lock parent is not a directory: {directory}"
                        ) from exc
                else:
                    created_directories.append(directory)
            try:
                handle = path.open("x", encoding="utf-8", newline="\n")
            except FileExistsError as exc:
                raise ValueError(f"writer worktree has an active Git lock: {path}") from exc
            marker = (
                f"coord.repo-set-lease admission pid={os.getpid()} nonce={secrets.token_hex(16)}\n"
            )
            stat = os.fstat(handle.fileno())
            handles.append((path, handle, (stat.st_dev, stat.st_ino), marker))
            handle.write(marker)
            handle.flush()
            os.fsync(handle.fileno())
        yield {path: (identity, marker) for path, _, identity, marker in handles}
    except BaseException as exc:
        body_error = exc
        raise
    finally:
        cleanup_errors: list[str] = []
        for path, handle, identity, marker in reversed(handles):
            try:
                handle.close()
            except OSError as exc:
                cleanup_errors.append(f"owned Git admission lock cannot be closed: {path}: {exc}")
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                cleanup_errors.append(f"owned Git admission lock disappeared: {path}")
                continue
            except OSError as exc:
                cleanup_errors.append(f"owned Git admission lock cannot be stated: {path}: {exc}")
                continue
            if (stat.st_dev, stat.st_ino) != identity:
                cleanup_errors.append(f"owned Git admission lock identity changed: {path}")
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                cleanup_errors.append(f"owned Git admission lock cannot be read: {path}: {exc}")
                continue
            if content != marker:
                cleanup_errors.append(f"owned Git admission lock content changed: {path}")
                continue
            try:
                path.unlink()
            except OSError as exc:
                cleanup_errors.append(f"owned Git admission lock cannot be removed: {path}: {exc}")
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except FileNotFoundError:
                continue
            except OSError:
                # A concurrent legitimate Git operation may have populated a
                # directory we created. Never remove non-empty Git metadata.
                pass
        if cleanup_errors:
            detail = "; ".join(cleanup_errors)
            if body_error is not None:
                body_error.add_note(f"Git admission lock cleanup also failed: {detail}")
            else:
                raise RuntimeError(detail)


def _validate_writer_binding(
    data: dict[str, Any],
    *,
    admitted_git_locks: dict[Path, tuple[tuple[int, int], str]] | None = None,
) -> None:
    schema_version = data.get("schema_version")
    needs_binding = schema_version == _V2_SCHEMA or (
        schema_version == _V1_SCHEMA and _is_coordination_self_write(data)
    )
    if not needs_binding or data.get("state") != "ACTIVE":
        return
    writers = [item for item in data["repositories"] if item["mode"] == "WRITE"]
    if not writers:
        return
    writer = writers[0]
    canonical_root = canonical_path(writer["canonical_path"], must_exist=True)
    writer_root = canonical_path(writer["worktree_root"], must_exist=True)
    branch = _writer_branch_name(_writer_branch_ref(data, writer))
    canonical_identity = _repository_identity_snapshot(canonical_root)
    writer_identity = _repository_identity_snapshot(writer_root)
    if (
        canonical_identity["common_dir_path"] != writer_identity["common_dir_path"]
        or canonical_identity["common_dir_identity"] != writer_identity["common_dir_identity"]
    ):
        raise ValueError("Writer worktree must belong to the canonical repository common Git dir")
    canonical_result = verify_repository(
        canonical_root,
        expected_origin=writer["repository"],
        offline=True,
    )
    if canonical_result["findings"]:
        raise ValueError(
            "Canonical repository binding failed:\n- " + "\n- ".join(canonical_result["findings"])
        )
    writer_result = verify_repository(
        writer_root,
        expected_origin=writer["repository"],
        stable_branch=branch if schema_version == _V2_SCHEMA else None,
        expected_sha=writer["exact_sha"],
        require_detached=False,
        offline=True,
    )
    findings = list(writer_result["findings"])
    if writer_result["tracked_dirty"]:
        findings.append("writer worktree has tracked changes")
    if writer_result["untracked"]:
        findings.append("writer worktree has untracked files")
    final_canonical_identity = _repository_identity_snapshot(canonical_root)
    final_writer_identity = _repository_identity_snapshot(writer_root)
    if canonical_identity != final_canonical_identity:
        findings.append("canonical repository filesystem identity changed during verification")
    if writer_identity != final_writer_identity:
        findings.append("writer worktree filesystem identity changed during verification")
    expected_git_locks = set(_writer_git_lock_paths(data))
    if admitted_git_locks is None:
        for lock_path in expected_git_locks:
            if lock_path.exists() or lock_path.is_symlink():
                findings.append(f"writer worktree has an active Git lock: {lock_path}")
    else:
        if expected_git_locks != set(admitted_git_locks):
            findings.append("writer Git metadata lock paths changed during admission")
        for lock_path, (identity, marker) in admitted_git_locks.items():
            try:
                metadata = lock_path.lstat()
                content = lock_path.read_text(encoding="utf-8")
            except OSError as exc:
                findings.append(f"owned Git admission lock is unavailable: {lock_path}: {exc}")
                continue
            if not stat.S_ISREG(metadata.st_mode):
                findings.append(f"owned Git admission lock is not a regular file: {lock_path}")
            if (metadata.st_dev, metadata.st_ino) != identity:
                findings.append(f"owned Git admission lock identity changed: {lock_path}")
            if content != marker:
                findings.append(f"owned Git admission lock content changed: {lock_path}")
    if findings:
        raise ValueError("Writer repository binding failed:\n- " + "\n- ".join(findings))


def _validate_decision_scope(data: dict[str, Any], decision: dict[str, Any]) -> None:
    if data.get("schema_version") != _V2_SCHEMA:
        return
    supplied = decision.get("scope")
    if not isinstance(supplied, list):
        raise ValueError("Lease decision scope is not a list")
    if any(not isinstance(scope, str) or not scope or scope != scope.strip() for scope in supplied):
        raise ValueError("Lease decision scope entries must be non-empty canonical strings")
    canonical_entries = [_canonical_decision_scope_entry(scope) for scope in supplied]
    if supplied != canonical_entries:
        raise ValueError("Lease decision scope entries must use canonical resource identity")
    if len(canonical_entries) != len(set(canonical_entries)):
        raise ValueError("Lease decision scope entries must be unique")
    scopes = set(canonical_entries)
    resource_sets = _sets(data)
    required = {resource for category in resource_sets.values() for resource in category}
    missing = sorted(required - scopes)
    if missing:
        raise ValueError(
            "Lease decision scope does not include every reserved resource:\n- "
            + "\n- ".join(missing)
        )
    expected_digest = decision.get("lease_candidate_sha256")
    actual_digest = lease_candidate_sha256(data)
    if expected_digest != actual_digest:
        raise ValueError(
            "Lease decision candidate SHA-256 binding mismatch: "
            f"expected {actual_digest}, found {expected_digest}"
        )


def _canonical_decision_scope_entry(scope: str) -> str:
    if is_v2_absolute_scope(scope):
        return canonical_scope(scope)
    if is_absolute_scope(scope):
        raise ValueError("Lease decision path scope is not valid v2 absolute-path syntax")
    branch_marker = ":refs/heads/"
    lowered = scope.casefold()
    marker_index = lowered.find(branch_marker)
    if marker_index > 0:
        repository = scope[:marker_index]
        branch = scope[marker_index + 1 :]
        return f"{_canonical_v2_repository_scope(repository)}:{branch.casefold()}"
    if ":" in scope:
        return scope.casefold()
    if scope.count("/") == 1:
        return _canonical_v2_repository_scope(scope)
    return scope.casefold()


def _canonical_v2_repository_scope(value: str) -> str:
    canonical = canonical_repo_v2(value)
    if re.fullmatch(
        r"[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*",
        canonical,
    ) is None or canonical.endswith(".git"):
        raise ValueError("Lease decision repository scope is not canonical owner/name syntax")
    return canonical


def _validate_terminal_release(
    data: dict[str, Any],
    repo_root: Path,
    *,
    observed_now: datetime | None = None,
) -> None:
    now = observed_now or _timestamp(utc_now(), label="current_utc")
    if _timestamp(data["released_utc"], label="released_utc") > now:
        raise ValueError("A terminal release cannot have a future released_utc")
    _validate_outcome_binding(data, repo_root)
    release_ref = data.get("release_decision_ref")
    if not isinstance(release_ref, str) or not release_ref:
        raise ValueError("A RELEASED v2 lease requires release_decision_ref")
    release_path = ensure_within(
        repo_root / release_ref,
        repo_root,
        label="lease release_decision_ref",
        must_exist=True,
    )
    authority = data.get("release_authority")
    action = "lease:release-stale" if authority == "STALE_RECOVERY" else "lease:release"
    decision = verify_decision(
        repo_root,
        release_path,
        run_id=data["run_id"],
        action=action,
        lease_id=data["lease_id"],
        lease_generation=data["generation"],
        require_candidate_digest=True,
    )
    if not decision["ok"]:
        raise ValueError(
            "Lease release decision verification failed:\n- " + "\n- ".join(decision["findings"])
        )
    if decision.get("authorized_actions") != [action]:
        raise ValueError("Release decision must authorize exactly one terminal action")
    _validate_decision_scope(data, decision)
    if decision.get("sequence") != data["generation"]:
        raise ValueError("Release decision sequence must match the terminal lease generation")
    active_ref = data.get("decision_ref")
    previous_ref = decision.get("previous_decision_ref")
    if not isinstance(active_ref, str) or not isinstance(previous_ref, str):
        raise ValueError("Release decision must directly follow the active lease decision")
    active_path = ensure_within(
        repo_root / active_ref,
        repo_root,
        label="active lease decision_ref",
        must_exist=True,
    )
    previous_path = ensure_within(
        repo_root / previous_ref,
        repo_root,
        label="release previous_decision_ref",
        must_exist=True,
    )
    if active_path != previous_path:
        raise ValueError("Release decision does not directly follow the active lease decision")
    active_candidate = dict(data)
    active_candidate["state"] = "ACTIVE"
    active_candidate["generation"] = data["generation"] - 1
    writers = [item["repository"] for item in data["repositories"] if item.get("mode") == "WRITE"]
    active_candidate["active_writer_repository"] = writers[0] if writers else None
    active_candidate["release_decision_ref"] = None
    active_candidate["release_authority"] = None
    active_candidate["released_utc"] = None
    active_candidate["outcome_ref"] = None
    active_candidate["outcome_sha256"] = None
    active_action = "lease:acquire" if active_candidate["generation"] == 1 else "lease:expand"
    active_decision = verify_decision(
        repo_root,
        active_path,
        run_id=data["run_id"],
        action=active_action,
        lease_id=data["lease_id"],
        lease_generation=active_candidate["generation"],
        require_candidate_digest=True,
    )
    if not active_decision["ok"]:
        raise ValueError(
            "Active lease decision verification failed:\n- "
            + "\n- ".join(active_decision["findings"])
        )
    _validate_decision_scope(active_candidate, active_decision)
    if active_decision.get("sequence") != active_candidate["generation"]:
        raise ValueError("Active decision sequence must match the active lease generation")


def _validate_lease(
    data: dict[str, Any],
    repo_root: Path,
    *,
    allow_coordination_self_write: bool = False,
    require_native_paths: bool = False,
) -> None:
    errors = validate_document(data, repo_root)
    if errors:
        raise ValueError("Lease validation failed:\n- " + "\n- ".join(errors))
    if data.get("schema_version") == _V2_SCHEMA:
        _validate_v2_lifecycle(data)
        if require_native_paths:
            for item in data["repositories"]:
                for key in ("canonical_path", "worktree_root"):
                    value = item.get(key)
                    if value is not None and not is_native_absolute_scope(value):
                        raise ValueError(f"A v2 lease requires a host-native absolute {key}")
            for value in data["local_scopes"]:
                if not is_native_absolute_scope(value):
                    raise ValueError("A v2 lease requires host-native absolute local_scopes")

    repositories = data.get("repositories", [])
    canonicalizer = (
        canonical_repo_v2 if data.get("schema_version") == _V2_SCHEMA else canonical_repo
    )
    identities = [canonicalizer(item["repository"]) for item in repositories]
    if len(identities) != len(set(identities)):
        raise ValueError("Lease repositories must be unique")
    coordination = canonicalizer(data["coordination_repository"])
    if coordination in identities:
        if not allow_coordination_self_write:
            raise ValueError("The coordination repository cannot also be a product repository")
        if data.get("state") == "ACTIVE":
            _validate_coordination_self_write(data, repositories, coordination)
        else:
            writers = [
                canonicalizer(item["repository"])
                for item in repositories
                if item.get("mode") == "WRITE"
            ]
            if writers != [coordination]:
                raise ValueError(
                    "A released coordination self-write lease must preserve its sole WRITE identity"
                )

    if data.get("schema_version") != _V2_SCHEMA:
        writers = [
            canonicalizer(item["repository"])
            for item in repositories
            if item.get("mode") == "WRITE"
        ]
        active_writer = data.get("active_writer_repository")
        if data.get("state") == "ACTIVE":
            if not data.get("decision_ref"):
                raise ValueError("An ACTIVE lease requires decision_ref")
            if active_writer is None:
                if writers:
                    raise ValueError(
                        "A lease with no active writer must not contain WRITE repositories"
                    )
            elif len(writers) != 1 or canonicalizer(active_writer) != writers[0]:
                raise ValueError(
                    "ACTIVE leases require exactly one WRITE repository matching "
                    "active_writer_repository"
                )
        elif active_writer is not None:
            raise ValueError("A RELEASED lease must not have an active writer")


def acquire(candidate_path: Path, lock_root: Path, *, repo_root: Path | None = None) -> Path:
    repo_root = repository_root(repo_root)
    lock_root = canonical_path(lock_root)
    candidate = load_json(candidate_path)
    _validate_lease(candidate, repo_root, require_native_paths=True)
    is_v2 = candidate.get("schema_version") == _V2_SCHEMA
    if candidate["state"] != "ACTIVE" or candidate["generation"] != 1:
        raise ValueError("A new lease must have state=ACTIVE and generation=1")
    decision_path = ensure_within(
        repo_root / candidate["decision_ref"],
        repo_root,
        label="lease decision_ref",
    )
    decision = verify_decision(
        repo_root,
        decision_path,
        run_id=candidate["run_id"],
        action="lease:acquire",
        lease_id=candidate["lease_id"],
        lease_generation=1,
        require_candidate_digest=is_v2,
    )
    if not decision["ok"]:
        raise ValueError(
            "Lease decision verification failed:\n- " + "\n- ".join(decision["findings"])
        )
    _validate_decision_scope(candidate, decision)
    if is_v2 and decision.get("sequence") != candidate["generation"]:
        raise ValueError("Acquisition decision sequence must match the lease generation")
    _validate_writer_binding(candidate)
    lease_path = ensure_within(
        lock_root / f"{candidate['lease_id']}.lease.json",
        lock_root,
        label="new lease file",
    )

    with admission_mutex(lock_root):
        decision = verify_decision(
            repo_root,
            decision_path,
            run_id=candidate["run_id"],
            action="lease:acquire",
            lease_id=candidate["lease_id"],
            lease_generation=1,
            require_candidate_digest=is_v2,
        )
        if not decision["ok"]:
            raise ValueError(
                "Lease decision verification failed:\n- " + "\n- ".join(decision["findings"])
            )
        _validate_decision_scope(candidate, decision)
        if is_v2 and decision.get("sequence") != candidate["generation"]:
            raise ValueError("Acquisition decision sequence must match the lease generation")
        with _writer_git_admission_guard(candidate) as git_locks:
            _validate_writer_binding(candidate, admitted_git_locks=git_locks)
            overlaps = find_overlaps(candidate, lock_root, repo_root=repo_root)
            if overlaps:
                detail = "; ".join(
                    f"{item.category}={item.value} held by {item.lease_id}" for item in overlaps
                )
                raise RuntimeError(f"Repository-set overlap detected: {detail}")
            _validate_writer_binding(candidate, admitted_git_locks=git_locks)
            write_json_atomic(lease_path, candidate, create_new=True, trusted_root=lock_root)
    return lease_path


def replace(
    candidate_path: Path,
    lock_root: Path,
    *,
    expected_generation: int,
    repo_root: Path | None = None,
) -> Path:
    repo_root = repository_root(repo_root)
    lock_root = canonical_path(lock_root)
    candidate = load_json(candidate_path)
    _validate_lease(
        candidate,
        repo_root,
        allow_coordination_self_write=True,
        require_native_paths=True,
    )
    lease_path = ensure_within(
        lock_root / f"{candidate['lease_id']}.lease.json",
        lock_root,
        label="replacement lease file",
    )

    with admission_mutex(lock_root):
        current = load_json(lease_path)
        if current.get("lease_id") != candidate["lease_id"]:
            raise RuntimeError("Lease file is not bound to the requested lease_id")
        if current.get("state") != "ACTIVE":
            raise RuntimeError("Only an ACTIVE lease can be replaced")
        if current.get("generation") != expected_generation:
            raise RuntimeError(
                f"Lease generation mismatch: expected {expected_generation}, "
                f"found {current.get('generation')}"
            )
        is_v2 = candidate.get("schema_version") == _V2_SCHEMA
        if is_v2:
            if current.get("schema_version") != _V2_SCHEMA:
                raise ValueError("A v2 replacement requires an existing v2 lease")
            _validate_lease(
                current,
                repo_root,
                allow_coordination_self_write=True,
                require_native_paths=True,
            )
            for field in (
                "schema_version",
                "run_id",
                "owner",
                "created_utc",
                "coordination_repository",
            ):
                if candidate.get(field) != current.get(field):
                    raise ValueError(f"Replacement must preserve lease identity field: {field}")
        else:
            current_schema = current.get("schema_version")
            candidate_schema = candidate.get("schema_version")
            if current_schema not in _SUPPORTED_LEASE_SCHEMAS:
                raise ValueError(f"Unsupported current lease schema_version: {current_schema}")
            if current_schema != candidate_schema:
                raise ValueError("Replacement must preserve lease schema_version")
        if candidate.get("generation") != expected_generation + 1:
            raise ValueError("Replacement generation must equal expected_generation + 1")
        if candidate.get("state") != "ACTIVE":
            raise ValueError("Use release() to close a lease")
        if not candidate.get("decision_ref"):
            raise ValueError("Lease replacement requires decision_ref")
        decision_path = ensure_within(
            repo_root / candidate["decision_ref"],
            repo_root,
            label="lease decision_ref",
        )
        decision = verify_decision(
            repo_root,
            decision_path,
            run_id=candidate["run_id"],
            action="lease:expand",
            lease_id=candidate["lease_id"],
            lease_generation=candidate["generation"],
            require_candidate_digest=is_v2,
        )
        if not decision["ok"]:
            raise ValueError(
                "Lease decision verification failed:\n- " + "\n- ".join(decision["findings"])
            )
        _validate_decision_scope(candidate, decision)
        if is_v2:
            current_decision_ref = current.get("decision_ref")
            previous_decision_ref = decision.get("previous_decision_ref")
            if not isinstance(current_decision_ref, str) or not isinstance(
                previous_decision_ref, str
            ):
                raise ValueError("Replacement decision must reference the current lease decision")
            current_decision_path = ensure_within(
                repo_root / current_decision_ref,
                repo_root,
                label="current lease decision_ref",
                must_exist=True,
            )
            current_action = "lease:acquire" if current["generation"] == 1 else "lease:expand"
            current_decision = verify_decision(
                repo_root,
                current_decision_path,
                run_id=current["run_id"],
                action=current_action,
                lease_id=current["lease_id"],
                lease_generation=current["generation"],
                require_candidate_digest=True,
            )
            if not current_decision["ok"]:
                raise ValueError(
                    "Current lease decision verification failed:\n- "
                    + "\n- ".join(current_decision["findings"])
                )
            _validate_decision_scope(current, current_decision)
            if current_decision.get("sequence") != current["generation"]:
                raise ValueError("Current decision sequence must match the lease generation")
            previous_decision_path = ensure_within(
                repo_root / previous_decision_ref,
                repo_root,
                label="replacement previous_decision_ref",
                must_exist=True,
            )
            if current_decision_path != previous_decision_path:
                raise ValueError(
                    "Replacement decision does not directly follow the current decision"
                )
            if decision.get("sequence") != candidate["generation"]:
                raise ValueError("Replacement decision sequence must match the lease generation")
        with _writer_git_admission_guard(candidate) as git_locks:
            _validate_writer_binding(candidate, admitted_git_locks=git_locks)
            overlaps = find_overlaps(
                candidate,
                lock_root,
                excluding_path=lease_path,
                repo_root=repo_root,
            )
            if overlaps:
                detail = "; ".join(
                    f"{item.category}={item.value} held by {item.lease_id}" for item in overlaps
                )
                raise RuntimeError(f"Repository-set overlap detected: {detail}")
            _validate_writer_binding(candidate, admitted_git_locks=git_locks)
            write_json_atomic(lease_path, candidate, trusted_root=lock_root)
    return lease_path


def release(
    lease_id: str,
    lock_root: Path,
    *,
    expected_generation: int,
    outcome_ref: str | None = None,
    candidate_path: Path | None = None,
    repo_root: Path | None = None,
) -> Path:
    lease_id = require_safe_id(lease_id, "lease_id")
    lock_root = canonical_path(lock_root)
    lease_path = ensure_within(
        lock_root / f"{lease_id}.lease.json",
        lock_root,
        label="released lease file",
    )
    with admission_mutex(lock_root):
        current = load_json(lease_path)
        if current.get("lease_id") != lease_id:
            raise RuntimeError("Lease file is not bound to the requested lease_id")
        if current.get("state") != "ACTIVE":
            raise RuntimeError("Lease is not ACTIVE")
        if current.get("generation") != expected_generation:
            raise RuntimeError(
                f"Lease generation mismatch: expected {expected_generation}, "
                f"found {current.get('generation')}"
            )
        current_schema = current.get("schema_version")
        if current_schema == _V2_SCHEMA:
            repo_root = repository_root(repo_root)
            _validate_lease(
                current,
                repo_root,
                allow_coordination_self_write=True,
                require_native_paths=True,
            )
            if candidate_path is None:
                raise ValueError("A v2 release requires an exact terminal candidate")
            candidate = load_json(candidate_path)
            _validate_lease(
                candidate,
                repo_root,
                allow_coordination_self_write=True,
                require_native_paths=True,
            )
            if candidate.get("lease_id") != lease_id:
                raise ValueError("Terminal candidate lease_id does not match the requested lease")
            if candidate.get("state") != "RELEASED":
                raise ValueError("A v2 terminal candidate must have state=RELEASED")
            if candidate.get("generation") != expected_generation + 1:
                raise ValueError("Terminal candidate generation must equal expected_generation + 1")
            immutable_fields = (
                "schema_version",
                "lease_id",
                "run_id",
                "created_utc",
                "heartbeat_utc",
                "expires_utc",
                "owner",
                "coordination_repository",
                "repositories",
                "local_scopes",
                "infrastructure_scopes",
                "decision_ref",
            )
            for field in immutable_fields:
                if candidate.get(field) != current.get(field):
                    raise ValueError(f"Terminal candidate must preserve lease field: {field}")
            with _writer_git_admission_guard(current) as git_locks:
                _validate_writer_binding(current, admitted_git_locks=git_locks)
                now = _timestamp(utc_now(), label="current_utc")
                released = _timestamp(candidate["released_utc"], label="released_utc")
                if released > now:
                    raise ValueError("Terminal candidate released_utc cannot be in the future")
                expected_authority = (
                    "STALE_RECOVERY"
                    if now > _timestamp(current["expires_utc"], label="expires_utc")
                    else "NORMAL"
                )
                if candidate.get("release_authority") != expected_authority:
                    raise ValueError(
                        "Terminal candidate release authority mismatch: "
                        f"expected {expected_authority}"
                    )
                _validate_terminal_release(candidate, repo_root, observed_now=now)
                _validate_writer_binding(current, admitted_git_locks=git_locks)
                write_json_atomic(lease_path, candidate, trusted_root=lock_root)
        elif current_schema == _V1_SCHEMA:
            if not isinstance(outcome_ref, str) or not outcome_ref:
                raise ValueError("A legacy release requires outcome_ref")
            if _is_coordination_self_write(current):
                repo_root = repository_root(repo_root)
                _validate_lease(current, repo_root, allow_coordination_self_write=True)
                with _writer_git_admission_guard(current) as git_locks:
                    _validate_writer_binding(current, admitted_git_locks=git_locks)
                    _write_legacy_release(
                        lease_path,
                        current,
                        expected_generation=expected_generation,
                        outcome_ref=outcome_ref,
                        lock_root=lock_root,
                    )
            else:
                _write_legacy_release(
                    lease_path,
                    current,
                    expected_generation=expected_generation,
                    outcome_ref=outcome_ref,
                    lock_root=lock_root,
                )
        else:
            raise ValueError(f"Unsupported current lease schema_version: {current_schema}")
    return lease_path


def _write_legacy_release(
    lease_path: Path,
    current: dict[str, Any],
    *,
    expected_generation: int,
    outcome_ref: str,
    lock_root: Path,
) -> None:
    current["state"] = "RELEASED"
    current["generation"] = expected_generation + 1
    current["released_utc"] = utc_now()
    current["outcome_ref"] = outcome_ref
    current["active_writer_repository"] = None
    write_json_atomic(lease_path, current, trusted_root=lock_root)


def observe(
    lease_id: str,
    lock_root: Path,
    *,
    observed_utc: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Observe ownership without reclaiming or mutating a stale lease."""

    lease_id = require_safe_id(lease_id, "lease_id")
    lock_root = canonical_path(lock_root, must_exist=True)
    findings: list[str] = []
    requested_path = lock_root / f"{lease_id}.lease.json"
    if not requested_path.exists() and not requested_path.is_symlink():
        raise ValueError(f"Lease file not found: {requested_path}")
    try:
        lease_path = ensure_within(
            requested_path,
            lock_root,
            label="observed lease file",
            must_exist=True,
        )
        if not lease_path.is_file():
            raise ValueError("Lease record is not a regular file")
        lease = load_json(lease_path)
    except (OSError, TypeError, ValueError) as exc:
        return {
            "lease_id": lease_id,
            "state": None,
            "ownership_status": "UNKNOWN_FAIL_CLOSED",
            "active_writer_repository": None,
            "generation": None,
            "automatic_reclaim": False,
            "path": str(requested_path),
            "findings": [str(exc)],
        }
    if lease.get("lease_id") != lease_id:
        findings.append("Lease file is not bound to the requested lease_id")
    if lease.get("schema_version") == _V2_SCHEMA:
        validation_root = None
        if repo_root is None:
            findings.append("Cannot verify a v2 lease without explicit repo_root")
        else:
            try:
                validation_root = repository_root(repo_root)
            except ValueError as exc:
                findings.append(str(exc))
        if validation_root is not None:
            findings.extend(validate_document(lease, validation_root))
        try:
            _validate_v2_lifecycle(lease)
            if lease.get("state") == "RELEASED":
                if validation_root is None:
                    raise ValueError("Cannot verify terminal release without repo_root")
                _validate_terminal_release(lease, validation_root)
        except (OSError, TypeError, ValueError) as exc:
            findings.append(str(exc))
    elif lease.get("schema_version") == _V1_SCHEMA:
        if not _legacy_record_is_valid(lease):
            findings.append("Legacy lease structure or lifecycle is invalid")
        elif lease.get("state") == "RELEASED" and not _terminal_record_is_valid(lease):
            findings.append("Legacy terminal lease evidence is incomplete")
    else:
        findings.append(f"Unsupported lease schema_version: {lease.get('schema_version')}")
    if findings:
        status = "UNKNOWN_FAIL_CLOSED"
    elif lease.get("state") == "RELEASED":
        status = "TERMINAL_RELEASED"
    elif lease.get("state") != "ACTIVE":
        status = "UNKNOWN_FAIL_CLOSED"
    elif lease.get("schema_version") == _V1_SCHEMA:
        status = "ACTIVE_LEGACY"
    else:
        observed = _timestamp(observed_utc or utc_now(), label="observed_utc")
        expires = _timestamp(lease["expires_utc"], label="expires_utc")
        status = "ACTIVE" if observed <= expires else "STALE_ACTIVE"
    return {
        "lease_id": lease_id,
        "state": lease.get("state"),
        "ownership_status": status,
        "active_writer_repository": lease.get("active_writer_repository"),
        "generation": lease.get("generation"),
        "automatic_reclaim": False,
        "path": str(lease_path),
        "findings": findings,
    }


def list_leases(lock_root: Path) -> list[dict[str, Any]]:
    if not lock_root.exists():
        return []
    lock_root = canonical_path(lock_root, must_exist=True)
    leases: list[dict[str, Any]] = []
    for path in sorted(lock_root.glob("*.lease.json")):
        safe_path = ensure_within(
            path,
            lock_root,
            label="listed lease file",
            must_exist=True,
        )
        leases.append(load_json(safe_path))
    return leases
