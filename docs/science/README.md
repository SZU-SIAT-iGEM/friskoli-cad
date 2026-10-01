# 科学模型审查与迁入

更新：2026-10-01。两份来源模型已完成首轮代码/数学审查、确定缺陷修订、有限规模回归和独立复核。完整源码修订保存在相邻的 `friskoli-model-review` 仓库，原始目录与 ZIP 保留。CAD 已实现 M0–M3 最小 PTS 计算链、M4 状态恢复与空间无偏基线；两套完整模型尚未全部迁入。

新增实现入口：[科学方程与参数证据](pts-minimal.md)、[共享库存结算](settlement.md)、[执行协议](../protocol/pts-bulk-profile.md)、[中文 Wiki](../wiki/models/pts-minimal.md)与[实际验收](../verification-n3.md)。CAD 包内保存选定来源文件的提交和 SHA、证据与固定条件 fixtures，运行不依赖外部模型目录。

后续空间基线见[底物、养分场、接触降解与运动](spatial-baseline.md)：采用独立执行规则，包含注册实体、表面酶限速释放、局部守恒场及无偏随机运动。它没有完成完整 A/B 模型重现或 MCP 受体链。

M4 的 [checkpoint 文件入口](../checkpoint-files.md) 和 [验收](../verification-m4.md) 已补齐；下一步按迁入计划完成 M5 信号—运动及 PTS/MCP/无趋化对照。数值复现不代替科学标定。

## 来源、修订与阅读入口

| 模型 | 原始基线提交 | 修订提交 | 验证 | 文档 |
| --- | --- | --- | --- | --- |
| rebuilt-v2 | `8b8bb76`，27 份原始文件 | `e2093f8` | 原 5 项 + 新 30 项 = 35 passed | [源代码与数学审查](source-models/rebuilt-v2.md) · [中文 Wiki 草稿](../wiki/models/rebuilt-v2.md) |
| simplified-v4 | `98b12ff`，52 份原始文本及来源清单 | `6b6180b` | 选定原 103 项 + 新 26 项 = 129 passed；新增 26 项真实 Numba JIT 复测通过 | [源代码与数学审查](source-models/simplified-v4.md) · [中文 Wiki 草稿](../wiki/models/simplified-v4.md) |

表中提交均属于独立修订仓库。两套测试分别在各自模型的导入环境运行；26 项 JIT 复测与前述 129 项有重叠，不能再相加称为独立测试总数。原来源 SHA、所选文件范围、工具版本和复现步骤见各审查页及副本内的来源记录。

rebuilt-v2 来源为 `D:/Wu Shangru/Documents/iGEM/model-A-rebuilt-v2`；simplified-v4 来源为 `D:/Wu Shangru/Documents/工作区/model.v4_simplified_v1_3(1).zip`。二者在水解空间、感知适应、运动时间和分裂机制上有实质差异，不能将其中一份称作另一份的纯加速版本。

## 本轮处理的确定问题

- rebuilt-v2：死亡后仍更新代谢、反射未翻转朝向、FFT 截零增质、分段 run 重置 RNG、非法参数与零纤维、实际增长率和总蛋白 copies 的含义。
- simplified-v4：PTS 信号未读接受摄取、FD 未推进完整时长、FFT 数值策略、持续源与全死场路径、算力上限伪装死亡、copies 政策、死亡残余账、运行时域、分裂记忆与数组同步。
- 每项区分原反例、修正后的计算语义及测试范围；不能只看总量守恒就认定局部误差、空间收敛或机制正确。离散扩散策略有变化，旧输出不能直接标为修订输出。

## 引文与科学边界

两份 Wiki 草稿包含原始论文/官方来源、DOI 或原文链接，并在相关论述处引用。来源支持机制、量纲或特定实验条件时，不将其扩展为本构建体所有默认参数的依据。摘要核查与全文核查分别标明；情景值、未标定参数和待验证假设保留标识。

本轮没有完成实验复现、构建体参数标定、长期/多种子/网格全面收敛，也没有补成完整 MCP、真实固体接触、完整碳能量账或微重力模型。代码/数值回归通过不等同于这些科学验收通过。Wiki 当前是可审阅的模型说明草稿，未发布为已验证科学成果。

## 接下来的工程顺序

主线按[路线图](../design/roadmap-and-acceptance.md)推进。N2 已完成，N3 使用独立 `conservative-pts-bulk-v1` 执行规则迁入第一段科学计算；旧 `legacy-explicit-v1` 保持原行为。

[迁入计划](migration-plan.md)给出两模型差异、类型/单位/时序与 M0–M7 的验收。M0–M3 已实现有限 bulk → PTS 请求 → 共享库存结算 → 胞内累计与接受通量驱动 EI/CheA/CheY，motor bias 仅为读数。下一步是 M4 随机流、可保存状态与失败回滚，再接 M5 运动及无趋化对照；纤维、增长、死亡和分裂按后续依赖逐项接入。M4 的状态准备不等于 N7 已支持通用暂停续算。
