# Task 0.6：长时计算、保存恢复与按需数组

实现日期：2026-10-03。旧 Task 0.1–0.5、旧 Workspace 和 Design Schema 不追溯修改。

## 运行与版本

Task 0.6 支持声明完整 checkpoint 的 spatial、chemotaxis 与 modular profile。步数和输出间隔上限为 4,320,000；`dt_s` 独立保持，`.01 s × 4,320,000 = 43,200 s = 12 h`。更小 dt 需要更多步时，超过能力上限应明确拒绝。旧 Task 保留 10,000 上限，同步接口的限制独立保留。Design/brief 0.3 和 Workspace 0.7 接纳长时设置；旧设计解释不变。

输出资源预检按实际 frame interval、预览格距、字段和 byte budget计算。大场单帧数组允许切成多个块；每块仍受 chunk byte limit 约束。事件接近缓存预算时主动保存一帧，数值计算没有跳步。单步本身超过容量仍拒绝；有界机制不承诺任意人口或无限历史都能装入机器内存。

Modular 内存估计采用 planner 的 output/state dtype 与 tensor 尺寸，按生命周期 division effect 预留当前运行器人口上限，按注册的浓度端口计算 field 库存。Record 每端口最多 1 MiB，估计包含该上限及保守复制系数；实际 worker RSS 仍独立限制。RNG provenance 来自模块执行声明。

碰撞 broadphase 额外按 `32 × N²` bytes 预留 pair workspace，N 为可能同时存活的细胞数上限；覆盖 threshold、坐标差与绝对值 float64 矩阵以及同时保留的布尔 mask。提高人口 cap 会同时增加这项二次空间和状态/输出预算。

模块还可声明 `workspace_bytes` 的 fixed/per_voxel/per_cell，planner 按节点求和后加入 Task 预算。Triangle mesh 的声明为 128 MiB fixed + 1024 bytes/voxel，覆盖 mesh 分块工作区和可能的 voxel BoxObstacle 对象。

Project 0.6 可设置 `system_limits.max_cells`，默认 256，含全部 population；不得超过 Task service 的 cells 资源上限。Modular 不沿用旧 spatial 的固定 256 能力上限。预算按声明的 cap 预留分裂后内存和输出；到达 cap 后再尝试分裂会以 `resource.cell_limit` 结束 Task，不提交该步，也不记录为生物分裂受抑或几何阻塞。worker 保存最后已提交状态。可对失败运行预演迁移，显式提高该资源设置后创建子段继续，方程、细胞、RNG 与库存不变。

新运行每隔 `checkpoint_every_steps`（默认 1000）保存完整状态。数值 worker 在既有壁钟预算的 70% 附近保存状态并排回队列，使用相同 run_id 自动继续；没有提高服务壁钟上限。初始化、单步或 checkpoint 自身超过实际预算仍会失败。每个 worker 只发送一个待确认消息，父服务校验文件并提交 SQLite 后才能继续。

## 暂停、下载、导入与继续

- `POST /api/runs/{id}/pause`：空 body，在下一已提交边界保存完整 checkpoint 后进入 `paused`。
- `GET /api/runs/{id}/checkpoint`：下载完整 ZIP，含冻结项目、状态、RNG、ID 历史和数组。
- `POST /api/runs/{id}/resume`：`{request_id, edit_revision}`，必须带 `Idempotency-Key`。创建关联子段，`parent_run_id` 和绝对 `start_step` 明确来源；旧运行和结果不改写。
- `POST /api/runs/checkpoint-import`：raw ZIP 上传，显式 Content-Length，有界分块写入；返回 `checkpoint_id`、冻结 project、step/time/seed。
- `POST /api/runs/from-checkpoint`：`{checkpoint_id, submission}` 和幂等 key。project/seed 必须与状态一致，steps 是大于已保存边界的绝对目标。

`interrupted`、`failed`、`cancelled` 或 `paused` 可从最后成功保存的状态创建新段；进度消息可能比最后 checkpoint 新，这部分未经保存的进度不会被误当作可恢复状态。父段与子段组合时，父历史仅取到子段的 `start_step`，同一边界只显示一次。保存状态含尚未输出的事件；自动 worker 更换不丢事件。保留子段时，清理操作保留其父记录。

checkpoint 格式 `task_checkpoint_version=1.0` 使用不压缩 ZIP；metadata ≤16 MiB，每个数组段 ≤4 MiB，总文件和数组分配受服务内存预算限制。文件中不保存 pickle 或可执行代码。读取前检查条目数量、名称、大小、dtype、shape、连续 offset 和 hash；随后原有运行器继续检查科学状态、实施锁、RNG、几何和物质量账。旧独立 JSON checkpoint API 默认行为不变。

## 运行边界迁移

先暂停，再提交 `POST /api/runs/{id}/migration-preview`：

