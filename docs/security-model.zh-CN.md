# 安全模型

## 信任假设

- 人工 Owner 控制 Owner Decision；
- Writer 共同使用同一 lock root；
- Git Review 与分支保护仍是权威机制；
- 本地证据目录可能包含敏感数据，因此不发布。

## 防御的故障

- 意外的仓库 writer 重叠；
- worktree/路径重叠；
- 未经审阅的 lease 扩张；
- attach 脚本启动进程；
- 耐久 Run Artifact 中常见的秘密形态；
- 将源码权限误认为生产 Apply 权限。

## 不防御

- 恶意 writer 绕过 lock root；
- GitHub 或本地凭据失陷；
- 没有共享原子文件系统时的多主机分布式竞态；
- 完整秘密检测；
- Owner 人工粘贴的不安全命令。
