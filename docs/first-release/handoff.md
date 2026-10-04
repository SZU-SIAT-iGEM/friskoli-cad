# 接手记录

2026-10-04：用户要求“提交就行，后面有人接手”，本轮停止新增长时验收，提交已完成的实现和证据。

工作目录为 `D:\Wu Shangru\Documents\工作区\friskoli-cad-unify-profile`，分支 `chore/unify-profile`。原 `friskoli-cad` checkout 未修改。当前没有 Git remote；本轮只做本地提交。

P0 `76d0a68` 固定规范与清单；P1 `6ac226a` 提取公共实现；P2 `f31832b` 接通观测、Task/Design 和原生交付；P3 `b931a2b` 删除旧执行链与重搭案例。P4 为本文件所在的提交，修正微量物质结算、事务与类型问题，完善显示和测试，保存中心材料研究结果，并把芯片梯度改为水平短边 X 轴（400 µm）。

当前唯一范围见 [support.md](support.md)，实际验收见 [acceptance.md](acceptance.md)。安装与独立用户流程见 [install-and-use.md](install-and-use.md)。当前 wheel 位于 `artifacts/`，SHA-256 见制品清单。按用户要求，旧 Y 轴芯片数据、图表、截图、原生回放和 Wiki 包均移出本次提交；不保留旧方向的研究结论。不要从旧 profile 或旧输入继续工作。

按用户后续要求，旧 Y 轴数据及衍生文件已集中移到工作区 `D:\Wu Shangru\Documents\工作区\chip-y-待删除`，由用户删除。该文件夹位于仓库外，不进入提交；当前仓库不再保留这些旧运行结果。

## 剩余工作顺序

1. 重新运行当前 X 轴芯片的 8 seeds × 梯度/对照 × 300 s。浓度场、X 两侧边界、观测区域和统计脚本已同步修改；当前提交只完成方向与一步数值核对。
2. 在最终安装版上跑必要 dt/dx 敏感性：先选中心材料代表场景，区分离散误差和随机轨迹差异；有新问题才扩展扫描。当前中心研究的 4-seed 区间都跨零，不能宣称降解收益。
3. 用最终 wheel 的同一冻结输入、seed 和配置，核对脚本、Task、UI 的所有指标。
4. 完成实际 Design UI 的生成、批量多 seed、比较、导出与重开；请未参与开发的人按操作清单留文字记录。自动 UI 检查不能代替非作者验收。
5. 对约定 400×800×200 µm、X 轴梯度、20 µm 网格、200 细胞、dt=0.1 s、k=0.3，至少做三次串行 G8 测量：记录 120 s 曲线出现、300 s 完成/包可读、引擎/任务/UI 开销及峰值内存。
6. 用当前 X 轴配置导出原生包和静态 Wiki，确认关闭原服务后可完整播放；补实体设备验证。之后才评价是否通过整套发布门槛。

复现研究命令：

```powershell
python -m pip install -e ".[standards]"
python research/chip-benchmark/chip_benchmark.py --duration 300 --seeds 8 --workers 1 --out runs/chip-x-axis-main
python research/first-release/center_study.py --duration 120 --seeds 4 --out runs/first-release-center
python research/first-release/export_handoff.py --center runs/first-release-center --chip runs/chip-x-axis-main --out runs/recreated-evidence
python -m pytest tests -q
node --test tests/*.test.mjs
```

`center_study.py` 还支持 `--scale`、`--mechanism`、`--dt` 和 `--spacing`，改动后的配置独立注明。当前扫描文件保留完整项目和 project SHA；不以旧 profile 轨迹作为正确性目标。最终原生 Task 必须携带它自己的实现锁。

本轮临时端口 8768/8769 的服务已停止，研究进程已完成。没有留下继续推进的后台任务。A 的完整表面糖池/QSSA/黏附、B 的完整来源机制、实验标定和视觉训练仍属于后续科学任务。
