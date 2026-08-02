# 仓库集合 Lease

Lease 同时保护仓库及其执行表面，重叠检查包括：

- 仓库身份（`owner/name`）；
- canonical checkout 与 worktree 路径；
- 额外本地作用域；
- 协调仓库身份；
- 基础设施作用域字符串。

准入使用原子目录 mutex 和 create-new lease 文件。系统不会自动删除陈旧 mutex。
Lease 扩张使用完整候选文件、必需的 Decision 引用以及 `expected_generation`。

从 v0.2 起，引用字符串本身不构成授权。获取 lease 必须验证 `lease:acquire`；扩张必须由
已接受或已合并的 v2 Decision 明确授权 `lease:expand`、目标 lease id 与候选 generation。

这是协作式锁。所有 writer 必须使用同一共享 lock root 并遵守协议；它不是面向互不信任
多主机的共识系统。
