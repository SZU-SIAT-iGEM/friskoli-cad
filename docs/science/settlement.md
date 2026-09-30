# 有限库存结算 reference

`engine/settlement.py` 是静止支持上的不可变 propose/commit 参考实现。它只处理一个 owner、一个物种的有限库存及摄取请求；不包含扩散、运动、生长或信号方程。各物种分别创建快照，不能把同为 molecule 的不同物种合并结算。

`InventorySnapshot(species, reservoir_ids, amounts, revision=0, owner_id="bulk")` 保存步起点库存。`propose_settlement` 要求显式提供 `cell_ids`、`support_cell_ids`、`support_weights`、`requested_flux` 和 `dt_s`，默认 `policy="strict"`。请求通量单位 molecule/s；请求量为 binary64 的 `requested_flux * dt_s`；库存、接受量及账本单位 molecule。`accepted_flux = accepted_amount / dt_s`。计数是连续平均值，不声明整数分子抽样。

支持矩阵是 cell × reservoir。行对应 cell stable ID，列对应快照 reservoir ID。两个 cell ID 序列必须按行完全一致，不能仅凭长度匹配；重复、空白字符串 ID、非字符串/整数 ID 拒绝。每行非负且归一到 1；全零行即使请求为零也拒绝。空细胞集合使用空行矩阵，可保留库存。均匀 bulk 用单个 reservoir，所有支持权重为 1。

支持行的归一误差限为各输入 binary64 权重半个 ULP 之和，再加 1 的一个 ULP；超过则拒绝。通过后，以权重的精确有理数总和定义归一支持，保证一个细胞的请求只分配一次。这仅定义采样支持，不对库存或质量账作事后归一化。

每个 reservoir 先汇总全体请求，再统一分配：

- `strict`：任一列请求超过起点库存，整步抛 `SettlementError`。短缺检查没有 epsilon。
- `proportional`：每列独立乘以 `min(1, inventory / total_request)`，不将一列未满足的请求移到其他列。10 molecule 库存面对 8、12 molecule 请求，分别接受 4、6，剩余 0。

中间需求和分配使用 `Fraction`，因此细胞顺序不影响结果；每份接受量向下舍入到 binary64，避免舍入造成超库存或超请求。剩余库存由原库存减去实际逐支持接受量计算，没有负值截零，也没有全局缩放。比例分配的向下舍入可能留下极小的可用库存。

`SettlementProposal` 返回冻结 tuple 数据，包括请求量、接受量、接受通量、逐支持接受量、前后快照和账本，`accepted_by_cell` 返回只读 stable-ID 映射。调用者输入数组先复制为 tuple，后续修改不会改变提案。

账本从实际前后库存及实际接受量独立计算精确 binary64 残差。每列允许的残差不超过**精确剩余库存向 binary64 最近舍入的半个 ULP**，没有固定 1 molecule 下限。细胞总账再计入每个接受量行和最多半个 ULP 的舍入；残差和误差限全部输出，测试独立重算；`conservation_residual_exact` 与 `total_conservation_residual_exact` 另外用有理数字符串完整保存精确残差，避免诊断本身再次舍入。大库存减微小摄取而使扣除完全消失的步骤另外明确拒绝，不以 ULP 预算放行。舍入预算是表示误差上界，不是实验精度保证；当实际转移量接近库存的 ULP，调用方仍需检查报告的相对转移误差并选择合适的尺度或时间步。过小的请求乘 dt 下溢为零、乘积/总量溢出、非法 dt、负值、非有限输入也拒绝。账本不能证明完整碳或能量守恒，只核查本接口中声明物种的转移。

`commit_settlement(current, proposal)` 检查完整起点快照、owner、物种及 revision，并重算提案以拒绝篡改，然后返回 revision + 1 的新快照。成功后再次对新快照提交旧提案会拒绝。纯函数允许从旧不可变快照显式分支；**唯一库存 owner 必须由运行器负责，并由运行器将库存、胞内量、信号等一起原子替换**。这个函数本身不提供线程锁、全图事务或持久化 checkpoint。任一步失败均不会修改输入快照或数组。

构造回归见 `tests/test_settlement.py`：手算短缺、默认整步拒绝、cell/reservoir permutation、跨支持独立列、零与耗尽、dt、稳定 ID 对齐、非法输入、上溢/下溢、独立物种、原数组不变、陈旧/篡改提交以及从 1e-100 到 1e100 的独立质量审计。此实现用于正确性参照；`Fraction` 不作为大规模网格运行的性能承诺。
