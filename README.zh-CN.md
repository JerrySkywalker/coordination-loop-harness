# Coordination Loop Harness（协调循环支架）

[English](README.md)

> 一个非官方、开源的仓库模板，用于构建持久、可审计、由人工监督的
> **ChatGPT 网页端 ↔ Codex CLI** 开发循环。

Coordination Loop Harness 把“在网页端做架构与决策、在本地 Codex CLI
执行开发”的聊天式流程，转化为一套可审计的仓库工作流。它为网页架构师、
本地 Implementer、Auditor、Supervisor 与人工 Owner 提供共同的耐久邮箱，
同时不把聊天上下文误当数据库，也不允许多个编码 Agent 无约束地同时写入同一仓库。

本项目与 OpenAI 无隶属或官方背书关系。

## 为什么需要它

长时间 AI 辅助开发通常不是败在编码，而是败在协调边界：

- 重要决策只存在于聊天记录；
- 新 Codex 会话无法证明自己基于哪个 exact SHA；
- 多个会话意外写入同一仓库、checkout 或 worktree；
- 源码写权限与生产部署权限混为一谈；
- 原始日志和秘密被提交到耐久仓库；
- attach 脚本在 Owner 审阅前悄悄启动另一个 Agent。

本模板将这些边界显式化。

## 工作模型

```text
人工 Owner / ChatGPT 网页端
        │ 请求、架构、Owner Decision
        ▼
协调仓库（由本模板派生）
        │ 耐久 Run Bundle、exact-SHA 合同、审计摘要
        ▼
本地 repo-set lease 准入
        │ 每个仓库集合仅允许一个活动 writer
        ▼
Codex CLI Implementer / Auditor / Supervisor
        │ commit、PR、exact-head 证据
        └────────────── 向 Owner 反馈 ──────────────┘
```

协调仓库是一个**耐久邮箱**，不是自治 Agent Server。内置 attach 工具只生成
Prompt 文件，不会启动 Codex。

## 核心保证

- **仓库集合独占：**活动 lease 会拒绝仓库、worktree 根目录、本地作用域、
  协调仓库或基础设施作用域的重叠。
- **原子准入：**目录 mutex 串行化 lease 创建与扩张；新 lease 文件使用
  create-new 语义写入。
- **移动式写令牌：**多仓父运行可以预先规划仓库集合，但任何时刻只允许一个
  `active_writer_repository`。
- **可验证 Owner Gate：**特权状态迁移和 lease 操作必须使用通过 Schema、动作、
  sequence、generation 与 Markdown 哈希绑定校验的已接受 Decision。
- **耐久状态与本地证据分离：**请求、计划、决策、状态、结果与审计摘要可提交；
  原始日志、凭据与本地 lease 不进入 Git。
- **exact-head 交接：**Goal 记录 exact base SHA 与验证命令。
- **禁止隐藏启动：**`Prepare-ImplementerAttach.ps1` 只写 Prompt，不启动进程。
- **生产默认拒绝：**拥有源码写权限不等于拥有基础设施 Apply 权限。
- **密封 Run Bundle：**确定性 SHA-256 清单会拒绝缺失、额外、变更或未哈希的耐久对象。
- **精确仓库绑定：**离线 Git 校验覆盖 canonical root、origin、branch、ref、detached
  worktree、tracked dirt 与 untracked 文件；在线 `gh` 校验为可选项。
- **安全模板演进：**按 ownership 分类的 bootstrap 可重复执行，`sync-plan` 只报告、
  不应用变更。

## 目录结构

```text
schemas/        Request、Plan、Goal、Decision、Status、Outcome、Audit、Manifest、
                repo-set lease 的 JSON Schema
templates/      派生协调仓库可直接使用的人工模板
requests/       Owner 请求
plans/          Plan、Goal 与 Manifest
runs/           Status 与 Outcome
decisions/      Owner Gate 和范围变更
audits/         脱敏审计摘要
handoffs/       生成的 attach Prompt
scripts/        PowerShell 包装器和一次性发布脚本
src/            `clh` 命令行实现
tests/          跨平台单元测试
.coord-local/   建议的本地状态目录（已忽略）
```

## 快速开始

### 1. 本地安装

