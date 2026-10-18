# 为 Wiki 重复生成模型数据 / Reproducible Wiki studies

脚本使用当前 CAD 的方程与空间运行器。结果目录保存输入、参数来源、抽样定义、CSV、SVG/PNG 和验证记录。先运行 A → B → C，再运行空间对照、M04、敏感性与 M16。每次使用新的输出目录。

## 安装与执行

在项目目录安装 `research` 依赖。下列命令适用于已启用的 Python 环境；Windows 也可将 `python` 换成 `.\.venv\Scripts\python`。

```bash
python -m pip install ".[research]"
python research/wiki_signal.py --out runs/wiki-signals
python research/wiki_spatial.py --out runs/wiki-spatial
python research/wiki_report.py --signals runs/wiki-signals --spatial runs/wiki-spatial --out runs/wiki-report
```

`wiki_signal.py` 默认完成 A/B/C：81 个稳态浓度、开启/关闭下游适应的升降阶跃，以及 64 组参数的 Latin hypercube sampling（LHS，分层覆盖各参数范围）。`wiki_spatial.py` 默认使用 8 个运动 seed，进行空间收敛与对照；M04 和敏感性使用前 4 个 seed。每个配置模拟 2 s，时间步为 0.02 s。长期条件可用 `--duration` 明确指定，结果中保留真实时程。

`wiki_report.py` 核对两组 CAD 源码哈希与空间审计结果，生成图位索引、中英文图注、M07 状态范围、核心数据流图、种子前缀均值变化表和输入文件校验值。图注从实际 manifest 读取抽样规模和时程。

分层评价数据存在时，报告同时生成 `manuscript-statistics.json`，按模型与 seed 配对终点记录，保存组内均值、样本标准差、配对差值及 bootstrap 区间。缺少配对或重复 seed 时停止生成统计表；单个 seed 的标准差与区间留空。

可以单独选择空间章节。例如：

```bash
python research/wiki_spatial.py --groups controls layers --seeds 1 2 3 4 --out runs/wiki-controls
python research/wiki_spatial.py --groups gradient --out runs/wiki-gradient
python research/wiki_spatial.py --groups sensitivity --morris-trajectories 8 --out runs/wiki-sensitivity
python research/wiki_spatial.py --groups credibility --out runs/wiki-credibility
```

源强、扩散、摄取和信号参数的扫描范围属于构造假设。信号脚本中的马达 Hill 系数 10.3 来自文献；其他设置与导出参数见 `parameters.csv`、`manifest.json` 和[文献对应表](WIKI_MODEL_EVIDENCE.md)。当前脚本未输入团队实测数据。

## A：静态剂量响应，M06 / M09

外部糖浓度在 `10⁻⁴–10⁴ µM` 的对数轴上变化。每个浓度求解稳态，再用 CAD 的 `advance` 函数推进一步检查稳态残差。输出实际摄取通量 J、去磷酸化 EI 分数、甲基化状态、CheA 活性、CheY-P 和 motor bias。

外部储库维持指定浓度，因此本组假设接受摄取量等于请求摄取量。它研究信号传递；空间耗竭与细胞间竞争由空间组单独处理。

适应开启时，甲基化可抵消恒定输入引起的活性变化，motor bias 的稳态曲线回到基线。关闭适应时，甲基化固定于同一参考浓度的初始值。两组共用同一组摄取及信号参数。`d(bias)/d ln C` 从稳态剂量曲线单独求导，图中与 motor bias 分开显示。

## B：动态阶跃响应，M08

先适应于 0.1 µM，在 5 s 升至 1 µM，在 35 s 回到 0.1 µM，运行至 65 s。开启/关闭适应两组采用相同输入与初始状态，关闭组仅令 `adaptation_rate_s = 0`。输出各信号层的完整时序。

`B-response-summary.csv` 记录有正负号的峰值变化、峰值时间、达到半峰值的响应时间和回落程度。适应时间定义为：峰值后首次进入原偏差峰值的 5% 范围，并在本阶段余下时段内保持其中。观察窗口中没有回落时留空。该比较定位适应在当前实现中的位置，结合文献解释其机制依据。

`B-time-step-check.csv` 比较 0.05 s 与 0.025 s 时间步。阈值在执行前指定为精细设置数值的 5%，绝对下限为 0.001 bias 或一个粗时间步。空的适应时间保留为空，未计为通过。

## C：背景浓度下的响应窗口，M12–M15 的区间基础

