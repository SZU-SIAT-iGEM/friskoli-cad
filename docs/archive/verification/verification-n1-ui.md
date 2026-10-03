# N1 浏览器验证记录

日期：2026-09-29。使用本机服务 `127.0.0.1:18765` 与已有 Playwright `n1-ui` 会话。
本记录区分真实浏览器操作、DOM 检查、单元测试和未测项；不把尺寸模拟当作实体设备验证。

## 已取得的浏览器证据

| 检查 | 实际观察 |
| --- | --- |
| 旧“Growth & division”入口 | 点击后成功加载；此项由界面验证代理检查 |
| 新“Geometry & sampling”入口 | 点击后成功加载，5 个节点、3 条连接，Run 可用 |
| geometry 数据节点折叠 | 展开后 position 端口数量为 1，折叠后为 0 |
| 运行到 Results | 真实运行请求 `POST /api/replay` 返回 200；主代理重新读取浏览器快照，确认 Completed、9 帧、1 个菌体 |
| 运行输入与首帧 | Time step 0.5 s，8 steps；Results 显示 Frame 0 / 8、0 s 和薄层 2×1×1 |
| 回放末帧 | 聚焦 Replay frame 后用 End 键到达 Frame 8 / 8，时间为 4 s |
| 放置与散布 | 新建空白 3D 项目，点击 Place population 后在视口中心放置；散布得到 32 个菌体、1 个群组、2 个节点，数据关联指向 capsule_readout_1 |
| 真实 Undo/Redo | 点击 Undo 后群组与节点均为 0，放置块保留且 dirty=true；Redo 恢复 1 群组、2 节点，project 与 population_blocks 逐值等同散布后快照 |
| 保存重开 | Save 下载 Workspace 0.3；随后把 Count 改为 33，经文件输入重新打开刚下载的工作区，Count 恢复 32 |
| 未知版本导入 | 将导出副本中的 population.static 版本改为 9.9.9 后导入；Run 禁用、Save 可用，Workflow 显示 module.missing 与 /graph/nodes/0 路径 |
| 未知版本保存 | 实际点击 Save 仍可下载；比较导入/导出文件，project、population_blocks、run_settings 完全一致，9.9.9 原样保留；恢复原工作区后 Run 再次可用 |
| 公式 DOM 渲染 | 从 Modules 选择胶囊读取，生成 1 个 .katex、0 个 .katex-error，包含面积/体积表达式；公式容器宽 240px、内容宽 246px，overflow-x=auto |
| 布局尺寸 | 1440×900、1024×768、390×844、844×390 下 document scrollWidth 均等于 clientWidth |
| 横屏工具栏 | DOM 记录 Design tools 的纵向 overflow 为 auto，具备内部滚动区域 |
| 公式资源请求 | 服务日志显示 KaTeX Math/Main/Size3 字体本地请求返回 200；资源加载不能单独证明视觉质量 |

新示例首次请求曾返回 404，原因是测试浏览器连接到未重启的旧服务。重启本轮服务后
相同入口返回 200 并加载成功；这次 404 不记为修复过的源码缺陷。

第一次保存自动化用精确名称 Save 定位，但未保存标记使名称变为 Save •，脚本没有点击
正确按钮，等待下载超时。使用已有 save-button 定位后正常下载；这是测试脚本定位错误，
没有因此修改应用源码。

测试文件原先尚未打开 Workflow，graph_layout 为空。重新打开后现有 autoLayout 为两个
节点补齐显示位置，胶囊读取默认 collapsed=true；这项编辑元数据初始化与数值输入、
模块版本、对象参数的往返保留分别核对。

## 验证方式与证据范围

界面代理执行了示例加载、节点展开/折叠和四尺寸 DOM 检查。代理随后长时间未回传
进度，主代理停止其轮次后接手续测，避免把未收到的结果写成通过。主代理通过
新的浏览器快照确认实际 Results 状态；服务日志只能作为请求成功的辅助证据。

完整自动测试为 Python 119 项、JavaScript 20 项，包含初始化、状态快照往返、迁移、
未知模块和旧结果回归。这些结果不能代替真实点击 Undo/Redo 或文件选择器的验证。
只读审阅另复现了手动 geometry 节点复用、旧格式未知版本与嵌套参数往返；未发现新缺陷。

## 尚未完成的验证

- 公式的完整截图目视检查及全部键盘焦点遍历；DOM 渲染成功不等于视觉验收。
- 真实触控板、触屏、一指/双指切换、pointercancel、软键盘、200% 缩放及实体设备。

当前欢迎页仍采用旧大标题/卡片布局。用户要求的紧凑专业工具风格仅完成
[研究与实施规范](design/professional-ui-reference.md)，本记录不表示视觉验收完成。

## 产物与服务

测试截图、浏览器临时快照、日志和安装包放在系统 Temp，没有加入源码仓库。
主仓库内原有 `.playwright-cli/`、`outputs/` 与 Python 缓存的时间早于本轮 N1 检查，
未把这些历史文件当成本轮垃圾删除。临时服务保留供本地查看；当前仍为同步运行接口。
