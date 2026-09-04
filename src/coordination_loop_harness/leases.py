from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .decisions import verify_decision
from .repository import verify_repository
from .util import (
    admission_mutex,
    canonical_path,
    canonical_repo,
    canonical_scope,
    ensure_within,
    is_absolute_scope,
    load_json,
    paths_overlap,
    require_safe_id,
    utc_now,
    write_json_atomic,
)
from .validation import repository_root, validate_document


@dataclass(frozen=True)
class Overlap:
    lease_id: str
    category: str
    value: str


_V2_SCHEMA = "coord.repo-set-lease.v2"


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


def _terminal_record_is_valid(data: dict[str, Any]) -> bool:
    schema_version = data.get("schema_version")
    if schema_version == _V2_SCHEMA:
        try:
            _validate_v2_lifecycle(data)
        except (TypeError, ValueError):
            return False
        return data.get("state") == "RELEASED"
    if schema_version == "coord.repo-set-lease.v1":
        return (
            data.get("state") == "RELEASED"
            and data.get("active_writer_repository") is None
            and isinstance(data.get("released_utc"), str)
            and bool(data.get("released_utc"))
            and isinstance(data.get("outcome_ref"), str)
            and bool(data.get("outcome_ref"))
        )
    return False


def _active_leases(
    lock_root: Path, *, excluding_path: Path | None = None
) -> list[tuple[Path, dict[str, Any]]]:
    lock_root = canonical_path(lock_root)
    excluded = canonical_path(excluding_path) if excluding_path is not None else None
    result: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(lock_root.glob("*.lease.json")):
        path = ensure_within(path, lock_root, label="active lease file", must_exist=True)
        if excluded is not None and path == excluded:
            continue
        try:
            data = load_json(path)
        except ValueError:
            # An opaque record supplies no positive resource identity. Explicit
            # observation still reports it, but it cannot globally block a
            # disjoint repository merely because a file exists.
            continue
        if data.get("state") == "RELEASED" and _terminal_record_is_valid(data):
            continue
        result.append((path, data))
    return result


def _sets(lease: dict[str, Any]) -> dict[str, dict[str, str]]:
    shared_access = lease.get("schema_version") == _V2_SCHEMA
    repositories: dict[str, str] = {}
    paths: dict[str, str] = {}
    branches: dict[str, str] = {}
    for item in lease.get("repositories", []):
        if not isinstance(item, dict) or not isinstance(item.get("repository"), str):
            continue
        repository = canonical_repo(item["repository"])
        mode = item.get("mode") if shared_access else "WRITE"
        if mode not in {"READ", "WRITE"}:
            mode = "WRITE"
        _merge_access(repositories, repository, mode)
        for key in ("canonical_path", "worktree_root"):
            if isinstance(item.get(key), str) and item[key].strip():
                _merge_access(paths, canonical_scope(item[key]), mode)
        branch_ref = item.get("branch_ref")
        if isinstance(branch_ref, str) and branch_ref.strip():
            _merge_access(branches, f"{repository}:{branch_ref.casefold()}", mode)
    for item in lease.get("local_scopes", []):
        if item:
            _merge_access(paths, canonical_scope(item), "WRITE")
    infrastructure = {
        str(item).strip().casefold(): "WRITE"
        for item in lease.get("infrastructure_scopes", [])
        if str(item).strip()
    }
    coordination: dict[str, str] = {}
    if lease.get("coordination_repository"):
        coordination_repository = canonical_repo(lease["coordination_repository"])
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
    return paths_overlap(left, right)


