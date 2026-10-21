# Windows local source handoff v1

Status: **SOURCE_PREPARED only**. This contract does not qualify `G-L1-ROOT`,
`G-L1-TIME`, a Worker, a Provider, or effect publication. CLI/MCP and the public
observer continue to return `custody_resolved=false`.

## Trusted inputs and ownership

The Operator fixes `WindowsIssuerConfig`, the exact canonical v2 Lease bytes,
Lease ID/generation, one `WriterTarget`, the nonrenewable `LocalDeadline` bound
to `WindowsHostClock.read` and the actual issuer process creation, one
SYSTEM issuer PID/process-creation pin, one distinct CLF client PID/creation/SID
pin, one local pipe name, and distinct Worker and restricting scratch SIDs
**before** the claim. The client obtains the
expected issuer pin, request and pipe name through a separately protected
Operator startup channel. It must never learn the expected pin from the pipe
response, a Worker, environment activation, or a JSON root claim. How that
startup channel is installed and tested is a future CLF/Operator integration
gate; a caller supplied pin is not itself authority.
CLF supplies `WindowsWriterBinding` only after it creates the participant
suspended and assigns its Job, and before any resume checkpoint. The binding
names its PID and creation, the already fixed Worker and scratch SIDs, and the
same intent/generation/Lease digest. CLF retains
its owned participant and Job handles and a native client `LocalDeadline` with
the exact transmitted boot and interrupt deadline.

`WindowsIssuer.claim_once` atomically burns the retained `LocalDeadline` attempt
before creating the pipe. A second issuer object using that deadline cannot
claim, even after first-attempt failure. Process restart requires reconciliation
with existing Owner admission and a newly admitted intent; no source constructor
can restore or re-anchor the consumed intent.
Its process must have an actual SYSTEM primary token and the pinned native PID
and creation time. `WindowsProtectedCustodyVerifier` has a separate native issuer
self-check for this call; its ordinary observer path still rejects SYSTEM. It
reuses the current v2 Lease/Decision ancestry, exact current bytes, one Writer,
Git common-dir/worktree/branch/HEAD/dirty checks, protected-tree and ancestor
ACL inventory, and the boot-bound deadline. No producer manifest, schema, or
existing public admission gate changes.

The issuer creates **one** first-instance, single-instance, byte-mode named
pipe named `\\.\pipe\CoordinationLoop-<fixed-id>` with
`PIPE_REJECT_REMOTE_CLIENTS`,
an explicit protected DACL, no inheritable handle, and an overlapped finite
connection/read/write. Its client ACE permits data read/write, but not
`FILE_CREATE_PIPE_INSTANCE`. Squatting fails first-instance creation and burns
the attempt. The client opens without an inheritable handle and uses
identification-level SQOS. Both sides close on failure or completion; there is
no accept loop, daemon, retry, process launch, token factory invocation, or ACL
installation here.

The server gets the connected client's kernel pipe PID, opens that process,
and checks the retained PID, creation time and primary-token SID while holding
the process handle across the exchange. It checks the pipe peer and process is
still running afterward. The client token also passes the observer policy: no
trusted grant-capable group, trusted restricting SID, or enableable privilege
beyond `SeChangeNotifyPrivilege`. The client independently checks the connected server
against its protected startup PID, creation time and SYSTEM primary token, and
checks its own primary token, PID and creation against its startup pin. It
samples native boot/interrupt time before connection and at each checkpoint;
CLF still must own suspend/wake containment and schedule those checks.
Neither a hash nor a serialized frame supplies OS origin. A lost channel,
partial/oversized/malformed frame, native query failure, wrong peer, timeout,
changed Lease/Decision/target, or boot/time uncertainty latches the deadline;
a concurrent second claim is refused without disturbing the first claim. The fixed
name, first-instance rule and PID/creation checks fail closed on issuer restart;
the old deadline and issuer cannot be deserialized or renewed. A new issuer
instance requires a **new Owner-admitted intent**, not automatic re-anchoring
of the old one. No new durable one-shot ledger is introduced.

## Wire binding

The request is canonical JSON (`clh.windows-local-claim.v1`) containing the
intent ID, Lease ID/generation/digest, fixed Worker/scratch SIDs, boot ID,
interrupt deadline, and exact
repository, canonical path, worktree root, branch ref and HEAD. The response
(`clh.windows-local-handoff.v1`) binds the request digest, Decision path/digest,
both process pins, boot/deadline, Lease/generation and target. The response
encodes native FILETIME process creation pins and the full 64-bit interrupt
deadline as canonical decimal strings. The client rejects booleans, numbers,
signs, leading zeroes, zero and out-of-range deadline strings. It always says
`custody_resolved=false` and keeps the runtime gate unqualified. The Decision
is opened with native read-only sharing before v2 validation; write and delete
sharing remain denied through the exchange. Its digest names those sealed bytes.

The client acknowledges the response digest and receives a retained
`WindowsHandoffSession`. The issuer keeps the authenticated pipe open. CLF
uses that session on its owning thread; cross-thread use latches HOLD. CLF
may send `pre_spawn_watchdog` heartbeats while suspended creation is underway;
the issuer rechecks protected custody on each one. CLF then calls `bind` once
with its owned process and Job handles. The issuer duplicates only query rights
from the kernel-authenticated pinned client process, checks native process
PID/creation/token and Job containment itself, and retains the copies until
the session closes. Raw binding JSON cannot prove either object. CLF must then
call `checkpoint` in order: `resume` before participant resume,
`effect_publication` before exposing staged effects, `receipt` before Receipt,
and `dag_ack` before DAG acknowledgement. Each call checks the client's native
boot/deadline and owned participant/Job handles, sends the same handoff digest
and fixed `WindowsWriterBinding`, and requires the live issuer's acknowledgement
after its deadline, full current protected Lease/Decision/Git validation, and
native participant/Job observation. CLF must send `watchdog` checkpoints during longer
work; the three-second frame wait fails closed without one. Loss, wrong order,
failed observation or a missed acknowledgement closes the session and refuses
later checkpoints. CLF must contain and settle its owned Job on loss.

These checks continue after `effect_publication`; strict Git admission remains
in force, so a dirty worktree or changed HEAD produces HOLD. A post-publication
acknowledgement says only that the producer's protected state and writer snapshot
still passed at that instant. CLF alone validates and accounts for actual staged
effects. Heartbeats do not renew the nonrenewable deadline (at most 600 seconds).

A stage acknowledgement and writer snapshot remain source evidence only. They
do not qualify atomic launch, actual filesystem denial, effect/Receipt/DAG
integration, or a runtime grant. A copied response or synthetic SOURCE_TEST
fixture has no offline bearer semantics.

## Remaining host qualification

No protected SYSTEM process, pipe, distinct-principal ACL denial, suspend,
restart, inherited-handle denial, restricted Worker, Job, model call or real
Provider was exercised for this source change. The exact Windows build must
prove the native boot ABI, pipe peer/token query permissions, process identity
checks, cancellation/cleanup, protected startup-pin delivery, actual filesystem
denials, wake settlement and CLF containment before any operational claim.
In particular, Microsoft documents `GetNamedPipeServerProcessId` with a
server-created handle; the client-end query in this candidate must be checked
on the exact host. If unavailable it refuses; there is no payload fallback.
The Operator and kernel are trusted; a malicious administrator/kernel is outside
this boundary. Local interrupt time is not authenticated UTC.

Windows API references: [CreateNamedPipeW](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-createnamedpipew),
[GetNamedPipeClientProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeclientprocessid),
[GetNamedPipeServerProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeserverprocessid),
[OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess),
[CancelIoEx](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex).
