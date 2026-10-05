# 第一版科学范围、方程和参数

本版研究输入均为探索性设定。数值测试验证实现与明确的数学模型相符；尚无湿实验参数拟合、轨迹标定或工程菌收益的实验验证。

## 公共执行与单位

调度为 prepare → field → physiology → lifecycle → observation → 原子提交。场浓度单位 uM，位置与长度 um，时间 s，库存与摄取量 molecule。一个体素的换算因子为 `602.214076 × dx × dy × dz` molecule/uM，使用三维体素体积，薄层也包含实际厚度。

field 阶段先结算材料释放、显式来源、外部交换和共享摄取，再进行有限体积扩散。模块返回提案；系统负责扣除、几何约束、身份事件和提交。same_step / previous_step 边是模型的一部分。芯片的 motor → motion 明确为 previous_step；不把信号提前用于本步运动。

初始场与采样位置来自 graph 的初始化声明。中心案例和芯片在 prepare 阶段采样上一完成步的场及位置，field 阶段结算本步请求，physiology 阶段更新信号及运动。观察器在本步身份事件结束后更新。

## 方程与数值方法

| 机制 | 本版关系与单位 | 数值方法与适用范围 |
| --- | --- | --- |
| 体素扩散 | `dC/dt = D∇²C`，D: um²/s | 面通量成对加减；封闭外壁和障碍物面无通量；显式稳定性约束决定内部子步 |
| 边界交换 | `C' = Ctarget + (C-Ctarget)exp(-k dt)`，k: 1/s | 只在声明的边界区域作用；实际场增减记入 external_net；有限速率，不能称严格恒浓度 |
| 共享摄取 | `request = copies × turnover × C/(K+C) × dt` | 按体素库存共享；accepted 由可表示的场扣除限制，不能以请求替代 accepted |
| 有限来源与材料 | 来源/底物库存扣除，等量溶解产物进入场 | 微小库存或场信用无法表示时保留未转移物质；物质误差采用 transfer-relative 检查 |
| A 功能载体 | `min(g × reference_copies, area × (1-basal_inner) × max_fraction / footprint)` | 固定膜占用近似；面积由胶囊几何计算 |
| B 功能载体 | `min(g × reference_copies, area / reference_area × reference_copies × gcap)` | 参考面积缩放近似，与 A 的膜占用方程分别声明 |
| PTS EI | `de/dt = alpha × accepted_flux × (1-e) - beta × e` | 本步 accepted_flux 固定；精确一阶松弛；e 是去磷酸化比例 |
| PTS 感知复合体 | `a = sigmoid(logit(a0) - g e + epsilon_m (m-m0))`；`A = Atotal × a` | PTS 输入改变复合体活性，不读取配体浓度 |
| PTS 甲基化适应 | `dm/dt = km(a0-a)`，`0 ≤ m ≤ mmax` | 有界 Backward Euler；边界处向外的变化率为零；EI → methylation → CheY 顺序分裂，一阶精度 |
| PTS CheY | `dy/dt = kp A (Ytotal-y)-kd y` | 固定本步末 CheA 做指数推进；A/B 共用 Hill 马达读出 |
| MCP MWC | `a=1/(1+exp(F))`；`F=N[epsilon(mref-m)+log((1+L/Ki)/(1+L/Ka))]` | 准平衡受体，L: uM；m 是约化的无量纲坐标 |
| MCP 适应 | `dm/dt=k(a0-a)` | Backward Euler，64 次有界二分求隐式标量方程；这不是原论文中完整拟合的适应律 |
| motor/hazard | `bias=Y^h/(Kmotor^h+Y^h)`；`lambda=lambda_min+(lambda_max-lambda_min)bias` | 单 motor surrogate；连续时间 hazard 的事件时刻，按 stable cell ID 划分 RNG 流 |
| 运动/墙面 | `dx/dt=speed×heading` | 胶囊几何约束、连续路径检查、沿壁滑动；未包含流体动力学和真实壁面相互作用 |
| 固定表面酶 | 接触范围内的有效 copies × kcat × dt，受材料库存限制 | 不模拟表达负担、酶折叠或表面糖池；降解贡献按菌群记账 |
| 平台生命周期 | 注册模块提出生长、死亡、分裂；系统修改几何、库存及 stable identity | 平台演示能力；中心研究第一阶段没有来源特定生命周期 |

