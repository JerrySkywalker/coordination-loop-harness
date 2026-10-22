# Fixed local result handoff — source candidate

This is a new, opt-in source contract. It is not adoption of changed v1 semantics,
protected runtime qualification, permission to publish, or a real Receipt. Its
normative change needs exact Owner source acceptance. Public CLI/MCP gates and
`WindowsProtectedCustodyVerifier.verify()` continue to refuse runtime custody and
require a clean Writer. The Lease v2 schema and ordinary release semantics remain
unchanged; the producer manifest identifies the updated implementation bytes.

## Version and fixed scope

The trusted SYSTEM Operator calls `WindowsIssuer.claim_result_once()` explicitly.
Its startup constructor must fix `result_release_ref` to one repository-relative
terminal-candidate path inside the protected issuance Root. A missing fixed ref
refuses and consumes the attempt. The client cannot supply this path, candidate,
release Decision, outcome or permission through a wire frame.
`claim_once()` remains the existing v1 contract and rejects the new request. There
is no fallback, automatic upgrade, environment activation or caller-selected path.
The new claim is `clh.windows-local-result-claim.v1`; its response is
`clh.windows-local-result-handoff.v1`. They retain the existing canonical UTF-8/LF
JSON keys and framing. The claim/response IDs select exactly this fixed policy:

- One Lease-bound `local/` repository whose canonical root is the Writer root,
  with an ordinary `.git` directory; linked Writer worktrees are unsupported.
- No configured Git remote of any name, including a non-`origin` remote.
- Create-new `M1/result.md`, strict UTF-8 and at most 8192 bytes.
- No tracked/index, HEAD/ref, local configuration, worktree registration, extra
  untracked or ignored side effect. No commit, push or automatic reconciliation.

The response digest binds the selected version, request digest, actual process
pins, target, Lease/generation, boot and nonrenewable deadline. Serialized hashes
and frames confer no authority. All existing native SYSTEM/client peer, protected
startup channel, one-attempt, current protected Lease/Decision ancestry and live
owned-handle checks remain mandatory. This contract starts no process or publisher.

## One-way phases

Admission, pre-spawn heartbeats, bind, resume, prepublication watchdogs and
`effect_publication` use the existing strict clean verifier. Additional fixed-result
checks retain the exact Git identity/control snapshots, zero remotes and absent
result. The native participant must have exit code zero and its owned Job must
have zero active processes before the `effect_publication` acknowledgement.
This parent/Job observation does not prove every descendant terminal identity;
CLF still owns that proof and the operational gate remains unqualified.

After that ACK, CLF alone publishes its validated staged bytes into a freshly
created, fixed-Client-owned file. It must copy the validated bytes; renaming or
linking a Worker-owned scratch inode into the target is not publication. The
issuer checks the actual file owner against the native authenticated Client SID.
Before
the next three-second frame deadline, CLF must finish publication and send its
next checkpoint on the same owner thread. Every later watchdog, `receipt` and
`dag_ack` requires the result to exist. There is no intermediate acceptance of
either a clean or dirty tree, longer timeout, renewal or retry. Missing, late or
uncertain publication permanently holds the attempt and retains its actual effects.

The private post-effect path reuses full current protected Lease/Decision lineage,
active generation, exact serialized bytes, protected-tree brackets, acquisition
expiry and boot/deadline checks. Only its Writer-state predicate differs: exact
unchanged Git identity/HEAD/branch/control state, no active Git lock, no tracked
change and exactly the one untracked result, with ignored files explicitly checked.
It cannot be selected by public `verify()` or an RPC root/custody assertion.

The issuer inspects the native exact path and ordinary single-link file, refuses
case/path aliases and reparse/type substitution, reads at most 8193 bytes and
strictly decodes UTF-8. On first observation it retains a native read-only seal
denying write/delete sharing through DAG acknowledgement, plus native file/parent
identity and the content size/hash. Later observations must match. The seal fails
if a conflicting writable handle already exists; it is not proof of all ACL or
startup isolation properties.

