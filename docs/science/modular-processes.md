# 可组合空间过程：实现与科学边界

更新于 2026-10-03。本文对应 `modular-spatial-v1`、Project 0.6 和 Module/Graph 0.2。旧 `chemotaxis-spatial-v1` 的机制和运行含义保持独立。新增参数均是构造示例或有来源的物性值，未完成菌株、培养基和实验条件的联合标定。

## 执行与守恒

机制通过普通注册模块的 `initialize`、`propose(StepContext)` 返回 `ModuleProposal`。模块不能直接修改世界。系统按 prepare、field、physiology、lifecycle、observation 执行；同一步依赖只能读取本阶段或更早阶段，循环必须显式使用 previous_step。

场先接收初值、输运和正输入，再对负输入及各摄取请求按共享库存比例分配，然后扩散。输运是单独的保守 `field.transport`，不混入外源输入。摄取输出是实际结算量。账目区分 external_net（外部输入输出）、internal_net（源/材料/残体转入场）、reaction_net（化学反应）、consumed（摄取）、maintenance 和 growth。内部转移不能当作新产生的全系统物质。

所有步骤在独立状态和随机流副本上计算。端口、场、几何或 frame 检查失败则不提交。checkpoint 保存模块状态、场、随机流、活细胞与死亡记录、几何、账目和输出；恢复会复核项目与模块锁、端口类型、mask 和场总量。

普通数值模块使用 float64；这并不等于任意尺度的精确实数算术。大库存与极小输入并存时需要特别检查精度。维持与共享增长均使用双分量库存及精确二进制有理数结算，保留低于主库存 ULP 的到达量；重整化误差预算同时考虑实际到达和实际维持扣账，零到达不等于零数值运算；浮点场和几何仍有自身可表示性边界。

## 基础过程

- `control.broadcast/scale/delay/lowpass/sustained_threshold`：显式广播、缩放、单步记忆、精确一阶滤波和采样阈值持续时间。滤波为 y'=x+(y-x)exp(-dt/tau)。阈值模块不推断采样点之间的未观测越界。
- `control.seeded_distribution`：以 seed、population ID、cell ID 构造确定性 normal/uniform 个体分布，应用显式上下限；分裂继承现有值。
- `geometry.capsule_derived`：L 为包括半球端部的总长，V=πd²(L-d)/4+πd³/6，A=πdL。无状态几何读数在生命周期处理后刷新。
- `source.spatial_schedule`：物理长方体支持域内的 pulse/rate；t=0 pulse 只在初始化执行，后续使用 (t,t+dt]。`field.initial_array` 校验完整体网格形状。
- `field.advection_upwind`：三维 donor-cell 有限体积迎风格式，按 CFL 分子步；周期或无通量边界，固体面无通量。速度场由用户给定，不求解 Navier–Stokes。
- `field.boundary_exchange`：线性松弛的精确指数推进；正负交换均记录，竞争负通量按实际可得库存修正。
- 氧交换和氧消耗是显式 species 模块，可与其他场并行；没有自动把氧摄取变成 ATP、呼吸链或生长收益。

## 库存、几何与生命周期

`metabolism.shared_inventory` 将同一胞内库存先用于维持，再用于按体积上限提出的增长需求。增长依赖显式 volume yield；胶囊增长须通过几何可表示性及碰撞检查，受阻消耗返还库存。维持优先顺序是构造的离散分配规则，不能称为 Pirt 实验直接测得的规律。

`growth.linear_elongation` 是给定长度速度的数值几何模块，不具有生物量来源解释。`division.volume_adder` 按体积阈值提出分裂，两个子体体积守恒。`system_limits.max_cells` 默认 256 是运行资源上限；达到上限会明确报告 resource.cell_limit 并保留上一提交状态，不把资源不足标成生物学 blocked。膜假设遵照项目约定：新增几何膜面积隐式可得，记录 added_membrane_area_um2，不引入虚构膜分子。

状态声明明确 division copy/split/reset 和 migration copy/conservative_regrid。整数 split 使用向下取整及余数；reset 必须提供 initial_value。当前没有通用 on_death retain/release 执行器，声明这些策略的模块在编译时拒绝。现有库存 owner 在死亡时由系统记录残量，随后 `life.residue_release` 按显式速率释放，避免继续运行活细胞摄取/运动。尸体碰撞采用粗略轴对齐包围盒，且为可渗透近似，不是精确胶囊力学。

`surface.adhesion` 是可逆随机结合状态及运动比例响应；`contact.wall_penalty` 是几何距离驱动的有限位移响应。它们不包含弹性形变、流体润滑或已标定的表面结合能。

## 材料、酶与化学

普通旧材料适配模块保留有限库存，contact 与 direct-bulk 分别按接触条件或全局条件分配表面酶能力。材料释放写入显式内部转移，不能超过源库存。同一 catalytic output 不能被多个独立反应重复花费；当前编译时拒绝该组合，材料共享催化 consumer 也限定为一个。同一 population 的 surface enzyme owner 必须唯一，contact provider 与材料降解引用同一 surface_enzyme 资源组，不能借不同 provider 节点重复花费。纯只读观察可继续读取。尚未实现任意催化反应网络的联合预算。

`enzyme.secreted` 使用指定细胞数、分泌速率及一阶周转。蛋白输入来自显式外部供应假设，未扣除细胞氨基酸/能量。`enzyme.contact_provider` 使用胶囊与长方体的几何距离。

