# 注册目录与工作区 0.3

本页保留 2026-09-29 N1 注册目录与 Workspace 0.3 的历史合同。**2026-10-03 扩展：** 新的 `modular-spatial-v1` 使用 Catalog 0.5、Project 0.6 与 Module/Graph 0.2，模块统一通过只读输入和提案接口执行；Workspace 0.7、Task 0.6 增加组合事务、长时任务与状态续算。完整结构见[系统运行图](system-execution.md)，接口见[新执行合同](protocol/modular-profile.md)，安装、依赖解析和精确锁见[包管理](package-management.md)。实施与验证进展见[本轮记录](archive/planning/implementation-2026-10-03.md)。下文“未实现”描述限于 N1 原阶段，不能代替上述当前合同。

当前按 execution profile 分别提供 Catalog 0.1/0.2/0.3、Project 0.1–0.4 与 Task 0.1/0.2/0.3；具体支持组合由 capabilities 公布。下方 N1 版本表记录原阶段合同，不代表当前服务只有这些版本。

## 现在可以怎么使用

启动本地服务，在欢迎页选“几何与浓度采样”。Space 显示菌群；Workflow 显示初始化、胶囊读取、浓度场及采样连接；选择胶囊读取或位置采样模块，右侧可以查看公式、符号与接口的对应关系、实现位置和测试引用。运行后在 Results 读取样例记录。示例使用构造验证参数，不表示真实趋化或实验标定。

也可以新建项目，在 Space 放置菌群块、设置数量/长度/直径/位置/体积/种子并点击“散布”。散布后生成稳定群组以及初始化、几何读取节点。这整个操作可撤销/重做。数据节点默认折叠，点击 + 展开端口；折叠状态、节点位置、对象类型和自动关联随工作区保存。重复散布不会重复添加读取节点；主动删除过的读取节点不会被悄悄补回。

未计算的放置块仍是编辑输入，标为待散布，运行按钮会禁用。散布与运行检查按当前 profile 执行；`spatial-unbiased-v1` 已检查胶囊与墙、障碍及其他胶囊的接触，初始重叠会被拒绝。旧 profile 保持原先的空间语义，不能据此宣称已有相同碰撞能力。

## N1 原阶段合同与版本

| 合同 | 本次状态 |
| --- | --- |
| Catalog 0.1.0 | 新增，GET /api/catalog；独立于执行 manifest |
| Module / Graph / Run 0.1.0 | 保持原合同与时序；/api/modules 保留原响应 |
| Project 0.1.0 / 0.2.0 | 不自动升版，不更换参数或模块版本 |
| Workspace 0.3.0 | 新保存格式；可读旧 0.1/0.2，新增对象关联和数据节点折叠 |
| API 0.2.0 | 保留同步接口，capabilities 增加 catalog/workspace 支持列表 |
| Task 0.1.0-draft.1 | N1 当时仅有草案；现已由 [N2 稳定合同 0.1.0](protocol/task-contract.md)及后续 profile 合同实现 |

Catalog 内含原样 manifest、与执行实现核对过的说明 entries，以及对象 objects。条目声明 category、端口 shape/quantity/unit/entity、CPU 能力、world_access、数学/算法说明与证据引用。每条连接的 same_step/previous_step 仍由 Graph 明确记录；catalog 不重新解释它。N1 Catalog 0.1 的 execution_semantics 为 `legacy-explicit-v1`；PTS 与空间 catalog 各自使用独立 profile，不修改旧图的语义。

前端只支持已实现的 initializer adapter。N1 的 adapter 是 population.block@1；它消费后端声明的对象类型、属性字段和初始化/数据模块引用。新增同类对象或已受支持形状的计算模块无需改前端 ID 名单。新几何类别或新初始化算法仍需实现相应 adapter，未知 adapter 显示不可用。这里没有插件热加载、任意脚本执行或插件安装入口。

空间 Catalog 0.3 另提供 `environment.node@1`，已登记轴对齐障碍、有限局部源及可降解材料盒。材料放置会检查并关联声明了 `material.degradation` role 的机制；对象是否可放置仍由当前 catalog 与 adapter 决定。纤维、MCP 和营养耦合生长没有因此实现，空间 profile 也不执行外部输入日程或分裂。

