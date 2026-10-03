# Friskoli-CAD

Friskoli-CAD 是面向趋化工程菌设计的模块化仿真与可视化工具。空间场景提供初始条件；行为图用有类型、单位和时间语义的端口连接计算模块；运行结果保留单菌体身份与变化序列，供前端回放和检查。

**开始使用模块：** [模块使用指南](docs/module-guide.md)说明如何选完整案例、连接基础过程与 PTS/MCP、调整速度/几何/释放/摄取/生存参数，并区分旧科学 profile 的 28 个模块与新统一 profile 的 [73 个注册模块](docs/module-reference.md)。新增五套 `foundation-*` 图显式包含营养储备和持续匮乏生存；原六套研究案例保留。数学、状态归属及 constructed 参数限制见[基础组合说明](docs/science/foundation-composition.md)。

**2026-10-03 系统与模块扩展：** 新 `modular-spatial-v1` 通过统一接口执行注册模块，提供类型/状态规则、CPU/CUDA 调度、本机包管理、多阶段检查以及两套可编辑的基础/材料模板。Task 0.6 支持目标物理时长、暂停/续算、完整二进制 checkpoint、同域重划、显式固定坐标的异域零填充扩缩及按需结果数组；Workspace 0.7 增加共享命令、编辑事务、三方模板升级、离线目录、最近项目和 ViewState。完整职责与时序见[系统运行图](docs/system-execution.md)，实现及边界见[本轮记录](docs/archive/planning/implementation-2026-10-03.md)。12 h 基础机制任务已实际完成；PTS `.01 s` 的 432 万步及原 N5 大场 10000 步尚未完成验收。

**2026-10-02 阶段记录：** N5 已调整为完整模拟与用户流程验收。通用 Task 0.5 支持独立的场预览格距、保守体积平均、完整分辨率末帧 NPZ、显式 CPU/CUDA 扩散后端和实际资源预检；旧 Task 0.1–0.4 保留。Design 已支持分批、失败重试、选择性重跑与 IndexedDB 历史；静态 Wiki 和有限 SBOL/OMEX 导出也已实现。局部微量摄取和生长的表示精度问题已修正，真实256³、200菌体前100步与阶段候选包安装检查通过，尚未完成10,000步验收。所有机制按“系统 → 基础模块 → 特殊模块”整理，PTS展示名明确为重建版/简化版。见[当前要求与验证](docs/archive/verification/verification-n5-simulation.md)、[Task 0.5 与资源配置](docs/task-field-previews.md)、[标准范围](docs/standards-export.md)和[Wiki](docs/wiki-publishing.md)。

**尚未完成的工作：** [独立待办清单](docs/archive/planning/remaining-work.md)逐项区分待实现、部分实现、待验收和依赖数据的后续研究，列明完成条件与依据。当前优先完成 N5 的完整 10000 步及主前端流程；已实现能力和已取消设想不重复计入待办。

**2026-09-29 工程规划：** 面向完整 CAD 的目标、候选设计、前后端结构、数值模块、插件与标准导出已整合到[工程设计总纲](docs/archive/design/README.md)。采用保留已验证核心的增量重构，先贯通真实趋化案例与设计比较；规划中的能力须逐项实现和验收，当前实施状态以下文和 PROGRESS 为准。开发顺序见[新版路线及验收](docs/archive/design/roadmap-and-acceptance.md)，原 4c–5 事项均有对应关系。

**当前状态：版本化连接协议、数值核心、可编辑工作区和本地结果回放已建立。** 前端现在可以从欢迎页新建、打开或恢复项目，在 Space 中创建菌群体积块、固定种子散布、移动/旋转/缩放、隐藏/锁定/复制和删除；在 Workflow 中拖动节点、连接端口、编辑参数并检查单位和时间语义；运行前通过后端校验，运行后保留不可变的结果记录、时间轴、单菌体检查、浓度数据和 JSON/CSV 导出。工作区保存格式为 `0.7.0`（可读 0.1–0.6，并保留原计算规则），协议、OpenAPI 与交互约定分别见[工作区协议](docs/archive/legacy-protocols/workspace-protocol-0.2.md)、[OpenAPI](docs/openapi.json)和[交互规范](docs/interaction-specification.md)。旧版 `friskoli-cad-webui` 提供布局和功能迁移参考，未声明的旧对象类型不会直接进入当前协议。

