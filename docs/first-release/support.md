# 第一版支持范围与验收记录

本文件是实施中的当前范围。P0 于 2026-10-04 建立；验证结果由后续阶段补入。基线为包含任务书的 `9099d9e`，调查基线为 `3a5d3be`。独立分支为 `chore/unify-profile`，原 checkout 不参与修改。

## 唯一执行规范

`modular-spatial-v1`：prepare → field → physiology → lifecycle → observation → 原子提交。模块通过同一个 StepContext / ModuleProposal 合同提交提案；系统管理库存、几何、身份、共享结算、事件和 RNG。same_step / previous_step 由 graph 明确声明；芯片 motor → motion 使用 previous_step。

field 阶段依次结算来源、材料释放、外部交换和共享摄取，再推进有限体积扩散。观察在每个数值步的物理与身份事件完成后更新。输出帧频率不能影响科学状态或累计观测；失败步骤回滚全部状态。

| 数据与流程 | 第一版范围 |
| --- | --- |
| 执行 | 一个正式 registry、一个 ModularSimulation |
| 项目 / Graph / Module | Project 0.6 / Graph 0.2 / Module 0.2 |
| 目录 / 任务 / 帧 | Catalog 0.5 / Task 0.6 / Frame 0.2 |
| 工作区 | Workspace 0.7（编辑状态与科学输入分离） |
| 恢复 | 同一软件版本生成的 modular checkpoint；实现 hash 与 RNG 环境核对 |
| 研究 | MCP–MeAsp 芯片测量基准；中心材料 PTS A/B 研究近似与匹配无反馈对照 |
| 平台 | 普通生长、分裂、死亡、几何、扩散、数组交付和模块包合同 |
| 旧输入 | 旧 project/task/checkpoint 明确拒绝，不迁移、不静默重新解释 |

## 模块去向

逐项清单见 [module-disposition.json](module-disposition.json)。六项旧包装退役：ideal_local_reservoir 改用显式 reservoir/boundary exchange；surface_copies 第一版改用固定 enzyme copies；nutrient_monod / nutrient_yield 使用普通 modular 生长和库存；health_balance 使用明确死亡 hazard；area_adder 使用 modular 分裂合同。没有新增旧版本兼容路径。

普通模块与来源特定模块共享合同。A/B/MCP 的替换通过 graph 和参数完成；运行器不得根据模型、案例或 module ID 选择科学算法。

## 科学声明

工程主线是 PTS 代谢–趋化耦合与表面酶中心底物降解。聚集与累计降解分别报告，匹配对照只关闭感知到运动的反馈。负结果可接受。MCP 富集只用于野生型测量基准。

A 稳定版尚缺表面糖池、接触 QSSA、黏附/脱附反馈；B 使用明确记录的研究简化。视觉训练、实验标定和完整来源模型是另外的任务。所有参数记录实际出处，不宣称湿实验标定或完整复刻。代码和自有 A/B 采用 MIT，第三方依赖保留各自许可。

## 验收状态

G1–G9 按 [任务书](../profile-consolidation-plan.md) 逐项提供证据。当前仅完成 P0 范围、环境与模块清单；尚未将功能或验收标为完成。后续记录必须包含命令、实际结果和制品 hash；依赖缺失的 skip 不计入通过。UI 尺寸模拟、真实 UI 操作与实体设备实测分开报告。
