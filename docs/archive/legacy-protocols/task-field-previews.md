# Task 0.5：计算网格与浓度预览

Task 0.5 在现有执行 profile 上增加通用输出能力；0.1–0.4 请求仍按原合同处理。

`output_plan.field_stride_xyz` 是可选 XYZ 正整数数组，默认 `[1,1,1]`。每个 stride 必须整除对应计算网格计数。worker 在请求的输出帧上计算均匀体素块的体积平均浓度，不抽取单点、不修改计算网格、物理步长、随机流或后续求解状态。

0.5 的每个 `concentrations[species]` 都包含 `unit`、`values_zyx`、`aggregation="volume_mean"` 和 `field_domain={geometry,counts_xyz,spacing_um_xyz}`。显示计数为计算计数除以 stride，显示间距为计算间距乘以 stride；空间范围不变。块平均保持全域浓度体积积分（浮点舍入范围内）。`grid_revision` 仍指向真实计算网格。旧合同不增加这些字段。

例如真实 `256³`、间距 `0.5 µm` 的网格，输出 stride `[8,8,8]` 形成 `32³`、间距 `4 µm` 的预览。10,000 steps、每 100 steps 输出一次，共 101 帧，包括初始和末帧。这些是请求参数说明，并不代表已经完成如此规模的运行。完整分辨率末帧可通过下述独立NPZ保存。

资源预估中的计算体素数和计算内存不会因 stride 减小；仅输出体积使用预览网格。不可整除的 stride 在启动前拒绝，单帧、总输出、真实内存和运行时上限仍生效。

`execution.backend` 在 0.5 中可显式选择 `numpy-cpu` 或可用的 `numpy-cupy-cuda`。后者只加速场扩散；细胞科学模块与 RNG 仍在 CPU。不可用 backend 在 admission 拒绝，不静默换用另一个 backend。实际选择写入 provenance。旧合同仅支持 CPU。

规范：`src/friskoli_cad/protocol/schemas/task-v0.5.schema.json`；OpenAPI：`docs/protocol/tasks-openapi-v0.5.json`。

## 完整末帧场与本机资源

设置 `output_plan.include_final_fields=true` 可独立保存完整分辨率末帧场，默认false。此开关与JSON预览分别生效。worker仅在最后完整数值步写私有临时NPZ；parent核验SHA-256与字节数、原子发布，再与完成状态共同索引。取消或中断不发布未完成的末帧场。

`manifest.artifacts` 提供 `artifact_id=final_fields`、href、SHA-256、字节数、末步时间、真实field_domain及各物种统计。`GET /api/runs/{run_id}/artifacts/final_fields` 校验文件后流式下载。恢复时复核索引；损坏返回明确错误。

NPZ内 `field_0000` 等是完整float64 ZYX数组，`metadata_utf8`是UTF-8 JSON字节的uint8数组，保存物种与数组键映射、单位、网格、时间、最小/最大/均值和浓度体积积分对应的总分子数。可用 `numpy.load(path, allow_pickle=False)` 读取。它不含pickle/object数组，也不是可继续运行的checkpoint。

CLI新增 `--task-voxels`、`--task-memory-mib`、`--task-output-mib`、`--task-chunk-mib`，旧默认不变。预检检查完整计算网格预算、当前可用主机RAM、CUDA显存及磁盘空间。0.5场内存按实际拥有者计数，不重复计算不可变snapshot/graph aliases；每物种预算八份float64缓冲及掩码/几何余量。这是保守估算，并非实测峰值；运行时仍监控worker RSS。

256³单物种的原始NPZ约128MiB，101帧32³JSON预览另计。提高预算不会缩小计算网格，也不保证运行时间。预检后可用资源仍可能变化。

真实spawn worker测试覆盖旧0.4逐帧数值一致、0.5均值预览、完整NPZ HTTP下载、allow_pickle=False读取与direct solver末帧逐元素相等、manifest schema、重启恢复及篡改检测。未据此宣称256³/10,000steps已完成或达到特定速度。

单物种256³任务的资源配置示例（任务的stride和最终场开关仍在请求内明确设置）：

```powershell
python -m friskoli_cad.replay_service --task-voxels 16777216 --task-memory-mib 2048 --task-output-mib 512 --task-chunk-mib 4
```

仍需按运行需求设置 `--task-wall-time-s`（CLI默认1800秒）；该命令不承诺此时限内完成。任务目录沿用默认仓库外路径，也可通过 `--task-dir` 指定。CUDA provenance额外记录CuPy版本、CUDA runtime/driver版本、设备名及compute capability；CPU旧字段保持不变。