```bash
python -m venv .venv
# Windows：.venv\Scripts\activate
# Linux/macOS：source .venv/bin/activate
python -m pip install -e .[dev]
```

### 2. 创建 Run Bundle

```bash
clh init-run \
  --root . \
  --run-id EXAMPLE-001 \
  --title "示例仓库变更" \
  --requested-by "owner" \
  --objective "实现并审计一个边界明确的变更。" \
  --repository example/product
```

### 3. 准备候选 lease

复制 `templates/repo-set-lease.example.json`，替换占位符，然后执行：

```bash
clh lease inspect \
  --candidate .coord-local/EXAMPLE-001.candidate.json \
  --lock-root .coord-local/locks

clh lease acquire \
  --candidate .coord-local/EXAMPLE-001.candidate.json \
  --lock-root .coord-local/locks \
  --repo-root .
```

### 4. 生成 Implementer Attach Prompt

```bash
clh render-attach \
  --root . \
  --run-id EXAMPLE-001 \
  --lease .coord-local/locks/EXAMPLE-001.lease.json
```

先人工审阅 `handoffs/EXAMPLE-001/implementer-attach.md`，再由你自己粘贴到目标
Codex CLI 会话。

### 5. 校验仓库

```bash
clh validate --root .
python -m unittest discover -s tests -v
```

### 6. 密封并校验 Run Bundle

```bash
clh bundle seal --root . --run-id EXAMPLE-001
clh bundle verify --root . --run-id EXAMPLE-001
```

### 7. 导出本地 Bound Goal

```bash
clh bind-goal \
  --root . \
  --run-id EXAMPLE-001 \
  --repository-root ../product \
  --state-root .coord-local \
  --stable-branch main \
  --expected-input-sha 0123456789012345678901234567890123456789
```

它会在本地状态根下写入 `bound-goal.md`、`coordinator-manifest.json` 和
`implementer-attach.md`，绝不会启动 Codex。

详见[命令参考](docs/command-reference.md)、[v0.1 迁移说明](docs/migration-v0.1-to-v0.2.md)
与 [Wave7 能力矩阵](docs/wave7-parity-matrix.md)。

## 多仓分阶段开发时如何扩张 lease

不要因为未来阶段可能需要某仓库，就在项目开始时一次性锁住所有仓库。应从最小活动集合开始，
并在阶段边界执行：

1. 停止产品写入；
2. 合并或冻结当前 exact main SHA；
3. 合并说明新范围的 Owner Decision；
4. 重新读取全部活动 lease；
5. 创建 `generation + 1` 且包含 `decision_ref` 的候选 lease；
6. 使用 `clh lease replace --expected-generation ...` 原子扩张；
7. 只恢复一个 `active_writer_repository`。

这样，产品开发列车可以与不相关的仓库健康行动并行，又不会破坏单 writer 约束。

## 安全边界

禁止提交：

- API Key、Token、Cookie、凭据、私钥或配对链接；
- 完整生产日志或请求/响应正文；
- 含秘密的运行时配置；
- 本地 lease 文件或 admission mutex；
- 产品仓库副本和 worktree。

内置扫描只是最低限度的护栏，不能替代专业秘密扫描工具。详见
[安全模型](docs/security-model.zh-CN.md)和[设计理由](docs/design-rationale.zh-CN.md)。

## GitHub 模板仓库

由 GitHub Template 生成的新仓库会复制目录和文件，但使用不相关的新历史。因此每个
Run Manifest 都记录 `template_version` 和 `template_exact_sha`，而不是假设可以从模板仓库
直接 merge。详见[模板仓库指南](docs/template-repository.zh-CN.md)。

## 当前状态

`v0.2.1` 是可审阅的补丁发布；它修复 GitHub Template provenance 验证，并且不移动不可变的
v0.2.0 tag。它有意不做以下事情：

- 自动启动或控制 Codex；
- 自动操作 ChatGPT 网页端；
- 管理云凭据；
- Apply 生产基础设施；
- 替代 GitHub 分支保护与 Code Review；
- 在没有共享 lock root 的不可信多主机之间提供分布式锁。
- 自动应用模板同步计划；
- 在只有自我声明时声称已证明进程独立性。

## 许可证

MIT，见 [LICENSE](LICENSE)。
