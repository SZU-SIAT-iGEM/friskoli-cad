# N4 参数设计与比较

更新：2026-10-02。N4 现已覆盖第一段设计流程和第二段生物组合/结果评价：以 N3 科学项目为共同基线，填写目标、参数约束和结果阈值，枚举候选、运行重复、比较、评价并保存原生设计包。底盘表示菌群的基础参数与几何；元件表示可装入的完整菌群 workflow 机制分支，外部环境提供者、物种和模块版本会在导出/应用时检查。

## 操作

1. 在启动页选择任意 N3 趋化模板，先在 Space / Workflow 调整场景、机制图和观测区域。顶部设置步长及步数。
2. 打开 **Design**，填写设计名称、底盘及依据，选择目标菌群、指标和最大化/最小化方向。目标采用数值内核实际记录的位移、区域占比、到达比例或驻留时间。
3. 从已注册的数值参数中选择扫描项，填写有限枚举值及不重复的 seed。可以添加多个参数和参数硬/软约束。硬约束排除方案；软约束计算单独显示的惩罚，不与不同单位的目标指标相加。
4. 点击 **Generate candidates**，检查每项修改、排除理由和运行预算。候选保留同一场景与观测；固定偏置对照基于未扫描原项目，只把运动的信号输入替换为 `signal.constant_bias`，`bias=0.5` 与 N3 对照模板一致。原信号读数保留，运动不再读取这些信号。
5. 选择候选/对照和 seed，点击 **继续所选缺失/失败运行**。默认每批 4 次，可设 1–8 次；显示本批及本批后剩余数量。已完整成功和仍在运行/提交状态未知的重复不会再提交，失败历史保留并允许显式再运行。可停止后续运行并取消活动任务；停止/失败不会伪造完成。
6. 使用 **Compare completed runs** 查看完整记录；HTML/CSV 报告列出目标、候选、重复数、均值、样本 SD、来源版本和排除原因。一次重复的 SD 为缺失；未到达比例为真实零，缺失指标或空分母保持缺失。
7. 用 **Export .friskoli** 保存完整设计；在 Design 中用 **Import .friskoli** 重开。普通 Save 仍保存工作区 JSON。打开候选图可继续高级编辑，改动后需重新生成设计。
8. 在 Design 页的 Biological chassis / component assembly 区导出或应用 assembly。part 保留目标菌群的空间位置和几何，替换其完整机制分支；chassis 还可以提供初始几何。环境节点不会被隐式复制，兼容性不满足时请求失败。
9. 对 0.2 brief 点击 Evaluate results。只有计划中的 seed 全部完成、版本和观测一致、硬阈值每次都通过且每次相对固定对照都达到最小改善时，才显示探索性推荐；并列、空值、失败、partial 或证据不足会显示无推荐。

## 合同与计算边界

- DesignBrief/Design 0.1.0 继续读取；0.2.0 增加结果阈值与选择规则。Workspace 0.6.0 保存 design、brief 和 assembly 相关草稿，继续读取旧 0.1–0.5。Project 与数值时序保持原合同；运行可选通用 Task 0.5，旧任务仍可读。
- 最多 16 个执行分支（包含对照），最多 8 个独立 seed，总计不超过 32 次运行。超预算拒绝，不静默截断。没有可行候选时返回空结果和原因；少于两项可行候选不会标为可推荐。
- 每个候选经过注册参数、项目与编译计划的静态检查；生成阶段不构造完整计算场、不推进数值步。HTTP 生成使用当前 TaskService 的资源配置，独立 Python 调用可传 task_limits。运行预算包含预计峰值内存和总输出量，沿用 TaskService 的保守估算，不承诺实际耗时。
- 设计及每次提交各自冻结。运行记录通过 `design_ref` 关联设计和候选，修改草稿不回写历史。新设计避免复用已有运行的 ID；比较清除其他设计的旧选择。
- 报告按实际版本锁分组，不合并不同来源。失败、partial、重复 seed、观测或输入不匹配的记录保留并注明未计入统计的原因。
- 当前结果评价是描述性的配对 seed 规则，不是统计显著性检验或实验性能预测；软阈值单独展示，不把不同单位混成分数。多目标 Pareto 排序和跨 profile 的 assembly 安装目录仍是后续工作；同一冻结设计内的分批与选择性重跑已提供。

## 原生设计包

`.friskoli` 是 ZIP 数据容器，当前布局为：

```text
manifest.json
 design/package.json          # 完整无损payload：设计、工作区、运行、目录
 design/brief.json
 design/candidates.json
 evidence/runs.json           # 冻结输入、结果、事件及来源
 dependencies/registry.json
 dependencies/locks.json
 reports/design.html
 reports/design.csv
```

