# 尚未完成的计划与验收清单

更新：2026-10-02。核对基线：`feat/n4-design` / `f8466a5`。本页汇总仍有效的需求、设计计划和验收缺项，供后续逐项更新；本次整理没有实现下列功能，也没有运行新的模拟。历史测试结果保留在原验收文档，不把旧文档中的“下一步”全部当成当前待办。

**当前最先完成的是 N5 的真实 10000 步及主前端全流程验收。** 真实 256³、200 菌体目前只验证了前 100 步；完整场景 preflight 通过不能代替求解和结果检查。小时级运行还有实现缺口。其余条目保留原有依赖，不另建新的阶段编号，也不把全部长期设想加入 N5 门槛。

共列出 52 项。状态含义：**待实现/待修正**表示尚无该能力或已有行为需要修改；**部分实现**表示已有基础、仍缺表中内容；**待验收**表示已有实现但证据不足；**条件后续**表示需要数据、具体科学用例或发布决定才能实施。一个条目列有两种状态时，须分别完成实现和验证。

所有计算机制继续遵循**系统 → 基础模块 → 特殊模块**：系统管理调度、数据、约束与提交；模块声明自己的方程、参数、状态和来源；特殊模块同样遵守系统规则。案例只组合普通对象和模块，不增加案例专用的隐藏计算路径。已实现模块及其 profile 边界见[模块指南](module-guide.md)。

## 1. 当前验收与已发现的修订

| ID | 状态 | 尚未完成的内容与完成条件 | 依据 |
| --- | --- | --- | --- |
| A01 | 待验收 | 完成 128 × 128 × 128 µm、0.5 µm 均匀格距（256³）、200 菌体（PTS 与固定 bias 对照各 100）、中心有限养分源的 10000 步实际运行。按当前模板 `dt=0.01 s` 为 100 s 物理时间；检查非负有限场、物质量账、无穿透、事件与最终径向指标。不得用前 100 步、旧小场 1900 步或有符号 X 漂移代替。 | [N5 验收](verification-n5-simulation.md)、[来源与场景](science/n5-b-lifecycle.md) |
| A02 | 待验收 | 在同一主前端完成大场景加载、对象/参数修改、检查、运行、完整回放和全部适用导出，并重新读取项目、运行、设计包、CSV/报告及标准子集。另完成“至少两候选＋明确对照”的真实设计包比较流程。当前主界面短任务及单候选两步检查不能代替完整产品验收。 | [主界面记录](verification-main-ui-task05.md)、[发布目标](design/roadmap-and-acceptance.md)、[设计工作流](design-workflow.md) |
| A03 | 待修正、待验收 | 补齐已发现的操作规则：Workflow 右键删除未跟随菌群锁定禁用，点击后才报错；`settings` 诊断除 `dt_s` 外都定位到 Steps，预览格距/backend/output 未定位实际控件。随后按用户路径复查按钮、禁用原因和检查定位。优先完善动作前的规则与校验，不用失败重试代替。 | [交互设计](design/product-and-interaction.md)、[主前端实现](../src/friskoli_cad/web/app.mjs) |

壁钟加速效果由用户测试，本清单不增加开发侧加速倍数验收。真实参数下是否出现死亡、分裂或可分辨富集应由计算结果说明，不为展示效果强制触发或调参。

## 2. 系统、协议与任务执行

