from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .util import require_safe_id, utc_now, write_json_atomic


def _markdown_heading(title: str, body: str) -> str:
    return f"# {title}\n\n{body.rstrip()}\n"


def init_run(
    root: Path,
    *,
    run_id: str,
    title: str,
    requested_by: str,
    objective: str,
    repositories: list[str],
    template_version: str,
) -> list[Path]:
    require_safe_id(run_id, "run_id")
    if not repositories:
        raise ValueError("At least one planned repository is required")

    targets = {
        "request_json": root / "requests" / run_id / "request.json",
        "request_md": root / "requests" / run_id / "request.md",
        "plan_json": root / "plans" / run_id / "plan.json",
        "plan_md": root / "plans" / run_id / "plan.md",
        "goal_json": root / "plans" / run_id / "goal.json",
        "goal_md": root / "plans" / run_id / "goal.md",
        "manifest_json": root / "plans" / run_id / "manifest.json",
        "status_json": root / "runs" / run_id / "status.json",
        "outcome_json": root / "runs" / run_id / "outcome.json",
    }
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing:
        raise FileExistsError("Run already contains files: " + ", ".join(existing))

    created_utc = utc_now()
    request: dict[str, Any] = {
        "schema_version": "coord.request.v1",
        "run_id": run_id,
        "title": title,
        "requested_by": requested_by,
        "created_utc": created_utc,
        "objective": objective,
        "constraints": [],
        "planned_repositories": repositories,
    }
    plan: dict[str, Any] = {
        "schema_version": "coord.plan.v1",
        "run_id": run_id,
        "title": title,
        "created_utc": created_utc,
        "planned_repo_set": repositories,
        "phases": [
            {
                "phase_id": "P1",
                "title": "Initial implementation",
                "write_repositories": [repositories[0]],
                "read_repositories": repositories[1:],
                "entry_gate": "OWNER_APPROVED",
                "exit_gate": "EXACT_HEAD_VALIDATED",
            }
        ],
        "infrastructure_apply_default": "DENY",
    }
    goal: dict[str, Any] = {
        "schema_version": "coord.goal.v1",
        "run_id": run_id,
        "role": "IMPLEMENTER",
        "active_phase": "P1",
        "write_repository": repositories[0],
        "read_repositories": repositories[1:],
        "exact_bases": {},
        "allowed_paths": [],
        "forbidden_paths": [],
        "validation_commands": [],
        "deliverables": ["commit", "draft_pull_request", "exact_head_report"],
        "production_mutation": False,
    }
    status: dict[str, Any] = {
        "schema_version": "coord.status.v1",
        "run_id": run_id,
        "revision": 1,
        "state": "PLANNED",
        "checkpoint": "RUN_MATERIALIZED",
        "active_phase": None,
        "active_writer": None,
        "lease_id": None,
        "product_writes_started": False,
        "updated_utc": created_utc,
    }
    outcome: dict[str, Any] = {
        "schema_version": "coord.outcome.v1",
        "run_id": run_id,
        "state": "PENDING",
        "summary": "",
        "completed_utc": None,
        "exact_heads": {},
        "production_mutation": False,
        "sensitive_output": False,
    }

    for path, data in (
        (targets["request_json"], request),
        (targets["plan_json"], plan),
        (targets["goal_json"], goal),
        (targets["status_json"], status),
        (targets["outcome_json"], outcome),
    ):
        write_json_atomic(path, data, create_new=True)

    targets["request_md"].parent.mkdir(parents=True, exist_ok=True)
    targets["request_md"].write_text(
        _markdown_heading(
            f"{run_id} — Request",
            f"**Title:** {title}\n\n**Requested by:** {requested_by}\n\n## Objective\n\n{objective}\n",
        ),
        encoding="utf-8",
    )
    targets["plan_md"].write_text(
        _markdown_heading(
            f"{run_id} — Plan",
            "## Planned repository set\n\n" + "\n".join(f"- `{repo}`" for repo in repositories)
            + "\n\n## Phases\n\n- P1: initial implementation; one write repository at a time.\n",
        ),
        encoding="utf-8",
    )
    targets["goal_md"].write_text(
        _markdown_heading(
            f"{run_id} — Implementer Goal",
            "The machine-readable source of truth is `goal.json`. Edit both files together.\n",
        ),
        encoding="utf-8",
    )

    for directory in ("decisions", "audits", "handoffs"):
        (root / directory / run_id).mkdir(parents=True, exist_ok=True)
        (root / directory / run_id / ".gitkeep").write_text("", encoding="utf-8")

    durable = [
        str(path.relative_to(root)).replace("\\", "/")
        for path in targets.values()
        if path.name != "manifest.json"
    ]
    manifest: dict[str, Any] = {
        "schema_version": "coord.manifest.v1",
        "run_id": run_id,
        "template_repository": "REPLACE_WITH_TEMPLATE_REPOSITORY",
        "template_version": template_version,
        "template_exact_sha": "REPLACE_WITH_40_CHAR_SHA",
        "planned_repo_set": repositories,
        "durable_files": sorted(durable),
        "local_state_policy": "UNTRACKED_OR_EXTERNAL",
    }
    write_json_atomic(targets["manifest_json"], manifest, create_new=True)
    return list(targets.values())


def render_attach(root: Path, run_id: str, *, lease_path: Path | None = None) -> Path:
    require_safe_id(run_id, "run_id")
    from .util import load_json

    goal = load_json(root / "plans" / run_id / "goal.json")
    status = load_json(root / "runs" / run_id / "status.json")
    lease = load_json(lease_path) if lease_path else None

    lines = [
        f"RUN_ID={run_id}",
        f"ROLE={goal['role']}",
        f"ACTIVE_PHASE={goal['active_phase']}",
        f"WRITE_REPOSITORY={goal['write_repository']}",
        f"RUN_STATE={status['state']}",
        "",
        "You are the only product writer authorized by this attach package.",
        "Read all active leases before any write and stop on overlap.",
        "Do not widen repository or infrastructure scope without a merged owner decision.",
        "",
        "READ_REPOSITORIES:",
        *[f"- {repo}" for repo in goal.get("read_repositories", [])],
        "",
        "FORBIDDEN_PATHS:",
        *[f"- {path}" for path in goal.get("forbidden_paths", [])],
        "",
        "VALIDATION_COMMANDS:",
        *[f"- {command}" for command in goal.get("validation_commands", [])],
        "",
        "This file is a prompt artifact only. It must not launch Codex or another process.",
    ]
    if lease:
        lines.extend(
            [
                "",
                f"LEASE_ID={lease['lease_id']}",
                f"LEASE_GENERATION={lease['generation']}",
                f"ACTIVE_WRITER_REPOSITORY={lease.get('active_writer_repository')}",
            ]
        )
    output = root / "handoffs" / run_id / "implementer-attach.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
