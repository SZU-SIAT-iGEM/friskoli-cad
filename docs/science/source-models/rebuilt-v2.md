# rebuilt-v2：科学、数值与迁入审查

审查日期：2026-09-29。源目录：D:/Wu Shangru/Documents/iGEM/model-A-rebuilt-v2。独立修订副本：D:/Wu Shangru/Documents/工作区/friskoli-model-review/rebuilt-v2。原始目录只读；副本修订须与原始基线区分。本轮尚未将模型注册到 CAD 执行库，参数也未经本项目实验标定。面向 iGEM 读者的说明见 [Wiki 草稿](../../wiki/models/rebuilt-v2.md)。

## 1. 版本与证据规则

源目录无 Git 元数据，不能提供来源 commit。27 个文件的规范 SHA256 清单 hash 为 6f7bfc361389a6d7a5e269cd414025afe10fc5579f53cf1720af5866a4ffe219。清单按 POSIX 相对路径字典序，每行是小写 SHA256、两个空格、相对路径、LF；UTF-8无BOM。完整清单见文末。主 CAD 仓库读取起点为 feat/registry-contracts，HEAD 00eb3b9c08d84bb9ed6dd122090c5504d919be18。

检查源目录及相关祖先、主仓库未找到额外 AGENTS.md；遵循会话给定的工作区指令。源内没有 LICENSE 文件；发布包前需确认授权。README.md:3,49–51 自称是旧 model-A-rebuilt 的重构，删除短时分裂与有限 tumble 时长。本轮没有取得旧版逐项复算；155 行迁移表不能单独证明行为等价。另一份 ZIP 是否由此源整理，必须另做文件/方程对照。

本文分开标识：源码行为、源作者声明、本轮构造检查、原始论文依据和迁入建议。找到相似的文献数值不等于找到了作者的原参数来源。以下源码行号均指修订前的27文件快照，修订实现另见第9节。

## 2. 真实模型结构

~~~text
位置/纤维距离 → 黏附/脱附 → 接触水解
有限纤维库存 → 每细胞contact → 每纤维surface → 3D bulk
                    ↘              ↓             ↙
                       PTS请求/共享库存分配
                            ↓接受通量
                  EI → CheA → CheY-P → motor bias
                    浓度时间记忆 → tumble → 游动
                            ↓
                  胞内底物 → 生长/展示/健康/死亡
~~~

这是静态纤维、离散细胞与单溶质场组成的混合 ABM–ODE–PDE 模型。README.md:49 的120–600 s属于作者目标范围，未给相应收敛或标定证明。没有细胞分裂、MCP配体结合/甲基化方程、流场、沉降或微重力专属输运律。gradient_bias 比较当前浓度和时间记忆，不计算空间梯度。n_inp 同时代理展示数量、水解活性和黏附能力；三者在构建体中是否成比例尚未测量。

## 3. 实体、状态、单位和所有权

state.py:13–43 用 frozen dataclass 固定字段，但 NumPy 数组仍可写；它不提供深度不可变快照。当前细胞按行号、纤维按整数索引识别，无稳定cell ID和谱系。

| 状态 | 实体/shape | 单位/定义 | 原更新方 |
| --- | --- | --- | --- |
| positions_um、directions | cell×3 | um、无量纲单位向量 | update_cells/motion |
| alive、attached_fiber、attached_time_s | cell | bool、fiber索引（−1自由）、s | adhesion和lifecycle |
| contact_pool、intracellular_pool | cell | 连续期望molecule，非整数 | carbon和cells |
| ei_fraction、chey_p_uM、memory_uM | cell | 去磷酸化EI比例、uM、uM | signaling |
| volume_um3、n_inp、health | cell | um³、copy、0–1经验变量 | growth/display/health |
| bulk_uM | grid(nx,ny,nz), C-order | 体素平均uM | carbon和diffusion |
| starts_um、ends_um | fiber×3 | um，有限中心线段端点 | 初始化后静态 |
| cellulose_initial/remaining、surface_pool | fiber | 产物等价库存、可溶产物molecule | carbon/adhesion/death |
| hydrolyzed_total、uptake_total | global | 内部转移累计molecule | carbon；不能重复加入总账 |
| growth_used_total、removed_total | global | 消耗/移除的底物等价molecule | cells；纳入库存账 |

