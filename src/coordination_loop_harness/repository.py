from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .util import canonical_repo

GITHUB_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*/[A-Za-z0-9_.-]+$")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def verify_repository(
    root: Path,
    *,
    expected_origin: str | None = None,
    stable_branch: str | None = None,
    expected_sha: str | None = None,
    local_ref: str = "HEAD",
    cached_origin_ref: str | None = None,
    require_detached: bool | None = None,
    offline: bool = True,
    gh_command: str = "gh",
) -> dict[str, Any]:
    root = root.resolve()
    findings: list[str] = []
    git_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if git_root != root:
        findings.append(f"not exact Git root: expected {root}, found {git_root}")
    head = _git(root, "rev-parse", "HEAD")
    local_ref_sha = _git(root, "rev-parse", "--verify", local_ref)
    branch_result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-C",
            str(root),
            "symbolic-ref",
            "--quiet",
            "--short",
            "HEAD",
        ],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    detached = branch_result.returncode != 0
    branch = None if detached else branch_result.stdout.strip()
    origin_result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), "remote", "get-url", "origin"],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    origin = origin_result.stdout.strip() if origin_result.returncode == 0 else None

    if expected_origin and canonical_repo(origin or "") != canonical_repo(expected_origin):
        findings.append(f"origin mismatch: expected {expected_origin}, found {origin}")
    if stable_branch and branch != stable_branch:
        findings.append(f"branch mismatch: expected {stable_branch}, found {branch or 'DETACHED'}")
    if expected_sha and local_ref_sha != expected_sha:
        findings.append(f"local ref mismatch: {local_ref}={local_ref_sha}, expected {expected_sha}")
    if require_detached is not None and detached != require_detached:
        findings.append(
            f"detached worktree mismatch: expected {require_detached}, found {detached}"
        )

    cached_sha: str | None = None
    if cached_origin_ref:
        try:
            cached_sha = _git(root, "rev-parse", "--verify", cached_origin_ref)
        except ValueError as exc:
            findings.append(str(exc))
        if expected_sha and cached_sha and cached_sha != expected_sha:
            findings.append(
                f"cached origin ref mismatch: {cached_origin_ref}={cached_sha}, "
                f"expected {expected_sha}"
            )

    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    tracked = [line for line in status if not line.startswith("??")]
    untracked = [line[3:] for line in status if line.startswith("??")]
    live_verified = False
    if not offline:
        if not expected_origin:
            findings.append("live GitHub verification requires --expected-origin")
        else:
            expected_identity = canonical_repo(expected_origin)
            if not GITHUB_REPOSITORY_RE.fullmatch(expected_identity):
                findings.append(
                    "live GitHub verification requires a github.com owner/name identity"
                )
                return {
                    "ok": False,
                    "offline": offline,
                    "read_only": True,
                    "git_root": str(git_root),
                    "canonical_path": str(root),
                    "origin": origin,
                    "branch": branch,
                    "detached": detached,
                    "head": head,
                    "local_ref": local_ref,
                    "local_ref_sha": local_ref_sha,
                    "cached_origin_ref": cached_origin_ref,
                    "cached_origin_sha": cached_sha,
                    "tracked_dirty": bool(tracked),
                    "tracked_changes": tracked,
                    "untracked": untracked,
                    "live_github_verified": False,
                    "findings": findings,
                }
            command = [gh_command]
            if Path(gh_command).suffix.casefold() == ".py":
                command = [sys.executable, gh_command]
            try:
                result = subprocess.run(
                    [
                        *command,
                        "repo",
                        "view",
                        canonical_repo(expected_origin),
                        "--json",
                        "nameWithOwner,url",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    shell=False,
                )
            except OSError as exc:
                findings.append(f"live GitHub verification failed: {exc}")
            else:
                if result.returncode != 0:
                    findings.append(f"live GitHub verification failed: {result.stderr.strip()}")
                else:
                    try:
                        metadata = json.loads(result.stdout)
                    except json.JSONDecodeError as exc:
                        findings.append(f"live GitHub verification returned invalid JSON: {exc}")
                    else:
                        actual_identity = (
                            metadata.get("nameWithOwner") if isinstance(metadata, dict) else None
                        )
                        repository_url = metadata.get("url") if isinstance(metadata, dict) else None
                        if (
                            not isinstance(actual_identity, str)
                            or canonical_repo(actual_identity) != expected_identity
                        ):
                            findings.append(
                                "live GitHub repository identity mismatch: "
                                f"expected {expected_identity}, found {actual_identity}"
                            )
                        if (
                            not isinstance(repository_url, str)
                            or urlparse(repository_url).hostname != "github.com"
                        ):
                            findings.append(
                                "live GitHub repository host mismatch: "
                                f"expected github.com, found {repository_url}"
                            )
                        if not findings:
                            live_verified = True

    return {
        "ok": not findings,
        "offline": offline,
        "read_only": True,
        "git_root": str(git_root),
        "canonical_path": str(root),
        "origin": origin,
        "branch": branch,
        "detached": detached,
        "head": head,
        "local_ref": local_ref,
        "local_ref_sha": local_ref_sha,
        "cached_origin_ref": cached_origin_ref,
        "cached_origin_sha": cached_sha,
        "tracked_dirty": bool(tracked),
        "tracked_changes": tracked,
        "untracked": untracked,
        "live_github_verified": live_verified,
        "findings": findings,
    }
