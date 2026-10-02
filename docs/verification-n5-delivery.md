# N5 独立交付检查

2026-10-02。本记录仅覆盖新增静态导出和标准子集，不代表整个 N5 发布门槛已完成。

## 已执行

Windows / Python 3.11 仓库虚拟环境，`PYTHONPATH=src`：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_wiki_standards.py -q
```

5 项测试通过（9.07 s）。覆盖真实内核计算的 3 帧记录（2 步，dt=0.05 s）构建/原生字节与完整 payload 保留；缺帧、草稿、取消运行、危险下载 URI 和已有目录拒绝；OMEX 清单与全部成员对应、原生字节和文件 SHA-256；pySBOL3 1.2.0.post0 Component 验证、序列化/回读、无序列以及缺来源和额外序列字段拒绝。另检查独立任务无虚构设计归属、三处来源 hash、篡改输入拒绝、原始 JSON 字节保留与未配置下载链接的安装说明。

测试中的 example.org 身份与下载链接只用于软件检查，不能作为公开项目下载入口、真实菌株或实际实验的来源。测试运行的锁字段来自既有测试 fixture；它们不作为可发布科学示例。实际科学示例须使用真实任务的冻结输入及版本锁另行构建。

## 实际计算记录构建

从完整版导出的实际 PTS-A 任务 `run_cc6a0eb81f7b4dd0bdbe8f569c8f57c8` 已构建为独立只读示例：95 s 模拟时间、1900 步、1901 帧。源文件为仓库外冻结的 `pts-a-1900.friskoli-run.json`（42,660,161 bytes），没有为它编造候选或设计归属。所有 task/manifest/replay 冻结来源 SHA-256、完整帧和事件已验证，构建目录为本机临时目录 `friskoli-n5-wiki-20261002`。该目录用于验收，未公开部署。

此记录 seed=42。静态 `record.json` 为 24,740,586 bytes，CSV 为 600,348 bytes，目录包含原始下载文件共约 68 MB。本机完整记录解析与逐帧验证为 11.68 s；这是一次数据校验耗时，不是求解速度、页面加载耗时或跨机器性能基准。

## 仍需发布验收

- 确认公开示例参数来源、环境记录、规模和使用限制；独立运行不能替代 N4 两候选与对照设计包验收。
- 静态服务无 backend 的 headless 浏览器与四尺寸检查已补充在下节；公开部署、真实触控板/手势与实体设备仍需分别记录。
- 对实际示例记录 CPU/内存、资源大小、加载耗时；不能把小测试速度推为完整模型性能。
- 根项目公开许可证、真实下载位置和从发布包而非源码目录的安装启动验证。
- 独立第三方 OMEX 验证器检查；SBML/SED-ML 有支持子模型后另做语义与数值复现，当前明确 unsupported。

构建器与静态 viewer 已可使用；缺上述记录时不标记整套 N5 验收完成。

## 共享 CAD 三维 viewer 验收（2026-10-02）

静态页现使用 `scene3d.mjs` 的 `SpatialViewport`，沿用深蓝 CAD 字标、双层导航、三维场景、时间轴与分区读数。只调用 `setDomain`、结果模式、保存 snapshot、相机和只读选择；没有内核客户端、编辑动作或求解入口。当前保存帧、完整来源与菌体通道按需展开，避免播放时重复生成大段 JSON。

发布构建新增相对路径 Three.js 与控制器依赖、几何 helper、原始 Logo 和 Three LICENSE；不需要外部 CDN 或字体。importmap 的实际文本 SHA-256 写入 CSP；文件清单递归覆盖子目录。`viewer-catalog.json` 明确为导出软件的显示适配器，科学版本锁和参数来源仍保留在冻结输入及原始文件。未知模块不补造环境几何。构建前完整性、原生字节、来源 hash、帧序列和浓度数组校验均保留。

更新的 `tests/test_wiki_standards.py` 五项检查通过（11.65 s），新增相对 importmap 与 CSP hash、递归资产 hash、共享 renderer 和 Three license 断言。新实际验收目录为仓库外 `C:/Users/Wu Shangru/AppData/Local/Temp/friskoli-n5-shared-viewer-20261002-v2`，没有覆写旧目录。通过只提供文件的 HTTP server 在 8884 端口验收；运行资源不请求计算 API。

本次 Chromium 使用 headless 模式，UA 确认包含 `HeadlessChrome`，未打开 headed 未验收窗口。以下操作实际完成：

- 原始 1901 保存帧载入，时间轴可访问第一帧和末帧；1898→1900 回放抵达末帧后自动暂停；前一帧、后一帧与拖动选择有效。
- 末帧原始时间为 `94.99999999999675` s，界面格式化为 95 s。`cell-000` 的 x 坐标与保存原值 `79.72365836920896` 完全一致。没有重算轨迹或插入模拟帧。
- nutrient 字段的 XY / XZ / YZ 切片、体素中心位置、μm 与 uM 单位、当前帧全场范围可见；末帧范围为 `0.293038 → 1.70591 uM`（显示精度）。当前记录为 2 μm 薄层，因此 XZ / YZ 截面真实地呈现为很薄的条带。
- 菌体选择读数、相机透视/前视/右视、记录与读数面板打开关闭、原始当前帧展开正常；浏览器 console 0 errors / 0 warnings。
- 浏览器尺寸 1440×900、1024×768、390×844、844×390 已截图并实际查看。手机横屏时间轴修正后下沿 357 px，位于页脚上沿 362 px 以内；无页面水平溢出。
- 另建 `hasTouch / isMobile` headless context 检查手机横竖屏的 coarse 布局，播放按钮至少 44 px，时间轴未被页脚遮盖。该检查为触屏布局模拟，没有声称实体手机或真实双指手势测试。

v2 包 22 个被校验文件合计 70,325,886 bytes（不含清单自身）；record 为 24,740,586 bytes；JS/CSS/SVG 运行资源合计 2,198,131 bytes。逐文件重算 SHA-256 全部一致。一次本机静态载入从 navigation 到可操作约 1,106 ms，JSON 解析阶段约 264 ms；载入后浏览器报告的 used JS heap 约 25.6 MB。这些是单次本地 headless 观测，不是设备通用性能保证，也不是总进程 RAM 或求解性能测量。页面提供读取 MB、解析状态和进度提示。

验收图片保存在临时目录 `n5-wiki-shared-*.png`，包括 desktop、final-frame、xz、yz、phone、phone-readings、landscape-final、tablet、touch-phone、touch-landscape；不纳入仓库资源。当前真实样例无环境障碍物/材料几何，因此本轮没有把环境对象显示适配器称为实际样例端到端验证。公开部署、发布许可与安装包、实体设备手势、CPU/总内存和跨设备性能仍需独立验收。
