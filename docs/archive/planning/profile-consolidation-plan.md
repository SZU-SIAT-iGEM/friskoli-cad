# 第一版统一执行规范：实施计划与验收标准

日期：2026-10-04。调查基线：3a5d3be，分支 chore/debt-cleanup。本次只同步讨论、调查代码并编写计划，尚未实施合并。

**决定：采用 modular-spatial-v1 作为第一版唯一执行规范。现在可以直接改接口、协议、模块组织和预设，删除旧实现；发现 modular 本身的问题也直接修正。验收看科学与软件行为是否正确，不要求复现旧 profile 的轨迹，也不建设旧格式兼容层。**

这是琥珀在本次同步后明确的发布原则：尚未公开的历史实现属于开发过程，正式发布才建立第一版支持范围。

## 1. 三份讨论统一后的主线

依据：用户提供的 Claude 对话，以及 [评估 CAD 模型迁移一致性](codex://threads/01a10103-e9d4-7e92-9654-8734bd9df462)、[评估 friskoli-cad 首发准备度](codex://threads/01a100ff-a9fa-70f0-817d-92c1c5803376)。两个审查会话的代码基线较早，以下采用用户后续决定和当前状态。

- **工程研究**：空间生物反应器的局部传质。PTS 连接代谢与趋化，表面展示酶参与中心底物降解；检验聚集、产糖和降解之间的收益，保留无效或反向结果。
- **测量研究**：野生型 MCP–MeAsp 芯片基准，后续与视觉识别/追踪轨迹比较。MCP 富集用于测量核对，工程菌研究仍围绕 PTS 与降解。
- **A/B**：A 的完整来源是 GitHub 稳定版，本地 rebuilt-v2 是其重建版本；B 的原始来源为 ZIP。用户希望研究版本形成较完整与有说明的简化两套机制。现有 A/B 的记忆方程已经不同，研究简化记录省略机制与方程，不用 A/B 名称推断结果可直接互换。
- **架构**：系统管理公共状态、求解、共享结算和事件；普通模块与来源特定模块遵守同一合同。生长、运动、降解等功能都按这个关系实现。
- **交付**：别人能重现主图、替换参数、运行并带走结果。研究使用小域/中域的方形中心底物场景；万步大域、12 小时任务、SBML 和广泛格式扩展另排。
- **生命周期**：旧 lifecycle/N5 演示可删。保留平台通用生长、分裂、死亡能力；工程主案例第一阶段不要求完整来源生命周期。
- **许可与来源**：MIT 已采用，用户同意自有 A/B 以 MIT 公开。此前 Apache-2.0 的建议已被这一决定替代。具体论文引用缺失需要补查，不推断团队是否阅读过原文。

已核对：文档归档 38cd42e、短 README 91fe656、MIT bb9066a、碰撞提速 539ce99、modular 沿壁滑动 1e90053、芯片研究包 3a5d3be。启动器已纳入 Git，目前无 remote。

Claude 记录中的 886 项通过、8-seed 富集与约 467 s 壁钟/300 s 模拟，属于既往记录，本次没有重跑。指标、Design 和完整导出还需接通，尚不能称已完成界面全过程验收。

## 2. 直接采用的第一版规范

### 执行与时间

一套正式 registry，一个 ModularSimulation，一套调度：

**prepare → field → physiology → lifecycle → observation → 原子提交**

- 输入边明确标记 same_step 或 previous_step，禁止读尚未结算的结果。
- field 阶段先结算来源、材料释放、外部交换和共享摄取，再推进有限体积扩散。这是本版选择的 operator splitting；用时间步/网格检查评价误差。
- 信号与运动按 graph 声明的时间边执行，不隐式提前或延后一拍。第一版芯片保留现有 motor_bias → motion 的 previous-step 边。
- 所有库存扣除、运动约束、生长和身份事件由系统处理；模块提交提案，不直接改其他模块的状态。
- 观察器读取完成本步物理过程后的状态，按每个数值步更新。帧输出间隔只改变存储频率。
- 失败的数值步整体回滚，包括 RNG、ledger 和观测状态。

旧趋化的先扩散再摄取不再是本版需要兼容的另一套规则。

### 模块与科学范围

PTS A、PTS B、MCP 作为普通模块/assembly，运行器不按模型名、案例名或 module ID 选择算法分支。保留公共生长、分裂和死亡接口，来源特定机制也从这些接口进入。

旧趋化 registry 中未进入 modular 的六项，按下面的决定处理：

| 旧模块 | 第一版处理 |
| --- | --- |
| field.ideal_local_reservoir | 删除旧实现；研究使用明确的 reservoir/boundary exchange，记录外部物质输入与浓度偏差，不宣称严格恒浓度 |
| expression.surface_copies | 第一阶段中心底物场景使用显式固定酶 copies 与 surface.enzyme_activity；动态表达/负担是另一个科学任务 |
| growth.nutrient_monod、growth.nutrient_yield | 删除旧 profile 的包装，保留现有 modular 生长和库存机制；需要来源生长定律时作为普通模块新增 |
| life.health_balance | 删除旧包装，保留明确的死亡 hazard 机制；不把未标定 health 分数写成实测存活率 |
| division.area_adder | 删除旧包装，保留现有 modular 分裂系统；来源 B 的固定参考面积规则在完整 B 研究中单独实现与验证 |

已有 73 个 modular 注册条目可重组、修正或删去无用项，不以模块数量作为验收条件。删改在清单中说明功能去向。

### 接口与协议

第一版只有 modular 项目和 Task 0.6 的正式运行路径。沿用仍有实际用途的内部格式：Project 0.6、Graph/Module 0.2、Catalog 0.5、Task 0.6、Frame 0.2，以及当前仍被引用的基础 run/frame 协议。

协议数字不决定是否旧。从保留的数据格式和 $ref 依赖清理 schema/validator/OpenAPI；依赖保留，旧执行分支删除。必要时可以直接修改尚未发布的格式，同步前后端、示例与测试即可。

旧项目、任务和 checkpoint 不迁移；新程序返回清楚的不支持此格式错误。禁止静默套用新模型计算旧输入。新版本自己生成的保存文件、checkpoint 和结果包必须可重开与续算。

## 3. 当前代码需要解决的依赖

只读实际加载：legacy 19 项、PTS bulk 8 项、spatial 14 项、chemotaxis 30 项、modular 73 项。这些数字包括基础工具，不代表实验验证过的模型数量。

1. science_adapters.py:adapted_modules() 仍先构造 chemotaxis_registry()，其后还有 spatial/PTS 注册表链。直接复制或重写需要的声明与 evaluator，改为通过公共模块 API 注册。
2. modular runtime/checkpoint 从 runtime.py 获取 World、Geometry、Snapshot、ModuleRegistry 和错误类型；多个模块从 pts_modules.py 取端口工具，checkpoint 从 spatial_checkpoint.py 取公共工具，project.py 从 spatial_runtime.py 取资源上限。提取共享部分后删除旧运行器。
3. engine/__init__.py 还导入旧默认 registry/Simulation；project、tasks、replay_service、registry 与前端均有多 profile 分支，需要一起清理。
4. design-panel.mjs:96 拒绝 modular，task-store.mjs:384 只为旧 profile/Task 读取指标；modular snapshot 尚未接入区域观测，wiki_export.py:read_run_export() 拒绝 Task 0.6。
5. 12 份趋化项目仍用旧 profile；现有 modular 内置项目只有 foundation/material，芯片研究脚本另行构造 modular 项目。正式预设直接重新搭建。

这些是代码调查发现；旧版本是否正常运行不作为删改前置条件。

## 4. 实施计划

从 3a5d3be 的代码建立独立开发分支，例如 chore/unify-profile，使用独立 checkout，保留原分支供研究继续。每阶段一个可检查的提交。熟悉代码后的初估约 4–6 个专注工作日，完整 A/B 科学补全另计。

| 阶段 | 工作 | 完成条件 |
| --- | --- | --- |
| P0 规范与范围，半天 | 按第 2 节固定执行顺序、状态/单位/时序合同、正式格式和保留模块。记录原 commit 与环境用于诊断 | 当前支持矩阵和模块去向表；不运行全套旧 profile 对照 |
| P1 公共实现，一天 | 移出 World/Geometry/Snapshot/registry/errors、端口声明、空间上限和 checkpoint 工具。正式模块直接注册；修复发现的问题 | modular 可独立加载，生产路径不调用旧 registry；模块与系统职责清楚 |
| P2 研究流程，一至两天 | 接通每步观测、沿轴平均位置、Design、多 seed 比较、任务指标、回放、Task 0.6 Wiki 与结果包 | 脚本与 UI 同条件得到相同指标，导出后能脱离原服务读取 |
| P3 删除与重搭，一天 | 删除四套旧执行器/registry/API/前端入口；清理无依赖协议。重搭 MCP 梯度/零梯度、中心底物 A/B/匹配对照、最小平台案例 | capabilities 只声明 modular；正式预设可验证和执行，旧输入明确拒绝 |
| P4 第一版验收，半天至一天 | 运行保留能力的必要测试、主案例、恢复、导出和隔离安装；更新 README/支持矩阵/限制 | 按下表交付日志、数据与制品；实际科学未完成项明确列出 |

P1–P3 可以按依赖调整提交顺序。无需把旧代码先修到可用、开发转换器、维持旧 public API，或建立新旧运行器双轨执行。

### P2 的具体指标与交付

- 区域占比、径向富集、到达与驻留使用统一定义；沿观测轴平均位置用于漂移计算。明确空群体 null、存活细胞/founder 的分母和边界包含规则。
- 脚本与 UI 共用指标。芯片漂移定义为 10–120 s 之间平均位置的线性拟合；多 seed 以独立 run 为统计单位。
- 观测历史进入 checkpoint，稀疏输出不会丢掉到达/驻留事件。
- Design、Task 0.6、Catalog 0.5、回放和导出采用一致的能力声明，去掉旧 profile 硬限制。
- .friskoli/Wiki 包携带所交付场景需要的数组、hash、参数、seed、模块/软件版本。大数据使用已有数组引用/分块机制，文件路径在包内可解析。
- 非作者完成安装、载入、改参数、运行、比较、回放、导出、关闭原服务、重读；静态演示标明预计算结果。

### P3 的研究范围

芯片使用已有 400×800×200 µm、20 µm 网格、200 细胞、dt=0.1 s、k=0.3 配置。说明原设计长度为 4 mm，计算场景缩短到 0.4 mm 并保留横截面；参数修正有依据并记录。

工程菌研究使用方形小/中域、中心材料、表面酶、PTS、摄取和运动。A/B 使用共同环境与观测定义；匹配对照只关闭感知到运动的反馈，保持摄取、表达设定和初态。聚集用于解释结果，累计底物转化量及其按初始菌量归一化的值用于评价降解收益。

A 稳定版缺失的表面糖池、接触 QSSA、黏附/脱附反馈，以及 B 的机制简化，作为后续科学任务。第一版可先使用明确的研究近似，不能据此写成完整来源复刻。完整工程场景尚未做出的正反馈属于待检验结果，不设必须富集或必须增益门槛。

## 5. 第一版验收标准

| 编号 | 必须证明的结果 | 证据 |
| --- | --- | --- |
| G1 单一执行规范 | 一个正式 registry/ModularSimulation；无旧 runner 可执行分支，新旧输入有明确接受/拒绝规则 | 导入/调用检查、capabilities、保留与退役清单、错误案例 |
| G2 模块复用 | A/B/MCP 使用同一合同；换一个兼容机制只改 graph/参数。通用生长、事件能力仍可用 | 三组 graph、模块来源/单位/版本、替换案例、保留功能的测试 |
| G3 科学与数值 | 方程/单位/时间顺序正确，确定机制对解析极限或独立计算；必要 dt/dx 检查支持主要结论 | 方程表、小型独立对照、误差与适用范围；不要求旧 profile 输出相同 |
| G4 库存与事务 | 场/库存非负；封闭系统守恒，外部输入和反应计量可追踪；accepted 摄取等于实际扣除，含极小可表示量；失败步整体回滚 | 复用守恒/回滚测试，修复审查发现的微量结算问题，提交量纲明确的误差 |
| G5 保存与恢复 | 同一新版本内，连续 N 步与 K 步→保存→重启→N-K 步一致；改变帧输出间隔不改变状态和观测历史 | RNG、ledger、数组、身份事件与指标对照；不测试旧版本 checkpoint 迁移 |
| G6 研究可复现 | 同参数、初态、seed 时脚本/task/UI 指标一致；多 seed 报效应量与区间，以 run 为重复单位 | CSV、主图、研究包与复现命令；不把 200 细胞当 200 次独立重复 |
| G7 使用与制品 | 正式安装包可运行；Design、回放、Task 0.6 导出和重读贯通；结果可脱离原服务使用 | 非作者操作记录、run ID、wheel/结果包/hash、Python/Node 日志 |
| G8 壁钟可用 | 记录 120 s 模拟出现曲线及 300 s 完成/包可读的端到端时间、峰值内存；参考机器上单个芯片候选 300 s 计算并打开结果，以 10 分钟内为第一版目标 | 同条件至少 3 次串行测量的中位数，分别报告引擎/任务/UI 开销；超目标定位实际瓶颈并优化 |
| G9 描述真实 | 当前文档/capabilities/预设与实测一致；说明科学近似与未标定项 | 唯一支持矩阵、参数出处、已知限制、模块删改说明 |

G3/G4/G5 的正确性优先于性能。沿壁滑动在第一版继续采用，并检查穿透、重叠及阻塞行为；不恢复旧运行器的冻结壁面行为。

G8 的 10 分钟是本任务设定的交付目标，不是已有成绩或 iGEM 规则。原约 467 s 数字来自裸引擎，前端全过程还未测。不得通过暗改 dt、网格、菌数或科学参数宣称同条件提速；研究规模调整在配置中明确。

软件测试证明实现与交付可靠；工程菌收益、实验吻合和完整来源复刻分别需要科学证据。负结果可以通过验收，缺乏数据支持的正向结论不能通过。

## 6. 开工命令与最终交付

以下芯片参数已按当前 argparse 核对；执行者生成新版本结果。20 s 用于流程检查，120/300 s 用于相应研究窗口。runs/ 已被 Git 忽略。

```powershell
$env:PYTHONPATH = 'src'
./.venv/Scripts/python.exe -B research/chip-benchmark/chip_benchmark.py --duration 20 --seeds 2 --workers 1 --out runs/first-release-smoke
./.venv/Scripts/python.exe -B research/chip-benchmark/chip_benchmark.py --duration 120 --seeds 8 --workers 1 --out runs/first-release-120s
./.venv/Scripts/python.exe -B research/chip-benchmark/chip_benchmark.py --duration 300 --seeds 8 --workers 1 --out runs/first-release-main
./.venv/Scripts/python.exe -m pytest tests -q
node --test tests/*.test.mjs
```

先补齐开发依赖，再运行保留能力的测试；缺依赖的 skip 不算通过。迁移有价值的守恒/恢复/事件/几何测试，移除只检查已删除入口的测试。最终不以测试数量或旧输出一致性证明正确，也不反复运行没有新问题的长时扫描。

交付者提供五项：

1. P0–P4 的独立 Git 提交、最终 commit 与安装制品。
2. 唯一当前支持矩阵、模块去向表、执行与协议规范。
3. 必要自动测试、独立数值对照与恢复记录。
4. 脚本/UI 的主图、CSV、可携带结果包与端到端耗时。
5. 科学假设、删改说明及尚未完成的 A/B、视觉与实验任务。

**实施者任务摘要**：采用 modular 为第一版唯一标准。直接重整公共代码与模块注册，接通研究流程，删除四套旧执行器及不再使用的协议，重搭正式预设。按 G1–G9 证明新版本正确、可用、可复现。无需旧格式兼容和新旧轨迹一致；现有 modular 有问题就修。完整来源模型迁移与实验标定另做科学验收。
