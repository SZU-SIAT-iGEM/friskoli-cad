# simplified-v4：源模型审查与模块迁入草案

审查日期：2026-09-29。对象：`model.v4_simplified_v1_3(1).zip`。状态：**源码审查与有限数值核查；尚未注册为 CAD 科学包，未完成实验标定或生物学验证。** 独立修订副本位于 `friskoli-model-review/simplified-v4/`；原 ZIP 保持不变。本文前七节描述原包，修订记录在第八节，不能把两者当作等价实现。

## 1. 来源与证据定位

| 项目 | 记录 |
| --- | --- |
| 原 ZIP | `model.v4_simplified_v1_3(1).zip`，1,879,248 bytes |
| SHA256 | `a3900092cb03a9d686800ca3c9450a2ce09fd5470c921b416b5aae5d0b63a5c2` |
| 根目录 | `model.v4_simplified_v1_3/` |
| 成员检查 | 220 个成员，未压缩总量 4,369,462 bytes；检查绝对路径、路径穿越、Windows 冒号/反斜线、大小写重复、链接及非普通文件类型，无此类问题 |
| 读取 | 63 份文本抽取至唯一系统 Temp；归档字节码、Numba 缓存未执行 |
| 独立基线 | 52 份必要源码/配置/测试/说明、501,736 bytes，复制时逐文件核对；`SOURCE_BASELINE.json` 记录原成员字节 hash；Git 基线 `98b12ff`；Git 换行规范化与原字节 hash 分别记录。排除缓存、图像、运行输出、旧归档及性能规划 |
| 授权边界 | 未在所审文本中找到明确许可证，未来向 CAD 迁入实现前仍须核实授权 |

下文 `文件:行号` 相对于该 ZIP 根目录，针对上述 hash；C 为代码证据，E 为外部原始/官方证据。代码运行结果、作者声称、论文结果、审查推导分别说明。

| ID | 原包定位 | 内容 |
| --- | --- | --- |
| C01 | `core/simulation.py:454–791`，`Simulation.step` | 批量主循环、次序、生命周期和扩散节拍 |
| C02 | `core/batch.py:532–583`，`pts_step` | 转运需求、EI/CheA/CheY 分步更新 |
| C03 | `core/batch.py:592–652` | CheY 记忆、马达有效信号和 Hill bias |
| C04 | `model_a_behavior/motion.py:13–61,89–162` | 随机 run/tumble、方向和周期边界 |
| C05 | `core/environment.py:57–151,190–201`；`core/batch.py:238–255,1396–1461` | 周期扩散、采样和库存结算 |
| C06 | `core/batch.py:1047–1190` | 黏附/脱附、有限纤维水解和沉积 |
| C07 | `core/batch.py:1219–1280`；`model_b_morphology/geometry.py:18–76` | 库存产率生长、固定半径球柱几何 |
| C08 | `integration/abm_coupling.py:26–61`；`model_b_morphology/membrane_occupancy.py:35–46,71–82,99–111` | 表达容量、INP 数量、负担 |
| C09 | `core/batch.py:1321–1388`；`model_b_morphology/health.py:25–57` | 健康 ODE 和死亡 hazard |
| C10 | `core/agent.py:381–498`；`model_b_morphology/division.py:13–118` | surface-adder、几何门槛与继承 |
| C11 | `config.py:8–22,95–224,244–309,334–491` | 参数和作者来源声明 |
| C12 | `core/simulation.py:72–153,196–211,290–335,793–820,873–1000` | 初始化、共享 RNG、招募与资源上限 |
| C13 | `model_a_behavior/expression.py:49–76`；`model_a_behavior/pts_signaling.py:544–592` | 设计到 G 的映射、近似半响应 |
| C14 | `tests/test_chemotaxis_adaptation.py:15–79`；`tests/test_issue7a_uptake_conservation.py:36–137` | 局部适应/摄取测试 |
| C15 | `tests/test_issue5_geometry_consistency.py:32–316`；`tests/test_issue6_health.py:23–147` | 几何测试及健康设计标准 |

## 2. 原包到底实现了什么

