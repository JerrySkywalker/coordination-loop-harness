from __future__ import annotations

from pathlib import Path
from typing import Any

from .audit import SECRET_PATTERNS
from .util import load_json, require_safe_id, sha256_file, write_json_atomic
from .validation import validate_document

DURABLE_DIRECTORIES = ("requests", "plans", "runs", "decisions", "audits", "handoffs")
DURABLE_SUFFIXES = {".json", ".md", ".txt"}
SEAL_NAME = "bundle-seal.json"


def _durable_files(root: Path, run_id: str) -> list[Path]:
    files: list[Path] = []
    for directory in DURABLE_DIRECTORIES:
        base = root / directory / run_id
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in DURABLE_SUFFIXES
                and path.name != SEAL_NAME
            ):
                files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _entry(root: Path, path: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
    }


def _bindings(root: Path, files: list[Path]) -> list[dict[str, str]]:
    files = [path for path in files if not path.is_symlink()]
    available = {path.resolve(): path for path in files}
    result: list[dict[str, str]] = []
    for json_path in (path for path in files if path.suffix.lower() == ".json"):
        markdown = json_path.with_suffix(".md")
        if markdown.resolve() not in available:
            continue
        result.append(
            {
                "json_path": json_path.relative_to(root).as_posix(),
                "json_sha256": sha256_file(json_path),
                "markdown_path": markdown.relative_to(root).as_posix(),
                "markdown_sha256": sha256_file(markdown),
            }
        )
    return sorted(result, key=lambda item: (item["json_path"], item["markdown_path"]))


def seal_bundle(root: Path, run_id: str) -> Path:
    require_safe_id(run_id, "run_id")
    files = _durable_files(root, run_id)
    if not files:
        raise ValueError(f"No durable objects found for run {run_id}")
    for path in files:
        if path.is_symlink():
            raise ValueError(f"Refusing to seal symbolic link: {path.relative_to(root).as_posix()}")
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                raise ValueError(
                    f"Refusing to seal sensitive durable object "
                    f"{path.relative_to(root).as_posix()}: {name}"
                )
    seal: dict[str, Any] = {
        "schema_version": "coord.bundle-seal.v1",
        "run_id": run_id,
        "algorithm": "sha256",
        "encoding": "utf-8",
        "files": [_entry(root, path) for path in files],
        "markdown_json_bindings": _bindings(root, files),
        "local_evidence_included": False,
    }
    errors = validate_document(seal, root)
    if errors:
        raise ValueError("Bundle seal validation failed:\n- " + "\n- ".join(errors))
    output = root / "runs" / run_id / SEAL_NAME
    write_json_atomic(output, seal)
    return output


def verify_bundle(root: Path, run_id: str) -> dict[str, Any]:
    require_safe_id(run_id, "run_id")
    seal_path = root / "runs" / run_id / SEAL_NAME
    seal = load_json(seal_path)
    findings = validate_document(seal, root)
    if seal.get("run_id") != run_id:
        findings.append(f"run_id mismatch: expected {run_id}, found {seal.get('run_id')}")

    actual: dict[str, str] = {}
    for path in _durable_files(root, run_id):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            findings.append(f"symbolic-link durable object is forbidden: {relative}")
            continue
        actual[relative] = sha256_file(path)
    declared_entries = seal.get("files", [])
    declared = {
        item["path"]: item["sha256"]
        for item in declared_entries
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("sha256"), str)
    }
    if len(declared) != len(declared_entries):
        findings.append("seal contains duplicate or malformed file entries")
    for path in sorted(set(declared) - set(actual)):
        findings.append(f"missing durable object: {path}")
    for path in sorted(set(actual) - set(declared)):
        findings.append(f"extra or unhashed durable object: {path}")
    for path in sorted(set(actual) & set(declared)):
        if actual[path] != declared[path]:
            findings.append(f"changed durable object: {path}")

    for binding in seal.get("markdown_json_bindings", []):
        if not isinstance(binding, dict):
            findings.append("invalid Markdown/JSON binding")
            continue
        for path_key, hash_key in (
            ("json_path", "json_sha256"),
            ("markdown_path", "markdown_sha256"),
        ):
            relative = binding.get(path_key)
            expected = binding.get(hash_key)
            if relative not in actual:
                findings.append(f"binding target missing: {relative}")
            elif actual[relative] != expected:
                findings.append(f"binding hash mismatch: {relative}")
    actual_binding_keys = {
        (item["json_path"], item["markdown_path"])
        for item in _bindings(root, _durable_files(root, run_id))
    }
    declared_binding_keys = {
        (item.get("json_path"), item.get("markdown_path"))
        for item in seal.get("markdown_json_bindings", [])
        if isinstance(item, dict)
    }
    for binding in sorted(actual_binding_keys - declared_binding_keys):
        findings.append(f"missing Markdown/JSON binding: {binding[0]} <-> {binding[1]}")
    for binding in sorted(declared_binding_keys - actual_binding_keys):
        findings.append(f"extra Markdown/JSON binding: {binding[0]} <-> {binding[1]}")
    return {"ok": not findings, "seal": str(seal_path), "findings": findings}
