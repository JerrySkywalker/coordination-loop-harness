from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
WINDOWS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_safe_id(value: str, label: str = "identifier") -> str:
    if not SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def write_json_atomic(path: Path, data: dict[str, Any], *, create_new: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if create_new:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        return

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def canonical_json_bytes(data: object) -> bytes:
    return (
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
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


def ensure_within(path: Path, root: Path, *, label: str = "path") -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
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


def canonical_scope(value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw
    if WINDOWS_PATH_RE.match(raw) or raw.startswith("//"):
        return raw.replace("\\", "/").rstrip("/").casefold()
    normalized = raw.replace("\\", "/").rstrip("/")
    if normalized.startswith("/"):
        return str(PurePosixPath(normalized))
    return str(Path(normalized).expanduser()).replace("\\", "/")


@contextmanager
def admission_mutex(lock_root: Path) -> Iterator[None]:
    """Serialize lease admission with an atomic directory creation.

    Stale mutexes are deliberately not removed automatically. A human must inspect
    and clear them after proving that no admission operation is active.
    """

    lock_root.mkdir(parents=True, exist_ok=True)
    mutex = lock_root / ".repo-set-admission.mutex"
    try:
        mutex.mkdir()
    except FileExistsError as exc:
        raise RuntimeError(
            f"Lease admission mutex already exists: {mutex}. "
            "Inspect it manually; the harness will not delete it automatically."
        ) from exc

    try:
        write_json_atomic(
            mutex / "owner.json",
            {"pid": os.getpid(), "created_utc": utc_now()},
            create_new=True,
        )
        yield
    finally:
        for child in mutex.iterdir():
            child.unlink()
        mutex.rmdir()
