# Friskoli-CAD 使用教程

Friskoli-CAD 将趋化机制、空间环境和参数比较放在同一个工作区。可以先查看菌群与营养物质的初始分布，再连接机制、运行模拟、比较候选，并保留完整输入和结果供复查。本教程对应当前 1.0.1 源码；[英文版](USER_GUIDE.md) 描述相同工作流程。

第一次使用可先完成[短时 Design 示例](QUICKSTART.zh-CN.md)。它有 8 个细胞、2 个速度候选、1 个对照和 2 个 seed，适合熟悉从输入到结果的过程。

## 安装与启动

准备 Python 3.11 或更新版本，下载或克隆项目，在项目目录创建独立环境。

Windows：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install .
.\start.ps1
```

也可双击 `run.cmd`。需要直接启动时运行：

```powershell
.\.venv\Scripts\python -m friskoli_cad.replay_service --port 8765
```

Linux / macOS：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m friskoli_cad.replay_service --port 8765
```

浏览器访问 `http://127.0.0.1:8765`，Settings 中可切换简体中文和英文。端口被占用时换一个端口；终端中按 `Ctrl+C` 结束服务。首次安装需要下载 Python 依赖，安装后的 CPU 计算与界面资源可以在本机离线使用。应用运行需要 Python 服务；Node.js 用于前端测试。

安装 wheel 时，在启用的环境中使用 `python -m pip install /path/to/friskoli_cad-1.0.1-py3-none-any.whl`，随后启动相同服务。

可选功能按需安装：`python -m pip install ".[research]"` 提供研究脚本绘图；`.[standards]` 提供 SBOL Component 导出；`.[cuda11]` 提供兼容 NVIDIA 环境下的扩散加速。CPU 适合作为最初的运行环境。

## 认识工作区

| 页面 | 用途 |
| --- | --- |
| Space | 设置菌群、材料、营养场、物质来源和障碍物的位置与尺寸 |
| Workflow | 检查模块的输入、输出、参数和连接 |
| Results | 回放已保存的帧，查看指标、浓度截面与轨迹 |
| Design | 枚举参数、生成候选、安排独立重复并比较结果 |

左侧对象与模块目录用于选择和放置对象，右侧查看属性、方程和参数来源。时间轴显示已保存的运行帧。显示相机、缩放和布局不会改变科学输入。

鼠标拖动旋转视角，右键拖动平移，滚轮缩放，也可使用相机工具和预设视角。选择菌群后可移动、旋转或缩放其初始分布。选择环境对象后，操作由对应模块的几何能力决定；体素环境对象按网格对齐。触控操作和键盘快捷键在界面帮助中列出。

## 选择适合的预设

| 预设 | 适合完成的工作 |
| --- | --- |
| Modular foundation | 第一次运行；营养物质、摄取、代谢与运动之间的连接 |
| Material and lifecycle | 材料转化、催化资源、几何与细胞谱系 |
| PTS A/B contact assay 及无反馈对照 | 8 个细胞、4 µm 网格的短时接触与转化检查 |
| PTS A/B small strong release 及无反馈对照 | 48 个细胞、1 µm 网格的中心底物研究配置 |
| PTS A maintained-gradient encounter 及无反馈对照 | 64 个细胞从距材料 33–36 µm 处出发，材料表面维持约 1 µM，60 s 内分别记录首次接触、驻留和转化 |
| MCP gradient / zero-gradient chip | 受体趋化与均一浓度条件的比较 |

接触测试预设将细胞放在四个底物表面附近，保留随机朝向，以较少计算检查接触和释放过程。A/B 及其对照保留相同环境和初始细胞。粗网格适合功能检查，界面浓度的定量研究需要同时检查网格敏感性。

中心底物预设的强释放设置使用 200 个表面酶拷贝和 `kcat = 130/s`。这些是构造的研究参数。MCP 芯片较长的观测窗口用于计算漂移率；短时运行先查看轨迹与位置数据。

## 建立和修改机制图

在 Workflow 选择模块，检查其版本、方程、输入输出端口和参数。参数包含数值、单位和来源。新建连接时，形状、物理量、单位、物种和菌群归属需要匹配；单位换算由明确的转换模块完成。

`same_step` 读取本步较早阶段已产生的输出；`previous_step` 读取上一个已提交步的输出，表示一个明确的计算延迟。连接的顺序决定了机制之间的信息关系。先检查诊断定位的节点、端口或参数，再运行。

每一步按 prepare、field、physiology、lifecycle、observation 的顺序处理。模块提出变化，系统处理共享物质、几何和细胞身份，再提交整个计算步。某一步发生错误时，之前已提交的状态仍保留。

从预设开始调整一个参数，有助于判断变化来自哪里。修改了物理环境、模块或观测定义后，生成新的设计，并保留此前运行记录。

