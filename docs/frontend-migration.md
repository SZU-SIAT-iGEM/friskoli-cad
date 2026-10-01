# 前端迁移验收

状态：2026-09-29 首版迁移验收记录。以下清单与数字描述当日结果，保留作为历史证据；旧版 `friskoli-cad-webui` 只作为交互参考，实现使用独立模块和后端协议。

**当前实施更新（2026-10-01）：** 默认服务已支持[异步任务](task-service.md)的排队、取消、恢复查询与 partial 结果；[空间 profile](protocol/spatial-profile.md)已注册障碍物、有限局部源和材料盒，并可传输真实场和库存。M4 的[保存恢复文件与 CLI](checkpoint-files.md)用于独立空间运行，任务 pause/resume/checkpoint 与 UI checkpoint 导入仍未开放。最新验收见 [N2](verification-n2.md)、[空间记录](verification-spatial.md)、[M4](verification-m4.md)及 [PROGRESS](../PROGRESS.md)，不以本页旧测试数量代表当前测试规模。

## 已迁移并重新实现

- 专业软件工作区：菜单栏、Space / Workflow / Results 主导航、对象树、模块库、属性检查、诊断底栏和状态栏。
- 空间编辑：菌群体积块放置、固定 seed 散布、平移、旋转、缩放、网格吸附、测距、隐藏、锁定、复制、删除和薄层容纳检查。
- 行为图编辑：节点拖动、端口连线、时序切换、参数编辑、节点删除、撤销/重做、模块 Evidence 信息。
- 运行与结果：能力查询、运行前 Check、不可变提交副本、请求 ID、运行记录、时间轴、事件和单菌体检查、浓度数据、数量历史、JSON/CSV 导出、本地草稿恢复。
- 性能：菌群批量使用 Three.js instanced mesh；请求在服务端进行尺寸、菌体数量和结果场总量限制检查，避免在建立运行时过早分配过大数组。

## 验收记录

| 范围 | 方法 | 结果 |
| --- | --- | --- |
| Python 后端与服务 | `python -m pytest -q` | 91 passed，19 subtests |
| JavaScript 模块 | `node --test tests/*.test.mjs` | 14 passed |
| JavaScript 语法 | `node --check` 覆盖 app、scene、workflow、workspace、panels、kernel-client、results | 通过 |
| 协议文档 | JSON 解析 OpenAPI、示例工作区和 Schema | 通过 |
| 桌面布局 | 浏览器窗口尺寸模拟，含 Space、Workflow、Results、拖拽面板 | 通过 |
| 平板布局 | 浏览器窗口尺寸模拟，检查抽屉、工具栏和结果区 | 通过 |
| 手机竖屏/横屏 | 浏览器窗口尺寸模拟，检查底部入口、滚动和安全区样式 | 通过 |
| 触摸逻辑 | 浏览器事件脚本检查轻触/拖动与多指状态分离 | 已检查逻辑；未做实体设备实测 |

浏览器尺寸模拟不等同于实体设备测试。此次首版验收时，同步运行服务尚不支持排队、取消、暂停/恢复、断线重连或部分结果；障碍物、局部源和纤维当时以不可用条目显示。后续已实现能力与仍未开放的边界见本页顶部更新，原验收数字不作追溯改写。