开发状态和每阶段验收见 [PROGRESS.md](docs/archive/planning/PROGRESS.md)，后续顺序见[开发安排](docs/archive/planning/development-order.md)，协议新增点和迁移规则见[协议扩展计划](docs/archive/planning/protocol-expansion-plan.md)。系统边界见 [架构约定](docs/architecture.md)，已实现的连接规则见 [协议 0.1.0](docs/archive/legacy-protocols/protocol-0.1.md)，执行顺序见 [图编译器](docs/engine-compiler.md)，第一个数值例子见 [无扩散摄取循环](docs/engine-runtime.md)，基础输运见[无通量扩散](docs/no-flux-diffusion.md)，移动与摄取时序见[移动菌体](docs/moving-cells.md)，项目格式与外部输入见[物种目录和输入日程](docs/project-schedule.md)，初始形状与容纳检查见[胶囊尺寸](docs/capsule-geometry.md)，逐帧几何与演示性生长见[生长帧](docs/growth-frames.md)，分裂与状态继承见[长度 adder 分裂](docs/adder-division.md)，本地服务与界面见[结果回放](docs/replay-ui.md)，工作区和交互说明见[前端用户历程](docs/frontend-user-journey.md)，两种环境的实际替换见 [环境模块对照](docs/environment-swap.md)，薄层与 3D 的选择见 [空间尺度](docs/spatial-resolution.md)，当前格距的局限见 [格点细化诊断](docs/grid-refinement.md)与[固定作用范围格距对照](docs/fixed-support-refinement.md)，换格时的物质量守恒见 [浓度场重划](docs/conservative-regrid.md)，跨格采样与沉积见 [固定物理作用范围](docs/box-support.md)，状态归属与扩展规则见[状态归属说明](docs/state-ownership.md)，Git 与代码迁入规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## N1 注册目录与数学说明

对象库和属性面板由后端注册声明生成；菌群散布自动加入可折叠的胶囊读取节点，保存、重开和撤销操作保留关联。欢迎页新增“几何与浓度采样”，模块面板提供离线 LaTeX、符号和证据说明。旧项目缺少指定模块时仍可阅读和保存，运行禁用。详情见[注册目录与工作区 0.3](docs/registry-contract.md)。

N2 已增加[本地异步任务服务](docs/task-service.md)：SQLite 持久队列、独立计算进程、取消、事件游标与分块结果；前端运行记录独立于正在编辑的草稿，支持刷新后查询。稳定[任务合同 0.1.0](docs/protocol/task-contract.md)与旧同步接口并存。旧 Task 0.1/0.2 只传单菌体帧；空间 Task 0.3 可传真实浓度场和对象库存；科学 Task 0.4 进一步提供逐步指标和死亡规则详情；通用 Task 0.5 在此基础上提供输出预览与完整末帧场，计算网格保持不变。旧任务保留原查询规则；[Task 0.6](docs/archive/legacy-protocols/task-contract-0.6.md) 在完整 checkpoint 边界创建关联的新运行段继续计算。

**N3 首批 M0–M3：** 欢迎页的“共享底物与 PTS 信号”可运行有限均匀底物、两种来源的 PTS 容量、摄取请求、跨菌群共享结算和 EI/CheA/CheY 信号，共 8 类模块。新执行规则、Project 0.3 / Catalog 0.2 / Task 0.2 独立版本化，保留旧图与任务行为。参数来源、构造例和数值限制见[科学说明](docs/science/pts-minimal.md)，连接与执行顺序见[新 profile](docs/archive/legacy-protocols/pts-bulk-profile.md)。这一步是固定菌体的信号链，motor bias 尚未驱动运动；完整趋化反馈已由下述科学 profile 单独实现；实验标定尚未完成；可安装 registry 已在新的统一 profile 中实现；候选设计与受限标准导出的当前实现见下文。