每个背景浓度先建立相应适应态，再施加 10% 浓度升高，在 20 s 内测量 motor bias 的最大下降。瞬态增益定义为该下降量除以 `ln(1.1)`。这与 A 的稳态导数分别命名和展示。

默认对 EI、甲基化和 CheY 时间常数、摄取半饱和浓度及 EI 去磷酸化系数扰动进行 64 组对数均匀 LHS，算法 seed 为 `20261016`。图带为参数响应的 5%–95% 分位数，中线为中位数。区间表示所声明参数范围内的模型输出分布。

自定义范围使用 `--ranges ranges.json`，JSON 中以参数名映射 `[下限, 上限]`，允许名称及默认值见脚本 `RANGES`。文献或实测范围需要保留逐项出处；默认区间按假设记录。`--samples`、`--seed` 可改变参数抽样规模与算法 seed。

## 空间对照与 M16

默认的 approach 场景在四个材料面外放置 8 个细胞，初始时位于观测区域外，便于记录到达。contact 场景将细胞放得更近，用于从零溶解糖开始研究释放与梯度。两种初始布置分别记录在配置中。

趋化关闭组保留摄取和信号计算，将马达至运动的输入改接为相同基线的固定 bias。每个 seed 的源强、几何、边界、初始菌群、初始溶解物及观测定义通过完整输入校验值核对。

M16 分别记录：

| 层次 | 输出 |
| --- | --- |
| Signal | 明确探测区域内的相对浓度梯度与浓度 |
| Response | 群体平均 CheY-P 与 motor bias |
| Arrival | 初始菌群的到达比例，到达者的平均首次观测时间 |
| Contact | 当前接触比例、累计接触时间、离散步边界观测到的接触进入次数 |
| Conversion | 材料转化量，以及按初始菌数归一化的转化量 |

`enrichment_vs_uniform_fluid` 将区域占比除以该区域可用液体体积占比，定义相对于均匀分布的富集倍数。富集、接触进入次数与接触时长分别保留。到达和接触进入均按提交的时间步边界计数；累计接触时间沿用 CAD 的左端点积分。

## M04：释放 × 扩散 × 摄取

本组将初始溶解糖设为零，扫描释放因子 `0、0.5、2`，扩散因子 `0.5、1、2` 与摄取因子 `0、0.5、2`。源强表示酶拷贝数与周转率的乘积；实际释放仍由接触决定，因此每次记录实际转化量。

探测区域为距材料表面 2–8 µm 的液体网格，排除接触固体的差分模板及外边界模板。单个探测体素同时满足 `C ≥ 0.01 µM` 和 `|∇C|/C ≥ 0.02 µm⁻¹` 时记为合格。合格体素达到探测区域的 10% 时记为达到本组梯度标准。记录本次有限时程内达到标准的累计时长，采用步末值积分。图注保留所有阈值与模拟时长。

M03 同时导出相同 seed 下的浓度切面与剖面线。M04 展示当前时程与标准下的区域，不自动外推为长期稳态。

相对梯度统计取高于浓度阈值的探测体素中位数。没有体素达到浓度阈值时，这一梯度代理值记为 0，合格体素比例也为 0；浓度本身另列，便于识别零信号条件。

## M12–M15：敏感性与不确定性

- M12：五个参数因子分别改变 ±20%，输出归一化 OAT 系数并排序。分母为零时系数留空。
- M13：适应速率与 EI 去磷酸化系数各取 `0.5、1、2` 倍，输出热图及逐 seed 数据。
- M14：四水平 Morris 筛选，默认 8 条轨迹、5 个参数、4 个共同运动 seed。输出 elementary effects、µ、µ*、σ 和每条参数轨迹。参数在对数因子坐标中变化。这里计算的是 Morris 指标。
- M15：A/B/C 中 C 的参数分布图提供当前假设范围下的输出分布。经核对的文献范围或实测范围应另存配置后重跑，并在图注中更新来源类别。

这些是有限样本、有限时程下的参数响应。相同运动 seed 用于配对，参数抽样 seed 与运动 seed 分开记录。

各参数配置从对应背景的适应态开始，避免把初始加载过程混入参数响应。空间场、菌群位置和朝向保持相同；信号内部初始状态随所声明参数推导，并写入冻结项目。

## 收敛、守恒与自洽