真实运行对象是 **三维离散个体 ABM + 周期扩散 PDE + 两状态 PTS ODE + 低通适应 surrogate + 随机运动/黏附/死亡 + 库存受限生长与分裂**。权威细胞状态在 `Simulation._batch_state`；普通 `Bacterium.update()` 不是主循环。[C01]

- `surface_pool` 标签实际调用直接进入 bulk 场的水解；主循环令 `surface_S=0`，旧表面池/微区函数没有参与这条路线。[C01,C06]
- `phi` 可以大于 1，表示相对表达负担；当前健康公式不使用 INP 负担。不能写成“膜面积占用百分比”。[C08,C09]
- 面积由固定半径体积几何计算；`sv_active` 名称不表示独立 S/V 合成模型。[C07,C08]
- 适应是 CheY 低通记忆，没有显式 MCP 配体结合、甲基化、CheR/CheB 动力学。论文中的受体适应证据不能代替 surrogate 参数标定。[C03,E02]
- `config.py:117` 所称“G≈5–10 最优”是作者注释，本轮没有相应实验或稳健设计比较证据。运行成功不能支持这个结论。[C11]

## 3. 数学结构、单位与实体

胞外浓度记作 \(c\)，表面积记作 \(A\)，避免源码同名 `S` 的混淆。\(dt\) 用秒，\(dt_m=dt/60\) 用分钟。cell 数组按细胞身份对齐；fiber 数组按纤维身份对齐；grid 是固定体素域。

### 3.1 场与摄取结算

实际 `field` 单位是 µM；`config.py:11` 的“扩散场 molecules/µm³”概述与实现不同。每体素库存为

\[
M_v=q c_v\Delta x^3,\qquad q=602.214076\;\mathrm{molecule/(\mu M\,\mu m^3)}.
\]

换算由 Avogadro 常数及升/微米换算得到，不是拟合。[E05；单位推导]

