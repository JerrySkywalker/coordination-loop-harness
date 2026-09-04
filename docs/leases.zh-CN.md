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

新建 lease 会先把完整 JSON 写入同目录临时文件并持久化，再以平台的原子 no-replace
操作发布；不会退回到直接写最终路径。v2 Decision 还必须绑定完整候选的 canonical JSON
SHA-256，防止授权后替换 mode、owner、路径、分支、期限或精确 SHA。
Windows 的新建与替换发布均使用原生 `MOVEFILE_WRITE_THROUGH`，其中新建不会携带覆盖权限；
POSIX 在原子 no-replace 发布后同步目录项。
通用 `decision.v2` schema 为兼容历史 v1 Decision 仍允许该字段为空，因此单纯 schema
合法不构成 v2 lease 授权；v2 操作必须执行跨文档候选摘要精确比对。
canonical v2 JSON 拒绝重复对象键和非有限数值，按对象键排序、直接输出非 ASCII 字符、
使用紧凑分隔符并以唯一一个 LF 结束，再编码为 UTF-8。

最终准入在共享 mutex 内持有 Git index、HEAD、config、packed refs 与当前 branch 的原生
lock；所有派生路径都必须留在精确 Git 元数据根内。系统重新验证前后快照，然后完成重叠
扫描与 lease 发布。崩溃遗留的 Git lock 只允许人工核实，不能按 PID 或超时自动回收。
同一 `lease_id` 即使声明了完全不相交的资源也会冲突。

v2 正常结束必须提交精确 terminal candidate：generation 加一、保留原资源身份、引用仓库内
outcome 及其 SHA-256，并由直接承接当前 Decision 的 release Decision 绑定。未过期时需要
`lease:release`；已经过期时必须显式使用 `lease:release-stale`。只有 schema、生命周期、
Decision 链、候选摘要与 outcome 内容全部通过时才是 `TERMINAL_RELEASED`；否则一律为
`UNKNOWN_FAIL_CLOSED`，并继续阻挡可解析出的同资源 writer。完全不可解析的记录不会变成
机器级全局锁，但仍按其规范文件名保留精确 `lease_id` 冲突。

这是协作式锁。所有 writer 必须使用同一共享 lock root 并遵守协议；它不是面向互不信任
多主机的共识系统。
