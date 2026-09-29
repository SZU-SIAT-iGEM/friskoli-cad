# 两套来源模型的 CAD 迁入计划

日期：2026-09-29。状态：来源代码审阅与实施建议；本文件没有新增运行模块，也没有变更已验收的内核。两个来源按独立模型处理，不能仅用同一方程的一组参数代表它们。

## 1. 范围与证据

- A：`D:/Wu Shangru/Documents/iGEM/model-A-rebuilt-v2`，逐文件核查，不修改来源。
- B：`D:/Wu Shangru/Documents/工作区/model.v4_simplified_v1_3(1).zip`，ZIP 内根目录 `model.v4_simplified_v1_3/`。以归档内当前主运行路径为准，不用旧说明推定实际机制；读取归档成员而不向主库复制源码。
- CAD：`feat/registry-contracts`；本轮开始时 HEAD `00eb3b9c08d84bb9ed6dd122090c5504d919be18`，另有 N1 工作区改动，基线合同见 [注册目录](../registry-contract.md)、[数值运行与模块库](../design/numerics-and-modules.md)、[协议与执行](../design/contracts-and-execution.md)。
- 原来源指纹：A 的 27 文件 SHA 清单 hash 为 `6f7bfc361389a6d7a5e269cd414025afe10fc5579f53cf1720af5866a4ffe219`；B ZIP SHA-256 为 `a3900092cb03a9d686800ca3c9450a2ce09fd5470c921b416b5aae5d0b63a5c2`。复核修订另在 `D:/Wu Shangru/Documents/工作区/friskoli-model-review/`，复制基线提交分别 `8b8bb76`（A）与 `98b12ff`（B）；主库不承载两套完整源码。
- 来源详细审阅见 [rebuilt-v2](source-models/rebuilt-v2.md) 和 [simplified-v4](source-models/simplified-v4.md)。以下源码行号指本轮所读快照，函数名作为稳定定位补充；代码行为不表示生物学已验证。

本计划提出迁入边界、状态归属、数值顺序和验收样例，并记录独立修订副本的交叉审查。参数的实验依据、物种特异性与生物有效性需单独追溯原始资料；不能因为一个来源较新、测试更多或命名为 simplified 就认定它更正确。原始 A/B 指纹已由本审查再次计算，与两来源报告一致。

## 2. 原始来源中已独立核查的关键差异

本节描述原始 A/B 快照；已修正的行为与复测结果见第 10 节，不把原始缺陷当作修订副本的当前状态。

| 内容 | A rebuilt-v2 | B simplified-v4 | CAD 决策 |
| --- | --- | --- | --- |
| 可溶产物 | 水解进入每菌 contact；随后 contact→fiber surface→bulk，独立库存与接触体积 | 当前 `surface_pool` 模式实际调用直接源，产物按附着菌酶权重进入菌位置体素；`surface_S=0` | 两种不同运输假设，分别命名，不默默恢复 B 残留的旧三池函数 |
| PTS 信号 | 先分配可用物质，再以实际总摄取/dt 驱动 EI/CheA/CheY | 先以浓度生成请求 J 并推进 PTS，再限制实际摄取；胞内只记实际量 | 共用量纲可共用合同；请求/接受通量不同，信号策略须独立版本 |
| 感知适应 | bulk 浓度 memory 改写 motor bias | CheY-P memory 后生成 motor effective signal | 不合并为一个无语义的 memory 端口 |
| 生长与分裂 | 胞内底物、Monod 与 yield 限制体积生长；主路径没有分裂 | uptake-yield 限制生长、面积 adder、随机且几何受限的分裂 | 两套增长律和生命周期都要独立测试，不能替换现有 length adder |
| 死亡 | 死菌保留；运动/附着屏蔽，bulk 摄取及生长路径未全屏蔽 | 主路径按 alive 处理；运行上限还会随机移除活菌并登记 death | CAD 区分 death、数值失败、资源上限；禁止资源不足伪装死亡 |
| 周期扩散 | reference 有限差分子步；加速 FFT 后截零 | FFT 后截零并恢复原总量 | 不宣称 FFT 与有限差分离散等价；总量修正不证明局部误差可接受 |
| 边界 | reflective 移动仅截断坐标，无方向反射 | 主 batch `motion_step` 无条件取模，忽略 reflective 配置；分裂却调用配置边界 | 保留 CAD 已实现反射；新模块须声明支持边界，未支持则拒绝运行 |
| 随机与续算 | `run()` 每次重置 seed+1，状态不含 RNG | 单个 Simulation 拥有 RNG，未据此证明序列化续算 | 新 profile 需保存 RNG、时钟、ID、事件和全部状态 |

