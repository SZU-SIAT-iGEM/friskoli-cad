# PTS 最小科学链：M0/M1

版本 `pts-minimal-v1`，成熟度 **exploratory**。范围为固定细胞几何、均匀有限 bulk、饱和摄取请求和接受通量驱动的 EI/CheA/CheY 信号；无生长、死亡、分裂、纤维、运动、适应或 MCP 动力学。公式在 `friskoli_cad.science.pts`，不自行扣除库存、不写 World，也不按数组长度推断物种或群组。库存与实体合同由运行器执行。

## 来源与参数证据

机器可读材料随包发布：

- `src/friskoli_cad/science/data/source_lock.json`：A 修订 commit `e2093f8ae68bbf3de48d51dcf3af1e42e391d9b8`、B 修订 commit `6b6180b68ac047b244aa9947fe7cc7d227f76310`，以及实际迁入公式涉及的 9 个文件 SHA256。实读文件与对应 commit 字节逐一一致。保存原始快照指纹和两个基线 commit，未修改原材料。
- `data/evidence.json`：方程、变量语义、参数值/单位/源符号、论文适用范围。所有参数来源仅使用 `source-derived`、`constructed`、`unknown`；生物校准状态为 `unknown`。
- `data/fixtures.json`：构造输入及实际调用锁定 A/B 源纯函数所得输出。A 对照 `effective_pts_expression`、无 contact/surface 的 `compartment_requests`、`pts_step` 和 capsule area；B 对照实际 batch `transport_demand/pts_step` 及 `effective_pts_expression`。测试无需外部源码存在。

`rebuilt-source-comparison-v1` 与 `simplified-source-comparison-v1` 是显式选择的源码比较参数集，不能作为生物默认值。`constructed-minimal-v1` 只供数值演示/验收。构造初值、库存、几何与 dt 同样不代表实验数据。运行 API 没有隐式生物参数。

## 方程与单位

胶囊几何用含端帽总长 `L`、直径 `d`，要求 `L >= d > 0`。面积 `A=πdL`，单位 um²。来源的圆柱段长 `h` 必须先转换为 `L=h+d`。

两套容量保留不同名称：

\[
N_{tar}=N_0G,\quad
N_{cap,A}=\frac{(1-f_{basal})A f_{PTS}}{a_{AscF}},\qquad
N_{cap,B}=N_0G_{cap}\frac{A}{A_0}.
\]

`rebuilt_capacity` 对应 footprint 面积假设；`simplified_capacity` 对应参考面积比例假设。`N_fun=min(N_tar,N_cap)`、`G_eff=N_fun/N0`、`N_excess=N_tar-N_fun`。N 均为**每菌总 copies 的连续期望值**，没有体积稀释或浓度语义；footprint 是 um²/copy。这里不引入来源健康负担变量 phi。

\[
J_{request}=N_{fun}k_{cat}\frac{S}{K_S+S},\qquad
\dot e=k_dJ_{accepted}(1-e)-k_re,
\]
\[
A_{active}=A_T\frac{K_I}{K_I+E_Te},\qquad
\dot Y=k_YA_{active}(Y_T-Y)-k_ZY,\qquad
b=\frac{Y^h}{K_M^h+Y^h}.
\]

S、K、E_T、A_T、Y_T、Y 为 uM；e 是去磷酸化 EI 比例；J 为每菌 molecule/s，因此 k_d 为 molecule⁻¹，k_r/k_Z/k_cat 为 s⁻¹，k_Y 为 uM⁻¹·s⁻¹。b 是单马达响应代理量，不能当作整菌 tumble 概率或 hazard。CheA 是代数 readout，无独立动态状态。EI/CheY 是浓度或比例，不能与总 copies 互换。

`steady_state_signal` 给定恒定**接受通量**的局部信号平衡，不是有限 bulk 长时间保持恒定通量的承诺。零速率导致非唯一平衡时拒绝返回任意值；有状态推进中零速率保持原状态。

## 数值方法与明确修正

`advance_accepted_signal(J,e,Y,dt,p)` 先以恒定 J 的解析式推进 EI，再以该步末 EI 求 CheA，并在整步冻结此 CheA 推进 CheY。数值版本为 `endpoint-frozen-chea-exponential-v1`。两来源的这一段相同，A/B 容量仍独立命名。

EI 子方程精确；完整耦合系统为**一阶近似**，不能称 EI/CheA/CheY 全解析解。测试以独立 RK4 解完整耦合 ODE，验证 EI 解及 CheY 随 dt 细化的一阶收敛。该测试只覆盖所列构造场景，尚未完成生物时间尺度上的误差标定。

迁入时修正了来源在纯公式中的几处不合理行为：

