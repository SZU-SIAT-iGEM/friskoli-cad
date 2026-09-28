# 模块与单菌体结果协议 · 0.1.0

状态：已实现结构校验、跨文件检查和第一批数值模块；运行服务尚未建立。本协议中的示例只用于证明数据能正确连接，不产生科学预测。数值示例与限制见[无扩散摄取循环](engine-runtime.md)。

四份 JSON Schema 分别定义[模块声明](../src/friskoli_cad/protocol/schemas/module.schema.json)、[行为图](../src/friskoli_cad/protocol/schemas/graph.schema.json)、[运行元数据](../src/friskoli_cad/protocol/schemas/run.schema.json)和[单菌体帧](../src/friskoli_cad/protocol/schemas/frame.schema.json)。`friskoli_cad.protocol` 另检查 Schema 无法表达的引用、单位、物质、连接时序和事件连续性。

## 模块声明与连接

模块声明包含标识、版本、作用范围、执行相位、科学角色、成熟度、端口、参数、内部状态，以及在 `t=0` 可读取的输出。作用范围为 `environment`、`source` 或 `population`；菌体组内的计算以 `cell.*` 端口表达逐菌体数组。一个图可以同时包含环境与菌体组模块，计算核心没有预先写死的模块插槽。

每个端口声明数据形状、物理量名称和单位。连接要求三者完全相同；单位换算应成为图中可见的模块。涉及具体物质的端口用 `species_parameter` 指向模块的字符串参数，连接双方的实际物质名称也要相同。这样受体活性不会误接到其他无量纲量，`cellobiose` 浓度也不会误接到另一种物质的输入。

图节点必须提供声明中的全部参数，并逐项记录值、单位及来源引用。`example` 来源只用于演示。`reference`、`exploratory`、`calibrated` 是模块对成熟度的声明；协议检查不替代文献、数据或模型验证。

连接的 `timing` 明确写为：

- `same_step`：本步读取。源相位不能晚于目标相位；本步连接不得形成环。
- `previous_step`：读取上一步。源模块必须声明该输出在 `t=0` 已可用。

必需输入要有且仅有一个提供者。图检查还拒绝重复节点、未知模块版本、跨菌体组的 `cell.*` 连接，以及不匹配的端口。

[常量场图](../examples/protocol/graph.json)和[扩散场图](../examples/protocol/graph-diffusion.json)使用相同的菌体采样模块，展示环境模块的替换；两图是早期协议示例，其中的 `field.diffusion` 未接入数值运行器。实际可运行的独立扩散模块与环境连接见[无通量扩散](no-flux-diffusion.md)。[表面展示模块](../examples/protocol/modules/display.surface_copies.json)的功能名称不依赖蛋白；图中的 `INP` 只是 `protein_id` 的示例值。

## 运行与单菌体序列

[运行元数据](../examples/protocol/run.json)列出菌体组和可观察通道，并把通道映射到图节点的输出与单位。[帧序列](../examples/protocol/frames.json)从 `frame_index=0`、`time_s=0` 开始，后续帧的序号连续、时间递增。每帧列出该运行中所有存活菌体的完整快照：稳定的菌体 ID、所属组、三维位置、四元数朝向和数值通道。

帧间事件按模拟时间排列。`division` 保留亲代 ID，为新子代分配新 ID；`birth` 引入一个新 ID；`death` 移除存活 ID。一个运行中 ID 不得复用。逐帧检查会将事件作用于上一帧，再核对本帧的菌体集合，因此前端可以明确创建、更新和移除可见菌体。前端可按 ID 保存选中菌体的历史，并用批量渲染展示全体。

0.1.0 使用完整快照作为逻辑协议。压缩传输、抽样记录和二进制帧需要后续单独定义，不能悄悄改变“缺席表示已离开”的含义。

## 当前边界与验证

协议还没有规定模块的 Python 执行接口、项目 v3 的全部字段、外部数据证据格式或空间求解器的数值方法。它不证明示例方程正确，也不把来源字符串视为已核实证据。这些工作随计算核心和模型迁入逐项完成。

在仓库根目录运行：

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests -v
```

测试覆盖有效示例、环境模块替换，以及单位、物质、时序、参数、菌体身份和事件错误。
