# 交付、整合包、标准与迁移

状态：2026-09-29 目标交付设计，2026-10-02 更新当前能力。现有 Project/Workspace/Result JSON、任务冻结输入与结果块、统计 CSV，以及 M4 独立 checkpoint JSON 文件和 CLI；N4 已提供原生 ZIP 设计包、HTML/CSV 报告与 biological assembly。OMEX、显式 SBOL Component 子集和共享静态 viewer 已实现；通用整合包/插件安装及更广标准适配仍待完成，详见[剩余工作清单](../remaining-work.md)。

2026-10-01 实施注记：M4 文件外层为 `checkpoint_file_version: 0.1.0`，包含冻结 Project 0.4 和内部 `spatial-checkpoint/v2`。文件可在匹配代码/依赖环境的新进程恢复最新已提交状态，不包含此前整条轨迹，不携带可执行插件。它不经项目编辑器导入，也不续接任务服务的旧任务。具体命令、写入保护和兼容限制见[checkpoint 文件](../checkpoint-files.md)，验证见 [M4 验收](../verification-m4.md)。任务 pause/resume 与运行中环境迁移仍属 N7。

完整 N3 当时增加科学 Project 0.5、Workspace 0.4、Task 0.4 与 checkpoint 文件 0.2。文件 0.2 包装动态科学状态，恢复入口和版本锁与旧文件一致。当前 Workspace 保存为 0.6；通用 Task 0.5 与标准子集的范围分别见[场输出合同](../task-field-previews.md)和[标准支持说明](../standards-export.md)。

## 1. 三类包分开

| 包 | 目的 | 导入行为 |
| --- | --- | --- |
| 原生设计包 | 无损保存设计、输入、依赖和可选运行/报告 | 解析与预览；缺依赖可只读打开；不自动执行代码 |
| 模型/参数整合包 | 复用模板、物种、底盘、参数、模型和示例 | 显示新增/冲突/覆盖项，生成锁定依赖 |
| 可执行科学插件 | 提供新的计算实现或导出器 | 独立安装操作，显示来源、许可证和执行内容 |

整合包可以引用可执行插件，但“打开整合包”不自动获得执行任意代码的权限。数据文件和代码的生命周期不同，用户应能只读参数、公式和结果而不安装插件。

上表三类包不等于当前 checkpoint 文件。M4 保存的是固定种群空间运行状态与恢复所需输入，仍执行精确源码及运行环境锁；旧内部 v2 结构形式可检查，不表示不同安装包之间自动迁移科学状态。

## 2. 原生设计包结构

当前 `.friskoli` 已作为 ZIP 容器实现，完整 payload、拆分 brief/candidates、registry/locks、runs 和报告均保留；精确现有布局见[设计工作流](../design-workflow.md)。下图仍是未来更细的目录组织目标，现有 JSON 保存继续可用。不再同时维护 YAML 与 JSON 两份科学事实源：规范文档采用 JSON，YAML 可以作为未来的人类编辑适配格式。

```text
project.friskoli
├── manifest.json                 # 格式版本、内容类型、文件校验和
├── design/
│   ├── brief.json                # 目标、约束、区域、时间、对照
│   ├── candidates.json           # 候选及相对于公共基线的覆盖
│   ├── experiment.json           # 初始化场地、对象、图、日程
│   └── observations.json         # 指标定义、通道与输出频率
├── workspace/view.json           # 可选视图与布局，独立于科学 hash
├── dependencies/
│   ├── lock.json                 # 包/模块/参数集版本与哈希
│   └── registry.json             # 冻结的说明与合同，无自动执行代码
├── evidence/
│   ├── parameters.json           # 数值、单位、来源、条件、不确定性
│   └── references.json           # 来源定位、数据许可、审核状态
├── assets/                       # 有单位与坐标说明的几何/公开数据
├── runs/<run_id>/
│   ├── submission.json           # 该次运行使用的不可变输入
│   ├── manifest.json             # 完整性、实际 backend/版本、索引
│   ├── arrays/                   # 帧、场、轨迹的分块数据
│   ├── events.jsonl              # 生命周期与模型事件
│   └── metrics.csv               # 已定义指标及单位/状态
├── reports/
│   ├── design-report.html
│   ├── alternatives.csv
│   └── validation-plan.md        # 数据缺口和建议验证目标
└── exports/                      # 可选标准子集及损失报告
```

空白草稿可以缺 runs/reports，不伪造推荐结果；结果可按选择打包或外部引用，缺失的外部结果有明确提示。包清单包含文件 hash，不能用文件名猜数据类型。完整结果的原始输入始终保留。版本锁只标识实现，不能代替实现本身；复算还要求取得同哈希的插件代码、运行依赖和兼容 backend。便携交付可在许可允许时附实现归档，仍须独立安装后执行；缺依赖时只能查阅，不承诺可复算。导入器防止路径越界、重复路径、解压规模失控、不合法数组形状或自动反序列化执行对象。

## 3. 科学包结构

```text
friskoli-chemotaxis/
├── package.json                  # namespace、版本、API 兼容、许可
├── dependencies.lock.json
├── manifests/                    # 模块与对象/场登记
├── models/                       # 数学说明、符号与代码映射
├── parameters/                   # 底盘/受体/介质数据与证据
├── templates/                    # 图组合和空间初始化配方
├── implementations/              # 可执行代码，仅经安装启用
├── examples/                     # 最小例子、Friskoli 模型、对照
├── validation/                   # 单元/数值/科学验证及限制
└── docs/                         # 对象用途、参数与操作教程
```

