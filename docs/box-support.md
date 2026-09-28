# 固定物理范围的采样与沉积 · 阶段 3g

原来的 `field.sample_nearest` 与 `field.deposit_nearest` 只使用菌体中心所在的一格。新增 `field.sample_box_support` 与 `field.deposit_box_support`，作为可替换的空间耦合模块：行为图的环境、摄取模块与连接方式不变，只更换采样和沉积的实现。

新模块用 `support_x_um`、`support_y_um`、`support_z_um` 指定以菌体中心为中心的**作用范围**。它们是明确的物理长度，不能自动解释成菌体本身的长、宽、高。示例参数 `2 × 2 × 0.8 µm³` 仅用于数值对照，没有生物学标定。

每格的权重按其与作用范围的重叠体积计算，归一化后总和为 `1`。采样输出这些格的加权平均浓度；沉积把菌体摄取的 `molecule/s` 按同样的权重分给各格，再由格点体积换成 `uM/s`。作用范围碰到场地边界时只使用场地内的部分并重新归一化。这个规则不会产生“半个格点”：格点仍是完整的，只有分到该格的物质通量是一个比例。

在仓库根目录运行：

```powershell
$env:PYTHONPATH='src'
python examples/runtime/compare_spatial_coupling.py --geometry thin_layer
python examples/runtime/compare_spatial_coupling.py --geometry volume
```

示例让一个菌体在 `x=5 µm` 的格线两侧取 `4.99`、`5.00`、`5.01 µm` 三个位置，每次首秒摄取 `1000` 个分子。最近格点方法在格线处把全部摄取突然换到另一格；固定物理范围方法在薄层中分别分配约 `505/495`、`500/500`、`495/505` 个分子。3D 菌体同时位于三个格面时，八个格点各分到约 `125` 个分子。记录的场损失与菌体摄取相等，浮点舍入误差远小于一个分子。

这一步改善了跨格连续性并保持总量。后续已在固定物理场地和作用范围下完成[格距对照](fixed-support-refinement.md)，并接入[基础扩散](no-flux-diffusion.md)。**当前作用范围还未证明科学适用性**：这个跨格例子没有扩散、真实菌体形状、表面通量或经过测量的作用范围；菌体位置仍固定。后续需检验移动菌体时的数值行为。
