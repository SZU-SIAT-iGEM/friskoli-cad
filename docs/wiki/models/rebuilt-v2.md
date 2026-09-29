# 从纤维表面水解到细菌运动

rebuilt-v2 中文 Wiki 草稿，2026-09-29。27文件原始来源清单标识：6f7bfc361389a6d7a5e269cd414025afe10fc5579f53cf1720af5866a4ffe219。原始源没有Git commit；[科学审查](../../science/source-models/rebuilt-v2.md)提供完整hash、方程、源码行号、修正与测试记录。**模型尚未迁入CAD执行库，参数也未经本项目实验标定。**

## 我们想理解的问题

表面展示水解活性的细菌接近固体纤维后，局部产生的cellobiose能否通过转运和信号改变运动、停留与生长？提高转运表达是否一直有利，还是会受到可及底物、膜面积和表达负担限制？

模型将这些问题连成可逐段检验的关系。它目前不能给出工程菌的最佳表达量、真实降解效率或微重力性能结论。源没有完整MCP受体甲基化网络、细胞分裂或流场（源README.md:49–51；model/和simulation.step）。

~~~text
纤维底物 → 接触处cellobiose → 纤维surface池 → 周围bulk
                  ↘                ↓               ↙
                            PTS实际摄取
                       ↙                 ↘
                  EI/CheY信号           胞内底物与生长
                       ↓                 ↓
浓度时间记忆 → motor bias → run/tumble   膜占用与健康假设
~~~

每根纤维固定于三维空间；每个细胞有位置、方向、附着和内部生理状态；bulk以体素浓度表示。这是ABM、局部ODE与扩散PDE的组合。以下介绍源模型的机制，原始数值错误与独立副本修订在文末说明。

## 几何和接触

纤维和细胞用spherocylinder近似。半径r、圆柱部分长度h给出

$$V=\pi r^2h+\frac43\pi r^3,\qquad A=2\pi rh+4\pi r^2.$$

h不含两端半球；CAD总长L=h+2r。源根据细胞体积与固定半径反推面积，再由接触面积占比估计参与水解的展示数量。附着与脱附是随机事件，危险率k转为步概率P=1−exp(−kΔt)；k随展示、距离、拥挤或停留时间变化。这些函数是待测经验关系。源只用细胞中心与纤维中心线距离，不能称为精确固体接触或排斥求解。代码：model/geometry.py:6–32、simulation.update_adhesion:52–96。

## 接触水解

附着细胞的产物请求速率为

$$q_i=E_i f_{c,i} k_h f_{a,f} f_{p,i},$$
$$f_c=\min(1,A_c/A_i),\quad f_a=\frac{(1+K_a)x_f}{x_f+K_a},\quad f_p=\frac1{1+(S_{c,i}/K_I)^n}.$$

E是活性展示代理量；A_c/A_i为接触面积比；x是纤维剩余量/初始量；S_c为接触池浓度。多个细胞共同水解时，总产物不能超过纤维可用库存。源k_h=.04或.40/s是whole-cell apparent情景值，不应称为纯化酶kcat。代码：model/hydrolysis.py:10–31、simulation.py:118–133。

默认K_I=170µM与Kuusk等在Cel7A/特定BMCC底物测得的观测抑制量级相符，但源没有注明该引文；相似数值不能证明工程菌表面展示适用。[R3] 底物、温度与反应状态对抑制的影响已有实验报道。[R6]

## 三个溶质池与实际摄取

contact每个细胞一份，surface每根纤维一份，bulk为三维场：

$$\dot N_{c,i}=q_i-J_{c,i}-k_{cs}N_{c,i},$$
$$\dot N_{s,f}=\sum_{i\in f}k_{cs}N_{c,i}-\sum_{i\in f}J_{s,i}-k_{sb}N_{s,f},$$
$$\partial_t S_b=D_{eff}\nabla^2 S_b+Q_{s\to b}-Q_{b\to cell}.$$

N的单位为molecule，J为molecule/s，S为µM。用µm³表示体积时，N=602.214076×S×V，换算依据精确Avogadro常数。[R5] 这些微分式说明物质路径；实际代码用固定顺序分步求解。contact→surface→bulk是单向交换，没有反向浓度差驱动；整根surface完全混匀，最终从纤维中点体素进入bulk，未解析沿纤维的表面梯度。代码：simulation.update_carbon:106–194。

对共享库存的请求r_i=J_iΔt，实际接受量为

$$a_i=r_i\min\left(1,\frac{N_{available}}{\sum_jr_j}\right).$$

零总请求时a_i=0。只有实际接受量才能进入胞内或驱动信号。代码：model/transport.allocate_shared:58–69。

## PTS数量与膜容量

表达因子G给出目标数量，再受可用膜面积限制：

$$N_{PTS}^{target}=N_0G,\qquad N_{PTS}^{functional}=\min\left(N_0G,\frac{(1-f_{basal})A\phi_{max}}{a_{PTS}}\right).$$

接触膜片与其余膜分别产生饱和摄取请求，并在三池间分摊。这样可以区分想表达多少、膜上容纳多少和实际摄取多少。源N0=500、Km=30µM、turnover=20/s，没有构建体测量出处。代码：model/transport.py:16–50、parameters.py:34–44。

## 摄取与运动的联系

实际通量J驱动去磷酸化EI比例e，再改变有效CheA与CheY-P：

$$\dot e=k_dJ(1-e)-k_re,\quad A=A_T\frac{K_{EI}}{K_{EI}+E_Te},\quad \dot Y_P=k_YA(Y_T-Y_P)-k_ZY_P.$$
$$B_0=\frac{Y_P^H}{K_M^H+Y_P^H}.$$

