from __future__ import annotations

import json
import re
import stat
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


def _branch_identity(root: Path) -> tuple[bool, str | None]:
    result = subprocess.run(
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
    detached = result.returncode != 0
    return detached, None if detached else result.stdout.strip()


def _origin_url(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), "remote", "get-url", "origin"],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _optional_ref_sha(root: Path, ref: str | None) -> tuple[str | None, str | None]:
    if not ref:
        return None, None
    try:
        return _git(root, "rev-parse", "--verify", ref), None
    except ValueError as exc:
        return None, f"cached origin ref unavailable: {ref}: {exc}"


def _path_identity(path: Path, *, follow_symlinks: bool = False) -> tuple[int, int, int]:
    metadata = path.stat() if follow_symlinks else path.lstat()
    return metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)


def _repository_identity_snapshot(root: Path) -> dict[str, object]:
    """Capture path and filesystem identity for one exact Git worktree."""

    root = root.resolve(strict=True)
    locator = root / ".git"
    git_dir = Path(_git(root, "rev-parse", "--absolute-git-dir")).resolve(strict=True)
    common_dir = Path(
        _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    ).resolve(strict=True)
    return {
        "root_path": str(root),
        "root_identity": _path_identity(root, follow_symlinks=True),
        "git_locator_path": str(locator),
        "git_locator_identity": _path_identity(locator),
        "git_dir_path": str(git_dir),
        "git_dir_identity": _path_identity(git_dir, follow_symlinks=True),
        "common_dir_path": str(common_dir),
        "common_dir_identity": _path_identity(common_dir, follow_symlinks=True),
    }


def _identity_changes(
    before: dict[str, object], after: dict[str, object], *, prefix: str = ""
) -> list[str]:
    return [f"{prefix}{key}" for key in before if before[key] != after.get(key)]


def _command(command: str) -> list[str]:
    if Path(command).suffix.casefold() == ".py":
        return [sys.executable, command]
    return [command]


