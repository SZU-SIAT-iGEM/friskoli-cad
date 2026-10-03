# N3 M0–M3 验收记录

日期：2026-09-30。基线为 `43ffad3` / `n2-async-tasks`，功能提交为 `55a36b6`，最终显示修复为 `680a50e`。本轮只完成科学迁入计划
M0–M3 和相应 Workflow 组件改进，完整 N3 趋化链仍在后续计划中。

## 交付范围

- 8 类模块组成有限均匀 bulk、胶囊面积、A/B 两套容量规则、浓度采样、PTS 请求、共享结算和接受通量驱动的 EI/CheA/CheY 信号。
- `conservative-pts-bulk-v1` 使用独立 Project 0.3 / Catalog 0.2 / Task 0.2；legacy 图、目录和任务保持原版本。新编译计划明确记录实际执行的准备、按库存结算和信号阶段。
- 库存与胞内接受量共享同一次结算，检查单位、群组、物种、唯一 owner、边时序和有限精度。失败不提前提交库存、信号或时钟。
- 来源锁记录两份审查副本的提交与 9 个来源文件 SHA；包内包含参数证据、构造例和固定条件输出。运行时无需读取外部模型目录。
- 欢迎页新增“共享底物与 PTS 信号”，包含 2 个菌群、3 个固定菌体、13 个节点和 14 条连接。Workflow 的名称/ID、参数、端口、公式与状态拆为可组合组件。

具体合同见[执行协议](protocol/pts-bulk-profile.md)，方程、来源及证据界限见
[科学说明](science/pts-minimal.md)，独立结算 API 见[共享库存](science/settlement.md)。

## 源码测试

