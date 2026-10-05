# 科学模型与来源

当前执行规范为 `modular-spatial-v1`。A/B 使用同一套 PTS 甲基化反馈和马达响应，容量公式分别保留膜面积占用限制与参考面积缩放。MCP–MeAsp 是独立的受体测量基准。

当前方程与参数见[第一版科学说明](../first-release/science.md)，模块入口见[使用指南](../module-guide.md)和[PTS 说明](pts-minimal.md)。运行验证与未完成事项见[验收记录](../first-release/acceptance.md)。

以下来源修订记录及 N3 比较描述原模型和早期实现，其输出不代表当前甲基化模型。

## 来源、修订与阅读入口

| 模型 | 原始基线提交 | 修订提交 | 验证 | 文档 |
| --- | --- | --- | --- | --- |
| rebuilt-v2 | `8b8bb76`，27 份原始文件 | `e2093f8` | 原 5 项 + 新 30 项 = 35 passed | [源代码与数学审查](source-models/rebuilt-v2.md) · [中文 Wiki 草稿](../wiki/models/rebuilt-v2.md) |
| simplified-v4 | `98b12ff`，52 份原始文本及来源清单 | `6b6180b` | 选定原 103 项 + 新 26 项 = 129 passed；新增 26 项真实 Numba JIT 复测通过 | [源代码与数学审查](source-models/simplified-v4.md) · [中文 Wiki 草稿](../wiki/models/simplified-v4.md) |

表中提交均属于独立修订仓库。两套测试分别在各自模型的导入环境运行；26 项 JIT 复测与前述 129 项有重叠，不能再相加称为独立测试总数。原来源 SHA、所选文件范围、工具版本和复现步骤见各审查页及副本内的来源记录。

rebuilt-v2 来源为 `D:/Wu Shangru/Documents/iGEM/model-A-rebuilt-v2`；simplified-v4 来源为 `D:/Wu Shangru/Documents/工作区/model.v4_simplified_v1_3(1).zip`。二者在水解空间、感知适应、运动时间和分裂机制上有实质差异，不能将其中一份称作另一份的纯加速版本。

## 2026-09-29 来源修订处理的确定问题

- rebuilt-v2：死亡后仍更新代谢、反射未翻转朝向、FFT 截零增质、分段 run 重置 RNG、非法参数与零纤维、实际增长率和总蛋白 copies 的含义。
- simplified-v4：PTS 信号未读接受摄取、FD 未推进完整时长、FFT 数值策略、持续源与全死场路径、算力上限伪装死亡、copies 政策、死亡残余账、运行时域、分裂记忆与数组同步。
- 每项区分原反例、修正后的计算语义及测试范围；不能只看总量守恒就认定局部误差、空间收敛或机制正确。离散扩散策略有变化，旧输出不能直接标为修订输出。

## 引文与科学边界

两份 Wiki 草稿包含原始论文/官方来源、DOI 或原文链接，并在相关论述处引用。来源支持机制、量纲或特定实验条件时，不将其扩展为本构建体所有默认参数的依据。摘要核查与全文核查分别标明；情景值、未标定参数和待验证假设保留标识。

当前没有完成本构建体实验复现或参数标定。多 seed、dt 和网格比较是有限的构造数值研究，不代表长期或全范围收敛；MCP 是降阶模型，固体接触是几何近似，物质账是 cellobiose-equivalent，均没有扩展为完整分子通路、接触力学、碳能量账或微重力预测。具体已执行数量、结果和不足以验收记录为准。Wiki 是可审阅的科学说明，未发布为已验证的工程成果。

## 接下来的工程顺序

主线按[路线图](../archive/design/roadmap-and-acceptance.md)推进。[迁入计划](../archive/planning/science-migration-plan.md)保留历史来源审查与 M0–M7 依赖关系，并在顶部标明当前状态。早期“仅 M0–M3、暂不启动 M5–M7”的阶段限制已结束，不能作为缩减完整 N3 的依据。

N3 阶段使用 Project 0.5 / Catalog、Task、Plan 0.4 的 `chemotaxis-spatial-v1`；这些配置及其他旧 profile 保留在历史记录中。当前仅执行 `modular-spatial-v1`，旧输入会被拒绝。设计搜索、发布、服务续算和实验拟合按当前[支持范围](../first-release/support.md)与各自验收开展。
