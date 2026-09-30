# N3 有限均匀底物与 PTS 执行协议

日期：2026-09-30。执行规则为 `conservative-pts-bulk-v1`。实现位置：
`engine/profiles.py`、`pts_modules.py`、`pts_runtime.py` 与 `settlement.py`。
本阶段覆盖迁入计划 M0–M3；尚未完成整套趋化模型。

## 版本与能力协商

| 内容 | 新版本 | 兼容关系 |
| --- | --- | --- |
| Project | `0.3.0` | 必填 `execution_profile`；旧 0.1/0.2 不改变执行语义 |
| Catalog | `0.2.0` | 仅描述新 profile 的 8 类模块；旧目录仍为 0.1 |
| Module / Graph / Run / Frame | 原有版本 | 沿用端口、群组、单位、边时序与逐菌体帧 |
| Task / CompiledPlan | `0.2.0` | 新 profile 单独协商；旧任务合同、Schema 和队列记录继续支持 |
| 通用传输错误 | `0.1.0` | 包括无法解析或尚不能识别 profile 的错误；新 Error Schema 显式引用旧定义 |

`GET /api/capabilities` 的 `task` 保留 legacy 任务能力，新能力由
`task_profiles["conservative-pts-bulk-v1"]` 提供。按实际项目选择合同与版本锁，不能把 legacy
目录的锁交给新 profile。目录通过 `GET /api/catalog?execution_profile=conservative-pts-bulk-v1`
读取；未知、空、重复或多余查询参数拒绝。N3 示例是 `GET /api/examples/pts-bulk`。

Task 生命周期、幂等、取消、事件游标、完整块公布和恢复查询沿用 [N2 合同](task-contract.md)。
新格式见 [Task 0.2 Schema](../../src/friskoli_cad/protocol/schemas/task-v0.2.schema.json)；
混合服务接口见 [OpenAPI](tasks-openapi-v0.2.json)。任务失败保留已公布帧和已知数值诊断；
原始未知异常仍不泄露内部异常栈。

## 模块与数据连接

| 模块 ID | 职责 | 状态归属 |
| --- | --- | --- |
| `bulk.finite_uniform` | 有限均匀底物及 strict / proportional 分配策略 | 每物种唯一环境库存 owner |
| `pts.capsule_area` | 从含端帽总长和直径读取表面积 | 无状态 |
| `pts.capacity_rebuilt` | A 来源的膜占据容量规则 | 无状态 |
| `pts.capacity_simplified` | B 来源的参考面积容量规则 | 无状态 |
| `bulk.sample_uniform` | 显式采样全域均匀浓度 | 无状态、全库支持，不代表局部梯度 |
| `uptake.pts_request` | 根据浓度与功能性总 copies 计算请求 | 无状态，不扣底物 |
| `uptake.bulk_settlement` | 参与共享库存结算、记录实际摄取和胞内累计 | 每群组/物种唯一胞内 owner |
| `signal.pts_accepted` | 用实际摄取推进 EI/CheA/CheY，读取 motor bias | 每节点显式信号初态 |

所有模块版本均为 `1.0.0`，成熟度为 exploratory，声明包含名称、数学式、符号、实现和测试引用。
参数 ID 使用小写，单位单独声明。例如 `ei_total_um` 的单位是 `uM`，经明确 adapter 传给
科学函数的 `ei_total_uM`；脚手架不修改全局 ID 规则。`ascf_area_um2` 的单位为 `um^2/molecule`。

不同群组的 cell 端口不能相连；物种、quantity、shape 或单位不匹配均拒绝。采样与结算必须指向
同一个 bulk owner。多个菌群通过各自结算节点共享环境库存，不连接彼此的菌体数组。

## 一步如何执行

1. 读取同一时间步起点库存与已提交状态。bulk 的浓度/库存边必须为 `previous_step`，其余边必须为 `same_step`。
2. 计算固定几何、来源明确的容量、全域浓度采样和每菌请求。
3. 收集所有指向同一 bulk 的请求，统一结算一次。strict 在不足时拒绝整步；proportional 显式按请求比例限制。
4. 用同一个接受量扣库存、增加胞内量；以接受量除以 dt 的通量推进信号。
5. 检查状态、物质误差和结果帧，成功后统一提交库存、胞内量、信号、帧和时钟。任何失败均不提交本步。

CompiledPlan 的 `nodes` 表示类型依赖拓扑；额外必填 `schedule` 保存准备节点、按库存组织的参与者、
信号节点及原子提交约定。运行器和计划生成器使用同一个 `pts_schedule()`，任务保存其真实摘要。
输入、模块实现、科学代码、来源锁与证据数据同时进入执行指纹。

所有体素共享一个均匀库存，体素数不增加数值状态数组；domain 仍用于体积、位置与几何检查。
没有运动、扩散、接触、纤维、外部日程、生长、分裂或死亡，禁止将这些模块混入这个 profile。

## 数值误差与科学边界

结算 reference 使用精确有理数计算请求比例，再转换到 float64，并记录精确残差与舍入界。
CAD 进一步按本步转移量 T 检查环境扣除及每菌胞内增量，预算为 `1e-10 × |T| + 8 × ulp(T)`。
不能用巨大总库存作相对容差，也不能通过截零或重新归一化修改账目。超预算拒绝整步；该数值预算
并非生物实验误差或参数有效范围。保存输出帧的间隔不参与数值机制。

EI 使用冻结接受通量下的解析更新；CheY 更新采用步末 CheA 冻结的串联近似，完整耦合为一阶。
稳态、极限、来源对照及步长细化见[科学说明](../science/pts-minimal.md)，库存 API 见
[结算说明](../science/settlement.md)。原论文支持机制方向，不会自动校准本队的具体方程或构建体参数。

当前结果依然使用逐菌体帧，环境库存可在运行器的只读 `inventory` 查询；没有把均匀库绘制成梯度热图。
motor bias 只是信号读数，还没有驱动运动。暂停续算、随机流及完整科学链另行验收。
