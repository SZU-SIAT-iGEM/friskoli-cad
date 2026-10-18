# 文献与当前 PTS 模型的对应 / Literature-to-model map

标记用于区分实验依据、数学约化和本项目中的待验证内容。代码位置指向当前实现。A/B/C 脚本直接调用这些方程，使用维持浓度的外部储库。

| 文献与位置 | 对应内容 | 当前模型 | 标记 |
| --- | --- | --- | --- |
| [Somavanshi 2016，摘要及 overall influx 小节](https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.2000074) | PTS 报告糖总流入 | 输入为实际接受的摄取通量 | 来自文献 |
| [Somavanshi 2016，linear propagation 小节](https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.2000074) | PTS 剂量响应向趋化通路近似线性传播 | 保留 PTS 输入不额外放大的机制选择 | 来自文献 |
| 同上 | 完整磷酸转移网络与传递函数 | 一个 EI 分数及线性的 `complex_activity` | 简化 |
| [Neumann 2012，摘要](https://www.pnas.org/content/pnas/109/30/12159.full.pdf) | PTS 与受体输入共用下游甲基化适应 | `advance` 的 methylation 反馈 | 来自文献 |
| 同上 | 甲基化及 CheY-P 动力学 | 标量反馈与一阶磷酸化周期 | 简化 |
| 两篇文献与本项目的连接 | 工程菌的 cellobiose 参数、耦合幅度与时间常数 | 构造值及区间采样，尚无本项目实测拟合 | 未验证 |

| Source | Model correspondence | Status |
| --- | --- | --- |
| Somavanshi 2016, abstract/overall influx | Accepted uptake drives the signal | From literature |
| Somavanshi 2016, linear propagation | No additional amplification of the PTS input | From literature |
| Somavanshi 2016 | One EI fraction and a linear activity law replace the network | Simplification |
| Neumann 2012, abstract | Downstream methylation supplies adaptation | From literature |
| Neumann 2012 | Scalar feedback and a first-order CheY cycle | Simplification |
| Application to this project | Engineered cellobiose sensing and numerical constants await measurements | Unvalidated |

代码：`science/pts.py` 的 `pts_request`，`science/pts_methylation.py` 的 `complex_activity`、`advance`，以及 `engine/science_adapters.py` 中的实际摄取连接。表中的“来自文献”指机制方向；实现采用的精确函数、工作点和参数仍按“简化”或“假设”记录。

当前 `ei_fraction` 在方程中表示 **去磷酸化 EI 的分数**。motor bias 表示模型的马达顺时针偏置；稳态灵敏度单独计算为 `d(bias)/d ln C`，小阶跃瞬态增益为 `Δbias/Δln C`。两者分别报告。马达 Hill 系数 10.3 取自 [Cluzel 2000 的单马达研究](https://cluzel.fas.harvard.edu/sites/g/files/omnuum7156/files/cluzel/files/cluzel_science_00.pdf)，当前工作点由模型约束导出。

Cluzel 的 Hill 拟合集中在 bias 约 0.1–0.9 的区间。当前脚本在全范围使用同一 Hill 近似，这一延伸属于模型简化。

## MCP 对照的用途

早期受体改造方案讨论工程化识别；后期天然 MCP–天冬氨酸测量用于运动与测量流程的参照。两类工作在图注中分别注明受体、配体、菌株和用途。当前芯片预设模拟 **MCP–MeAsp**，参数表 `science/data/chip_parameters.json` 记录其构造设置。用于 L-aspartate 实验比较时，需先提供相应配体参数、几何和边界；现有 MeAsp 模拟不自动改名为 L-aspartate 实测结果。

Early receptor engineering and the later native-MCP/aspartate measurement have separate purposes. The current software chip benchmark uses MeAsp. Captions identify the actual ligand, receptor, strain, parameter source and measurement role.

## 低对流条件

溶质 Péclet 数 `Pe = U L / D` 比较流动携带与分子扩散，见 [COMSOL 的物料衡算推导](https://www.comsol.com/books/elements-of-chemical-reaction-engineering-5th/models)。这里的 U 为液体流速，细菌游动速度另行记录。采用模型构造值 `D = 10 µm²/s`、材料尺度 `L = 16 µm` 时，`Pe ≤ 0.1` 对应 `U ≤ 0.0625 µm/s`；采用 `L = 64 µm` 时对应 `U ≤ 0.015625 µm/s`。这是条件估算。实验中的残余流速需由测量给出，才能判断装置是否位于该范围。

## 图注的共同信息

每张图记录参数来源（文献、实测、假设）、代表细胞或菌群数量、运动随机种子数，以及参数抽样数和抽样 seed。A/B/C 的确定性信号计算使用一个代表细胞、零个运动 seed；C 的区间来自参数抽样。空间对照另外核对源强、几何、边界、初始菌群与物质库存。