扩散方程 \(\partial_t c=D\nabla^2c\)，周期边界。原 FFT 先用 \(\hat c_k'=e^{-D|k|^2dt_d}\hat c_k\)，再裁负值、按原总量重标；原 FD 是三维七点周期 stencil + Euler。只有这个均匀 FD 适用 \(D\delta t/\Delta x^2\le1/6\)。原 FFT 后处理不属于精确热方程解。[C05]

采样、需求分组和扣账使用移动前的细胞中心所在体素，无插值或胶囊表面平均。请求量 \(R_i=J_i dt\)，同体素总请求 \(R_v=\sum R_i\)，实际接受量

\[
U_i=R_i\min(1,M_v/R_v),
\]

零请求另处理。环境扣账与胞内增加使用同一个 \(U_i\)，但原 PTS 已经先按请求 \(J_i\) 更新。[C01,C05]

### 3.2 PTS、CheA 与 CheY-P

\[
J=N_0G_{eff}k_{cat}\frac{c}{K_S+c},\quad
\dot e=k_dJ(1-e)-k_re,
\]
\[
A_P=A_T\frac{K_I}{K_I+E_Te},\quad
\dot Y=k_YA_P(Y_T-Y)-k_ZY.
\]

\(J\)：molecule/(cell·s)；\(e\)：EI **去磷酸化分数**；\(A_P,Y,E_T,A_T,K_I,Y_T\)：µM；\(k_d\)：cell/molecule；\(k_r,k_Z,k_{cat}\)：s⁻¹；\(k_Y\)：µM⁻¹s⁻¹。[C02,C11]

先冻结 \(J\) 解析更新 \(e\)，再取新的 \(e\) 计算并冻结 \(A_P\)，解析更新 \(Y\)。两个线性子步有解析解，整体耦合系统仍有分步误差。没有 PEP、HPr、EIIA/B、CheZ 的显式状态，蛋白总浓度保持常量。[C02]

PTS 总糖 influx 向趋化系统传递有实验支持，[E01,E02] 但当前两状态简化式和所有参数并未由这些实验确定。缺货时原程序的信号读请求通量，与实际跨膜通量存在数值耦合差异；修订必须另记版本。

### 3.3 适应、马达与运动

\[
Y_m'=Y_m e^{-dt/\tau_a}+Y'(1-e^{-dt/\tau_a}),\quad
Y_{mot}=\operatorname{clip}(Y_0+Y'-Y_m',0,Y_T),
\]
\[
b=\frac{Y_{mot}^h}{K_M^h+Y_{mot}^h},\quad
\lambda=\lambda_{min}+(\lambda_{max}-\lambda_{min})b,\quad
p_{turn}=1-e^{-\lambda dt}.
\]

\(Y_m,Y_0,Y_{mot},K_M\)：µM；\(\tau_a\)：s；\(b\)：无量纲；\(\lambda\)：s⁻¹。恒定输入使 \(Y_m\to Y\)，马达回同一基线，这是滤波结构的结果。[C03]

未附着且正在 run 的存活细胞移动 \(x'=x+vu\,dt\)，再周期取模；翻转开始那一步不前进。角度为截到0–180°的正态，方位角均匀，翻转时间固定。每步最多一个翻转开始；没有旋转扩散、流体力、细胞碰撞或鞭毛束状态。[C04]

Cluzel 的单马达 CW-bias 实验支持陡峭 Hill 响应；将 bias 线性映成整个细胞的 tumble hazard 是额外假设。[E03]

### 3.4 黏附、水解与纤维库存

默认路线按周期线段距离判断：最终捕获范围是纤维半径+接触肩距，默认10+20=30 µm；200 µm仅是候选搜索范围。黏附和脱附使用常数 hazard 转成 \(1-e^{-kdt_f}\)，没有真实接触力或吸到表面的几何处理。[C06,C11]

\[
E_f=\sum_{i\in\mathcal A_f}N_{INP,i},\qquad
Q_f=\min\left(k_hE_f(C_f/C_{f,0})dt_f,C_f\right).
\]

\(C_f,Q_f\)：cellobiose-equivalent molecule；\(N_{INP}\)：有效展示拷贝数；\(k_h\)：每有效酶每秒生成的产物当量。\(Q_f\) 按每个附着细胞的酶数比例沉积到细胞所在体素。纤维当量减少等于产物当量增加，尚非化学产物组成或实验水解率的验证。[C06]

保留的 retention、cocktail synergy、表面池泄漏、接触池上限、距离衰减和最大附着数参数，不应因存在或被日志打印就显示为活跃机制。该主路径水解只读 `DISPLAY_ENZYME_KCAT_EFF`，黏附使用固定 hazard。[C06,C12]

### 3.5 库存生长、胶囊几何与分裂

胞内量 \(n\) 单位为 molecule，接受摄取之后：

\[
B=\min(n,\mu_{max}Vdt_m/Y_V),\quad \Delta V=Y_VB,\quad n'=n-B,\quad
\mu=\Delta V/(Vdt_m).
\]

\(Y_V\)：µm³/molecule；\(V\)：µm³；\(\mu\)：min⁻¹。`K_S_GROWTH` 已不用。没有维护代谢、氧限制、呼吸产物和展示酶合成资源账，不能称完整碳/能量模型。[C07,C11]

源码 `length` 是圆柱段 \(\ell\)，CAD 胶囊长度是总长 \(L\)：

\[
V=\pi r^2\ell+4\pi r^3/3,\quad A=2\pi r\ell+4\pi r^2=2V/r+4\pi r^2/3,
\quad L=\ell+2r,\quad d=2r.
\]

\(r\) 固定，面积由体积推得。要求 \(V\ge4\pi r^3/3\)，不能把负圆柱长度的截断当作输入有效。[C07]

surface-adder 使用固定参考 \(A(1.0)-A(0.5)\) 加噪声，非每个实际出生体积到1.0的差。\(r=0.4\) 时平均 \(\Delta A=2.5\) µm²等价于 \(\Delta V=0.5\) µm³。[C10；代数推导] 达阈值且母体 \(V\ge2V_{min}\) 才分裂；普通 adder 分支没有相同几何门槛。

体积、胞内量、INP 按受几何范围限制的随机比例分割；对象级代码计划继承e/Y/Y_mem/H/G，但批量主路径漏同步及追加Y_mem，分裂后记忆继承和数组对齐实际不可靠。新子体随机方向、固定0.5 µm位置偏移，无接触避让。新极带来总面积增加 \(4\pi r^2/3\)，没有独立膜材料付款。[C10；原core/simulation.py:336–452,822–867]

### 3.6 容量、表达负担与健康

\[
N_{tar}=N_0G,\quad N_{cap}=N_0G_{cap}A/A_0,\quad
N_{fun}=\min(N_{tar},N_{cap}),\quad G_{eff}=N_{fun}/N_0,
\]
\[
\phi=\max(N_{tar}-N_0,0)/\max(N_{cap}-N_0,\epsilon).
\]

\(\phi\) 可以超过1；INP、功能性容量占比等另外记录，当前不进入总健康负担。[C08]

原INP用分钟积分 \(\dot N=k_{exp}f_{INP}-(\mu+k_{deg})N\)，传入H和phi_inp不影响这个更新。变量被定义为每细胞数量，分裂另行分割。纯体积增长会降低浓度N/V，不会减少数量N；因此−µN不能同时解释为数量方程中的“稀释”。若要代表额外生长相关丢失，应单独命名并给证据。修订版采用显式政策区分这两种含义，见第八节；没有借此推定实际展示酶的降解速率。[C08,C10,E07]

\[
\dot H=k_R(1-H)-k_B\phi-s_{max}\max(0,1-\mu/\mu_{max}),
\quad \lambda_D=k_D^{death}\max(0,(H_D-H)/H_D).
\]

先 Euler 加 [0,1]限幅，再以 \(1-e^{-\lambda_Ddt_m}\) 抽死亡。速率均为min⁻¹；H是经验状态，尚无实验量映射，H_DEATH是hazard起点。[C09]

### 3.7 近似半响应的适用范围

`compute_Kd_eff` 给出 \(k_rK_IK_S/(E_Tk_dN_0Gk_{cat})\)，来自 low-flux、low-substrate 和 CheA 零糖活性减半条件。精确半响应还要求 \(E_T>K_I\) 及 \(J_{max}>k_rK_I/[k_d(E_T-K_I)]\)；不满足时有限半响应不存在。当前实际G_eff会受容量限制，马达还适应回基线，所以这个诊断量不能直接称全细胞趋化解离常数、行为检测阈值或最优表达指标。[C13；推导]

## 4. 时序、RNG与所有权

原包默认dt=0.01 s，纤维每10步、扩散每0.1 s，生长/健康用dt/60。step0已注入完整0.1 s的源，首次扩散在step9末。每步：纤维事件/产物 → 旧位置bulk采样 → 请求J/PTS → 适应 → 库存接受 → 运动 → 生长 → 新几何/表达 → 健康/死亡 → 分裂 → 到点扩散 → 招募观察。[C01]

新G_eff下一步才被PTS读取；初始化强制Y=Y_mem为无糖平衡，非任意初始浓度的稳态。`Simulation.rng` 是一个共享 Generator（seed默认42），用于纤维、初始化、运动、黏附、健康和分裂；没有实体/模块随机流分离及RNG回滚。[C12]

| owner | 状态 | 迁入要求 |
| --- | --- | --- |
| DiffusionField | grid浓度、可重建FFT缓存 | 场统一结算；显式domain/species/grid revision |
| Environment | fiber几何、有限当量 | fiber稳定ID；与grid/cell通过显式映射连接 |
| _batch_state | cell位置、方向、信号、胞内量、V/INP/H、附着状态 | cell稳定ID、每个状态唯一writer、生命周期后重建索引 |
| Bacterium | 镜像及分裂创建；类级ID计数 | 镜像不能成为第二个owner；ID并入run状态 |
| Simulation RNG | 共享随机状态 | 固定算法/版本，回滚与checkpoint另需实现；配对seed不保证生命周期后轨迹配对 |
| Observer | 前一附着状态、招募ID集合、计数 | 在计算步累计，分母与founder排除规则独立定义 |

## 5. 参数出处与科学成熟度

| 参数 | 原包说明/核查结论 |
| --- | --- |
| q=602.214076 | 单位推导与官方精确常数，[E05] |
| K_S=30 µM、N0=500/cell、kcat=20/s | K_S已明示来源未确立；N0为工程参考；kcat为作者glucose-PTS类比，非AscF实测；`config.py:100–112` |
| EI=6、CheA=5、CheY=8 µM | `config.py:128–154`引用Rohwer2000；本轮未核实其是否逐项支持CheA/CheY或当前菌株，保持待核实 |
| k_d=0.001 cell/molecule、k_r=20/s、K_I=0.3 µM | 简化耦合和恒定供能参数，待标定；`config.py:133–146` |
| h=10.3、K_M=3.1 µM | 原包借用单马达响应；[E03]支持陡峭Hill关系，不支持整个细胞hazard参数 |
| tau_adapt=3s、Y0=2.59µM、lambda=0.1–10/s | surrogate及运动选择，未找到工程菌标定记录 |
| D=650/10=65 µm²/s | 水中扩散/相对黏度的作者设定，当前纤维培养体系未测 |
| 生长yield | 作者用分子量、参考干重/体积和0.50g/g组合；量纲可核对，培养条件产率未验证；`config.py:300–309` |
| G_CAP=10、r=0.4µm、INP数量/速率 | 结构尺度假设，缺匹配构建的测量 |
| K_BURDEN=0.006/min | 按phi=1饥饿仍存活、phi=2充分营养进入死亡区两个设计标准选择；非数据拟合，`tests/test_issue6_health.py:52–66,132–147` |
| 水解5/s、黏附0.5/s、脱附0.05/s | 有效机制参数，未核实匹配底物/展示体系的原始测量 |
| 启动子→G | 相对强度×拷贝因子线性映射，未验证翻译与膜插入关系；C13 |

“未核实”表示证据不足，不等于证明值错误。旧审查Markdown也是作者材料，不能替代原始测量。

## 6. 原包测试与反例

安全抽取目录中，Python3.11.9、NumPy2.1.2、Windows，设置NUMBA_DISABLE_JIT=1、PYTHONDONTWRITEBYTECODE=1，pytest禁用cache。选择 `test_chemotaxis_adaptation.py`、`test_growth_yield.py`、`test_issue5_geometry_consistency.py`、`test_issue6_health.py`、`test_issue7a_uptake_conservation.py`、`test_kd_eff.py`、`test_expression_geometry.py`：**103 passed in 1.06s**。部分测试仍针对旧surface-pool函数；未跑全部测试、JIT、8小时ABM或实验拟合。health测试中的8h survival是公式积分，不是培养实验。[C14,C15]

| 短探针（原包） | 观测 |
| --- | --- |
| FD，8³网格、dx50、D65、内部dt.01、单体素脉冲 | update(.015)与update(.01)完全相同；请求余时被舍弃 |
| FFT，20³网格、dx50、D65、dt.1、单位单体素脉冲 | 未裁剪最小值−0.00129214623；负质量绝对量0.01251509643；裁剪/归一化L1改变量0.02503019287 |
| 1µm³体素、c=10⁻⁶µM、两细胞G_eff10、dt1s | 各请求0.00333333322 molecule、各接受0.000301107038；EI按前者更新，非默认培养结论 |
| 点源1µM·µm³/s，1个G0细胞，无fiber，20步0.2s | 初脉冲100，最终仍约100；持续源应另加0.2。主循环绕过Environment.update_diffusion中的释放 |

## 7. 风险与应验收的改变

| ID | 原包问题 | 修改/验证方向 |
| --- | --- | --- |
| F01 | 信号读请求J，实际库存读U；日志mean_J也为请求 | 分开demand/accepted；用U/dt驱动EI，另版本。缺货、多细胞争用、零库存与无限库存对照 |
| F02 | FFT裁负/全局重标改变局部解；FD丢余时，短于内部dt还多推进 | 版本化离散算子；Fourier模态、脉冲、非整倍时长、正性、守恒和收敛 |
| F03 | MAX_CELLS触发随机删细胞并register_death | 资源停止独立于生物死亡；人口/胞内量不得因算力上限消失 |
| F04 | INP copies连续−µN被称稀释，分裂又分割；AscF按N0G瞬时重算 | 采用copy_number默认；额外损失用legacy_growth_loss明示；真实分裂谱系计数试验 |
| F05 | source在step0预付整段；同tick可脱附后再附着 | 固定物理节拍比较dt；明确事件优先级，量化分拆误差 |
| F06 | 子体位置偏移不防重叠；运动直接读全局domain | 显式domain和胶囊几何adapter；接触作为独立可验机制 |
| F07 | continuous source不持续释放；无活细胞早退跳过扩散 | 场推进独立于细胞存活；零细胞扩散、无摄取常源M=M0+rt |
| F08 | 名称/旧说明/日志与有效机制不符 | catalog由真实调用生成；legacy与active分开 |
| F09 | 经验健康/容量已部分预设高G成本，无完整实验验证 | 分开验证转运、瞬态、运动、展示/容量、附着水解和生长；不发布最优G |
| F10 | 批量→对象同步和子体追加漏Y_mem | 同步、继承、追加均修正；真实主循环分裂后检查所有数组和母子记忆 |
| F11 | 死细胞胞内糖随定期清理丢失，没有移出记录；浮点while可超出请求时长 | 新死/已有死行一次转移至残余移出账；固定步数，拒绝非整倍时域 |

## 8. 独立副本修订状态

独立仓库基线`98b12ff`保留原实现；修订说明见相邻仓库`simplified-v4/SCIENTIFIC_REVISIONS.md`。版本为`simplified-v4-reviewed-2026-09-29.1`，执行语义`accepted-flux-periodic-v1`。以下已实际修改并测试，未迁入CAD执行器。

| 修订 | 实际行为与证据 |
| --- | --- |
| 接受通量 | 先按体素分配U，再用J=U/dt更新EI；requested_J单独保留，J/consumption_rate代表实际接受通量。全活/部分死亡、充足/不足库存构造例均通过 |
| 扩散 | 默认改为`fft_discrete`：七点周期离散Laplacian的矩阵指数；旧`fft`仅显式选择保留。FD用ceil分步覆盖整个dt_total，dt=0不前进，负/非有限时长报错 |
| 边界和时钟 | 仅接受periodic；运动和子代位置使用Simulation实例domain。持续点源每step注入，全死step仍推进场；run遇灭绝明确提前结束并记status。末尾推进尚未结算的扩散时长 |
| INP数量 | 默认`copy_number`为dN/dt=k_exp f−k_deg N，分裂再按比例分割。`legacy_growth_loss`显式加入−µN作为额外损失；不再称数量的稀释。选择被写入metadata |
| 资源与死亡 | MAX_CELLS触发resource_limited，保留现有人口，不记随机生物死亡。死亡残余胞内糖转入`death_removed_substrate_molecules`并清零，每次只结算一次；政策`remove_without_lysis`不向胞外释放 |
| 分裂记忆 | 两处batch→object同步及新增子体数组均包含Y_mem；真实Simulation.step分裂验证母子V、胞内糖、INP总量与记忆继承，不仅手写比例恒等式 |
| 运行终点 | TOTAL_TIME必须为DT整数倍，按step_count驱动；metadata记录requested_time_s、actual_time_s、time_policy、complete和stop_reason，避免浮点while多走一步 |

新扩散的特征值为

\[
\lambda_{\mathbf{k}}=-\frac4{\Delta x^2}\sum_{a=x,y,z}\sin^2(\pi k_a/n_a),\qquad
\hat c_{\mathbf{k}}'=\exp(D\lambda_{\mathbf{k}}dt_d)\hat c_{\mathbf{k}}.
\]

七点生成矩阵的非对角元非负，行列和为零，故其矩阵指数在精确算术中保正且守恒；这是离散空间模型的性质，不是对连续热方程空间误差的消除。实现不裁负或整体重标，保留FFT舍入误差。检查容差为128ε乘实际场幅度，无固定1µM下限；细胞只采到近零尾部时携带全场幅度判断。1与10⁻¹⁵µM脉冲、显著负场反例均覆盖。原摄取结算的浮点末端裁零仍在，因此只能在容差内核对局部库存，不能声称逐比特或完整生物物质守恒。

Python3.11.9、NumPy2.1.2下：七个原有测试文件103项和`test_review_numerical_contracts.py`新增26项，共**129 passed（1.77s）**；新增26项另用真实Numba JIT运行，**26 passed（2.99s）**。禁用pytest缓存，PYTHONDONTWRITEBYTECODE=1；JIT缓存及输出均在独立Temp。新增测试包含Fourier模态、分步semigroup、脉冲质量/正性、非整倍FD、接受通量、零活细胞场、资源限制、INP政策、真实分裂、死糖一次结算和时域终点。原包103项通过却未捕获上述主路径缺陷，说明局部原测试不足以验证整条执行链。

这些测试支持所列数值/程序性质，**不构成实验校准、全尺度收敛、完整碳账或优化G结论**。纤维step0预付节拍、随机事件优先级、共享RNG、普通adder几何门槛、无细胞碰撞、膜与蛋白合成物料账、经验健康和参数迁移性仍未解决。将legacy_growth_loss或旧fft选回也不能恢复整个原包语义。

## 9. CAD注册拆分与迁入

已对照 [registry-contract](../../archive/legacy-protocols/registry-contract.md)、[numerics-and-modules](../../archive/design/numerics-and-modules.md)、[contracts-and-execution](../../archive/design/contracts-and-execution.md)。当前是N1：Catalog0.1.0、Module/Graph/Run0.1.0、Workspace0.3.0、legacy-explicit-v1。cad-next/1、propose/commit、显式读写集属于目标草案，不能塞入拒绝附加字段的当前Schema。

下列是候选ID，没有运行实现/安装承诺。cell scalar/vector须按同群组稳定ID对齐，fiber→cell与cell→grid必须有显式映射；旧合同无法表达的事务和实体关系可先封装为复合adapter，不靠数组长度猜身份。

| 候选模块 | 数学类别；输入→输出（单位） | 状态owner及最小验证 |
| --- | --- | --- |
| friskoli.init.founder_population | 离散初始化；seed/场景/几何→ID/位置/谱系 | 初始化owner；S1/S2数量和复现，无接触保证 |
| friskoli.transport.ascf_demand | 纯代数；c[µM],G_eff[1]→demand[molecule/(cell·s)] | 无状态；零糖/饱和/非法参数 |
| friskoli.inventory.voxel_allocate | 保守结算；demand/位置/grid→accepted amount/flux | 统一扣账/记账owner；争用/重排/守恒 |
| friskoli.signal.pts_ei_chey | ODE复合模块；accepted flux→e/Y/CheA | e/Y owner；内部可展开，分步与高精度reference对照 |
| friskoli.signal.adaptation_surrogate | 低通+限幅；Y→Y_motor | Y_mem owner；阶跃、基线、初值/继承 |
| friskoli.motion.run_tumble | 随机状态机；bias/黏附→position/heading | 唯一运动writer；run/角度统计、dt收敛、边界 |
| friskoli.surface.attachment | 几何+随机事件；位置/纤维→附着映射 | relation owner；距离、再附着、生命周期 |
| friskoli.source.cellulose_hydrolysis | 有限源；附着/INP/纤维库存→产物量 | fiber库存owner；零酶/空源/权重/当量账 |
| friskoli.field.periodic_diffusion | PDE算子；grid→grid | 场结算owner；周期边界不可冒充CAD已有no-flux |
| friskoli.growth.yield_inventory | 离散库存增长；U/n/V→µ/ΔV/消耗 | V与胞内量owner；量纲、零库存、最大增长 |
| friskoli.expression.capacity_inp | 几何代数+一阶ODE；V/G/µ→G_eff/INP/phi | INP owner；copies含义、容量与负担区分 |
| friskoli.life.health_surrogate | 经验ODE+hazard；µ/phi→H/death proposal | H owner；概率/步长，无生物标定宣称 |
| friskoli.life.surface_adder | 随机阈值/分裂；几何→子实体提案 | 周期/生命周期owner；copy/split/reset及膜材料边界 |
| friskoli.observe.recruitment | 观察；附着事件/谱系→招募/次数/新纤维数 | 只读科学状态、独立累计状态；分母明确 |
| friskoli.design.promoter_to_g | 设计代数；相对强度/拷贝→G | 不逐细胞逐步运行；出处和非法值 |

注册项至少记录：源hash、公式/符号映射、shape/quantity/unit/entity、active参数、数值与RNG、owner/继承、测试与科学证据分别标记。纯几何读取沿用world_access=read_only，不能被旧状态推断当成第二个运动或几何writer。

迁入顺序：先冻结原语义与修订差异；登记只读数学说明；做几何/需求/库存/局部生长；再实现接受通量与统一结算的独立执行版本；最后接入周期场、纤维、运动和生命周期，并做步长/网格/节拍、多seed和观测定义检查。机制、边界、时序改变均须版本化。科学包进入CAD前，另做许可证与实验适用条件核查。

## 10. 已核查原始/官方证据

访问日期2026-09-29；这是有限机制核查，不是系统综述。部分PMC直链返回验证页，使用PubMed原文摘要/图注或已检索正文片段；未取得的信息保持未知。

| ID | 来源与定位 | 支持与限制 |
| --- | --- | --- |
| E01 | Somavanshi, Ghosh & Sourjik (2016), Sugar Influx Sensing by the Phosphotransferase System of Escherichia coli. DOI `10.1371/journal.pbio.2000074`；[PLOS全文](https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.2000074)，Abstract、Results/Fig.3 | 所测PTS糖influx与趋化信号传递；不证明当前AscF-specific参数或两状态ODE精确性 |
| E02 | Neumann et al. (2012), Chemotactic signaling via carbohydrate phosphotransferase systems in Escherichia coli. DOI `10.1073/pnas.1205307109`；[原文](https://pmc.ncbi.nlm.nih.gov/articles/PMC3409764/)，CheB Phosphorylation Is Not Essential…/Fig.4与Discussion | 下游受体甲基化适应，CheB磷酸化非必需；不支持把3秒CheY低通视为真实甲基化网络 |
| E03 | Cluzel, Surette & Leibler (2000), An Ultrasensitive Bacterial Motor Revealed by Monitoring Signaling Proteins in Single Cells. DOI `10.1126/science.287.5458.1652`；[作者机构PDF](https://cluzel.fas.harvard.edu/sites/g/files/omnuum7156/files/cluzel/files/cluzel_science_00.pdf)，p.1652/Fig.1A | 单马达CheY-P/CW关系，Hill coefficient10.3±1.1；不验证本模型全细胞tumble hazard |
| E04 | Harris & Theriot (2016), Relative Rates of Surface and Volume Synthesis Set Bacterial Cell Size. DOI `10.1016/j.cell.2016.05.045`；[原文摘要/图注](https://pubmed.ncbi.nlm.nih.gov/27259152/)，Abstract/Fig.1,6,7 | 相对合成速率与过剩表面材料积累假说；固定半径surface-adder不等于原论文模型 |
| E05 | [NIST SP330 section 2](https://www.nist.gov/pml/special-publication-330/sp-330-section-2)，mole定义 | Avogadro精确常数6.02214076×10²³mol⁻¹；不产生任何生理参数标定 |
| E06 | Hall & Xu (1992), Nucleotide sequence, function, activation, and evolution of the cryptic asc operon of Escherichia coli K12. DOI `10.1093/oxfordjournals.molbev.a040753`；[原文摘要](https://pubmed.ncbi.nlm.nih.gov/1630307/) | ascF/ascB功能背景；本轮材料不支持K_S=30µM直接测量，原包已撤去这个旧归因 |
| E07 | Lin & Amir (2018), Homeostasis of protein and mRNA concentrations in growing cells. DOI `10.1038/s41467-018-06714-z`；[原文](https://www.nature.com/articles/s41467-018-06714-z)，Introduction及Results: Model | 区分protein number与concentration、体积增长及分裂；不为INP展示拷贝的实际合成/降解参数提供标定 |

没有文献来源的参数不会补造出处；作者旧文档的拟合声称也不替代原始数据。代码测试通过只能说明所选构造例，不等于真实工程菌行为已验证。
