# 协议与执行设计

状态：2026-09-29 目标设计，2026-10-01 补充实施状态。N1 注册目录、N2 异步任务、N3 固定种群 PTS、空间无偏基线与 M4 checkpoint 已有实现。本文保留的扩展字段和示例草案不能直接提交给当前 API；实际请求以已发布 Schema 和各 profile 合同为准。

2026-10-01 实施注记：M4 已提供[独立 checkpoint 文件与 CLI](../checkpoint-files.md)，外层 `checkpoint_file_version: 0.1.0` 包含冻结 Project 0.4 和内部 `spatial-checkpoint/v2`。它恢复独立空间运行；任务 pause/resume 与运行中环境迁移仍属于 N7。当前空间 profile 不含生长、分裂、死亡或外部 controls。

## 1. 协议族与兼容边界

| 合同族 | 当前基线 | 下一步设计 |
| --- | --- | --- |
| 模块/图 | `protocol_version: 0.1.0` | 类型作用域、数学说明、读写集、实体对齐、求解语义，需要独立的新合同版本 |
| Project | legacy `0.1.0/0.2.0`；固定 PTS `0.3.0`；空间 `0.4.0` | 设计目标、候选与更完整的机制/观测合同 |
| Catalog / Task | legacy `0.1.0`；固定 PTS `0.2.0`；空间 `0.3.0` | 按 profile 精确协商，不把新版本应用到所有旧项目 |
| Workspace | `workspace_format_version: 0.3.0`，兼容读取旧 `0.1/0.2` | 更完整的设计草稿与视图状态分离 |
| Local API | 顶层 `api_version: 0.2.0`；已实现独立任务合同 | 更完整的诊断集合与能力扩展；同步 replay 保留受限兼容 |
| Run metadata | 当前共享 `0.1.0` | 与已有独立 Task 合同继续分别版本化 |
| Frame/Replay/Result | Frame `0.1.0/0.2.0`、Replay/Result `0.1.0`；任务已有完整帧分块，空间 Task 0.3 含场与库存 | 二进制数组、增量帧等另立合同 |
| Checkpoint 文件 / 数值状态 | 文件 `0.1.0` / `spatial-checkpoint/v2`，仅空间 profile | 其他 profile 的状态合同与 N7 服务续算另行验收 |
| Package / Plugin | 尚无完整安装合同 | 清单、依赖、实现与数据版本分别声明 |
| Execution semantics | legacy、固定 PTS、空间三个独立 profile | 新机制明确积分策略，旧运行不自动改义 |

现有多个 Schema 使用 `additionalProperties: false`，所以新增可选 metadata 也需要新版本或外置 sidecar，不能直接塞入旧合同。兼容使用 reader/adapter；旧结果按旧语义播放，不重新计算。旧模块无法证明的信息标 unknown，不宣称支持碰撞、GPU 或 checkpoint。改变方程、离散时序、随机过程或继承规则时，升级相应实现/执行语义版本。

当前对应关系见[固定 PTS 合同](../protocol/pts-bulk-profile.md)、[空间合同](../protocol/spatial-profile.md)与[任务服务](../task-service.md)。空间 Task 0.3 已传输真实浓度场和有限对象库存，旧 Task 0.1/0.2 的输出约束保持不变。

## 2. 注册项合同

登记项共有 namespace、ID、version、类型、显示说明、schema 引用、来源和许可证。安装时解析依赖并生成 lock；打开项目不自动取最新版本。同一 ID/版本不能被其他包静默覆盖。

对象类型声明以下内容：

- `properties_schema`：单位、必需值、合法范围、示例、证据、默认值适用条件。
- `initializer`：从编辑对象到稳定 ID、初始状态和支持区域的后端转换。
- `graph_template`：自动生成的数据提供者与机制模板。
- `render`：受控 renderer key、拾取规则、尺度和标签；可显示不等于可求解。
- `commands`：定位、散布、启用、隐藏等能力及验证入口。
- `requires`：场、几何、机制、运行后端等依赖和支持的 domain 类型。

能力查询区分 declared、resolved、initializable、executable、renderable、explainable，并返回不可用原因。UI 仅在动作所需能力都成立时启用；遇到未知扩展可保留原始资料并显示占位，但不能参与求解。

## 3. 模块合同

| 类别 | 必须可说明的内容 |
| --- | --- |
| 身份与展示 | ID、版本、名称、分类、一句用途、帮助、科学成熟度 |
| 端口 | shape、quantity、unit、dtype、物种/实体集合、坐标、采样位置、时间角色、optional |
| 参数 | schema、单位、合法域、来源、适用条件、未知处理、是否允许候选覆盖 |
| 数学 | equation/algorithm、LaTeX、符号到参数/状态/端口映射、假设和适用域 |
| 数值 | 离散方法、精度、稳定条件、误差/收敛证据和随机过程 |
| 状态 | reads/writes、初值、唯一 owner、分裂/死亡/迁移/checkpoint 策略 |
| 执行 | initialize/evaluate/propose/commit、CPU/GPU 实现、规模估算方法 |
| 证据 | 文献、参数集、测试、源码标识、审核记录；作者声明和验证结论分开 |

