# 第一版实现结构

当前执行规范只有 modular-spatial-v1。一个正式 ModuleRegistry 汇集普通模块与来源特定模块，一个 ModularSimulation 执行同一合同。没有按 A/B/MCP、案例名或旧 profile 选择运行器的分支。当前协议及验收见[支持矩阵](first-release/support.md)。

engine/core.py 提供 World、CellGroup、Geometry 和错误；module_api.py 定义冻结 StepContext、ModuleProposal 与 Effects；compiler/planner 检查端口、单位、owner、阶段及时间边。模块声明在科学适配器、基础扩展和标准模块中注册。

modular_runtime.py 管理事务、共享场结算、几何约束、生长与身份事件。prepare → field → physiology → lifecycle → observation 后统一验证并提交。失败时 RNG、库存、ledger、身份和观察历史一并回滚。详细运行图见[系统执行](system-execution.md)。

TaskService 负责准入、幂等、持久任务和发布；独立 worker 检查源文件/实现锁后求解。checkpoint 保存模块状态、完整场、几何、RNG、身份和观测。元数据与二进制数组分开交付，shape/unit/hash 由读者核对。

浏览器工作区将科学项目与相机、选择、布局分开保存。Design、单次任务、按需回放、原生结果包和静态 Wiki 共享冻结输入和指标定义。旧格式明确拒绝，同版本保存与恢复是本版合同。
