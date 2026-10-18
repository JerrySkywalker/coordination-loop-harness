# Windows ancestor ACL policy — source preparation only

The same access mask has different consequences on an ancestor directory and on
the authority object itself. The Windows candidate now checks an existing
protected directory first, then its parents outward to the volume root. It does
not install a root, change ACLs, enable the public MCP/CLI verifier or resolve
custody. All snapshots and operational gates remain false/HOLD.

## Protected objects versus ancestors

| Evidence | Protected root and every descendant | Existing ancestor directory |
| --- | --- | --- |
| Owner | SYSTEM / Administrators only; fixed issuer remains SYSTEM | Same, plus the one exact TrustedInstaller service SID |
| DACL | Explicitly protected on every object | Native owner/DACL inspected; inheritance alone is not a refusal |
| Unknown/missing evidence, unsupported ACE/type/flags | HOLD | HOLD, even for an inherit-only ACE |
| Non-Operator effective DELETE, FILE_DELETE_CHILD, WRITE_DAC, WRITE_OWNER, GENERIC_ALL, MAXIMUM_ALLOWED | HOLD | HOLD |
| Non-Operator file/append/EA/attribute or generic-write grant | HOLD, including dangerous inherit-only defaults | Permitted only in the existing nonempty directory chain described below |
| Inherit-only defaults | Strict mutation policy retained | Not effective on this ancestor; every next child is checked on its own actual ACL |
| Reparse, alias, hardlink or replacement | HOLD | HOLD |

This is a conservative all-principal policy, not Windows `AccessCheck`. An unsafe
allow is rejected even if a deny ACE precedes it. No same-token observation or
ACL snapshot is effective-denial or launch qualification.

## Why existing children matter for reparse safety

For directories, bits 2 and 4 mean create-file/create-subdirectory rather than
overwrite/append to a child. A parent's ordinary ACL does not grant access to an
already existing child. However, simply removing those bits and
FILE_WRITE_ATTRIBUTES from a mutation mask is **not sufficient**:
`FSCTL_SET_REPARSE_POINT` can use FILE_WRITE_DATA or FILE_WRITE_ATTRIBUTES access.
The documented filesystem operation also rejects a nonempty directory.

The verifier therefore anchors its traversal in an **existing, non-reparse,
protected directory**, not a proposed/missing root or a file. Each parent contains
that already checked directory. Every link rejects untrusted DELETE on the child
and DELETE_CHILD on its parent, as well as ownership/DACL takeover. Consequently
an untrusted principal cannot empty a validated ancestor or rename/remove its
checked child to prepare a reparse substitution. Protected descendants cannot
inherit an unsafe default across their protected DACLs; their actual ACLs are
still individually inventoried. Non-security ancestor metadata writes may cause
a conservative continuity refusal; they never grant child authority writes.

The private `_check_ancestor_security` is only the ACL part of this proof. It must
never be used alone to admit an empty directory, create a root or authenticate a
caller-selected capability. There is no caller-supplied `nonempty=true`, trust
list or runtime bypass. The full inventory is still compared before/after Lease,
Decision and Git Writer verification. This is snapshot evidence, not atomic
issuance or protection against a malicious administrator/kernel. Exact host and
filesystem qualification, handle containment and real negative tests remain
separately authorized operational gates.

## Narrow servicing identity

The additional ancestor principal is exactly
`S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464`
(Windows Modules Installer / TrustedInstaller). This reflects the existing
trusted-Operator/OS threat model. No service-SID prefix, friendly name, arbitrary
user, generic owner or Operator-provided allowlist is accepted. It is not added
to protected-tree mutators or issuer configuration. Observer/Writer tokens with
this user, a grant-capable group or restricting SID are explicitly refused.

Windows volume-root servicing ownership and ProgramData inherit-only / `0x116`
defaults can thus be compatible as **ancestor ACL components** of a complete
checked chain. An existing product ancestor granting an ordinary user Modify
(`0x1301bf`, including DELETE) still refuses; this change does not make the
previously proposed host path operationally qualified. Missing identities, the
restricted launcher/Job, package integrity and time qualification remain HOLD.

## Source validation and native limits

`tests/test_windows_ancestor_acl.py` uses deterministic security observations and
ordinary temporary fixture files, without changing their ACLs. It covers volume
and ProgramData shapes, inherited versus inherit-only ACEs, narrow servicing
identity, every namespace-takeover bit, deny-before-allow conservatism, protected
descendants, actual traversal order, missing/non-directory roots and reparse
ancestors. Existing Lease/Decision, time, token/Job and public refusal tests remain.

No live rename/delete/reparse/denied-write trial, identity creation, protected Root
installation or real Provider is performed by these tests. SOURCE_TEST and CI
cannot qualify actual Windows isolation. A future exact-host grant must verify
the filesystem's nonempty-directory refusal and the complete chain before any
operational use; unsupported/uncertain behavior stays HOLD, not a threshold change.

## Primary Windows references

- [File security and access rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights): parent ACL versus child access and file generic mappings.
- [ACE_HEADER](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-ace_header): inherit-only ACEs are ineffective on their attached object.
- [MS-FSA FSCTL_SET_REPARSE_POINT](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fsa/4aeefef8-92c3-4abc-af7a-a610caf8a165): write-data/attributes checks and nonempty-directory refusal.
- [File access rights constants](https://learn.microsoft.com/en-us/windows/win32/fileio/file-access-rights-constants): DELETE_CHILD and directory-specific access bits.
- [Windows security identifiers](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/manage/understand-security-identifiers): OS servicing ownership and restricting-SID checks.
