from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import ensure_within, load_json, sha256_file
from .validation import validate_document

ACCEPTED_STATUSES = {"ACCEPTED", "MERGED"}


def _verify_predecessor_chain(
    root: Path,
    decision_path: Path,
    decision: dict[str, Any],
    *,
    run_id: str,
) -> list[str]:
    findings: list[str] = []
    current_path = decision_path
    current = decision
    seen = {decision_path.resolve()}
    while True:
        sequence = current.get("sequence")
        previous_ref = current.get("previous_decision_ref")
        if not isinstance(sequence, int) or sequence < 1:
            break
        if sequence == 1:
            if previous_ref is not None:
                findings.append("sequence 1 decision must not reference a previous decision")
            break
        if not isinstance(previous_ref, str) or not previous_ref:
            findings.append("sequence greater than 1 requires previous_decision_ref")
            break

        previous_path = Path(previous_ref)
        if not previous_path.is_absolute():
            previous_path = root / previous_path
        try:
            previous_path = ensure_within(
                previous_path,
                root,
                label="previous_decision_ref",
            )
        except ValueError as exc:
            findings.append(str(exc))
            break
        resolved_previous = previous_path.resolve()
        if resolved_previous in seen:
            findings.append("decision predecessor chain contains a cycle")
            break
        seen.add(resolved_previous)
        try:
            previous = load_json(previous_path)
        except ValueError as exc:
            findings.append(str(exc))
            break

        prefix = f"decision chain sequence {sequence - 1}"
        for error in validate_document(previous, root):
            findings.append(f"{prefix}: {error}")
        if previous.get("schema_version") != "coord.decision.v2":
            findings.append(f"{prefix}: decision verification requires coord.decision.v2")
        previous_markdown = previous_path.with_suffix(".md")
        if not previous_markdown.is_file():
            findings.append(f"{prefix}: decision Markdown companion missing")
        elif previous.get("markdown_sha256") != sha256_file(previous_markdown):
            findings.append(f"{prefix}: decision Markdown SHA-256 binding mismatch")
        if previous.get("run_id") != run_id:
            findings.append(f"{prefix}: decision run_id mismatch")
        if previous.get("sequence") != sequence - 1:
            findings.append(f"{prefix}: decision sequence is not contiguous")
        if previous.get("status") not in ACCEPTED_STATUSES:
            findings.append(f"{prefix}: decision is not accepted or merged")

        current_path = previous_path
        current = previous
        if current_path == decision_path:
            findings.append("decision predecessor chain contains a cycle")
            break
    return findings


def verify_decision(
    root: Path,
    decision_path: Path,
    *,
    run_id: str,
    action: str,
    lease_id: str | None = None,
    lease_generation: int | None = None,
) -> dict[str, Any]:
    findings: list[str] = []
    try:
        decision_path = ensure_within(decision_path, root, label="decision file")
        decision = load_json(decision_path)
    except ValueError as exc:
        return {"ok": False, "findings": [str(exc)]}
    findings.extend(validate_document(decision, root))
    if decision.get("schema_version") != "coord.decision.v2":
        findings.append("decision verification requires coord.decision.v2")
    if decision.get("run_id") != run_id:
        findings.append(f"run_id mismatch: expected {run_id}")
    if decision.get("status") not in ACCEPTED_STATUSES:
        findings.append("decision status must be ACCEPTED or MERGED")
    if not isinstance(decision.get("sequence"), int) or decision.get("sequence", 0) < 1:
        findings.append("decision sequence must be a positive integer")
    sequence = decision.get("sequence")
    previous_ref = decision.get("previous_decision_ref")
    if sequence == 1 and previous_ref is not None:
        findings.append("sequence 1 decision must not reference a previous decision")
    if isinstance(sequence, int) and sequence > 1:
        findings.extend(
            _verify_predecessor_chain(
                root,
                decision_path,
                decision,
                run_id=run_id,
            )
        )
    if action not in decision.get("authorized_actions", []):
        findings.append(f"decision does not authorize action: {action}")
    if lease_id is not None and decision.get("lease_id") != lease_id:
        findings.append(f"decision does not cover lease_id: {lease_id}")
    if lease_generation is not None and decision.get("lease_generation") != lease_generation:
        findings.append(f"decision does not cover lease generation: {lease_generation}")
    markdown = decision_path.with_suffix(".md")
    if not markdown.is_file():
        findings.append(f"decision Markdown companion missing: {markdown}")
    elif decision.get("markdown_sha256") != sha256_file(markdown):
        findings.append("decision Markdown SHA-256 binding mismatch")
    return {
        "ok": not findings,
        "decision": str(decision_path),
        "decision_id": decision.get("decision_id"),
        "sequence": decision.get("sequence"),
        "findings": findings,
    }
