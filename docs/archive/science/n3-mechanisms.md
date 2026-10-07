# N3：趋化、直接释放与细胞生理

核查日期：2026-10-01。执行语义为 `chemotaxis-spatial-v1`。本文解释当前机制和来源差异；实际数值验收见 [完整 N3 验证](../verification/verification-n3-complete.md)。这些模型用于比较假设，尚无本工程菌的实验标定。

## 来源与复现范围

A 指相邻审查库的 `rebuilt-v2`，B 指 `simplified-v4`。逐文件 SHA256 与最后改动 commit 存在 [n3_source_lock.json](../../../src/friskoli_cad/science/data/n3_source_lock.json)，从独立源进程生成的构造输入输出存在 [n3_fixtures.json](../../../src/friskoli_cad/science/data/n3_fixtures.json)。原 B ZIP 的 SHA256 仍为 `a3900092cb03a9d686800ca3c9450a2ce09fd5470c921b416b5aae5d0b63a5c2`。纯公式测试比较源输出、独立解析解和数值参照；这不等于整条轨迹复现。

按本轮选择，水解产物直接进入同一胞外 field，不迁入 A 的 contact/surface/bulk 三池。A 的传质假设已被替换。其浓度记忆、Monod、表达反馈与健康机制可以分别使用，但这个组合不能称为原 A 的完整复刻。B 的 direct-bulk 机制也经过 CAD 的接触几何、局部源与库存结算规则适配。

默认案例的短时可见参数属于 `constructed`；在下面列出的 A/B 原值属于 `source-derived`；它们对本工程菌的校准状态均为 `unknown`。文献支持具体机制，不替代码参数背书。[n3_evidence.json](../../../src/friskoli_cad/science/data/n3_evidence.json)记录文献定位、读取范围与不支持的推断。

## 状态与时间

| 状态 | 单位 | 含义 |
| --- | --- | --- |
| 胞外浓度 C、ligand L | µM | 独立命名的化学物种场 |
| 接受量 U、胞内量 n、材料量 M | molecule-equivalent | 同一次结算的物质当量 |
| EI 去磷酸化比例 e | 1 | PTS 降阶信号状态 |
| CheY-P Y、B 的记忆 mY | µM | 细胞内信号与低通记忆 |
| A 的浓度记忆 mC | µM | 胞外浓度低通，不是受体甲基化 |
| MCP 甲基化变量 m | 1 | 连续平均状态；未加入有限位点上下界 |
| motor bias b、health H | 1 | 模型读数；H 没有实验量映射 |
| 剩余 hazard、tumble 剩余时间 | 1、s | 事件时钟，不随帧输出重抽 |
| 体积 V、面积 A、INP 总数 N | µm³、µm²、copy | 数量与浓度严格分开 |

