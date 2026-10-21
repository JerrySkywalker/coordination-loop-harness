# Windows Local-First T1 source candidate

Status: `LOCAL_HOST_TIME_SOURCE_PREPARED` / `LOCAL_CUSTODY_CANDIDATE_PREPARED`.
The [source-only authenticated handoff contract](WINDOWS_LOCAL_HANDOFF_CONTRACT.md)
adds one retained issuer claim and a native named-pipe peer check. It does not
change either status or qualify the protected host boundary.
These modules are opt-in source APIs. No public CLI/MCP runtime gate is enabled.
The separately versioned [fixed-result source candidate](WINDOWS_LOCAL_RESULT_CONTRACT.md)
adds a private post-effect observation; legacy admission stays strict and no
protected runtime boundary is qualified by that candidate.
They do not constitute `LOCAL_BOUNDARY_QUALIFIED`, authenticated UTC, or an
executable Owner grant. Existing v2 Lease/Decision schemas and public refusals
remain unchanged. Program is not a runtime dependency.

## Local deadline

`local_time.WindowsHostClock` samples Windows interrupt time through the realtime
API set. Interrupt time includes sleep/hibernation; the unbiased counter excludes
it. The deadline uses the interrupt count, with no wall-clock conversion or expiry
grace. The independent boot GUID uses dynamically linked
`NtQuerySystemInformation(SystemBootEnvironmentInformation=90)`. This is an
**internal, build-sensitive Windows ABI**. Missing exports, nonzero status, wrong
record size or a zero GUID refuse operation; no tick-derived/WMI/wall-clock boot
fallback exists. A successful read does not qualify the boot ABI on another build.

`LocalDeadline` retains an immutable intent (ID, generation, exact Lease digest,
boot/process/source anchor and deadline) inside the trusted Operator process.
It takes the earliest of start + approved duration (1–600 seconds) and a shorter
interrupt-time cap. It exposes no renewal, re-anchor, serialization or restore
API. Recreating it is a new unadmitted intent, never a way to resume an old task;
the source issuer atomically burns one claim attempt on the retained deadline
across issuer instances. Restart requires existing Owner admission reconciliation
and a new intent. The live handoff session requires ordered writer-bound checks
through DAG acknowledgement; CLF Claim ownership and protected runtime
qualification remain future integration gates.

All checkpoints (`admission`, `pre_spawn`, `watchdog`, `resume`,
`effect_publication`, `receipt`, `dag_ack`) read a fresh sample. Equality at expiry
denies. Boot/source/process mismatch, counter rollback, changed intent, a missing
read, a sample span above 20ms, a changed sleep bias outside that sampling
uncertainty or an observation gap above 5s latches HOLD. The 20ms sample allowance
is **not** an expiry extension or authenticated time error bound. Tiny/ambiguous
suspends cannot be proven safe from samples alone: mandatory isolated staging,
actual process containment and before-publication revalidation remain necessary.
An issuer/watchdog failure must invalidate the guard and settle the owned Job.

Polling after wake cannot prevent every Worker write. This module launches no
watchdog and makes no containment claim. Unexpected restart, hibernation or loss
of continuity requires reconciliation and a fresh Owner decision, not an automatic
new duration. No NTS, remote clock, WSL BOOTTIME, clock/service adjustment or
independently authenticated UTC claim is involved.

## Fixed issuer and existing durable semantics

`WindowsIssuerConfig` is trusted **Operator startup configuration**, not an RPC
argument or authority token. This first verifier permits only the actual SYSTEM
issuer SID; account-shaped/group SIDs cannot substitute. Pin the exact accepted producer package/tree, fixed
authority/lock paths, distinct issuer SID and exact v2 producer-manifest bytes.
`WindowsProtectedCustodyVerifier` is not wired into `create_server` and cannot be
selected by a caller environment flag or alternate root. A protected local-only
`CLH-ISSUANCE-ROOT.json` must exactly name the issuer, CLH ownership/policy and
producer-manifest SHA. The manifest pin is not a substitute for deployment package
integrity or authenticated IPC. The separate named-pipe source contract adds
cross-process native peer checks, still pending protected host qualification.

The Windows backend reuses the existing CLH Lease/Decision/Git validation functions
for current canonical Lease bytes, active generation/state, complete accepted Decision ancestry,
Markdown/candidate digest bindings and legacy UTC **refusal** checks. The narrow
Windows orchestration also retains every archived-candidate/immutable-lineage
check. The manifest-bound published `leases.py` and producer manifest remain byte
unchanged; no OS issuer hook is inserted into that artifact. Those UTC
checks add a conservative rejection; they supply no authenticated UTC evidence.
It additionally binds the immutable L1 intent, requires exactly one Writer and
uses the existing Git verifier for actual repository/common-dir/worktree/branch,
HEAD, dirty state and Git-lock refusals.

