# Workflow 组合组件验收记录

日期：2026-09-30。此记录只覆盖 Workflow 表现层；不证明任何科学模型已经校准。

## 实现范围

- `workflow-components.mjs` 提供节点外壳、名称/ID、端口行、只读参数行、数学式与状态组件。名称优先使用 registry 的 label，兼容已有中英双语文本；没有友好名称时从 module ID 生成可读名称。
- 完整实例 ID 和注册 ID 在 ID 面板内可读取、选中与复制。动态文本通过 textContent 或 input.value 输出。数学式继续使用仓库内 KaTeX，关闭 trust 并限制展开；长公式和源码在节点内滚动。
- `GraphEditor` 仍通过既有 select / move / connect / toggle / context callbacks 修改应用状态，不增加第二份 graph 或 history。未知版本保留边与端口，显示不可运行状态。
- 参数值是摘要，编辑仍在原 Inspector；最多显示两项参数，完整清单仍由 Inspector 提供。数据节点沿用已有折叠状态和回调。
- 缩放采用左上角原点；适合图形按节点实际范围计算，不再使用固定 800×460 最小画板。窗口调整更新画板范围并保留 zoom 与滚动位置。新增图内缩放、原始大小与适合图形按钮。
- Workflow 范围内的按钮、滚动条与 ID 输入采用深色样式。端口分成左右两列，粗指针端口行/按钮为 44 CSS px；连线维持 same_step / previous_step 线型和完整说明。

## 自动检查

运行 `node --test tests/workflow-components.test.mjs tests/graph-edit.test.mjs tests/catalog.test.mjs`：18 项通过。

新增 9 项检查覆盖友好名称及长文本、组合区块与端口几何、缺输入/未知模块状态、未知模块端口可读取、保存布局不被替换、实际内容 fit、缩放锚点、动态文本和 global.scalar 单位，以及取消拖动和双指切换。

已有 graph/catalog 检查继续覆盖端口类型、owner、fan-in、same_step cycle 到 previous_step、删除与参数校验、对象读出节点保存/恢复，以及未知模块保留原始数据。此次未更改这些业务实现。

## 浏览器与设备

使用独立 Chromium / Playwright 会话 `workflow-n3`。截图和缓存写入系统 Temp，不保存至仓库。

| 检查 | 结果 |
| --- | --- |
| 1440×900、1024×768、390×844、844×390 | 临时组件 fixture 与实际完整应用均无页面横向溢出。大图保持可滚动和平移，缩放由独立工具条控制；并不假定手机能同时阅读全图。 |
| 长名称、长数学式、错误状态 | fixture 的两行长标题、长公式横向滚动、源码展开、缺输入和未知模块均可读取。动态 `<script>` 文本未成为元素。 |
| 完整 ID | 实际 N3 图可打开实例/注册 ID；复制后通过浏览器剪贴板读回 `bulk.finite_uniform@1.0.0`。检查发现 ID 面板会被参数层遮挡，已调整 header 层级并复测。 |
| 选择、连接与布局 | fixture 键盘依次选择输出/输入触发连接回调；实际应用鼠标拖动后 Undo/Redo 恢复相应位置。 |
| 粗指针与双指 | CDP 触屏模拟在100%缩放下测得端口命中区44×44 CSS px、标题14px；发送双指触点序列触发放大并正确清理 pointer 状态。Node测试另覆盖取消拖动、双指切换后单指平移。 |
| N3 图组合 | 实际 `Shared substrate & PTS signals` 示例13节点、14连线，13个数学区均由 KaTeX 输出，名称来自新 registry；不同形状端口仍显示完整量纲。 |
| registry 切换 | N3→旧生长分裂示例（2节点）→空项目（0节点、Space可放置population）→N3（13节点），均未出现错误的未知模块状态。 |

这些是浏览器尺寸与输入事件模拟。实体鼠标、实体触控板、实体触屏和软键盘未实测。触控板的普通滚轮与 Ctrl/Meta-wheel 分支保留，但尚未使用实体触控板验证。N3 task执行结果由独立集成验收记录覆盖。

## 最终集成补记

冻结源码后，在重启的本地服务重新提交 `run_345c4d113c3f4a8ba5bfc48e8ce9823c`：task 0.2，completed，8/8 steps，4 s，complete output，共9帧。a0末帧在应用Inspector显示 accepted_flux 0.0082 molecule/s、cumulative_uptake 0.0331 molecule、ei_fraction 0.0187、CheY-P 0.4937 uM、motor_bias 0.196。位置仍为0.5/0.5/0.5 µm，这些信号不表示已经实现运动或趋化。

刷新页面、恢复N3草稿、选择同一个完成任务后，网络监听记录新增 `POST /api/runs` 为0；未重新提交计算。随后检查默认13节点的浏览器矩形，两两无重叠。保留开发期间旧源码锁不匹配造成的 failed 历史，没有为截图删除记录。

最终截图位于系统Temp：`workflow-n3-final-fit-completed.png`（默认布局概览与完成任务）、`workflow-n3-final-signals.png`（末帧信号）、`workflow-n3-final-graph.png`（100%图阅读）。

最终概览检查发现 Fit 至约35%时工具条遮住首排标题。随后按主线程授权做了最小修复：图内容预留56px固定空间，缩放锚点转换和Fit滚动位置同步计入该偏移。桌面1440宽及手机390宽复验Fit首排标题完整、100%变换准确，鼠标Ctrl-wheel锚点保持在1 CSS px误差内；相关9项Node测试通过。`workflow-n3-final-fit-completed.png`已更新为修复后的图。刷新后等待已发布结果重新载入，再选同一任务，Replay frame范围为0–8，确认9帧可以恢复读取。
