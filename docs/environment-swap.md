# 环境模块的运行时替换 · 阶段 3d

同一套菌体采样、线性摄取和通量汇总模块，现在可分别连接 `field.local_inventory` 与 `field.ideal_reservoir`。两种环境实现都接收物种相同的 `consumption_rate: field.scalar, uM/s`，输出 `concentration: field.scalar, uM`；[有限库存行为图](../examples/runtime/uptake.graph.json)与[理想储库行为图](../examples/runtime/reservoir.graph.json)只有环境节点的模块 ID 与图 ID 不同，其余节点、参数和连接边相同。

| 环境实现 | 一步后的浓度 | 环境中的额外记录 |
| --- | --- | --- |
| 有限库存 `field.local_inventory` | `C(t+Δt) = C(t) − R(t)Δt` | 没有外部补给，库存不足时拒绝该步 |
| 理想储库 `field.ideal_reservoir` | `C(t+Δt) = C(t)` | 按 `R(t) × 每格 1 uM 对应的分子数` 补给，记录每格补给速率与累计分子数 |

`R(t)` 是前一帧由菌体通量汇总得到的局部消耗率，单位为 `uM/s`。每格 `1 uM` 对应的分子数为 `602.214076 × 体素体积(µm³)`。储库是**无限补给的演示性闭合条件**，没有模拟实际流动、补给延迟或扩散。它的 `supply_flux` 单位为每格 `molecule/s`，`cumulative_supply` 单位为每格 `molecule`；环境快照为每个输出保留形状、物种和单位。在当前只有摄取这一去向的例子中，储库累计补给分子数等于菌体累计摄取分子数。

在仓库根目录运行：

```powershell
$env:PYTHONPATH='src'
python examples/runtime/compare_environment.py --geometry thin_layer
python examples/runtime/compare_environment.py --geometry volume
```

薄层例子从 `10 uM` 开始。第一秒后，有限库存中菌体所在格点降到约 `9.93358 uM`；理想储库维持 `10 uM`，两个菌体共摄取并从储库补入 `2000` 个分子。第二秒起，有限库存中的摄取通量随浓度下降，储库中的通量保持不变。完整 3D 使用同一行为图，在不同 Z 格的菌体上得到相同的替换关系。

这里的“替换”发生在**创建新运行时**。模块的内部状态不同，当前没有在一次运行中途把有限库存状态迁移成储库状态。外部模块即使端口形状、单位与物种都兼容，也需要说明科学假设、参数来源和状态迁移规则；接口兼容不代表结果科学等价。