Workflow 节点现由名称/ID、端口、参数摘要、数学式和状态组件组合；完整 ID 可复制，图内提供独立缩放和适合图形。实际浏览器与设备限制见[组件验收](docs/archive/verification/verification-workflow-components.md)，早期固定 PTS 验证见[阶段记录](docs/archive/verification/verification-n3.md)，完整 N3 见[本轮验收](docs/archive/verification/verification-n3-complete.md)。

**N3 空间基线：** `spatial-unbiased-v1` 使用 Project 0.4 / Catalog 0.3 / Task 0.3，注册障碍物、可降解底物和引诱物源。底物放置自动生成有删除约束的全局降解机制；显式表面酶与接触范围限制降解，产物和直接来源进入同一养分场，供 PTS 感知与摄取。胶囊检查胞体、器壁和实体碰撞，无偏 run/tumble 保存独立 RNG 和事件时钟；失败步骤整体回滚。浓度热图与耗尽材料显示由真实结果驱动。见[科学语义与限制](docs/science/spatial-baseline.md)、[版本化协议](docs/archive/legacy-protocols/spatial-profile.md)和[本阶段验收](docs/archive/verification/verification-spatial.md)。这一旧 profile 的 Python checkpoint 保留原合同；新科学 profile 的信号驱动运动、MCP 对照与动态状态另行版本化。Task 0.6 已接任务暂停续算；实验标定仍需真实数据。

**M4 状态保存与恢复：** 空间运行可保存为包含冻结项目和完整状态的 JSON 文件，在新进程继续计算。新增 `python -m friskoli_cad.checkpoint` 入口、原子文件保存、严格恢复校验；失败重试保持原有 RNG 与时钟，保存帧间隔不改变计算。使用方法及状态范围见 [checkpoint 文件](docs/checkpoint-files.md)，验证结果见 [M4 验收](docs/archive/verification/verification-m4.md)。任务服务现已通过 Task 0.6 接入暂停/续算，见[长时任务验证](docs/archive/verification/verification-system-tasks.md)。

**完整 N3：** `chemotaxis-spatial-v1`（Project 0.5 / Catalog、Task 0.4）登记 25 类模块，六套普通可编辑图涵盖 PTS A、PTS B、reduced MCP、无趋化对照、有限来源/材料及营养生命周期。信号实际控制 run/tumble，支持可选生长、表达、死亡与分裂，记录受阻状态及死亡规则。结果提供方向位移、区域占用、到达/驻留和多 seed 比较；科学 checkpoint 保存完整动态状态。见[执行合同](docs/archive/legacy-protocols/chemotaxis-profile.md)、[科学依据与来源差异](docs/science/n3-mechanisms.md)和[完整验收](docs/archive/verification/verification-n3-complete.md)。

三池输运按本轮产品决定退出必需项，接触降解直接释放到可溶养分场。MCP 采用经典 MWC/甲基化适应框架及明确的线性反馈近似。所有示例参数保持探索性标记，不宣称实验标定或逐轨迹复现原 A 三池模型。N4 已增加 Design 工作区：参数目标与约束、有限候选和固定对照、重复比较、原生 `.friskoli` 包与 HTML/CSV 报告。使用与后续边界见[设计工作流](docs/design-workflow.md)。

启动窗口现采用紧凑命令列表，与工作区共用控件风格；关闭、恢复、导入错误与迟到请求处理已修正。浏览器交互、四尺寸与中英文检查见[启动界面验收](docs/archive/verification/verification-startup-ui.md)，其中明确列出实体设备等未测项。

两份真实科学模型的首轮审查、确定缺陷修订与有出处的 Wiki 草稿已完成，阅读入口和独立源码仓库提交见[科学模型审查总览](docs/science/README.md)。N3 已迁入上述来源机制，并保存来源提交和文件指纹；源代码与迁入方程的差异有明确记录，参数标定与实验验证尚未交付。

## 交付原则

- 共用接口按功能命名。特定受体、蛋白或模型的名称属于具体实现、参数集或示例。
- 菌体模块与环境模块遵循同一套登记、连接和校验规则。
- 后端保存并更新科学状态；前端按稳定的菌体 ID 接收帧与事件，展示轨迹和模块状态。
- 每项进入交付仓库的实现都要有明确的输入输出、适用范围和可复现验证。

仓库目录随已验收的内容逐步建立，避免保留空壳和复制旧项目的未使用文件。