参数中的换算为 N=C V κ，κ=602.214076 molecule/(uM·um³)，由精确 Avogadro 常数推导 [R5]（parameters.py:180–182）。纤维素库存与cellobiose一比一扣加，没有显式聚合物重复单元化学计量。因此应称为 cellobiose-equivalent 底物账，不能冒充完整元素碳守恒。生长产率没有对应CO₂、ATP、维护代谢与其他组分账。

## 4. 按数学意义拆分及 CAD 接口

下列模块ID是提案，尚不可运行。C/F/G分别表示cell/fiber/grid。所有端口还需species、entity IDs、坐标/网格修订、时间角色；库存统一由事务结算器提交，禁止多个模块独立覆盖同一池。当前 CAD Catalog 0.1.0 和 legacy-explicit-v1 不能自动表达新执行语义；按 docs/registry-contract.md、docs/design/numerics-and-modules.md、docs/design/contracts-and-execution.md 另立合同/版本。

| 候选模块 | 数学及原源码 | 输入→输出/owner | 迁入必要检查 |
| --- | --- | --- | --- |
| rebuilt.initialize | state.initialize:66–91；lognormal展示、均匀位置、Gaussian归一化方向 | 参数+seed→C初态；initializer分配ID | 同seed复现、非法放置、初始化不穿固体 |
| core.geometry.spherocylinder | model/geometry.py:6–32 | C体积/r和F中心线h/r→面积/体积；只读 | 球极限、体积往返、最小球体积 |
| rebuilt.fiber_proximity | reference.fiber_scan:36–54；periodic查27镜像 | C位置+F线段→最近F/距离；只读 | 端点、零长度、周期镜像、实体映射 |
| rebuilt.adhesion | simulation.update_adhesion:52–96；展示/距离/拥挤/年龄hazard | 状态+draw→事件及返还提案；adhesion owner | 固定draw、脱附再附着、容量语义 |
| rebuilt.contact_hydrolysis | model/hydrolysis.py:10–31；simulation.py:118–133 | C展示/面积/浓度+F库存→molecule/s请求 | 零酶/零库存、可及性初值1、抑制单调 |
| rebuilt.pts_capacity | model/transport.py:16–23 | G表达因子+C面积→target/functional/excess copy | G=0、容量饱和、拒绝非法G |
| rebuilt.pts_demand | model/transport.py:26–50 | C三池浓度/接触比例/functional→三路请求 | 请求和≤capacity、空池、compartment身份 |
| core.inventory.proportional | model/transport.py:53–69 | 请求积分量+分组+库存→accepted/余量；事务owner | [8,8,3]→[5,5,3]、置换、负值、死细胞 |
| rebuilt.exchange | simulation.py:171–182 | C/F池+dt→contact→surface→bulk积分量 | 解析衰减、相消、空间支持固定的网格收敛 |
| rebuilt.ei_chey | model/signaling.py:10–29；串联ODE | C接受通量/旧e,Y+dt→新e,Y,CheA,bias；signal owner | 常输入解析解、范围、dt收敛、维度 |
| rebuilt.memory | model/signaling.py:32–43；ODE+指数门控 | bulk/旧memory/bias→新memory/bias | 浓度阶跃解析式、γ=0、τ特殊值 |
| rebuilt.run_tumble | simulation.py:207–223；reference.py:57–70；motion.py:6–18 | C bias/位置/方向/mask/RNG→运动提案；motion owner | hazard、单位向量、周期/反射、多seed统计 |
| rebuilt.carbon_growth | growth.py:12–19；Monod+有限量 | C胞内池/体积+dt(min)→used/体积；growth owner | ΔV=YV×used、耗尽、dead不变 |
| rebuilt.display | growth.py:22–37；门控Euler | INP/health/μ/面积→新INP；display owner | dt收敛、零表达、总copy与稀释语义 |
| rebuilt.health_death | health.py:10–23；simulation.py:231–263 | C health/μ/occupancy/excess/draw→health/death/转移 | 死亡只一次、物质返还、死后停止代谢 |
| core.diffusion.fd3d | reference.diffuse:12–33；六邻点显式FD+CFL子步 | G field/D/dx/dt/boundary→场；field owner | 常场、脉冲、质量/非负、Fourier模式 |
| rebuilt.diffusion.legacy_spectral | acceleration.py:24–39；连续波数平方乘子+截零 | periodic G→场；原始兼容记录 | 原反例、与修订算法区分、不能声称离散等价 |
| core.diffusion.discrete_semigroup | 独立修订acceleration.periodic_diffusion；六邻点空间矩阵的指数传播 | periodic G→场；field owner，数值版本单列 | 脉冲正性/守恒、解析模式、半群、FD时间细化；尚未迁入CAD |
| rebuilt.inventory_observer | state.py:94–103；main.py:16–30 | 全池/累计量→绝对及相对残差；只读 | 局部/全局账、零存活时统计缺值 |

