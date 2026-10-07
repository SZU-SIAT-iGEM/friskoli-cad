# Modular spatial 执行合同

`modular-spatial-v1` 是新增的显式执行合同。它使用 Project 0.6、Graph/Module 0.2、Catalog 0.5；Task 0.6 保存完整任务与续算关系。旧 profile 的版本、模块时序、文件解释和复现检查保持原样。需要新能力时创建或显式转换到新项目，不在读旧文件时自动添加机制。

完整运行图见[系统运行结构](../system-execution.md)，科学过程见[模块扩展](../science/modular-processes.md)。

## 执行入口

注册实现提供 `manifest`、`execution_contract`、`initialize(context)` 与 `propose(context)`。两个方法均返回 `ModuleProposal(outputs, state, effects)`。

`execution_contract` 声明 `api_version=0.1.0`、`stage`、`reads`、`writes`、`effects` 与 `backends`；`effect_targets` / `write_targets` 可使用固定目标、`$owner` 或指向字符串参数的 `$parameter_name`。系统在编译时检查唯一写入者和 effect 的结算阶段，在执行时检查类型与目标。`settled_outputs` 列出需经系统结算才生效的输出端口。返回未声明输出、错配状态、非有限数值、错误 shape/dtype 或未获授权的 effect 均拒绝当前事务。

`StepContext` 提供时间、dt、步号、节点 ID、owner、稳定实体 ID、实体集合、只读 world/inputs/parameters/state、随机流和已选 backend。实现只接收 `reads` 授权的 world 资源。`propose` 必须收到所有必需输入；`initialize` 可以在输入尚未产生时使用自己声明的初态，不能依赖系统伪造的零数组。插件代码属于明确安装的受信任 Python，不是安全沙箱。

## 调度与数据语义

阶段顺序是 `prepare → field → physiology → lifecycle → observation`，每阶段按编译后的依赖顺序执行。系统先结算共享场，再处理碰撞和可实现增长；lifecycle 模块读取已兑现的增长结果，系统最后处理死亡、分裂与状态继承。观察器每个数值步执行，保存帧频率独立。只有所有检查通过，整步才一次提交。

Graph 的 `same_step` 在当前依赖顺序中取值，`previous_step` 读取上个已提交状态；后者的来源必须声明初始输出。逆向阶段的 same-step 边和同一步环都在运行前拒绝。

声明在 `settled_outputs` 中的端口只能通过 same-step 连接到严格后续的阶段，或通过 previous-step 明确读取上一时步。摄取请求不能在同一 field 阶段被当作已接受摄入量；增长提案也不能在系统确认几何前触发分裂。模块若在系统处理时点之后声明相应 effect，预检会拒绝，不会静默丢弃或推迟执行。

端口保留 `shape / quantity / unit / species_parameter`，并支持：

| 属性 | 含义 |
| --- | --- |
| `dtype` | float64/float32、精确整数宽度、bool 或结构记录 json；不隐式降精度 |
| `tensor_shape` | 明确维度，可为正整数或 `parameter:count`，后者必须绑定 integer 参数 |
| `entity_set` / `entity_order` | 默认 cell 为 owner.cells/stable_id，field 为 world.grid/zyx；可用 `parameter:source_population` 明确映射来源 |
| `coordinate_frame` / `axis_order` / `location` | 空间参考、XYZ/ZYX、cell/voxel center 等采样位置 |
| `domain_revision` / `grid_revision` | 域和网格版本引用 |
| `temporal` | 瞬时量、速率、区间量等声明；不同含义须通过明确模块转换 |

参数支持 number/integer/boolean/string/array/object，结构参数同样检查 JSON Schema、来源和单位，并冻结为不可变快照。参数绑定只用于解析声明，不执行表达式。相同数组长度不能代替同一实体集合。`mapping.*_by_id` 使用目标 ID 到来源 ID 的显式表；源死亡或目标分裂后缺少映射时必须修订映射，不能偷偷复用数组下标。

每个 record/event 输入、输出和状态的 UTF-8 JSON 表示不得超过 1 MiB，嵌套不得超过 64 层；`cell.record` 限制作用于整个端口。系统实际检查该限制，并将它计入任务资源预算。数值场使用有 shape/dtype 的数组端口；逐步事件由任务服务流式保存，不能在一个 record 中无限累积历史。

## 检查与设备

`diagnose_project` 汇总独立结构、参数、连接、几何和 owner 错误。依赖尚未正确的参数时，后续类型检查标 `skipped`；没有服务和输出设置时，资源检查标 `unknown`。历史 scheduler 标 `not_applicable`。只有真检查过的内容标 `pass`，未知不冒充通过。Task preflight 另检查真实资源和输出计划。

Planner 使用实际注册的 backend，输出阶段、节点实现、求解组、数据传输、所有权与数值数组内存下限。全图选择 `numpy-cupy-cuda` 时，只支持 CPU 的模块仍在 CPU 上运行；有限体积扩散使用明确的 CUDA 实现。当前系统协调数据存放在主内存，CUDA 求解有明确往返，不承诺整图 GPU 驻留。实际内存、细胞规模与输出预算仍由任务层检查。

模块可通过 `execution_contract.workspace_bytes` 声明非数组工作区的 `fixed`、`per_voxel`、`per_cell` 非负整数字节预算；population owner 按该群体规模计算，environment owner 按总菌数计算。Planner 汇总至 `declared_workspace_bytes`，Task 准入同步计入。三角网格模块为有界 winding/SAT 工作区声明 128 MiB，并为体素碰撞盒保守声明每格 1024 bytes；不会只预算浓度数组而遗漏几何对象。

## 包、版本与恢复

Project 可以保存 `dependency_lock`，其中每包固定 ID、版本与 SHA-256。加载时检查完整依赖闭包和安装文件内容，再与内置模块组成 registry。任务冻结该 registry 与实现来源；执行前和 checkpoint 恢复时都核对。缺包、被修改文件或版本冲突不会自动安装、自动升级或继续使用旧代码。

分裂、死亡、迁移以及 checkpoint 的策略跟随状态声明；没有相应迁移规则的状态要拒绝转换。新运行段关联父运行、完整状态与原输入。原记录保留，恢复不会覆盖历史帧。二进制 checkpoint 与逐帧数组描述符的任务合同见[任务服务](../archive/legacy-protocols/task-service.md)。

## 验证入口

`tests/test_module_system.py` 检查只读输入、授权 effect、实体/时间语义、结构参数冻结、设备选择、owner 冲突、非法阶段依赖与独立错误汇总。`tests/test_packages.py` 包含实际安装包在新 Python 进程中进入普通 modular 模拟一步的验证。科学组合、完整状态恢复、12 h 基础任务和主前端测试分别记录在本轮验收文档；单个接口测试不代替模型的科学验证。
