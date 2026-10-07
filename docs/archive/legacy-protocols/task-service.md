# 本地异步任务服务 · N2

2026-10-03 新入口：[Task 0.6 长时任务、二进制 checkpoint、暂停续算、边界迁移及数组分页](task-contract-0.6.md)。下文的未开放 pause/resume 等描述属于旧版本；新合同显式协商，不修改旧 Schema。

N2 原始任务合同版本为 `0.1.0`，使用 NumPy 运行器与 `legacy-explicit-v1` 执行顺序；当前服务支持的其他版本见下文。
输入、任务状态和结果分开保存；编辑当前项目不会修改已经提交的输入。
接口与严格数据定义见[任务合同](task-contract-0.1.md)、[OpenAPI](protocol/tasks-openapi.json)。

2026-09-30 起同一服务还支持 N3 的 `conservative-pts-bulk-v1`，其 Task 0.2 能力在
`capabilities.task_profiles` 单独公布，按项目精确选择版本和锁。详见[新 profile](pts-bulk-profile.md)
与[混合版本 OpenAPI](protocol/tasks-openapi-v0.2.json)；以下任务生命周期同样适用。

当前还支持 `spatial-unbiased-v1` 的 Task 0.3，传输真实浓度场及有限对象库存，详见
[空间 profile](spatial-profile.md)与[OpenAPI 0.3](protocol/tasks-openapi-v0.3.json)。
M4 新增的 [checkpoint 文件与 CLI](checkpoint-files.md)恢复独立数值运行，不续接服务中的任务；
服务能力中的 pause/resume/checkpoint 仍为 false，已中断任务仍保留 interrupted 状态。

科学 `chemotaxis-spatial-v1` 使用 Task 0.4，新增 `metrics` 与 `lifecycle_details`，稀疏保存保留间隔内生命周期事件。用户可按显式 seed 列表顺序提交重复运行；只有完整结果进入完整比较。所有机制仍走相同服务预算、幂等与取消规则。见[科学合同](chemotaxis-profile.md)与[OpenAPI 0.4](protocol/tasks-openapi-v0.4.json)。

通用 Task 0.5 在精确协商的 profile 上提供显式 CPU/CUDA 后端选择、独立场预览格距、体积平均与完整末帧 NPZ；CUDA 当前用于扩散。旧 Task 0.1–0.4 保持原合同。参数、资源配置和支持范围见[Task 0.5](task-field-previews.md)，后续实现缺口见[剩余工作清单](../planning/remaining-work.md)。

## 启动与存放位置

安装项目依赖后，从仓库根目录启动：

```powershell
$env:PYTHONPATH='src'
python -m friskoli_cad.replay_service --port 8765
```

服务监听 `127.0.0.1`。Windows 默认保存到 `%LOCALAPPDATA%/Friskoli-CAD/tasks`；
其他系统使用 `$XDG_STATE_HOME/Friskoli-CAD/tasks`，未设置时使用 `~/.local/state/Friskoli-CAD/tasks`。
SQLite 和运行输出不进入源码仓库。可使用 `--task-dir` 指定单独目录；同一目录只允许一个服务持有。

```powershell
python -m friskoli_cad.replay_service --port 8765 --task-dir 'D:/Friskoli-runs'
```

本地命令行启动的每任务执行时限默认为 1800 秒，可通过 `--task-wall-time-s 3600` 显式调整。参数必须为正整数；`--sync-only` 忽略该异步设置。服务按实际配置公布 `limits.wall_time_s`，Checks 会显示该值。直接嵌入 `TaskService` 且未传自定义 `TaskLimits` 时，仍使用原 60 秒默认值。运行时间预算不改变要模拟的物理时长。

`--sync-only` 仅开放原同步能力，供旧入口和小任务兼容。`/api/capabilities` 的顶层
`api_version: 0.2.0` 与旧 `execution` 继续描述同步接口；新的 `task` 单独描述异步版本、
执行方式、精确实现锁、资源限制与保留期限。前端仅在支持精确任务版本时启用异步运行。

## 用户操作

Checks 和 Run 在异步模式下均先将完整 Submission 发送到 `POST /api/runs/preflight`。它与正式提交共用项目、版本锁和资源判定，计算实际输出间隔、场数据和通道对应的预算；不会创建任务、占用幂等键或自动调整输入。检查通过会显示初始菌体数、体素数、保存帧数和预计输出大小。检查不预留队列或磁盘空间，也不保证运行不会超过时间、实际内存和数值限制。

1. 在 Space / Workflow 设置项目，提交时冻结当前项目、编辑修订、时间步、步数、执行锁和输出计划。
2. 运行记录显示排队、运行、已完成、失败、取消或中断。单 worker 顺序计算，队列有界。
3. 可继续编辑、新建或打开另一项目；后台任务与原始输入保持关联。迟到结果不会替换新草稿。
4. 取消运行中的任务先显示请求已发送，终态由服务确认。完成与取消竞争时，已提交的终态保持不变。
5. Results 可以查看已经公布的完整帧；部分结果明确标为 partial。失败、取消和重启中断不伪装成完整结果。
6. 浏览器保存有界的任务记录。重新打开时按任务 ID 查询；未知提交结果使用原键与原内容重试，超过幂等保留窗口不自动提交。