独立源码定位：A `simulation.py:update_carbon` 106–194、`update_cells` 197–264、`step` 281–292、`run` 295–303；`model/transport.py:compartment_requests` 26 起；`model/motion.py:move` 11–18。B `core/simulation.py` 576–610、649–685；`core/batch.py:pts_step` 532–583、`fiber_hydrolysis_direct_source` 1140–1190；`core/environment.py:_update_fft` 138–151。

不能遗漏的共同与新增内容：

- **共同骨架：**几何→有效 PTS 容量→饱和摄取请求→EI/CheA/CheY→motor bias；有限可用底物、胞内库存、附着/展示酶也同时存在。共同接口不等于共同参数或离散轨迹。
- **B 的简化：**去掉主路径 contact/surface 溶质池和 A 的产物抑制/非线性 accessibility gate，改为剩余纤维比例与展示酶总量的直接 bulk 源；胞内生长从 Monod 改为 yield-limited。A 水解定位 `model/hydrolysis.py:rate` 17–30；B 定位 `core/batch.py:1140–1190`。这些是机制变化，不能列为缺漏函数修复。
- **B 的新增：**CheY-P adaptation、明确 run/tumble 驻留时间、面积相关分裂与随机子体分配；A 的 tumble 为瞬时改方向后在该步移动。B `model_a_behavior/motion.py:13–61` 与 A `simulation.py:207–223` 可对照，给两者相同 hazard 不会产生相同有效速度。
- **负担量不同：**A `model/growth.py:32–37` 按 copies×footprint/可用面积定义占据率；B `membrane_occupancy.py:99–111` 的 `phi` 是目标 PTS 超过基线量相对于可用容量的比例，可大于 1；`phi_inp` 仅为归一化诊断。不能统一显示成“膜面积百分比”。
- **计数语义待修正审查：**两来源 INP 都以总 copies 参与几何占据和水解，却把 `−μN` 与真实 turnover 混写；B 还在分裂时按份额分配 copies（`core/agent.py:478,490`）。见第 9 节，纯体积稀释与总 copies 减少必须分开。

## 3. 现有 CAD 能承接什么

当前 Catalog 0.1.0 描述模块、端口、数学说明与 `world_access`，并固定标识 `legacy-explicit-v1`。它不是任意状态调度器，登记说明不会赋予新求解能力。Graph 中的 `same_step` / `previous_step` 仍决定数据时间。

| 迁入对象 | 合同与状态 | 当前能否作为只读模块 | 前置工作 |
| --- | --- | --- | --- |
| 胶囊位置、面积、体积 | `geometry.capsule_readout@1.0.0`；position um，area um²，volume um³ | 已实现，`world_access=read_only` | 延用总长包含端帽定义与同群组检查 |
| PTS 静态容量、请求通量、无记忆响应曲线 | 浓度 uM、面积 um²、copies molecule、请求 molecule/s；无状态 | 可新增纯计算/只读模块；只是计算，不扣库存 | 单位、参数出处、零值和非法输入 fixture |
| 外部场在菌位置的采样 | `field.sample_box_support@2.0.0`，显式 position；species/quantity/shape/unit 匹配 | 已有采样机制，可复用 | 采样支持不能替代附着、接触或反应面积 |
| 有限源、contact/surface/bulk 交换 | 环境库存、fiber ID、接触库存、接受通量与账本 | 不可仅登记为读取器 | 唯一库存 owner；守恒分配器；surface/fiber 实体域和映射 |
| EI/CheY、memory 与 motor bias | e 无量纲，CheY-P uM；浓度 memory 与 CheY memory 独立 | 有状态信号机制，不能称无状态 readout | 明确信号使用请求或接受通量；初始化/分裂/死亡/checkpoint 策略 |
| 随机 run/tumble | position+heading 唯一 owner，run/tumble 状态与 RNG | 不可冒充只读；也不可并联第二个 pose owner | RNG 分流、回滚、统计与边界验证 |
| 营养生长、INP、health、death、division | 几何、分子数、内含物、事件、ID | 需要明确 owner 和状态迁移 | 接受摄取先结算；生物死亡与资源错误区分；几何约束 |

