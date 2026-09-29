# 异步任务合同草案

状态：**N1 draft，`task_contract_version: 0.1.0-draft.1`，未实现服务**。
日期：2026-09-29。本文与 [OpenAPI 3.1](tasks-openapi.json)、
[JSON Schema](../../src/friskoli_cad/protocol/schemas/task-draft.schema.json) 共同定义 N2 的验收输入。
Schema 校验通过只说明资料结构正确，不证明并发、持久化、资源估算或数值正确。

## 兼容边界

已核对 `src/friskoli_cad/replay_service.py`：当前 `api_version: 0.2.0`
使用 `POST /api/replay` 同步运行，`POST /api/validate` 检查并初始化，
GET `/api/modules`、`/api/catalog`、`/api/example-project` 和 `/api/capabilities` 查询。
当前执行能力为 synchronous，pause/resume/partial_results 均为 false。
[现有 OpenAPI](../openapi.json) 继续描述旧接口；本文不修改其请求、结果或错误形状。
新 `/api/runs` 路径现在不可用。客户端必须先检查未来 capabilities 的 `task` 字段
及精确合同版本；不支持则保持受限同步工作流，不能假定异步提交成功。
旧 replay 的 `project_sha256` 使用 Python JSON 序列化，不能与本草案摘要直接比较。

任务独立版本化，不修改 graph/run/frame 0.1.0、Project 0.1.0/0.2.0
或 Frame 0.1.0/0.2.0。Submission 引用已发布 Project Schema，仍须执行语义验证。
未知字段和未知合同版本拒绝，升级需 adapter 和迁移记录，不静默改变执行顺序。
本文 JSON frame chunk 仅包裹既有 frame，不包含字段二进制容器；
`include_fields` 固定 false，observables 仅允许已锁定的 frame channel ID，
未知观测返回 422。场数据和额外指标需后续独立版本合同，不能悄悄丢弃请求输出。

## 状态、取消与发布

| 当前状态 | 允许的新状态 | 条件 |
| --- | --- | --- |
| queued | running | worker 独占领取并持久化 started |
| queued | cancelled | 取消事务先完成，不启动 worker |
| queued | failed | 领取前的持久化或初始化准备失败 |
| queued | interrupted | 服务重启后的统一恢复策略 |
| running | completed | 全部要求的步和输出已提交，完整 manifest 原子发布 |
| running | failed | 数值、资源、worker 或输出发布错误 |
| running | cancelled | cancel_requested 在已提交步边界生效 |
| running | interrupted | 重启发现未结束的旧 worker 任务 |
| completed/failed/cancelled/interrupted | 无 | 终态不可改写 |

running 内可以更新 progress 或 `cancel_requested`，不构成另一种状态。
设计文档的 cancelling 对应 `status=running, cancel_requested=true`，
不另加第七个状态。queued 的取消在同一事务进入 cancelled，不能留下 queued + true。
取消响应 202 仅确认请求已持久化；200 返回终态，包括已经 completed 的原结果。
重复取消不重复生成事件。并发领取和 queued 取消须 compare-and-set；
运行完成、失败、取消只允许第一个成功提交的终态事务胜出。
cancel_requested=true 仍可能得到 completed（完整发布先赢得终态事务），
后来的取消不能删除结果。磁盘错误不能伪造 completed。

`committed_step=0` 表示尚未推进数值步；初始帧可在 running 时发布为 partial。
progress 只前进到已持久化并公布的完整步，不公布计算中的数组或半帧；
worker 内存中更晚的步不得出现在可查询记录中。
先写完整块至临时文件并持久化，再原子发布索引、事件与任务进度；
完整 manifest 发布与 completed 使用同一提交边界，失败可留下无索引孤立块，
孤立块不可查询。SQLite 与文件系统不能假定共享事务，N2 必须实现提交顺序与恢复核对。

`result.completeness` 为 none/partial/complete：none 时 result GET 返回 409，
包括启动前取消；至少一个块公布后为 partial。仅 completed 可为 complete，
failed/cancelled/interrupted 的已发布输出保持 partial。partial 默认不参加设计比较。
manifest 必须与 task 的 run_id、输入摘要、终态和已公布进度一致；每个 chunk ID 唯一，
步序与帧序递增且不超过 progress，chunk 内步范围与 manifest 一致，checksum 对原始响应
UTF-8 字节计算。帧采样允许不连续数值步，但 sequence 必须连续；最后一步必须输出。
这些跨记录约束不能仅靠 JSON Schema 比较字段，属于 N2 服务验收。

重启后所有旧 queued/running 均标 interrupted，并记录 `task.service_interrupted`；
已提交 partial 保留，终态记录保持不变。worker 可捕获异常为 failed；
进程丢失而未能提交失败则由恢复扫描转 interrupted。
磁盘不可写时不能承诺立刻保存失败诊断：拒绝新接收并返回 503，
恢复存储后再核对并标 interrupted。查询恢复不包含暂停、checkpoint 或断点续算。

## 幂等与不可变输入

