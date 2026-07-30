from __future__ import annotations

from pathlib import Path

from .decisions import verify_decision
from .util import load_json, write_json_atomic
from .validation import validate_document

LEGAL_TRANSITIONS = {
    "PLANNED": {"ADMITTED", "ABORTED"},
    "ADMITTED": {"RUNNING", "ABORTED"},
    "RUNNING": {"BLOCKED", "OWNER_ACTION_REQUIRED", "COMPLETE", "ABORTED"},
    "BLOCKED": {"RUNNING", "OWNER_ACTION_REQUIRED", "ABORTED"},
    "OWNER_ACTION_REQUIRED": {"RUNNING", "ABORTED"},
    "COMPLETE": set(),
    "ABORTED": set(),
}
PRIVILEGED = {
    ("PLANNED", "ADMITTED"): "status:admit",
    ("BLOCKED", "RUNNING"): "status:resume",
    ("OWNER_ACTION_REQUIRED", "RUNNING"): "status:resume",
}


def transition_status(
    root: Path,
    run_id: str,
    *,
    target: str,
    expected_generation: int,
    timestamp: str,
    checkpoint: str,
    decision_path: Path | None = None,
) -> Path:
    path = root / "runs" / run_id / "status.json"
    status = load_json(path)
    if status.get("schema_version") != "coord.status.v2":
        raise ValueError("status transition requires coord.status.v2")
    current = str(status.get("state"))
    target = target.upper()
    if target not in LEGAL_TRANSITIONS.get(current, set()):
        raise ValueError(f"Illegal status transition: {current} -> {target}")
    if status.get("generation") != expected_generation:
        raise RuntimeError(
            f"Status generation mismatch: expected {expected_generation}, "
            f"found {status.get('generation')}"
        )
    required_action = PRIVILEGED.get((current, target))
    decision_ref: str | None = None
    if required_action:
        if decision_path is None:
            raise ValueError(f"{current} -> {target} requires decision action {required_action}")
        verification = verify_decision(
            root,
            decision_path,
            run_id=run_id,
            action=required_action,
        )
        if not verification["ok"]:
            raise ValueError(
                "Decision verification failed:\n- " + "\n- ".join(verification["findings"])
            )
        decision_ref = decision_path.relative_to(root).as_posix()

    history = list(status.get("history", []))
    history.append(
        {
            "from": current,
            "to": target,
            "generation": expected_generation + 1,
            "timestamp": timestamp,
            "checkpoint": checkpoint,
            "decision_ref": decision_ref,
        }
    )
    status.update(
        {
            "state": target,
            "generation": expected_generation + 1,
            "revision": int(status.get("revision", expected_generation)) + 1,
            "checkpoint": checkpoint,
            "updated_utc": timestamp,
            "history": history,
        }
    )
    errors = validate_document(status, root)
    if errors:
        raise ValueError("Updated status validation failed:\n- " + "\n- ".join(errors))
    write_json_atomic(path, status)
    return path
