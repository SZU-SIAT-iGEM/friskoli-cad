# N4 参数设计与比较

更新：2026-10-01。N4 现已覆盖第一段设计流程和第二段生物组合/结果评价：以 N3 科学项目为共同基线，填写目标、参数约束和结果阈值，枚举候选、运行重复、比较、评价并保存原生设计包。底盘表示菌群的基础参数与几何；元件表示可装入的完整菌群 workflow 机制分支，外部环境提供者、物种和模块版本会在导出/应用时检查。

## 操作

1. 在启动页选择任意 N3 趋化模板，先在 Space / Workflow 调整场景、机制图和观测区域。顶部设置步长及步数。
2. 打开 **Design**，填写设计名称、底盘及依据，选择目标菌群、指标和最大化/最小化方向。目标采用数值内核实际记录的位移、区域占比、到达比例或驻留时间。
3. 从已注册的数值参数中选择扫描项，填写有限枚举值及不重复的 seed。可以添加多个参数和参数硬/软约束。硬约束排除方案；软约束计算单独显示的惩罚，不与不同单位的目标指标相加。
4. 点击 **Generate candidates**，检查每项修改、排除理由和运行预算。候选保留同一场景与观测；固定偏置对照基于未扫描原项目，只把运动的信号输入替换为 `signal.constant_bias`，`bias=0.5` 与 N3 对照模板一致。原信号读数保留，运动不再读取这些信号。
5. 点击 **Run all candidates × seeds**，按候选和 seed 顺序提交真实异步任务。可停止后续运行并取消活动任务；已发布结果保留。失败不会伪造完成，也不会自动继续后续分支。
6. 使用 **Compare completed runs** 查看完整记录；HTML/CSV 报告列出目标、候选、重复数、均值、样本 SD、来源版本和排除原因。一次重复的 SD 为缺失；未到达比例为真实零，缺失指标或空分母保持缺失。
7. 用 **Export .friskoli** 保存完整设计；在 Design 中用 **Import .friskoli** 重开。普通 Save 仍保存工作区 JSON。打开候选图可继续高级编辑，改动后需重新生成设计。
8. 在 Design 页的 Biological chassis / component assembly 区导出或应用 assembly。part 保留目标菌群的空间位置和几何，替换其完整机制分支；chassis 还可以提供初始几何。环境节点不会被隐式复制，兼容性不满足时请求失败。
9. 对 0.2 brief 点击 Evaluate results。只有计划中的 seed 全部完成、版本和观测一致、硬阈值每次都通过且每次相对固定对照都达到最小改善时，才显示探索性推荐；并列、空值、失败、partial 或证据不足会显示无推荐。

## 合同与计算边界

- DesignBrief/Design 0.1.0 继续读取；0.2.0 增加结果阈值与选择规则。Workspace 0.6.0 保存 design、brief 和 assembly 相关草稿，继续读取旧 0.1–0.5。Project、Task、数值时序不变。
- 最多 16 个执行分支（包含对照），最多 8 个独立 seed，总计不超过 32 次运行。超预算拒绝，不静默截断。没有可行候选时返回空结果和原因；少于两项可行候选不会标为可推荐。
- 每个候选经过注册参数、项目与编译初始化检查；生成阶段不推进数值步。运行预算包含预计峰值内存和总输出量，沿用 TaskService 的保守估算，不承诺实际耗时。
- 设计及每次提交各自冻结。运行记录通过 `design_ref` 关联设计和候选，修改草稿不回写历史。新设计避免复用已有运行的 ID；比较清除其他设计的旧选择。
- 报告按实际版本锁分组，不合并不同来源。失败、partial、重复 seed、观测或输入不匹配的记录保留并注明未计入统计的原因。
- 当前结果评价是描述性的配对 seed 规则，不是统计显著性检验或实验性能预测；软阈值单独展示，不把不同单位混成分数。多目标 Pareto 排序、跨 profile 的 assembly 安装目录和大型设计的选择性重跑仍是后续工作。

## 原生设计包

`.friskoli` 是 ZIP 数据容器，当前布局为：

```text
manifest.json
 design/package.json          # 完整无损payload：设计、工作区、运行、目录
 design/brief.json
 design/candidates.json
 evidence/runs.json           # 冻结输入、结果、事件及来源
 dependencies/registry.json
 dependencies/locks.json
 reports/design.html
 reports/design.csv
```

manifest 记录文件长度和 SHA-256。导入检查校验和、重复路径、路径越界、成员类型、展开大小和跨引用；不自动执行模型或安装插件。HTML 转义输入，CSV 防止文本被解释为公式。压缩包最大 64 MiB，单成员 64 MiB，总展开 128 MiB；HTTP JSON 请求上限 64 MiB，因此浏览器 base64 导入的实际压缩包上限更小，超出明确拒绝。

运行的完整冻结输入和版本锁均保留，重新计算仍需兼容的代码与依赖。导入结果为只读历史；页面刷新后需要重新打开原生包才能恢复这些导入结果。本机自己运行的任务仍由 TaskStore/服务保存。清理历史需要先导出原生包并显式操作，活动任务不能清理。

SBOL、SBML 和 OMEX 的真实转换与损失说明按 N5 实施。完整目标包目录见[交付设计](design/delivery-and-migration.md)，当前简化布局不丢弃payload信息。

实现：`design.py` 负责有限枚举，`design_delivery.py` 负责数据包和报告，`web/design-panel.mjs` 负责表单与候选界面，`app.mjs` 复用原任务批次。对应验收见[本轮记录](verification-n4-design.md)。
