# Coordination Loop Harness

Coordination Loop Harness (CLH) is the provider-neutral durable coordination kernel of the Coordination Loop product family.

The product topology is fixed as:

- CLH — durable coordination contracts and validation;
- CLE — authoritative DAG/scheduling/policy control plane;
- CLF — execution/worker/provider plane;
- CLT — bootstrap/distribution/starter product.

CLH is not an agent runtime and is not tied to ChatGPT Web, Codex, DeepSeek Harness, OpenCode, Claude Code, Hermes, or any other provider.

See [CLH v5 Product Direction](docs/V5_PRODUCT_DIRECTION.md) for the current architecture boundary.

## v0.3 generic Harness contracts

Version 0.3 includes portable, versioned generic contracts including:

- `coord.harness-model.v1` for generic authority/budget/proof/progress semantics;
- `coord.profile-pack.v1` for concrete compatible profile values supplied externally.

Product/operator-specific profile values remain external to CLH.

Validate a Model and optional Profile Pack with:

```powershell
clh harness validate --model harness-model.json --profile-pack profile-pack.json
```

## What CLH owns

CLH provides durable, machine-verifiable coordination primitives for long-running agent-assisted development, including:

- Requests/Goals/Decisions/Runs;
- repository-set/resource leases;
- exact repository/head/worktree verification;
- authority and budget envelopes;
- durable status/outcome/audit objects;
- sealed Run Bundles;
- safe handoff/admission tooling;
- generic Harness Model/Profile Pack formats.

Its purpose is to make work identity, authority, scope, provenance, and handoff durable across sessions and agents.

## What CLH does not own

CLH does not:

- schedule the authoritative development DAG — that belongs to CLE;
- start or manage coding-agent provider sessions — that belongs to CLF;
- own the starter/distribution product — that belongs to CLT;
- apply production infrastructure by default;
- imply permission to create remote GitHub repositories;
- require user-specific governance repositories.

## SkyBridge

SkyBridge is a frozen external historical precursor, not a CLH dependency or v5 compatibility target. V5 work must not access SkyBridge source/runtime or preserve/migrate SkyBridge interfaces.

## Core guarantees

- repository/resource admission is explicit and fail-closed;
- durable authority does not widen automatically;
- owner-gated operations bind durable decisions/generations where required;
- exact repository identity/head/handoff is mechanically verified;
- raw logs, credentials, local leases, and transient evidence stay outside Git;
- sealed bundles detect missing/extra/changed durable objects;
- production authority remains disabled by default.

## Repository layout

```text
schemas/        durable coordination protocol schemas
models/         generic Harness model artifacts
templates/      legacy/current scaffold assets pending CLH/CLT v5 separation
requests/       durable owner requests
plans/          plans/goals/manifests
runs/           durable status/outcome records
decisions/      durable owner gates/scope changes
audits/         sanitized audit summaries
handoffs/       rendered handoff artifacts
scripts/        helper wrappers/tooling
src/            clh package implementation
tests/          cross-platform tests
.coord-local/   local-only transient state (ignored)
```

## Development and validation

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e .[dev]
clh validate --root .
python -m unittest discover -s tests -v
```

See the [command reference](docs/command-reference.md), [security model](docs/security-model.md), and [design rationale](docs/design-rationale.md) for the existing implementation.

## V5 direction

V5 keeps the proven durable coordination kernel while:

- evolving resource/lease semantics for safe per-repository writers;
- preserving exact identity admission;
- moving active starter/distribution ownership toward CLT;
- removing provider-specific product positioning;
- keeping all external agent runtimes replaceable below the CLF boundary.

Remote repository creation remains outside ordinary CLH source/test authority, and subagents never receive remote repository lifecycle authority.

## License

MIT. See [LICENSE](LICENSE).
