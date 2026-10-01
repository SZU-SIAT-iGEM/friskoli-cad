# M4：保存与恢复空间运行状态

`spatial-unbiased-v1` 现在可以把已经提交的数值状态保存为自包含 JSON 文件，并在新的 Python 进程恢复。
文件带有冻结的 Project 0.4 输入、实际执行 seed、完整 checkpoint 和两层 SHA-256，恢复不依赖原项目文件所在位置。
这是 M4 的库与命令行入口；异步任务服务的 pause/resume 仍未开放，原任务记录不会因此改为续算中。

## 直接使用

安装本阶段 wheel 后，在 PowerShell 运行：

```powershell
$m4CheckpointDir = Join-Path $env:TEMP 'friskoli-m4-demo'
New-Item -ItemType Directory -Force -Path $m4CheckpointDir | Out-Null
python -m friskoli_cad.checkpoint --example --steps 20 --dt 0.1 --output "$m4CheckpointDir/step20.json"
python -m friskoli_cad.checkpoint --resume "$m4CheckpointDir/step20.json" --steps 30 --dt 0.1 --output "$m4CheckpointDir/step50.json"
```

第二条计算命令从第 20 步的真实状态继续 30 步，不重新初始化位置、材料库存或随机数。
`--project path.json` 可代替 `--example`，从自己的空间 Project 0.4 文件开始。
`--steps 0` 仅检查和另存当前状态；每次最多 10000 步，`--dt` 必须为正有限值。
要和连续运行严格比较，必须使用相同的数值步长序列；改变 `dt` 是新的数值离散实验。

已存在的输出默认拒绝。选择新的路径保留不同阶段；确需替换时显式加 `--overwrite`。
同一路径作为输入和输出也必须指定这个选项，整个续算与文件写入成功后才替换原文件。
父目录必须已存在。失败时输出 JSON 错误并返回非零退出码，成功时输出路径、文件大小、帧号、时间和摘要。
CLI 是直接数值运行入口，不使用服务队列、任务取消或服务级墙钟预算。

保存文件包含最新状态，**不包含此前整条轨迹**。历史结果回放继续由原来的任务结果管理。
界面的项目导入入口仍只读取设计文件，不能把 checkpoint 当作设计文件导入。

## Python 接口

```python
from friskoli_cad.engine.checkpoint_io import save_checkpoint, load_checkpoint

save_checkpoint(simulation, "step20.friskoli-checkpoint.json")
restored = load_checkpoint("step20.friskoli-checkpoint.json")
restored.step(0.1)
save_checkpoint(restored, "step21.friskoli-checkpoint.json")
```

`save_checkpoint` 必须在已提交步边界、调用者独占 simulation 时调用，不支持另一线程同时执行 `step()`。
自定义降解 adapter 必须已被显式安装和注册，随后把 registry 传给 `load_checkpoint(path, registry)`。
文件不携带 Python 可执行代码，也不会自动安装插件。

## 文件与恢复约束

外层格式 `checkpoint_file_version: 0.1.0` 由
[文件 Schema](../src/friskoli_cad/protocol/schemas/checkpoint-file-v0.1.schema.json) 描述；
内部保持 `spatial-checkpoint/v2`。外层 Schema 检查文件结构，恢复实现继续检查完整项目、数值状态及其相互关系。
文件中的 `document_sha256` 是去掉该字段后整份文档的 RFC 8785 SHA-256；内部 checkpoint 另有自己的摘要。
实际文件使用 UTF-8 JSON，保留浮点数字面量，避免较大 binary64 被写成超出安全范围的整数 token。
摘要用于发现损坏，不是作者身份或防篡改签名。

- 默认最多读取 64 MiB，超出时在 JSON 解析和数值分配前拒绝；Python 调用者可显式更改 `max_bytes`。
- 重复键、无效 UTF-8、NaN/Infinity、非法 Unicode、未知格式和未知字段均拒绝。
- 项目、模块、实现文件、已注册 adapter、依赖和运行环境必须符合内部版本锁；不执行隐式格式或科学状态迁移。
- 写入使用同目录临时文件，完整写入并 `fsync` 后公布。默认通过 hard link 原子创建，显式覆盖使用原子替换。
  不支持 hard link 的文件系统会明确拒绝默认创建，不退化成可能暴露半个文件的复制操作。
- 写盘或公布失败时保留原文件并清理本次临时文件；断电和存储硬件故障不在这个保证范围内。

本轮增加显式帧检查器和最后一步碰撞诊断保存，仍可检查旧 v2 的结构形式。
这项结构兼容不取消源码锁：上个安装包生成的状态若代码摘要不同，仍会拒绝恢复。
要继续旧运行，应使用产生该文件的同一安装包和相匹配的依赖环境。

## M4 状态逐项对应

| 原计划要求 | 本 profile 的实际状态 |
| --- | --- |
| 模块/菌群/稳定实体独立 RNG | 命名空间、PCG64 完整状态与缓存、实际执行 seed |
| 运行时钟与事件 | 时间、帧号、每菌剩余 tumble 等待时间；无待发出生/死亡事件 |
| 库存、模块状态与几何 | 浓度、来源与材料库存、胞内累计、信号、位姿、动态掩码、账本与输出 |
| ID 与帧序列 | 固定种群 ID、已见/存活 ID、下一帧号、上帧时间及最后一步碰撞诊断 |
| 日程位置 | 外部日程不受本 profile 支持，非空 controls 在运行前拒绝 |
| 扩散累计时间 | 每个数值步完整推进扩散子步，没有独立延迟队列或待补余时段 |
| 失败与恢复 | 候选步失败整体回滚，恢复接续既有 RNG 与时钟，不重新 seed |

生长、分裂、死亡、可变实体 ID、外部日程以及运行中更换环境都需要后续机制自己的状态映射和验收。
不能将它们尚未存在的状态以空占位符宣称已经支持。

实际验证见 [M4 验收](verification-m4.md)。下一阶段 M5 连接信号与运动，保留 PTS 有限养分、
MCP 均匀持续补给背景与独立引诱物、无趋化对照各自的科学假设。