## 设置和运行计算

模拟时长为 `dt × steps`。例如 `dt = 0.02 s`、25 步对应 0.5 s。保存帧间隔决定写出的帧数，观测仍在每个计算步更新。勾选浓度场会增加结果体积；分辨率预览在显示时平均体素，原计算网格保持原样。

Run 提交时冻结项目、执行参数、模块版本和来源信息，随后编辑草稿不会改变该次运行。Runs 显示排队、运行、暂停、完成或失败状态。失败时读具体诊断；较大网格、更多细胞、更长时程和更频繁的输出都会增加资源需求。

命令行服务默认给异步任务 1800 s 的运行时间。需要调整时可设置 `--task-wall-time-s`、`--task-memory-mib`、`--task-output-mib`、`--task-voxels` 和 `--task-chunk-mib`。先用小规模配置检查输入，再为研究运行分配资源。

## 使用 Design

Design 基于当前项目枚举参数组合。填写研究名称、底盘说明、目标菌群、指标与方向，再选择节点和数值参数，输入逗号分隔的枚举值与独立 seed。硬约束排除不符合条件的候选；软约束单独记录偏差。结果阈值用于计算完成后的评价。

**生成候选**检查参数、机制图和资源预算，此时还没有模拟。每个候选保留冻结的完整项目。被排除的候选显示原因。通用固定偏置对照从未扫描的基线生成，保留环境、菌群、观测和原运动参数，将运动偏置设为 `0.5`。已有 PTS 预设的无反馈对照采用各自的匹配偏置定义。

设计最多安排 16 个分支（含对照）、8 个独立 seed 和 32 次运行。页面显示分支数、重复数、总步数及预计内存和输出。每批 1–8 次运行，按所选候选与 seed 继续。程序跳过已有的完整重复，保留失败、取消和重试记录。停止批次后，已发布结果仍可查看。

完成后使用**比较已完成运行**查看各次记录，使用**评价结果**检查候选是否满足结果阈值、独立重复数和每个 seed 的对照改善条件。均值与样本标准差按独立运行计算。没有唯一且满足条件的候选时，页面显示具体原因。

新 Design 使用 0.3 版目标表单，支持表面接触比例 `contact_fraction` 与初始菌群累计接触时间 `contact_cell_seconds`。使用这些目标前，项目需要声明 `contact_range_um`。接触测试预设已包含该定义。

一个完整示例：加载 Modular foundation，打开 Design，点击**短时速度比较示例**，生成候选，分批运行 6 次并评价。详细字段和结果解释见[第一次使用](QUICKSTART.zh-CN.md)。命令行的同一过程为：

```powershell
.\.venv\Scripts\python examples/design/compare_speeds.py --out runs/first-design
```

## 读取指标与回放

| 指标 | 含义 |
| --- | --- |
| 平均位置 / 有向位移 | 沿声明观测轴的位置与有正负号的位移 |
| 区域占比 | 当前活细胞中，细胞中心位于指定区域内的比例 |
| 到达比例 | 初始细胞群中，曾在计算步边界出现在区域内的比例 |
| 平均停留时间 | 初始细胞群在区域内的累计占据时间均值 |
| 表面接触比例 | 当前活细胞中，菌体表面与材料距离在设定范围内的比例 |
| 累计接触时间 | 初始细胞群接触占据的时间积分之和，单位 cell·s |
| 累计转化量 | 材料被转化的物质量，以 molecule 当量记录 |
| 漂移率 | 10–120 s 内群体平均轴向位置的最小二乘斜率 |

到达和停留采用初始细胞群作为参照，后代不会扩大这个分母。空菌群或尚无足够观测时间的量保留为空值。径向观测采用完整球体内的累计计数，不同半径并非互斥的壳层。

Results 读取已保存的数据。移动时间轴、查看浓度截面、选中细胞或聚焦细胞，都不会继续计算。轨迹遇到细胞缺失时保留断点。比较接触和降解时，分别查看两类指标，再结合环境与机制理解变化。

## 保存、导入、导出与继续

工作区保存编辑中的项目、图布局与显示状态。`.friskoli` 原生包保留冻结输入、设计、历史运行和结果。File → Open 可以打开支持的项目 JSON、Workspace 0.7 或原生包。旧 Project 0.6 中保留的空 `controls` 字段仍可读取，新的日程机制使用图节点表达。

CSV 用于数据分析，HTML 用于阅读报告。共享时同时保留原生包，使接收者可以复查输入、来源与结果。Design 的历史运行保持只读；重新生成候选会得到新的冻结设计。

暂停在已提交边界后产生 checkpoint。继续计算会检查实现哈希和随机数环境。修改后端源码或模块声明后重启服务，保留产生旧 checkpoint 的原环境用于复现。

