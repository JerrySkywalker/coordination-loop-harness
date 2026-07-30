# 模板仓库指南

1. 将本仓库发布为 Public；
2. 在 GitHub Settings 中启用 **Template repository**；
3. 派生协调仓库时默认只复制主分支；
4. 在每个 Run Manifest 中替换 `template_repository`、`template_version` 和
   `template_exact_sha`；
5. 不要从模板仓库向派生仓库普通 merge：二者历史不相关；
6. 公共 Schema/Script 升级必须通过独立同步 PR，不能静默改写活动 Run 文件。

在已登录 GitHub CLI 的本机执行 `Publish-PublicTemplate.ps1`，可以创建公开仓库并将其标记为模板。

v0.2 的 `bootstrap-derived-repository.yml` 仅用于已经由模板创建的仓库。它使用仓库范围
`GITHUB_TOKEN`，需要 `contents: write` 与 `pull-requests: write`，创建专用分支、校验结果并
打开 Draft PR；它不会创建顶层仓库，也不会直接写 main。