源 capsule length 是圆柱段 h，CAD胶囊参数 L 是含端帽总长；显式转换 L=h+2r、d=2r。直接按字段同名连接会改变面积和体积。

## 5. 方程与数值时序

### 接触、库存与输运

设 E 为展示代理数量，f_c=min(1,A_contact/A_cell)，x=clip(N_remaining/N_initial,0,1)：

$$q=E f_c k_h\frac{(1+K_a)x}{x+K_a}\frac{1}{1+(S_c/K_I)^n}.$$

仅存活且附着者提出水解请求；qΔt按纤维库存同比例限量后进入contact。交换积分量为 N[1−exp(−kΔt)]，默认k_cs=2/s、k_sb=D_eff/δ²=125/s。交换单向，不按浓度差反向传质。整根surface混匀，bulk沉积全进纤维中点所在体素（simulation.py:177–182）；不能称为沿纤维均布源。

PTS将bulk+surface作为ambient；接触膜片读contact+ambient。接触膜片与其余膜分别饱和后按浓度分摊请求。这是重叠池暴露假设；每一路缺货后不会重分配闲置转运capacity。共享库存对请求r_i=J_iΔt分配 a_i=r_i min(1,N_available/Σr_j)，零请求另处理。信号必须读Σa_i/Δt。

### EI、CheY与时间记忆

$$\dot e=k_dJ(1-e)-k_re,\quad A=A_TK_{EI}/(K_{EI}+E_Te),\quad \dot Y=k_YA(Y_T-Y)-k_ZY.$$
$$B_0=Y^H/(K_M^H+Y^H),\quad \dot m=(S_b-m)/\tau,\quad B=\mathrm{clip}(B_0e^{-\gamma(S_b-m)},0,1).$$

e是去磷酸化比例；J为molecule/s，因此k_d实际需1/molecule，gradient_strength需1/uM。EI子步解析后用新EI计算CheA，再固定CheA解析推进CheY；这不是整个耦合ODE的解析解。memory先推进再计算差值，时序影响响应。

λ=λ_min+(λ_max−λ_min)B；事件概率1−exp(−λΔt)。每主步至多一次tumble；θ=max(Normal(68°,36°),0)，未限制≤180°；φ均匀[0,2π)。转向后沿新方向前进完整dt，不定位步内事件时刻。没有有限tumble时长、rotational diffusion或流体速度。

### 生长、展示与健康

dt_min=dt_s/60。μ=μ_max S_int/(K_g+S_int)，requested=μVdt_min/YV，used=min(requested,N_int)，ΔV=YV×used。限量后源返回的μ仍是需求速率，表达/健康继续读取该值。INP按N_new=max(N+[expression−(μ+k_turnover)N]dt_min,0)更新；n_inp在其他模块当作总copy，μN稀释项的生物定义仍需澄清。health为修复减占用毒性/过量PTS/饥饿的Euler式后截[0,1]，再按health-dependent hazard死亡；没有实验拟合记录。

