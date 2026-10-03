# N3 前端验收记录

日期：2026-10-01。环境：Windows、Node test runner、Chrome + Playwright CLI、本地实际任务服务 `http://127.0.0.1:8876`。本页只记录本次实际执行的验证；科学数值与发布安装验证另见 N3 主验收记录。

## 自动化

- `node --check src/friskoli_cad/web/app.mjs` 通过。
- `node --test tests/*.test.mjs`：90 项通过，0 失败。
- 新增测试覆盖：声明默认值与缺参、Workspace 0.4/Project 0.5 往返、指标校验与 CSV、完整运行比较和样本 SD、重复 seed/partial/不兼容输入拒绝、完整版本锁分组、死亡事件与 sidecar 的严格一一对应、行为分支 owner/节点/通道/边重映射、固定 seed 保守避碰及失败原子性、Task 0.4 稀疏帧和累积生命周期事件。
- 运行中的非保存步进度另有回归：committed step 为 4、stride 为 3 时，正确接收 `[0,3]`，不要求虚构第 4 帧。
- 历史容量测试覆盖两批各 8 条结果全部保留、32 条上限、比较选择保护、容量不足拒绝保持原列表，以及存储异常/字节超限时恢复原记录和结果缓存。这一项为自动化行为测试，未冒称浏览器完成 16 次数值运行。

完整输出保存在本机外部验收目录的 `ui/js-tests.txt`。

## 实际浏览器操作

1. 从欢迎页打开 PTS A，检查其真实行为图及 Evidence 公式、注册声明、来源；在 Workflow 替换为 MCP，实际节点更新。修改 dt 后再次替换会询问是否舍弃修改，取消后保留原 MCP 图和 dt。
2. 复制 MCP 初始化块、散布 8 个新菌体，再显式复制原群行为分支；得到 2 群、16 菌体、18 节点。实际 Check 通过（16 cells / 200 voxels）。保存 Workspace 0.4 后通过 Open 重开，保留 Project 0.5 与双群输入。
3. 实际 MCP Task 0.4 单次运行完成：`run_17cddf87c064453ba656b9b2b4ef0b33`，16 菌体、seed 42、dt 0.1 s、20 steps。
4. 将保存间隔设为 7，通过原生 seed series 控件依次运行 seed 7 与 8：`run_97cefd791d6b4d76983b91a057cc604e`、`run_af273492235b46a98acea8e539647dfc`。两次均完成，结果帧为 `[0,7,14,20]`，保留 ligand/nutrient 场、metrics 0.1 与 lifecycle sidecar。
5. 显式选中这两个运行，显示均值和 SD，导出比较 CSV。实际文件包含 8 行原始/mean/sd 数据及完整 version_lock；结果 JSON、指标 CSV 也通过原生导出按钮下载。重载浏览器后按原 run ID 恢复完成状态，没有重复提交。
6. 导入外部 UI 诊断 fixture：从 lifecycle 模板修改 simplified health 的 `initial_health=0`、`death_max_per_min=600`，只用于触发显示路径。`run_eecb8ee5e2264f4197efd0f65c0784bd` 实际完成，Data 出现带模块、health、hazard、概率与 draw 的死亡明细。该 fixture 不作生物参数推荐。更早仅修改 rebuilt hazard 参数的诊断 run 没有死亡，未作为死亡显示证据。
7. 在 Space 的 observation 原生控件中将位移轴改为 Y，保存后检查实际下载文件：Workspace 0.4、observation.axis 为 1。
8. 默认 PTS A 图以 dt 0.05 s、1000 steps、stride 10 实际运行，点击 Cancel task；`run_b0425876b3364d05b4f1d7d553ea73c1` 在已保存 step 410 取消。重载后可查看已公布帧，界面显示 Cancelled / Partial output，没有加入比较复选框。导出 JSON 实际带 `status: cancelled`、`completeness: partial`，末帧为 410。
9. 第 8 项之前的旧服务在开发期源文件修改后返回 initialize 拒绝（`run_d71e54df00c54eda9a53ef5e352b6d43`）。服务启动时固定 source hashes，worker 再核对磁盘文件，拒绝混用代码；重启服务后相同模板正常运行。此记录属于执行文件锁保护，不记作模型计算失败。Runs 现在也显示后端返回的具体 issue。

## 四尺寸和视觉检查

实际 Chrome 浏览器尺寸：桌面 1440×900、平板 1024×768、手机竖屏 390×844、手机横屏 844×390。各尺寸均检查 Space/Results、面板和主控件，最终四张结果截图已逐张查看；document 无横向溢出。修正了竖屏 dt 输入被裁切、横屏时间轴/视图控件重叠、手机抽屉跨回桌面后 backdrop 残留，以及原生滚动条过亮。深色滚动条已在最终截图确认。

截图位于仓库外：
`C:/Users/Wu Shangru/AppData/Local/Temp/friskoli-n3-complete-20261001/ui/screenshots/`

- `n3-final-desktop.png`
- `n3-final-tablet.png`
- `n3-final-portrait.png`
- `n3-final-landscape.png`
- `n3-death-details.png`
- `n3-partial.png`

资料完整阅读审计、需求对应记录、下载文件和其他过程截图均在同级 `ui` 目录。既有 pointer/gesture 单元测试随 90 项测试通过；本次为浏览器尺寸模拟与程序化操作，未完成实体手机、软键盘、安全区或真实触控板验证。保守外接球散布可能拒绝实际胶囊可容纳的紧密排列，会明确失败并保留原输入。界面不声称模板参数已完成生物标定，也不提供耗时/内存预估。

## 材料整数 copies 修复后的追加验收（15:22）

安装验证发现的 extensive 整数数组乘分配权重问题修复后，关闭旧 8876 服务并以相同参数重启，沿用原任务目录；浏览器原有 7 条已验收历史均保留。未修改前端源码，未重复执行无变更的 JS 测试或四尺寸验证。

通过欢迎页选择 Contact degradation 材料模板，保留示例参数，设 dt 0.05 s、20 steps。实际 Check 返回 valid（8 cells / 200 voxels）。点击 Run 完成 `run_e25476644228458ebbcbd81a866ae389`，得到 21 个真实帧、末帧 step 20 / 1.0000000000000002 s。结果 provenance 中全部 `source_sha256` 与当前磁盘 `source_hashes()` 逐项一致，确认 worker 使用修复后的源码。

在 Results 选择 nutrient 浓度场并将时间轴移至末帧；Data 显示库存曲线，选择 substrate 后属性显示 `9990.0025 molecule`。同时核对实际结果：substrate 库存从 10000 降至 9990.0025，attractant 从 10000 降至 9900；nutrient 场最大值从 1.76 变为 1.7592037773508615 µM。变化来自记录帧，并非前端生成。

外部证据：`ui/materials-post-fix-evidence.json`、`ui/materials-post-fix-final.yml`、`ui/screenshots/n3-materials-post-fix.png`。服务留在 `http://127.0.0.1:8876`，浏览器停在该运行末帧、材料库存属性与 Data 面板。