断线恢复指恢复查询。服务重启时，旧 queued/running 任务标为 interrupted，并保留已公布的帧；
任务 API 当前不支持暂停、生成 checkpoint 或从中断步骤继续计算；独立 Python 运行可使用 M4 文件入口保存恢复。回放的播放/暂停只控制观看时间轴。

## 输出边界

Task 0.1/0.2 仅传递既有单菌体 frame，`include_fields` 固定为 false，其 Results 不生成
缺少数值数组的空间热力图。空间 Task 0.3 与科学 Task 0.4 可设置 true，输出真实浓度数组，并始终输出有限对象库存；
前端按精确协商结果显示。旧同步 `/api/replay` 仍返回其支持的浓度场，不能将任何缺失数组填成零。

块内容先写入临时文件并同步，再原子重命名；随后通过 SQLite 事务公布索引、事件、
已提交步与状态。浏览器读取每块时验证字节数和 SHA-256，未公布的文件没有查询入口。
最终 manifest 含冻结输入摘要、版本化编译计划与运行环境记录。任务 `run_id` 是服务记录的标识；
Project.run.run_id 保留原值，仍用于既有 frame 关联。

科学 Task 0.4 可设置存帧间隔，期间的生命周期事件和死亡详情累计到下一个存帧点，指标仍每数值步更新。API 的降采样仅用于服务能够保证事件完整性的图；
无法保留谱系的组合在接受任务前拒绝。最后一步始终输出。legacy 和固定 PTS 的确定性模块记录
`rng.used=false`；空间 profile 记录实际 execution seed、固定 RNG 算法，以及是否存在使用随机事件的非空菌群。
不能把无随机机制声称为随机重复，也不能把保存帧间隔用作数值步长。

## 资源与错误

具体输入、菌体、体素、步数、内存、输出、墙钟、排队数量、事件页和保留期限均查询
`capabilities.task.limits`。这些是本地服务的保护限额，不能当作科学有效范围或硬件性能承诺。

- 接受前检查版本、实现锁、项目语义与规模，先估算后分配。超预算返回 413，队列满返回 429 与 `Retry-After`。
- 每任务使用独立子进程。服务监测其实际 RSS、输出大小、运行时长和增长；超限终止任务，仅保留已公布的完整块。
- 同一 `Idempotency-Key` 和相同规范化输入只接收一次；不同内容复用键返回 409。规范化使用 RFC 8785，严格拒绝重复 JSON 成员和无效数值。
- 事件游标过期返回 410。前端恢复当前状态并标记历史事件不完整，不伪造缺失事件。
- 磁盘写入失败不能保证立即保存诊断，服务停止接收新任务；存储恢复后核对记录并标记中断。

任务错误提供稳定 code、阶段、JSON Pointer 和可读说明。当前服务是本机工具，未增加多用户
账户或网络部署协议。真实趋化科学包与完整运行比较已在 N3 接入；N4 已有目标/约束、有限候选和结果评价，Task 0.5 已有显式 CUDA 扩散，OMEX/显式 SBOL 子集已实现。生物标定、通用 GPU 模块调度和更广标准适配仍待推进，N5 完整模拟尚未验收。

当前工作区按对应 profile 的 capabilities 设置步数上限；异步预检查与工作区文件支持最多 10000 步，实际能接受的规模仍受服务预算约束。同步 replay 也接受最多 10000 步，但另受 100 万场数值、202000 个累计 cell-frames、128 MiB JSON 输出等限制；初始预算在求解前检查，增长后的累计细胞和实际 JSON 大小在每帧检查。因此小项目可运行 1900 步，不代表默认空间项目可以用同步入口保存全部 1901 帧。

异步输出超预算时，错误显示预计值、当前上限，以及适用时的最小保存间隔。用户手动调整“每 N 步保存一帧”，数值 `dt_s` 和 `steps` 不变。单帧超过块限制时，减少存帧数量没有作用，提示会明确区分。生命周期模型的输出估算为可能增长到 256 个菌体预留空间；静态检查通过仍受服务公布的执行时间、实际内存与数值限制约束。
主前端 TaskStore 最多保存 64 条运行元数据，持久化预算 16 MiB；使用 IndexedDB 保存恢复所需输入与标识，重新打开后从服务查询结果。设计归属、正在比较和当前 seed 批次的记录受保护，开始批次前检查容量。此预算不限制运行期解码缓存和 replay 数组，详见[主界面记录](../verification/verification-main-ui-task05.md)。稀疏输出的运行进度可以领先于最新已保存帧，前端按输出间隔校验两者关系，取消后的 partial 保留可查询的完整帧。
RSS 每 10 ms 采样，超过限额后终止子进程，并非操作系统硬内存配额。
保留期是最短保存承诺：`TaskService.reap()` 和 `prune_events()` 提供显式清理接口，
尚未接入后台自动清理或管理 UI；活动任务不会清理，幂等保留期届满前不删除对应任务。
