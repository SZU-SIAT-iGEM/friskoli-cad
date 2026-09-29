# 架构、状态与文件结构

状态：目标结构；按职责迁移，不在本轮移动源码。架构选择是保留 Python/NumPy、现有 ES modules 与 Three.js，先解决合同与状态边界。

## 1. 四类职责及依赖

```mermaid
flowchart TD
  UI[交互应用 Space / Workflow / Results] --> APP[应用服务 设计 / 校验 / 任务 / 比较 / 导出]
  CLI[CLI 批量运行] --> APP
  APP --> CORE[基础运行时 图编译 / 状态 / 数值 / 记录]
  PLUGIN[科学插件和模型包] --> API[Plugin API 与类型注册]
  API --> CORE
  CONTRACT[协议与领域数据模型] -.约束.-> UI
  CONTRACT -.约束.-> APP
  CONTRACT -.约束.-> API
  CONTRACT -.约束.-> CORE
  DEMO[Wiki 预计算包] --> VIEW[共享只读查看器]
  UI --> VIEW
```

协议是跨层共同规则，不能替代执行器、调度器或物理模型。基础层含平台能力与可替换基础数值算子，两者分别归档：数值求解方法的变化不能藏在 UI 或文件加载器中。插件实现合同，核心不 import 某个项目专用科学包；应用服务负责把目标、候选、图与计算组织成完整用户任务。

## 2. 领域实体

| 实体 | 保存内容 | 不承担的职责 |
| --- | --- | --- |
| DesignBrief | 指标、区域/时间、硬约束、软目标、对照、可变参数 | 不自动生成物理事实或校准数据 |
| DesignCandidate | 设计方案、底盘/元件配置、证据引用、参数覆盖 | 不以一次随机运行代表优劣 |
| ExperimentSpec | 场地、初始化、作用机制图、输入日程、观察计划 | 不保存相机或面板位置 |
| WorkspaceDraft | 可编辑方案、未完成图、模板绑定、编辑修订 | 不持有可变的运行状态 |
| ViewState | 相机、图布局、选中对象、面板与语言 | 不影响科学输入 hash |
| RegistrySnapshot | 已解析的类型、模块、实现、参数包和依赖版本 | 不自动实例化全部条目 |
| CompiledPlan | 排序、状态分配、显式转换、后端选择、时间积分策略 | 不更改提交的图与参数 |
| RunSubmission / Run | 不可变输入、执行设置、任务状态、结果引用 | 不回写正在编辑的草稿 |
| Result / Report | 已提交帧、指标、事件、日志、来源和完整性 | 不自行推算缺失的科学结果 |

第一版把 ExperimentSpec 嵌在单一项目文档中即可，不必为了名字拆成多个服务或数据库。稳定 ID 和明确引用先于文件拆分。

## 3. 注册目录分工

- `species`：物质身份、外部标识和带条件的属性；同一物质可被多个机制引用，避免按“引诱物/营养物”复制为不同身份。
- `entity_types`：可放置的菌群、源、底物表面、障碍、观察区域及初始化合同。
- `field_types`：标量/向量、体场/表面场、物种、单位、网格/坐标定义；登记不等于分配场。
- `modules`：输入输出、公式/算法、参数、状态读写、计算实现和生命周期。
- `parameter_sets`、`chassis`、`parts`：带来源、适用条件和不确定性的数据，独立于执行代码。
- `templates`、`model_packs`：对象和图的组合配方、依赖锁定及示例；科学包可以同时贡献多种登记项。
- `exporters`：目标格式、支持的语义子集、损失说明、验证器和回读能力。

UI 通过 schema 生成普通属性编辑器，通过受控 renderer key 选择胶囊、盒、点源、网格等显示方式。插件清单不包含能直接执行的 HTML/JavaScript。新几何若超出内置渲染器，先显示明确占位；可扩展 UI 需要单独受信任的发布与兼容机制。

## 4. 前后端各自拥有的状态

| 状态 | 所有者 / 唯一更新路径 |
| --- | --- |
| 用户可编辑输入 | 前端 document store，经 command/transaction 更新 |
| 相机、选择与面板 | 前端 view store；不参与科学版本变化 |
| 程序运行状态 | 服务端任务存储；前端仅缓存和查询 |
| 位置/朝向 | 声明的运动算子提出更新，空间约束协调后原子提交 |
| 几何与分裂 | 生长/分裂算子提出更新，由生命周期协调器提交 |
| 物种库存/浓度 | 单一场更新器接受多个速率贡献并统一结算 |
| 受体/酶/能量等内部状态 | 明确的模块 owner；死亡/分裂/迁移遵守各自策略 |
| 历史结果 | 记录器只追加已提交状态；完成后按 manifest 发布 |

插件拿到只读 StepContext 和受控的输出/状态提案接口，不直接改共享数组。一个状态一个提交所有者；多个源/汇可以通过声明的合并器贡献速率，不能依赖插件执行先后次序覆盖彼此。

群组 ID、细胞 ID 与数组索引分离。出生、分裂、死亡、筛选或并行重排之后必须重新建立 ID 到行的映射；端口绑定按 entity set 对齐，不能仅凭数组长度相同就相连。

## 5. 建议的渐进式目录

以下是目标目录图。`engine/`、`protocol/`、`web/` 保留当前位置，减少迁移成本；仅在对应实现进入时创建具体目录与文件。

