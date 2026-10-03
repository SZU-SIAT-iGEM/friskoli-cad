# 运行服务与协议回放 · 阶段 4b

**当前实施更新（2026-10-01）：** 默认本地启动启用[异步任务服务](task-service.md)，前端按项目 profile 精确选择 Task 0.1–0.4 后使用 `/api/runs`，支持排队、取消、恢复查询和 partial 结果。[空间 Task 0.3](archive/legacy-protocols/spatial-profile.md)可传输真实浓度场，并始终传输有限源与材料库存；[科学 Task 0.4](archive/legacy-protocols/chemotaxis-profile.md)进一步提供逐步指标、稀疏帧生命周期记录与死亡规则。Task 0.1/0.2 仍不传场数组。[Checkpoint 文件与 CLI](checkpoint-files.md)分别支持独立空间和科学运行的完整状态恢复；任务 pause/resume/checkpoint 能力仍为 false，项目编辑器不导入 checkpoint。

本页其余同步请求、旧示例和限额说明记录阶段 4b 的 `/api/replay` 兼容路径。可用 `--sync-only` 启动旧模式；当前任务限制以 capabilities 为准，不能用旧同步限额推定异步能力。

这一阶段建立可从源码启动的本地运行服务和可编辑的工作区。界面参考旧版 WebUI 的专业软件布局：项目与运行设置在左、空间视口及时间轴居中、单菌体检查在右；代码和样式重新编写，没有迁入旧项目的编辑器、内核或假结果。工作区、接口和交互约定分别见[工作区协议](archive/legacy-protocols/workspace-protocol-0.2.md)、[OpenAPI](openapi.json)和[交互规范](interaction-specification.md)。

在仓库根目录运行：

```powershell
$env:PYTHONPATH='src'
python -m friskoli_cad.replay_service --port 8765
```

浏览器打开 `http://127.0.0.1:8765`，从欢迎页选择示例后再运行。阶段 4b 的[三维示例项目](../src/friskoli_cad/examples/workspace_3d.project.json)仍保留：`18 × 12 × 6` 格、格距 `2 µm`，一个 8 菌体的组接演示性伸长与长度 adder；以 `0.5 s` 运行 4 步即出现第一处真实分裂事件。空间示例使用独立 profile，不包含生长或分裂。可调整时间步与步数再次运行，也可导入符合所支持 Project/Workspace Schema 的设计 JSON。首次使用默认英文；Settings 可切换到中文，语言偏好保存在本地浏览器。项目里的 ID、模块名和协议数据保持原文。

## 阶段 4b 同步数据边界

- `GET /api/example-project` 提供该阶段的包内示例。
- `GET /api/modules` 返回后端登记的全部模块声明。前端据此解析行为图并读取 `population.static`，不自行维护模块定义。
- `population.static` 是数值上的空模块：不读输入、不写状态，只让新放置的菌群有一个所属节点，保持初始位置、朝向和尺寸不变，直到用户接入其他行为模块。
- `POST /api/replay` 接收 `{project, dt_s, steps}`。服务使用现有 `simulation_from_project` 创建运行时，生成初始状态和后续完整快照；任一步失败就返回明确错误，不交付部分回放。
- 响应外层标为 `replay_format_version: 0.1.0`，携带项目 ID、原有 `run` 元数据、原有 `domain` 数据和逐时刻 `snapshots`。每项由**原样的单菌体 `frame`**及实际运行的 `concentrations` 组成。外层版本只描述这次 HTTP 封装，不改变行为图 `protocol_version: 0.1.0` 或结果帧版本。
- 每个浓度场标明物种、单位与 `[z][y][x]` 数值。只登记未运行的物质没有数组；例如默认示例中的氧显示为“Registered · not computed”。
- 服务使用现有 `validate_frame_sequence` 再检查完整结果。浏览器拒绝不支持的版本和与事件不一致的 ID，按帧内尺寸与姿态画胶囊，按 `birth`、`division`、`death` 事件和稳定 ID 更新列表。浏览器不推算生长、分裂或浓度变化。

视口用 three.js 绘制场地边框、底面格线和按帧内尺寸与四元数摆放的胶囊，可切换透视、正交及 XY、XZ、YZ 三个正视图。浓度场按选定的 Z 格层画成半透明平面，色阶按整次回放、当前物种的实际最小与最大值固定，方便比较不同时刻。旧 `0.1.0` 帧若没有尺寸，以线框小球标记未知尺寸；`0.2.0` 帧显示胶囊长和直径。渲染胶囊本身不执行碰撞判定；空间 profile 的接触检查由后端完成，旧示例保持原语义。重叠位置的菌体可反复点击按 ID 轮换选中。

Space 工作区可放置菌群体积块，设置数量、胶囊尺寸、随机种子和欧拉旋转后散布。散布用固定种子，结果可复现；薄层场地只在 XY 平面内取向。对象支持平移、旋转、缩放、网格吸附、测量、隐藏、锁定、复制和删除；批量菌体使用实例化渲染。散布写入项目的 `groups`，并为新菌群登记 `population.static` 节点。Workflow 工作区可拖动节点、连接端口、编辑参数、删除节点，`previous_step` 连接用虚线标出；右侧 Evidence 页显示模块登记的成熟度、科学角色和适用范围。

运行前点击 Check 会调用后端校验，错误带有稳定的 `code` 和 `path`，缺少菌群或行为图时 Run 保持禁用。点击 Run 时提交项目的不可变副本，并以请求 ID 关联运行记录；编辑项目不会改变已完成的 Results。Results 保留多个运行，可回看实际事件、选择单菌体，并按输出内容查看浓度和数量历史、导出 JSON 或 CSV。4b 原保存格式为 Workspace `0.2.0`；当前 Save 使用 [Workspace `0.4.0`](registry-contract.md)，兼容读取 0.1–0.3。浏览器草稿恢复与任务状态查询分别保存和处理。

## 阶段 4b 原范围与当前边界

4b 交付了结果回放、单菌体检视、时间轴、逐物种浓度场、菌群体积散布、行为图编辑、诊断、导出和本地项目导入，当时仅有同步请求与菌群对象。后续 N2 已增加异步任务；空间 profile 已登记障碍物、有限局部源和可降解材料盒。服务仍是本机工具，默认只监听 `127.0.0.1`，没有多用户认证或公开网络部署协议。示例、网页和 three.js（MIT，许可证随附）作为包数据发布；外部部署仍需单独处理。旧同步路径每帧限制 2000 个菌体、XY 截面限制 4096 格，并限制回放场数值总量；当前异步资源限制另由相应 profile 的 capabilities 公布。

本阶段测试覆盖真实分裂帧、旧帧和双物种场封装、HTTP 响应、非法输入及界面端对事件和版本的检查。浏览器尺寸模拟需记录桌面、平板、手机竖屏和横屏；实体设备实测尚未完成。
