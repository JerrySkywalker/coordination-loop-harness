from __future__ import annotations

import re
from collections.abc import Iterable
from os import walk
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .util import load_json, require_safe_id, sha256_file, write_json_atomic
from .validation import validate_document, validate_json_file

SECRET_PATTERNS = {
    "github_classic_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "github_fine_grained_token": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    "openai_key": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "authorization_bearer": re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._~-]{16,}", re.I),
}
PUBLISHABLE_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".ps1",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
    ".tmpl",
}
IGNORED_DIRECTORIES = {
    ".coord-local",
    ".coord-state",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
}
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _audit_result_semantic_findings(result: object, finding_count: int) -> list[str]:
    if result == "PASS" and finding_count != 0:
        return ["PASS audit cannot contain findings"]
    if result == "FAIL" and finding_count == 0:
        return ["FAIL audit requires at least one finding"]
    if result == "BLOCKED" and finding_count == 0:
        return ["BLOCKED audit requires at least one explicit blocker reason"]
    return []


def iter_durable_json(root: Path) -> Iterable[Path]:
    for directory in ("requests", "plans", "runs", "decisions", "audits"):
        base = root / directory
        if base.exists():
            yield from sorted(base.rglob("*.json"))


def iter_publishable_files(root: Path) -> Iterable[Path]:
    for directory, child_directories, filenames in walk(root):
        child_directories[:] = [
            name for name in child_directories if name not in IGNORED_DIRECTORIES
        ]
        base = Path(directory)
        for filename in filenames:
            path = base / filename
            if path.suffix.casefold() in PUBLISHABLE_SUFFIXES:
                yield path


def scan_markdown_links(root: Path) -> list[str]:
    findings: list[str] = []
    for path in iter_publishable_files(root):
        if path.suffix.casefold() != ".md":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for raw_target in MARKDOWN_LINK_RE.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            relative = target.split("#", 1)[0]
            if relative and not (path.parent / relative).resolve().exists():
                findings.append(f"{path.relative_to(root)}: missing Markdown link target {target}")
    return findings


def validate_repository(root: Path) -> list[str]:
    findings: list[str] = []
    for schema_path in sorted((root / "schemas").glob("*.schema.json")):
        try:
            Draft202012Validator.check_schema(load_json(schema_path))
        except Exception as exc:
            findings.append(f"{schema_path.relative_to(root)}: invalid JSON Schema: {exc}")
    for path in iter_durable_json(root):
        for error in validate_json_file(path, root):
            findings.append(f"{path.relative_to(root)}: {error}")
    provenance_path = root / ".coord-template.json"
    if provenance_path.exists():
        for error in validate_json_file(provenance_path, root):
            findings.append(f"{provenance_path.relative_to(root)}: {error}")

    for path in sorted(iter_publishable_files(root)):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{path.relative_to(root)}: sensitive pattern {name}")
    findings.extend(scan_markdown_links(root))

    attach_script = root / "scripts" / "Prepare-ImplementerAttach.ps1"
    if attach_script.exists():
        text = attach_script.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?im)^\s*(?:&\s*)?(?:codex|openai\s+codex)(?:\.exe)?\b", text):
            findings.append(
                "scripts/Prepare-ImplementerAttach.ps1 launches Codex; this is forbidden"
            )
    return findings


def record_audit(
    root: Path,
    *,
    run_id: str,
    audit_id: str,
    audit_type: str,
    auditor: str,
    timestamp: str,
    audited_sha: str,
    result: str,
    findings: list[str],
    read_only_asserted: bool,
    independently_launched_asserted: bool,
    read_only_verified: bool | None = None,
    independently_launched_verified: bool | None = None,
) -> list[Path]:
    require_safe_id(run_id, "run_id")
    require_safe_id(audit_id, "audit_id")
    semantic_findings = _audit_result_semantic_findings(result, len(findings))
    if semantic_findings:
        raise ValueError("Audit result semantics invalid: " + "; ".join(semantic_findings))
    directory = root / "audits" / run_id
    markdown_path = directory / f"{audit_id}.md"
    json_path = directory / f"{audit_id}.json"
    if markdown_path.exists() or json_path.exists():
        raise FileExistsError(f"Audit already exists: {audit_id}")
    directory.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(
        "\n".join(
            [
                f"# {audit_id} — {audit_type}",
                "",
                f"- Run: `{run_id}`",
                f"- Auditor: {auditor}",
                f"- Audited SHA: `{audited_sha}`",
                f"- Result: **{result}**",
                f"- Read-only asserted: `{str(read_only_asserted).lower()}`",
                (
                    "- Independently launched asserted: "
                    f"`{str(independently_launched_asserted).lower()}`"
                ),
                "",
                "## Findings",
                "",
                *([f"- {item}" for item in findings] or ["- None"]),
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    document: dict[str, Any] = {
        "schema_version": "coord.audit.v2",
        "audit_id": audit_id,
        "run_id": run_id,
        "audit_type": audit_type,
        "auditor": auditor,
        "audited_utc": timestamp,
        "audited_sha": audited_sha,
        "result": result,
        "finding_count": len(findings),
        "findings": findings,
        "read_only_asserted": read_only_asserted,
        "read_only_verified": read_only_verified,
        "independently_launched_asserted": independently_launched_asserted,
        "independently_launched_verified": independently_launched_verified,
        "markdown_sha256": sha256_file(markdown_path),
        "sensitive_output": False,
    }
    errors = validate_document(document, root)
    if errors:
        markdown_path.unlink()
        raise ValueError("Audit validation failed:\n- " + "\n- ".join(errors))
    write_json_atomic(json_path, document, create_new=True)
    return [json_path, markdown_path]


def verify_audit(root: Path, audit_path: Path) -> dict[str, Any]:
    findings: list[str] = []
    try:
        audit = load_json(audit_path)
    except ValueError as exc:
        return {"ok": False, "findings": [str(exc)]}
    findings.extend(validate_document(audit, root))
    if audit.get("schema_version") != "coord.audit.v2":
        findings.append("audit verification requires coord.audit.v2")
    markdown = audit_path.with_suffix(".md")
    if not markdown.is_file():
        findings.append(f"audit Markdown companion missing: {markdown}")
    elif audit.get("markdown_sha256") != sha256_file(markdown):
        findings.append("audit Markdown SHA-256 binding mismatch")
    if audit.get("finding_count") != len(audit.get("findings", [])):
        findings.append("finding_count does not match findings")
    findings.extend(
        _audit_result_semantic_findings(
            audit.get("result"),
            len(audit.get("findings", [])),
        )
    )
    return {
        "ok": not findings,
        "audit": str(audit_path),
        "result": audit.get("result"),
        "asserted": {
            "read_only": audit.get("read_only_asserted"),
            "independently_launched": audit.get("independently_launched_asserted"),
        },
        "verified": {
            "read_only": audit.get("read_only_verified"),
            "independently_launched": audit.get("independently_launched_verified"),
        },
        "findings": findings,
    }
