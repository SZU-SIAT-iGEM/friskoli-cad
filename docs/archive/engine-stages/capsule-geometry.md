# 胶囊形菌体初始尺寸 · 阶段 3l

项目快照 `0.2.0` 可在菌体组中按 `ids` 顺序填写 `initial_geometry`。每项是一个胶囊形尺寸声明，或 `null` 表示尺寸未知；整个字段缺失也表示这组菌体的尺寸都未知。旧 `0.1.0` 项目继续读取，尺寸按未知处理。`0.1.0` 项目不能直接写入新字段，需先把 `project_version` 改为 `0.2.0`。

```json
"initial_geometry": [
  {
    "shape": "capsule",
    "length_um": 2.0,
    "diameter_um": 0.8,
    "provenance": {"kind": "example", "reference": "illustrative dimensions only"}
  }
]
```

这两个数仅是格式示例，不能当作项目菌株的实测尺寸。`length_um` 是沿菌体局部 `+X` 长轴从一端到另一端的总长，`diameter_um` 是横向直径；要求 `length_um ≥ diameter_um > 0`。运行器按菌体 ID 保留各自尺寸，移动位置和转向不会重设它。项目里的 `provenance` 留存初始尺寸来源。

若尺寸已知，薄层运行会检查当前姿态的 Z 向占据高度。胶囊轴方向在 Z 上的分量为 `a_z` 时，高度是 `diameter_um + (length_um − diameter_um) × |a_z|`。菌体中心与上下边界都须留有正间距；过厚或倾斜后放不下的声明会在创建运行时拒绝。尺寸未知时不执行这项检查，也不宣称菌体一定能放进薄层。薄层原有的物质体积换算没有改变。

初始几何用于保存尺寸和薄层容纳检查。[逐帧几何 `0.2.0`](growth-frames.md)与演示性轴向生长现已接入；旧项目仍使用原来的结果帧。摄取、扩散、采样范围与边界反射仍采用原有实现；运动反射仍按**中心点**计算，尚未使用胶囊外形处理 XY 墙面、3D 边界或菌体之间的碰撞。

[项目读取与薄层检查测试](../tests/test_project_schedule.py)覆盖旧格式、已知与未知尺寸、胶囊长宽约束、平放与倾斜时的薄层容纳情况；[移动测试](../tests/test_motion.py)检查尺寸在位置更新后保留。

在仓库根目录运行[小示例](../examples/runtime/catalog_capsule_demo.py)：

```powershell
$env:PYTHONPATH='src'
python examples/runtime/catalog_capsule_demo.py
```

示例从旧快照生成一个 `0.2.0` 项目：`oxygen` 保留在物种目录，但移除氧场和输入节点；菌体获得标为示例值的胶囊尺寸。输出可直接比较 `registered_species` 与 `active_concentration_fields`。
