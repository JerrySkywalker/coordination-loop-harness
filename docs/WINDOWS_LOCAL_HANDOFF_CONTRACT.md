# Windows local source handoff v1

Status: **SOURCE_PREPARED only**. This contract does not qualify `G-L1-ROOT`,
`G-L1-TIME`, a Worker, a Provider, or effect publication. CLI/MCP and the public
observer continue to return `custody_resolved=false`.

## Trusted inputs and ownership

The Operator fixes `WindowsIssuerConfig`, the exact canonical v2 Lease bytes,
Lease ID/generation, one `WriterTarget`, the nonrenewable `LocalDeadline` bound
to `WindowsHostClock.read` and the actual issuer process creation, one
SYSTEM issuer PID/process-creation pin, one distinct CLF client PID/creation/SID
pin, and one local pipe name **before** the claim. The client obtains the
expected issuer pin, request and pipe name through a separately protected
Operator startup channel. It must never learn the expected pin from the pipe
response, a Worker, environment activation, or a JSON root claim. How that
startup channel is installed and tested is a future CLF/Operator integration
gate; a caller supplied pin is not itself authority.

`WindowsIssuer.claim_once` burns the retained attempt before creating the pipe.
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
no server loop, daemon, retry, process launch, token factory invocation, or ACL
installation here.

The server gets the connected client's kernel pipe PID, opens that process,
and checks the retained PID, creation time and primary-token SID while holding
the process handle across the exchange. It checks the pipe peer and process is
still running afterward. The client independently checks the connected server
against its protected startup PID, creation time and SYSTEM primary token, and
checks its own primary token, PID and creation against its startup pin. It
samples native boot/interrupt time before connection and after the final frame;
CLF still must own suspend/wake containment and every later checkpoint.
Neither a hash nor a serialized frame supplies OS origin. A lost channel,
partial/oversized/malformed frame, native query failure, wrong peer, timeout,
changed Lease/Decision/target, boot/time uncertainty, or concurrent second
claim leaves the one-shot attempt spent and the deadline latched. The fixed
name, first-instance rule and PID/creation checks fail closed on issuer restart;
the old deadline and issuer cannot be deserialized or renewed. A new issuer
instance requires a **new Owner-admitted intent**, not automatic re-anchoring
of the old one. No new durable one-shot ledger is introduced.

## Wire binding

The request is canonical JSON (`clh.windows-local-claim.v1`) containing the
intent ID, Lease ID/generation/digest, boot ID, interrupt deadline, and exact
repository, canonical path, worktree root, branch ref and HEAD. The response
(`clh.windows-local-handoff.v1`) binds the request digest, Decision path/digest,
both process pins, boot/deadline, Lease/generation and target. The response
encodes native FILETIME process creation pins as canonical decimal strings,
because their 64-bit values exceed JSON's portable safe-integer range. It
always says `custody_resolved=false` and keeps the runtime gate unqualified.
The client acknowledges the exact response digest; the issuer rechecks the
deadline and sends a final digest frame. The client accepts only that final
frame on the same authenticated connection. A lost response, acknowledgement,
or final frame refuses the exchange and never authorizes a retry.
It has no offline bearer semantics. The future CLF consumer may use the
exchange only on its authenticated live pipe, followed by its own authorized
process, containment, effect, time and receipt checks. A copied response or
synthetic SOURCE_TEST fixture cannot admit a Worker or publish effects.

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