```json
{"project": "完整目标项目对象", "mapping": {"fields":"conservative-volume","cells":"identity","module_state":"identity"}}
```

返回 `preview_sha256` 和逐物种库存审计。执行 `/migrate` 时带上相同输入、preview hash、request_id/edit_revision 和幂等 key。只在候选项目、数组、模块状态、几何、库存及 RNG 全部校验通过后创建子段；预演或发布失败不改变父段。

上述三字段映射用于同一物理域内的守恒体积平均重划，保留细胞身份、位置、模块状态和未来日程；旧场模块可显式改变 diffusivity。未知模块状态映射、隐式改变物种库存或图结构会拒绝。障碍体素变化若会吞掉库存，同样拒绝。

Project 0.6 / modular-spatial-v1 还支持显式物理域映射，其他 profile 保留原同域限制：

```json
{"fields":"conservative-volume","cells":"identity","module_state":"identity","domain":"physical-coordinates-zero-fill","sources":"identity"}
```

原点和物理 XYZ 坐标保持不变，不缩放细胞、材料或来源。相同 geometry mode 下可扩域，新体积浓度为零；缩域只允许裁切零库存。任何非零浓度/amount，即使很小，若对应体素有部分被裁切，也会拒绝。真实细胞外形、材料几何、来源支持域、未来空间日程和保存的死亡材料位置必须仍合法。自定义模块尚无独立的空间支持声明与映射注册合同，异域迁移提前拒绝；内置模块的物理支持按已知参数和状态检查。

Modular state 必须声明 `on_migration`：非场状态接受 `copy`；场 scalar/vector/tensor 接受 `conservative_regrid`。支持浮点 uM concentration 与每格 molecule amount，vector/tensor 按 ZYX 后声明的分量逐一积分；每格 amount 额外按体素体积比转换。未知单位、整数场、未解析的 tensor_shape 或 module mapper 拒绝。非零缩域外流需要独立的外流库存账及 module-state 结算合同，当前没有这项机制，不能静默删库存。

预演 audit 返回 source/target grid、固定坐标/零填充/零裁切规则、逐物种和逐场分量库存，以及 origin project hash；创建子段的 accepted 事件和后续 Task/Manifest 包含 `migration_audit`。异域 checkpoint 保存首次迁移前 `migration_origin_project`，完整核对除已支持域/资源/diffusivity之外的科学参数、图、种子字段、依赖锁与日程，并从原域重新计算初始库存基准。连续迁移保留最初 origin，不把扩域初始化误记为新增营养；binary 下载重读和后续 checkpoint 继续保留它。

迁移边界保留非场模块的最后已提交输出和 observer 指标；它们在下一数值步按模块顺序更新，不在迁移时额外推进方程或日程。

## 逐帧数组与索引分页

每帧 JSON 继续通过 `chunks` 索引读取。浓度值变为 `array` 描述符：`array_version=1.0`、`dtype=<f8`、ZYX shape、C order、axis_order、bytes、SHA-256 和连续 segments。每段包含 name、offset、bytes、hash 与独立 href。完整数组 hash 是原始 little-endian 字节 hash。浏览器先检查 shape×itemsize 和自己的解码预算，再分段读取与校验；可用 typed array 保留数据，不要求转成嵌套 JS 数组。

`GET /result?offset=N&limit=L` 对 Task 0.6 最多返回 1024 条 chunk 索引；返回 `chunk_offset`、`total_chunks`、`next_chunk_offset`。默认从 0 开始。客户端应按需缓存少量索引页和帧，不能把整个运行的数组重新收集进一个无限增长数组。旧合同继续提供原 manifest。

## 已执行验证和限制

`tests/test_task_longrun.py` 覆盖二进制损坏/预算、RNG 连续恢复、真实 spawn 暂停续算、同域重划、旧状态不变、新 Task/Manifest/Chunk Schema 和 432 万步准入；HTTP 测试覆盖 raw checkpoint 上传重读、恢复、数组下载和索引分页。旧 checkpoint、Task、Design 测试继续运行。

独立数值用例 `tests/verify_12h_task.py` 已完成真实 Task 的 **43,200 × 1 s**，最终 time_s=43,200.0；包含显式暂停、新段续算、原 60 s worker 壁钟预算内的自动状态续算、非负有限场以及最终完整 checkpoint 重读。用例从 outset 选择 1 s：单 voxel、单 cell、有限初始营养、D=0、0.01 µm/s、恒定 motor bias、saturating uptake、reserve 和 starvation survival。原始证据在本机临时目录 `friskoli-foundation-12h-20261003/evidence/verification.json`，输入及隔离源码快照一并保留。

这项验证不等于 PTS-A 使用 `.01 s` 完整执行 432 万步，不是菌株实验标定，也不证明小时级生物预测准确。壁钟加速由用户另行验收；本文不报告加速倍数。
