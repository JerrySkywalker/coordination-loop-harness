"""Validation for the versioned generic Harness Model and Profile Pack contracts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .validation import validate_document

_AXES = ("A", "B", "P", "V", "E", "F", "G", "L")


def validate_harness_model(document: dict[str, Any], repo_root: Path) -> list[str]:
    """Validate generic semantics beyond the JSON Schema shape."""

    findings = validate_document(document, repo_root)
    if findings:
        return findings
    axes = document["axes"]
    if set(axes) != set(_AXES):
        findings.append("axes must contain exactly A, B, P, V, E, F, G, L")
    progress = document["progress_semantics"]
    known_progress = set(axes["P"])
    for field in ("initial_states", "advancing_states"):
        unknown = set(progress[field]) - known_progress
        if unknown:
            findings.append(f"progress_semantics.{field} contains unknown P values")
    if not set(progress["initial_states"]):
        findings.append("progress_semantics.initial_states must not be empty")
    return findings


def validate_profile_pack(
    document: dict[str, Any], model: dict[str, Any], repo_root: Path
) -> list[str]:
    """Validate a concrete Profile Pack against a generic Harness Model."""

    findings = validate_harness_model(model, repo_root)
    findings.extend(validate_document(document, repo_root))
    if findings:
        return findings
    model_ref = document["model_ref"]
    if model_ref["model_id"] != model["model_id"]:
        findings.append("model_ref.model_id does not match the Harness Model")
    model_axes: Mapping[str, list[str]] = model["axes"]
    ledger = document["budget_ledger"]
    domains = ledger["domains"]
    tuple_fields = ledger["tuple_fields"]
    if tuple_fields != model["budget_ledger"]["tuple_fields"]:
        findings.append("budget_ledger.tuple_fields does not match the Harness Model")
    for profile_name, profile in document["profiles"].items():
        _validate_axis_values(
            findings,
            profile_name,
            "allowed_authority_classes",
            profile["allowed_authority_classes"],
            model_axes["A"],
        )
        _validate_axis_values(
            findings,
            profile_name,
            "elasticity_grade",
            [profile["elasticity_grade"]],
            model_axes["B"],
        )
        _validate_axis_values(
            findings,
            profile_name,
            "initial_progress_state",
            [profile["initial_progress_state"]],
            model_axes["P"],
        )
        _validate_axis_values(
            findings,
            profile_name,
            "allowed_layers",
            profile["allowed_layers"],
            model_axes["L"],
        )
        matrix = profile["budget_matrix"]
        if len(matrix) != len(domains):
            findings.append(f"profiles.{profile_name}.budget_matrix must cover every budget domain")
        for row_index, row in enumerate(matrix):
            if len(row) != len(tuple_fields):
                findings.append(
                    f"profiles.{profile_name}.budget_matrix[{row_index}] has the wrong tuple length"
                )
    return findings


def _validate_axis_values(
    findings: list[str], profile_name: str, field: str, values: list[str], known: list[str]
) -> None:
    unknown = set(values) - set(known)
    if unknown:
        findings.append(
            f"profiles.{profile_name}.{field} contains values outside the Harness Model"
        )