| ID | 状态 | 尚未完成的内容与完成条件 | 依据 |
| --- | --- | --- | --- |
| S01 | 部分实现 | 继续统一各机制的系统/基础/特殊模块职责与接入方式。多个 profile 仍按已知模块 ID 编排；新增 manifest 不自动取得执行能力。基础 `growth.linear_elongation` 仍只在 legacy；若纳入目标 profile，须通过声明的合同接入，并保持旧时序、状态和结果兼容。 | [模块指南](module-guide.md)、[架构设计](design/architecture-and-packages.md) |
| S02 | 部分实现 | 可安装 registry、依赖解析与包锁：安装/卸载、namespace 与同 ID/version 冲突、实现和数据依赖、API 兼容及升级检查；分别管理物种/参数/模型整合包和可执行插件。显示来源、许可证和执行内容；打开数据包不自动安装代码。现有 Catalog、实现 hash/version lock 和 assembly 数据库不能代替包管理。 | [注册合同](registry-contract.md)、[注册与执行设计](design/contracts-and-execution.md)、[三类包](design/delivery-and-migration.md) |
| S03 | 部分实现 | 发布受控的状态与执行接口：类型化 reads/writes、唯一 owner/合并器、只读 StepContext、initialize/evaluate/propose/commit，以及初始化、分裂、死亡、迁移和序列化策略。现有 `world_access` 与特定运行器的状态管理只是其中一部分；外部实现须经接口和兼容验证。 | [模块合同](design/contracts-and-execution.md)、[状态归属](state-ownership.md) |
| S04 | 部分实现 | 补全端口的 dtype、tensor、实体集合与索引顺序、坐标/轴序、采样位置、domain/grid revision、区间量/速率/事件等组合语义，并提供显式转换、broadcast、map/reindex/aggregate。quantity、shape、单位、物种、菌群 owner 和基本时序检查已经存在。 | [端口与连接设计](design/contracts-and-execution.md) |
| S05 | 部分实现 | 多阶段独立诊断集合与受控修复建议：区分 pass/fail/unknown/not_applicable/skipped，保存预期值、实际值、可翻译消息和可执行修复 command；前置数据缺失时抑制依赖它的重复错误。现有 preflight、code/path 与基本定位继续复用。 | [诊断设计](design/contracts-and-execution.md)、[交互设计](design/product-and-interaction.md) |
| S06 | 部分实现 | 全图 backend/求解组规划、设备驻留与数据转移成本管理。当前显式 CPU/CUDA 加速覆盖场扩散，生理计算仍在 CPU；尚无任意模块独立声明后即可统一调度的 CPU/GPU 插件系统。新增实现须声明精度、资源和一致性范围。 | [编译与调度](design/contracts-and-execution.md)、[数值执行](numerical-resource-execution.md) |
| S07 | 部分实现 | 小时级物理时长：联动服务步数上限、输出间隔建议、Workspace/Design 版本及迁移、前端时长设置、导入导出与批次校验。当前仍有 10000 步/间隔限制。12 h 在 `dt=.01 s` 时需 432 万步，在 `.05 s` 时需 864000 步；不能提高 dt 或壁钟上限冒充支持。还需长期输出、事件缓存、保存恢复和科学稳定性验证，同步入口保持独立有界。 | [12 小时实现缺口](task-performance.md)、[科学时间尺度限制](science/n4-semantics-audit.md) |
| S08 | 部分实现 | 将完整 checkpoint 接入任务服务与 UI：暂停、保存、导入、续算和任务关联；服务重启后的 interrupted 记录可按明确操作继续计算。连续与恢复运行应在声明容差内一致，RNG/ID/模块状态完整。现有独立库/CLI 已能恢复，任务 Reconnect 当前只恢复查询。 | [Checkpoint](checkpoint-files.md)、[任务服务](task-service.md)、[N7](design/roadmap-and-acceptance.md) |
| S09 | 待实现 | 在已提交边界预演并执行环境/网格/模块状态迁移，记录映射与事件；保存单位、库存、RNG、ID 和未来日程。同域守恒重划与改变物理域分开处理；无映射时拒绝，替换失败仅取消替换，原结果仍可查。已有独立 regrid 不能代表运行中迁移完成。 | [协议扩展](protocol-expansion-plan.md)、[N7](design/roadmap-and-acceptance.md) |
| S10 | 部分实现 | 通用逐帧二进制/typed-array/增量数据合同，以及浏览器按需分块读取、有界解码与 replay 缓存。当前已有 JSON 分块、稀疏输出、体积平均预览和完整末帧 NPZ；16 MiB 只约束恢复元数据，未约束解码块与完整回放的峰值内存。 | [传输设计](design/contracts-and-execution.md)、[主界面内存边界](verification-main-ui-task05.md) |
| S11 | 部分实现 | 任务数据自动清理与管理入口：把已有 `TaskService.reap()`、`prune_events()` 接入可配置后台维护/管理 UI，遵守活动任务与幂等保留期，清理结果可见且可核对。 | [任务服务](task-service.md) |

S07 是保留的长时运行需求，不把“12 h 已跑通”作为本轮 N5 的既成事实或临时新增的验收任务。实现时应逐合同决定兼容策略，无须修改所有历史 Schema。

## 3. 基础模块与特殊科学模块

