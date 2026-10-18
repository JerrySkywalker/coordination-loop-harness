# T1 minimal protected-operation request — NOT AUTHORIZED / NOT EXECUTED

This is a proposed next scope, not an executable approval. Exact source PR HEAD,
accepted merge/package hash, host identity/Windows build, current boot GUID, all
paths/SIDs, action list, start/deadline and rollback must be frozen in a **new**
Owner grant before host changes. Do not interpret approval of the source PR as
approval of this request. T1 has installed nothing.

1. **Disposable qualification host preferred.** Adopt an exact supported Windows
   build and accepted source package. Request a separate one-shot elevated
   Operator invocation, not a permanent service or global policy change.
2. **Proposed fixed authority root:**
   `C:\ProgramData\CoordinationLoop\LocalPilot\T1-<accepted-head-12>\authority`;
   separate `locks`, `scratch` and `receipts` beneath that fixed qualification
   root. Freeze the fully expanded paths and inspect the existing ancestors
   before approval. If unsafe ancestors require modification, request those exact
   ancestor ACE edits separately; this request does not permit changing unrelated
   `ProgramData` or volume permissions. An existing root is never overwritten.
3. **Distinct principal:** create one temporary, non-admin local Worker account
   `CLPilotWorkerT1` and one dedicated restricting local SID/group
   `CLPilotScratchT1`, or adopt exact existing disposable identities after Owner
   inspection. Record discovered SIDs in the grant; no passwords/credentials in
   Git/logs. Issuer is the trusted one-shot SYSTEM Operator with fixed package and
   configuration. Never elevate the Worker or reuse the Owner's token as proof.
4. **ACL scope:** only the enumerated new root/authority/locks/receipts entries
   receive protected Operator-only mutation ACLs. Worker receives read-only
   authority access and write access only inside the isolated scratch tree;
   the unique restricting SID has no unrelated writable grants. Pin every
   descendant ACL and binary/runtime read requirement in the final installation
   manifest. `plan_windows_acls` is preparatory text, not installation authority.
5. **One bounded qualification process:** a non-AI test executable under a
   restricted primary token/separate desktop, suspended creation, Job assignment
   before resume, kill-on-close/no breakaway, no writable inherited authority
   handles. No Codex/model endpoint, real WorkOrder, CLE DAG or production action.
   CLF must own the future launcher; a separately scoped implementation/host-test
   grant is required for that bridge. T1's read-only inspector alone cannot prove
   atomic launch or filesystem confinement.
6. **Exact negative probes:** attempted Root/Lease/Decision/Receipt overwrite,
   deletion, rename and ACL takeover; extra/nonallowlisted output, ancestor
   delete-child, reparse/hardlink substitution, same-token control, inherited
   handle escape, Job breakaway/grandchild escape and Root abnormal exit. Require
   actual `ACCESS_DENIED`/terminal evidence and allowlisted scratch positive.
   Uncertain effects remain HOLD with no automatic retry.
7. **Separate disruptive time consent:** specify one suspend/resume and, on a
   disposable host only, hibernate/reboot test. Each task has a nonrenewable
   <=600-second cap. Preserve original intent and verify stale-task refusal;
   never restore/re-anchor it across restart. No clock, NTS, W32Time, network,
   firmware or global power-policy change is requested.
8. **Cleanup:** after child/Job settlement and evidence inspection, disable/remove
   only newly created disposable identities/root listed in the grant, under a
   separately confirmed exact cleanup manifest. Preserve unknown effects and
   local sanitized evidence. Do not recursively reclaim an existing resource.

Grant ceilings: one qualification host, one issuer invocation, one Worker, one
explicit attempt per named vector, bounded task/total deadline and output sizes.
Any unknown path/SID/package, inherited access, unsafe ancestor, incomplete child
terminal identity or required scope expansion stops that operation. A later
runtime pilot additionally needs governance, source merges, provider and separate
success/interruption Owner grants. No production or existing Campaign is involved.