网格比较使用同一连续高斯场的精确体素平均值，使初始物质总量一致。空间组分别减半网格与时间步，在多个共同 seed 上比较指标差值。默认以参考均值的 5% 或各指标预设绝对下限中较大者作为允许变化。bootstrap 配对差值区间完全位于阈值内时记录 `within_threshold`；跨越阈值时记录当前种子数下未分辨。配置与原始差值保留在 CSV。

梯度探测采用同一物理距离范围，能用于中央差分的体素集合随网格变化。因此相对梯度的设置差异同时包含浓度求解与观测离散化的影响。判断该指标时保留模板筛选方法。

确定性扩散基准另用封闭边界的余弦模态，对照解析解的 L2 误差与质量变化。种子精度表比较前 4 与全部 8 个 seed 的均值及区间；固定域的 8→16 细胞扫描单独记录，因为它同时改变细胞密度、竞争和接触环境。

每步独立核对化学场收支、累计接受摄取与消耗、细胞内储备收支。脚本同时统计 PTS 信号函数的实际调用次数，要求每个提交步恰好一次。各配置通过后才继续，失败记录保留供检查。低对流条件的 [Péclet 数估算及 MCP 用途](WIKI_MODEL_EVIDENCE.md) 另作说明。

## 遭遇、驻留与表达水平（2026-10-17 补充）

上面的空间组运行 2 s，细胞离材料约一次游程，首次接触在 PTS 响应之前就由初始朝向决定。下列三个脚本分别检验核心假设中的不同环节：

```bash
python research/encounter_study.py --out runs/encounter-60s
python research/encounter_study.py --out runs/encounter-gap16-30s --start-gap 16 --duration 30
python research/g_scan.py --out runs/g-scan
python research/dark_start.py --out runs/dark-start
```

- `encounter_study.py`：与 `center-pts-a-encounter` 预设相同的持续梯度场景。材料表面维持约 1 µM，64 个细胞从 33–36 µm 外出发，默认 8 对 seed、60 s。分别输出曾接触比例、首次接触时间、进入次数、每次接触的驻留时间、累计接触时间与转化量，以及配对统计 `paired-statistics.csv`。
- `g_scan.py`：在 A、B 两种膜容量规则下扫描请求表达量 G，用 A/B/C 的信号方程计算各背景浓度的瞬态增益。同时给出当前模型（EI 耦合固定于参考菌株）与 2026-10-17 之前的按拷贝数重新推导两种约定。
- `dark_start.py`：沿用 contact 布置，从零溶解糖开始扫描释放因子 ×0.5–×16（含默认 ×1），10 s，反馈与对照各一组，报告自生成信号何时进入 PTS 响应区间以及对接触的影响。

接触结果同时取决于碰撞规则：细胞碰到材料后原地停住、朝向不变，直到下一次翻滚，所以驻留时间随翻滚率变化。报告时把首次接触（遭遇）、驻留和转化分开写。

## 图位、结构图与实验数据

M07 的状态变量范围可由 A/B 表读取；EI 和 activity 为 0–1，甲基化采用声明范围，CheY-P 受总量约束。M10/M11 可以在 Workflow 查看实际图，或参考[模型架构](MODEL_REFERENCE.md)中的阶段图。

M17–M20、检测精度分层和实验差异分析需要显微图像、标注或微流控实测记录。这些材料由相应实验工作提供。图表中的实测数据应保留配体、菌株、几何、边界和采样条件。

## English workflow notes

Run `wiki_signal.py` for A/B/C, then `wiki_spatial.py` for matched controls, M04, OAT/Morris screens, numerical audits and M16. Both use the CAD implementation and keep per-run inputs and results. New output directories preserve previous studies. `--groups` selects individual spatial chapters.

A solves maintained-concentration steady states and checks them with the CAD step function. B applies identical up/down inputs with downstream adaptation enabled or disabled. C uses 64 log-stratified parameter samples and plots response quantiles across background concentrations. Steady-state sensitivity, transient gain and motor CW bias are distinct outputs.

The spatial default lasts 2 s with eight motility seeds; phase and sensitivity screens use the first four. The phase criterion combines a 0.01 µM concentration floor, a 0.02 µm⁻¹ relative gradient and 10% qualifying probe volume. Morris provides elementary-effect statistics on assumed factor ranges. Seed precision, population-size scans and deterministic diffusion convergence are reported separately. Per-step inventory audits and actual signal-call counts accompany every spatial result.

Default intervals and scan settings are assumptions, with the motor Hill coefficient linked to its primary source. Parameter quantiles describe the sampled model range. Experimental figures require the actual measurement inputs and conditions; retain their provenance when substituting them for constructed values.