`equivalence_claim` 只表示作者声明。界面显示 declared / tested / reviewed 及适用范围，不自动提升为数学等价证明。LaTeX 只渲染，不作为可执行代码；未来可研究受控数学 AST，但不作为首版前置。

以下为尚未发布模块的简化草案，完整正式 schema 还需补齐说明和证据字段：

```json
{
  "contract_draft": "cad-next/1",
  "id": "core.geometry.capsule_metrics",
  "version": "0.1.0",
  "kind": "derived_observable",
  "scope": "population",
  "inputs": {
    "geometry": {"shape": "cell.capsule", "quantity": "geometry", "unit": "um", "entity_set": "$population", "temporal": "state_at_step_start"}
  },
  "outputs": {
    "surface_area": {"shape": "cell.scalar", "quantity": "surface_area", "unit": "um^2", "entity_set": "$population", "temporal": "state_at_step_start"}
  },
  "math": {
    "kind": "equations",
    "latex": ["A=\\pi dL"],
    "symbols": {"A": "outputs.surface_area", "d": "inputs.geometry.diameter", "L": "inputs.geometry.total_length"},
    "assumptions": ["spherocylinder", "L>=d>0"]
  },
  "state_access": {"reads": ["cell.geometry"], "writes": []},
  "execution": {"supported_backends": ["cpu.numpy"], "determinism": "within_declared_numeric_tolerance"},
  "verification": {"status": "planned", "required_cases": ["sphere_limit", "positive_dimensions", "unknown_geometry"]}
}
```

## 4. 类型、单位和索引

在现有 scalar/vector/index/boolean 基础上明确 tensor shape，同时声明：

- 作用域：global、population、cell、grid、surface、event；
- entity set 和 ID 顺序：同组、明确子集或经映射的另一组；
- species ref：物种定义与项目实例分别引用；
- domain ref、grid revision、坐标单位与轴序；
- location：体素中心/面、菌体中心/表面、边界/观察区；
- temporal：时刻状态、区间平均速率、区间积分量或离散事件。

`molecule/s` 通量和 `molecule` 积分量是不同 quantity；浓度梯度和浓度时间变化同样分开。跨群组通过 aggregate/map/reindex，标量广播和单位转换通过可见节点，不做无记录自动转换。长度相同的数组不能据此认为实体一一对应。

JSON 保留规范 ASCII unit key（如 `um`、`uM`），展示可排为 µm、µM。单位登记包含维度和转换规则；同量纲不保证同语义。缺值采用 typed unknown/缺省状态，不能把 0、NaN 或文字塞进数组。需要未知值的模块必须报告；只观察的对象可保留未知。

## 5. 编译与检查

1. 读取包/文档，检查版本、大小、引用并保留原件。
2. 解析依赖，冻结 registry；此时不自动安装执行代码。
3. 展开空间对象、参数覆盖和受管理图模板，建立初始化与绑定。
4. 检查单位、物种、实体、坐标/网格、端口和参数证据。
5. 检查状态 owner、合并器、same-step 图、previous-step 初值和生命周期策略。
6. 检查物理/几何约束、模型适用域；unknown 不冒充 pass。
7. 选择求解语义、精度、后端及输出计划，估计资源；不偷偷降低分辨率。
8. 生成不可变 CompiledPlan 和全部独立诊断。

Same-step 依赖须可排序；代数环仅允许存在于声明收敛策略的复合求解器内。方程组可作为单一模块或 solver group 的内部图。

模块声明已实现的 CPU/GPU backend 及约束，由全图 planner 决定实例和数据驻留。依赖、转移成本、精度、随机性和内存统筹；模块不能各自抢 GPU 并声称互不影响。初版 CPU 为参考执行路线。

## 6. 可定位诊断

```json
{
  "code": "geometry.population.outside_domain",
  "severity": "error",
  "check_state": "fail",
  "phase": "initialize",
  "message_key": "population_outside_domain",
  "message_args": {"axis": "z", "unit": "um"},
  "path": "/experiment/populations/group_1/placement",
  "targets": [{"kind": "population", "id": "group_1"}],
  "suggested_actions": ["edit_placement", "edit_domain"],
  "source": {"checker": "core.geometry", "contract_draft": "cad-next/1"}
}
```

稳定错误码、可翻译消息、JSON Pointer、UI target 分开。建议动作是受控 command ID，不是脚本。严重程度为 error/warning/info，检查结论为 pass/fail/unknown/not_applicable；被上游缺值阻断的检查标 skipped。新 API 返回 issues 集合；旧 API 适配返回首个 error。