| ID | 状态 | 尚未完成的内容与完成条件 | 依据 |
| --- | --- | --- | --- |
| M01 | 部分实现 | 补齐按用例需要的基础数学/控制库：单位转换、广播、显式 delay/filter/持续阈值及更完整几何派生量；每项声明初值、单位、作用域和状态恢复。已有 previous-step、模型记忆与持续匮乏规则不等于通用控制库齐备。 | [基础模块规划](design/numerics-and-modules.md) |
| M02 | 部分实现 | 空间/科学 profile 的环境日程模块与编辑入口：时变局部入口、瞬时投料、任意初始场、复杂浓度维持条件。逐物种记录净输入，明确事件时刻及续算后的日程；当前 legacy 全域速率日程、有限局部源、初始线性梯度和显式均匀储库已实现，新 profile 仍拒绝非空 controls。 | [环境日程](environment-schedule-plan.md)、[状态归属](state-ownership.md) |
| M03 | 待实现 | 可导入的物种/介质属性目录：物理量、单位、适用条件、来源、不确定性与未知值；黏度由环境拥有，模块明确其对 D 或运动的作用。当前扩散系数显式给定，无黏度推算；仅登记属性不产生逐步计算。 | [状态归属](state-ownership.md)、[协议扩展](protocol-expansion-plan.md) |
| M04 | 条件后续、待设计 | 若需同一机制同时分配维持与增长，设计通用胞内库存分配模块：同一份 accepted amount 只入账一次，维持、生长和余量显式结算。目前 reserve 与来源 growth 为互斥 owner，preflight 禁止同时消费；不能靠重复接线实现两者共用库存。 | [库存与组合限制](module-guide.md)、[基础生存](science/foundation-composition.md) |
| M05 | 待实现，可选机制 | 膜材料、受体与表面资源模块：内外膜库存、合成周转、受体密度/分布、新增极区与分裂材料结算。几何面积与可用材料分开；未启用模块时不额外限制生长。当前面积读数、营养增长/分裂已实现。 | [膜面积与材料](membrane-area-and-crowding.md)、[N6](design/roadmap-and-acceptance.md) |
| M06 | 条件后续 | 按科学用例接入耗氧、气液交换等独立过程；提供方程、参数证据、物种账与适用边界。当前 `oxygen` 登记不会自动产生氧场、耗氧或输运行为。 | [环境日程](environment-schedule-plan.md)、[协议扩展](protocol-expansion-plan.md) |
| M07 | 部分实现、条件后续 | 完整 Cellulose 材料化学与产物化学计量，以及分泌酶等替代降解提供者；逐机制建立实物含义、单位、反应与物质量账。当前材料盒、有限接触酶降解和 direct-bulk 产物场已实现，追踪营养当量，尚非完整纤维化学。 | [科学模块规划](design/numerics-and-modules.md)、[N6](design/roadmap-and-acceptance.md) |
| M08 | 条件后续 | 黏附/脱附、连续侵蚀及接触力机制分别建模。当前碰撞为几何阻挡，部分降解保留原盒边，耗尽后才移除；新增机制需有接触/几何演化验证，不能把任意反弹当作真实力学。 | [接触机制规划](design/numerics-and-modules.md)、[N6](design/roadmap-and-acceptance.md) |
| M09 | 条件后续 | 死亡残体与向环境释放的显式模块；声明残余物质去向、释放速率与碰撞体生命周期。目前死亡残余计入 removed-residual 账，不自动回流外场。 | [生命周期边界](module-guide.md)、[数值模块规划](design/numerics-and-modules.md) |
| M10 | 条件后续 | 有来源的外部速度场与守恒平流、其他边界、表面/体场映射、高阶匹配采样沉积和任意物理三角网格。按具体用例检查单位、网格闭合/法向/尺度、碰撞或体素化，以及固定物理支持下的库存和 dt/dx 误差。通用 CFD 不在首版门槛。 | [空间与输运规划](design/numerics-and-modules.md)、[N8](design/roadmap-and-acceptance.md) |
| M11 | 部分实现 | 转化指标、完整谱系分析和更多声明清楚的观察器。已有数量、通道、库存、方向位移、区域/径向到达与驻留；新增量须定义分母、时间窗、未到达/缺值处理及生命周期归属。 | [统计与比较规划](design/numerics-and-modules.md) |

膜材料、完整化学计量和高级力学按实际案例选择；它们的缺失必须在模型适用范围中说明，不能据此宣称当前已有的营养当量守恒或碰撞检查全部缺失。