def find_overlaps(
    candidate: dict[str, Any],
    lock_root: Path,
    *,
    excluding_path: Path | None = None,
) -> list[Overlap]:
    candidate_sets = _sets(candidate)
    overlaps: list[Overlap] = []
    for path, other in _active_leases(lock_root, excluding_path=excluding_path):
        other_sets = _sets(other)
        lease_id = str(other.get("lease_id", path.stem))
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

    coordination_entries = [
        item for item in repositories if canonical_repo(item["repository"]) == coordination
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
        or canonical_repo(data["active_writer_repository"]) != coordination
    ):
        raise ValueError(
            "Coordination self-write requires active_writer_repository to match "
            "coordination_repository"
        )
    writers = [
        canonical_repo(item["repository"]) for item in repositories if item.get("mode") == "WRITE"
    ]
    if writers != [coordination]:
        raise ValueError(
            "Coordination self-write requires the coordination repository to be the sole WRITE "
            "repository"
        )
    if any(
        canonical_repo(item["repository"]) != coordination and item.get("mode") != "READ"
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


def _validate_v2_lifecycle(data: dict[str, Any]) -> None:
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
            writers.append(canonical_repo(item["repository"]))
        for key in ("canonical_path", "worktree_root"):
            value = item.get(key)
            if value is not None and (not isinstance(value, str) or not is_absolute_scope(value)):
                raise ValueError(f"A v2 lease requires an absolute {key}")
    for value in data.get("local_scopes", []):
        if not isinstance(value, str) or not is_absolute_scope(value):
            raise ValueError("A v2 lease requires absolute local_scopes")
    active_writer = data.get("active_writer_repository")
    if state == "ACTIVE":
        if data.get("released_utc") is not None or data.get("outcome_ref") is not None:
            raise ValueError("An ACTIVE v2 lease cannot contain terminal release fields")
        if not data.get("decision_ref"):
            raise ValueError("An ACTIVE v2 lease requires decision_ref")
        if active_writer is None:
            if writers:
                raise ValueError(
                    "A lease with no active writer must not contain WRITE repositories"
                )
        elif len(writers) != 1 or canonical_repo(active_writer) != writers[0]:
            raise ValueError(
                "ACTIVE leases require exactly one WRITE repository matching "
                "active_writer_repository"
            )
    else:
        released_utc = data.get("released_utc")
        if released_utc is None or not data.get("outcome_ref"):
            raise ValueError("A RELEASED v2 lease requires released_utc and outcome_ref")
        if _timestamp(released_utc, label="released_utc") < created:
            raise ValueError("released_utc must not precede created_utc")
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


def _validate_writer_binding(data: dict[str, Any]) -> None:
    if data.get("schema_version") != _V2_SCHEMA or data.get("state") != "ACTIVE":
        return
    writers = [item for item in data["repositories"] if item["mode"] == "WRITE"]
    if not writers:
        return
    writer = writers[0]
    canonical_root = canonical_path(writer["canonical_path"], must_exist=True)
    writer_root = canonical_path(writer["worktree_root"], must_exist=True)
    branch_ref = writer["branch_ref"]
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
        stable_branch=branch,
        expected_sha=writer["exact_sha"],
        require_detached=False,
        offline=True,
    )
    findings = list(writer_result["findings"])
    if writer_result["tracked_dirty"]:
        findings.append("writer worktree has tracked changes")
    if writer_result["untracked"]:
        findings.append("writer worktree has untracked files")
    if _git_path(writer_root, "index.lock").exists():
        findings.append("writer worktree has an active Git index.lock")
    if findings:
        raise ValueError("Writer repository binding failed:\n- " + "\n- ".join(findings))


def _validate_decision_scope(data: dict[str, Any], decision: dict[str, Any]) -> None:
    if data.get("schema_version") != _V2_SCHEMA:
        return
    supplied = decision.get("scope")
    if not isinstance(supplied, list):
        raise ValueError("Lease decision scope is not a list")
    scopes = {scope.strip() for scope in supplied if isinstance(scope, str) and scope.strip()}
    resource_sets = _sets(data)
    required = {resource for category in resource_sets.values() for resource in category}
    missing = sorted(required - scopes)
    if missing:
        raise ValueError(
            "Lease decision scope does not include every reserved resource:\n- "
            + "\n- ".join(missing)
        )