## 文件职责

- src/friskoli_cad/registry.py：把运行模块转换成可验证 catalog，防止说明覆盖实际身份、端口或 world_access。
- engine/declarations/：对象属性、算法与 LaTeX、符号映射、源码及测试引用。
- engine/geometry.py：胶囊只读几何模块；manifest 与实现分别登记。
- protocol/schemas/catalog.schema.json、workspace-v0.3.schema.json：已实现合同。
- web/catalog.mjs、placeables.mjs：前端目录、adapter 选择与原子初始化。
- web/migration.mjs：旧工作区适配及逐项报告。
- web/math-inspector.mjs、registry.css：离线公式与说明；沿用现有面板和字体。
- examples/runtime/registry_readout.project.json：贯通验证样例；src/friskoli_cad/examples/ 内保存随安装包交付的相同副本。

## 数值与数据所有权

geometry.capsule_readout@1.0.0 只读所属群组的当前位置、总长 L、直径 d，并给出 A=πdL、V=πd²(L−d/3)/4。总长包含两端半球；L=d 时退化为球。缺失几何、非法长度和越界都有 code 与对象位置，不代填数值。

原内核通过 position/heading 和 length/diameter 输出识别状态写入者；新的只读模块显式声明 world_access=read_only，防止读取模块被当作第二个运动或生长模块。旧模块保持 legacy_inferred。读取在 phase 3，位于当前运动/生长 phase 2 之后、浓度采样 phase 4 之前；分裂后按既有 refresh 顺序重读。既有示例的事件、几何、轨迹、原 channels 和浓度数组均作回归对照。

position 可以连到 field.sample_box_support@2.0.0 的 position 输入，仍要求同群组。场输入要求相同物种、quantity、shape、unit；相同单位不代表不同物理量可相连。面积读取本身不引入膜容量约束，采样不等于摄取或趋化。

## 数学说明的边界

首批明确登记了菌群初始化算法、胶囊面积/体积、重叠权重浓度采样。其余既有模块显示“尚未登记数学说明”，不会伪装已审查。tested 表示登记了实现测试引用，不代表方程自动证明、实验标定或科学模型有效性。

LaTeX 使用固定版本 KaTeX 0.18.9 离线渲染，资源与 MIT 许可证随包交付，见 [第三方资源说明](../src/friskoli_cad/web/vendor/katex/README.md)。trust=false，公式不执行模型代码；源码、测试路径作为文本展示，不自动加载外部内容。

## 迁移与未知模块

旧工作区导入报告列出编辑 metadata 升版和体积块推断。推断块只提供编辑手柄，原菌体位置不重新抽样；用户点击散布才改变初始化。恢复存储沿用旧 recovery key，能读取已保存草稿。旧 Project、Graph、边 timing、参数和模块版本保持原样。

缺模块或缺指定版本时，保留节点、参数、原连接，可阅读与保存；端口以 unknown 标识，运行禁用。既有格式之外的未来版本拒绝导入。报告与当前检查有区别：迁移成功不表示模型能运行。

## 验证

- Python：目录完整性/引用、只读权限与源码声明、球极限、非均匀场采样、错误位置、旧帧逐值回归、HTTP 目录和离线资源。
- JavaScript：动态登记、unsupported adapter、原子初始化、撤销/重做快照、保存重开、手改图不覆盖、未知模块、旧格式报告、离线 LaTeX。
- N1 当时将新 workspace Schema 与已有 Project 引用一起验证，Task 草案只测结构与正反例；后续真实 worker、任务取消及恢复查询见 [N2 验收](archive/verification/verification-n2.md)，空间与 M4 验收分别见[空间记录](archive/verification/verification-spatial.md)和[M4 记录](archive/verification/verification-m4.md)。
- 最新源码与安装包验证见 [PROGRESS](archive/planning/PROGRESS.md)；未执行的设备或操作不视为通过。
