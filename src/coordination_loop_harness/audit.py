from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .validation import validate_json_file


SECRET_PATTERNS = {
    "github_classic_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "github_fine_grained_token": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    "openai_key": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "authorization_bearer": re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._~-]{16,}", re.I),
}


def iter_durable_json(root: Path) -> Iterable[Path]:
    for directory in ("requests", "plans", "runs", "decisions", "audits"):
        base = root / directory
        if base.exists():
            yield from sorted(base.rglob("*.json"))


def validate_repository(root: Path) -> list[str]:
    findings: list[str] = []
    for path in iter_durable_json(root):
        for error in validate_json_file(path, root):
            findings.append(f"{path.relative_to(root)}: {error}")

    scan_roots = [root / name for name in ("requests", "plans", "runs", "decisions", "audits", "handoffs")]
    for base in scan_roots:
        if not base.exists():
            continue
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            if path.suffix.lower() not in {".json", ".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for name, pattern in SECRET_PATTERNS.items():
                if pattern.search(text):
                    findings.append(f"{path.relative_to(root)}: sensitive pattern {name}")

    attach_script = root / "scripts" / "Prepare-ImplementerAttach.ps1"
    if attach_script.exists():
        text = attach_script.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?im)^\s*(?:&\s*)?(?:codex|openai\s+codex)(?:\.exe)?\b", text):
            findings.append("scripts/Prepare-ImplementerAttach.ps1 launches Codex; this is forbidden")
    return findings
