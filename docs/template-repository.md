# Template repository guide

1. Publish this repository as public.
2. In GitHub Settings, enable **Template repository**.
3. Create derived coordination repositories from the default branch only.
4. Replace `template_repository`, `template_version`, and `template_exact_sha` in each run manifest.
5. Do not merge ordinary branches from this template into derived repositories: template-derived
   repositories have unrelated histories.
6. Upgrade shared schemas/scripts through a reviewed synchronization pull request, never by silently
   rewriting active run files.

`Publish-PublicTemplate.ps1` can create and mark the repository when run from an authenticated local
GitHub CLI session.

The v0.2.1 `bootstrap-derived-repository.yml` workflow is for a repository that already exists from
the template. It uses the repository-scoped `GITHUB_TOKEN`, requires `contents: write` and
`pull-requests: write`, validates dispatch inputs, verifies canonical Template provenance through
GitHub REST metadata, binds the derived checkout tree to the claimed template commit tree on
`github.com`, creates a dedicated branch, validates the result, and opens a Draft pull request.
The dispatch event snapshot is not treated as canonical repository provenance, and user input alone
cannot establish provenance. API failure, absent or mismatched REST provenance, and tree mismatch
all fail closed. A second dispatch also fails closed while an earlier bootstrap Draft pull request
is open. Dispatch values enter shell steps only through quoted environment variables. The workflow
never creates the top-level repository or writes directly to main.

The v0.2.0 release used the `workflow_dispatch` repository snapshot for Template provenance. That
snapshot can omit `template_repository`, so affected consumers should upgrade to v0.2.1; the
immutable v0.2.0 tag is not moved.

`.coord-template.json` records the exact template identity and the rendered hashes of
template-managed files. A later non-mutating sync plan reports `safe-update` only when the current
derived file still matches its recorded hash; a derived edit remains a `conflict`.
