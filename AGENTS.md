# Agent instructions

- Treat this repository as a coordination protocol, not a product runtime.
- Never add credentials, real tokens, private endpoints, production logs, or generated secret-bearing config.
- Do not make attach scripts start Codex, ChatGPT, browsers, or background agents.
- Preserve `additionalProperties: false` in durable object schemas unless a reviewed migration requires otherwise.
- Keep local leases and raw evidence outside Git.
- All lease expansion must require a durable decision reference and generation check.
- Tests must use temporary directories and synthetic repository names.
- Production apply must remain denied by default.
