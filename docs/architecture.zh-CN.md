# 架构

Coordination Loop Harness 将系统分成四个平面：

1. **对话平面**——人工 Owner 与可替换的规划或复核界面讨论意图；
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

历史 v0.2/v0.3 CLH template renderer 曾把文件分为 template-managed、render-once、
derived-owned 和 template-source-only，并让同步停在非变更计划。该 renderer 现仅为冻结的
兼容窗口；CLT 是 Minimum V1 唯一 active starter、bootstrap 与 distribution 产品。