独立对照见 `tests/test_first_release_science.py`、`test_dense_field_execution.py`、`test_diffusion.py`、`test_pts_science.py`、`test_pts_methylation.py` 及保留的模块、恢复、几何与生命周期测试。数学核对不意味着参数已标定。Catalog 的 tested 只表示列出的数值检查；unreviewed 条目保留探索性声明，不算科学验证完成。

## 中心材料工程研究

小域为 64×64×32 um，中域为 128×128×64 um，XY 均为正方形。中心 PTS 预设网格为 1 um、dt=0.1 s。中心材料边长为 8 / 16 um，初始库存 1e8 molecule equivalent。48 个初始细胞位于三层环形初态，方向由 seed 生成；长 2 um，直径 0.8 um，速度 20 um/s。

固定表面酶每细胞 1000 copies，kcat=0.5/s，接触范围 0.5 um。初始溶解糖是显式 Gaussian 场，peak=1 uM，sigma=8 / 16 um；材料内部为零。这部分是初始场库存，不计作材料降解。D=10 um²/s，reserve=1e6 molecule/cell，maintenance=10 molecule/s/cell。全部为构造值，完整输入保存在各 run 的 project 中。

A/B 共享环境、摄取律、初态、PTS 甲基化信号及马达参数；差异保留在容量公式。纯 PTS 链只有 accepted_flux 输入，关闭摄取后没有浓度旁路。默认 a0=0.5、g=2、epsilon_m=1、m0=2、mmax=4、km=1/s；初始 EI 去磷酸化比例为零，CheY-P=10/3 uM，位于零通量平衡。对照仅将 motor → motion 改为相同基线的常数，约 0.4514。通用 Design 的 0.5 control 是另一配置。

[Neumann 等（2012）](https://doi.org/10.1073/pnas.1205307109)支持 PTS 与甲基化适应的通路联系；上述能量关系和参数是约化模型假设。A/B 尚不是经误差分析建立的完整模型与降阶模型。旧记忆模型的聚集结果不能作为本版验收数据。

评价同时报告区域占比、到达与驻留、径向分布、累计材料转化，以及按初始细胞数归一化的转化量。多个 seed 是独立 run；同一 run 的 48 个细胞不当作 48 次独立实验。无效或反向效应均保留。

A 稳定版尚缺表面糖池、接触 QSSA、黏附/脱附反馈。B 的简化不是完整原始 ZIP 复刻。本阶段不宣称工程菌正反馈已成立。

## MCP–MeAsp 测量基准

约定配置为 400×800×200 um、20 um 网格、200 个细胞、dt=0.1 s、适应 k=0.3/s。梯度沿水平短边 X 轴（400 um），从 X=0 的 0 uM 增至 X=400 um 的 200 uM，初始斜率为 0.5 uM/um。两侧有限交换位于 X 的首末体素层，高浓度观测区域为 X≥200 um。Y/Z 方向初始浓度一致；零梯度对照为 100 uM。D=800 um²/s、边界 relaxation=5/s 是探索性设定。

速度 20 um/s、cluster size 6、Ki=18 uM 与 Ka=3000 uM 保留原候选数值。此前 GPT 辅助说明没有记录可核对的原始参数出处，因此本版标为 assumed。数值没有更改；这组参数配对的文献归属仍待核实。参数表位于 `src/friskoli_cad/science/data/chip_parameters.json`，任何命令行覆盖都会写入项目 provenance。

漂移取每个数值步的菌群沿 X 轴平均位置，在 10–120 s 范围拟合斜率，单位 um/s。120 s 前 drift 为 null。300 s 的终点分布和 10–120 s 漂移是两个窗口。只在上述配置上计入性能目标；改变 dt/dx 的结果独立标注。旧 Y 轴芯片数据及其衍生制品不纳入本次提交，当前 X 轴配置的长时研究待接手者重跑。

## 一手文献与声明边界

Somavanshi 等的研究支持 PTS 感知糖 influx、并与趋化通路相连的机制背景；不能据此把本项目的 EI/CheY 参数称为实验拟合值。[Somavanshi, Ghosh & Sourjik, PLOS Biology 2016](https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.2000074)。

MWC 受体与适应的理论背景参考 [Tu, Shimizu & Berg, PNAS 2008](https://pmc.ncbi.nlm.nih.gov/articles/PMC2551628/)。本版采用约化适应律与自己的数值分裂；当前没有证明 Ki/Ka、cluster size 或 k=0.3 的这组数值与该论文参数一致。

用户授权自有 A/B 材料以 MIT 公开。引用论文是机制背景，不推断团队曾阅读过这些原文。视觉识别、轨迹对比、实验校准、完整来源模型及长期生物预测均是后续任务。
