# 归档资源

这里保存已经退出当前 `modular-spatial-v1` 执行路径的历史资源。归档文件用于追溯和比较，不会被打包为当前运行器的入口。

## legacy-runtime

- `engine/degradation.py`：旧的实体底物接触降解提案；当前由 `engine/science_adapters.py` 的 `contact_degradation` 模块执行。
- `engine/settlement.py`：旧的固定支持区 exact-rational 结算；当前局部场在 `engine/local_fields.py` 内按采样体素结算。
- `engine/spatial.py`：旧的固定物理盒支持重叠权重；当前使用 `field.sample_local` 的最近体素采样。
- `science/materials.py`：旧的直接水解函数；当前反应公式位于 `science/processes.py` 并由统一适配器接入。
- `engine/random_walk.py`：旧的独立无偏 run/tumble 推进；当前运动由 `engine/hazard_walk.py` 统一推进，共享的各向同性方向和预算异常保留在 `engine/walk_primitives.py`。
- `src/friskoli_cad/examples/center_pts_*.project.json`：旧的中心 PTS 参数扫描；当前预设位于仓库根目录的 `src/friskoli_cad/examples/`，使用 `*_small_strong` 项目。

配套的旧测试保存在 `archive/legacy-runtime/tests/`，旧说明在 `docs/archive/legacy-runtime/`。它们记录历史实现的行为，不代表当前模块合同或科学结论。
