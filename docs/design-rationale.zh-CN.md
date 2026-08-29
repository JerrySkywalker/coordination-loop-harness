# 设计理由

> **V5 当前解释：**CLH 是固定 `CLH + CLE + CLF + CLT` 四产品体系中的 Provider-neutral 耐久协调内核。早期 ChatGPT/Codex 示例只属于历史动机，不是当前产品身份、依赖或兼容要求。当前方向见 `docs/V5_PRODUCT_DIRECTION.md`。

## 为什么采用仓库支撑的协调 Harness，而不是高权限 Agent Server？

长期 Agent 辅助开发最难的部分并不是消息传输，而是耐久范围、授权、精确仓库身份、恢复与证据。Git 仓库天然提供可审阅历史与精确 Commit 身份，而 CLH 本身无需成为高权限 Provider/进程编排器。

CLE 与 CLF 分别承担更上层的控制面和执行面；CLH 保持为协调合同与验证层。

## 为什么采用协作式 Lease？

早期 Lease 设计用于防止可信开发进程意外重叠，同时保持可检查性。V5 保留耐久 Lease/Resource 思想，并演进为明确资源集合和“每仓最多一个 Writer”的模型，使独立仓库未来能够并发，而不是依赖全局单 Writer。

仅凭 Lease 文件并不意味着实现了分布式共识，也不防御恶意 Writer。

## 为什么必须 Provider-neutral？

Coding Agent 与 Harness 产品变化很快。CLH 因此只描述 Goal、Decision、Lease、Authority、Budget、Repository/Resource Identity 与证据，不把 Codex、DeepSeek Harness、OpenCode、Claude Code、Hermes 等具体运行时写入核心合同。

Provider/Session 执行属于 CLF；Provider 品牌最多是可选集成，不进入 CLH Authority 语义。

## 为什么 CLH 不控制 Provider 进程？

直接启动 Coding Agent、管理 Session、保存凭据或拥有 Browser/Process 生命周期会扩大 CLH 威胁面，并把协调授权和执行混在一起。CLH 刻意止步于耐久、可审阅的协调对象与验证；CLF 管执行生命周期，CLE 管权威调度与状态。

## 为什么要密封 Run Bundle？

文件名清单无法证明内容与完整性。Seal 记录确定性的 SHA-256 条目及 Companion 绑定，再与重新枚举结果比较，从而在不复制本地原始证据的前提下暴露缺失、额外、变更或未哈希对象。

## 为什么区分 Audit 的声明属性与已验证属性？

同一进程可以声明自己只读或独立启动，但不能机械证明自己的隔离性。因此 asserted 与外部 verified 应保持为不同概念。

## 为什么远端仓库创建不属于普通 CLH 权限？

创建、归档、删除、Fork、转移或重命名远端仓库都是耐久外部生命周期操作。源码写入、Push 或 PR 权限不等于这些生命周期权限。V5 因此默认拒绝远端仓库创建，禁止 Subagent 进行远端仓库生命周期操作，并优先使用本地临时仓库/Worktree 做测试。

未来 Bootstrap/Distribution 由 CLT 负责；CLT 同样不会默认创建远端仓库。