### 一个原始主步（simulation.step:281–292）

1. 旧位置查纤维；脱附并返还contact；再附着，刚脱附者本步可重附着。
2. 旧面积/展示、新附着状态水解；以生产后的contact、交换前surface/bulk摄取；再contact泄漏、surface泄漏。
3. 接受通量→信号；移动前位置读已扣除/沉积的bulk→memory；采样tumble并移动。
4. 生长；新体积/旧展示算占用；展示更新；health仍读旧展示占用；死亡contact返surface、胞内剩余进入removed。
5. bulk扩散；时钟增加dt。死亡释放到surface的物质等到下步carbon才外泄。

原初始化用seed，run每次新建seed+1的一个default_rng，所有事件共用它；数组顺序和事件数改变随机数消耗。state原不存RNG。步数为round(seconds/dt)，非整倍数会舍入时长。新CAD语义必须单独命名，不能把现有legacy-explicit-v1套上来宣称结果保留。

## 6. 参数来源与科学适用条件

参数目录有89个有效输入、6个派生量；旧155项CSV记录迁移去向，不含逐项实验条件/原始论文/拟合误差。下列是源码默认值。evidence_baseline是情景名称，不能赋予已验证的证据等级。

| 参数（parameters.py原行号） | 默认值 | 证据与待测项 |
| --- | --- | --- |
| domain 19–31 | 200×200×100um；dx5um；dt.01s；100cells/3fibers/seed42；periodic | 场景/数值选项；需细化、边界与放置检查 |
| transport 34–44 | N0=500copy；Km30uM；turnover20/s；Dwater650um²/s，ηrel1.3→Deff500 | 无构建体实测出处；transport/代谢/信号不能互相代证 |
| signaling 47–60 | EI6/CheA5/CheY8uM；kd.001/molecule；kr20/s；KEI.3uM；kY2/(uM·s),kZ10/s | PTS与趋化联系有R1；这套方程与参数未标定 |
| motor/memory 57–69 | Hill10.3、half3.1uM；τ3s、γ2/uM；v25um/s；λ.1–10/s；θ68±36° | R2支持陡峭motor响应；已核查摘要不够追认精确10.3/3.1；R4不测本源τ/γ |
| fiber 73–96 | r2.5um/h60um；边界层2um；接触.20um²×.05um→.01um³；每纤维1e12等价分子 | 几何/库存情景；接触支持和实际活性待测 |
| hydrolysis 83–107 | Ka.3；展示均值5000/CV.35；KI170uM,n1；kh.04/.40 /s | R3的Cel7A/BMCC观测常数相似；源未引文且不能无条件移到表面展示菌 |
| adhesion 77–92 | shoulder.25/decay.50um；enzyme half2000；kon1/koff.005 /s；年龄300s；bond half50；crowding600 | 全为经验闭合关系；停留、接触概率、容量待测 |
| growth 110–129 | Vbirth.5um³,r.4um；μmax.005/min；Kg10uM；干重2.8e−13g/1.15um³；MW342.30g/mol；Y.50g/g | 公式可验算；温度、培养条件、构建体与yield来源缺失 |
| membrane 132–147 | basal内外.5；AscF3e−5/INP5e−5um²；PTS可用膜上限.55；表达800copy/min；turnover.03/min | footprint/比例/门控函数没有逐项证据 |
| health 150–166 | H0=1；repair.01/min；occupancy toxicity.20/min；excess.03/min；starvation.002/min；death max1/min,Hhalf.05 | 探索规则；不能称实验死亡概率 |

