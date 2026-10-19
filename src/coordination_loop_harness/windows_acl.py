"""Pure ACL plans for separately authorized installation; never apply a DACL."""

from dataclasses import dataclass

from .windows_custody import ADMINISTRATORS, SYSTEM
from .windows_security import WindowsBoundaryHold, is_account_sid


@dataclass(frozen=True)
class WindowsAclPlan:
    authority_sddl: str
    scratch_sddl: str
    runtime_gate: str = "ACTUAL_DISTINCT_PRINCIPAL_DENIAL_UNQUALIFIED"


def plan_windows_acls(*, issuer_sid: str, worker_sid: str, scratch_sid: str) -> WindowsAclPlan:
    """Return strict protected DACL text for one authority tree + isolated scratch.

    This plan does not authorize installation and does not grant a Worker access
    to the actual target repository. The restricting SID must be uniquely scoped
    and absent from unrelated writable resources. Runtime/binary read rights and
    inherited-handle restrictions require a separate exact deployment plan.
    """
    sids = (issuer_sid, worker_sid, scratch_sid)
    if (
        issuer_sid != SYSTEM
        or not is_account_sid(worker_sid)
        or not is_account_sid(scratch_sid)
        or len(set(sids)) != 3
        or issuer_sid == ADMINISTRATORS
        or worker_sid in {SYSTEM, ADMINISTRATORS}
        or scratch_sid in {SYSTEM, ADMINISTRATORS}
    ):
        raise WindowsBoundaryHold("HOLD_DISTINCT_FIXED_SIDS_REQUIRED")
    authority = (
        f"O:{issuer_sid}G:SYD:P(A;OICI;FA;;;{issuer_sid})"
        f"(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FR;;;{worker_sid})"
        f"(A;OICI;FR;;;{scratch_sid})"
    )
    scratch = (
        f"O:{issuer_sid}G:SYD:P(A;OICI;FA;;;{issuer_sid})"
        f"(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        f"(A;OICI;FRFW;;;{worker_sid})(A;OICI;FRFW;;;{scratch_sid})"
    )
    return WindowsAclPlan(authority, scratch)
