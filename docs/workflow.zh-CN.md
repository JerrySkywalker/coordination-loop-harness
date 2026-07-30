# 工作流

1. 物化 Request 与 Plan；
2. 审计活动 lease，准备最小候选仓库集合；
3. 合并授权当前阶段的 Owner Decision；
4. 原子获取 lease；
5. 创建隔离 worktree 并绑定 exact base SHA；
6. 生成、审阅并人工粘贴 Implementer Prompt；
7. 每次只写一个产品仓库；
8. 完成 exact-head 校验并创建 PR；
9. Implementer 停止后再启动独立 Auditor；
10. 合并、更新耐久状态，并释放或扩张 lease。

Issue 评论或聊天消息本身不是执行授权；必须按照项目 Owner Gate 规则写入协调仓库。
