# N4 CAD 设计、评价与生物组合验收

日期：2026-10-01。分支 feat/n4-design，基于 N3 完整实现。此次节点把用户目标、参数扫描、可复现比较、结果判断和生物学机制组合接成一条可检查的流程。

## 已完成

- DesignBrief/Design 0.1.0 继续可读；0.2.0 增加结果硬/软阈值、最少重复次数和逐 seed 对照改善规则。
- 候选生成使用有限注册参数和离散值；保留每个候选的解释、排除原因、预算和固定对照，不推进科学数值步。
- 结果评价只读已保存的完整运行。候选和对照必须使用相同计划 seed、观测定义、执行设置、实现版本锁和端点；缺失、失败、partial、重复 seed、空指标或版本不一致不会生成推荐。
- 硬阈值要求每一次有效重复都通过；软阈值逐项展示；主目标必须有唯一最佳候选；配对改善必须对每一个 seed 超过声明的最小值。评价结果明确为探索性描述，不代表统计显著性、实验性能或已标定参数。
- biological assembly 已实现。part 保存一个菌群及其所属 workflow 机制分支；chassis 另外保存初始几何。assembly 冻结模块版本、物种需求、外部环境端口、来源和生物学作用。应用时保留目标菌群位置、姿态、ID 和其他菌群；不兼容 provider、物种、profile 或 graph 依赖会拒绝。
- Workspace 0.6 保存 design 和 brief，继续读取旧 Workspace 0.1–0.5。HTTP 新增结果评价、assembly 提取和应用接口；这些接口只编辑数据，不隐式启动求解任务。
- 原生设计包的结构化证据、依赖和冻结输入继续逐字节交叉校验；报告文件按包内 checksum 保留历史版本，报告渲染器升级不会拒绝旧的合法包。

## 自动化验证

- N4 Python 专项：68 passed，包含候选生成、原生包、旧包回读、结果评价、assembly 组合和 HTTP 提取/应用；唯一 warning 是拒绝测试故意写入重复 ZIP 成员。
- JavaScript N3/N4 回归：20 passed；Workspace 0.6、结果评价表单、assembly 元数据和现有任务/空间行为均通过。
- JavaScript 全量回归：99 passed，包含修正旧 Workspace 版本断言后的完整 tests/*.test.mjs。
- Python 全量回归：640 passed，186 subtests passed；本轮没有修改科学数值内核。
- 浏览器实体设备触控仍未测；尺寸模拟和交互逻辑测试与实体设备验收分开。

## 当前边界

- assembly 目前是数据包和应用接口，不是可安装的全局 registry；更多经过审查的生物组合、跨 profile 安装锁和插件分发安排在后续。
- 结果评价没有置信区间、统计检验或 Pareto 多目标排序；多单位指标不合并为一个分数。
- 大型设计的选择性重跑、部分候选重跑和导入历史管理仍待补充。
- SBOL、SBML、OMEX 标准子集、静态 Wiki 发布、服务级 pause/resume、实验参数标定和 GPU 性能扩展仍属于 N5–N8。

## 复现入口

代码入口：src/friskoli_cad/design.py、design_evaluation.py、biological_assemblies.py、design_delivery.py、replay_service.py；前端入口：src/friskoli_cad/web/design-panel.mjs、app.mjs、workspace.mjs。
测试入口：tests/test_design_evaluation.py、tests/test_biological_assemblies.py、tests/test_design_delivery.py、tests/test_design_http.py、tests/n4-ui.test.mjs。
