# Workspace 与浏览器验证 · 2026-10-03

## 自动验证

最终 `node --test tests/*.test.mjs`：160 项，156 通过，4 个既有跳过，0 失败。新增覆盖命令锁定、settings 精确定位、事务依赖、旋转外廓、离线 Catalog/最近项目、run-level t 区间、分页二进制校验/缓存预算、父子恢复段拼接与流式导出、三方结构升级、缺帧轨迹断线、SW 版本隔离与无 aggregate metrics 的比较拒绝。已有 pointercancel 与单指/双指切换逻辑测试通过。所有修改过的入口通过 Node 语法检查。

## 实际浏览器流程

使用 Playwright Chromium，仓库外独立源码快照服务 http://127.0.0.1:8877。隔离快照避免并行开发中的源文件哈希变化；最终 modular 运行使用 science/runtime/system_limits 修复后的完整源码快照。

| 流程 | 操作与实际结果 |
| --- | --- |
| Task 0.6 初次运行 | PTS A，dt=0.01，12 steps，run_6ca8dfc974ea4aceb0c86c7e83a14aca 完成12/12，字段读取正常；上一帧0.12 s→0.11 s |
| 暂停与恢复 | run_936aa1e85ecc434b8eac59de326fd148 暂停386，checkpoint=386；child run_ae06e201277a42cf802c07d85c29a8f5 start_step=386，最后暂停463。393是中途观察值 |
| Checkpoint ZIP 导入 | 新PTS run_566a8b0eba46428cb6ae7a1a9f94de04暂停189；通过界面下载ZIP，再Settings导入，目标199；run_f1335e116a4e41b49af788e5c6a9f1b6完成199/199 |
| 保守网格迁移 | 由暂停189的任务，界面将20×10×1、10×10×2 µm改成40×20×1、5×5×2 µm；preview显示物质量差−3.725290298461914e−9 molecules；确认生成linked run_fd37a5ba866b4df6971c67bc05c9b3d9，从189继续至288暂停 |
| 跨段浏览 | 上述迁移任务的slider覆盖Frame0–288；键盘Home载入父段20×10网格，End载入子段40×20网格 |
| 最终Modular模板 | 欢迎页Catalog模板modular-foundation，dt=.01，100 steps；run_cbccebc8c8e04dda87476cf4a919c177完成100/100，字段与Frame100正常显示；最后复验console 0 errors。旧快照reserve精度失败已由science修复；修复前失败任务保留作证据 |
| 初始分布恢复 | Count8/seed3645557682→手工Count6/seed123→Reset预览原seed/尺寸/位置→Apply恢复8与原seed；一次Undo回6/123，Redo恢复。保留初始实际cell位置与几何，不仅记录seed |
| Details阅读状态 | 展开Capsule surface area后reload，再Recent重开，相同details保持展开 |
| 三方模板升级 | 由真实PTS owner节点生成模板v2，增加upgrade_added_area普通节点。预览明确Add node，确认后节点出现且Run可用；一次Undo移除新增节点。自动测试另覆盖本地/模板同时改参数的两种策略、结构删除、缺实现拒绝与手工边转修复草稿 |
| Offline冷启动 | 修复/index.html预缓存404；最终cache为friskoli-shell-20261003-5且无旧shell。断网reload仍打开app与Recent项目；modular项目max_cells300恢复，Run禁用。之前已实际断网修改steps4999，联网Reconnect后4999保留、Run启用。断网时3个API fetch失败为预期，非静态资源缺失 |
| 数据包可用流程 | ZIP preview→显式install uidemo@1.0.0→列出文件→下载foundation.project.json→预览后明确导入项目→resolve/pin项目锁并加载project Catalog成功。archive SHA-256 1172dbbafb726c167435b918a1a4033906bc5a27158ed3d6cc215087afc105e5；没有自动运行 |
| 资源上限 | Project0.6 Maximum live cells默认256，实际改300后reload保留300；控件max=2000，说明明确资源限制不是生物承载量 |

## 多端与缩放

390×844、844×390、768×1024、1440×900截图已逐张查看；主要导航与运行按钮可见，小屏通过Objects/Properties打开侧栏。横屏Runs面板会占用较多高度，可关闭。200%等效桌面重排采用720×450 CSS viewport（对应1440×900物理视口），确认document scrollWidth=clientWidth=720并查看截图。此项是浏览器模拟，未声称实际操作浏览器原生缩放菜单。

纯逻辑测试覆盖pointercancel、单指平移→双指缩放→单指恢复；键盘slider Home/End实际操作通过。实体触屏/触控板、软键盘、安全区仍须实体设备实测。

## 模板升级范围与运行边界

升级输入为template_version、owner内完整nodes及可选edges；按稳定ID对base/current/incoming三方比较，忽略JSON对象键排序。未改内容可新增、删除、替换；本地冲突默认保留，可明确选择incoming。手工连接优先保留，失效连接成为可修复草稿，修复或明确丢弃前Run禁用。升级是一项Undo事务，同时更新base/overrides；解除托管后保留初始分布恢复能力。

缺失精确module版本、参数不符合Catalog或观察channel端口不兼容时阻止提交。channels不属于nodes/edges模板的隐式升级范围；删除仍被观察channel引用的节点时，保留本地策略保留节点，incoming策略要求先修复观察引用，绝不悄悄删除观测。跨owner重命名/自动迁移不是此格式支持的操作。

新增异域migration映射下拉支持显式选择Modular physical-coordinates-zero-fill、identity sources；描述写明物理坐标保持、扩域零填充、非法裁切拒绝。同域流程已完成上述实际验收；异域后端是后续并行补充，本报告不声称又做了一次异域浏览器任务。

包内assembly JSON经过验证后加入本机机制库，不自动应用；本次实际包导入操作验证的是Project JSON。没有测量巨大真实长时结果的浏览器峰值内存；自动测试与代码预算不能当作该实测结果。未进行12小时用户壁钟性能验收。

## 证据与服务

证据在仓库外 `D:/Wu Shangru/Documents/工作区/friskoli-cad-task-evidence-2026-10-03/`：playwright截图、CLI快照/下载文件、隔离服务源码和ui-tasks数据库。此轮8877测试服务结束后停止，不改用户启动脚本，不占用主8876服务。
