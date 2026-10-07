# N4 科学语义复核

2026-10-02。复核现有科学执行器与模板；本轮没有改动模型方程、有效参数默认值、执行时序或正在运行的任务。MCP 新增 v2 参数合同，去除未参与计算的 EI 参数；碰撞内核优化固定三维叉乘。依据为当前代码、来源锁和 [N3 机制审查](n3-mechanisms.md)，没有新增未经核查的物种能力或实验参数。

## MCP 与 PTS 的场

MCP 模板的 `ligand` 是独立有限扩散场，只向 MWC 感知输入浓度，没有 ligand 摄取结算。`nutrient` 使用均匀恒浓度 reservoir；每个实际摄取量进入外部补给账。因此在本案例中背景养分可持续供给、感知物不作为养分消耗。

模板复用 `pts.capacity_rebuilt` 与 `uptake.pts_request` 描述背景养分的容量受限饱和转运，但没有连接 `signal.pts_accepted`，不包含 PTS 感知对运动的反馈。复用转运模块不能理解为 MCP 通过营养通量感知 ligand。新增因果对照将营养转运设为零；其 MCP activity、adaptation、CheY-P、motor bias、运动轨迹及 ligand 场逐步完全相同，补给量相应归零。该对照没有 growth/health 模块；带生理耦合的图不能据此推断营养对运动永远无间接影响。

PTS 模板使用同一个 `nutrient` 场供采样、有限库存扣除和感知。实际接受通量驱动 EI/CheA/CheY，未接受请求不能作为摄取信号。材料降解与有限源直接释放到该场，扩散与摄取共同改变分布；不存在维持梯度的额外池或预留份额。既有零背景、零独立源、接触酶释放对照仍覆盖自然梯度和当量账。

## 随机数与 signal/motor

`signal.*` 的 ODE、低通适应与 Hill bias 是给定输入和状态后的确定性计算。bias 决定 hazard，事件时间和转向由运动机制抽样。相同 seed 可重现轨迹不等于没有随机过程。

随机能力由 `random_streams.RandomStreams` 提供，版本 `pcg64-sha256-key-v1`。seed 和 node/population/stable-cell/purpose 确定 PCG64 子流。运动分别使用 `run_hazard` 与 `tumble_direction`；health 使用 `death`；分裂使用 `division_fraction`。这些职责现已写入三个使用者的 Catalog mathematics assumptions。保留独立子流服务，没有增加可误接共享流的图输入。完整 RNG 状态随 checkpoint 保存；候选步失败不提交抽样。

新增完整图对照验证：插入无关随机抽样不改变运动轨迹；改变 seed 可改变轨迹，同时恒定 motor bias 完全不变。已有 hazard 测试继续覆盖剩余时钟、变率、事件边界和分段推进。

## 尺度和参数出处

| 量 | 当前模板值 | 可解释范围 |
| --- | --- | --- |
| 菌体总长/直径 | 2 / 0.8 µm | 总长包含两端；constructed，未标定具体菌株 |
| run 速度 | 5 µm/s | constructed；tumble dwell 与碰撞会降低位移/时间，不应当作实测群体速度 |
| 物理域 | 200×100×2 µm | thin layer；连续菌体几何与场网格分别表示 |
| 网格 | 20×10×1，格距 10×10×2 µm | 200 个体素，XY 格距为菌长的 5 倍；不代表已收敛的推荐精度 |
| 初始均值/梯度/扩散率 | 1 µM / 0.008 µM/µm / 1 µm²/s | constructed；梯度仅初始化，随后自由扩散 |

场采样与库存交换采用当前体素规则；细胞不占据一个“像素”。调细格距时必须同步网格数以保持物理域不变，并重新比较时间步、空间步和所关心观测量。既有 10/5/2.5 µm 比较未建立普遍单调收敛阶，A 的时间步敏感性仍可见，详见 [多 seed 比较](n3-comparison.md)。不能为了使长任务更快而直接放大 dt 或修改生物参数。

N3 来源记录区分 `source-derived` 与 `constructed`；参数从旧代码迁入并不等于真实菌株标定。当前模板不是经实验确认的 E. coli 或 B. subtilis 官方菌株库。

## MCP v2 参数修订与兼容