当前 `runtime.py` 299–300 只跳过显式 read_only 的 pose/geometry/division writer 推断；其余由输出名称识别且每组只允许一个 owner（303–347）。`read_only` 是 World 写入权限，不是完整科学状态所有权声明，也不意味着任意有状态信号已经有继承与续算支持。

两来源几何中的 cylinder length 是圆柱段 h，CAD 的 length 是含端帽总长 L。adapter 必须显式做 `L=h+2r`、`d=2r`，并拒绝 `V<4πr³/3` 的固定半径几何；不能因同名 length 直接连线。

## 4. 执行语义必须分开

### 4.1 保持 legacy-explicit-v1

编译器按 same-step 依赖拓扑排序，再用 phase/node ID 选可执行节点；previous-step 不约束当步拓扑（`compiler.py` 50–95）。runtime 对 same-step 读本轮输出，对 previous-step 读前轮输出（448–474）。现有 phase 2 运动/生长改变 working World，phase 3 几何读取，phase 4 采样，phase 5 产生下一段通量。

`uptake.linear.advance` 将旧 `uptake_flux × dt` 累加到摄取量，同时由新局部浓度算下一段通量（`modules.py` 436–445）；`field.local_inventory` 遇负库存拒绝步骤（126–130），不是资源限制后向细胞回传部分通量。分裂在全图执行后进行，继承目前只有 copy/split/reset，随后 refresh 不得改状态（`runtime.py` 543–601）；最后才提交 World、状态、时间与 ID（666–681）。

因此不能只把源码函数套进 phase 5 就宣称得到 A 或 B 的原时序，也不能为两来源新增机制偷偷改旧图的边 timing。旧项目、示例、轨迹、事件和浓度数组保持回归。

### 4.2 拟议新 profile

建议独立名称 `conservative-explicit-v1-draft`，这是设计标签，不是当前 API 可接受值。先提交新合同/profile 的正反例和 capability；旧合同保留。不把 profile 名塞入 `additionalProperties:false` 的现有 Schema。

1. 固定步起点位置/支持、库存、模块状态及 RNG；日程边界先处理。
2. 从同一快照生成源/反应/摄取请求，记录 interval `[t,t+dt]`、molecule/s 与 molecule 的区别。
3. 按库存 owner 统一结算；默认拒绝非法步骤。显式启用比例分配时，同一接受通量同时扣场、记胞内、驱动已声明的信号。
4. 从已约定输入推进 EI/CheY/memory，再提议运动、生长、表达和生命周期；强耦合留在复合机制内部，不能靠图节点顺序解代数环。
5. 检查库存、几何、alive 策略和有限性；统一提交场、状态、事件、ID、RNG。拒绝或缩步必须回滚随机流和所有临时提案。
6. 分裂/死亡改变实体集后重新对齐 ID 并生成观测。输出间隔不能改变数值步和事件判断。

**信号选择：**参考 profile 默认使用接受通量是保守数值设计，尚不能单凭代码论证它在全部 PTS 条件下的生物真实性。若为复现 B 保留请求通量驱动信号，应以独立 named policy 标注；必须有“库存不足时两者分离”的对照。

## 5. 下一步 N3：最小科学链

先做静止、不生长、不分裂、不死亡、无 fiber 的最小链：**有限均匀 bulk → 固定位置与显式支持采样 → PTS 请求摄取 → 共享库存结算 → 胞内累计量 + 接受通量驱动 EI/CheA/CheY → 只读信号曲线**。选择这一链只为先验证物质与信号的连接，不提前承诺完整趋化、生长或纤维降解有效。

链的第一步可先以独立 reference harness 与 fixture 实现，不修改 legacy runner；profile 与事务验收完成才开放到 CAD 运行。参数只来自明确锁定的来源快照或标为构造验证，不发布未经证实的生物默认常数。

## 6. 按数值依赖拆分实施包