## 4. 工作区、交互与模板复用

| ID | 状态 | 尚未完成的内容与完成条件 | 依据 |
| --- | --- | --- | --- |
| U01 | 部分实现 | 受管理模板的完整事务：统一 `generated_by/owner_object_id/template_version/overrides`，支持解除托管、模板升级冲突处理、复制 ID/引用重写预览、删除依赖预览和保留可修复悬空草稿。已有菌群分支复制、assembly 提取/应用/Undo 与自动数据节点继续复用。 | [对象与图联动](design/product-and-interaction.md) |
| U02 | 部分实现 | 能力驱动的共享 command 目录，供菜单、右键、触屏“更多”和快捷键使用；统一禁用原因。补齐按有朝向外廓对齐顶/底面、空间中心定位、重置初始分布等声明动作。 | [命令与对象操作](design/product-and-interaction.md) |
| U03 | 部分实现 | 放置对象的“预览位置→属性→确认”事务，以及重散布替换清单、数量不足时显式接受较少数量的流程。当前放置后编辑、可撤销创建和原子失败回滚已有；不能静默减少菌数。 | [放置与散布](design/product-and-interaction.md) |
| U04 | 部分实现 | 区分初始梯度、有限源和维持浓度边界的操作向导；删除梯度时预览会删除的初始化/源/边界及受影响连接。已有这几类机制的参数编辑，不等于向导已实现。 | [梯度交互](design/product-and-interaction.md)、[环境日程](environment-schedule-plan.md) |
| U05 | 部分实现 | 参数按“必须提供/有来源参考值/待研究”分层，引导少量必要输入；明确 unknown/不适用和不确定性，支持有 seed 的群内参数分布。当前真实参数、单位、来源、缺参检查和驱动模块定位已接入。 | [参数交互](design/product-and-interaction.md) |
| U06 | 部分实现 | 持久化已知 Catalog，支持离线冷启动阅读/编辑，以及目录/内核级显式重连和编辑状态保留。当前活动页面可编辑草稿、恢复一个草稿和重连任务；冷启动仍依赖 Catalog、示例与 capabilities 请求。 | [离线与连接状态](design/product-and-interaction.md) |
| U07 | 部分实现 | 最近项目列表与完整 ViewState：相机、选择、展开、阅读位置等可恢复且不污染科学输入。当前只有一个草稿恢复入口；面板尺寸/语言/部分偏好已本机保存，图布局/折叠可随 Workspace 保存。 | [启动与视图状态](design/product-and-interaction.md)、[状态分工](design/architecture-and-packages.md) |
| U08 | 部分实现 | 分辨率指导：菌体跨格数、模块/源/障碍尺度的格距建议、细化教程、收敛证据驱动的推荐与耗时估算。当前物理尺寸、计算/预览格距、资源预检和部分内存/输出估计已显示；建议不能超出验证范围。 | [用户历程](frontend-user-journey.md)、[空间尺度](spatial-resolution.md) |
| U09 | 部分实现 | Results 的 3D 轨迹叠加、事件类型过滤和时间区间选择、候选共同参数/差异视图，以及有统计依据的置信/不确定性区间。现有 XYZ 截面、径向图、曲线、死亡详情和时间线保留；均值/样本 SD 不充当置信区间。 | [结果交互](design/product-and-interaction.md)、[比较边界](design-workflow.md) |
| U10 | 部分实现 | 模块数学说明、公式编号、符号和实际端口之间的双向定位，完善 Mathematical model 阅读组织。当前 KaTeX、符号表、端口说明、源码/测试引用和 Evidence 已实现。 | [Workflow 阅读](design/product-and-interaction.md) |
| U11 | 待验收 | 实体鼠标/触控板/触屏、单双指切换、pointercancel、轻触/拖动、键盘、200% 缩放、软键盘、安全区、真实横竖屏及滚动阅读位置。分别记录桌面、平板、手机横竖屏；已有四尺寸浏览器模拟和部分手势逻辑验证不能代替实体实测。 | [交互规范](interaction-specification.md)、[主界面记录](verification-main-ui-task05.md)、[静态查看记录](verification-n5-delivery.md) |
| U12 | 部分实现 | 按实际修改需要继续分离 document/view/run 状态与 command 层，减少大型 app 文件中的交叉职责。保留已拆分的任务存储、图组件、模板库和结果组件；用晚到响应、撤销、冻结运行、刷新恢复验证边界，不为目录图进行全栈重写。 | [架构与文件职责](design/architecture-and-packages.md) |