`Idempotency-Key` 是必填 header，`request_id` 只追踪单次请求。作用域为一个
本地服务数据库中的 `POST /api/runs`；未来多用户服务必须另立权限和作用域合同。
严格 UTF-8 JSON 解码，拒绝重复成员、NaN/Infinity、非法 Unicode 和不满足
I-JSON 数字约束的输入。使用 [RFC 8785 JCS](https://www.rfc-editor.org/rfc/rfc8785.html) 的 UTF-8 字节做 SHA-256：
不自行做 Unicode normalization；对象按 JCS 排序，数组顺序保留，缺失与 null 不等同。
禁止用 `json.dumps(sort_keys=True)` 假装完整的 JCS 实现。

幂等内容为 Submission 去掉 `request_id` 后的完整对象，包含 edit_revision、
project、version_lock、execution、output_plan 和合同版本。属性书写顺序变化不冲突；
数组换序、版本锁、seed、步长、输出计划或 edit_revision 改变均算不同内容。
同键同内容返回 200 与原 task（即使原任务已失败或中断），不新建、不重新排队；
同键不同内容返回 409 `task.idempotency_conflict`。同时到达的同键请求通过数据库唯一约束
及同一事务处理，最多接收一次。新请求返回 202、Location 和 queued task。
先验证请求再冻结输入；输入快照、幂等行、task 和 accepted 事件必须耐久保存后才应答。
4xx/429 拒绝不产生可运行任务、不占用键；503/网络丢包后的客户端用原键原内容重试。

TaskCapabilities 公布 retention_seconds、idempotency_retention_seconds。
活动任务不清理；终态的最短保留时间从 finished_at 算，幂等记录不得早于任务清理。
仍保留的删除标记返回 410；彻底清理后未知 ID 返回 404，过期键可能创建新任务。
客户端超过幂等保留窗口后必须提示可能重复，不能自动重提交。

输入快照通过 `/input` 返回首次接受的完整 Submission；修改编辑器文档不会更改快照。
`document_sha256` 是 JCS(完整首次 Submission) 摘要；`scientific_sha256` 覆盖 JCS
对象 `{project, version_lock, execution, output_plan}`，保守包含整个 Project，
不包含外部 Workspace view/UI、request_id、edit_revision。同一科学摘要不会自动复用结果。
`registry_sha256` 是精确冻结 registry 文档的 JCS 摘要；`plan_sha256` 是编译计划 JCS 摘要。
N2 实现前还须确定计划序列化 Schema、实际库/backend/精度/RNG provenance；
未冻结前不能宣称跨版本可复现。示例中的重复十六进制摘要仅为结构示例。
服务器生成 run_id；嵌套 Project.run.run_id 仍是旧项目元数据，不能用于指定存储目录，
需在输出关联中明确区分，不能静默修改快照。

## 事件、查询与重连

事件 seq 从 1 开始、每个 run 内连续递增，与对应状态写入同一持久化事务。
每个事件携带当时 task 快照，其 last_event_seq 必须等于 seq。
GET events?after=N 返回 seq>N 的升序有界页面；客户端按 (run_id, seq) 去重，
完整处理页面后才保存 next_after。空页 next_after 等于请求值。
latest_seq 为本次读取快照的最新序号；has_more 只描述该快照，之后仍可能产生事件。
断线后用 run ID 读状态和原游标继续查询，无需重新 POST。
游标已清理（包括清理后 after=0）返回 410 `task.cursor_expired`，
客户端重新读取 task 并从其 last_event_seq 继续，只能恢复当前状态，不能假称历史事件完整。
负数、非整数或大于最新序号的游标返回 400。未来可增加 SSE，但轮询为本合同基线。

## 资源、错误与 N2 必测项

接受前检查版本、项目和模块语义、依赖锁、dt/steps/seed、输入 bytes、cells、voxels、
所有字段和输出采样的内存与输出估算；不得先分配大数组再发现超限。
TaskCapabilities.limits 所有数值必须由实际实现给出，本文不编造允许规模。
单 worker 的 active_runs 必须为 1，有界 queued_runs；超过静态预算为 413，
队列满为 429 + Retry-After。运行时继续约束增长、实际内存、输出和墙钟时间；
超过限制在安全边界失败，强制终止只保留已发布块，不自动降网格或缩短用户时长。
N2 必须测到磁盘可用空间不足和估算低于实际增长的情况。

错误集合包含稳定 code、severity、phase、相对于 Submission 的 JSON Pointer path、
UI targets 和可读 message；整体错误使用空指针。系统存储错误不能泄漏任意服务器路径。
建议代码：`task.idempotency_conflict`、`task.cursor_expired`、`task.resource_limit`、
`task.queue_full`、`task.worker_failed`、`task.output_write_failed`、`task.service_interrupted`。
HTTP code 与 issue code 分开；当前同步旧 error 对象保持原样。

N1 测试验证 Schema、正反例、状态矩阵和所有 OpenAPI 引用，不模拟异步服务并冒称完成。
N2 必须另外以真实服务测试：并发同键一次执行/冲突；排队与运行取消；
complete/cancel 双向竞争；重连分页与过期游标；worker 崩溃和重启扫描；
磁盘写失败与孤立块；资源超限；运行中编辑、旧响应晚到不替换新草稿；
partial 与 complete 原子可见；从安装包读取新增资源。

校验命令（不在仓库产生缓存）：

```powershell
$env:PYTHONPATH='src'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest discover -s tests -p test_task_contract.py -v
```
