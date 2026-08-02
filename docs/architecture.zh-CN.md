# 架构

Coordination Loop Harness 将系统分成四个平面：

1. **对话平面**——人工 Owner 与网页架构师讨论意图；
2. **耐久协调平面**——提交 Request、Plan、Decision、Status、Outcome 与 Audit；
3. **本地执行平面**——保存 worktree、lease、原始日志、构建产物和凭据；
4. **基础设施平面**——始终需要独立 Apply Gate 的部署作用域。

协调仓库可以规划较大的仓库集合，但活动 lease 只应覆盖当前阶段真正需要的仓库。
只有在停止写入的阶段边界，结合耐久 Owner Decision 和 generation 检查，才能扩张 lease。

本项目不代理模型 API 调用；它刻意保持为“仓库协议 + 小型本地 CLI”。

## v0.2 完整性分层

1. 严格 Schema 拒绝耐久对象中的未声明属性；
2. Markdown/JSON Companion 通过 SHA-256 绑定；
3. Sealed Run Bundle 枚举全部耐久对象并拒绝漂移；
4. Bound Goal 将耐久 Goal、只读 Git 证据与可选 lease 数据组合；
5. 特权迁移消费已验证 Owner Decision，而不是仅检查引用字符串；
6. 本地 lease 与原始证据始终位于密封耐久平面之外。

模板 bootstrap 属于独立的派生仓库平面。Ownership 将文件分为 template-managed、
render-once、derived-owned 和 template-source-only；同步只生成非变更计划。
