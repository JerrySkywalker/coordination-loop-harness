# Design rationale

> **V5 current interpretation:** CLH is a provider-neutral durable coordination kernel in the fixed `CLH + CLE + CLF + CLT` product topology. Historical ChatGPT/Codex examples motivated early versions but are not product identity, dependencies, or compatibility requirements. See `docs/V5_PRODUCT_DIRECTION.md`.

## Why a repository-backed coordination harness instead of a privileged agent server?

The difficult part of long-running agent-assisted development is not message transport. It is durable scope, authority, exact repository identity, recovery, and evidence. Git repositories provide reviewable durable history and exact commit identities without requiring CLH itself to become a privileged provider/process orchestrator.

CLE and CLF own later control-plane and execution-plane responsibilities; CLH remains below those layers as coordination contracts and validation.

## Why cooperative leases?

The original lease design prevents accidental overlapping writers while remaining inspectable. V5 keeps the durable lease/resource idea but evolves it toward explicit resource sets and a maximum-one-writer-per-repository model so independent repositories can later execute concurrently without a global writer token.

The project does not claim distributed consensus or protection from a malicious writer merely because a lease file exists.

## Why provider neutrality?

Coding-agent and harness products change quickly. CLH therefore describes Goal, Decision, Lease, authority, budget, repository/resource identity, and evidence without making Codex, DeepSeek Harness, OpenCode, Claude Code, Hermes, or another runtime part of the core contract.

Provider/session execution belongs below CLE in CLF. A provider brand may appear in an optional integration, not in CLH authority semantics.

## Why keep provider process control out of CLH?

Starting coding agents, controlling sessions, storing credentials, or owning browser/process lifecycle would expand CLH's threat surface and mix coordination authority with execution. CLH intentionally stops at durable, reviewable coordination artifacts and validation. CLF owns execution lifecycle; CLE owns authoritative scheduling/state.

## Why seal a Run Bundle?

A filename inventory does not prove content or completeness. The seal records deterministic SHA-256 entries and companion bindings, then compares them against a fresh enumeration. This makes missing, extra, changed, and unhashed durable objects visible without copying raw local evidence.

## Why separate asserted and verified audit properties?

An audit file can truthfully report that a process declared itself read-only or independently launched, but the same process cannot mechanically prove its own isolation. Assertion and external verification therefore remain distinct concepts.

## Why is remote repository creation outside ordinary CLH authority?

Creating, archiving, deleting, forking, transferring, or renaming a remote repository is a durable external lifecycle mutation. Source-write, push, or PR authority does not imply that lifecycle authority. V5 therefore defaults remote repository creation to deny, forbids subagent remote-repository lifecycle actions, and prefers local temporary repositories/worktrees for tests.

CLT owns future bootstrap/distribution behavior; it also does not create remote repositories by default.