R1在E. coli中发现PTS信号与受体–CheW–CheA复合体及共同适应相联系，不直接证实工程AscF/cellobiose链路和单独EI抑制式。R2测单motor响应，不直接验证整菌tumble线性映射。R4支持时间比较概念，实验体系不同，也不支持该源τ=3s的数值。KI是条件依赖观测量，R3/R6都提醒底物和反应状态不能忽略。故此次修订保留生物参数，不补造测量值。

## 7. 原始版有界检查

环境为Windows、Python3.11.9、NumPy2.1.2，Numba可导入。Temp执行、Python字节码关闭、pytest cache关闭、Numba cache指定Temp；未安装依赖、未跑长模拟/性能benchmark。源作者docs/VALIDATION.md的5tests/53倍性能属于作者记录；本轮独立复跑原5tests，5 passed in 1.09s。

| 输入/检查 | 原始版实测结果 | 解释 |
| --- | --- | --- |
| 两细胞手动附着fiber0；reference .05s | hydrolyzed1.078389553051448、uptake.10494928524455313；绝对库存残差0 | 仅此极短构造例子通过 |
| 两细胞alive=False，bulk全1uM；carbon再cells，dt.01s | 摄取6.451612903225806；生长消耗.7640210529442967molecule；仍dead但各ΔV约4.459e−10um³ | 死亡后代谢错误 |
| reflective位置(199.9,100,50)，方向(1,0,0)，v25、dt.01 | x=200，方向仍+X | clip并非镜面反射 |
| 8³periodic unit impulse，D500、dx5、dt.01 | FFT原sum1/min−.00367334948、252负格；截零sum1.09173890065；FD sum1/min0 | 截零增量9.17389%，非舍入噪声 |
| 两细胞seed42，tumble min=max100/s；run(.05)对run(.02)再run(.03) | max位置差.7438485763um，方向差1.7583780459 | 重置RNG破坏续算；默认无事件小例相等不证明可续算 |
| dt=NaN调用validate | 通过 | finite检查缺失 |
| fiber_count=0，1cell，reference .01s | 空surface池IndexError | 无纤维对照不可运行 |
| positions_um.flags.writeable | True | frozen不等于数组只读 |

复现记录位于系统Temp的friskoli-rebuilt-v2-audit-20260929目录。第一次hash探针路径未解析到文件，其空清单结果废弃；改用已导入parameters.__file__定位后得到本文27文件清单。全账初值约3e12，只有相对残差会掩盖局部小池损失；必须加绝对残差及相对本步转移量的检查。

## 8. 原始版问题与迁入门槛

| 编号 | 原源码定位/问题 | 修正或限制 |
| --- | --- | --- |
| R01 | simulation.py:146–168摄取、225–234生长表达/健康未mask dead | 存活状态贯穿全部生理更新；残体行为单独机制 |
| R02 | motion.py:16–17 reflective仅clip | 明确点运动镜面边界；胶囊/纤维碰撞另做 |
| R03 | acceleration.py:38谱负值截零增质 | 更换守恒正性可说明的离散算子，独立数值版本 |
| R04 | simulation.run:299重seed；state缺随机状态 | RNG快照进状态；split run对照；分模块流是另一语义 |
| R05 | simulation.py:135–143、177–182整根混匀及中点沉积 | 保留且声明降阶假设；沿纤维分布不是单纯加速 |
| R06 | adhesion只看中心距离；初始化未排固体；无排斥 | 不宣称物理接触求解；需新几何/放置模块 |
| R07 | simulation.py:87–91拥挤按旧count同时采样 | 600是软门控参数，不能宣称严格容量上限 |
| R08 | parameters.py:191–207非法值漏检；grid_shape用round | finite/合法域/网格整除；零纤维正式支持 |
| R09 | state.py:94–103只追踪等价底物；全局相对分母太大 | 元素账另建；局部绝对/相对容差 |
| R10 | 无逐参数证据；独立memory可能重复体现适应；copy稀释含义不清 | 明确假设、消融、构建体测量，不改成任意新默认 |
| R11 | main.py:26–27无存活均值填0；run时长round；README说无Numba走reference而代码仍FFT | 统计缺值、时长规则、实际backend都要准确报告 |