def _gh_api_value(
    gh_command: str,
    endpoint: str,
    jq: str,
    *,
    label: str,
) -> str:
    try:
        result = subprocess.run(
            [
                *_command(gh_command),
                "api",
                "--hostname",
                "github.com",
                endpoint,
                "--jq",
                jq,
            ],
            check=False,
            capture_output=True,
            text=True,
            shell=False,
        )
    except OSError as exc:
        raise ValueError(f"{label} failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"{label} failed: {detail}")
    return result.stdout.strip()


def verify_template_repository_provenance(
    root: Path,
    *,
    target_repository: str,
    template_repository: str,
    template_exact_sha: str,
    gh_command: str = "gh",
) -> dict[str, Any]:
    root = root.resolve()
    target_identity = canonical_repo(target_repository)
    template_identity = canonical_repo(template_repository)
    if not GITHUB_REPOSITORY_RE.fullmatch(target_identity):
        raise ValueError("target_repository must use github.com owner/name form")
    if not GITHUB_REPOSITORY_RE.fullmatch(template_identity):
        raise ValueError("template_repository must use github.com owner/name form")
    if not re.fullmatch(r"[0-9a-f]{40}", template_exact_sha):
        raise ValueError("template_exact_sha must be a lowercase 40-character Git SHA")

    git_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if git_root != root:
        raise ValueError(f"template provenance requires the exact Git root: {git_root}")
    checkout_tree = _git(root, "rev-parse", "HEAD^{tree}")

    actual_template = _gh_api_value(
        gh_command,
        f"repos/{target_identity}",
        ".template_repository.full_name // empty",
        label="target repository REST provenance check",
    )
    if not actual_template:
        raise ValueError(
            "target repository REST metadata does not identify a GitHub Template source"
        )
    if canonical_repo(actual_template) != template_identity:
        raise ValueError(
            "target repository template source mismatch: "
            f"expected {template_identity}, found {actual_template}"
        )

    template_tree = _gh_api_value(
        gh_command,
        f"repos/{template_identity}/git/commits/{template_exact_sha}",
        ".tree.sha",
        label="template commit REST tree check",
    )
    if not re.fullmatch(r"[0-9a-f]{40}", template_tree):
        raise ValueError("template commit REST tree check returned an invalid tree SHA")
    if checkout_tree != template_tree:
        raise ValueError(
            "derived checkout tree does not match template commit tree: "
            f"checkout {checkout_tree}, template {template_tree}"
        )
    return {
        "ok": True,
        "read_only": True,
        "target_repository": target_identity,
        "template_repository": template_identity,
        "template_exact_sha": template_exact_sha,
        "checkout_tree": checkout_tree,
        "template_tree": template_tree,
        "provenance_source": "github-rest-template_repository",
    }


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
    identity = _repository_identity_snapshot(root)
    git_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if git_root != root:
        findings.append(f"not exact Git root: expected {root}, found {git_root}")
    head = _git(root, "rev-parse", "HEAD")
    local_ref_sha = _git(root, "rev-parse", "--verify", local_ref)
    detached, branch = _branch_identity(root)
    origin = _origin_url(root)

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

    cached_sha, cached_finding = _optional_ref_sha(root, cached_origin_ref)
    if cached_finding:
        findings.append(cached_finding)
    if cached_origin_ref:
        if expected_sha and cached_sha and cached_sha != expected_sha:
            findings.append(
                f"cached origin ref mismatch: {cached_origin_ref}={cached_sha}, "
                f"expected {expected_sha}"
            )

    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    tracked = [line for line in status if not line.startswith("??")]
    untracked = [line[3:] for line in status if line.startswith("??")]

    # Git exposes these facts through separate commands.  Re-read the complete
    # local identity after status collection and fail closed if a concurrent
    # operation moved any binding or changed the worktree during verification.
    final_git_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    final_head = _git(root, "rev-parse", "HEAD")
    final_local_ref_sha = _git(root, "rev-parse", "--verify", local_ref)
    final_detached, final_branch = _branch_identity(root)
    final_origin = _origin_url(root)
    final_cached_sha, final_cached_finding = _optional_ref_sha(root, cached_origin_ref)
    if final_cached_finding and final_cached_finding not in findings:
        findings.append(final_cached_finding)
    final_status = _git(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    final_identity = _repository_identity_snapshot(root)
    changed: list[str] = []
    for label, before, after in (
        ("git_root", git_root, final_git_root),
        ("HEAD", head, final_head),
        (local_ref, local_ref_sha, final_local_ref_sha),
        ("branch", branch, final_branch),
        ("detached", detached, final_detached),
        ("origin", origin, final_origin),
        (cached_origin_ref or "cached_origin_ref", cached_sha, final_cached_sha),
        ("worktree_status", status, final_status),
    ):
        if before != after:
            changed.append(label)
    if changed:
        findings.append("repository state changed during verification: " + ", ".join(changed))
    identity_changed = _identity_changes(identity, final_identity)
    if identity_changed:
        findings.append(
            "repository filesystem identity changed during verification: "
            + ", ".join(identity_changed)
        )
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

        # The remote observation can be slow. Re-read every local binding after
        # it completes so an otherwise valid response cannot certify a checkout
        # that changed during the network call.
        post_git_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
        post_head = _git(root, "rev-parse", "HEAD")
        post_local_ref_sha = _git(root, "rev-parse", "--verify", local_ref)
        post_detached, post_branch = _branch_identity(root)
        post_origin = _origin_url(root)
        post_cached_sha, post_cached_finding = _optional_ref_sha(root, cached_origin_ref)
        if post_cached_finding and post_cached_finding not in findings:
            findings.append(post_cached_finding)
        post_status = _git(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
        post_identity = _repository_identity_snapshot(root)
        post_changed: list[str] = []
        for label, before, after in (
            ("git_root", final_git_root, post_git_root),
            ("HEAD", final_head, post_head),
            (local_ref, final_local_ref_sha, post_local_ref_sha),
            ("branch", final_branch, post_branch),
            ("detached", final_detached, post_detached),
            ("origin", final_origin, post_origin),
            (cached_origin_ref or "cached_origin_ref", final_cached_sha, post_cached_sha),
            ("worktree_status", final_status, post_status),
        ):
            if before != after:
                post_changed.append(label)
        if post_changed:
            findings.append(
                "repository state changed during verification: " + ", ".join(post_changed)
            )
            live_verified = False
        post_identity_changed = _identity_changes(final_identity, post_identity)
        if post_identity_changed:
            findings.append(
                "repository filesystem identity changed during verification: "
                + ", ".join(post_identity_changed)
            )
            live_verified = False

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
