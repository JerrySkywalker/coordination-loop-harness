"""One fixed local-result observation, private to the live result issuer.

This never publishes a file, grants runtime custody, or accepts a path list.
The legacy clean verifier and the published Lease v2 artifact remain unchanged.
"""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from pathlib import Path

from .leases import _writer_git_lock_paths
from .repository import _git, _repository_identity_snapshot, verify_repository
from .util import sha256_bytes, sha256_file
from .windows_custody import sealed_decision
from .windows_security import WindowsBoundaryHold, WindowsSecurityReader

RESULT_CLAIM = "clh.windows-local-result-claim.v1"
RESULT_HANDOFF = "clh.windows-local-result-handoff.v1"
RESULT_PATH = "M1/result.md"
RESULT_LIMIT = 8192


class ResultPublication:
    """Retained by one issuer attempt; no serialization or offline bearer use."""

    def __init__(self, writer: dict) -> None:
        self._writer = dict(writer)
        self._root = Path(writer["worktree_root"])
        if (
            not writer["repository"].startswith("local/")
            or self._root != Path(writer["canonical_path"])
            or not (self._root / ".git").is_dir()
        ):
            raise WindowsBoundaryHold("HOLD_RESULT_REQUIRES_SINGLE_LOCAL_REPOSITORY")
        self._reader = WindowsSecurityReader()
        self._check_path(self._root / ".git", directory=True)
        self._seals = ExitStack()
        self._result = None
        self._identity = None
        self._git_pin = self._git_identity()
        self.check_clean()

    def __getstate__(self):
        raise TypeError("Publication observations cannot be restored")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._seals.close()

    def _git_identity(self) -> tuple:
        root = self._root
        for name in ("config", "index", "HEAD"):
            self._check_path(root / ".git" / name, directory=False)
        identity = _repository_identity_snapshot(root)
        if Path(identity["git_dir_path"]) != root / ".git":
            raise WindowsBoundaryHold("HOLD_RESULT_LINKED_WORKTREE_FORBIDDEN")
        exclude = root / ".git" / "info" / "exclude"
        exclude_digest = None
        if os.path.lexists(exclude):
            self._check_path(exclude, directory=False)
            exclude_digest = sha256_file(exclude)
        return (
            identity,
            sha256_file(root / ".git" / "config"),
            sha256_file(root / ".git" / "index"),
            sha256_file(root / ".git" / "HEAD"),
            exclude_digest,
            _git(root, "show-ref", "--head"),
            _git(root, "worktree", "list", "--porcelain"),
        )

    def _check_git(self, *, published: bool) -> None:
        root, writer = self._root, self._writer
        observed = verify_repository(
            root,
            expected_sha=writer["exact_sha"],
            stable_branch=writer["branch_ref"].removeprefix("refs/heads/"),
            require_detached=False,
            repository_identity_version="v2",
            offline=True,
        )
        if observed["findings"] or observed["tracked_dirty"]:
            raise WindowsBoundaryHold("HOLD_RESULT_GIT_BINDING_CHANGED")
        if _git(root, "remote"):
            raise WindowsBoundaryHold("HOLD_RESULT_REQUIRES_ZERO_REMOTES")
        # Include ignored entries: neither an ignore rule nor a side output can
        # hide an effect. NUL framing preserves exact names rather than quoting.
        status = _git(
            root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
        )
        expected = f"?? {RESULT_PATH}\0" if published else ""
        if status != expected:
            raise WindowsBoundaryHold("HOLD_RESULT_NOT_SOLE_EXACT_EFFECT")
        if self._git_identity() != self._git_pin:
            raise WindowsBoundaryHold("HOLD_RESULT_GIT_METADATA_CHANGED")
        lease_shape = {
            "repositories": [writer],
            "schema_version": "coord.repo-set-lease.v2",
            "state": "ACTIVE",
        }
        for lock in _writer_git_lock_paths(lease_shape):
            if os.path.lexists(lock):
                raise WindowsBoundaryHold("HOLD_RESULT_ACTIVE_GIT_LOCK")

    def _check_path(self, path: Path, *, directory: bool) -> tuple:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & 0x400
            or (directory and not stat.S_ISDIR(metadata.st_mode))
            or (not directory and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1))
        ):
            raise WindowsBoundaryHold("HOLD_RESULT_PATH_TYPE_OR_ALIAS")
        evidence = self._reader.file_security(path)
        return evidence, metadata.st_dev, metadata.st_ino

    def _parent(self) -> Path:
        root = self._root
        self._check_path(root, directory=True)
        for name in os.listdir(root):
            if name.casefold() == "m1" and name != "M1":
                raise WindowsBoundaryHold("HOLD_RESULT_PATH_CASE_ALIAS")
        parent = root / "M1"
        if os.path.lexists(parent):
            self._check_path(parent, directory=True)
            for name in os.listdir(parent):
                if name.casefold() == "result.md" and name != "result.md":
                    raise WindowsBoundaryHold("HOLD_RESULT_PATH_CASE_ALIAS")
        return parent

    def check_clean(self) -> None:
        if self._result is not None:
            raise WindowsBoundaryHold("HOLD_RESULT_PHASE_CANNOT_REVERSE")
        parent = self._parent()
        if os.path.lexists(parent / "result.md"):
            raise WindowsBoundaryHold("HOLD_RESULT_MUST_BE_CREATE_NEW")
        self._check_git(published=False)

    def verify(self, current: dict) -> dict:
        writers = [item for item in current["repositories"] if item["mode"] == "WRITE"]
        if writers != [self._writer]:
            raise WindowsBoundaryHold("HOLD_RESULT_WRITER_CHANGED")
        self._check_git(published=True)
        parent = self._parent()
        path = parent / "result.md"
        parent_before = self._check_path(parent, directory=True)
        before = self._check_path(path, directory=False)
        if self._result is None:
            # Read sharing only, retained through DAG acknowledgement. An
            # absent/unsealable file fails immediately; no clean-or-dirty phase.
            self._seals.enter_context(sealed_decision(path))
        with path.open("rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (metadata.st_dev, metadata.st_ino) != before[1:]:
                raise WindowsBoundaryHold("HOLD_RESULT_FILE_REPLACED")
            raw = stream.read(RESULT_LIMIT + 1)
        if len(raw) > RESULT_LIMIT:
            raise WindowsBoundaryHold("HOLD_RESULT_TOO_LARGE")
        try:
            raw.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise WindowsBoundaryHold("HOLD_RESULT_INVALID_UTF8") from exc
        proof = {"path": RESULT_PATH, "size": len(raw), "sha256": sha256_bytes(raw)}
        if (
            self._check_path(path, directory=False) != before
            or self._check_path(parent, directory=True) != parent_before
        ):
            raise WindowsBoundaryHold("HOLD_RESULT_CHANGED_DURING_READ")
        self._check_git(published=True)
        identity = (before, parent_before)
        if self._result is not None and (proof != self._result or identity != self._identity):
            raise WindowsBoundaryHold("HOLD_RESULT_OBSERVATION_CHANGED")
        self._result, self._identity = proof, identity
        return dict(proof)