## 5. 文件、标准与正式交付

| ID | 状态 | 尚未完成的内容与完成条件 | 依据 |
| --- | --- | --- | --- |
| D01 | 待实现 | 旧 WebUI `format/version/scene/workflow` 的独立转换器、迁移预览与损失报告；保留原件和未知字段，不与当前 Workspace 混用，不为旧展示对象补造科学参数。 | [旧格式迁移](design/delivery-and-migration.md) |
| D02 | 部分实现 | 原生设计包/Wiki 与大型外部数组的便携打包或可靠关联，包括完整末帧 NPZ 的复制、hash 校验、缺失提示与重读。当前包内已有回放 JSON，NPZ 可独立下载；记录中的 artifact 链接不等于二进制已内含。逐帧数组的外置二进制打包、便携关联及按需载入仍待完成。 | [原生包设计](design/delivery-and-migration.md)、[Wiki 数据边界](wiki-publishing.md) |
| D03 | 待实现，有支持子模型后验收 | SBML 转换器：选择明确支持的动力学子模型，完成语义、结构、回读和外部数值对照，说明空间 ABM/插件的损失。当前不支持 SBML。 | [标准支持范围](standards-export.md)、[标准分工](design/delivery-and-migration.md) |
| D04 | 待实现，依赖 D03 或其他可识别模型 | SED-ML 的模型编码/算法映射及外部实验复现；模型引用、求解设置和任务可由目标工具识别。当前不生成 SED-ML 文件。 | [标准支持范围](standards-export.md)、[标准分工](design/delivery-and-migration.md) |
| D05 | 部分实现、依赖真实数据 | 扩展 SBOL 的 sequence/features/interactions/model linkage，并在有真实序列与适当注释后支持 GenBank/FASTA。当前已实现显式 Component 核心属性、官方工具验证及回读；没有序列不能生成序列文件，表型参数不冒充 DNA 元件。 | [标准支持范围](standards-export.md) |
| D06 | 待验收 | 用独立第三方 OMEX 工具核验实际交付归档，记录容器验证与外部求解各自结果。当前 manifest/hash/原生字节保留与本地回读已验证，尚无独立 OMEX 验证器证据。 | [交付记录](verification-n5-delivery.md)、[标准支持范围](standards-export.md) |
| D07 | 部分实现、待发布验收 | 确认根项目公开许可证、来源模型/依赖再分发许可、真实下载地址；交付可复现完整示例与操作教程，在另一台机器/拟支持平台从正式发布包启动。现有 wheel 已做同机隔离安装；未进行全新机器验证，根目录无公开 LICENSE，未交付 Windows 安装器。安装器/桌面壳按发布需要另行评估。 | [开发与迁入规则](../CONTRIBUTING.md)、[交付设计](design/delivery-and-migration.md)、[候选包证据](verification-n5-simulation.md) |
| D08 | 部分实现、待发布验收 | 公开 Wiki 部署前核对真实计算来源、数据许可、下载位置与资源规模，补真实环境对象样例及设备检查。当前共享 3D viewer、真实 1901 帧本地静态样例与四尺寸检查已完成；网络下载已有分块进度，多样例清单、按帧/块增量解析、按需加载及有界缓存仍属规模扩展。Wiki 保留现有实现，不能代替 A01/A02。 | [静态发布](wiki-publishing.md)、[交付验证](verification-n5-delivery.md) |

## 6. 科学证据、条件研究与数值扩展

