# N5：原始 B 生命周期与中心营养源对照

本案例使用普通 graph 与注册模块，不含按案例 ID 选择的执行逻辑。`make_n5_acceptance()` 生成 `n5_b_lifecycle_128um.project.json`。运行设置是 **dt = 0.01 s、10000 数值步、总模拟时间 100 s**。域边长为 **128 µm**，即 128 × 128 × 128 µm；它不是体积 128 µm³。

原模型 A 没有分裂；原模型 B 包含摄取、生长、表达、健康与面积分裂。本案例选 B 完整生命周期，不以 maintenance reserve 代替生长。旧 `chemotaxis-lifecycle` 保持加速演示性质与原参数；不得把该旧例的 μmax=1/min 或 yield=0.001 µm³/molecule 当作原 B 参数。

## 可追溯参数

源模型是 `friskoli-model-review/simplified-v4`，审查版本 `6b6180b68ac047b244aa9947fe7cc7d227f76310`，文件校验见 [n3_source_lock.json](../../src/friskoli_cad/science/data/n3_source_lock.json)。主要依据为 `config.py`、`core/agent.py`、`model_b_morphology/division.py` 和 `model_b_morphology/health.py`。这里的“源模型参数”只表示忠实沿用代码，**不表示这些数值经过 Friskoli 菌株实验测定**。

| 参数 | 值与单位 | 性质 |
|---|---|---|
| 细胞半径、出生体积 | 0.4 µm；0.5 µm³ | 源 B |
| 胶囊总长、直径 | 1.2613850609910124 µm；0.8 µm | 由源 B 几何推导，总长含端帽 |
| 出生参考面积 | 3.1702064327658226 µm² | 由源 B 几何推导 |
| N₀、G、Gcap | 500 copies；1；10 | 源 B |
| 转运 kcat、Km | 20/s；30 µM | 源 B 中的代用参数，非 AscF 实测拟合 |
| EI 总量、去磷酸化、再磷酸化 | 6 µM；0.001/molecule；20/s | 源 B，去磷酸化率乘实际每细胞摄取通量 |
| EI 对 CheA 抑制常数、CheA 总量 | 0.3 µM；5 µM | 源 B |
| CheY 总量、磷酸化、去磷酸化 | 8 µM；2/(µM·s)；10/s | 源 B |
| 初始 EI 去磷酸化比例、CheY-P、memory | 0；0 µM；0 µM | 源 B `Bacterium` 默认初态，保留启动瞬态 |
| CheY memory 时间、motor baseline | 3 s；2.59 µM | 源 B |
| Hill 指数、半响应浓度 | 10.3；3.1 µM | 源 B surrogate；不把单 motor 关系等同于整细胞实测 hazard |
| 速度、hazard 范围 | 25 µm/s；0.1–10/s | 源 B |
| 转向角、tumble dwell | Normal(68°,36°)，裁至 0–180°；0.1 s | 源 B 分布，CAD 使用独立随机流 |
| μmax、体积产率 | 0.005/min；1.1672551805315156e-9 µm³/molecule | 源 B；产率来自质量平衡估计 |
| 初始胞内底物 | 0 molecule | 源 B |
| 表面表达初始量、合成、turnover | 10000 copies；800 copies/min；0.03/min | 源 B，总 copies，不额外添加生长稀释 |
| 健康初值、repair、burden、starvation | 1；0.01/min；0.006/min；0.002/min | 源 B 现象学规则 |
| 死亡 onset、最大 hazard | H=0.05；1/min | 源 B，随机死亡，不是硬阈值直接删除 |
| 分裂目标体积、面积 CV、最小面积 | 1 µm³；0.15；1e-6 µm² | 源 B 每周期规则 |
| 分割比例 | Normal(0.5,0.05)，裁至 [0.35,0.65]，再按球帽可行体积裁剪 | 源 B |
| 有效扩散系数 | 65 µm²/s = 650/10 | 源 B 的水中估算值及相对黏度假设 |

简化健康规则只使用 PTS 负担；它不包含 INP 表达的额外代谢成本。版本 1 表达/健康模块保留的 A-policy 参数在本案例中不参与执行，参数 provenance 显式标为 inactive。原 B 的 surface/fiber 环境未在本案例重建，改用下述单一可溶场。

## 新构造的数值场景