下列路径、fixture 名和新模块名都是建议，不表示本轮已建立。每包提交应只包含该包的合同/实现/测试/说明；前一包不通过，不以增加更多生物机制代替排错。

| 包 | 具体产物与依赖 | 完成条件 |
| --- | --- | --- |
| M0 来源与参数锁 | 原始快照指纹、修订 commit/diff、方程/参数 evidence ID、原始与修订 profile 标签；最小机器可读输入及输出 | 能重放原反例；未知参数显式 unknown；测试数据标 constructed 或 source-derived |
| M1 无状态公式 | 依赖 M0；`pts.capacity`、`uptake.pts_request`、信号 steady-state readout 草案；只用显式输入，无扣库存、无运动写入 | 0 浓度→0 请求；面积/容量边界正确；单位/物种/群组不匹配拒绝；原来源纯函数对照 |
| M2 结算 reference harness | 依赖 M1；一个库存 owner 统一处理多菌请求；requested_flux、accepted_amount、accepted_flux 分开；以接受通量推进EI/CheA/CheY；记录 support at step start | 单体素手算、多菌排列不变、耗尽、跨支持检查和物质账通过；拒绝通量后胞内与信号均不提前提交 |
| M3 独立 profile 与编译合同 | 依赖 M2；新 profile 能力协商、唯一 owner、propose/commit、诊断、回滚；旧 graph/manifest 维持原义 | M2 链可由 CAD 图执行；旧 legacy fixtures 逐项回归；非法 same-step 环、重复 owner、未知 temporal 拒绝 |
| M4 时钟、随机与续算 | 依赖 M3；RNG 按模块/群组/稳定实体分流；checkpoint 含版本、状态、RNG、日程位置、ID、待发事件、扩散累计时间 | 连续/分段/保存恢复一致；失败再重试不多耗随机数；改变保存帧间隔不改机制；不以重新 seed 代替恢复 |
| M5 运动与感知对照 | 依赖 M4；A 瞬时 tumble 与 B dwell-time 两 profile；位置唯一 owner；memory 单独命名 | 无梯度无定向漂移；关闭信号基线；趋化与单纯速度变化分开；边界与驻留统计通过 |
| M6 纤维与产物 | 依赖 M2/M3/M5 所需部分；fiber/surface 实体域、接触、有限固体库存；先 direct bulk，再独立三池 profile | 固体反应量与声明等效碳账一致；无酶/无接触/无纤维归零；脱附/死亡池转移不丢失；网格与支持独立收敛 |
| M7 生长、表达、死亡和分裂 | 依赖 M3/M4/M6 及证据审查；count/浓度/面积三种状态明确；材料与营养账；生命周期迁移 | 分裂总量守恒、胶囊可行；死亡后状态规则明确；资源上限无随机死亡；INP 单位冲突解决后才启用 |

下一轮 N3 的范围仅为这条最小链及 M0–M3；M4 先保留完整状态保存要求，但不为静止无随机链引入不必要随机机制。M5–M7 是后续包，本次不启动；修订副本即使已有测试，也必须重新通过 CAD 的端口/owner/时序验收。

## 7. 必须随实施提交的 fixtures 与反例

每条 fixture 保存：版本锁、单位、domain/grid/support、实体 IDs、初值、dt 与步数、参数来源、期望不变量/事件、绝对与相对容差。计数可作为连续平均值；若声明整数随机分配，则另测整数与总量守恒。以下数字均为构造验证值，不是生物参数建议。