| ID | 状态 | 尚未完成的内容与完成条件 | 依据 |
| --- | --- | --- | --- |
| R01 | 科学证据仍有限 | 已完成 N3 主研究、追加方向诊断与等价复核；仍需在声明范围内研究 A 的时间步敏感性，以及 B 群体方向增益能否从 seed 噪声中分辨。根据问题选择更长观察、多 seed、dt/grid 收敛和功效分析，不把已做的诊断重复列为未做，不调参伪造正结果。 | [N3 比较](science/n3-comparison.md)、[完整 N3 验收](verification-n3-complete.md) |
| R02 | 待实现，依赖数据 | 实验数据合同、单位/坐标转换、观测算子、空间残差和误差模型；明确模型变量如何对应实验观测。外部建议只作为研究输入，未合并贡献代码。 | [开发安排中的研究路线](development-order.md)、[标定规划](design/numerics-and-modules.md) |
| R03 | 条件后续 | 实际构建体参数标定、可辨识性、留出验证、敏感性与适用区间图，以及小时级生物预测验证。当前参数为 source-derived 或 constructed；没有真实菌株实验数据，不能宣称完成生物标定或 12 h 预测验证。 | [N8](design/roadmap-and-acceptance.md)、[时间尺度限制](science/n4-semantics-audit.md) |
| R04 | 条件后续 | 比较的置信区间、统计检验、参数不确定性传播和多目标/Pareto 分析；预先规定指标、重复、配对与缺失规则。当前有限参数候选、结果约束和描述性均值/SD 已实现，不宣称统计显著性。 | [设计交互](design/product-and-interaction.md)、[设计工作流](design-workflow.md)、[比较规划](design/numerics-and-modules.md) |
| R05 | 条件后续 | 完整碳/能量/生物量/蛋白/产物化学计量，以及更详细受体、鞭毛和 motor 到整菌运动的映射。依具体研究问题提供机制和实验证据，分别注册；当前营养当量账、来源信号和运动映射保持其明示范围。 | [基础组合边界](science/foundation-composition.md)、[科学机制](science/n3-mechanisms.md)、[数值规划](design/numerics-and-modules.md) |
| R06 | 条件后续 | 运动中连续感知、刚性反应、强 MCP–PTS 耦合求解和精确事件时间；必要时采用有误差估计的自适应积分。现有显式分拆、稳定扩散子步、共享通量和原子回滚已实现。常规输入/规则错误在运行前处理；自适应步进只解决数值误差，拒绝步骤须恢复全部状态/RNG，代数环不能靠排序解决。 | [时间推进](design/numerics-and-modules.md) |
| R07 | 按实际瓶颈推进 | 更多向量化/并行、稀疏场、数据驻留与 I/O 优化；新数值实现继续做结果一致性和资源边界检查。CPU/CUDA 扩散和大场数组已有实现；不把提高显示帧率或减少科学计算冒充求解加速，壁钟加速由用户验收。 | [N8](design/roadmap-and-acceptance.md)、[数值资源执行](numerical-resource-execution.md) |

## 7. 不再算作当前欠缺的内容

- **已实现，继续保留：** 基础无偏运动与碰撞、有限局部源/接触降解、同场释放扩散摄取、基础储备与匮乏死亡、来源生长/表达/健康/分裂、完整独立 checkpoint、quantity 等基本端口检查、模块名称和 ID、面板尺寸拖动、模板库 validate/官方目录/Apply Undo、分批设计与选择性重跑、IndexedDB 恢复元数据、OMEX/显式 SBOL 子集、共享静态 3D viewer、Task 0.5/CUDA 扩散/预览/末帧 NPZ/径向指标。完成边界分别见[模块指南](module-guide.md)、[N5 记录](verification-n5-simulation.md)与[主界面记录](verification-main-ui-task05.md)。
- **已调整或取消：** 三池输运退出必需项；温度/PCR 模拟不在当前计划；不通过负浓度表达排斥；不为展示强制死亡/分裂。见[开发安排](development-order.md)。
- **仍为远景/按需评估：** 复杂自由浮动停靠、完整布局预设/通用渲染调参面板、SSE、受控数学 AST、桌面壳、通用 CFD、自动优化/序列设计、在线社区和分布式 GPU。不把这些升级为本次 N5 的漏交项。见[界面范围](design/professional-ui-reference.md)、[架构](design/architecture-and-packages.md)、[合同](design/contracts-and-execution.md)与[路线](design/roadmap-and-acceptance.md)。
- **贡献与发布边界：** `contrib/repro-cli`、`contrib/plugin-sdk` 继续只审阅、不合并；不为目录设计创建空目录，不全栈复刻，不因本次文档修改重新构建安装包。具体科学扩展和公开发布按对应条目另行完成。

## 8. 后续怎样更新

先完成 A01–A03 及实际验收暴露的必要修正；实现缺口按[开发顺序](development-order.md)和依赖推进。每次更新本页的条目状态，并链接提交、验证记录和仍存边界；实现完成但未验收时保留“待验收”。只有所列完成条件有证据时才移到已完成记录。原计划中的设计动机和历史测试不追溯改写。
