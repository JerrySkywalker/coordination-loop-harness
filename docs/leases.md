# Repository-set leases

A lease protects repositories and their execution surfaces. Overlap checks include:

- repository identity (`owner/name`);
- canonical checkout and worktree paths;
- extra local scopes;
- coordination repository identity;
- infrastructure scope strings.

Admission uses an atomic mutex directory and create-new lease file. The harness never deletes a
stale mutex automatically. Lease expansion uses a full replacement candidate, a required decision
reference, and `expected_generation`.

In v0.2 the reference is not sufficient by itself. Acquisition requires a verified
`lease:acquire` action; expansion requires `lease:expand`, the exact lease id, and the candidate
generation in an accepted or merged v2 decision.

This is a cooperative lock. Every writer must use the same shared lock root and respect it. It is
not a consensus system for mutually untrusted machines.