原FD的六邻点Laplacian与原FFT连续波数平方不同；即使关闭clip也不是同一离散系统。源docs/MODEL_VS_ACCELERATION.md:28–31已承认数值差异。纯Numba几何/旋转才更接近等价实现替换，仍需容差对照。

## 9. 独立副本修订状态

用户已授权并已完成独立副本的正确性修订。原样基线commit为8b8bb76fea4094dc3b5d2d1539ad8a6db791c3dd；副本以review-correctness-v1标识行为，数值扩散标识discrete-laplacian-semigroup-v1。源原字节及Git blob的SHA256分别记录在friskoli-model-review/rebuilt-v2/SOURCE_BASELINE.json；Git只对两张CSV做CRLF→LF归一化，其余字节匹配。原始外部27文件再次核对hash全部未变。修订结果保存在同副本REVIEW_VALIDATION.json及docs/REVIEW_2026-09-29.md。

| 修订 | 已实现行为和回归结果 | 兼容边界 |
| --- | --- | --- |
| R01死亡状态 | 摄取全路alive过滤；dead信号、记忆、体积、展示、健康冻结；强制死亡返还/removed仅一次。原反例摄取与生长均0 | 未建残体代谢机制，冻结是当前明确政策 |
| R02反射 | point位置按2L周期三角波折回，多次越界保留剩余位移，逐轴翻转heading；反例变为x199.85、−X | 改变旧clip轨迹；不是胶囊与固体碰撞 |
| R03扩散 | 六邻点离散Laplacian精确时间半群；unit impulse总和0.9999999999999999，最小7.3127354866e−13 | 替换原连续波数离散，不与原数值逐值等价；reference仍显式Euler |
| R04续算 | 状态保存PCG64 bit-generator字典；run恢复，step深拷贝新状态；原强事件反例split/full全部状态与RNG一致 | 同参数/实现可分段；尚无版本化checkpoint文件格式；随机流仍共享 |
| R08合法域 | finite、整数数量、分数范围、网格整除、最小几何检查；0fiber对照可运行且守恒 | 过去被默许的非法输入现在拒绝；模型合法不代表实验合理 |
| 总copy语义 | n_inp保留总数量，只减原turnover；体积增长不再额外扣−μN；无表达/turnover时copy保持5000 | 明确改变源展示动力学；浓度稀释应由N/V或面积密度得到，[R7]支持数量/浓度区分 |
| 实际增长率 | 下游接收used×YV/(V_start dt_min)，不把未满足营养需求当实现的增长 | 仅限量时明显改变健康/表达输入；仍是显式分步近似 |
| 运行和统计 | 非整dt时长拒绝；无存活均值为null；显式reference优先环境变量 | CLI/API缺值与时长规则改义，旧输出按基线解读 |

2026-09-29从Temp对独立副本运行原5项加新增30项，**35 passed in 3.74s**。新增验证包含解析Fourier模式、扩散半群组合、reference时间细化、真实附着源5步accelerated组合账、dead状态、强制死亡转移、反射边界、RNG全状态一致、非法参数、0fiber、总copy和accepted增长率。组合账比较已水解量与mobile库存，不用约3e12未反应库存掩盖误差。这些测试没有验证长时预测、空间收敛、多seed结论或实验准确性。

数值修正依据可直接推导：六邻点L_h的非对角元非负且行列和为0，exp(tD L_h)因而保持非负、常场和总量。其Fourier特征值为−4Σsin²(πk_a/n_a)/dx²。实现仅容许64epsilon×场幅度以内的负舍入，超出即报错；舍入投影同时保持原总量，不再靠无界截零掩饰不适用的传播算子。

总copy修正依据c=N/V的微分关系dc/dt=(dN/dt)/V−μc；增长稀释属于c，不能无证据从N中再扣μN。[R7]将protein number与concentration明确分开，但不证明本构建体表达门控或turnover的数值。生物参数默认值均未更改。surface混匀/中点源、接触几何、软拥挤、额外memory和健康规则继续作为限制披露；未把这些未测假设悄悄改成新经验值。CAD迁入仍未执行。

