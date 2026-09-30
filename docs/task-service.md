# 本地异步任务服务 · N2

任务合同版本为 `0.1.0`，继续使用已验证的 NumPy 运行器与 `legacy-explicit-v1` 执行顺序。
输入、任务状态和结果分开保存；编辑当前项目不会修改已经提交的输入。
接口与严格数据定义见[任务合同](protocol/task-contract.md)、[OpenAPI](protocol/tasks-openapi.json)。

2026-09-30 起同一服务还支持 N3 的 `conservative-pts-bulk-v1`，其 Task 0.2 能力在
`capabilities.task_profiles` 单独公布，按项目精确选择版本和锁。详见[新 profile](protocol/pts-bulk-profile.md)
与[混合版本 OpenAPI](protocol/tasks-openapi-v0.2.json)；以下任务生命周期同样适用。

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

`--sync-only` 仅开放原同步能力，供旧入口和小任务兼容。`/api/capabilities` 的顶层
`api_version: 0.2.0` 与旧 `execution` 继续描述同步接口；新的 `task` 单独描述异步版本、
执行方式、精确实现锁、资源限制与保留期限。前端仅在支持精确任务版本时启用异步运行。

## 用户操作

1. 在 Space / Workflow 设置项目，提交时冻结当前项目、编辑修订、时间步、步数、执行锁和输出计划。
2. 运行记录显示排队、运行、已完成、失败、取消或中断。单 worker 顺序计算，队列有界。
3. 可继续编辑、新建或打开另一项目；后台任务与原始输入保持关联。迟到结果不会替换新草稿。
4. 取消运行中的任务先显示请求已发送，终态由服务确认。完成与取消竞争时，已提交的终态保持不变。
5. Results 可以查看已经公布的完整帧；部分结果明确标为 partial。失败、取消和重启中断不伪装成完整结果。
6. 浏览器保存有界的任务记录。重新打开时按任务 ID 查询；未知提交结果使用原键与原内容重试，超过幂等保留窗口不自动提交。

断线恢复指恢复查询。服务重启时，旧 queued/running 任务标为 interrupted，并保留已公布的帧；
当前不支持暂停、checkpoint 或从中断步骤继续计算。回放的播放/暂停只控制观看时间轴。

## 输出边界

N2 合同仅传递既有单菌体 frame，`include_fields` 固定为 false；异步 Results 不包含
浓度场体素数组，也不据此生成空间热力图。前端明确提示这一点。旧同步 `/api/replay`
仍返回其原有浓度场。场输出需要独立的版本化合同，不能将缺失数组填成零。

块内容先写入临时文件并同步，再原子重命名；随后通过 SQLite 事务公布索引、事件、
已提交步与状态。浏览器读取每块时验证字节数和 SHA-256，未公布的文件没有查询入口。
最终 manifest 含冻结输入摘要、版本化编译计划与运行环境记录。任务 `run_id` 是服务记录的标识；
Project.run.run_id 保留原值，仍用于既有 frame 关联。

前端逐步保存帧，保留分裂等连续谱系事件。API 的降采样仅用于服务能够保证事件完整性的图；
无法保留谱系的组合在接受任务前拒绝。最后一步始终输出。当前记录 seed，但已接入模块
本身为确定性计算，provenance 明确 `rng.used=false`，不会把无随机机制声称为随机重复。

## 资源与错误

具体输入、菌体、体素、步数、内存、输出、墙钟、排队数量、事件页和保留期限均查询
`capabilities.task.limits`。这些是本地服务的保护限额，不能当作科学有效范围或硬件性能承诺。

- 接受前检查版本、实现锁、项目语义与规模，先估算后分配。超预算返回 413，队列满返回 429 与 `Retry-After`。
- 每任务使用独立子进程。服务监测其实际 RSS、输出大小、运行时长和增长；超限终止任务，仅保留已公布的完整块。
- 同一 `Idempotency-Key` 和相同规范化输入只接收一次；不同内容复用键返回 409。规范化使用 RFC 8785，严格拒绝重复 JSON 成员和无效数值。
- 事件游标过期返回 410。前端恢复当前状态并标记历史事件不完整，不伪造缺失事件。
- 磁盘写入失败不能保证立即保存诊断，服务停止接收新任务；存储恢复后核对记录并标记中断。

任务错误提供稳定 code、阶段、JSON Pointer 和可读说明。当前服务是本机工具，未增加多用户
账户或网络部署协议。生物参数标定、真实趋化科学包、GPU、候选比较和标准导出分别由后续阶段验收。

当前工作区继续使用每次 1–100 步的输入范围；任务 API 的上限独立由服务公布，不代表界面已开放同样规模。
RSS 每 10 ms 采样，超过限额后终止子进程，并非操作系统硬内存配额。
保留期是最短保存承诺：`TaskService.reap()` 和 `prune_events()` 提供显式清理接口，
尚未接入后台自动清理或管理 UI；活动任务不会清理，幂等保留期届满前不删除对应任务。
