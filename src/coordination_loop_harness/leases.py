from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .decisions import verify_decision
from .util import (
    admission_mutex,
    canonical_path,
    canonical_repo,
    canonical_scope,
    ensure_within,
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


def _active_lease_files(lock_root: Path, *, excluding: str | None = None) -> list[Path]:
    lock_root = canonical_path(lock_root)
    result: list[Path] = []
    for path in sorted(lock_root.glob("*.lease.json")):
        path = ensure_within(path, lock_root, label="active lease file", must_exist=True)
        data = load_json(path)
        if data.get("state") != "ACTIVE":
            continue
        if excluding and data.get("lease_id") == excluding:
            continue
        result.append(path)
    return result


def _sets(lease: dict[str, Any]) -> dict[str, set[str]]:
    repositories = {
        canonical_repo(item["repository"])
        for item in lease.get("repositories", [])
        if isinstance(item, dict) and isinstance(item.get("repository"), str)
    }
    paths: set[str] = set()
    for item in lease.get("repositories", []):
        if not isinstance(item, dict):
            continue
        for key in ("canonical_path", "worktree_root"):
            if isinstance(item.get(key), str) and item[key].strip():
                paths.add(canonical_scope(item[key]))
    paths.update(canonical_scope(item) for item in lease.get("local_scopes", []) if item)
    infrastructure = {
        str(item).strip().casefold()
        for item in lease.get("infrastructure_scopes", [])
        if str(item).strip()
    }
    coordination = (
        {canonical_repo(lease["coordination_repository"])}
        if lease.get("coordination_repository")
        else set()
    )
    repositories.update(coordination)
    return {
        "repository": repositories,
        "path": paths,
        "infrastructure": infrastructure,
        "coordination_repository": coordination,
    }


def _paths_overlap(left: str, right: str) -> bool:
    return paths_overlap(left, right)


def find_overlaps(
    candidate: dict[str, Any],
    lock_root: Path,
    *,
    excluding: str | None = None,
) -> list[Overlap]:
    candidate_sets = _sets(candidate)
    overlaps: list[Overlap] = []
    for path in _active_lease_files(lock_root, excluding=excluding):
        other = load_json(path)
        other_sets = _sets(other)
        lease_id = str(other.get("lease_id", path.stem))
        for category in ("repository", "infrastructure", "coordination_repository"):
            for value in sorted(candidate_sets[category] & other_sets[category]):
                overlaps.append(Overlap(lease_id, category, value))
        for left in sorted(candidate_sets["path"]):
            for right in sorted(other_sets["path"]):
                if _paths_overlap(left, right):
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


def _validate_lease(
    data: dict[str, Any],
    repo_root: Path,
    *,
    allow_coordination_self_write: bool = False,
) -> None:
    errors = validate_document(data, repo_root)
    if errors:
        raise ValueError("Lease validation failed:\n- " + "\n- ".join(errors))

    repositories = data.get("repositories", [])
    identities = [canonical_repo(item["repository"]) for item in repositories]
    if len(identities) != len(set(identities)):
        raise ValueError("Lease repositories must be unique")
    coordination = canonical_repo(data["coordination_repository"])
    if coordination in identities:
        if not allow_coordination_self_write:
            raise ValueError("The coordination repository cannot also be a product repository")
        _validate_coordination_self_write(data, repositories, coordination)

    writers = [
        canonical_repo(item["repository"]) for item in repositories if item.get("mode") == "WRITE"
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
        else:
            if len(writers) != 1 or canonical_repo(active_writer) != writers[0]:
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
    decision = verify_decision(
        repo_root,
        ensure_within(
            repo_root / candidate["decision_ref"],
            repo_root,
            label="lease decision_ref",
        ),
        run_id=candidate["run_id"],
        action="lease:acquire",
        lease_id=candidate["lease_id"],
        lease_generation=1,
    )
    if not decision["ok"]:
        raise ValueError(
            "Lease decision verification failed:\n- " + "\n- ".join(decision["findings"])
        )
    lease_path = ensure_within(
        lock_root / f"{candidate['lease_id']}.lease.json",
        lock_root,
        label="new lease file",
    )

    with admission_mutex(lock_root):
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
        if candidate.get("generation") != expected_generation + 1:
            raise ValueError("Replacement generation must equal expected_generation + 1")
        if candidate.get("state") != "ACTIVE":
            raise ValueError("Use release() to close a lease")
        if not candidate.get("decision_ref"):
            raise ValueError("Lease replacement requires decision_ref")
        decision = verify_decision(
            repo_root,
            ensure_within(
                repo_root / candidate["decision_ref"],
                repo_root,
                label="lease decision_ref",
            ),
            run_id=candidate["run_id"],
            action="lease:expand",
            lease_id=candidate["lease_id"],
            lease_generation=candidate["generation"],
        )
        if not decision["ok"]:
            raise ValueError(
                "Lease decision verification failed:\n- " + "\n- ".join(decision["findings"])
            )
        overlaps = find_overlaps(candidate, lock_root, excluding=candidate["lease_id"])
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
        write_json_atomic(lease_path, current, trusted_root=lock_root)
    return lease_path


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
