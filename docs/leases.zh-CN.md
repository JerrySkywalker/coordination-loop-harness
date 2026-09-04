# 仓库集合 Lease

Lease 同时保护仓库及其执行表面，重叠检查包括：

- 仓库身份（`owner/name`）；
- canonical checkout 与 worktree 路径；
- 仓库限定的 branch ref；
- 额外本地作用域；
- 协调仓库身份；
- 基础设施作用域字符串。

v2 对仓库、路径和 branch 使用 shared-read/exclusive-write：READ/READ 可以并行，任一侧为
WRITE 就发生冲突。每个 ACTIVE lease 最多只有一个 writer；共享同一 lock root 时，不同
仓库 writer 可以并行，而同一仓库 writer 最多一个。

准入使用原子目录 mutex 和 create-new lease 文件。系统不会自动删除陈旧 mutex。
Lease 扩张使用完整候选文件、必需的 Decision 引用以及 `expected_generation`。
所有 v2 `acquire`、`replace` 与 `release` 变更都必须显式提供仓库根，不能从进程当前目录
推导 Decision 或 outcome 权限；只有 v1 保留历史根目录发现行为。

从 v0.2 起，引用字符串本身不构成授权。获取 lease 必须验证 `lease:acquire`；扩张必须由
已接受或已合并的 v2 Decision 明确授权 `lease:expand`、目标 lease id 与候选 generation。

新建 lease 会先把完整 JSON 写入同目录临时文件并持久化，再以平台的原子 no-replace
操作发布；不会退回到直接写最终路径。v2 Decision 还必须绑定完整候选的 canonical JSON
SHA-256，防止授权后替换 mode、owner、路径、分支、期限或精确 SHA。
Windows 的新建与替换发布均使用原生 `MOVEFILE_WRITE_THROUGH`，其中新建不会携带覆盖权限；
POSIX 在原子 no-replace 发布后同步目录项，替换则把已完整持久化的临时文件一次原子 rename，
读者只会看到旧记录或完整新记录。替换后的目录同步失败会原样报告，不推断旧记录已恢复。
通用 `decision.v2` schema 为兼容历史 v1 Decision 仍允许该字段为空，因此单纯 schema
合法不构成 v2 lease 授权；v2 操作必须执行跨文档候选摘要精确比对。
canonical v2 JSON 拒绝重复对象键、所有浮点数以及超出 `[-(2^53-1), 2^53-1]` 的整数，
按对象键排序、直接输出非 ASCII 字符、使用紧凑分隔符并以唯一一个 LF 结束，再编码为
UTF-8。

v2 序列化路径仅允许盘符限定的 Windows 绝对路径或单根 POSIX 路径；控制字符、空组件、点段、
父段、尾点/尾空格、Windows 保留设备名、UNC/device namespace、双前导分隔符以及 POSIX 路径中的
反斜杠一律默认拒绝。变更操作还要求路径属于当前主机 dialect；跨主机只读观察不等于变更
准入。

最终准入在共享 mutex 内持有 Git index、HEAD、公共 config、每 worktree config、worktree
管理 `locked`、packed refs 与当前 branch 的原生 lock；所有派生路径都必须留在精确 Git
元数据根内。系统绑定 canonical 仓库与 writer worktree 的同一 common Git dir，重新验证
文件系统身份、完整 lock 集合和每个自有 marker，然后完成重叠扫描与 lease 发布。崩溃或
被替换的 Git lock 只允许人工核实，不能按 PID 或超时自动回收。
同一 `lease_id` 即使声明了完全不相交的资源也会冲突。

Decision scope 的每一项必须非空、唯一且已 canonical 化，并覆盖 lease 的完整资源集合。
它可以是严格超集，但额外的授权边界不会扩大或占用候选 lease 的资源；空白、重复或别名
形式均 fail closed。
基础设施身份可以包含 `/`，但不得为空或带首尾空白。v2 仓库字段使用无 `.git` 后缀的
`owner/name`，序列化时可以保留展示大小写，冲突与 Decision scope 身份则统一 casefold；
`active_writer_repository` 必须与唯一 WRITE 项的序列化拼写逐字一致。

v2 正常结束必须提交精确 terminal candidate：generation 加一、保留原资源身份、引用仓库内
outcome 及其 SHA-256，并由直接承接当前 Decision 的 release Decision 绑定。未过期时需要
`lease:release`；已经过期时必须显式使用 `lease:release-stale`。只有 schema、生命周期、
Decision 链、候选摘要与 outcome 内容全部通过时才是 `TERMINAL_RELEASED`；否则一律为
`UNKNOWN_FAIL_CLOSED`，并继续阻挡可解析出的同资源 writer。完全不可解析的记录不会变成
机器级全局锁，但仍按其规范文件名保留精确 `lease_id` 冲突。v2 terminal 证明必须显式
提供仓库根，绝不继承当前工作目录；未知 schema 也不能退回 v1 replace/release 路径。
仓库相对 Decision/outcome 引用还会拒绝绝对路径、点段穿越、以点结尾的组件以及 Windows
保留设备名和控制字符，避免同一字符串在 Windows 上解析到另一文件。v2 consumer 会将该规则应用于
完整 Decision predecessor 链，并在原子发布前重新验证 Decision 证据。v2 只读观察也会先
执行完整仓库身份语义校验，再报告 ownership 状态。

历史 v1 lease 的全重叠、Decision 顺序和仓库名 canonical 语义保持不变。已有 v1 协调仓库
self-write replacement 仍兼容，但现在也必须通过精确 live Git/common-dir、clean worktree、
文件系统身份和 Git guard 检查；`acquire` 仍不能创建该例外。新 v2 仓库身份只能使用无
`.git` 后缀的 canonical `owner/name`。

v2 协调仓库 self-write 也只允许由 `replace` 创建：它必须是唯一 WRITE 仓库、与
`active_writer_repository` 一致，并精确绑定协调仓库路径、worktree、branch 和 SHA；其他
仓库只能保持 READ。普通 product lease 仍把协调仓库作为共享 READ 观察。

这是协作式锁。所有 writer 必须使用同一共享 lock root 并遵守协议；它不是面向互不信任
多主机的共识系统。
