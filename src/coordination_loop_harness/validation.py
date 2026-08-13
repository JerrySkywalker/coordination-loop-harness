from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .util import load_json

SCHEMA_BY_VERSION = {
    "coord.request.v1": "request.v1.schema.json",
    "coord.plan.v1": "plan.v1.schema.json",
    "coord.goal.v1": "goal.v1.schema.json",
    "coord.manifest.v1": "manifest.v1.schema.json",
    "coord.status.v1": "status.v1.schema.json",
    "coord.outcome.v1": "outcome.v1.schema.json",
    "coord.decision.v1": "decision.v1.schema.json",
    "coord.audit.v1": "audit.v1.schema.json",
    "coord.audit.v2": "audit.v2.schema.json",
    "coord.decision.v2": "decision.v2.schema.json",
    "coord.status.v2": "status.v2.schema.json",
    "coord.bundle-seal.v1": "bundle-seal.v1.schema.json",
    "coord.repo-set-lease.v1": "repo-set-lease.v1.schema.json",
    "coord.template-provenance.v1": "template-provenance.v1.schema.json",
    "coord.harness-model.v1": "harness-model.v1.schema.json",
    "coord.profile-pack.v1": "profile-pack.v1.schema.json",
}


def repository_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "schemas").is_dir() and (candidate / "TEMPLATE_VERSION").is_file():
            return candidate
    raise ValueError("Could not locate repository root containing schemas/ and TEMPLATE_VERSION")


def load_schema(repo_root: Path, schema_version: str) -> dict[str, Any]:
    try:
        filename = SCHEMA_BY_VERSION[schema_version]
    except KeyError as exc:
        raise ValueError(f"Unsupported schema_version: {schema_version}") from exc
    return json.loads((repo_root / "schemas" / filename).read_text(encoding="utf-8"))


def validate_document(document: dict[str, Any], repo_root: Path) -> list[str]:
    schema_version = document.get("schema_version")
    if not isinstance(schema_version, str):
        return ["schema_version must be a string"]
    schema = load_schema(repo_root, schema_version)
    validator = Draft202012Validator(schema)
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(
            validator.iter_errors(document),
            key=lambda item: list(item.absolute_path),
        )
    ]


def validate_json_file(path: Path, repo_root: Path) -> list[str]:
    try:
        document = load_json(path)
        return validate_document(document, repo_root)
    except ValueError as exc:
        return [str(exc)]