所有运动、扩散和信号步长以秒输入；生长、表达、health 以 `dt_s/60` 转成分钟。1 µM·µm³ = 602.214076 个分子，来自 SI 精确 Avogadro 常数和单位换算。[BIPM](https://www.bipm.org/en/si-base-units/mole)

CAD 使用已提交 motor 状态驱动下一积分区间，在本区间得到的信号供下一段运动使用。源 A/B 在一次固定步中把新算出的 motor 用于同一步移动，两者有限步长轨迹会不同。场采样、实际摄取、局部 ODE、运动和生理仍有 operator-splitting 误差；解析子步不代表整个系统精确。输出帧仅观察已提交状态。

## PTS 实际通量与 A/B 适应

同一局部库存的请求为 `R_i=J_req,i dt`，接受量为 `U_i=R_i min(1,M/sum R)`，零总请求单独处理。只有 `J_i=U_i/dt` 驱动 PTS；胞外扣除与胞内增加使用同一个 U。已有 EI/CheY 降阶关系为

\[
\dot e=k_dJ(1-e)-k_re,\quad A_P=A_T\frac{K_I}{K_I+E_Te},\quad
\dot Y=k_YA_P(Y_T-Y)-k_ZY.
\]

PTS 总体 influx 信号有实验依据，但以上省略 PEP、HPr 和 EII 各组分的 surrogate 并未由该实验逐式验证。[Somavanshi et al., 2016，Results: PTS Network Senses the Overall Influx of Sugars](https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.2000074)

A 的 `mC'=C+(mC-C)exp(-dt/τ)`；`b=min(1,bPTS exp(clip[-g(C-mC'),-20,20]))`。B 的 `mY'=Y'+(mY-Y')exp(-dt/τ)`；`Yeff=clip(Y0+Y'-mY',0,YT)`，再取 Hill bias。A 在恒定浓度下恢复到原始 PTS bias；B 在恒定 CheY 下恢复到固定 Y0。二者不可互换，也不等于 MCP methylation。原值 A：τ=3 s、g=2/µM；B：τ=3 s、Y0=2.59 µM、YT=8 µM，均只是源参数。

PTS 趋化适应涉及下游受体甲基化，低通记忆只是代码近似；论文没有测定本模型的 τ。[Neumann et al., 2012，receptor methylation adaptation 与 Fig. 3](https://pmc.ncbi.nlm.nih.gov/articles/PMC3409764/)

## MCP：经典 MWC 结构与显式简化

选择 `Ki<Ka` 表示 attractant，受体 activity 为

\[
a=\{1+\exp(N_c[\alpha(m_0-m)+\ln(1+L/K_i)-\ln(1+L/K_a)])\}^{-1},
\qquad \dot m=k(a_0-a).
\]

MWC 自由能、线性甲基化能量及依赖 activity 的负反馈来自 Tu、Shimizu、Berg 的经典框架；这里选 `F(a)=k(a0-a)`，没有实现论文用于 ramp 拟合的分段 F(a)，也没有显式 CheR/CheB。m 不限于离散甲基化位点范围，强刺激下的真实适应精度不能由此推断。[Tu et al., 2008，General Model 与 Fig. 1–2](https://pmc.ncbi.nlm.nih.gov/articles/PMC2551628/)

初始化用局部 L 解 `a=a0` 得 m。恒定 ligand 下，`da/dm>0`、`F'(a)<0`，所以 activity 回到 a0；attractant 突升先降低 activity。纯函数以冻结 ligand 的 backward Euler 更新 m，64 次单调二分求解，再取 `CheA=A_total a` 解析更新 CheY。步长减半相对独立 RK4 的误差检验属于数值验证。

当前注册示例 `Nc=6, Ki=1 µM, Ka=100 µM, α=1, m0=0, k=1/s, a0=.5` 是构造值。它们没有声称采用论文 Tar/MeAsp 的整套数值。ligand 感知本身不消耗库存；营养摄取另接物种。均匀恒定营养 reservoir 是实验场景假设，每次接受的营养都计入外部补给累计量。非代谢 attractant 可引发趋化有经典实验支持，但这不意味着所有 MCP ligand 都不能代谢。[Mesibov & Adler, 1972，Abstract](https://pmc.ncbi.nlm.nih.gov/articles/PMC251414/)

## motor 与运动

`b=Y^h/(KM^h+Y^h)`，`λ=λmin+(λmax-λmin)b`。Cluzel 的单 motor 数据支持陡峭 CheY-P 响应，h≈10.3、KM≈3.1 µM 是源采用的量级；其 Hill 拟合集中在 bias 0.1–0.9，不能声称完整区间严格符合。单 motor bias 到整菌 tumble hazard 的线性映射是额外假设。[Cluzel et al., 2000，pp.1652–1653、Fig.2](https://cluzel.fas.harvard.edu/sites/g/files/omnuum7156/files/cluzel/files/cluzel_science_00.pdf)

事件时钟抽 `H~Exp(1)`，run 时扣 `λ dt`；rate 变化保留剩余 H。instant 模式事件发生后马上继续运行；dwell 模式在 tumble 开始时抽方向，停留固定时长后运行。B 原固定 dwell 为 .1 s。A 转角为 `max(Normal(68°,36°),0)`，B 再截到 180°；CAD 分别命名 `rebuilt_normal` 与 `simplified_normal`。`isotropic` 是独立构造核。二维有符号转角是 CAD 平面化选择，原源为三维。

CAD 在一个区间内定位多个事件；源固定步每步至多一次事件。因此统计机制可对照，逐步随机轨迹不应称完全一致。碰撞阻挡是几何近似，继续消耗事件时间；不模拟接触力、鞭毛束或流体动力。提高停留比例、降低速度和真正沿梯度偏向必须用独立对照区分。

## direct-bulk 材料与守恒

每个接触细胞的有效酶预算按接触材料数分摊，避免同一份酶重复使用。材料 f 的请求取 `q_if=k_h E_if M_f/M_f0`，总接受产物不能超过剩余 M。无酶、无接触或耗尽时为零。B 原 active path 与此同属 direct-bulk：产物在细胞接触位置附近进入胞外场；CAD 的几何接触范围、材料 box、释放到 step-start 流体体素属于明确适配。

本轮移除三池的目的，是让释放、扩散与有限摄取速率共同决定局部堆积。新产物全量进入同一个场，再由普通 PTS 速率与该场库存决定接受量；程序没有给“细胞摄取”和“维持梯度”预留两份产物或设置配额。每步起点浓度用于提出摄取需求，场在同一事务中处理释放与摄取，仍须检查时间分拆误差。

`tests/test_chemotaxis_delivery.py::test_material_builds_a_natural_gradient_in_the_same_field_used_for_uptake` 使用初始浓度 0、无预置梯度、独立源释放率 0、静止接触菌，按 20×.1 s 推进；水解和摄取参数沿用模板。D=1 与 100 µm²/s 分别得到峰浓度 .001589178834 与 .000325153374 µM，均释放 198.111351704654 个底物当量；接受量分别为 .612501743 与 .264677195，其余留在同一场。较快扩散使此构造例的局部峰更平，局部与总量核对均通过。这证明这条计算路径可自然产生浓度差，不是对真实工程菌的扩散或酶参数标定。

库存核对使用 `固体 + 有限胞外 + 可用胞内 + 生长已用 + 死亡移出`；源与外部补给按各自累计量计入初始/输入侧。累计 hydrolyzed、uptake 是内部转移指标，不能再次加进存量。这里追踪 cellobiose-equivalent，缺少 CO₂、ATP、蛋白合成和维护代谢，不能称完整元素碳或能量预算。局部余额误差需要相对单次转移检查，不能只看巨大总账的相对误差。

## 生长、总 copies 与两种 health

取可用胞内 `n=旧库存+本步U`，A 用 `c=n/(602.214076 V)`、`μreq=μmax c/(Kg+c)`；B 用 `μreq=μmax`。共同按 `used=min(n, μreq V dt_min/YV)`，`ΔV=YV used`，`μactual=ΔV/(V dt_min)` 更新。后续表达/health 读实际 μ。几何阻挡增长时不消费该份库存。A/B 原 `μmax=.005 /min, YV=1.1672551805315156e-9 µm³/molecule`；A 的 `Kg=10 µM`，B active path 没有 Monod。

总 INP copies 满足 `dN/dt_min=s-kturn N`。B 合成为常数表达因子乘源速率；A 的 s 另乘 health、代谢及 INP 占用三项反馈。源 A 的参数为 `s0=800 copy/min, Hfloor=.25, hH=2, metabolic_floor=.6, Kμ=.001/min, Kφ=.35, hφ=4, kturn=.03/min`。CAD 对冻结合成率与 turnover 用精确指数步，源为 Euler，这是版本化数值差异。

纯体积增长不扣总 N；浓度 `cN=N/V` 的导数才含 `-μcN`，这是商法则。分裂时总 N 按子代份额分配一次。数量与浓度的区别也与生长细胞的表达建模相符，但本文不采用该论文的完整核糖体资源模型。[Lin & Amir, 2018，Abstract 与 model](https://www.nature.com/articles/s41467-018-06714-z.pdf)

A：`φeff=φPTS+wINP φINP`，`excessφ=max(φeff-φtol,0)`；repair 为 `rmax μ/(μ+Krepair)(1-H)`，toxicity 为占用超额 Hill 项加未容纳 PTS copy 的饱和项，starvation 为 `smax/[1+(μ/Ks)^hs]`。B：`φ=max(Ntarget-N0,0)/max(Ncapacity-N0,1e-12)`，可大于 1；`dH/dt=repair(1-H)-burden φ-starvation max(0,1-μ/μmax)`。toxicity 与 starvation 均不额外乘 H。H 用源定义的 Euler 后 [0,1] 截断，死亡 hazard 另算，按 `1-exp(-hazard dt_min)` 抽取。

A 的占用百分比与 B 的相对超基线表达负担不同。B 原 `repair=.01, burden=.006, starvation=.002, deathmax=1`（/min），死亡 onset `.05`；`.006` 是源按设计判据选出的值，未经死亡实验拟合。A 的完整参数清单随机器记录保存。死亡将剩余可用糖一次转移到移出账；资源上限和数值失败不构成生物死亡。

## 面积 adder 与几何边界

固定半径球柱体 `V=πr²(L-2r)+4πr³/3`，`A=2πrL=2V/r+4πr²/3`；L 包含两端，原源部分 length 仅指中间圆柱段。面积增长达到出生面积加阈值时提出分裂；体积、胞内糖和 INP 总数按相同 f 分配，母子身份、信号及记忆按合同继承。

两个子代都必须达到球体最小体积；放不下时延后分裂并保留内容物。几何算得分裂前后额外面积为 `4πr²/3`。这里没有独立膜材料库存，不能声称膜物料守恒。固定半径 A(V) 加 adder 不是 Harris–Theriot 的独立面积/体积合成率模型。[Harris & Theriot, 2016，Abstract、Fig.1 与 Fig.7](https://pubmed.ncbi.nlm.nih.gov/27259152/)

## 验证解释

纯函数覆盖 A/B 源输入输出、MCP 符号/稳态/独立 RK4 收敛、低通与 turnover 的时间组合、库存受限生长、死亡 hazard 及分裂当量。集成验证还须覆盖真实分裂、死亡、受阻、checkpoint、rollback、输出帧独立、同 seed 复现，以及多 seed 的零梯度、信号关闭、梯度、驻留对照和 dt/dx 敏感性。完成情况以可追溯测试/报告为准，不能由本页公式描述推断全部已经通过。

完整图行为研究见 [N3 多 seed 比较](n3-comparison.md)。`tests/test_chemotaxis_scientific_controls.py` 另用相反的固定行进方向隔离感知链：关闭 tumble 事件，保留所有信号与摄取速率，两菌从 x=60 µm 分别游到 75/45 µm，dt=.05 s、3 s。B 的 bias 从 .5 变为上坡 .493996548、下坡 .515333047；MCP 从 .451364107 变为 .421385501/.488406439。两条完整计算链的方向符号正确。这个开环检验不代表自由游走群体一定出现可分辨的定向增益；其效果大小仍应通过反向梯度和多 seed 对照判断。

安装验收随后修复了一个数组类型缺陷：canonical JSON 的整数 copies 输入在 extensive 输出按浮点份额继承时，原位乘法会失败；普通乘法允许结果成为浮点数。当前数量采用连续近似，按体积分数分配的公式和浮点输入结果没有修改。旧 wheel 与最终源码的 43 对运行覆盖全部 37 个研究条件的代表 seed，以及 6 个真实分裂例；5813 个提交边界的全部科学 checkpoint 分量精确相等。研究原始指纹与修复后指纹分别保留，具体范围和未重跑的 seed 见[版本与等价核查](n3-comparison.md#安装验收后的数组类型修复与证据版本)及[机器证据](verification/n3-inheritance-equivalence.json)。来源模型的 17 文件锁没有变化。