```text
friskoli-cad/
├── README.md / PROGRESS.md / CONTRIBUTING.md
├── docs/
│   ├── design/                       # 本设计集与后续决策
│   ├── ...当前版本协议说明...
│   └── validation/                   # 科学对照、数值与设备验收记录
├── src/friskoli_cad/
│   ├── protocol/
│   │   ├── schemas/                  # 按格式族、版本保留现有 Schema
│   │   ├── validation.py             # 兼容入口，逐项下放验证
│   │   ├── diagnostics.py / units.py
│   │   └── migrations/               # 纯数据转换和损失报告
│   ├── domain/                       # 设计任务、候选、对象、证据等纯数据
│   ├── registry/                     # 各类登记、依赖解析、冻结快照
│   ├── application/
│   │   ├── design.py / validation.py
│   │   ├── runs.py / comparison.py
│   │   └── export.py                 # CLI 与 HTTP 共用用例
│   ├── engine/
│   │   ├── compiler.py / runtime.py   # 先保留入口和旧语义执行路线
│   │   ├── state/                    # World、ID、生命周期、事务
│   │   ├── scheduling/               # 时序、图依赖、后端计划
│   │   ├── numerics/                 # 输运、采样沉积、几何、重划
│   │   ├── recording/                # 帧、事件、指标、checkpoint
│   │   └── manifests/                # 现有基础模块，逐项补 metadata
│   ├── plugin_api/                   # 数据合同、受控执行接口、适配器
│   ├── server/
│   │   ├── routes.py / capabilities.py
│   │   ├── jobs.py / worker.py
│   │   └── storage.py                # SQLite 元数据 + 运行文件目录
│   ├── exporters/                    # native、CSV、标准格式适配器
│   ├── replay_service.py             # 旧 CLI/API 兼容入口
│   ├── project.py                    # 旧项目加载兼容入口
│   └── web/
│       ├── index.html / app.mjs       # 启动装配，逐步瘦身
│       ├── state/                    # document、view、run stores
│       ├── commands/                 # 编辑事务、undo、对象与图绑定
│       ├── registry/                 # catalog、参数表单、renderer keys
│       ├── transport/                # kernel-client、任务与分块读取
│       ├── features/                 # welcome、design、space、workflow
│       │                             # results、compare、export
│       ├── viewer/                   # 本地与 Wiki 共用的只读查看器
│       ├── ui/                       # dock、dialog、menus、icons、styles
│       └── vendor/                   # 固定版本、许可证与离线依赖
├── scientific_packages/
│   └── friskoli_chemotaxis/           # 独立 manifest、公式、参数、适配器
├── examples/
│   ├── protocol/ / runtime/          # 保留历史兼容样例
│   └── design/                       # 趋化案例、对照与最小外部插件
├── tests/                            # 现有测试先保持原路径
│   ├── contracts/ / migrations/
│   ├── numerics/ / application/ / ui/
│   └── fixtures/                     # 小型锁定输入及基准结果
├── benchmarks/                       # 计算、I/O、内存与显示分开测量
└── tools/                            # build-demo、check-package、release
```

默认采用一个 Python 进程提供本地 UI/API，独立计算 worker 执行长任务，SQLite 保存任务与候选元数据，大数组放运行目录。先单 worker、有界队列；无需先搭分布式集群、在线社区或微服务。完整版首发为 Python 包 + 一键启动本地浏览器，打包安装器在发布阶段验证；桌面壳另行评估，不作为科学流程的前置。

## 6. 当前文件如何处理

| 现有文件 | 当前证据与处理 |
| --- | --- |
| `engine/compiler.py` | 已有确定性排序与延迟边；保留测试，通过 adapter 增加类型、状态和 device 计划 |
| `engine/runtime.py` | 已有 World、ModuleRegistry、Simulation、回滚与生命周期；按职责渐进抽取，先保持导入入口 |
| `engine/modules.py` 与 `manifests/` | 保留全部已验收基础机制，补人类说明与来源；真实趋化模型单独包 |
| `project.py` | 保留旧版读取；新 project compiler 显式展开对象/模板 |
| `replay_service.py` | 目前集中静态文件、同步校验/求解/HTTP；新增 application/server 层，旧命令和受限 replay 继续可用 |
| `web/app.mjs` | 当前单一 state 含项目、块、模块、结果、检查和运行；优先拆 stores、commands、transport，避免整页重写 |
| `workspace.mjs`、`graph-edit.mjs`、`population.mjs` | 已有文件、图与散布逻辑；迁到明确职责时保留测试和兼容 export |
| `scene3d.mjs`、`workflow.mjs` | 复用渲染与操作能力，输入改为经过注册适配的选择/对象模型 |
| `replay.mjs`、`results.mjs` | 先抽成共享只读 viewer，再接任务分块结果和 compare |
| `placeables.mjs`、`catalog.mjs` | 改为真正 registry 消费者，避免以固定科学对象名单判断能力 |

只改目录而没有可见收益的搬迁延期。每次抽取同时保证旧示例可载入、旧数值结果可对照、当前界面仍可操作。

## 7. 本地服务与插件边界

本机服务默认监听 loopback；写接口验证会话、Origin/Host 和允许的资源路径，避免网页任意调用本地计算。任务目录由服务生成，不接受客户端任意路径。

数据包导入与可执行插件安装分开。导入项目不自动安装或运行附带代码；可执行插件需要可见的依赖、来源与用户明确安装操作。worker 子进程提供故障与资源隔离，但不宣称它是任意恶意 Python 代码的安全沙箱。首版只执行用户选择信任的本地插件。数值结果、公开演示包和许可证一起保留来源。
