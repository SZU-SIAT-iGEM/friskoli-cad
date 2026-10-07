# PTS 摄取与甲基化适应

当前 A/B 共用 `signal.pts_methylation@1.0.0`。局部糖浓度先生成摄取请求，经共享库存结算后的实际通量驱动 EI；甲基化反馈调节感知复合体活性，再推进 CheY-P 和马达读出。模块没有浓度输入。

## 结构

PTS 不结合甲基受体。摄取通量改变酶 I 的磷酸化状态，去磷酸化的 EI 抑制感知复合体中的 CheA；CheR/CheB 的甲基化与之相反，把活性拉回基线，这就是适应。链上唯一的放大是鞭毛马达，其 Hill 系数是马达属性而非 PTS 属性。

    a = a0 - (e - 0) + gain_m * (m - m_min)
    dm/dt = adaptation_rate * (a0 - a)

两个耦合都不是自由增益，而是由各自输入的量程定出来的：`e` 按定义是 0 到 1 的分数，甲基化量程是 `methylation_min` 到 `methylation_max`，各自声明跨越完整的活性区间 `[0, 1]`。这正好让甲基化能抵消任意大小的 PTS 驱动，因此不存在"选一个放大倍数"这一步。

## 参数从哪来

| 量 | 来源 |
| --- | --- |
| `methylation_min` / `methylation_max` | 未甲基化受体 / MCP 甲基化位点数 |
| `tau_methylation_s` | 甲基化适应时间常数，4 s |
| `baseline_activity` | 适应态马达 CW 偏置，1/3 |
| `motor_hill` | 鞭毛马达 Hill 系数，10.3（Cluzel 等，2000） |
| `chey_total_uM` | 约 8000 CheY/细胞、约 1 fL 折算 |
| `ei_dephos_per_molecule` | 由"EI 在 PTS 摄取半饱和处恰好半去磷酸化"推出 |
| `adaptation_rate_s` | 由 `tau_methylation_s` 与甲基化增益推出 |
| `chey_phos_per_um_s`、`chey_dephos_s` | 由 CheY-P 响应时间 0.1 s 与基线处收支平衡推出 |
| `initial_ei_fraction`、`initial_methylation`、`initial_chey_p_um` | 环境浓度处的固定点 |
| `motor_half_um` | 判据 0：`bias(y0) == baseline_activity` |

初态不是任选值：`initial_*` 是细胞在起始环境浓度下已经适应到的固定点，`motor_half_um` 随之重新推出。否则每次运行都会先经历一段加载瞬态，而声明的基线活性只是名义值。

A/B 的容量公式分别为膜面积占用限制和参考面积缩放，其余信号与马达参数相同。甲基化是有上下限的连续状态，量程恰好覆盖完整 PTS 区间，因此满量程刺激仍能适应回基线而不被上下限钳住。MCP–MeAsp 保留独立的配体结合模型。

完整方程、初态、数值方法及参数见[第一版科学说明](../first-release/science.md)。通路依据为 [Neumann 等，2012](https://doi.org/10.1073/pnas.1205307109) 与 [Somavanshi 等，2016](https://doi.org/10.1073/pnas.1602145113)；PTS 到感知复合体的耦合强度本身仍缺少直接实验约束。

`tests/test_pts_science.py` 检查容量、摄取和来源记录；`tests/test_pts_methylation.py` 检查判据 0 与耦合推导、阶跃与撤除、冻结适应、满量程适应、独立 RK4 对照、步长收敛、参数范围及状态恢复。
