# Design rationale

## Why a repository harness instead of an agent server?

The difficult part of a ChatGPT Web and Codex CLI workflow is not message transport. It is durable
scope, authority, and evidence. A repository provides review, immutable commit identities, branch
protection, and human-readable history without requiring another privileged service.

## Why cooperative leases?

The harness is designed for one owner operating several trusted Codex sessions. A shared atomic
filesystem is enough to prevent accidental overlap while staying inspectable. The project does not
claim distributed consensus or protection from a malicious writer.

## Why MIT?

The useful contribution is a small protocol, schemas, and reference tooling. Permissive reuse makes
it easier for teams to adapt the model to their own repositories, CI systems, and agent products.

## Why keep ChatGPT and Codex out of the runtime?

Automatically controlling a web session or spawning coding agents would require credentials,
browser state, and lifecycle ownership. Those concerns expand the threat surface and make the
coordination repository a privileged orchestrator. This template intentionally stops at rendering
reviewable prompt artifacts.

## Why seal a Run Bundle?

A filename inventory does not prove content or completeness. The seal records deterministic
SHA-256 entries and companion bindings, then compares them against a fresh enumeration. This makes
missing, extra, changed, and unhashed durable objects visible without copying raw local evidence.

## Why separate asserted and verified audit properties?

An audit file can truthfully report that a process declared itself read-only or independently
launched, but the same process cannot mechanically prove its own isolation. The v0.2 audit model
therefore preserves assertion and external verification as different fields.
