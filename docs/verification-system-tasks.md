# 长时 Task 与状态恢复验证（2026-10-03）

## 执行事实

`tests/verify_12h_task.py` 已用真实 Task worker 完成 43,200 × 1 s，最终时间 43,200.0 s。输入从一开始就是独立的基础机制用例：单 voxel、单 cell、有限营养库存、D=0、0.01 µm/s、恒定 motor bias、saturating uptake、reserve 与 starvation survival。没有修改 PTS-A 的时间步长。

- 初始化边界显式暂停，创建 child 后继续；parent=`run_184e184cca44417eaa2ea061a70991b8`，child=`run_758fdaee2f7f42d49e336abba69d18dd`。
- 该用例 `start_step=0`。非零步暂停、服务重开后继续，由 80 步测试另行覆盖，并与连续计算逐字比较最终 cell frame 和 RNG 状态。
- 原 60 s worker 壁钟预算内自动 checkpoint 轮换 3 次，生成 16 chunks，最终 checkpoint 35,784 bytes。
- 场数组有限且非负，最终 checkpoint 成功完整重读。执行日志约 178.484 s，仅为该次日志，未用来推断加速倍数。
- 原始输入、隔离源码、SQLite 和证据在本机临时目录 `friskoli-foundation-12h-20261003/evidence/verification.json`。证据 SHA-256：`53d2516ddd6e82aa31b22d1115a505e42b3cb46b9e331989fdecd742b98dbec1`。

另一次大数组验证使用 chemotaxis-pts-a、1 cell、192³=7,077,888 voxels。在初始化已提交状态保存并完整重读 176,996,599 bytes 的 binary checkpoint，超过旧 JSON 64 MiB 限制；重读的 grid 和 step=0 一致。未执行该大场的数值 step。

大数组证据目录为本机临时目录 `friskoli-large-checkpoint-20261003`，含 `verification.json` 与 `large.zip`。ZIP SHA-256：`498de68dcb1e555deae7274affb9d8adb260dc5642c74aa6f7b2662233673b03`。约 4.359 s 为保存和重读的日志时间。

## 自动化覆盖

`tests/test_task_longrun.py` 检查 binary 数组 hash、shape 与预算，完整状态恢复后的 RNG 连续性，真实 spawn 暂停/继续，服务重开后的非零步恢复，Task/Manifest/ChunkBody 0.6 Schema，432 万步准入，同域迁移库存与 RNG，以及 modular Task 的 planner、checkpoint、重划后继续。边界事件测试验证 child 首帧继承同一边界事件且下一帧不重复。

`tests/test_task_longrun_http.py` 检查 raw ZIP 下载、上传、导入、损坏输入拒绝、暂停、数组段读取与结果索引分页。相关旧 Task、field preview、final fields、spatial/chemotaxis checkpoint、独立 checkpoint CLI、Design/delivery/evaluation 测试在包含 src、tests、docs 的隔离副本运行，避免其他并行源码修改触发正确的 implementation lock 拒绝。

上述 12 个测试文件整组结果：**202 passed，47 subtests passed**，227.05 s。唯一测试 warning 来自故意构造重复 ZIP 条目的负例。隔离目录为本机临时目录 `friskoli-system-validation-final-20261003`，命令如下（在该目录设置 `PYTHONPATH=src`）：

```text
python -m pytest tests/test_task_longrun.py tests/test_task_longrun_http.py tests/test_task_service.py tests/test_task_http.py tests/test_task_field_preview.py tests/test_task_final_fields.py tests/test_spatial_checkpoint.py tests/test_chemotaxis_checkpoint.py tests/test_checkpoint_io.py tests/test_design.py tests/test_design_delivery.py tests/test_design_evaluation.py -q
```

随后补充 module-state 未声明迁移拒绝、真实 service 迁移（资源不足/错误 preview hash 拒绝后仍能成功创建子段），并更新模块内存预算后，新 longrun 与 HTTP 两个文件再次隔离运行：**12 passed**，98.87 s。

## 迁移支持范围

已支持同一物理域内改变网格：浓度按体积平均映射；每格 `molecule` amount 按体素体积比转换，分别检查库存守恒。新模块 state 必须显式声明 `on_migration`；保留状态只接受 `copy`，网格 scalar 只接受 `conservative_regrid` 且单位/quantity 必须有已实现映射。历史 profile 保留明确的 concentration adapter。

未知单位、field.vector/field.tensor、缺少声明或要求 module 自定义 mapper 的状态会拒绝。物理域变化、细胞/来源位置转换、自定义 domain/cell/source migration provider 尚未实现。障碍重划若吞库存同样拒绝。预演及发布失败不修改父段。

旧 profile 在 step=0 严格校验目标初始化。若保守重划结果不同于目标网格的显式初始化（例如梯度重新采样），迁移拒绝；需要实际执行至少一步后暂停再迁移。

## 未作出的结论

未执行 PTS-A `.01 s` 的完整 432 万步运行；432 万步准入测试不能替代这项执行。未完成 256³ 长时数值验收。上述数值机制验证没有进行菌株实验标定，也不能证明小时级生物预测准确。
