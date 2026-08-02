from __future__ import annotations

from pathlib import Path
from typing import Any

from .repository import verify_repository
from .util import ensure_within, load_json, write_json_atomic


def bind_goal(
    root: Path,
    run_id: str,
    *,
    repository_root: Path,
    state_root: Path,
    expected_origin: str | None = None,
    stable_branch: str | None = None,
    expected_input_sha: str | None = None,
    expected_output_sha: str | None = None,
    lease_path: Path | None = None,
) -> list[Path]:
    state_root = state_root.resolve()
    output_root = ensure_within(state_root / run_id, state_root, label="bound goal output")
    goal = load_json(root / "plans" / run_id / "goal.json")
    verification = verify_repository(
        repository_root,
        expected_origin=expected_origin,
        stable_branch=stable_branch,
        expected_sha=expected_input_sha,
        offline=True,
    )
    if not verification["ok"]:
        raise ValueError("Repository binding failed:\n- " + "\n- ".join(verification["findings"]))
    lease = load_json(lease_path) if lease_path else None
    decision_refs = goal.get("decision_refs", [])
    manifest: dict[str, Any] = {
        "schema_version": "coord.coordinator-manifest.v1",
        "run_id": run_id,
        "role": goal["role"],
        "active_phase": goal["active_phase"],
        "repository": {
            "identity": goal["write_repository"],
            "canonical_path": verification["canonical_path"],
            "origin_url": verification["origin"],
            "stable_branch": stable_branch or verification["branch"],
            "exact_input_sha": expected_input_sha or verification["head"],
            "expected_output_sha": expected_output_sha,
        },
        "allowed_write_surfaces": goal.get("allowed_paths", []),
        "prohibited_write_surfaces": goal.get("forbidden_paths", []),
        "required_milestones": goal.get("required_milestones", goal.get("deliverables", [])),
        "lease": (
            {"lease_id": lease["lease_id"], "generation": lease["generation"]} if lease else None
        ),
        "decision_references": decision_refs,
        "process_launch_allowed": False,
        "local_only": True,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "coordinator-manifest.json"
    write_json_atomic(manifest_path, manifest)
    bound_goal = output_root / "bound-goal.md"
    bound_goal.write_text(
        "\n".join(
            [
                f"# Bound Goal: {run_id}",
                "",
                f"- Role: `{goal['role']}`",
                f"- Active phase: `{goal['active_phase']}`",
                f"- Repository: `{goal['write_repository']}`",
                f"- Canonical path: `{verification['canonical_path']}`",
                f"- Origin URL: `{verification['origin'] or 'NONE'}`",
                f"- Stable branch: `{stable_branch or verification['branch'] or 'DETACHED'}`",
                f"- Exact input SHA: `{expected_input_sha or verification['head']}`",
                f"- Expected output SHA: `{expected_output_sha or 'UNKNOWN'}`",
                f"- Lease: `{lease['lease_id'] if lease else 'NONE'}`",
                f"- Lease generation: `{lease['generation'] if lease else 'NONE'}`",
                "",
                "## Allowed write surfaces",
                "",
                *[f"- `{item}`" for item in goal.get("allowed_paths", [])],
                "",
                "## Prohibited write surfaces",
                "",
                *[f"- `{item}`" for item in goal.get("forbidden_paths", [])],
                "",
                "## Required milestones",
                "",
                *[
                    f"- {item}"
                    for item in goal.get("required_milestones", goal.get("deliverables", []))
                ],
                "",
                "## Decision references",
                "",
                *[f"- `{item}`" for item in decision_refs],
                "",
                "This local artifact does not authorize process launch.",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    attach = output_root / "implementer-attach.md"
    attach.write_text(
        "\n".join(
            [
                f"RUN_ID={run_id}",
                f"ROLE={goal['role']}",
                f"ACTIVE_PHASE={goal['active_phase']}",
                f"REPOSITORY={goal['write_repository']}",
                f"CANONICAL_PATH={verification['canonical_path']}",
                f"INPUT_SHA={expected_input_sha or verification['head']}",
                f"LEASE_ID={lease['lease_id'] if lease else 'NONE'}",
                f"LEASE_GENERATION={lease['generation'] if lease else 'NONE'}",
                "",
                "Read bound-goal.md and coordinator-manifest.json before any write.",
                "Remain inside the listed write surfaces and stop on any binding mismatch.",
                "This prompt is generated for manual review and attachment only.",
                "PROCESS_STARTED=false",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    return [bound_goal, manifest_path, attach]
