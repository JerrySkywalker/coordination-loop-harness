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

The v0.2 `bootstrap-derived-repository.yml` workflow is for a repository that already exists from
the template. It uses the repository-scoped `GITHUB_TOKEN`, requires `contents: write` and
`pull-requests: write`, validates dispatch inputs, binds the derived checkout tree to the claimed
template commit on `github.com`, creates a dedicated branch, validates the result, and opens a
Draft pull request. Dispatch values enter shell steps only through quoted environment variables.
It never creates the top-level repository or writes directly to main.

`.coord-template.json` records the exact template identity and the rendered hashes of
template-managed files. A later non-mutating sync plan reports `safe-update` only when the current
derived file still matches its recorded hash; a derived edit remains a `conflict`.
