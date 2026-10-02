# 科学模型审查与迁入

**使用入口：** [模块使用指南](../module-guide.md)按完整案例、可调参数和模块目录介绍当前28个模块 ID（MCP、面积分裂各含两个兼容版本）。新增通用饱和摄取、营养储备与持续匮乏生存，以及五套完整 `foundation-*` 组合，详见[基础过程与趋化机制的组合](foundation-composition.md)。这些新生存规则和数值均为 constructed phenomenological 假设，未标定真实菌株；旧最小机制与来源生命周期图保持原语义。沿用原B生物参数的128µm中心源普通图、随机分裂v2与通用径向观测见 [N5 B 生命周期](n5-b-lifecycle.md)。

更新：2026-10-01。两份来源模型已完成首轮代码/数学审查、确定缺陷修订、有限规模回归和独立复核。完整来源修订保存在相邻的 `friskoli-model-review` 仓库，原始目录与 ZIP 保留。CAD 的 `chemotaxis-spatial-v1` 现已注册 A 浓度记忆、B CheY-P 记忆、MCP 受体适应、信号驱动运动，以及 direct-bulk 水解、营养生长、总 copy 表达、health/death 和面积 adder。实现范围与实际通过的检查分开说明。

当前入口：[N3 机制、单位与来源差异](n3-mechanisms.md)、[N3 中文 Wiki](../wiki/models/n3-chemotaxis.md)、[完整 N3 验收](../verification-n3-complete.md)。最小 PTS 链的历史入口仍可查阅：[PTS 方程与参数证据](pts-minimal.md)、[共享库存结算](settlement.md)、[bulk 执行协议](../protocol/pts-bulk-profile.md)及[早期验收](../verification-n3.md)。运行所需选定来源文件锁、证据和 fixtures 在包内，运行不依赖相邻模型目录。

[多 seed、时间步和网格比较](n3-comparison.md)记录完整图构造研究，包括没有显示明确群体增益的结果，不把单次轨迹或只读信号响应当作工程菌趋化验证。

本轮按明确选择使用 direct-bulk：接触水解产物进入同一个可溶场，不迁入 A 的 contact/surface/bulk 三池。因此 A 的传质假设已替换，当前组合不能称为原 A 的完整复刻。B 的活跃直接释放路径也经过 CAD 的接触、材料几何、事件定位与数值分拆适配。MCP 使用经典 MWC 结构和显式简化的 activity 反馈，参数没有本构建体实验标定。

已有[空间无偏基线](spatial-baseline.md)保留独立执行语义；[checkpoint 文件入口](../checkpoint-files.md)及[M4 历史验收](../verification-m4.md)继续描述相应版本。新增趋化/生理状态的恢复与回滚检查见完整 N3 验收。支持数值状态恢复不等于服务级通用暂停续算。

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

主线按[路线图](../design/roadmap-and-acceptance.md)推进。[迁入计划](migration-plan.md)保留历史来源审查与 M0–M7 依赖关系，并在顶部标明当前状态。早期“仅 M0–M3、暂不启动 M5–M7”的阶段限制已结束，不能作为缩减完整 N3 的依据。

当前执行配置为 Project 0.5 / Catalog、Task、Plan 0.4 的 `chemotaxis-spatial-v1`；旧 `conservative-pts-bulk-v1`、空间基线和 `legacy-explicit-v1` 仍有各自的运行语义。N3 的对照、来源、物质账与生命周期检查完成后，后续设计搜索、发布、通用服务续算和实验拟合按各自验收开展。
