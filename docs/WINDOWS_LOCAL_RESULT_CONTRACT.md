# Fixed local result handoff — source candidate

This is a new, opt-in source contract. It is not adoption of changed v1 semantics,
protected runtime qualification, permission to publish, or a real Receipt. Its
normative change needs exact Owner source acceptance. Public CLI/MCP gates and
`WindowsProtectedCustodyVerifier.verify()` continue to refuse runtime custody and
require a clean Writer. The published Lease v2 schema, implementation and manifest
remain unchanged.

## Version and fixed scope

The trusted SYSTEM Operator calls `WindowsIssuer.claim_result_once()` explicitly.
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

## Compatibility and source evidence

Compatibility window: existing v1 peers remain v1, including strict post-publication
clean refusal. The result claim has a distinct response ID and handoff digest;
mixed versions fail before resume. Existing control frames remain v1-shaped, with
the post-effect proof required only inside the explicitly selected result session.
There is no offline bearer capability or cross-product implementation import.

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