## 10. 迁入与标定顺序

1. 来源锁定：hash、许可证、方程、参数、数值版本分别保存；Catalog只登记planned/不可执行状态。
2. 几何/类型：h↔L转换；C/F/G实体对齐、species与quantity；molecule/s和molecule不可互接，未知尺寸阻止接触求解。
3. 确定性碳链：水解→请求→统一allocator→交换→FD；accepted量同时喂胞内和信号，逐池守恒；先原时序对照再发布修订profile。
4. 信号/运动：EI–CheY保留复合solver；memory可见；固定draw测试与多seed统计；反射和瞬时tumble语义明确。
5. 生长/生命周期：死后冻结与物质返还在事务提交；未实现division就禁用，未来copy/分配/浓度继承不可全平均。
6. 数值检验：固定域/源物理支持/观察区域，细化dx、dt；累计水解、摄取、到达/停留/存活和局部残差都有预设容差；后端比较以收敛和统计为准。
7. 实验标定：构建体摄取曲线、展示活性/抑制、黏附停留、表达负担及趋化响应；缺数据时仅作情景比较，不给“最优G”或性能提升结论。

对照应包括无活性水解、G=0、memory关闭、EI–CheY调制关闭、固定tumble、无黏附、数值后端；G=0同时取消营养和信号，差值不能单独归因趋化。每次结果保存实现/参数/输入/plan hash、随机算法与状态、实际backend、dtype、步长、物理支持和观测定义。

## 11. 已核查原始来源

核查于2026-09-29。每项仅支持标明的范围，未把本轮后补文献当作源码已有参数出处。