- 256³ 立方网格，dx=dy=dz=0.5 µm，小于细胞直径 0.8 µm。
- 中心源位置 (64,64,64) µm，半径 5 µm，库存 1e10 molecule-equivalents，以 1e8/s 释放，恰好支持 100 s。初始可溶浓度 0 µM，无外加线性梯度。
- CAD 封闭无通量边界与几何碰撞规则；原 B 的周期边界、纤维黏附和接触场没有照搬。源是分布释放的可溶库存，不是实物纤维。
- 初始 100 PTS + 100 control，两组共享同一有限源和可溶场。每对位置通过中心反演配对，方向也反演；因此初始径向位置与径向 heading 精确匹配，避免重叠。位置种子 20261002；run 随机种子沿用 project 中显式值 42。
- 两组都保留相同的几何、摄取、PTS 信号、memory、生长、表达、健康和分裂。control 的运动只读取额外 `signal.constant_bias`，值为 `Hill(2.59 µM)=0.13571263629578523`，不是 0.5。control 的 PTS/memory 仍计算并可观察，但不反馈至运动。

这是无方向偏好的 run–tumble control，不是每步独立 Brownian 位移。两组原模型信号初态均为零；PTS 组包含启动适应瞬态，control 的固定 bias 不追随该瞬态。需要区分启动效应时可通过普通参数将两组 PTS 初态设为零通量稳态，并明确标为另一场景。

在无摄取的质量上界下，100 s 后平均浓度为 7.9180673 µM。无限域持续点源近似 `C(r)=Q/(4πDr × 602.214076)` 在 r=10 µm 给出约 20.33 µM，仅用于数量级核查；实际是有限域、有限半径源、瞬态扩散并含摄取，不能把该近似当作真实浓度答案。

dt=0.01 s 下每步自由游动 0.25 µm，tumble dwell 有 10 步分辨率。3D 显式扩散的稳定上限为 `dx²/(6D)=0.00064102564 s`，每主步至少 16 个稳定子步；更大域及后端选择由通用 field 系统处理。单个 float64 的 256³ 场占 128 MiB，实际峰值还包含候选场、临时数组、输出与 checkpoint；不据此承诺完成用时。

### 可表示的几何增长

原 A/B 方程在 `science/physiology.py` 中先给出理想营养消耗与体积增量；系统提交时通过 `engine/growth_system.py` 的 `realize_capsule_growth` 检查 float64 胶囊几何。提交后的真实体积增量不得超过可用营养乘产率，消耗量与实际 growth rate 都由该几何增量计算。若增量不足以改变胶囊长度/体积，此步不消耗营养，该量留在原胞内库存，后续可继续累积。该规则适用于新profile中所有营养增长模块，没有增加物理池或改变 μmax、产率；正常规模差异只来自浮点几何反算的舍入。

真实 256³ 场的首次检查在第4步遇到约 8.71e-22 molecule 的摄取。直接将这种量乘产率后加入 0.5 µm³ 体积，float64 体积不变；旧写法却扣除了营养，并报告了非零实际 growth rate。上述通用提交规则修正这一问题。它不意味着双精度能分辨任意小的生长速率；它保证未计入几何的营养不会被误记为已转成生物量。

## 注册的随机分裂版本

`division.area_adder@1.0.0` 的固定面积阈值、uniform 分割行为保持不变。新 `@2.0.0` 声明普通输入 volume、输出 divide/birth_area/required_area/blocked，并保存 birth_area 与 required_area 两个逐细胞状态。

每个 cell cycle 开始时，令 `m=max(minimum_area, A(target_volume)-A(birth_volume))`，采样 `required_area=max(minimum_area, abs(m*(1+CV*Z)))`。这保留原 B 对负样本取绝对值、对零样本应用正 floor 的语义。CV=0 时直接取 m，不消耗 threshold 随机流。每次符合阈值且几何可分割的尝试按 clipped Normal 生成比例；split_sd=0 不抽样。阈值在延迟/碰撞阻止分裂时保持，成功分裂后母 ID 和新子 ID 各开始一个新 cycle，以各自出生体积采样。

随机流 purpose 为 `division_threshold` 和 `division_fraction`；都由现有 seed/node/group/stable-cell/purpose 命名。标准正态使用无缓存 Box–Muller，每个样本明确消费两个 open uniform；不要求与原 B NumPy RNG 逐样本相同。候选步失败不提交随机流或新阈值；checkpoint 包含完整周期状态和 RNG，并拒绝阈值低于 floor、随机 namespace 缺失等状态。

