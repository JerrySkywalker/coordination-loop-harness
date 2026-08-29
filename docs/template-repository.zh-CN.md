# 模板仓库指南

> **V5 状态：**本文件保留为 CLH 早期 Template 设计的迁移/历史说明。当前 Bootstrap/Distribution 产品是 CLT。远端 GitHub 仓库创建默认拒绝，不属于普通 CLH/CLT/Agent 能力。

## V5 当前规则

- CLH 不负责创建远端仓库；
- CLT 负责未来 Starter / Bootstrap / Distribution；
- 源码写入、Push 或 PR 权限不等于远端仓库 Create/Fork/Archive/Delete/Transfer 权限；
- Subagent 永远没有远端仓库生命周期权限；
- 测试默认使用本地临时仓库/Worktree；
- `scripts/Publish-PublicTemplate.ps1` 已 fail-closed 禁用，仅用于阻止历史调用者静默创建远端资源。

如果未来发布流程确实需要创建新的远端仓库，必须由普通 CLH/CLT Bootstrap 之外的 Owner-controlled 生命周期流程，以显式耐久 Authority 单独授权。

## 历史 v0.2 行为

早期 CLH 使用 GitHub Template repository，并提供 repository-scoped `bootstrap-derived-repository.yml`。该 Workflow 只在**已经存在**的仓库内验证 Template provenance、创建独立分支并打开 Draft PR；它本身不会创建顶层 GitHub 仓库。

不可变的 v0.2/v0.2.1 发布历史和 provenance 语义继续作为历史证据保留，但不构成 V5 远端仓库生命周期授权，也不意味着 CLH 仍是长期 Distribution Owner。

当前 CLH/CLT 分工以 `docs/V5_PRODUCT_DIRECTION.md` 和 Program Roadmap v5 为准。
