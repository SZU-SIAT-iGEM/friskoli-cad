# 工作区与服务协议 · 0.2.0

本页保留 0.2 格式与兼容接口记录。新保存格式为 Workspace 0.3，新增对象关联和数据节点折叠；API 仍是同步 0.2，详见[注册目录与工作区 0.3](registry-contract.md)。

状态：2026-09-29 已实现。适用于当前本地服务；交互规则见 [Interaction Specification](interaction-specification.md)，机器接口见 [OpenAPI](openapi.json)，工作区格式见 [JSON Schema](../src/friskoli_cad/protocol/schemas/workspace-v0.2.schema.json)。

## 版本与对象关系

| 对象 | 当前版本 | 内容与所有者 |
| --- | --- | --- |
| Workspace | `workspace_format_version: 0.2.0` | 前端的可编辑文档：项目、菌群初始化块、节点布局、求解设置 |
| Project | `project_version: 0.1.0 / 0.2.0` | 科学输入快照；0.2 可保存初始胶囊尺寸与来源 |
| Module / Graph / Run metadata | `protocol_version: 0.1.0` | 后端登记、编译与运行通道；具体模块有独立版本 |
| Replay | `replay_format_version: 0.1.0` | 完整帧序列及实际计算的浓度场；HTTP 响应增加可选 `execution` 元数据 |
| Frame | 0.1.0 或 `frame_version: 0.2.0` | 一个时刻的全部存活菌体及事件；0.2 包含逐帧尺寸 |
| Native result | `result_format_version: 0.1.0` | 提交的 Project、求解设置、execution、Replay；只导出成功完整的运行 |
| Local API | `api_version: 0.2.0` | 能力查询、初始状态检查、同步求解与错误结构 |

这些版本独立递增。编辑器升级不改变科学模块的公式、版本或结果帧语义。旧原型的 `format/version/scene/workflow` 文件不能当作本仓库 Workspace 导入；需要未来的显式转换器，目前会拒绝。

## Workspace

完整样例见 [workspace.json](../examples/protocol/workspace.json)。顶层字段为：

- `workspace_format_version`：固定为 `0.2.0`。
- `project`：已有项目格式。草稿允许空行为图、缺少连线或科学上无效的参数组合；加载先检查结构，运行前交给后端做语义校验。
- `population_blocks`：初始化工具，不是额外的动力学状态。每项有稳定 `id`、显示 `name`、`center[3]`、`size[3]`（均为 µm）、`rotation[3]`（XYZ Euler，度）、`count`、胶囊总长 `length` 与直径 `diameter`（µm）、整数 `seed`、`dirty`，以及可选 `hidden`、`locked`。
- `graph_layout`：节点 ID 到 `{x,y}` 的映射，单位为工作流画布 CSS 像素；不参与计算。
- `run_settings`：`dt_s > 0`，整数 `steps` 为 1–100。当前每个计算步记录一帧，不能另设记录间隔。

旋转按 Three.js 的 XYZ Euler 约定形成四元数。散布先在局部长方体内按 seed 均匀抽样，再旋转位置和朝向。完整块的八个角必须处于场地内；薄层只允许绕 Z 旋转。胶囊保守留出边缘空间，**不检查菌体间重叠**。重复 seed、尺寸和数量产生相同初始条件。改变数量保留仍存在的初始 ID，新 ID 使用菌群前缀；删除群组同时删除其节点、连接与结果通道引用。

拖动、旋转、缩放和修改数量只更新块并设为 `dirty`。散布是一个可撤销事务：成功后写入 `project.groups`，缺少所属节点时添加后端登记的 `population.static@1.0.0`；失败则恢复整个草稿。散布不求解。未散布的块阻止 Run。隐藏和锁定只影响编辑视图，不能删除求解中的菌体，也不隐藏 Results 的真实状态。

