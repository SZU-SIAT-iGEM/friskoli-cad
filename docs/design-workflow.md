# 参数设计与比较

Design 直接生成 modular 项目和 Task 0.6 提交。DesignBrief/Design 0.1、0.2、0.3 均为仍使用的内部数据合同；Workspace 0.7 保存设计草稿。旧项目和旧任务不读取或转换。当前能力和验收见[支持矩阵](first-release/support.md)。

1. 载入当前案例，在 Space / Workflow 编辑场景、参数和观测区域。顶部设置数值 dt 和 steps。
2. 在 Design 填设计名称、底盘依据、目标群体、指标方向、枚举参数及独立 seeds。
3. Generate candidates 检查参数、graph 和预算。非法候选记录排除原因。生成阶段不推进数值步。
4. 选择候选/对照、seed 和批量大小，继续缺失或失败的运行。成功记录不重复提交，失败历史保留。取消不能伪造完整结果。
5. Compare completed runs 读取完整运行，以 run 为重复单位。报告均值、样本 SD 和缺失值；每个细胞不算独立重复。Evaluate results 是描述性配对规则，不能解释为实验显著性或性能保证。
6. Export .friskoli 保存冻结设计、完整提交、所有交付帧/指标及请求的数组。Import .friskoli 重开并校验 hash。CSV 的输入摘要指向包内完整输入，避免在单个 CSV 单元格复制大数组。

通用 Design 的 fixed-bias control 明确使用 bias=0.5。中心 A/B 研究使用各自预设的匹配对照，两者不可混称；其差异见[科学范围](first-release/science.md)。静态生成通过不保证运行完成，任务仍检查实际 RAM、输出、时间及 cell cap。

扫描最多 16 个执行分支、8 个 seed、总计 32 次运行。输出帧频率只改变存储，观察器在每个数值步更新。完整输入、版本锁和 seed 冻结后，草稿修改不改变历史。
