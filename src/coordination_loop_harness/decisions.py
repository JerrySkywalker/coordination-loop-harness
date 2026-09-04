from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import (
    ensure_within,
    is_repository_relative_path_v2,
    is_safe_json_integer,
    load_json,
    require_safe_id,
    sha256_file,
)
from .validation import validate_document

ACCEPTED_STATUSES = {"ACCEPTED", "MERGED"}


def _verify_predecessor_chain(
    root: Path,
    decision_path: Path,
    decision: dict[str, Any],
    *,
    run_id: str,
    require_portable_refs: bool = False,
) -> list[str]:
    findings: list[str] = []
    current_path = decision_path
    current = decision
    seen = {decision_path.resolve()}
    while True:
        sequence = current.get("sequence")
        previous_ref = current.get("previous_decision_ref")
        if not is_safe_json_integer(sequence) or sequence < 1:
            findings.append("decision predecessor sequence must be a positive safe integer")
            break
        if sequence == 1:
            if previous_ref is not None:
                findings.append("sequence 1 decision must not reference a previous decision")
            break
        if not isinstance(previous_ref, str) or not previous_ref:
            findings.append("sequence greater than 1 requires previous_decision_ref")
            break
        if require_portable_refs and not is_repository_relative_path_v2(previous_ref):
            findings.append(
                "previous_decision_ref must use portable repository-relative path syntax"
            )
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
    require_candidate_digest: bool = False,
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
    if not is_safe_json_integer(decision.get("sequence")) or decision.get("sequence", 0) < 1:
        findings.append("decision sequence must be a positive safe integer")
    sequence = decision.get("sequence")
    previous_ref = decision.get("previous_decision_ref")
    if sequence == 1 and previous_ref is not None:
        findings.append("sequence 1 decision must not reference a previous decision")
    if is_safe_json_integer(sequence) and sequence > 1:
        findings.extend(
            _verify_predecessor_chain(
                root,
                decision_path,
                decision,
                run_id=run_id,
                require_portable_refs=require_candidate_digest,
            )
        )
    if action not in decision.get("authorized_actions", []):
        findings.append(f"decision does not authorize action: {action}")
    decision_lease_generation = decision.get("lease_generation")
    if decision_lease_generation is not None and not is_safe_json_integer(
        decision_lease_generation
    ):
        findings.append("non-null lease_generation must be a safe integer")
    if require_candidate_digest:
        decision_lease_id = decision.get("lease_id")
        if not isinstance(decision_lease_id, str):
            findings.append("lease decision requires a non-null lease_id")
        else:
            try:
                require_safe_id(decision_lease_id, "decision lease_id")
            except ValueError as exc:
                findings.append(str(exc))
        if not is_safe_json_integer(decision_lease_generation) or decision_lease_generation < 1:
            findings.append("lease decision requires a positive safe-integer lease_generation")
        digest = decision.get("lease_candidate_sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            findings.append("lease decision requires a non-null candidate SHA-256 binding")
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
        "scope": decision.get("scope"),
        "authorized_actions": decision.get("authorized_actions"),
        "lease_candidate_sha256": decision.get("lease_candidate_sha256"),
        "previous_decision_ref": decision.get("previous_decision_ref"),
        "findings": findings,
    }
