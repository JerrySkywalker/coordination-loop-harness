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