| Fixture | 正例 / 可手算预期 | 反例与拒绝条件 |
| --- | --- | --- |
| `shared_inventory_2cells` | 一体素 10 molecule，请求 8 与 12 molecule，比例限制给 4 与 6，剩余 0；交换 cell ID 顺序结果按 ID 对齐不变 | 每个细胞各取 10、先到先得且未声明、胞内记请求量；默认严格策略则须整步拒绝 |
| `accepted_signal_shortage` | 相同 J_request、不同实际可得库存时，接受通量 policy 的 EI 按各自 J_accept 演化；充足库存与原请求通量路径一致 | 环境扣 J_accept、EI 用 J_request 却声称同一 policy；J=0 从非零 e 按 rephosphorylation 衰减 |
| `sample_deposit_support` | 一个跨体素支持的 1 菌案例：扣除权重与本段采样位置一致 | 先移动再从新体素扣旧位置请求；cell permutation 后将同长度数组误配另一群组 |
| `diffusion_impulse` | 均匀场不变、单脉冲质量不变、零场不生物质；离散 Fourier 模态按该算子 eigenvalue 衰减 | 任意负浓度截零后仍宣称守恒；整体归一化掩盖局部误差；不满时间段被丢弃 |
| `source_schedule_edge` | 常源 q 持续 T 的账本增量为 qT；日程中点切换时拆步；无细胞时场仍推进 | 灭绝早退跳过源/扩散；末段不足 diffusion cadence 被遗漏 |
| `dead_mixed_population` | live/dead 同体素，dead 摄取=0；约定保留残体时位置/体积/表达固定；死亡时胞内量进入显式 residual/removed 账 | 死菌继续生长；死亡清理抹掉物质；MAX_CELLS 超限计入 death |
| `count_growth_division` | 无合成、无真实降解时，长大前后 N 不变、N/V 降低；分裂两子总 N 等于母 N，浓度按体积/分配策略算 | 总 copies 因“体积稀释”连续减少后又分裂减半；面积份额当作体积浓度 |
| `boundary_pose` | 反射例 x=199.9、v=25、dt=.01、上界200，点粒子末位置199.85且 heading=-X；多次穿越也正确 | clip 到200保持+X；reflective 配置取模为.15；胶囊外廓越界却仅中心合法 |
| `rng_resume_rollback` | 同版本/后端，50 步一次运行与20+30步保存恢复逐数组与事件一致；拒绝一步再恢复重试一致 | 每次 run(seed)重开随机流；只保存位置；分裂后不保存新 ID/事件/随机状态 |
| `division_inheritance` | 总底物与分子数守恒；mother/child ID 唯一；浓度按策略copy、timer reset、count split | 一律均分包括浓度/方向；refresh 改机制状态；增加端帽面积不结算材料却声称膜守恒 |
| `invalid_inputs` | 0 fibers/0 cells可作为明确对照；dt正且有限；shape与物种匹配 | NaN/Inf/负dt、未知几何、重复owner、无初值previous-step、同单位不同quantity误连 |

容差按量纲设定并与 fixture 一起冻结；不能单靠相对误差检查接近零的库存。扩散先固定物理域/源量/支持范围再作 dx 与 dt 收敛，分别报告总量、L1/L2、峰值和采样信号差。FFT 离散算子与有限差分 Euler 的比较应看步长收敛，不要求不同时间积分法在有限 dt 下逐值相同。

## 8. 原始反例的独立复现

2026-09-29 独立运行，`python -B`、`NUMBA_DISABLE_JIT=1`，只读源文件，输出均在系统 Temp。A 默认参数仅 initial_cells=2；FFT 和边界另用表中输入。

| 原始路径 | 输入与实测 | 判定 |
| --- | --- | --- |
| A 死菌 | 两行 alive=False，bulk=1 uM，dt=.01；一次 carbon→cells 摄取 6.451612903225806 molecule，生长消耗 .7640210529442967 molecule | 与停止摄取/生长的 alive 语义冲突，确定性实现错误 |
| A FFT | 8³ 网格单位脉冲，D=500 um²/s，dx=5 um，dt=.01；输出总量1.0917389006501648 | 截零增加总量，不能标为守恒加速等价实现 |
| A 反射 | x199.9、heading+X、v25、dt.01，上界200；结果x200 | 截断，不是所声明的反射 |
| A 分段 | seed42，tumble min=max100/s；一次.05s vs .02+.03s，最大位置差 .743848576298177 um | `run()` 重开 RNG；分段运行不是连续轨迹 |
| B FFT | 20³，dx50，D65，dt.1；原谱输出min −.0012921462288836196，负质量.012515096433365895；截零归一化造成L1变化.025030192866731693，最后总量≈1 | 总量通过并不能证明局部正性问题消失或原算子等价 |
| B FD 时间 | 8³，dx5，D65，dt_diffusion=.01；update(.015)与update(.01)逐值相同 | 原 `int(dt_total/self.dt)` 丢失余时段 |
| B reflective 配置 | domain200×200×100，v25，dt.01，x199.9，hazard=0；主 batch 输出x.15000000000000568且heading+X | 运行路径无条件周期穿越，与配置不符 |