`reaction.cellulose_hydrolysis` 按 cellulose 的 anhydroglucose unit（AGU）库存记账，选择葡萄糖或纤维二糖产物，记录累计水消耗；葡萄糖路线每 AGU 形成一个 glucose 并消耗一个 water，纤维二糖路线每两个 AGU 形成一个 cellobiose 并消耗一个 water。这是无限长聚合物单元的计量近似，不追踪有限链端、结晶度或内切/外切酶协同。水视为足量溶剂并记账，不是有限水场。

当前未提供独立 cellobiose + water → 2 glucose 的耦合场反应节点。因此这套模型不应被称为完整纤维素水解化学网络。实验文献只支持反应种类/计量，不能直接支持本示例 turnover 参数。

`material.isotropic_erosion` 以剩余比例的立方根缩放三个轴；previous_step 输入会产生明确一拍延迟，checkpoint 保存实际应用比例。新固体若覆盖含有溶质的流体网格，会拒绝该步，不能静默删掉溶质。

`geometry.triangle_mesh` 支持合法非凸闭合定向三角网格、分离的固体分量及内部空腔。外表面法向朝外，空腔表面法向朝向空腔；物理体积由有向三角面积分，空腔体积扣除。顶点必须焊接且均被使用；重复/退化面、开边、非流形边或顶点、自交、非拓扑表面接触及错误的壳层方向会在参数预检中拒绝。验证使用与物理尺度相关的 float64 容差，间隙小于该数值分辨率的表面可能被拒绝，不自动修补网格。

体素化先用有向 solid-angle 判定固体内部，再以 triangle–box separating-axis test 判断实际三角面与体素是否相交。AABB 仅筛选候选体素，不将整块包围盒填实；凹陷与空腔中未接触边界的体素保持流体。边、角、面恰好相触均保守标成固体，因此小于网格尺度的窄缝可能关闭。碰撞使用这些体素盒，不是连续精确三角面碰撞。模块 volume 是物理网格的解析有向体积，不能等同于体素占据体积；该几何节点不声明物质库存，材料质量仍由独立库存节点及显式密度假设负责。

## 物性、映射与观测

`medium.property_catalog` 返回包含适用条件和来源的物性记录。`medium.viscosity_diffusion` 固定 hydrodynamic size，使用 D/Dr=(T/Tr)(ηr/η)。适用稀薄连续介质及 Newtonian 溶剂；不能把它直接当细菌推进速度规律。`medium.viscosity_motion` 的速度幂律是单独的构造响应。

`mapping.surface_volume` 和标准采样/沉积模块使用匹配的三线性权重，显式表面数量与实体集合；这些纯映射端口本身不会自动创建物质。`observer.conversion_lineage` 声明累计区间、初始分母和谱系事件；分母为零时 conversion 为 null，不用 0 或 100% 替代。

## 公开案例与验证

`modular-foundation` 在基础迁移示例上加入真实空间日程和黏度扩散；`modular-material` 连接分泌酶、AGU 水解、连续侵蚀及转化观测。两者各完成 100×0.01 s，在第 50 步保存 checkpoint 后分段继续，与连续运行的 frame 和 fields 完全一致；foundation 另完成 1000×0.01 s。`tests/test_scientific_processes.py` 覆盖数学边界，`tests/test_modular_science.py` 覆盖注册执行、场量、共享库存、材料产物、死亡残体、mesh、输运竞争及分裂恢复。已用非均匀初场（3 uM，x-gradient 0.01）、seed 17，在 CPU/CUDA 各执行 3×0.01 s，场最大绝对误差为 0，cell frame 完全一致。CUDA 仅执行系统扩散，其余模块在 CPU；planner 明示 host 往返。该对照不是性能基准；实体实验标定尚未验证。

## 来源及其支持范围

- [IUPAC Gold Book: Stokes–Einstein equation](https://goldbook.iupac.org/terms/view/12260)：扩散与温度/黏度的关系及连续介质假设。检索到官方定义，页面直接访问曾受限。
- [NIST 动态光散射协议](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=854412)：hydrodynamic diameter、溶剂温度和零剪切黏度的条件。
- [NIST SI guide](https://www.nist.gov/pml/special-publication-811/nist-guide-si-chapter-8)：黏度单位。
- [Pirt, 1965](https://pubmed.ncbi.nlm.nih.gov/4378482/)：生长与维持消耗的区分，不支持本实现的优先级算法。
- [Real-Time Measurement of Cellobiose and Glucose Formation during Enzymatic Biomass Hydrolysis, 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8173519/)：纤维二糖与葡萄糖产物及计量背景，不移植反应速率。
- [E. coli oxygen transfer cultivation study, 2014](https://pmc.ncbi.nlm.nih.gov/articles/PMC4226889/)：kLa 对培养与装置条件敏感，不把宏观反应器参数作为微域标定。
- [LeVeque finite-volume resources](https://www.clawpack.org/fvmhp_materials/)：保守有限体积及 CFL 数值方法，不能作为给定速度场的物理验证。

非凸网格专项覆盖 U 形凹陷、带空腔壳层、精确边界接触、三角面 AABB 的假阳性、相交闭合壳、非流形顶点、开边、重复/退化面、分离固体及平移/缩放。真实 triangle_mesh 节点验证凹陷保持溶质流体并完成 checkpoint 恢复和继续运行。