- 负输入、NaN/Inf、非法比例/几何/参数和错形数组直接拒绝；不靠 clip 接受无效初态。
- 去掉任意 EPS 导致的零速率伪平衡，明确“状态不动”和“稳态不唯一”。
- 饱和式使用缩放比例，Hill 使用稳定 log 算法；Y=0 给严格 0，Y=K_M 给 0.5。有限极强输入不因大数相加/幂函数溢出出现 NaN；无法表示的中间速率明确拒绝。
- 更新以区间内凸组合保界，计算时从最近端点插值，保证旧值等于目标值时严格保持，避免两个乘积求和使满量 CheY 多出一个浮点 ulp；不用截零掩盖错误。两来源的普通参数输出在 `rtol=2e-13, atol=1e-14` 下对照通过；严格零值另外验证。

API 接受标量或一维数组，只有标量可广播；两个非标量输入形状必须完全一致。单位通过带单位的参数名、机器可读符号及上层端口合同明确；裸数组无法自行识别错误物种或群组。纯函数不修改输入，不产生随机数。

## 文献支持与限制

2026-09-30 重新读取 [Somavanshi et al. 2016 原文](https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.2000074) 的摘要、Introduction 与 influx 结果：被测 E. coli 的 PTS 网络能感知总体糖 influx，并向趋化通路传递信息。这为使用接受转运通量提供机制方向的支持，不能证明本队的三段现象学方程、AscF-cellobiose 参数或任意 dt 下的近似。

[Neumann et al. 2012](https://pubmed.ncbi.nlm.nih.gov/22778402/) 的既有原论文摘要核查支持 chemoreceptor–CheA–CheW 复合体及共同适应参与；本轮重新请求 PubMed 返回空正文，因此不增加全文核验声称。最小链省略了这些复杂机制，只能称 surrogate。已有审查读过的 [Cluzel et al. 2000 原论文](https://cluzel.fas.harvard.edu/sites/g/files/omnuum7156/files/cluzel/files/cluzel_science_00.pdf) 支持单马达的陡峭响应，不据此校准整菌运动。论文机制证据与源码参数出处分开保存。

## 验证

`tests/test_pts_science.py` 包含锁定源码纯函数对照、球极限/两种容量边界、零糖/半饱和/强输入、无接受摄取时 EI 再磷酸化、状态边界、稳态固定点、零速率、完整耦合 ODE 的步长细化、非法输入及纯函数不改输入。25 项通过；测试缓存关闭，输出在系统 Temp，设置 `PYTHONDONTWRITEBYTECODE=1`。

这些结果支持所测数值公式与输入合同，不能称工程菌模型已经生物验证、已经实验校准或能预测最优表达量。

## CAD 构造例与事务检查

`src/friskoli_cad/examples/pts_bulk.project.json` 使用 Project 0.3 与 `conservative-pts-bulk-v1`，所有参数显式标记为 constructed。A 群组 2 菌、B 群组 1 菌，胶囊面积相同；A 容量密度为 2 copies/um²，B 为 6 copies/um²。8 um³ 均匀 bulk 初始有 10 个期望底物分子。设定 dt=500 s 时需求超过库存，比例分配应得到 2、2、6 molecule；这个大步只用于展示共享限流，不作为生物过程的时间步建议。每个信号读取该菌接受量/dt。

运行图由 8 类模块组成：`bulk.finite_uniform`、`pts.capsule_area`、两套分别命名的 `pts.capacity_rebuilt` / `pts.capacity_simplified`、`bulk.sample_uniform`、`uptake.pts_request`、`uptake.bulk_settlement`、`signal.pts_accepted`。这套图执行上述最小链，不是完整趋化模型。

`tests/test_pts_runtime.py` 的 15 项检查覆盖初始信号未经推进、两组共享分配及信号对照、零库存衰减、实体/节点重排、strict 拒绝后全状态回滚、库存结算后信号失败的回滚、非法时序/双胞内库存 owner/跨群组连接、copies 与浓度单位错配、连续步骤的胞内与 bulk 总量账及 legacy runner 保持原行为。与 25 项纯公式测试合计 40 项通过。环境库存通过只读 `simulation.inventory` 映射查询；全体细胞在步起点读取同一均匀浓度，采样输出代表该起点，库存输出代表该步结算后状态。当前不产生空间浓度热图，也没有空间梯度或扩散场的含义。

另有局部数值反例：初始胞内量为 1e16 molecule 时，接受 2.5 molecule 在 float64 中可能只增加 2。只检查更新不等于旧值仍会漏掉这类部分丢失；运行器必须检查本步接受量与实际胞内增量的误差，不能用巨大胞内总量的相对容差掩盖它。

当前执行器分别检查 bulk 实际减少量及每菌胞内实际增加量，对应接受转移额 T 的误差预算为 `1e-10*abs(T) + 8*ulp(T)` molecule，以精确二进制有理数计算差值后比较。超出预算整步拒绝，时间、信号、库存、累计量和账本都不提交。此预算是版本化的浮点数值验收条件，**不是生物学容差**，也不是允许额外创造或删除底物的修正量。不能据此宣称位级精确的物质守恒。极小转移相对大库存无法可靠表示时可能被拒绝，单纯继续减小时间步不保证解决表示精度问题。