manifest 记录文件长度和 SHA-256。导入检查校验和、重复路径、路径越界、成员类型、展开大小和跨引用；不自动执行模型或安装插件。HTML 转义输入，CSV 防止文本被解释为公式。压缩包最大 64 MiB，单成员 64 MiB，总展开 128 MiB；HTTP JSON 请求上限 64 MiB，因此浏览器 base64 导入的实际压缩包上限更小，超出明确拒绝。

运行的完整冻结输入和版本锁均保留，重新计算仍需兼容的代码与依赖。导入结果为只读历史，可由 IndexedDB 归档恢复；原生包仍是跨机器备份。本机自己运行的任务只在浏览器保存冻结输入和服务端引用，不把长 replay 写进 localStorage。刷新后读取服务端已经提交的结果块；未知提交保留原 request_id 与 Idempotency-Key，不换新键猜测重试。清理历史需要先导出原生包并显式操作，活动任务不能清理。

已提供明确元数据的 SBOL Component 子集与 OMEX 容器及损失说明；SBML/SED-ML 等仍明确拒绝不支持的转换，详见[标准范围](standards-export.md)。完整目标包目录见[交付设计](archive/design/delivery-and-migration.md)，当前简化布局不丢弃payload信息。

实现：`design.py` 负责有限枚举，`design_delivery.py` 负责数据包和报告，`web/design-panel.mjs` 负责表单与候选界面，`app.mjs` 复用原任务批次。对应验收见[本轮记录](archive/verification/verification-n4-design.md)。

## 分批容量与持久化

TaskStore 默认有界 64 条记录、16 MiB 冻结元数据，为最多 32 次计划和保留失败尝试留出空间；不是无限历史。分批前 `assertBatchCapacity(submissions)` 一次核验整个批次记录数与序列化大小，额外为每条状态保留 8 KiB。受保护的设计记录不会被自动淘汰；容量不足需要先导出，再显式清理旧设计。

IndexedDB 写入完成后才发送提交请求。数据库写入失败时不向服务器发送新任务；页面显示失败，已有归档保留。另一个窗口修改相同任务历史时拒绝旧窗口覆盖，应刷新重新读取。普通页面刷新仍可恢复；浏览器清除网站数据、隐私模式或跨浏览器迁移不在恢复保证内。

导入归档单包上限 128 MiB，最多 4 个设计、总计 256 MiB。超过限制不清除已有包。同设计 ID 的不同证据拒绝静默覆盖；需保留原包并明确处理旧归档或改用新设计 ID。后端导入校验必须先通过，IndexedDB 层不代替科学/格式验证。

### 主界面接线

- 初始化先 `await openTaskPersistence(localStorage)`，以返回的 `storage` 构造 TaskStore，再按原逻辑 `restore()`。如果 IndexedDB 不可用，应明确提示，不声称刷新恢复已开启。
- `planDesignBatch(design, runs, selection)` 返回 queue、eligible、completed、active 和 remainingAfterBatch；selection 包括 candidateIds/seeds/batchSize。开始批次前对全部 submission 做 `assertBatchCapacity`，并删除旧的“此设计已有历史就禁止运行”条件。
- 导入原生包经后端验证后 `await archive.savePackage(payload)`。加载对应工作区时 `await archive.loadPackage(design.id)`，验证 design 并 normalizeReplay，再合并为 imported 只读记录。清理归档用 `removePackage(id)`，任务清理后 `await taskStore.flushPersistence()`。
- 评价后端应保留失败尝试的排除原因，但只对完整有效成功重复做唯一 seed 检查；两个完整成功的同 seed 仍不能伪装独立重复。

### 本次验证

Node 的 design-batches/task-persistence/n4-ui/tasks 共 45 项通过。覆盖 32 个成功重复加 8 次保留失败、IDB 迁移与刷新重读、存储写入失败不发送、精确 request/idempotency 重试、跨窗口冲突、导入冲突与四包上限。IDB 测试使用 `fake-indexeddb@6.2.4`，属于 API 逻辑验证，不冒充实体浏览器或设备实测。


## 通用运行与场输出设置

Design settings 可保存 `backend`、`field_stride_xyz`、`include_final_fields`，均为所有候选共同的冻结设置。体积平均预览只改变输出；完整末帧场独立保存为 NPZ。原生包与结果评价核对这些设置，不能把不同 backend 或输出计划的记录混作同一重复。完整合同及资源 CLI 见[Task 0.5](task-field-previews.md)。实际显存、主机 RAM 与磁盘可用量在任务 preflight 再检查；候选生成成功不保证运行完成。
