# Coordination Loop Harness（CLH）

[English](README.md)

> 面向长期 Agent 辅助开发的、**Provider-neutral 的耐久协调内核**。

Coordination Loop Harness 是 Coordination Loop 四产品体系中的基础协议/约束层：

```text
PRODUCT = CLH + CLE + CLF + CLT
```

- **CLH**：耐久协调合同、授权、Lease、资源与验证；
- **CLE**：权威 DAG / 调度 / Policy / WorkOrder 控制面；
- **CLF**：Worker / Provider / Session / ExecutionClaim / Event / Receipt 执行面；
- **CLT**：Bootstrap / Distribution / Starter 产品。

CLH 不绑定 ChatGPT、Codex、DeepSeek Harness、OpenCode、Claude Code、Hermes 或任何其他 Agent/Provider。它解决的问题是：**一项工作被谁授权、允许在什么精确范围内发生、其耐久状态如何表示和验证。**

本项目与任何 Agent/模型厂商均无官方隶属关系。

## CLH 的职责

CLH 当前与未来的通用职责包括：

- Goal / Decision / Run 等耐久协调对象；
- repository-set / resource Lease 与 generation；
- authority / budget / resource 边界；
- exact repository / SHA / worktree 绑定与校验；
- durable bundle / handoff / status / outcome；
- Harness Model 与 Profile Pack **格式**；
- fail-closed 的合同和仓库验证。

CLH **不负责**：

- 启动或调度具体 Coding Agent；
- Provider/Worker 的 session/process 生命周期；
- CLE 的权威 DAG 调度；
- CLF 的执行与 Receipt 生产；
- Jerry/DGF/JDG 等个性化配置；
- 创建 GitHub 远端仓库作为普通 bootstrap 能力。

## Provider-neutral 工作模型

```text
Human / Architect
      │ Goal / Owner Decision
      ▼
     CLH
 durable contracts / authority / lease / exact identity
      │
      ▼
     CLE
 DAG / policy / WorkOrder
      │
      ▼
     CLF
 provider / worker / execution / Receipt
```

CLT 位于采用入口，负责为新项目生成最薄的 starter/distribution surface。

## 核心保证

- **耐久边界明确：**Goal、Decision、Lease、Outcome 等可以被版本化、审计和恢复；
- **精确身份：**依赖仓库 identity、remote main、HEAD、branch、worktree、dirty state，而不是目录名猜测；
- **Authority 不自动扩大：**源码写权限不等于生产权限或远端资源生命周期权限；
- **历史不可变：**旧 Decision/Outcome 不因为新 schema 或新架构而被重写；
- **远端仓库创建默认拒绝：**`REMOTE_REPOSITORY_CREATION = DENY_BY_DEFAULT`；
- **Subagent 永不创建远端仓库：**远端 create/fork/archive/delete/transfer 不属于可递归委派能力；
- **Provider-neutral：**Agent/模型/协议变化不改变 CLH 核心语义。

## 当前实现与 v5

当前 CLH 已实现较成熟的 Run/Goal/Decision/Lease/bundle/repository verification/Harness Model 等能力。历史版本还保留了一些 template/bootstrap 与 ChatGPT/Codex 示例，这是待 v5 收口的实现/文档历史，不是当前产品定义。

V5 当前方向见：

- [`docs/V5_PRODUCT_DIRECTION.md`](docs/V5_PRODUCT_DIRECTION.md)
- [`AGENTS.md`](AGENTS.md)

CLT 将逐步成为唯一明确的 bootstrap/distribution owner；CLH 收敛为 durable coordination kernel。

## 本地开发

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e .[dev]

clh validate --root .
python -m unittest discover -s tests -v
```

现有命令参考见 [`docs/command-reference.md`](docs/command-reference.md)。旧版本迁移、template 与设计理由文档可能包含历史产品表述；若与 v5 当前入口冲突，以 `README.md`、本文件、`AGENTS.md` 与 `docs/V5_PRODUCT_DIRECTION.md` 为当前解释。

## 远端仓库生命周期

CLH v5 不把远端 GitHub repository creation 视为普通能力。旧 `scripts/Publish-PublicTemplate.ps1` 路径已被 fail-closed 禁用，仅保留用于阻止历史调用者静默创建远端资源。

如果未来确实需要新建/归档/删除/转移远端仓库，必须由产品外部的显式 Owner-controlled lifecycle 流程授权和执行；不能从普通 Goal、源码写入、push 或 PR 权限推导。

## 安全边界

不要提交：

- API Key、Token、Cookie、凭据、私钥或配对链接；
- 完整生产日志或原始 Provider transcript；
- 含秘密的运行时配置；
- 本地 Lease / mutex / transient database；
- 产品仓库副本和 worktree。

生产权限默认关闭。

## 许可证

MIT，见 [LICENSE](LICENSE)。
