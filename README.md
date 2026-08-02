# Coordination Loop Harness

[简体中文](README.zh-CN.md)

> An unofficial, open-source repository template for durable, human-supervised
> **ChatGPT Web ↔ Codex CLI** development loops.

Coordination Loop Harness turns a chat-driven development process into an auditable
repository workflow. It gives the web architect, local implementers, auditors, and
human owner a shared durable mailbox without pretending that chat context is a database
or that multiple coding agents can safely write the same repository at once.

This project is not affiliated with or endorsed by OpenAI.

## Why this exists

Long-running AI-assisted development tends to fail at the coordination boundary:

- decisions live only in chat history;
- a new Codex session cannot prove the exact repository baseline;
- multiple sessions accidentally write the same checkout or repository;
- production permission is confused with source-code permission;
- raw logs and secrets leak into durable artifacts;
- an attach script quietly starts another agent before the owner reviews the prompt.

This template makes those boundaries explicit.

## Mental model

```text
Human owner / ChatGPT Web
        │ request, architecture, owner decisions
        ▼
Coordination repository (this template)
        │ durable run bundle, exact-SHA contracts, audits
        ▼
Local repo-set lease admission
        │ one active writer per repository set
        ▼
Codex CLI implementer / auditor / supervisor
        │ commits, pull requests, exact-head evidence
        └────────────── feedback to the owner ──────────────┘
```

The coordination repository is a **durable mailbox**, not an autonomous agent server.
The included attach tooling renders prompts but never launches Codex.

## Core guarantees

- **Repository-set exclusivity:** active leases reject overlapping repositories,
  worktree roots, local scopes, coordination repositories, or infrastructure scopes.
- **Atomic admission:** a directory mutex serializes admission and lease expansion;
  a new lease file is created with create-new semantics.
- **Moving write token:** a multi-repository run can reserve an authorized set while
  allowing only one repository to be the active writer at a time.
- **Verified owner gates:** privileged status transitions and lease operations require
  schema-valid, accepted decisions with explicit actions, generation, Markdown hash
  binding, and a complete contiguous predecessor chain back to sequence 1.
- **Durable vs. local evidence:** requests, plans, decisions, status, outcomes, and audit
  summaries are tracked; raw logs, credentials, and local leases stay outside Git.
- **Exact-head handoff:** goals carry exact base SHAs and validation commands.
- **No hidden process launch:** `Prepare-ImplementerAttach.ps1` only writes a prompt file.
- **Production deny by default:** source-code authority does not imply infrastructure apply.
- **Sealed Run Bundles:** deterministic SHA-256 inventories reject missing, extra, changed,
  unhashed, non-UTF-8, symlinked, or reparse-point-escaped durable objects.
- **Exact repository binding:** offline Git checks cover canonical root, origin, branch,
  refs, detached worktrees, tracked dirt, and untracked files; live `gh` checks additionally
  bind the returned repository identity and GitHub host.
- **Safe template evolution:** ownership-aware bootstrap is idempotent and `sync-plan`
  distinguishes untouched template-managed files from derived edits without applying changes.

## Repository layout

```text
schemas/        JSON Schemas for requests, plans, goals, decisions, status, outcomes,
                audits, manifests, and repository-set leases
templates/      Human-editable starter artifacts for a derived coordination repository
requests/       Durable owner requests
plans/          Plans, goals, and manifests
runs/           Status and outcome records
decisions/      Owner gates and scope changes
audits/         Sanitized audit summaries
handoffs/       Rendered attach prompts
scripts/        PowerShell wrappers and one-time publishing helper
src/            `clh` command-line implementation
tests/          Cross-platform unit tests
.coord-local/   Suggested local-only state root (ignored)
```

## Quick start

### 1. Install the harness locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e .[dev]
```

### 2. Create a run bundle

```bash
clh init-run \
  --root . \
  --run-id EXAMPLE-001 \
  --title "Example repository change" \
  --requested-by "owner" \
  --objective "Implement and audit one bounded change." \
  --repository example/product
```

### 3. Prepare a candidate lease

Copy `templates/repo-set-lease.example.json`, replace placeholders, then run:

```bash
clh lease inspect \
  --candidate .coord-local/EXAMPLE-001.candidate.json \
  --lock-root .coord-local/locks

clh lease acquire \
  --candidate .coord-local/EXAMPLE-001.candidate.json \
  --lock-root .coord-local/locks \
  --repo-root .
```

### 4. Render the Implementer attach prompt

```bash
clh render-attach \
  --root . \
  --run-id EXAMPLE-001 \
  --lease .coord-local/locks/EXAMPLE-001.lease.json
```

Review `handoffs/EXAMPLE-001/implementer-attach.md`, then paste it into the intended
Codex CLI session yourself.

### 5. Validate the repository

```bash
clh validate --root .
python -m unittest discover -s tests -v
```

### 6. Seal and verify the Run Bundle

```bash
clh bundle seal --root . --run-id EXAMPLE-001
clh bundle verify --root . --run-id EXAMPLE-001
```

### 7. Export a local Bound Goal

```bash
clh bind-goal \
  --root . \
  --run-id EXAMPLE-001 \
  --repository-root ../product \
  --state-root .coord-local \
  --stable-branch main \
  --expected-input-sha 0123456789012345678901234567890123456789
```

This writes `bound-goal.md`, `coordinator-manifest.json`, and
`implementer-attach.md` below the local state root. It never starts Codex.

See the [command reference](docs/command-reference.md), [v0.1 migration
guide](docs/migration-v0.1-to-v0.2.md), and [Wave7 capability
matrix](docs/wave7-parity-matrix.md).

## Lease expansion for phased multi-repository work

Do not acquire every repository merely because a future phase may need it. Start with the
smallest active set. At a phase boundary:

1. stop product writes;
2. merge or freeze the current exact main SHA;
3. merge an owner decision describing the new scope;
4. inspect all active leases;
5. create a candidate with `generation + 1` and `decision_ref`;
6. run `clh lease replace` with `--expected-generation`;
7. resume with exactly one `active_writer_repository`.

This allows a product train to run beside an unrelated repository-health train without
weakening single-writer guarantees.

## Security boundary

Never commit:

- API keys, tokens, cookies, credentials, private keys, or pairing links;
- full production logs or request/response bodies;
- generated runtime configuration containing secrets;
- local lease files or admission mutexes;
- copied product repositories or worktrees.

The built-in scanner is a narrow guardrail, not a replacement for a dedicated secret
scanner. See [Security model](docs/security-model.md) and [Design rationale](docs/design-rationale.md).

## GitHub template repository

GitHub template-derived repositories copy the template structure and files into a new,
unrelated history. The template therefore records `template_version` and
`template_exact_sha` in each run manifest instead of assuming ordinary Git merges from
the template. See [Template repository guide](docs/template-repository.md).

## Project status

`v0.2.0` is a reviewable release candidate. It intentionally does not:

- start or control Codex processes;
- call ChatGPT Web automatically;
- manage cloud credentials;
- apply production infrastructure;
- replace GitHub branch protection or code review;
- provide distributed locking across untrusted hosts without a shared lock root.
- automatically apply template synchronization plans.
- claim that process independence is proven when it is only self-declared.

## License

MIT. See [LICENSE](LICENSE).