最终源码运行：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='src;C:/Users/Wu Shangru/AppData/Local/Temp/friskoli-n2-deps'
python -B -m pytest tests -q -p no:cacheprovider --basetemp "$env:TEMP/friskoli-n3-pytest-pipe-final"
node --test --test-isolation=none tests/*.test.mjs
```

结果：**265 passed，126 subtests passed**，Python 用时 54.38 s；**55 项 JavaScript 通过**。
子测试单独列出，不混入独立用例数。开发机测试耗时不能作为科学运行性能指标。
日志为系统 Temp 中的 `friskoli-n3-python-pipe-final.log` 和 `friskoli-n3-javascript-display-final.log`。
Python 验证后仅修改 Workflow 顶部预留空间与缩放坐标，并重新运行全部 55 项 JavaScript；数值与服务源码未改变。

| 检查 | 本轮覆盖 |
| --- | --- |
| 科学公式，25 项 | A/B 容量、球极限面积、零浓度、参数边界、形状/广播、来源固定条件对照、EI/CheY 稳态、连续积分对照及时间步细化 |
| 结算 reference，47 项 | 多菌共享、跨支持、ID 对齐、排列不变、耗尽、严格拒绝、精确残差、下溢/舍入、提交前核对 |
| PTS runtime，15 项 | 新图实际执行、跨菌群共享库存、接受量与信号一致、非法 owner/时序拒绝、多步守恒、超精度范围回滚 |
| PTS 任务，8 项 | 真实子进程、版本锁、冻结输入、编译 schedule、结果/校验摘要、资源估算及错误路径 |
| PTS HTTP，14 项 | 实际 HTTP 服务，新旧能力与目录、严格查询参数、两种任务 schema、共同错误 schema、异步输出与直接运行一致 |
| 既有服务与工作区 | 原有 legacy 测试继续通过；新增发布/确认边界强杀回归；JS 新增 9 项组件检查和 3 项 profile/任务恢复检查 |

科学函数与实际 runtime 共 40 项还经过独立复核。来源文件指纹与锁定修订提交一致；
构造参数明确标记，未伪装为实验校准。

文档与合同另检查 164 个本地 Markdown 链接、96 个本地 JSON 引用和 3 份新增 Schema，均通过；
最终 `git diff --check` 与暂存内容检查通过。

## 发现并修正的异常路径

首次全量测试暴露出 worker 强制终止后服务可能停在确认信号处。独立受控复现显示：
子进程在 `multiprocessing.Event.wait()` 内被终止后，父进程 `Event.set()` 可永久阻塞于
共享 Condition 的通知过程。受控诊断说明了机制，不将其冒充为首次随机失败的原始堆栈。

确认改为单向 Pipe，每步最多一个确认字节，保持完整块耐久发布后再确认。两端句柄均关闭；
worker 断开记为失败，保留已发布的 partial。新增测试在耐久发布前后轮流强杀 6 次，
每次之后都运行一个正常任务，并检查服务关闭、重新打开同一目录及再次运行。
专项两次执行共 12 轮通过；最终全量测试另包含这 6 轮。没有提高业务超时或跳过测试。

另一项旧 HTTP 断言仍只允许 Catalog 0.1，现明确要求 `["0.1.0", "0.2.0"]`，
同时继续检查默认目录仍为 legacy，没有以放宽为任意版本绕过兼容验证。

## 浏览器与界面

实际 Chromium 检查见[Workflow 组件验收](verification-workflow-components.md)：
四尺寸、真实应用 13 节点/14 连线、完整 ID 复制、拖动与 Undo/Redo、公式、粗指针和双指事件模拟。
实体鼠标、触控板、触屏和软键盘未实测。

正式服务重启并读取当前版本锁后，浏览器提交 Task 0.2 任务
`run_345c4d113c3f4a8ba5bfc48e8ce9823c`：8/8 步、dt=0.5 s、终点 4 s，状态 completed，
结果 complete，共 9 帧。a0 末帧显示 accepted_flux≈0.0082 molecule/s、
cumulative_uptake≈0.0331 molecule、EI 去磷酸化比例≈0.0187、CheY-P≈0.4937 uM、
motor bias≈0.196；位置仍为 (0.5, 0.5, 0.5) um。这里是构造值的软件检查结果。

初次开发预览曾在服务启动后修改源码，导致旧进程缓存的实现锁与子进程源码不一致，
任务正确失败且未发布输出。重启服务后使用新锁提交通过；原 failed 记录继续保留，未删除或改成成功。
截图 `workflow-n3-final-signals.png` 保存在系统 Temp，主代理已目视检查。

刷新页面后恢复同一 N3 草稿、选择原 completed run 并回到 Workflow，捕获到
`POST /api/runs = 0`，仍显示完整结果，未重新提交任务。新示例的默认 13 节点两两矩形不重叠；
N3 → 旧生长分裂示例 → 空项目 → N3 的目录切换没有产生未知模块。

最后截图发现 Fit 后工具条覆盖首排标题，修复为固定顶部预留空间，并同步调整缩放锚点、Fit 滚动和画板最小高度。
桌面与 390 宽浏览器复验首排完整、100% 阅读正常、Ctrl-wheel 锚点误差小于 1 px；
更新后的 `workflow-n3-final-fit-completed.png` 已由主代理目视检查。此修复后重新构建并验收最终安装包。

## 安装包

交付目录：相邻 `friskoli-cad-releases/n3-m0-m3-20260930/`。它是 Python wheel，
尚未制作 Windows EXE 安装向导。N2 包保留在原交付目录，不覆盖。

- 文件：`friskoli_cad-0.1.0-py3-none-any.whl`，1,661,124 bytes。
- SHA-256：`6a3d5d5b52556324a43722eb80c62d6a2a99051ff4af194dd9d8904575f4d5ea`。
- **696 项检查通过**：162 个有效源码/资源文件逐字节比对 wheel 与安装目录；89 条静态 HTTP 路由；两种 profile 的真实 spawn 均 completed、各 4 chunks。
- 验证进程加载的 26 个项目模块全部来自隔离安装目录；`sys.path` 不含主库源码或外部科学模型仓库。验证进程的工作目录和 PYTHONPATH 独立于源码目录。
- 科学数据 3 份 JSON、新 schema、目录、示例、Workflow 组件、实际 schedule 和共同错误 schema 均在检查范围。
- 最终源码快照比较 `163/163` 一致（含 pyproject），没有新增、修改或删除。发行版本仍为 0.1.0，以阶段目录、SHA 和 Git 标签区分 N2 / N3；各协议版本独立管理。

构建使用独立临时 staging 和已有构建依赖，随后将 wheel 隔离安装；未全局安装或在主库留下构建产物。
验证依赖：Python 3.11，NumPy 2.1.2、jsonschema 4.26.0、rfc8785 0.1.4、psutil 7.2.2。
原始报告 `verification.json` 与 `source-comparison.json` 随包保留。696 是资源/API/运行断言的检查数，
不与 265 项源码测试相加。

## 明确未完成

当前为全域均匀有限库存、固定几何和确定性 PTS 信号。motor bias 未驱动运动，
没有局部梯度、扩散、随机 run/tumble、纤维、接触、生长或死亡。胞内累计量也不是完整碳/能量账。
EI 使用冻结通量解析推进；CheY 使用步末 CheA 的串联近似，完整耦合仍是一阶时间精度。

异步结果仅含逐菌体帧和通道，暂不传全局库存时间序列或场热图；库存可通过运行器只读查询。
没有任意参数/时间步的科学有效性承诺，也没有实验标定、完整趋化对照、通用暂停续算或 GPU 加速。

下一步先实施 M4 独立随机流、状态保存及失败回滚，再接 M5 运动、边界、信号耦合和无趋化对照。
N4 候选设计与比较、N5 发布和标准导出继续依赖完整 N3 的验收。
