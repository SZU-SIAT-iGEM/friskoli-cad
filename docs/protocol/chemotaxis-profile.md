# 科学趋化执行合同

2026-10-01。执行规则 `chemotaxis-spatial-v1`，Project **0.5.0**、Catalog/Task/CompiledPlan **0.4.0**、Workspace **0.4.0**。旧三种 profile 的执行含义和旧 Schema 保留。科学依据与来源差异见[机制说明](../science/n3-mechanisms.md)，验收见[完整 N3](../verification-n3-complete.md)。

## 注册和编辑

Catalog 登记模块 ID/版本、名称、参数/端口/状态、数学式、来源、适用限制，以及六套可展开模板：PTS A、PTS B、MCP、无趋化对照、有限源与材料、营养生命周期。模板生成普通 Project 节点与边，后续编辑和计算使用同一编译器与执行器。`pts.capsule_area` 是本 profile 菌群初始化的数据提供者；障碍物、来源与底物继续使用已注册的对象声明。

`field.diffusive_local@2.0.0` 的梯度参数只生成初始线性浓度，中心值来自 Project species；初始流体体素浓度不得为负。以后由真实释放、扩散和接受摄取改变场。`field.ideal_local_reservoir` 则保持声明的均匀浓度，记录外部实际补给。物种登记本身不创建任何计算。

source/material 必须引用已存在的有限场；每个物种只有一个场 owner，每个菌群的运动与各类生理状态也只有一个 writer。底物需要唯一可执行的 `material.degradation` 提供者和显式表面酶。`reaction.contact_degradation` 与 `reaction.direct_bulk_hydrolysis` 可按角色替换；后者使用每个材料各自冻结的初始库存作为水解比例分母。不能只登记一个空角色让材料开始计算。

参数值、单位、来源和跨节点物种/owner/时序均需校验。模板值是可编辑的探索性设置，不能因为有默认值就标作实验标定。旧工作区读入后保留原执行规则；新保存格式显式容纳 Project 0.1–0.5。

## 时间和状态

数值步使用明确的 operator splitting：

1. 从已提交的步初几何和场读取浓度、容量与摄取请求。
2. 有限接触降解、有限来源释放、稳定扩散子步、共享摄取结算，必要时补给恒浓度储库。
3. 接受通量更新 PTS 信号；A/B memory 或 MCP 受体适应产生下一段 motor 状态。
4. **本段运动读取步初已提交 motor bias**。用单位指数剩余 hazard 推进变率事件，instant 与有限 dwell 各有命名规则；碰撞期间时钟仍推进。
5. 营养增长、总 copies 表达、健康和随机死亡、面积 adder 分裂逐项执行。增长/分裂需通过几何检查；受阻不制造死亡、不丢弃营养。
6. 验证候选几何、ID、数值和账；逐数值步更新观察器；一次性提交状态、RNG、事件、帧和时间。后期失败也不能留下部分更新。

总 copies 表达只减真实 turnover，体积增长不再额外删除 copies；分裂按体积分数分配一次。未用胞内养分、体积和酶 copies 分配到子代；几何派生量重算。死亡移除活菌碰撞体，将剩余可用养分记入显式 removed-residual 账，不声称完整碳/能量守恒或自动释放到外场。

`division.blocked` 表示达到面积阈值但当前空间、最小几何或数量预算不允许分裂。死亡由 `life.health_balance` 的具名规则产生，事件详情保留节点、策略、健康值、hazard、概率和随机抽样；组合规则不被展示成未经证实的单一“饿死”或“毒死”。

## 任务与结果

任务状态机沿用 N2。能力通过 `capabilities.task_profiles["chemotaxis-spatial-v1"]` 精确协商；接口见 [OpenAPI 0.4](tasks-openapi-v0.4.json)与 [Task Schema](../../src/friskoli_cad/protocol/schemas/task-v0.4.schema.json)。同步 replay 仍限制短运行；预检查与异步任务可检查最多 10000 步，具体预算由服务公布。

每个 FrameEnvelope 带 `metrics`、`object_states` 和 `lifecycle_details`；请求场时附 `concentrations`。Frame 0.2 本身不增加未知字段。稀疏保存汇集上次保存以来的生命周期事件与死因详情，分别保留序列号、原数值步号和事件时刻；超过输出预算明确失败，不能静默丢弃事件。

观察器独立于存帧频率：方向位移是存活初始 ID 的平均有符号位移；区域占用按所有当前活菌计算；到达和驻留以初始 cohort 为分母，子代不扩大分母。到达按已提交数值步边界判断，驻留采用步初矩形积分，不能解释为连续轨迹的精确首次穿越时间。空分母输出 `null`。指标定义随 Catalog 公布，完整运行比较保留冻结输入、观察区域、seed 与计算版本。

Checkpoint 文件 **0.2.0** 包装 `chemotaxis-checkpoint/v1`：保存动态实体、记忆、hazard/dwell、独立随机流、营养/酶/健康、来源/材料/场、库存账、ID/事件和观察器。文件恢复严格检查来源锁和状态一致性，不重新初始化。旧空间文件 0.1 继续按原规则读取。CLI 见[文件入口](../checkpoint-files.md)；服务级 pause/resume、环境迁移仍属 N7。

## 科学边界

PTS 示例中引诱物就是摄入养分，底物释放与直接来源进入同一个有限场。MCP 示例中 ligand 独立，营养由明确的均匀外部储库供给。MCP 使用 Tu、Shimizu、Berg 2008 的 MWC 与甲基化适应思路，并声明线性反馈近似；不冒称论文完整拟合模型。

按用户本轮调整，三池输运不列为 N3 必需机制，接触降解直接进入可溶外场。A/B 保留各自感知、运动和生理规则；不声称逐轨迹复现原 A 三池模型。碰撞是保守几何阻挡，部分降解材料保持原盒体边界；流体力、连续侵蚀、膜材料预算、实验标定和标准格式导出分别留在后续阶段。