Post-effect ACKs contain exactly one additional `published_result` object:
`{"path":"M1/result.md","sha256":"<64 lowercase hex>","size":<0..8192 integer>}`.
Its canonical bytes are bound to the live handoff/stage. The client checks the
exact shape, fixed path, integer/size/digest types and immutability, and retains
the proof only in the live session. It also requires native terminal observation
before publication and at later checkpoints. CLF must compare this observation
with its staged/actual effect and bind a genuinely persisted Receipt to the same
WorkOrder/Session/Claim/generation and file hash. CLH neither manufactures a
Receipt nor advances CLE's DAG. Every source snapshot retains `custody_resolved=false`.

## Result-aware terminal settlement

Legacy Lease acquire/expand/release operations retain their strict Writer checks.
A post-effect observation or client `dag_ack` request alone does not release the
Lease. At the final actual `dag_ack` checkpoint only, the retained SYSTEM issuer
loads the Operator-fixed terminal candidate. It must be an existing v2 RELEASED
candidate at generation +1, preserving all ordinary immutable fields, with the
exact existing Owner release Decision, predecessor chain, scope, candidate digest,
expiry authority and actual outcome hash. Missing or invalid material stays HOLD.
CLH does not manufacture a Receipt, DAG commit, outcome or Owner Decision: those
must be supplied by their qualified owners. The client checkpoint is no proof of
CLE acceptance. The existing release Decision is the terminal mutation authority.

The issuer seals the terminal candidate, release Decision/Markdown and outcome
through the existing mutex/Git-guard/atomic publication transaction. Its private
Writer predicate reuses the retained immutable sole-result observation instead
of public clean admission. Each check, including the post-fsync prepublication
check, requires the exact original active Lease, current protected lineage,
native SYSTEM identity, pinned protected Root identity/DACL, live authenticated
Client, terminal participant/Job observation and unexpired boot deadline. Git
guards are accepted only with their exact owned identity and marker; no path
allowlist or general dirty-tree permission is added.

The future protected transaction copies the current Lease's exact DACL onto its
own SYSTEM-owned temporary replacement with PROTECTED_DACL_SECURITY_INFORMATION,
checks the readback and uses the existing write-through atomic publication. It
never installs a Root or changes existing resource/ancestor ACLs. Complete
protected trees are checked again after owned transient mutex/temp cleanup. The
ordinary inherited temporary DACL cannot silently become a weakened final Lease.
This native setter is mocked in SOURCE_TEST and has not been exercised on a
protected Root. Actual DACL preservation remains part of G-L1-ROOT qualification.

Only a read-back exact terminal Lease permits the final ACK's additional
`released_lease` object, containing exactly `lease_id`, `generation` and
`lease_sha256`. The client requires its original ID and generation +1, a valid
digest and the unchanged result proof. All values remain authenticated live
observations, not transferable authority; `custody_resolved` remains false.
Loss/expiry after publication can leave the Lease genuinely RELEASED without
an accepted ACK. Preserve that state and the result, HOLD and reconcile; never
rollback, delete the file, retry the intent, or claim Receipt/DAG success.

## Compatibility and source evidence

Compatibility window: existing v1 peers remain v1, including strict post-publication
clean refusal. The result claim has a distinct response ID and handoff digest;
mixed versions fail before resume. Existing control frames remain v1-shaped, with
the post-effect proof required only inside the explicitly selected result session.
There is no offline bearer capability or cross-product implementation import.
This unaccepted Draft contract now requires terminal settlement; the earlier
Draft's observation-only frames are not accepted as final settlement evidence.

Producer identity is the exact accepted CLH commit/tree and package bytes recorded
in its Owner Merge Pack, together with these fixed wire IDs. Canonical positive
and negative source vectors are in `tests/test_windows_publication.py`; existing
issuer/capability/custody tests preserve v1 and native-origin refusals. No new
durable Lease schema or generic artifact/evidence framework is introduced.

SOURCE_TEST uses temporary no-remote Git repositories, real file bytes and explicit
mocks for uninstalled protected OS custody. The optional native test seals only a
test-owned ordinary file; it does not install a Root or prove distinct-principal
ACL denial. Actual Operator bootstrap, package integrity, SID/ACL/Token/Job/
descendant/time qualification, CLF publication/Receipt, CLE commit, disruptive
tests and real Provider trials remain separate HOLD gates. A source test or a
copied ACK cannot satisfy them.

Native API references: [PeekNamedPipe](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-peeknamedpipe),
[SetNamedSecurityInfoW](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow),
[SECURITY_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information).