def _validate_lease(
    data: dict[str, Any],
    repo_root: Path,
    *,
    allow_coordination_self_write: bool = False,
) -> None:
    errors = validate_document(data, repo_root)
    if errors:
        raise ValueError("Lease validation failed:\n- " + "\n- ".join(errors))
    if data.get("schema_version") == _V2_SCHEMA:
        _validate_v2_lifecycle(data)

    repositories = data.get("repositories", [])
    identities = [canonical_repo(item["repository"]) for item in repositories]
    if len(identities) != len(set(identities)):
        raise ValueError("Lease repositories must be unique")
    coordination = canonical_repo(data["coordination_repository"])
    if coordination in identities:
        if data.get("schema_version") != _V2_SCHEMA:
            raise ValueError("Coordination self-write requires coord.repo-set-lease.v2")
        if not allow_coordination_self_write:
            raise ValueError("The coordination repository cannot also be a product repository")
        _validate_coordination_self_write(data, repositories, coordination)

    if data.get("schema_version") != _V2_SCHEMA:
        writers = [
            canonical_repo(item["repository"])
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
            elif len(writers) != 1 or canonical_repo(active_writer) != writers[0]:
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
    _validate_lease(candidate, repo_root)
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
    )
    if not decision["ok"]:
        raise ValueError(
            "Lease decision verification failed:\n- " + "\n- ".join(decision["findings"])
        )
    _validate_decision_scope(candidate, decision)
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
        )
        if not decision["ok"]:
            raise ValueError(
                "Lease decision verification failed:\n- " + "\n- ".join(decision["findings"])
            )
        _validate_decision_scope(candidate, decision)
        _validate_writer_binding(candidate)
        overlaps = find_overlaps(candidate, lock_root)
        if overlaps:
            detail = "; ".join(
                f"{item.category}={item.value} held by {item.lease_id}" for item in overlaps
            )
            raise RuntimeError(f"Repository-set overlap detected: {detail}")
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
    _validate_lease(candidate, repo_root, allow_coordination_self_write=True)
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
        _validate_lease(current, repo_root, allow_coordination_self_write=True)
        for field in (
            "schema_version",
            "run_id",
            "owner",
            "created_utc",
            "coordination_repository",
        ):
            if candidate.get(field) != current.get(field):
                raise ValueError(f"Replacement must preserve lease identity field: {field}")
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
        )
        if not decision["ok"]:
            raise ValueError(
                "Lease decision verification failed:\n- " + "\n- ".join(decision["findings"])
            )
        _validate_decision_scope(candidate, decision)
        current_decision_ref = current.get("decision_ref")
        previous_decision_ref = decision.get("previous_decision_ref")
        if not isinstance(current_decision_ref, str) or not isinstance(previous_decision_ref, str):
            raise ValueError("Replacement decision must reference the current lease decision")
        current_decision_path = ensure_within(
            repo_root / current_decision_ref,
            repo_root,
            label="current lease decision_ref",
            must_exist=True,
        )
        previous_decision_path = ensure_within(
            repo_root / previous_decision_ref,
            repo_root,
            label="replacement previous_decision_ref",
            must_exist=True,
        )
        if current_decision_path != previous_decision_path:
            raise ValueError("Replacement decision does not directly follow the current decision")
        if decision.get("sequence") != candidate["generation"]:
            raise ValueError("Replacement decision sequence must match the lease generation")
        _validate_writer_binding(candidate)
        overlaps = find_overlaps(candidate, lock_root, excluding_path=lease_path)
        if overlaps:
            detail = "; ".join(
                f"{item.category}={item.value} held by {item.lease_id}" for item in overlaps
            )
            raise RuntimeError(f"Repository-set overlap detected: {detail}")
        write_json_atomic(lease_path, candidate, trusted_root=lock_root)
    return lease_path


def release(
    lease_id: str,
    lock_root: Path,
    *,
    expected_generation: int,
    outcome_ref: str,
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
        current["state"] = "RELEASED"
        current["generation"] = expected_generation + 1
        current["released_utc"] = utc_now()
        current["outcome_ref"] = outcome_ref
        current["active_writer_repository"] = None
        if current.get("schema_version") == _V2_SCHEMA:
            _validate_v2_lifecycle(current)
        write_json_atomic(lease_path, current, trusted_root=lock_root)
    return lease_path


def observe(lease_id: str, lock_root: Path, *, observed_utc: str | None = None) -> dict[str, Any]:
    """Observe ownership without reclaiming or mutating a stale lease."""

    lease_id = require_safe_id(lease_id, "lease_id")
    lock_root = canonical_path(lock_root, must_exist=True)
    lease_path = ensure_within(
        lock_root / f"{lease_id}.lease.json",
        lock_root,
        label="observed lease file",
        must_exist=True,
    )
    lease = load_json(lease_path)
    if lease.get("lease_id") != lease_id:
        raise RuntimeError("Lease file is not bound to the requested lease_id")
    if lease.get("schema_version") == _V2_SCHEMA:
        _validate_v2_lifecycle(lease)
    if lease.get("state") == "RELEASED":
        status = "TERMINAL_RELEASED"
    elif lease.get("state") != "ACTIVE":
        status = "UNKNOWN_FAIL_CLOSED"
    elif lease.get("schema_version") != _V2_SCHEMA:
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
