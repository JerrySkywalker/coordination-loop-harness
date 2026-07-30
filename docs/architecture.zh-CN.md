# 架构

Coordination Loop Harness 将系统分成四个平面：

1. **对话平面**——人工 Owner 与网页架构师讨论意图；
2. **耐久协调平面**——提交 Request、Plan、Decision、Status、Outcome 与 Audit；
3. **本地执行平面**——保存 worktree、lease、原始日志、构建产物和凭据；
4. **基础设施平面**——始终需要独立 Apply Gate 的部署作用域。

协调仓库可以规划较大的仓库集合，但活动 lease 只应覆盖当前阶段真正需要的仓库。
只有在停止写入的阶段边界，结合耐久 Owner Decision 和 generation 检查，才能扩张 lease。

本项目不代理模型 API 调用；它刻意保持为“仓库协议 + 小型本地 CLI”。
