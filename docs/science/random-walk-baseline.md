# 无偏 run/tumble 参考机制

`isotropic-poisson-run-tumble-v1` 是 M4 的数值参考机制。速度 `speed_um_s`、翻滚事件率 `tumble_rate_s` 和维度 `dimensions` 必须显式构造。这里没有菌株生理标定，也没有 PTS、CheY-P、浓度或资源梯度对运动的偏置。初始朝向来自项目姿态，不用随机数覆盖它。

每个 run 以恒定速度沿当前 heading 平移，tumble 视为瞬时事件。事件等待时间服从指数分布，率为 `tumble_rate_s`；下一次等待的剩余时间保存在 cell state，跨时间步沿用。2D 在平面均匀抽样角度且 heading.z 严格为零；3D 使用均匀方位角和均匀 `cos(theta)` 在球面各向同性抽样。3D 不能把两个极角都取均匀分布。该机制未包含有限 tumble 时长、旋转扩散或相邻 run 的转角记忆，因此不能直接作为真实细菌运动的定量预测。

`speed_um_s=0` 时位置不变，非零事件率仍会更新朝向。`tumble_rate_s=0` 时保持初始/碰撞后朝向，不消耗随机数。`dt_s=0` 返回原状态，不初始化随机流。参数在一次仿真中保持固定；若以后需要改变事件率，应另行定义剩余 hazard 的变换规则，不能直接重用旧率下的剩余等待时间。

## 可复现随机流

`RandomStreams(run_seed).stream(node_id, group_id, stable_cell_id, purpose)` 为每个身份组合创建独立命名空间。稳定 cell ID 不得用数组序号替代。键用 JSON 数组无歧义编码，与算法版本和十进制 run seed 一起通过 SHA-256 派生 PCG64 seed。创建顺序、群组/细胞排序、新增无关细胞以及新增其他用途的流不改变现有 cell 的序列。这是伪随机流隔离机制，不声称有限状态流之间在数学上完全独立或永不碰撞。

本版明确使用 `numpy.random.PCG64`，不用可改变默认生成器的 `default_rng`。`tumble_wait` 与 `tumble_direction` 使用不同流。浮点 uniform 由一次 `random_raw()` 的高 52 位映射到开区间中点；指数等待使用 `-log(u)/rate`。这些变换由本模块固定，不依赖 NumPy `Generator` 的分布采样实现。

NumPy 官方说明 PCG64 对固定 seed 保证相同的随机整数流，并说明其状态包含两个 128 位整数。[PCG64 官方文档](https://numpy.org/doc/stable/reference/random/bit_generators/pcg64.html)。NumPy 对一般 `Generator` 的复现保证要求同样调用、构建、环境与机器，分布实现可能随版本改变。[NumPy compatibility policy](https://numpy.org/doc/stable/reference/random/compatibility.html)。两份文档于 2026-09-30 核对。

本项目允许安装的 NumPy 范围较宽，但随机 checkpoint **要求恢复端 NumPy 完整版本字符串一致**；本次单测在 NumPy **2.1.2** 上运行。checkpoint 保存算法版本、NumPy 版本、十进制字符串 seed、各身份键和完整 PCG64 状态，包括 `has_uint32` / `uinteger` 缓存。128 位 `state` / `inc` 使用 32 字符小写 hex，避免 I-JSON 的安全整数范围问题。`rfc8785.dumps` 的测试确认 checkpoint 可做规范 JSON 编码。恢复直接设置完整状态，不根据 seed 重新开流。版本不一致会明确拒绝；没有静默迁移。

原始随机整数流的可复现性不等同于整条浮点轨迹跨平台逐位一致。严格科学复现还应保存 Python、NumPy 构建、平台环境、项目参数与执行版本，固定同一环境。`log`、三角函数以及浮点累加可能受平台影响。

M4 的[自包含文件入口](../checkpoint-files.md)在完整空间状态中保存这些 RNG 状态。2026-10-01 增量严格检查 RNG/walk 的字段和 JSON 数值类型，拒绝静默忽略未知字段或把布尔/字符串转换成朝向。原有合法随机序列与积分规则保持不变；系统级续算与回滚见 [M4 验收](../verification-m4.md)。

## 事务与碰撞接口

在整个模拟步开始时调用 `candidate_streams = committed_streams.clone()`。对每个 cell 调用 `advance_random_walk(..., candidate_streams, node_id=..., group_id=..., cell_id=...)`，得到不可变的 proposal。所有碰撞、结算、数值和帧验证成功后，才一并替换 committed streams、cell state、位置和时间。失败时丢弃 candidate；再次从 committed clone 重试会得到相同抽样。`advance_random_walk` 会消费传入的实例，不能直接传 committed owner 进行可能失败的计算。

proposal 包含最终位置、带剩余等待时间的 state、每段 `start_um/end_um/time_start_s/time_end_s/heading/end_heading`，以及 `events=((time_s, new_heading), ...)`。时间相对于该模拟步开始，恰好发生在步末的事件也会记录并更新最终 state。root 可以合并所有 cell 的事件时间，在共同区间同步调用碰撞检查；之后保存 proposal 的剩余等待时间，并用实际接受姿态替换最终 heading。该做法避免按 cell 顺序逐一处理碰撞的顺序偏差。

可选的 `transport(start, heading, duration_s, speed_um_s)` 回调用于单 cell 或外部已协调的路径约束，返回终点和碰撞后的 heading，供下一 run 段延续。该回调自身也必须只修改 candidate 状态。它不取代同步多 cell 碰撞的调度。

每次调用最多接受 `max_events` 个 tumble，默认数值预算为 10000；预算是计算保护，不是生物参数。超限抛出 `RandomWalkBudgetError`，不得截断、跳过事件或提交部分结果。非有限参数、负数、非法维度、无效单位 heading 以及不可表示的等待时间均明确拒绝。

保存帧频率不触发抽样。把同一物理时间区间分成不同模拟步，使用同一初始状态和参数时，事件序列及 RNG 最终状态相同，位置和事件时间仅允许浮点累加误差；单测比较 2D/3D 整段和分段积分。保存帧频率也不应改变上层物理调度的步长或碰撞预算。

## 验证范围

`tests/test_random_streams.py` 覆盖命名空间隔离、重排序/无关流不扰动、不同 seed、完整 uint32 缓存恢复、I-JSON 编码、clone 回滚和无效 checkpoint。`tests/test_random_walk.py` 覆盖整段/分段一致、checkpoint 继续运行、拒绝重试、多事件预算、步末事件、零速度/零事件率/零步长、初始 heading 保留、回调反射以及参数错误。

固定 seed 的 20000 个抽样检查方向均值、二阶矩和交叉矩：2D 期望二阶矩为 `(1/2, 1/2, 0)`，3D 为 `(1/3, 1/3, 1/3)`。另检查指数等待均值与标准差。它们是可复现的回归检查，不能替代更大规模的 RNG 质量研究或生物实验标定。
