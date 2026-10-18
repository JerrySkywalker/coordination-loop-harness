"""Frozen v0.2/v0.3 local scaffold compatibility; active bootstrap belongs to CLT."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .util import (
    canonical_repo,
    ensure_within,
    load_json,
    require_safe_id,
    sha256_bytes,
    sha256_file,
    write_json_atomic,
)
from .validation import validate_document

PROTECTED_DIRECTORIES = ("requests", "plans", "runs", "decisions", "audits", "handoffs")
GITHUB_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*/[A-Za-z0-9_.-]+$")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"template provenance git check failed: {detail}")
    return result.stdout.strip()


def _command(command: str) -> list[str]:
    if Path(command).suffix.casefold() == ".py":
        return [sys.executable, command]
    return [command]


def _verify_template_provenance(
    template_root: Path,
    *,
    template_repository: str,
    template_sha: str,
    target_repository: str | None,
    gh_command: str,
) -> dict[str, str]:
    git_root = Path(_git(template_root, "rev-parse", "--show-toplevel")).resolve()
    if git_root != template_root.resolve():
        raise ValueError(
            "template provenance requires template_root to be the exact Git checkout root"
        )
    head = _git(template_root, "rev-parse", "HEAD")
    tree = _git(template_root, "rev-parse", "HEAD^{tree}")
    try:
        origin = _git(template_root, "remote", "get-url", "origin")
    except ValueError as exc:
        raise ValueError("template provenance requires a verified checkout origin") from exc

    expected_template = canonical_repo(template_repository)
    actual_origin = canonical_repo(origin)
    if actual_origin == expected_template:
        if head != template_sha:
            raise ValueError(
                f"template provenance exact-head mismatch: expected {template_sha}, found {head}"
            )
        return {
            "verification": "exact-template-checkout",
            "checkout_head": head,
            "checkout_tree": tree,
            "checkout_repository": actual_origin,
        }

    if target_repository is None:
        raise ValueError(
            "template provenance checkout origin does not match the template repository"
        )
    expected_target = canonical_repo(target_repository)
    if actual_origin != expected_target:
        raise ValueError(
            "template provenance derived checkout origin mismatch: "
            f"expected {expected_target}, found {actual_origin}"
        )
    try:
        result = subprocess.run(
            [
                *_command(gh_command),
                "api",
                "--hostname",
                "github.com",
                f"repos/{expected_template}/git/commits/{template_sha}",
                "--jq",
                ".tree.sha",
            ],
            check=False,
            capture_output=True,
            text=True,
            shell=False,
        )
    except OSError as exc:
        raise ValueError(f"template provenance live GitHub check failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"template provenance live GitHub check failed: {detail}")
    remote_tree = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", remote_tree):
        raise ValueError("template provenance live GitHub check returned an invalid tree SHA")
    if tree != remote_tree:
        raise ValueError(
            "template provenance tree mismatch: "
            f"derived checkout {tree}, template commit {remote_tree}"
        )
    return {
        "verification": "github-template-tree",
        "checkout_head": head,
        "checkout_tree": tree,
        "checkout_repository": actual_origin,
    }


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
    target = Path(item["path"])
    if target.suffix.casefold() == ".json":
        for key, value in values.items():
            text = text.replace(
                '"{{' + key + '}}"',
                json.dumps(value, ensure_ascii=False),
            )
        try:
            rendered = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Rendered JSON is invalid for {target.as_posix()}: {exc}") from exc
        if not isinstance(rendered, dict):
            raise ValueError(f"Rendered JSON must be an object: {target.as_posix()}")
    else:
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
    target_repository: str | None = None,
    gh_command: str = "gh",
) -> dict[str, Any]:
    require_safe_id(project_slug, "project_slug")
    if not project_name.strip():
        raise ValueError("project_name cannot be empty")
    if not re.fullmatch(r"\d+\.\d+\.\d+", template_version):
        raise ValueError("template_version must use semantic version X.Y.Z")
    if not re.fullmatch(r"[0-9a-f]{40}", template_sha):
        raise ValueError("template_sha must be a lowercase 40-character Git SHA")
    if not GITHUB_REPOSITORY_RE.fullmatch(template_repository):
        raise ValueError("template_repository must use owner/name form")
    if target_repository is not None and not GITHUB_REPOSITORY_RE.fullmatch(target_repository):
        raise ValueError("target_repository must use owner/name form")
    template_root = template_root.resolve()
    target_root = target_root.resolve()
    provenance_verification = _verify_template_provenance(
        template_root,
        template_repository=template_repository,
        template_sha=template_sha,
        target_repository=target_repository,
        gh_command=gh_command,
    )
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
    provenance_path = ensure_within(target_root / ".coord-template.json", target_root)
    first_bootstrap = not provenance_path.exists()
    previous_provenance: dict[str, Any] | None = None
    previous_managed_hashes: dict[str, str] = {}
    if not first_bootstrap:
        previous_provenance = load_json(provenance_path)
        provenance_errors = validate_document(previous_provenance, template_root)
        if provenance_errors:
            raise ValueError(
                "Existing template provenance is invalid:\n- " + "\n- ".join(provenance_errors)
            )
        previous_managed_hashes = {
            item["path"]: item["sha256"] for item in previous_provenance["managed_files"]
        }
    actions: list[dict[str, str]] = []
    pending_writes: list[tuple[Path, str]] = []
    managed_files: list[dict[str, str]] = []
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
        rendered_sha256 = sha256_bytes(content.encode("utf-8"))
        if classification == "template-managed":
            managed_files.append(
                {
                    "path": relative.as_posix(),
                    "sha256": rendered_sha256,
                }
            )
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
                previous_sha256 = previous_managed_hashes.get(relative.as_posix())
                action = (
                    "safe-update"
                    if previous_sha256 is not None and sha256_file(target) == previous_sha256
                    else "conflict"
                )
            else:
                raise ValueError(f"Unknown ownership classification: {classification}")
        actions.append(
            {"path": relative.as_posix(), "classification": classification, "action": action}
        )
        if action in {"create", "render", "safe-update"}:
            pending_writes.append((target, content))

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
        "managed_files": sorted(managed_files, key=lambda item: item["path"]),
    }
    provenance_errors = validate_document(provenance, template_root)
    if provenance_errors:
        raise ValueError(
            "Template provenance validation failed:\n- " + "\n- ".join(provenance_errors)
        )
    provenance_action = "unchanged"
    if previous_provenance != provenance:
        provenance_action = "safe-update" if provenance_path.exists() else "create"
    actions.append(
        {
            "path": ".coord-template.json",
            "classification": "template-managed",
            "action": provenance_action,
        }
    )
    conflicts = [item for item in actions if item["action"] == "conflict"]
    if conflicts and not dry_run:
        paths = ", ".join(item["path"] for item in conflicts)
        raise ValueError(f"Template-managed file conflicts require review: {paths}")
    if not dry_run:
        for target, content in pending_writes:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        if provenance_action != "unchanged":
            write_json_atomic(provenance_path, provenance)
    return {
        "ok": True,
        "dry_run": dry_run,
        "safe_mode": safe_mode,
        "active_runs_preserved": active,
        "actions": actions,
        "provenance_verification": provenance_verification,
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
    provenance_errors = validate_document(provenance, template_root)
    if provenance_errors:
        raise ValueError(
            "Existing template provenance is invalid:\n- " + "\n- ".join(provenance_errors)
        )
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
