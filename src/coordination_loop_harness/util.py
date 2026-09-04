from __future__ import annotations

import json
import ntpath
import os
import posixpath
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
WINDOWS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
MOVEFILE_REPLACE_EXISTING = 0x1
MOVEFILE_WRITE_THROUGH = 0x8


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> Any:
    raise ValueError(f"Non-finite JSON number is not permitted: {value}")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_safe_id(value: str, label: str = "identifier") -> str:
    if not SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_non_finite_json,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"JSON file not found: {path}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def write_json_atomic(
    path: Path,
    data: dict[str, Any],
    *,
    create_new: bool = False,
    trusted_root: Path | None = None,
) -> None:
    if trusted_root is not None:
        trusted_root = canonical_path(trusted_root)
        path = ensure_within(path, trusted_root, label="atomic JSON target")
    payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if trusted_root is not None:
        path = ensure_within(path, trusted_root, label="atomic JSON target")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            if trusted_root is not None:
                ensure_within(Path(tmp_name), trusted_root, label="atomic JSON temporary file")
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if create_new:
            # A direct ``open('x')`` exposes a partially written final record if
            # the process dies between creation and the last write.  Linking a
            # fully flushed same-directory temporary file gives us both
            # create-if-absent semantics and an atomic visible publication.
            # Failure is deliberately closed; there is no unsafe direct-write
            # fallback.
            if os.name == "nt":
                _move_file_windows(Path(tmp_name), path, replace_existing=False)
            else:
                os.link(tmp_name, path)
                os.unlink(tmp_name)
                tmp_name = ""
        else:
            if os.name == "nt":
                _move_file_windows(Path(tmp_name), path, replace_existing=True)
            else:
                os.replace(tmp_name, path)
        _fsync_directory(path.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            if tmp_name:
                os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _fsync_directory(path: Path) -> None:
    """Sync POSIX directory metadata; Windows publication is write-through."""

    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _move_file_windows(source: Path, target: Path, *, replace_existing: bool) -> None:
    """Atomically publish a flushed file using Windows write-through semantics."""

    if os.name != "nt":
        raise RuntimeError("Windows write-through move requested on a non-Windows platform")
    import ctypes

    flags = MOVEFILE_WRITE_THROUGH
    if replace_existing:
        flags |= MOVEFILE_REPLACE_EXISTING
    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move_file.restype = ctypes.c_int
    if not move_file(str(source), str(target), flags):
        raise ctypes.WinError(ctypes.get_last_error())


def canonical_json_bytes(data: object) -> bytes:
    return (
        json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_path(path: Path | str, *, must_exist: bool = False) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Cannot safely resolve path {candidate}: {exc}") from exc


def ensure_within(
    path: Path,
    root: Path,
    *,
    label: str = "path",
    must_exist: bool = False,
) -> Path:
    resolved_root = canonical_path(root)
    resolved = canonical_path(path, must_exist=must_exist)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay within {resolved_root}: {resolved}") from exc
    return resolved


def canonical_repo(value: str) -> str:
    value = value.strip().removesuffix(".git")
    for prefix in (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
    ):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return value.strip("/").casefold()


def is_absolute_scope(value: str) -> bool:
    raw = value.strip()
    return bool(raw) and (PureWindowsPath(raw).is_absolute() or PurePosixPath(raw).is_absolute())


def canonical_scope(value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw
    if WINDOWS_PATH_RE.match(raw) or raw.startswith("//"):
        normalized = ntpath.normpath(raw.replace("/", "\\"))
        if os.name == "nt":
            normalized = str(canonical_path(normalized))
        normalized = normalized.replace("\\", "/")
        if re.fullmatch(r"[A-Za-z]:/", normalized):
            return normalized.casefold()
        return normalized.rstrip("/").casefold()
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/"):
        if os.name == "posix":
            return str(canonical_path(normalized))
        return posixpath.normpath(normalized).casefold()
    return str(canonical_path(normalized)).replace("\\", "/")


def paths_overlap(left: str | Path, right: str | Path) -> bool:
    left_scope = canonical_scope(str(left))
    right_scope = canonical_scope(str(right))
    if left_scope == right_scope:
        return True
    left_prefix = left_scope if left_scope.endswith("/") else left_scope + "/"
    right_prefix = right_scope if right_scope.endswith("/") else right_scope + "/"
    return left_scope.startswith(right_prefix) or right_scope.startswith(left_prefix)


@contextmanager
def admission_mutex(lock_root: Path) -> Iterator[None]:
    """Serialize lease admission with an atomic directory creation.

    Stale mutexes are deliberately not removed automatically. A human must inspect
    and clear them after proving that no admission operation is active.
    """

    lock_root = canonical_path(lock_root)
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_root = canonical_path(lock_root, must_exist=True)
    mutex = ensure_within(
        lock_root / ".repo-set-admission.mutex",
        lock_root,
        label="lease admission mutex",
    )
    try:
        mutex.mkdir()
    except FileExistsError as exc:
        raise RuntimeError(
            f"Lease admission mutex already exists: {mutex}. "
            "Inspect it manually; the harness will not delete it automatically."
        ) from exc

    mutex_identity = mutex.lstat()
    mutex_resolved = canonical_path(mutex, must_exist=True)
    body_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        body_error = exc
        raise
    finally:
        cleanup_error: BaseException | None = None
        try:
            current_mutex = mutex.lstat()
            if (
                (current_mutex.st_dev, current_mutex.st_ino)
                != (mutex_identity.st_dev, mutex_identity.st_ino)
                or mutex.is_symlink()
                or (hasattr(os.path, "isjunction") and os.path.isjunction(mutex))
                or canonical_path(mutex, must_exist=True) != mutex_resolved
            ):
                raise RuntimeError("Lease admission mutex identity changed during use")
            # The mutex deliberately remains empty. Removing an empty directory
            # (or refusing a swapped non-directory/non-empty entry) cannot
            # traverse and unlink attacker-controlled children.
            mutex.rmdir()
        except BaseException as exc:
            cleanup_error = exc
        if cleanup_error is not None:
            if body_error is not None:
                body_error.add_note(f"Lease admission mutex cleanup also failed: {cleanup_error}")
            else:
                raise cleanup_error