The read-only Windows backend opens entries without write/delete sharing,
inspects handle-bound file identity/final path/owner/DACL, and inventories ancestors
and descendants before/after validation. Reparse/short-name/path aliases,
multi-link files, absent or unsupported ACL evidence, unsafe owner/ACE mutation
rights, unbounded traversal and observed replacement refuse. Root descendants
need protected DACLs and retain the strict non-Operator mutation refusal.
Ancestors use a separate existing-directory-chain policy: effective namespace
deletion, rename and security takeover remain forbidden; inherit-only defaults
are evaluated on each actual child instead of being mistaken for parent access.
Only the exact Windows servicing SID may additionally own/mutate ancestors; it
cannot issue CLH authority, own the protected subtree or be a Writer. See the
[ancestor policy and its nonempty/reparse invariant](WINDOWS_ANCESTOR_ACL_POLICY.md).
The observer must differ from the issuer and lack privileged or enableable trusted
groups and privileges other than traversal notification. This deliberately rejects
many ordinary user-owned development paths.
The separate trusted issuer path checks the current native SYSTEM primary token,
PID and process creation against Operator startup pins; it does not relax this
observer policy or allow a caller-selected issuer SID.

All snapshots still return `custody_resolved=false`. Inventories do not provide
atomic issuance/CAS, a malicious-admin defense, installed authenticated consumer
transport, or actual filesystem denial. Existing trusted CLH `acquire`/Decision mechanisms
remain the issuer's durable producer; there is no new schema or alternate ledger.

## Restricted Writer, ACL and Job bridge

`plan_windows_acls` produces SDDL text only. The authority tree allows the fixed
issuer/SYSTEM/Administrators to mutate; Worker and scratch-restricting SID read
access grants no mutation and satisfies both restricted-token access checks.
Isolated scratch grants Worker and a unique restricting SID read/write access.
This plan does not authorize installation or target-repository access. Every
authority descendant needs an explicitly protected ACL; inherited-only defaults
are insufficient. An installer must also establish safe ancestor ACLs, reject
aliases and prove no previously inherited writable authority handles exist.

`restricted_writer_token` is an explicit source-only factory, never called by
CLI/MCP or tests with a real token. It uses `CreateRestrictedToken` with
`DISABLE_MAX_PRIVILEGE` and one scratch restricting SID (no `WRITE_RESTRICTED`
weakening), then inspects the actual returned primary token, principal, remaining
privileges and restricting SID. Both ordinary and restricting-SID access checks
must pass; the scratch SID must not grant writes elsewhere. Handles close on both
success and refusal. It creates no account, ACL, Job, process, desktop or service.

CLF owns the launcher and may reuse its existing owned-process Job primitive.
The provider-neutral bridge is owned native process/Job handles plus an Operator-
retained `WindowsWriterBinding` (exact Worker/scratch SID, PID/creation and Lease
intent ID/generation/digest), checked against the retained deadline before and
after observation. No cross-product source import or caller `custody=true` is
accepted. `WindowsWriterInspector` reads the actual token, PID/creation identity,
Job membership, kill-on-close and breakaway flags, active member list and exit.
Policy tests refuse breakaway, missing identity and unsettled children. Returned
snapshots always retain the runtime HOLD, including zero parent exit/zero members.

Before any operational use CLF must prove restricted-primary-token creation on a
separate desktop, suspended creation → Job assignment → resume with no pre-Job
execution, no inherited writable handles, protected Job ownership/no breakaway,
actual participant and child terminal identities, and effective deny-outside-
scratch. CLH does not launch/manage Workers. An observation cannot reconstruct
startup atomicity or qualify child success. All target publication stays behind
fresh time/custody/process/effect checks and the separately qualified consumer path.

## Verification and pending Owner gate

`tests/test_local_time.py` and `tests/test_windows_custody.py` exercise deterministic
expiry/reboot/rollback/continuity, immutable cap/generation/digest, ACL/token/Job
negatives, token-factory cleanup and the real existing v2 Lease/Decision/Git
validation with **only the uninstalled OS inventory mocked** for semantic positives.
Read-only native tests sample current counters/boot/process, inspect one test-owned
directory and refuse same-principal protection. They never suspend/reboot the
machine, alter ACLs/accounts/services or create a real restricted Worker/Job.

Actual sleep/hibernate/reboot tests, different-principal attempted writes to
Root/Lease/Decision/Receipt, allowed scratch versus denied unrelated paths, Root
crash/wake, inherited-handle, breakaway and descendant-terminal trials remain
`HOLD_NOT_AUTHORIZED_NOT_EXECUTED`. See [the minimal installation request](
WINDOWS_LOCAL_BOUNDARY_OWNER_REQUEST.md) before any such operation. Source CI
never changes this disposition.

References: [interrupt time](https://learn.microsoft.com/en-us/windows/win32/sysinfo/interrupt-time),
[precise interrupt counter](https://learn.microsoft.com/en-us/windows/win32/api/realtimeapiset/nf-realtimeapiset-queryinterrupttimeprecise),
[unbiased counter](https://learn.microsoft.com/en-us/windows/win32/api/realtimeapiset/nf-realtimeapiset-queryunbiasedinterrupttimeprecise),
[internal system-information API caveat](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntquerysysteminformation),
[restricted tokens](https://learn.microsoft.com/en-us/windows/win32/secauthz/restricted-tokens),
[Job containment](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