旧 MCP v1 声明复用了 `SIGNAL_PARAMETERS`，暴露四个不参与 MCP 计算的 EI 参数。现注册 `signal.mcp_adaptation@2.0.0`，只保留实际读取的 MWC、CheA/CheY、motor 与初始化参数，新增独立 `CheYParameters` 数据结构。PTS 的 EI 参数与执行完全保留。新模板及两份随包示例使用 MCP v2，误加 EI 参数会被拒绝。

MCP v1 仍注册且可运行，标注 legacy v1 和 EI 无效参数说明；保持旧参数校验规则。旧项目不会静默重写。显式迁移返回项目副本和每个节点移除参数的原值/单位/来源报告：

```python
from friskoli_cad.engine.chemotaxis_templates import migrate_mcp_parameters
new_project, changes = migrate_mcp_parameters(old_project)
```

迁移不改变节点 ID、边、有效参数或随机流；必须从新项目建立新的运行和版本锁。旧运行输入、结果、checkpoint 均不原地修改。新旧版本完整短轨迹和各自 checkpoint 恢复有专门对照；checkpoint 的实现锁仍严格检查，不能据此声称修改前任意旧安装的 checkpoint 可跨代码版本恢复。

## 12 小时与耗尽边界

12 h = 43200 s；dt=.05 s 需要 864000 个数值步。保存帧间隔与数值步是不同设置，稀疏保存不改变积分精度。现有 10/30 s 数值对照和短程 checkpoint 回归不能支持“12 小时生物学预测已验证”的结论。

PTS A/B 基础模板没有 growth/health/death；有限养分耗尽后摄取归零、EI 继续依其速率松弛，菌体不会被程序自动判为饿死，固定 run 速度也不会自动归零。新增空库存对照验证这一点。需要营养、表达负担、健康和死亡时必须显式接入相关机制；现有 life 模板的加速演示生长参数也没有长期实验标定。死亡移除剩余胞内可用养分到 removed-residual 账，不自动回流为外部养分。耗尽源停止释放；全死状态仍可推进场。不存在完整碳、能量、维持代谢或膜物料预算。

当前负载下 PTS-A 8 菌、200 体素、dt=.05 s 的直接 `simulation.step` 100 步为 9.7197195 s（.0971972 s/步）。包含观察器和 Frame 验证，不含任务序列化、fsync 或 HTTP，且与短回归并行；只用于识别服务额外开销，不能作为空闲机器 benchmark 或长时保证。

30 步 cProfile 定位到 `guard_motion` 累计 2.955 s / `step` 4.916 s，其中通用 `np.cross` 1.400 s，Frame 校验 .776 s。将段距离内部叉乘改为固定三维分量公式，保留同一浮点运算顺序、候选距离、阈值与异常校验。315 个随机/退化/多尺度几何和 12 步完整 checkpoint 与原 `np.cross` 实现精确相等；28 项碰撞测试通过。

同一进程每组预热 5 步、计时 100 步，按原/新/新/原顺序分别为 6.1644 / 5.2723 / 4.4835 / 7.2457 s，平均下降约 27.3%。机器仍有其他工作，时间波动明显；没有跳过任何碰撞或 Frame 校验，不能把这个结果外推为所有规模固定提升或实时 12 小时能力。

## 验证

新增 `tests/test_n4_science_semantics.py` 三项因果/边界回归。该文件前两项加 scientific_controls、n3_science、random_streams、hazard_walk、chemotaxis_runtime、chemotaxis_checkpoint 共 **79 passed，13 subtests passed**（166.12 s）。新增第三项后，三个新回归与 delivery 的六套模板一致性、同场自然梯度测试共 **10 passed**（43.13 s）；两批有重叠，不相加为独立测试数。测试使用项目 `.venv/Scripts/python.exe` 和 `PYTHONPATH=src`。初次系统 Python/未设置源码路径的调用在 collection 阶段失败，未当作科学测试结果。未运行 12 小时生物时间模拟，未新增真实菌株参数标定。

随后新增 MCP v1/v2 迁移与无效 EI 拒绝两项，当前该文件共五项。新版本参数合同、旧版轨迹/恢复、纯科学公式、上下坡符号和 delivery 非 worker 检查共 **41 passed，7 deselected**（37.15 s）。碰撞优化独立 **28 passed**（6.03 s）。这些专项与前面的批次有重叠；临时目录清理产生 Windows `WinError 145` 警告，未影响测试通过。

更新后的 MCP 模板另通过真实异步 worker 的冻结计划、场与帧结果对照 **1 passed**（6.24 s）；测试独立启动自己的临时 worker，没有重启或修改 `localhost:8876` 的运行。