PTS与趋化存在生物学联系，但原始实验指出信号与受体–CheW–CheA复合体及共同适应有关。[R1] 上式应理解为降阶描述。单motor陡峭响应有实验依据，[R2] 但不直接验证该代码的整菌tumble映射。

源另加浓度时间记忆：

$$\dot m=(S_b-m)/\tau,\qquad B=\mathrm{clip}(B_0e^{-\gamma(S_b-m)},0,1).$$

时间比较思想有经典实验支持，[R4] 但指数形式、τ=3s、γ=2/µM是源情景设置，不等于完整MCP适应网络。λ=λmin+(λmax−λmin)B决定tumble危险率；自由细胞瞬时转向后按25µm/s前进。每步至多一次事件，没有有限tumble时长。代码：model/signaling.py:10–43、simulation.py:197–223。

## 生长和健康

$$\mu=\mu_{max}\frac{S_{int}}{K_g+S_{int}},\qquad\Delta V=Y_V N_{used}.$$

体积只由实际消耗的胞内底物增加；YV由源质量产率与干重密度换算。展示表达受health、代谢与膜占用门控。健康量用修复减膜占用、过量PTS和饥饿惩罚更新，再决定随机死亡。它没有经过实验标定，不能将0–1健康量当作已测生理指标或把其hazard当真实死亡概率。代码：model/growth.py:12–41、model/health.py:10–23。

## 怎样验算

先检验等价底物账：

$$N_{fiber}+N_{contact}+N_{surface}+N_{bulk}+N_{intracellular}+N_{growth\ used}+N_{removed}.$$

源按一份纤维库存产生一份cellobiose计数，这是模型约定的等价账，不包括CO₂等完整代谢组分。hydrolyzed和uptake只是内部转移累计，不能重复加到账内。代码：state.carbon_inventory:94–103。

本轮原始版5个自动测试通过；两细胞附着、reference运行.05s的构造检查生成1.07839、摄取.10495等价分子，绝对账残差为0。这是短小代码验算，不是工程性能测量。另有明确反例：原FFT将负格截零，使8³格脉冲的离散总量增加9.17389%；原dead细胞仍摄取和生长；原reflective边界只截位置不翻转方向。详见[原始版检查](../../science/source-models/rebuilt-v2.md#7-原始版有界检查)。

独立副本review-correctness-v1已修正上述错误：dead摄取/生长为0；反射保留越界剩余位移并翻转方向；FFT改为六邻点离散扩散半群，脉冲总量保持在浮点误差内；随机状态可以随分段run继续。原5项加新增30项回归共35 passed，另有实际附着源5步组合账验证。[修订状态](../../science/source-models/rebuilt-v2.md#9-独立副本修订状态)给出原始基线8b8bb76、机器记录与兼容范围。

修订还明确n_inp为总copy数量：保留turnover损失，移除仅由体积增长造成的−μN项。浓度c=N/V会随体积增长稀释，总copy本身不会因此消失；这一数学区分与Lin、Amir的原始模型一致，[R7] 但不标定本工程菌的表达率。下游健康/表达也改为读取实际营养消耗支持的区间增长率。生物参数默认值保持不变。

修正数值错误不构成生物学标定。后续仍需固定域、物理源支持与观察指标，细化dt/dx，多seed比较随机变异；再用构建体摄取、水解抑制、停留、表达负担和趋化数据标定。

## 当前边界

模型尚未进入CAD执行库；当前CAD示例不能当作它的运行结果。未完成长时间、多seed和空间/时间收敛；没有微重力专用规律、精确固体接触、细胞分裂或完整MCP网络。两个水解情景及尚未测量的生物参数需要和结果一起披露。原始与修订版本必须分别保存，不能让修订结果伪装成原源输出。

## 已核查来源

所有来源核查于2026-09-29。它们提供机制或适用范围依据，不代表每个代码默认值都来自这些论文。

- [R1] Neumann S, Grosse K, Sourjik V. PNAS 2012. Chemotactic signaling via carbohydrate phosphotransferase systems in Escherichia coli. DOI 10.1073/pnas.1205307109。[原文](https://pmc.ncbi.nlm.nih.gov/articles/PMC3409764/)。
- [R2] Cluzel P, Surette M, Leibler S. Science 2000. An ultrasensitive bacterial motor revealed by monitoring signaling proteins in single cells. DOI 10.1126/science.287.5458.1652。[原论文摘要](https://pubmed.ncbi.nlm.nih.gov/10698740/)。
- [R3] Kuusk S, Sørlie M, Väljamäe P. JBC 2015. The predominant molecular state of bound enzyme determines the strength and type of product inhibition in the hydrolysis of recalcitrant polysaccharides by processive enzymes. DOI 10.1074/jbc.M114.635631。[原文](https://pmc.ncbi.nlm.nih.gov/articles/PMC4416869/)。
- [R4] Macnab RM, Koshland DE Jr. PNAS 1972. The gradient-sensing mechanism in bacterial chemotaxis. DOI 10.1073/pnas.69.9.2509。[原论文摘要](https://pubmed.ncbi.nlm.nih.gov/4560688/)。
- [R5] BIPM. [SI mole官方定义](https://www.bipm.org/en/si-base-units/mole)。
- [R6] Teugjas H, Väljamäe P. Biotechnology for Biofuels 2013. Product inhibition of cellulases studied with 14C-labeled cellulose substrates. DOI 10.1186/1754-6834-6-104。[原文](https://pmc.ncbi.nlm.nih.gov/articles/PMC3726336/)。
- [R7] Lin J, Amir A. Nature Communications 2018;9:4496. Homeostasis of protein and mRNA concentrations in growing cells. DOI 10.1038/s41467-018-06714-z。[原文](https://www.nature.com/articles/s41467-018-06714-z)。支持总数量与浓度的区分，不提供本构建体表达/turnover参数。