一个物种可被多个包引用，命名冲突需解析，不按受体机制重复注册。参数集允许不同实验条件，但必须显示适用域，不能后导入的文件无声覆盖整个菌株。代码、参数、模板、registry 合同分别有版本并锁定组合。社区仓库和在线发布延期；本地版不自动上传设计或实验数据。

## 4. 外部标准的分工

以下按已核查的官方规范限定语义；引用集中见[证据表](decisions-and-evidence.md#外部依据)。标准输出需要真实转换器与验证记录，不能靠换扩展名或声明“符合 iGEM”完成。

| 输出 | 适合映射的部分 | 本项目必须说明的限制 | 首版门槛 |
| --- | --- | --- | --- |
| SBOL 3 | 设计组件、序列/特征、相互作用、模型关联和来源 | 空间 ABM、运行调度和碰撞细节无通用无损映射；无已知序列也不编造序列 | 有结构化组件信息时导出相应子集，官方工具验证并回读 |
| SBML Level 3 | 可表达的生化动力学子模型；必要时单独支持 Spatial 等 package | 完整任意 Python 插件和 ABM 不直接等价于 SBML；使用 package 还需目标工具支持 | 对一个明确支持的子模型作语义映射、验证和数值对照 |
| SED-ML | 模型引用、求解/实验设置、任务和输出定义 | 描述实验不等于对方具备 Friskoli 求解器；需要可识别模型编码与算法 | 对支持的子模型实验在目标工具复现 |
| COMBINE/OMEX | 将模型、实验描述、数据和元数据打包 | 容器有效不代表内容全部可被其他软件运行 | manifest、内容类型、来源、可解析性与限制说明 |
| GenBank / FASTA | 已提供的序列及适当注释 | 当前方案可能只有受体/表达等表型参数；没有序列时禁用序列导出 | 提供真实来源序列，验证注释与回读 |
| CSV / HTML | 指标、参数比较、可阅读报告 | 不保存完整对象语义，不能替代无损项目 | 单位、缺值、对照与版本可追溯 |

产品第一版必须完成原生设计包、CSV 和 HTML 报告；N5 先交付一个适用的 SBOL 设计子集和 OMEX 打包样例，再逐项完成有数值对照的 SBML/SED-ML 适配。某个设计不具备导出条件时，保留原生包并明确拒绝该转换，不能空造合法外壳。

每次导出生成机器可读 `loss-report.json`，列出 exact / approximated / omitted / unsupported 的路径、原因和影响；有损输出不能覆盖原生文件。结构验证、语义验证、回读验证和外部工具复现分别报告。

## 5. 本地版与 Wiki

当前沿用 Python 包、本机服务和浏览器界面，已有独立 worker、SQLite 任务存储与 JSON 结果块；任务目录可显式指定，数据不写进源码。M4 文件由独立 CLI 或 Python 调用保存到调用者指定路径。完整末帧场已有独立 NPZ，通用逐帧二进制大数组交付仍待扩展。跨机器/平台发布验证尚未完成，安装器/桌面壳按发布需要评估，不先承诺未测系统。

Wiki 构建只装共享 viewer、冻结 registry、公开模型说明和预计算结果。初始化入口仅从示例开始；无服务端时依然可浏览完整记录。所有字体、图标、数学渲染和必要脚本可本地打包，避免演示依赖现场联网；发布前检查大小、许可证和加载速度。

每份预计算记录写明项目/内核/科学包版本、计算设置、seed、参数来源、运行环境、完整性和实际结果。图表必须说明其为模型预测。性能要求用指定示例、规模和机器的 CPU/内存/耗时记录表示，不能把早期小例子的速度推广为完整模型要求。

## 6. 旧格式迁移

当前合同并存：legacy Project 0.1/0.2 对应 Catalog/Task 0.1，固定 PTS Project 0.3 对应 Catalog/Task 0.2，空间 Project 0.4 对应 Catalog/Task 0.3，科学 Project 0.5 对应 Catalog/Task 0.4；通用 Task 0.5 单独协商后端与场输出能力。Workspace 当前保存为 0.6，兼容读取旧 0.1–0.5。选择新 profile 必须显式使用匹配合同；不会将旧项目的生长、分裂或 controls 自动移入另一种计算规则。以下是后续新增格式与包转换器仍需遵守的迁移要求。

1. 建立现有 project、workspace、graph、frames 和 result 的 fixtures，记录数值与已知限制。
2. 新 reader 先识别格式族/版本，再由纯转换函数生成目标文档；保留原件与 migration report。
3. 缺失的科学字段保持 unknown；只添加无科学含义的布局缺省。不默默填营养、碰撞或模型参数。
4. 图的旧执行语义保持 legacy profile；选择新语义必须形成新方案/运行并提供差异。
5. 未识别扩展仅只读保留；运行有明确阻断，未知未来版本不能冒充兼容成功。
6. 新/旧 reader 与 fixture 对照通过后再调整默认保存版本，旧生产 Schema 不原地改义。

旧同步接口继续用于已公布的小任务限制；新异步服务不通过悄悄放大所有限制获得“长任务支持”。旧 WebUI 的 `format/version/scene/workflow` 格式仍需要独立转换器，不能与本仓库 Workspace 直接混用。