CAD 延用沿母细胞 heading 分配两个胶囊、守恒分配体积及胞内量、碰撞可延迟分裂的规则。原 B 的随机子代方向和固定 offset 可能导致重叠，未照搬；本案例不宣称逐轨迹重现原代码。

## 通用径向观测 radial-spheres@1

所有 population 使用同一个系统观测入口。`project.observation` 可成对声明 `radial_center_um` 和严格递增的 `radial_radii_um`；球必须完整处于域内。N5 配置半径 10、20、40 µm。没有这两个配置项时，原有 box/axis 指标与 checkpoint 形状不变。

每组新增可选 `metrics.by_group[group].radial`：

| 字段 | 单位 | 定义 |
|---|---|---|
| center_um | µm | 声明的中心 |
| mean_distance_um | µm | 全部存活细胞的中心距离均值 |
| mean_inward_displacement_um | µm | 存活初始 ID 的初始半径减当前半径的均值，向内为正 |
| live_founder_count / live_descendant_count | cell | 存活初始 ID / 新生成子 ID；不是谱系总人数 |
| shells[].radius_um | µm | **累积球**半径，shells 并非互斥环带 |
| shells[].live_count / live_fraction | cell / 1 | 当前该球内全体 live 数及占该组 live 的比例 |
| shells[].volume_enrichment | 1 | live_fraction 除以球体积/域体积 |
| shells[].founder_ever_arrived_fraction | 1 | 曾在任一数值步边界进入闭球的初始 ID 比例，分母固定初始人数 |
| shells[].founder_mean_residence_s | s | 初始 ID 球内停留时间的左端积分均值，死亡后不再累计 |
| shells[].founder_mean_first_arrival_s | s | 只在已到达初始 ID 中计算首次步边界进入时间的均值 |

空分母返回 null。边界采样不声称捕获单步内进入又离开的所有事件；统计逐数值步执行，不受保存帧间隔影响。径向历史使用 observation_state_version 0.2.0；现有 metric_version 0.1.0 保留并增加可选 radial。Catalog 列出全部新增 metric ID、单位与定义。

初始细胞反演配对使径向初始分布相同，但两组在同一场竞争，单个细胞不是独立实验重复。比较结论应补充独立 run seed、标签互换，必要时再做 PTS-only 与 control-only 场景。旧 +X 漂移只是辅助检查；中心对称趋化的 +X 平均可以为零。

## 100 s 内应期待什么

即使无限营养，最大体积增幅也只有 `exp(0.005×100/60)-1≈0.837%`，远低于平均分裂所需增幅。G=1 的饱和转运上限是 10000 molecule/s，对 V=0.5 µm³ 的初始比生长率上限仅约 0.0014007/min，低于 μmax。

完全饥饿且 G=1 时，B 规则为 `dH/dt_min=0.01(1-H)-0.002`，从 H=1 趋向 0.8；100 s 后约 0.996694，甚至无限长饥饿也不会低于 0.05 死亡 onset。这是源模型的限制，不应通过暗改参数制造死亡。

因此验收检查机制、守恒账本、状态变化与恢复一致性；不要求这 200 个默认细胞在 100 s 内出现分裂或死亡。专门的短程生命周期测试使用标明为 constructed 的临界初态/参数，验证实际分裂、死亡与回滚路径，不能把它们混成原参数实验结果。

## 已执行的真实网格科学检查

2026-10-02，使用本案例原样256³网格、200细胞、dt=0.01 s，单进程只保留当前状态。首次CPU检查在第4步发现转账精度问题；随后通用可表示摄取规则与上述增长提交规则修复后，CUDA后端通过100步（1 s）检查。此项不比较运行速度，不等于10000步完成。

第100步场浓度最小1.41805201943876e-20 µM、最大45.1503560924199 µM；两组各100个细胞均有正摄取。累计摄取1718.64075545018 molecule，增长使用1718.64074801893 molecule，胞内剩余7.43124852825147e-6 molecule；全部数值步的场与生理守恒审计通过。没有分裂或死亡。第10步曾出现3.87491454674785e-83 molecule的正摄取量，未引发生长、健康或转账错误；尚未形成可表示体积的营养保持在胞内库存。

测试日志位于本机临时目录 `friskoli-n5-science-256cube-100steps.log`，包含普通节点参数、每步场极值、摄取/增长/库存/健康/径向统计；未保存100份全量场。模板显式推荐 `numpy-cupy-cuda`，无可用CUDA时应明确选择其他后端，不静默替换。
