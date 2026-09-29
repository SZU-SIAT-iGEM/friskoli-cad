# Friskoli-CAD

Friskoli-CAD 是面向趋化工程菌设计的模块化仿真与可视化工具。空间场景提供初始条件；行为图用有类型、单位和时间语义的端口连接计算模块；运行结果保留单菌体身份与变化序列，供前端回放和检查。

**当前状态：交付仓库、0.1.0 连接协议、数值核心、可编辑工作区和本地结果回放已建立。** 前端现在可以从欢迎页新建、打开或恢复项目，在 Space 中创建菌群体积块、固定种子散布、移动/旋转/缩放、隐藏/锁定/复制和删除；在 Workflow 中拖动节点、连接端口、编辑参数并检查单位和时间语义；运行前通过后端校验，运行后保留不可变的结果记录、时间轴、单菌体检查、浓度数据和 JSON/CSV 导出。工作区格式为 `0.2.0`，协议、OpenAPI 与交互约定分别见[工作区协议](docs/workspace-protocol-0.2.md)、[OpenAPI](docs/openapi.json)和[交互规范](docs/interaction-specification.md)。旧版 `friskoli-cad-webui` 提供布局和功能迁移参考，未声明的旧对象类型不会直接进入当前协议。

开发状态和每阶段验收见 [PROGRESS.md](PROGRESS.md)，后续顺序见[开发安排](docs/development-order.md)，协议新增点和迁移规则见[协议扩展计划](docs/protocol-expansion-plan.md)。系统边界见 [架构约定](docs/architecture.md)，已实现的连接规则见 [协议 0.1.0](docs/protocol-0.1.md)，执行顺序见 [图编译器](docs/engine-compiler.md)，第一个数值例子见 [无扩散摄取循环](docs/engine-runtime.md)，基础输运见[无通量扩散](docs/no-flux-diffusion.md)，移动与摄取时序见[移动菌体](docs/moving-cells.md)，项目格式与外部输入见[物种目录和输入日程](docs/project-schedule.md)，初始形状与容纳检查见[胶囊尺寸](docs/capsule-geometry.md)，逐帧几何与演示性生长见[生长帧](docs/growth-frames.md)，分裂与状态继承见[长度 adder 分裂](docs/adder-division.md)，本地服务与界面见[结果回放](docs/replay-ui.md)，工作区和交互说明见[前端用户历程](docs/frontend-user-journey.md)，两种环境的实际替换见 [环境模块对照](docs/environment-swap.md)，薄层与 3D 的选择见 [空间尺度](docs/spatial-resolution.md)，当前格距的局限见 [格点细化诊断](docs/grid-refinement.md)与[固定作用范围格距对照](docs/fixed-support-refinement.md)，换格时的物质量守恒见 [浓度场重划](docs/conservative-regrid.md)，跨格采样与沉积见 [固定物理作用范围](docs/box-support.md)，状态归属与扩展规则见[状态归属说明](docs/state-ownership.md)，Git 与代码迁入规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 交付原则

- 共用接口按功能命名。特定受体、蛋白或模型的名称属于具体实现、参数集或示例。
- 菌体模块与环境模块遵循同一套登记、连接和校验规则。
- 后端保存并更新科学状态；前端按稳定的菌体 ID 接收帧与事件，展示轨迹和模块状态。
- 每项进入交付仓库的实现都要有明确的输入输出、适用范围和可复现验证。

仓库目录随已验收的内容逐步建立，避免保留空壳和复制旧项目的未使用文件。