临时证据文件：`Temp/aoem-integration-audit-duisst3_/original-rebuilt-probes.json`、`Temp/aoem-integration-b-audit-wodn0bav/original-simplified-probes.json`。这两份是复核记录，不是交付依赖；可复现输入与结果已在表中保留。来源原有测试通过数量不能替代这些反例。

### 数值修正的判断

离散周期六邻点 Laplacian 的 eigenvalue 为 `−4 Σ sin²(πk_i/n_i) / dx²`。用该 eigenvalue 作指数传播，是固定空间离散方程的另一时间积分器；与原 `−|k|²` 截断 Fourier 表示不同。其空间生成矩阵非对角元非负且列和为零，所以理论指数保持非负与总量；这是数学推导，仍需有限精度 tests，不能用生物论文证明。新实现须独立版本，微小负 roundoff 与实质负值要区分，禁止不记录地 clip/rescale。

## 9. 论文独立核查与适用范围

本节为本轮针对关键推断的定向核查，非系统综述。逐篇重新打开原始文章或作者论文摘要，未只采纳来源 worker 的引用。检索日期2026-09-29；主要检索词为论文 DOI、PTS overall influx、protein number versus concentration dilution。

| ID / 原始文献 | 实际核对位置与支持范围 | 不能由该文献推出 |
| --- | --- | --- |
| E1 [Somavanshi, Ghosh & Sourjik, 2016, Sugar Influx Sensing by the PTS of E. coli](https://journals.plos.org/plosbiology/article?id=10.1371/journal.pbio.2000074)，DOI 10.1371/journal.pbio.2000074 | 全文 Abstract、Results “PTS Network Senses the Overall Influx of Sugars”、Fig.3；FRET 结果支持实际总体糖 influx 与PTS信号及其向趋化通路传播的关系，也报告其他代谢输入 | 不证明本代码EI简化ODE/速率常数，不专门标定AscF-cellobiose，不验证网格限流U/dt在任意dt下的误差 |
| E2 [Neumann, Grosse & Sourjik, 2012, Chemotactic signaling via carbohydrate PTS in E. coli](https://pubmed.ncbi.nlm.nih.gov/22778402/)，DOI 10.1073/pnas.1205307109 | PubMed原论文摘要；PTS信号进入chemoreceptor–CheA–CheW复合体并共享methylation-dependent adaptation；全文PMC访问受检查限制，未宣称逐段核验补充模型 | 不证明独立固定3s低通memory等价受体适应，不支持完全删去感受复合体后仍声称机制级模型 |
| E3 [Cluzel, Surette & Leibler, 2000, An Ultrasensitive Bacterial Motor](https://cluzel.fas.harvard.edu/sites/g/files/omnuum7156/files/cluzel/files/cluzel_science_00.pdf)，DOI 10.1126/science.287.5458.1652 | 原论文PDF与PubMed摘要；测量单flagellar motor输出相对于细胞内信号蛋白的陡峭响应 | 单motor CW bias不是整菌tumble hazard；不自动标定本文线性hazard映射、tumble角度/驻留时间或表达负担 |
| E4 [Lin & Amir, 2018, Homeostasis of protein and mRNA concentrations in growing cells](https://www.nature.com/articles/s41467-018-06714-z)，DOI 10.1038/s41467-018-06714-z | 原全文 Results变量定义、Eq.(6–7)与显式分裂段；区分protein number与number/volume，模型中计数因分裂降低，浓度受增长稀释 | 不标定INP合成/降解常数，不证明INP展示可用恒速合成近似，也不证明某个面积分配律 |
| E5 [Harris & Theriot, 2016, Relative Rates of Surface and Volume Synthesis Set Bacterial Cell Size](https://pubmed.ncbi.nlm.nih.gov/27259152/)，DOI 10.1016/j.cell.2016.05.045 | 原论文摘要与Figure 1/6/7说明；支持分别考虑面积/体积合成，提出材料积累与分裂联系；图7是excess SA material阈值 | 不证明固定半径A(V)即膜材料预算；不标定B面积adder阈值或任意分裂时凭空补充新端帽材料 |

**INP 具体审查判断：**如果N确为total copies，`c=N/V`且`dV/dt=μV`，则`dc/dt=(1/V)dN/dt−μc`；纯体积增长使浓度下降，但不使N下降。若采用N状态并显式分裂，合理的计数方程需把合成、真实降解与分裂分配分别写清。将`−μN`保留为“生长相关降解”也可以形成一个假设模型，但必须更名、独立参数和证据，不能再称纯稀释。此判断来自量纲/变量变换，E4提供一致的原始建模例证；没有凭论文替换未测INP动力学。

**PTS 具体审查判断：**代码既把J解释为转运通量，又实际分配到U，那么在该简化机制中信号读U/dt可消除“无底物仍按需求转运”的内部冲突。它是受E1方向性支持的模型修订，生物范围仍是surrogate；如未来有并行代谢输入或更细PTS状态，不能把U/dt单输入合同当作永久生物定律。

## 10. 修订副本交叉审查与迁入阻断条件

### A：已独立检查的修订

读取 `friskoli-model-review` 相对原始基线的实际 diff，并在 `rebuilt-v2` 独立复跑最终全部测试：**35 passed in 1.65s**，其中原5项与新增30项均在。使用 `python -B` 调用 `pytest.main(['-q','-p','no:cacheprovider','--basetemp',独立Temp路径,'tests'])`，`NUMBA_DISABLE_JIT=1`；测试退出码0，无警告。最终测试还包含附着纤维源连续5步的物质账，以及显式 reference 后端优先于环境变量的选择行为。

| 修改 | 独立核对与前后结果 | 尚未证明 |
| --- | --- | --- |
| 全生理路径alive mask | `simulation.update_carbon/update_cells`；第8节相同死菌例，摄取6.4516129→0，生长消耗.7640211→0；死亡转移只计一次测试通过 | 残体力学、细胞裂解与真实死亡动力学 |
| 点运动镜面反射 | `model.motion.move_with_directions` 三角波映射余位移和heading；相同边界例x200/+X→199.85/−X；测试包含多次穿越/精确落墙 | 胶囊、纤维或菌间碰撞 |
| 离散Laplacian指数传播 | `acceleration._fft_decay/periodic_diffusion`；原8³脉冲总量1.0917389→.9999999999999999，min7.3127354866e−13；模态/半群/FD收敛测试通过 | 任意网格/步长与原谱算法等价、实验扩散系数 |
| RNG写入状态并恢复 | `state.SimulationState.rng_state`、`simulation.step/run`；相同.05对.02+.03位置差.7438486→0，测试比较全部状态及RNG | 可移植文件checkpoint、模块独立随机流、失败事务回滚 |
| count语义与实际增长率 | `growth.inp_expression`去掉纯增长−μN，保留turnover；无合成/降解N1000保持1000；`growth.step`返回已接受底物对应actual_mu | 真实INP合成/脱落、健康负担参数 |
| 输入与零纤维 | `parameters.validate`检查finite、合法域与网格整除；零fiber对照可运行；`run`拒绝非整倍时长 | 全部非法状态注入、零初始细胞（当前仍拒绝） |

A 的FFT修订**仍有明确限定的roundoff处理**：结果低于 `−64ε×max(input_max,output_abs_max)` 则抛错；界内负值投影为0，再恢复原总量。应这样描述其有限精度策略，不能说“完全无截零”。它与B新算子的保留signed roundoff策略不同；未来CAD需统一容差/诊断策略后另发数值版本。独立复核输出在 `Temp/aoem-integration-a-reviewed-59rxhfdz/reviewed-rebuilt-probes.json`；可复现输入沿用第8节。

### B：已独立检查的最终修订

读取相对基线 `98b12ff` 的最终实际 diff，独立复跑选定原测试103项与 `tests/test_review_numerical_contracts.py` 新增26项：**129 passed in 1.73s**，退出码0。原测试文件为 `test_chemotaxis_adaptation.py`、`test_growth_yield.py`、`test_issue5_geometry_consistency.py`、`test_issue6_health.py`、`test_issue7a_uptake_conservation.py`、`test_kd_eff.py`、`test_expression_geometry.py`。本次未声称执行所有原始长程 ABM 测试；使用 `python -B`、`NUMBA_DISABLE_JIT=1`、`-p no:cacheprovider` 与独立 `--basetemp`，执行目录和输出都在 `Temp/aoem-integration-b-final-4t90251v`，没有修改原ZIP。另在启用真实Numba JIT且使用独立缓存时复跑新增26项：**26 passed in 3.19s**，退出码0；输出在 `Temp/aoem-integration-b-jit-b6j4vrp0`。

| 修改 | 独立核对与验收结果 | 尚未证明 |
| --- | --- | --- |
| 接受摄取先于PTS信号 | `core.batch.transport_demand/pts_step` 与 `Simulation.step`；先请求、共享分配、记胞内，再以U/dt推进EI/CheY；库存充足/不足各含全活与部分死亡两条路径，4个对照通过 | 真实AscF-cellobiose转运动力学、其他代谢输入、任意dt误差 |
| 离散扩散与实际余时长 | 默认改为 `fft_discrete`，使用7点离散Laplacian的指数传播；模态、脉冲质量、半群及幅值1/1e−15检查通过；FD覆盖.015s全段，不再只走.01s | 所有网格与耦合源的收敛；旧 `fft` 截零归一化路径仍保留供历史对照，不能称修订默认算法 |
| 常源与空群组 | `Simulation.step/_advance_diffusion_clock` 在无活菌时仍推进常源与扩散，结束run时补齐累计扩散余时长 | `run()`仍在灭绝后提前结束，并明确记录extinct；不表示它会自动把空群组场推进到原计划时长 |
| 死亡残余底物账 | `_settle_dead_substrate`将死亡行s_int一次转入 `death_removed_substrate_molecules` 并清零；新死亡、预先死亡、全死亡及清理/非清理步均通过 | 此账只包含残余可用底物，不含生物量、蛋白、代谢产物；未模拟裂解释放 |
| 整数步时域 | 初始化拒绝TOTAL_TIME非DT整倍数；`run`按step_count<n_steps执行，t由step_count×dt计算；.3s/30步和.025s拒绝例通过；元数据区分requested/actual time | 任意变步长、输出间隔不影响轨迹的完整验收、保存恢复 |
| 资源上限与边界 | 超过MAX_CELLS只停止并记录resource_limited，不删除活菌或登记death；仅接受periodic；运动和子体边界都用实例domain | 反射实现、胶囊碰撞、严格的分裂前容量预约；当前资源检查发生在步后 |
| copies与真实分裂 | 默认 `copy_number` 去掉纯增长−μN，真实turnover保留；历史项另称 `legacy_growth_loss`；实际 `sim.step()`分裂保持母子V、s_int、INP总和，Y_mem同步/继承和全部数组长度通过 | INP参数、生物膜材料预算、面积adder阈值的实验标定 |

B 新FFT**不截零、不做全局归一化**，保留幅值相对容差内的signed roundoff，越限抛错；采样进入转运请求时只把这一级舍入误差当0。容差以实际场幅值为尺度，没有固定1的下限；这与A的有界投影策略不同。B的元数据写入 `simplified-v4-reviewed-2026-09-29.1`、`accepted-flux-periodic-v1`、扩散与INP policy、停止原因及J的接受通量语义。该profile是修订来源自己的标识，不代表CAD已支持同名执行合同。

**共同未完成项：**两套修订通过的主要是实现不变量与构造反例，未完成生物参数校准、全范围网格/步长收敛、通用checkpoint或原子回滚、按模块稳定分流的RNG、胶囊相互作用、膜材料守恒或实验结果复现。不能据此发布“已验证的最优G”“完整碳/能量平衡”或“机制级趋化预测”。纤维账采用来源声明的cellobiose-equivalent；未建立化学计量时不改称元素碳质量守恒。

迁入最低条件：源码/参数/算子/profile版本冻结；无隐式截零增量或未记录归一化；共享库存与接受通量一致；计数单位冲突已解决或相应机制明确禁用；alive、边界、RNG与续算按声明工作；资源限制不制造死亡；旧CAD回归保持；未标定机制在catalog显示exploratory，不显示validated。
