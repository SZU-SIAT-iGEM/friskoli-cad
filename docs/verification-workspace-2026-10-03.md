# Workspace 与浏览器验证 · 2026-10-03

## 自动验证

`node --test tests/*.test.mjs`：156 项，152 通过，4 个既有跳过，0 失败。覆盖命令执行时重新检查锁定、settings 精确定位、事务依赖、旋转外廓、离线 Catalog/最近项目持久化、run-level t 区间、分页二进制字段校验与缓存淘汰、父子恢复段无重复拼接和流式完整导出。已有 pointercancel 与单指/双指切换逻辑测试通过。`node --check web/app.mjs` 通过。

## 浏览器实际操作

Playwright Chromium，独立源码快照服务 http://127.0.0.1:8877。为了避免并行开发期间源文件哈希变化，运行内核使用本轮源码快照；这并不代表最后所有科学内核更改均重新完成浏览器验收。

- PTS A，dt=0.01，12 steps：由界面提交 run_6ca8dfc974ea4aceb0c86c7e83a14aca，完成 12/12；二进制浓度载入正常，上一帧从 0.12 s 到 0.11 s，无 console error。
- 5000 steps：由界面提交 run_936aa1e85ecc434b8eac59de326fd148，Pause and save 后 paused；API 核实 committed_step 与 checkpoint.step_index 均为 386。
- Resume checkpoint 创建 run_ae06e201277a42cf802c07d85c29a8f5，start_step=386、parent_run_id 指向父任务。界面观察继续进度到 393；再次暂停后的最终 committed_step 与 checkpoint.step_index 均为 463。393 是运行中观察时刻，并非恢复起点。
- 390×844、844×390、768×1024、1440×900 截图已逐张查看：Space、任务按钮与主要导航可见；小屏通过 Objects/Properties 开侧栏，横屏下 Runs 占用较大视口但可关闭。属于浏览器尺寸模拟。

证据保存于仓库外 `D:/Wu Shangru/Documents/工作区/friskoli-cad-task-evidence-2026-10-03/`，含 playwright 截图、服务源码快照与 ui-tasks 数据。8876/8877 本轮服务已停止。

## 已实现与验证边界

- 新增独立事务、CommandRegistry、WorkspaceSession、分辨率指导、统计分析、分页回放和包管理模块，Workspace 0.7 保持编辑草稿/ViewState/运行记录分离。
- 人群与环境放置使用预览后确认；散布、复制、删除有事务预览；删除连接留 workspace 草稿供修复。模板升级目前限定参数，保留参数冲突，不声称任意拓扑升级。
- 相机类型/位置、Workflow 缩放/滚动与面板阅读位置保存恢复；未知参数可阅读并阻止无效运行。完整所有 details 展开状态与初始分布 reset 命令仍未完成。
- Task 0.6 result 分页、二进制校验、单活动段 field 缓存、父子历史、stream export 有自动测试；尚未以真实长时巨大结果测量浏览器峰值内存。
- Offline shell/service worker、包管理、迁移预览/确认、checkpoint ZIP 导入界面已有代码；本轮尚未完成端到端实际浏览器验证。离线纯函数持久化测试不能替代离线冷启动测试。
- 新增 Modular foundation/material 欢迎页入口；最终源码的这些入口仍待浏览器验收。
- 本轮未完成 200% 浏览器缩放、实体触屏/触控板、软键盘/安全区、完整锁定 Workflow 右键删除和全部键盘流程实测。不得据此宣布 U01–U12 全部验收完成。