- [R1] Neumann S, Grosse K, Sourjik V. PNAS 2012;109:12159–12164. Chemotactic signaling via carbohydrate phosphotransferase systems in Escherichia coli. DOI 10.1073/pnas.1205307109。[原文](https://pmc.ncbi.nlm.nih.gov/articles/PMC3409764/)。支持PTS与复合体/共同适应，不标定本构建体。
- [R2] Cluzel P, Surette M, Leibler S. Science 2000;287:1652–1655. An ultrasensitive bacterial motor revealed by monitoring signaling proteins in single cells. DOI 10.1126/science.287.5458.1652。[原论文记录及摘要](https://pubmed.ncbi.nlm.nih.gov/10698740/)。已核查摘要支持陡峭motor响应，不据摘要追认精确10.3/3.1或tumble映射。
- [R3] Kuusk S, Sørlie M, Väljamäe P. JBC 2015;290:11678–11691. The predominant molecular state of bound enzyme determines the strength and type of product inhibition in the hydrolysis of recalcitrant polysaccharides by processive enzymes. DOI 10.1074/jbc.M114.635631。[原文](https://pmc.ncbi.nlm.nih.gov/articles/PMC4416869/)。14C-BMCC上Cel7A的观测抑制0.17±0.02mM；不等同真实结合Ki，也不证明表面展示菌适用。
- [R4] Macnab RM, Koshland DE Jr. PNAS 1972;69:2509–2512. The gradient-sensing mechanism in bacterial chemotaxis. DOI 10.1073/pnas.69.9.2509。[原论文摘要](https://pubmed.ncbi.nlm.nih.gov/4560688/)。支持时间响应与恢复的概念，不提供本源τ/γ。
- [R5] BIPM. [SI mole官方定义](https://www.bipm.org/en/si-base-units/mole)。NA=6.02214076×10²³/mol为固定精确值。
- [R6] Teugjas H, Väljamäe P. Biotechnology for Biofuels 2013;6:104. Product inhibition of cellulases studied with 14C-labeled cellulose substrates. DOI 10.1186/1754-6834-6-104。[原文](https://pmc.ncbi.nlm.nih.gov/articles/PMC3726336/)。支持抑制常数受底物和温度影响的限制。
- [R7] Lin J, Amir A. Nature Communications 2018;9:4496. Homeostasis of protein and mRNA concentrations in growing cells. DOI 10.1038/s41467-018-06714-z。[原文](https://www.nature.com/articles/s41467-018-06714-z)。Results及c=N/V关系区分总数量与浓度；不用于标定本源表达或降解率。

## 附录：源SHA256清单

~~~text
3bbe3f427a22345d3293400213c6effb66ef9e9a5e69d0ba08e4ba18e3c82edd  README.md
440bf29a714618128d7047b056cf2e092955d7591c95a4442108ccad99901823  acceleration.py
541a63ad538cdb65cf209db4a4c36b31f0cb4cb46315c48a0627ace5e6218a14  benchmarks/benchmark_backends.py
8762117cf14cb3d4dfd573086b3803fee9d17d7042c7a4efb826c7fa555b288f  docs/MODEL_VS_ACCELERATION.md
1806a009502326b00bf62eae2f31572ca8324a7e6870888c648dc0df0b8519f3  docs/PARAMETERS.md
c42db74e74161cb31d8d933a4639d8736d5c6ff090392dc30c258827131a2547  docs/VALIDATION.md
a9a6f511f067a2f26dfa8e73e5901e3328a0a6b26dc90c159922cf1370574ba2  docs/current_parameters.csv
5695e4f9443753ec39386064fa694294465dac53cc98e68da0937c007f2272e5  docs/parameter_migration.csv
b40659f6c441749875986710654c6ccd9ea6a1fa2f1bf15b393925988ff8de1f  docs/wiki_model_page.md
d2ad31d5e75698a6a0c63b77fa26c1c531d4de1a48ddf58216f406c37d85ad11  equations.py
4f4e1343d890b45dbcf78d09f8e1e22bd09282e5532b35c78dde6de63c6902c2  main.py
e0fdfd334af4ee2319e9d1da6029a3168e0204dabc6a5872ef1df8974ee280cc  model/__init__.py
eab8b401a199355f0c36f8900664fb350ae4034ab1918b8f2b7f1b1a03726f3e  model/geometry.py
26e028cd023c4c9431ea5b793122d40e07bd015a68b4c636911cb385525b1040  model/growth.py
afaa4fa23b3d65f6afa43f674c6c666f55c791c90eeb9014e412f100e42361b2  model/health.py
d56c548e9c2f8e5363c779827b87748c4531203266845f3504207d7100a3052a  model/hydrolysis.py
48ea00d00a833910c7e9546762b8cb983dccaf2560d8a3235b485c2220834b00  model/motion.py
e7454db51c8d4df9f6816e8ce3bab9f6d19a753db3232a0d51033e9d9e8c57ef  model/signaling.py
b16c9ba2a98305411ead9e8bad0b30fcbdb8e0a15021f0385f2522a7f7f80f47  model/transport.py
41633accd4599ba24ddda954f520d6ab721e6d6bd42c74a1147f119126add7fd  parameters.py
9c894f5e90fdfaaa39a3bdfb703a820e7786c36e45bf41acf81ae1a93027a0af  reference.py
03f6498c697bfb8d3416c8edc414d34326d620906039a3b4bb10d57d1fec230e  requirements-accelerated.txt
a83a2a4ed7ab2e022b9b8a83cfa2b8cf52cc243006c597e8b96f90a8365590ac  requirements.txt
b93f05c45e29ecdca60233238e0dbbaa5495921e201f3e964bae24edebea0ddd  simulation.py
eda69d72cbbe875ac40734f58bf4cbea809a64688e8fb73864417a4a28d896e9  state.py
bcfbba6fa856757645e053ae5e07aedfb5db9029413c0af347356f53454cc315  tests/test_model.py
5ab6f38398c311a7757518988e89e214bcb57cb3e05ac6ac7af059d8cfd1a29f  tools/build_parameter_catalog.py
~~~
