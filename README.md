# Friskoli-CAD

Friskoli-CAD 是面向趋化工程菌设计的模块化仿真与可视化工具。空间场景提供初始条件；行为图用有类型、单位和时间语义的端口连接计算模块；运行结果保留单菌体身份与变化序列，供前端回放和检查。

**当前状态：交付仓库、0.1.0 连接协议、数值核心和本地结果回放工作区已建立；空间与行为图编辑尚未接入。** 数值核心支持两种环境实现、薄层与 3D、可只登记不计算的物种、外部输入日程、胶囊尺寸与薄层容纳检查、基础扩散、演示性的单菌体移动、逐帧伸长和长度 adder 分裂。旧结果帧可继续读取，新 `0.2.0` 结果帧记录逐菌体几何及分裂事件。前端可从真实项目生成帧并按 ID 和事件回放，默认英文，可在 Settings 切换中文；详见[本地结果回放](docs/replay-ui.md)。本仓库从空仓开始，旧版 WebUI、`friskoli-simulation` 和 `model-A-rebuilt` 只作为迁入时的参考。旧原型仍在各自原目录。

开发状态和每阶段验收见 [PROGRESS.md](PROGRESS.md)，后续顺序见[开发安排](docs/development-order.md)。系统边界见 [架构约定](docs/architecture.md)，已实现的连接规则见 [协议 0.1.0](docs/protocol-0.1.md)，执行顺序见 [图编译器](docs/engine-compiler.md)，第一个数值例子见 [无扩散摄取循环](docs/engine-runtime.md)，基础输运见[无通量扩散](docs/no-flux-diffusion.md)，移动与摄取时序见[移动菌体](docs/moving-cells.md)，项目格式与外部输入见[物种目录和输入日程](docs/project-schedule.md)，初始形状与容纳检查见[胶囊尺寸](docs/capsule-geometry.md)，逐帧几何与演示性生长见[生长帧](docs/growth-frames.md)，分裂与状态继承见[长度 adder 分裂](docs/adder-division.md)，本地服务与界面见[结果回放](docs/replay-ui.md)，两种环境的实际替换见 [环境模块对照](docs/environment-swap.md)，薄层与 3D 的选择见 [空间尺度](docs/spatial-resolution.md)，当前格距的局限见 [格点细化诊断](docs/grid-refinement.md)与[固定作用范围格距对照](docs/fixed-support-refinement.md)，换格时的物质守恒见 [浓度场重划](docs/conservative-regrid.md)，跨格采样与沉积见 [固定物理作用范围](docs/box-support.md)，状态归属与扩展规则见[状态归属说明](docs/state-ownership.md)，后续界面需求见 [前端用户历程笔记](docs/frontend-user-journey.md)，Git 与代码迁入规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 交付原则

- 共用接口按功能命名。特定受体、蛋白或模型的名称属于具体实现、参数集或示例。
- 菌体模块与环境模块遵循同一套登记、连接和校验规则。
- 后端保存并更新科学状态；前端按稳定的菌体 ID 接收帧与事件，展示轨迹和模块状态。
- 每项进入交付仓库的实现都要有明确的输入输出、适用范围和可复现验证。

仓库目录随已验收的内容逐步建立，避免保留空壳和复制旧项目的未使用文件。
