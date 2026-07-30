from __future__ import annotations

from typing import Any

from .util import canonical_json_bytes, sha256_bytes


def evaluate_blocker(
    *,
    code: str,
    summary: str,
    scope: str,
    recurrence_count: int,
    retry_limit: int,
    owner_action_required: bool = False,
) -> dict[str, Any]:
    if recurrence_count < 1:
        raise ValueError("recurrence_count must be at least 1")
    if retry_limit < 0:
        raise ValueError("retry_limit cannot be negative")
    normalized = {
        "code": code.strip().upper(),
        "summary": " ".join(summary.split()),
        "scope": scope.strip().replace("\\", "/").casefold(),
    }
    fingerprint = sha256_bytes(canonical_json_bytes(normalized))
    escalation = owner_action_required or recurrence_count > retry_limit
    return {
        **normalized,
        "fingerprint": fingerprint,
        "recurrence_count": recurrence_count,
        "retry_limit": retry_limit,
        "escalation_required": escalation,
        "owner_action_required": owner_action_required,
        "background_polling": False,
        "external_mutation": False,
    }
