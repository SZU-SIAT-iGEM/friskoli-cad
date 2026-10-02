# 基础过程与趋化机制的组合

2026-10-02。下列模块均注册于 `chemotaxis-spatial-v1`，新完整预设明确生成节点与边。运行器不在用户图之外暗加生存或死亡。旧六套最小/来源案例保留原语义，用于公式与数值研究；新增五套 `foundation-*` 是含基础生存的完整探索性案例。

本页中的新生存机制为 **phenomenological** 假设。参数全部是 constructed，没有菌种或实验标定；它追踪可用营养当量和维持消耗，不声称包含 ATP、氧限制、蛋白合成、废物、完整碳能量账或真实存活曲线。

来源按支持范围区分：摩尔数与实体数换算采用 [BIPM 的 SI mole 定义](https://www.bipm.org/en/si-base-units/mole)，由此得 1 µM·µm³=602.214076 个分子当量；`1−exp(−H)` 的概率关系对应 [NIST 指数分布的 CDF、survival 与 cumulative hazard](https://www.itl.nist.gov/div898/handbook/eda/section3/eda3667.htm)。这两个官方来源于2026-10-02读取，分别只支持单位换算和概率恒等式，不支持本工程菌的维持速率、匮乏宽限期、恢复规律或死亡参数。新储备/匮乏规则是本文明确给定的构造假设，不伪称来自生物实验论文；原 PTS/MCP 文献与来源代码范围仍见 [N3机制说明](n3-mechanisms.md)。

## 职责、输入输出与依赖

| 层次 | 模块 ID | 输入 → 输出 / 状态归属 | 依赖与含义 |
| --- | --- | --- | --- |
| 空间 | `pts.capsule_area` | 菌体几何 → 表面积 | 沿用已注册胶囊数据入口；名称保留历史兼容，读几何不等于开启 PTS |
| 有限场 | `field.diffusive_local@2.0.0` | 初始浓度、扩散率 → 网格浓度 | 每物种唯一场 owner；维护有限库存 |
| 自动补给背景 | `field.ideal_local_reservoir` | 浓度设定 → 均匀场、累计补给 | 同一物种不能再挂有限来源/底物；摄取补给进入外部输入账 |
| 有限球源 | `source.finite_local` | 球心、半径、库存、释放率 → 剩余库存 | 产品进入所指物种的有限场；球形支持区与流体网格相交分配，耗尽停止 |
| 接触底物 | `material.degradable_box` + `reaction.direct_bulk_hydrolysis` + `surface.enzyme_activity` | 有限固体、接触几何、酶 → 同场可溶产物 | 无酶/无接触/耗尽不释放；没有三池分配 |
| 局部取样 | `field.sample_local` | 上一步场、位置 → 浓度与梯度读数 | 输入绑定相同菌群运动 owner；当前最近体素语义 |
| 通用摄取请求 | `uptake.saturating_request` | 浓度 → 请求通量 | 不声称转运器一定是 PTS；必须另接结算 |
| PTS 容量 | `pts.capacity_rebuilt` / `pts.capacity_simplified` | 表面积 → functional copies | 只在需要来源 PTS 容量模型时使用 |
| PTS 摄取请求 | `uptake.pts_request` | 浓度、functional copies → 请求通量 | 容量×turnover×饱和比例；与通用请求可替换 |
| 实际摄取 | `uptake.local_settlement` | 请求、同一个场 → accepted amount/flux | 库存不足按共享结算分配；累计摄取是统计量，不是另一份胞内储备 |
| 基础营养生存 | `metabolism.reserve_balance` | accepted amount → 可用储备、维持已用量、未满足时长 | 唯一胞内营养库存 owner；无需 growth/expression/PTS |
| 持续匮乏死亡 | `life.starvation_hazard` | 未满足时长 → 匮乏时间、health 读数、等效 hazard | 消费 reserve 输出；宽限期后才抽死亡；自身唯一 death owner |
| PTS 特殊信号 | `signal.pts_accepted` + A/B memory | 实际接受通量 → EI/CheA/CheY、motor bias | 只有实际摄取驱动 PTS 信号，不使用未接受请求 |
| MCP 特殊信号 | `signal.mcp_adaptation@2.0.0` | 独立 ligand 浓度 → MWC、CheY、motor bias | 无无效 EI 参数；完整 MCP 场景另有恒定养分背景 |
| 无趋化对照 | `signal.constant_bias` | 固定偏置 → motor bias | 摄取、营养生存仍可运行；没有信号方向反馈 |
| 基础随机运动 | `motion.hazard_run_tumble` | 已提交 motor bias → 连续位置/朝向 | 独立 hazard/方向 RNG，统一胶囊碰撞；恒 bias 即无偏随机运动对照 |
| 来源生理替代链 | `growth.nutrient_*` + `expression.surface_copies` + `life.health_balance` | 接受摄取 → 增长、表达负担、健康与死亡 | 保留来源 A/B 模型；不能与新 reserve 重复消费同一菌群的 accepted amount |

## 通用饱和摄取

`uptake.saturating_request@1.0.0`：

\[
J_{req}=J_{max}\frac{C}{K+C}.
\]

输入 `concentration`（µM），输出 `requested_flux`（molecule/s）；参数 `maximum_flux_molecules_s=200` molecule/s、`half_saturation_um=1` µM、`species=nutrient`。零浓度请求为零。K 必须大于零。计算复用已有稳定饱和函数，但注册含义是通用现象学请求，无 functional copies 或 EI 假设。

## 营养储备与维持

`metabolism.reserve_balance@1.0.0`：令 R 为上一步储备、U 为本步真实接受量，q 为维持需求，Δt 为秒。

\[
R^+=R+U,\quad Q=\min(R^+,q\Delta t),\quad R^{new}=R^+-Q.
\]

需求未满足时长 `t_u=Δt−Q/q`；q=0 时定义 `t_u=0`。输出 `intracellular_molecules`、`used_molecules`、`unmet_duration_s`。参数 `initial_molecules=100` molecule-equivalent、`maintenance_molecules_s=10` molecule-equivalent/s、`species=nutrient`。这些是为观察机制提供的构造值，不能解释为真实细菌的维持速率。

本模型按明确 splitting 假设先接收本步 U，再连续消耗维持需求；因此同一步内先是需求满足的前段，随后才可能匮乏。不是逐分子事件模型。体积保持不变，维持已用当量进入 `maintenance_consumed_molecules`，不虚构回流产物。

储备使用主值与带符号补偿余量 `reserve_correction_molecules` 表示，避免自然扩散产生的极小摄取被较大储备的浮点间距吞掉。余量是数值状态，随 checkpoint 保存；两部分共同进入余额。它不构成额外营养池。

## 持续匮乏与死亡

`life.starvation_hazard@1.0.0`：状态 `starvation_time_s=S` 初始为 0。需求满足的前段 `t_f=Δt−t_u` 中 S 以 recovery_rate=r 下降且不小于0；未满足后段中 S 以每秒1秒增加。

\[
S_{fed}=\max(0,S-r t_f),\quad S_{new}=S_{fed}+t_u,\quad
\lambda(S)=\frac{k}{60}\mathbf1_{S>g}.
\]

参数 `grace_s=g=60` s（严格大于0）、`recovery_rate=r=1`（每秒满足需求消除1秒匮乏暴露）、`death_rate_per_min=k=0.1` /min。r=0 表示暴露不恢复。宽限期 g 不等于必死时刻，只是开始出现非零风险。

在这两个线性片段中精确积分超过阈值的时长，得 H=∫λdt；死亡概率为 `−expm1(−H)`。仅 H>0 时使用该细胞独立 `death` 子流抽样。刚把外场设为零不会立即全死：默认储备先提供10秒维持，之后还需60秒连续匮乏才有风险。重新供养会逐渐降低暴露；尚未恢复到阈值以下时仍有风险，这是本模型的显式假设。

输出 `starvation_time_s`、`health=exp(−S/g)` 与 `death_hazard=60H/Δt`（/min）。health 只是可读的暴露指示量，未经实验映射；death_hazard 是本区间等效平均率，不是步末瞬时率。死亡日志保留模块、`policy=reserve_starvation`、概率与实际随机抽样。

死亡执行复用现有移除/碰撞/RNG事务流程；剩余胞内营养记入 `removed_residual_molecules`，不自动成为可再次摄取的养分。错误、资源预算限制和分裂受阻仍不构成生物死亡。

## 完整组合与原案例

| 新预设 ID | 环境与摄取 | 特殊信号 | 基础生存 |
| --- | --- | --- | --- |
| `foundation-control` | 有限球源→扩散场→通用饱和摄取 | 固定 motor bias | reserve + starvation |
| `foundation-pts-a` | 有限球源→扩散场→PTS 容量 A 摄取 | accepted PTS + 浓度记忆 A | reserve + starvation |
| `foundation-pts-b` | 有限球源→扩散场→PTS 容量 B 摄取 | accepted PTS + CheY memory B | reserve + starvation |
| `foundation-mcp` | 均匀自动补给 nutrient→通用饱和摄取；独立有限 ligand | MCP v2 | reserve + starvation |
| `foundation-materials` | 接触有限降解与球源→同一有限 nutrient 场→PTS 摄取 | accepted PTS + A memory | reserve + starvation |

非 MCP 完整案例初始 nutrient 为零且没有预置梯度；场由释放/降解与扩散自然形成。完整 PTS 的 EI/CheY 和相应 memory 按零初始摄取的解析稳态初始化，避免人为初值启动瞬态；没有改变这些机制的速率。MCP 保留独立初始 ligand 梯度，自由扩散演化；背景 nutrient 恒定。源位置、库存、速率，材料酶，储备和生存参数均可通过普通图节点编辑。

旧 `chemotaxis-pts-a/b/mcp/control/materials/lifecycle` 仍保持原运行含义，没有静默增加死亡。需要完整基础生存时从新的 `foundation-*` 开始。来源 health 与新 starvation 是可选择的两套假设，不叠加为两个 death owner。当前不支持新 reserve 与旧 growth 并用，preflight 明确拒绝重复胞内库存；未来若要同时分配维持和增长，需独立设计共享资源分配，不能将同一接受量复制两次。

## 守恒、checkpoint 与验证

有限场独立核对来源/材料/扩散/累计实际摄取。胞内核对：初始储备 + 累计实际摄取 = 当前活菌储备 + 维持已用 + 死亡移出；旧增长在其自身组合中另计增长消耗。累计摄取不能再次加到总库存。所有候选状态、库存、死亡、RNG、观察器只在整步验证成功后提交。

新增模块状态与随机流均进入现有严格 checkpoint；恢复检查补偿余量、health/exposure 一致性和库存账。测试入口为 `tests/test_foundation_survival.py`，覆盖分析公式、宽限边界、恢复、持续补给、完整组合余额及新旧库存冲突。

2026-10-02 验证：冻结实现后的 **32 passed / 56.00 s**，包含前13项 foundation 测试、旧六套模板运行/恢复、全部11套示例文件与工厂一致性、旧稀疏死亡/分裂 worker。此前较广测试82通过、3失败中，checkpoint 显示测试期间源码锁发生变化；固定代码后上述3项均复测通过。没有放松版本锁。另有新增晚期失败回滚和伪造补偿余量拒绝检查，完成后记录结果。未验证真实菌株参数或12小时生物预测。

加入晚期失败回滚、伪造补偿余量拒绝后，foundation 专项 **15 passed / 28.23 s**，包含真实异步 worker 的稀疏保存死亡日志。与上一批有重叠。新模块 Catalog 中的名称、端口单位、状态继承、数学表达、来源限制和验证入口均来自实际声明；reserve 主值/补偿余量继承标记为 split，持续匮乏时间为 copy。当前基础组合没有增长/分裂，分裂并未因此被暗加到新预设。
