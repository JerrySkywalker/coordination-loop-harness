# Synthetic Wave7-style parity fixture

This fixture contains only synthetic identities. The test suite materializes it
inside temporary coordination and Git repositories, uses a fake `gh` executable,
and proves:

- bundle sealing and drift rejection;
- Markdown/JSON hash binding;
- exact local repository binding;
- decision authorization;
- Bound Goal, coordinator manifest, and attach generation;
- status transitions and blocker fingerprints;
- audit recording and verification;
- no automatic process launch.

No endpoint, credential, local username, production configuration, or real
repository identity is stored here.
