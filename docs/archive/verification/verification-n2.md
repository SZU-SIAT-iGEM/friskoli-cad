# N2 验收记录

日期：2026-09-30。实现提交为 `240090d`，分支为 `feat/registry-contracts`，任务合同为 `0.1.0`。
本记录针对单机任务服务，不代表科学模型已标定、支持 GPU 或完成正式产品发布。

## 可检查的功能

提交时冻结项目、编辑修订、版本锁和输出计划，SQLite 保存任务与幂等键；单 worker
通过独立子进程计算。状态、输入、事件、结果清单和完整块分别查询。任务运行时可继续
编辑或打开项目，旧响应只更新其所属运行记录。取消与完成共用终态事务，已公布 partial
保留；服务重启把未完成任务记为 interrupted。

合同和同步兼容边界见[任务合同](protocol/task-contract.md)，启动方式、存放位置和限制见
[任务服务说明](task-service.md)。旧 draft Schema/OpenAPI 仍有单独测试。

## 自动化检查

- 最终 Python 全套 **155 项通过**（44.600 s），其中任务 HTTP 5 项、任务服务 16 项、稳定合同 15 项。
  内部 JCS 大数修复覆盖输入冻结、真实 worker、事件、结果、重启、幂等与全部 24 个 RFC 8785 有限数值向量。
- HTTP 实测覆盖新提交、16 路服务并发同键及 HTTP 并发重试、同键冲突、严格 JSON、未知执行方式、
  参数错误、输入快照、事件分页、bodyless cancel、超长/重复 Content-Length、Schema 与块 SHA-256。
- 服务故障测试覆盖排队/运行取消、两种终态竞争、真实 worker 异常退出、宿主强杀重启、
  文件写失败、SQLite 提交失败、未索引孤立块、磁盘不足、增长/RSS/墙钟/输出超限、
  游标过期、单目录服务锁与保留期/删除标记。磁盘不足通过故障注入检查，未实际写满磁盘。
- JavaScript 43 项通过，包含 20 项既有行为及 23 项任务回归；检查原键原内容重试、接受前保存、
  有界存储、查询恢复、410 事件缺口标记、停止无限重试、迟到响应、终态不倒退、部分结果与完整结果、
  原始块长度/校验和、bodyless cancel，以及无变化轮询不重复写存储或重绘。

在已安装依赖的环境，从仓库运行：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='src'
python -m unittest discover -s tests -q
node --test --test-isolation=none tests/*.test.mjs
```

## 安装包验证

从主库复制 `src` 与 `pyproject.toml` 到 Temp staging，离线构建 wheel，再安装到独立 target。
验证进程使用 `python -B -s`，工作目录为独立空目录，`PYTHONPATH` 只含已安装包和依赖副本；
全部 20 个加载的 Friskoli 模块均来自安装 target，没有通过源码目录导入。

**604 项检查通过**：148 个包内源文件与构建快照逐一比对 SHA-256；88 个静态路由均通过真实 HTTP
读取；稳定任务 Schema、task-store、three.js、KaTeX 字体可用；安装包真实 spawn 任务完成 3 步，
输出 4 个完整块，校验计划摘要、序列、Task/Manifest/EventPage/ChunkBody 和原项目 run ID。

构建依赖取自本机 bundled runtime 并复制到 Temp；未安装或改写全局 Python。
具体包摘要随最终交付目录的 `SHA256SUMS.txt` 保存。已安装依赖的隔离验收不等于全新机器安装测试。

本机交付目录为 `D:/Wu Shangru/Documents/工作区/friskoli-cad-releases/n2-20260930/`，
包含 `friskoli_cad-0.1.0-py3-none-any.whl`、安装说明、校验和与原始安装验证记录。
wheel 大小为 1,621,066 字节，SHA-256 为
`6e39af4750ea2490eb6b9fc4e661b75ff78c6563264486e60813a5bb1aeb41df`。
这是 N2 的阶段安装包；Python distribution 版本仍为 `0.1.0`，任务合同版本单独管理。

## 浏览器操作验收

- 桌面 Chromium 真实提交 8 步任务，收到 9 帧，任务与状态栏均显示 Completed。
- 运行中取消后显示 Cancelled，已经公布的初始帧保持 Partial；刷新页面后按同一任务 ID 恢复查询和结果。
- 运行中继续编辑，迟到结果不接管当前草稿；运行后新建项目，旧任务完成时仍停留在新项目的 Space。
- 1440×900、1024×768、390×844、844×390 四种浏览器尺寸均检查 Runs 面板滚动、
  Submitted input snapshot 打开及 Escape 关闭。手机尺寸下面板紧凑，但这些控件仍可访问。
- 截图与操作记录保存在系统 Temp 的 `friskoli-n2-browser-final` 目录；这是桌面浏览器操作与尺寸模拟，
  未完成实体触屏、触控板或完整手势验收。图组件重构、白色控件和缩放体验仍按后续界面计划处理。

## 本轮已知限制

- UI 每次运行仍为 1–100 步；API 上限单独发布。任务运行恢复的是查询，不是 checkpoint 续算。
- 异步结果只含既有单菌体 frame，没有场体素数组和热力图；旧同步入口仍保留原场结果。
- RSS 每 10 ms 采样，不是操作系统硬配额；输出与时间限制超出后保留已公布块。
- 保留和事件清理目前通过 Python 方法显式调用，无自动后台清理或管理 UI。
- 浏览器尺寸模拟、桌面 Chromium 操作与实体设备测试分别记录；实体触屏、触控板及所有手势未测。
- wheel 是 Python 安装包，依赖已有 Python 3.11+ 环境；没有 Windows `.exe` 安装器，未验证全新电脑安装。

临时日志、快照、浏览器图片与构建目录放在系统 Temp；主仓库只提交源码、测试、协议与说明。