环境迁移先显示预览，再执行支持的域调整。已有运行和父子关系会保留。程序按映射规则处理物质库存，拒绝丢弃已有细胞或非零库存的迁移。

## 生成可分享的静态回放

先导出有完整结果的原生包，再执行：

```bash
python -m friskoli_cad.wiki_export runs/first-design/study.friskoli runs/first-design/viewer
python -m http.server 8881 --directory runs/first-design/viewer
```

访问 `http://127.0.0.1:8881`。部署完整输出目录后，可以在 Wiki 等静态网站查看已计算记录。静态查看器提供回放与数据展示；计算和编辑仍在本机应用中完成。

## 为 Wiki 重复生成研究数据与图表

安装 research 依赖，然后运行：

```powershell
.\.venv\Scripts\python -m pip install ".[research]"
.\.venv\Scripts\python research/pts_assay.py --out runs/pts-assay-01
```

默认执行 A/B 的反馈与无反馈配对，各 2 个 seed；另外检查半时间步、半网格间距与 `kcat` 的 0.5 倍和 2 倍，共 12 个配置。每次写出项目、参数、来源哈希、时序、终点指标与物质记录。`runs.csv` 保存终点，`paired-effects.csv` 保存同 seed 的“反馈减无反馈”差值，SVG/PNG 图展示接触、转化和敏感性结果。

默认 0.4 s 的接触测试用于检查计算流程。研究较长过程时，明确指定时程、seed 和网格，例如 `--duration 2 --seeds 1 2 3 4`。不同网格下保持同一物理场景，图中记录各自的计算条件。选择新的输出目录可保留先前的研究。

传入测得的参数时使用 `--parameters measurements.csv`。CSV 列按以下顺序填写：

```csv
node_id,parameter,value,unit,source,condition
```

每行对应一个已有的数值参数，来源可写实验编号、数据文件或文献，条件记录培养基、温度与测量方法等。数值有限、单位匹配、来源和条件完整后才会使用。程序将输入原样保存在研究记录中，再检查参数范围和模型约束。文档不预填实验结果；参数拟合与适用条件判断由研究者结合实测数据完成。

完整配置和脚本说明见[研究工作流程](RESEARCH.md)。

按 Wiki 图位生成 A/B/C 信号曲线、空间对照、梯度相图、敏感性与分层评价时，使用[Wiki 数据教程](WIKI_STUDIES.md)。图注、参数来源和[文献—模型对应表](WIKI_MODEL_EVIDENCE.md)随结果保存。

## 注册自己的模块

模块通过 manifest 声明 ID、版本、菌群或环境归属、输入输出、参数和状态；Python 实现 `initialize(context)` 与 `propose(context)`，返回 `ModuleProposal`。系统负责共享库存、几何、身份与完整步提交。新增机制可沿用已有的系统效果和端口。

先运行完整示例：

```powershell
.\.venv\Scripts\python examples/modules/constant_bias/demo.py
.\.venv\Scripts\python examples/modules/constant_bias/build_package.py
.\.venv\Scripts\python -m friskoli_cad.packages preview runs/tutorial/constant-bias.zip
```

示例将新模块与内置模块放入同一个 `ModuleRegistry`，替换已有节点引用并计算一整步。构建的 ZIP 包含 manifest、源码、许可证和文件哈希。检查预览后，使用构建器打印的实际哈希安装：

```powershell
.\.venv\Scripts\python -m friskoli_cad.packages install runs/tutorial/constant-bias.zip --sha256 实际的64字符哈希
```

在项目中用 `PackageStore.default().resolve({'tutorial': '1.0.0'})` 生成并保存 `dependency_lock`。服务和任务会据此加载相同实现。模块会出现在目录中，参数可以在界面和 Design 中使用。完整锁定步骤与代码见[模块教程](EXTENDING.md)。

模块包预览和安装检查并复制文件，加载可执行依赖时会导入其 Python factory。模块代码按可信扩展运行。状态型模块还需声明分裂、死亡和迁移规则；随机模块使用上下文提供的稳定随机流。新增系统资源或效果需要修改系统契约并提供相应测试。

## 标准交换与复现

软件可导出 OMEX 研究档案，也可在安装 standards 依赖并明确填写元件信息后，导出声明的 SBOL Component 子集。选择匹配的交换格式有助于保留元件身份与来源；SBML、SED-ML 和序列生成不在当前导出功能中。

复查运行时保留项目、执行设置、seed、源码与模块锁、原生包和环境版本。当前数值检查支持已实现的方程和软件行为；模型中的生物参数及预测范围见[模型与数据参考](MODEL_REFERENCE.md)。

遇到问题时，在 GitHub Issue 附上软件版本、操作步骤、实际诊断和可分享的最小输入。贡献代码和模块的方法见 [CONTRIBUTING](../CONTRIBUTING.md)。
