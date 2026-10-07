# 归档资源

这里保存已经退出当前 `modular-spatial-v1` 执行路径的历史资源。归档文件用于追溯和比较，不会被打包为当前运行器的入口。

## legacy-runtime

### 整文件退休的实现

- `engine/degradation.py`：旧的实体底物接触降解提案；当前由 `engine/science_adapters.py` 的 `contact_degradation` 模块执行。
- `engine/settlement.py`：旧的固定支持区 exact-rational 结算；当前局部场在 `engine/local_fields.py` 内按采样体素结算。
- `engine/spatial.py`：旧的固定物理盒支持重叠权重；当前使用 `field.sample_local` 的最近体素采样。
- `engine/random_walk.py`：旧的独立无偏 run/tumble 推进；当前运动由 `engine/hazard_walk.py` 统一推进，共享的各向同性方向和预算异常保留在 `engine/walk_primitives.py`。
- `science/materials.py`：旧的直接水解函数；当前反应公式位于 `science/processes.py` 并由统一适配器接入。
- `src/friskoli_cad/examples/center_pts_*.project.json`：旧的中心 PTS 参数扫描；当前预设位于仓库根目录的 `src/friskoli_cad/examples/`，使用 `*_small_strong` 项目。

### 从现行模块中拆出的退休函数

这些函数原先留在现行源文件里，但自 P3（`b931a2b`，删除旧路径并重搭预设）起已无调用方，现按追溯需要单独存放：

- `engine/motion_center_point.py`：`turn_about_z` 与 `reflect_in_box`。原先服务于 `motion.periodic_turn` 和 `motion.reflective_run` 的固定速度直行与盒面反射；当前运动由积分风险率行走推进，几何约束由 `engine/collision.py` 负责，边界反射不再参与。`engine/motion.py` 保留了仍在使用的 `heading_from_orientation` 和 `orientation_after_heading`。
- `science/retired_lifecycle_forms.py`：旧的"读入状态返回下一状态"形式，来自三个现行模块。
  - 原 `science/physiology.py`：`advance_growth`、`advance_copy_number`、`rebuilt_expression_rate`、`advance_simplified_health`、`advance_rebuilt_health`、`surface_adder_delta`、`division_split`、`division_split_bounds`，以及只被它们使用的 `HealthReadout`、`RebuiltHealthParameters`、`DivisionReadout` 和常数 `MOLECULES_PER_UM_UM3`。
  - 原 `science/survival.py`：`advance_reserve`（`ReserveReadout` 与 `advance_starvation` 仍在使用，保留在原模块）。
  - 原 `science/chemotaxis.py`：`tumble_hazard`。

  同一批公式现在由 `science/processes.py` 提供纯函数、`engine/science_adapters.py` 包装成注册模块提案，状态归属移交 `engine/modular_runtime.py` 的原子提交。

配套的旧测试保存在 `archive/legacy-runtime/tests/`，旧说明在 `docs/archive/legacy-runtime/`。它们记录历史实现的行为，不代表当前模块合同或科学结论。

## docs/archive

`docs/archive/` 保存当前文档树之外的说明。除原有的 `design/`、`legacy-protocols/`、`legacy-runtime/`、`planning/`、`pre-first-release/`、`verification/` 之外：

- `engine-stages/`：阶段 3a–3n 的引擎与数值专题记录（图编译器、无扩散摄取、几何模式、环境替换、格点诊断、守恒重划、基础扩散、运动时序、外部输入日程、胶囊尺寸、逐帧几何、adder 分裂）。这些记录描述统一执行规范之前的实现，正文里的 `examples/runtime/` 路径随 P3 一并删除，不再可解析。
- `science/`：N3/N4/N5 阶段的科学记录及其机器可读验收数据（原 `docs/science/` 下的五个阶段文档与 `verification/`）。当前科学说明仍在 `docs/science/`。

当前文档入口以 [docs/README.md](../docs/README.md) 为准。