## 7. 长任务合同

请求包含 request ID（追踪）、idempotency key（防重复）、输入、版本锁、执行语义、步长/时长、seed、观测与输出计划。服务端生成 run ID；客户端不能借 ID 指定任意存储路径。

```text
请求检查失败 → 4xx + issues，不创建可运行任务
接受 → queued → running → completed
                 ├── failed
queued → cancelled
running（cancel_requested=true）→ cancelled
服务中断 → interrupted（保留 partial，重启后可查询）
```

排队取消立即结束；运行取消先设置 `cancel_requested=true`，没有独立 `cancelling` 状态。worker 在安全边界处理取消；强制终止时仅保留已公布的完整块。完成与取消竞争以事务写入的终态为准，已完成结果不被后来取消抹除。

下表保留目标接口分工；其中 `/api/runs` 生命周期、幂等、取消、事件与结果块已实现。当前注册目录入口为 `GET /api/catalog`（可按 `execution_profile` 查询），没有 `/api/registry` 路由；`/api/validate` 的实际能力也须按已发布合同读取，不能据本草案假定全部多阶段检查都已开放。

| 方法/路径或目标 | 合同 |
| --- | --- |
| `GET /api/capabilities` | 模式、合同版本、后端、资源限制、特性和 registry revision |
| `GET /api/registry` | 按类别读取目录，完整清单可独立取得 |
| `POST /api/validate` | 多阶段检查，不推进数值步 |
| `POST /api/runs` | 接受后返回 202 与 run ID；同键同内容返回原任务 |
| `GET /api/runs/{id}` | 状态、已提交步、模拟时间、输出与诊断引用 |
| `POST /api/runs/{id}/cancel` | 幂等取消；终态保持不变 |
| `GET /api/runs/{id}/events?after={seq}` | 有序事件；可重读，客户端按 seq 去重 |
| `GET /api/runs/{id}/result` | result manifest，明确 complete/partial |
| `GET /api/runs/{id}/chunks/{chunk}` | 已发布的帧/场/指标块 |

同键不同请求返回 409；幂等记录与任务绑定，过期/清理策略公开。初版状态轮询与游标拉取足够，验证断线查询后再按需加入 SSE。恢复连接只是查回任务，不代表恢复求解。

通用 checkpoint 目标需覆盖模块状态、RNG、时钟、场/几何、ID 分配器、待发事件、日程位置和版本锁，并通过中断/不中断对照。M4 已在固定种群空间 profile 保存全部现存状态，包括独立 RNG、剩余 tumble 时钟、动态阻挡、账本、帧检查器和最后一步碰撞诊断；该 profile 没有可变 ID 分配、生命周期事件或外部日程，非空 controls 在运行前拒绝。连续 50 步与保存 20 步后恢复 30 步及失败回滚的验证见 [M4 验收](../verification-m4.md)。任务服务的 pause/resume/checkpoint 能力仍为 false，播放暂停不改变求解状态。

## 8. Hash、修订与结果发布

服务端计算可执行科学输入 hash，覆盖算法版本、参数、初始化、依赖锁、积分策略、seed 和输出计划；不含相机/面板。另存原提交文档 hash 和 edit revision。相同 hash 不保证不同硬件位级一致，还需记录库、实际 backend、精度和 RNG。

规范化算法/版本须固定，明确 Unicode、数字、数组顺序、缺值和非有限值；前端以服务端 hash 为准。现有任务与 M4 文件摘要已使用 RFC 8785，严格拒绝重复 JSON 键及非法数值；文件实际写出的 JSON 字面量与计算摘要的规范化过程分别处理。

结果 manifest 保存 run ID、输入/plan/registry hash、实际实现、seed、状态/完整性、模拟时间范围、帧/场/事件索引、指标定义、诊断与证据。帧声明 sequence、step index、time、grid revision、entity IDs；数组附 shape、dtype、轴序、单位、坐标、压缩和 checksum。

现有任务以 JSON 完整帧分块保存，先完成数值步，再写临时块并原子公布索引，完整 manifest 发布后才标 completed；失败记录可为 partial。空间 Task 0.3 的浓度数组也在 JSON 帧块中。大数组二进制容器、typed-array 传输及增量帧是后续目标，须分别验证和版本化，不能视为当前已支持。

## 9. 环境替换

运行开始前选环境与中途换环境分开。后者在合法边界 checkpoint，声明场、库存、累计量、内部状态、边界和菌体状态的 map；预演/检查后一次提交并记录事件。网格重划只解决空间分布转换，不自动迁移全部科学状态。无映射则拒绝，原运行保持可查。变更物理域范围还需解释新增/移除区域的物质量。此能力保留在路线中，排在首个完整 CAD 案例之后。
