from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .util import ensure_within, load_json, require_safe_id, sha256_file, write_json_atomic

PROTECTED_DIRECTORIES = ("requests", "plans", "runs", "decisions", "audits", "handoffs")


def _active_runs(root: Path) -> list[str]:
    result: list[str] = []
    runs = root / "runs"
    if not runs.exists():
        return result
    for status_path in runs.glob("*/status.json"):
        try:
            state = load_json(status_path).get("state")
        except ValueError:
            state = "UNKNOWN"
        if state not in {"COMPLETE", "ABORTED"}:
            result.append(status_path.parent.name)
    return sorted(result)


def _render(template_root: Path, item: dict[str, Any], values: dict[str, str]) -> str:
    source = ensure_within(
        template_root / item["source"],
        template_root,
        label="template source",
    )
    text = source.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def bootstrap_repository(
    template_root: Path,
    target_root: Path,
    *,
    project_name: str,
    project_slug: str,
    template_repository: str,
    template_version: str,
    template_sha: str,
    dry_run: bool,
    safe_mode: str | None,
) -> dict[str, Any]:
    require_safe_id(project_slug, "project_slug")
    if not project_name.strip():
        raise ValueError("project_name cannot be empty")
    if not re.fullmatch(r"\d+\.\d+\.\d+", template_version):
        raise ValueError("template_version must use semantic version X.Y.Z")
    if not re.fullmatch(r"[0-9a-f]{40}", template_sha):
        raise ValueError("template_sha must be a lowercase 40-character Git SHA")
    if "/" not in template_repository:
        raise ValueError("template_repository must use owner/name form")
    target_root = target_root.resolve()
    active = _active_runs(target_root)
    if active and safe_mode != "preserve-active":
        raise ValueError(
            "Derived repository contains active runs; select --safe-mode preserve-active"
        )
    ownership = load_json(template_root / "templates" / "ownership-manifest.json")
    values = {
        "PROJECT_NAME": project_name,
        "PROJECT_SLUG": project_slug,
        "TEMPLATE_REPOSITORY": template_repository,
        "TEMPLATE_VERSION": template_version,
        "TEMPLATE_SHA": template_sha,
    }
    first_bootstrap = not (target_root / ".coord-template.json").exists()
    actions: list[dict[str, str]] = []
    for item in ownership["files"]:
        relative = Path(item["path"])
        target = ensure_within(target_root / relative, target_root)
        normalized_relative = target.relative_to(target_root)
        if normalized_relative.parts and normalized_relative.parts[0] in PROTECTED_DIRECTORIES:
            raise ValueError(
                f"ownership manifest targets protected run data: {normalized_relative}"
            )
        classification = item["classification"]
        if classification == "template-source-only":
            actions.append(
                {
                    "path": relative.as_posix(),
                    "classification": classification,
                    "action": "template-only",
                }
            )
            continue
        content = _render(template_root, item, values)
        action = "create"
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if existing == content:
                action = "unchanged"
            elif classification == "render-once" and first_bootstrap:
                action = "render"
            elif classification in {"render-once", "derived-owned"}:
                action = "preserve"
            elif classification == "template-managed":
                action = "conflict"
            else:
                raise ValueError(f"Unknown ownership classification: {classification}")
        actions.append(
            {"path": relative.as_posix(), "classification": classification, "action": action}
        )
        if not dry_run and action in {"create", "render"}:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")

    provenance = {
        "schema_version": "coord.template-provenance.v1",
        "template_repository": template_repository,
        "template_version": template_version,
        "template_exact_sha": template_sha,
        "project_name": project_name,
        "project_slug": project_slug,
        "ownership_manifest_sha256": sha256_file(
            template_root / "templates" / "ownership-manifest.json"
        ),
    }
    provenance_path = ensure_within(target_root / ".coord-template.json", target_root)
    provenance_action = "unchanged"
    if not provenance_path.exists() or load_json(provenance_path) != provenance:
        provenance_action = "update" if provenance_path.exists() else "create"
        if not dry_run:
            write_json_atomic(provenance_path, provenance)
    actions.append(
        {
            "path": ".coord-template.json",
            "classification": "template-managed",
            "action": provenance_action,
        }
    )
    return {
        "ok": True,
        "dry_run": dry_run,
        "safe_mode": safe_mode,
        "active_runs_preserved": active,
        "actions": actions,
    }


def sync_plan(
    template_root: Path,
    derived_root: Path,
    *,
    project_name: str,
    project_slug: str,
    template_version: str,
    template_sha: str,
) -> dict[str, Any]:
    provenance = load_json(derived_root / ".coord-template.json")
    plan = bootstrap_repository(
        template_root,
        derived_root,
        project_name=project_name,
        project_slug=project_slug,
        template_repository=provenance["template_repository"],
        template_version=template_version,
        template_sha=template_sha,
        dry_run=True,
        safe_mode="preserve-active",
    )
    conflicts = [item for item in plan["actions"] if item["action"] == "conflict"]
    plan.update(
        {
            "mode": "non-mutating",
            "conflicts": conflicts,
            "apply_performed": False,
            "active_run_modification": False,
            "from_template_version": provenance["template_version"],
            "from_template_sha": provenance["template_exact_sha"],
            "to_template_version": template_version,
            "to_template_sha": template_sha,
        }
    )
    return plan