读取旧 Workspace `0.1.0` 时补充零旋转、未隐藏、未锁定与 `{dt_s:0.5,steps:8}`。已有求解设置存在时保留。读取纯 Project 时从初始菌体包围范围生成编辑块；尚未散布时保持原始菌体位置与尺寸。下一次保存写 0.2.0。未知未来版本拒绝，失败导入不得覆盖当前文档。

## 请求与结果关联

`GET /api/capabilities` 返回格式版本、运行方式、限制与可放置对象。`GET /api/modules` 返回真实 registry 的全部声明，前端按 `id@version` 获取端口、参数、相位及成熟度。前端只预检连接；后端 compiler 是科学连接的最终校验者。

`POST /api/validate` 与 `POST /api/replay` 接收以下 JSON；旧的三字段请求继续有效。

```json
{"project": {}, "dt_s": 0.5, "steps": 8, "request_id": "run-unique-id"}
```

`project` 在实际请求中必须是完整项目。`request_id` 可省略，存在时须为 1–128 字符。它仅用于关联请求与响应，不是服务端幂等键或持久任务 ID。

Validate 校验项目、编译行为图、建立 `t=0` 状态并检查当前查看器资源限制，不推进任何数值步。成功返回 `valid:true`、空 `issues`、菌体数 `cells` 和格点数 `voxels`。它不预先证明全部后续步可执行；例如扩散稳定性、生长后越界和后续过量摄取仍可能在 Run 中失败。

Run 读取提交瞬间的深拷贝。浏览器生成唯一 run ID 并写入提交副本的 `run.run_id`，保留原草稿。响应的 `execution` 包含 `api_version`、原样回传的 `request_id`、`status:completed`、`dt_s`、`steps` 和 `project_sha256`。哈希采用 Python `json.dumps(project, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)` 的 UTF-8 SHA-256；它记录服务收到的项目，不要求其他语言的普通 JSON 序列化产生相同哈希。

运行中继续编辑只改变草稿；完成时不会用旧副本覆盖编辑。前端用请求 ID 校对响应，并保存每次运行自己的项目和设置。较早修订的结果带提示。Undo/Redo 也产生新的草稿修订，因此提示代表编辑时序，不表示数值结果一定不同。

当前服务以一次 HTTP 请求完成求解；前端等待期间保持响应。**没有持久队列、进度流、取消、暂停、恢复或部分结果接口**。关闭页面后无法重连该请求，刷新仅恢复草稿；结果需先导出。本地历史在当前页面会话中保留成功与失败记录，结果文件导入与跨会话结果库留到下一版本。

## 错误与资源

错误返回 `{error:{code,path,message}}`。`code` 稳定用于定位类别，`path` 为科学协议中的位置或 `/`；`message` 保留后端详情。HTTP 400 表示请求字段/标识错误，413 表示限制超出，422 表示结构、科学参数或数值失败。任何失败不交付一个标为 completed 的部分 Replay，也不清除上一份成功结果。检查面板目前展示后端首先发现的问题，修复后可继续检查。

当前限制：请求 1,000,000 bytes；前端工作区文件 2 MB；每帧 2,000 个菌体；XY 截面 4,096 格；`voxel_count × max(1, actual_field_count) × (steps+1)` 不超过 1,000,000。服务在分配世界之前先检查格点和初始菌体上限，运行中继续检查分裂后的数量。限制属于本地服务与查看器，不是科学模型的适用范围。

## 扩展规则

未来对象必须同时具备 backend 科学声明、初始化/运行适配器、前端操作适配器和验收用例；仅有图标不能启用。新增异步服务时另加 `/api/runs` 资源，定义 queued/running/completed/failed/cancelled、事件序号、断线重连、快照哈希和能力协商；在实现前不得将播放暂停冒充求解暂停。二进制/增量帧、独立采样频率、运行中换模块与状态迁移均另立版本，保留当前完整快照语义。

导出目前是 Friskoli 原生 JSON 与逐帧统计 CSV，未实现或声明 SBOL、SBML、SED-ML、OMEX 兼容。标准适配器要有独立转换、损失说明和相应验证，不能通过更换扩展名获得兼容性。
