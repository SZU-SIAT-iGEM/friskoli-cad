# 状态归属与后续扩展

给系统增加底层信息会影响保存格式、计算模块和前端。现有模块接口能减少牵连，但不能保证“后来加字段完全无影响”。每增加一种状态，先确定它属于谁、单位是什么、由谁更新、哪些模块读取，以及怎样写入可回放结果。

当前状态更新：2026-10-03。新统一执行合同、状态策略和资源规则见[运行结构](system-execution.md)。下表区分旧执行规则、空间基线与科学 profile；完整能力对应见[开发安排](archive/planning/development-order.md)。

| 信息 | 归属与更新方式 | 当前实现 |
| --- | --- | --- |
| 某种物质的浓度 | 环境中按物种标识的空间场；输入、摄取、释放和输运分别改变它 | `species` 参数约束模块连接；[项目快照](archive/engine-stages/project-schedule.md)可只登记物种，图中接入环境模块时才创建浓度场。双物种示例用 `substrate` 与 `oxygen` 检验独立物质账，新 profile 已注册显式氧消耗与气液交换，不自动与生长耦合 |
| 环境黏度 | 介质属性。若空间均匀，可先是环境模块参数；若随位置或时间变化，需要环境状态场与更新模块 | 协议可声明带 `quantity`、单位的 `field.scalar` 端口；新 profile 提供条件物性目录、均匀黏度对扩散的 Stokes–Einstein 缩放及独立的构造运动响应；未实现任意变黏度流体力学 |
| 扩散系数 | 具体物种在指定介质和条件下的输运参数，或由环境属性计算出的场 | 已有[均匀系数、无通量边界的基础扩散](archive/engine-stages/no-flux-diffusion.md)；新 profile 的 `medium.viscosity_diffusion` 显式接入条件化缩放 |
| 菌体位置与朝向 | 单菌体运动状态；一个运动模块更新，结果帧按 ID 输出，空间采样与沉积读取该位置 | legacy 保留[定时转向及中心点反射](archive/engine-stages/moving-cells.md)；空间基线提供无偏 run/tumble 和胶囊碰撞保护；[科学 profile](archive/legacy-protocols/chemotaxis-profile.md)另接 PTS/MCP 信号驱动的 hazard 运动 |
| 菌体尺寸 | 单菌体几何状态。项目可逐菌体给初始尺寸及来源，生长模块更新总长 | Project 0.2 起可声明胶囊总长与直径；legacy 保留演示伸长与[长度 adder](archive/engine-stages/adder-division.md)。科学 profile 使用实际营养接受量更新生长和面积 adder，几何受阻时保留未消费营养 |
| 几何表面积与膜材料 | 表面积由当前胶囊尺寸推导；膜材料、受体拷贝数及其分布应是各自带单位的单菌体状态 | 已有 `geometry.capsule_readout` 与 `pts.capsule_area` 面积读数；按用户决定不设膜材料库存，分裂新增几何膜面积默认可获得并记录；受体/表达拷贝数按自己的规则继承，见[膜面积与拥挤生长](archive/design/membrane-area-and-crowding.md) |
| 有限源与实体底物 | 环境图节点持有几何与库存；自释放或接触降解提出有限释放，产物进入同物种场 | 空间 profile 已注册源/底物与降解机制，结果携带库存；部分降解保持原盒体，耗尽后退出碰撞和场阻挡 |
| 信号、营养、表达与健康 | 相应单菌体模块持有状态，各机制只有一个 writer；分裂按声明分配或继承 | 科学 profile 保存 A/B memory、MCP 适应、胞内可用养分、总 copies 和 health；死亡事件保留触发规则与随机抽样，剩余养分进入移出账 |
| RNG、时钟与完整状态 | 运行实例独占；候选步整体提交或回退 | M4 保存模块/群组/实体独立 RNG、剩余事件时钟、ID、场和模块状态；科学 checkpoint 另含动态实体、生理、hazard/dwell、死因与观察器。[自包含文件](archive/legacy-protocols/checkpoint-files.md)可在新进程恢复，Task 0.6 已接完整二进制 checkpoint、暂停与关联续算 |

这里说的黏度首先指**环境介质的动态黏度**，单位可声明为 `Pa*s`。若研究的是菌体内部黏度，它应归于对应菌体状态。黏度与扩散系数有联系，但不能把它们合成一个字段：某种分泌物增加时，可以先改变该物质的浓度，再由经过定义的介质模块计算黏度场，输运或运动模块读取这个场。浓度可换算并核对物质分子数；黏度没有同样的物质守恒账。没有关系式时，系统不应自行推算。

## 菌体尺寸与生命周期

本节的演示性生长和长度 adder 属于 legacy 执行规则；`spatial-unbiased-v1` 保持固定种群与固定尺寸。`chemotaxis-spatial-v1` 单独登记营养生长、总 copies 表达、health/death 和面积 adder；来源差异、分裂分配和受阻行为见[科学说明](archive/science/n3-mechanisms.md)。

用户提出的顺序是：先声明初始尺寸，再由生长模块改变单菌体尺寸；分裂后，延续原 ID 的菌体与获得新 ID 的菌体各有明确的几何和其他状态。几何应在运行时有唯一来源，前端读取结果帧显示，不能在前端独立“长大”。分裂的状态分配按模块声明的 `copy`、`split`、`reset` 规则执行。当前摄取累计量平分、运动朝向继承、adder 新增长度归零；更多状态仍需逐项判断是否应守恒，不能套用同一条分割规则。

`CellGroup` 现有可选的逐菌体胶囊尺寸；旧项目读取为未知，新项目见[初始尺寸说明](archive/engine-stages/capsule-geometry.md)。旧 `0.1.0` 单菌体帧仍拒绝未声明的几何字段，新 `0.2.0` 帧可[逐帧输出尺寸](archive/engine-stages/growth-frames.md)和分裂事件；生长模块遇到未知尺寸会报错，旧模块继续按原接口运行。当前空间采样模块的 `support_*_um` 是作用范围，不能当作菌体尺寸。

## 对现有接口的判断

目前模块端口按**形状、物理量、单位、物种**检查连接；模块声明带版本，行为图带协议版本。运行器已处理 `field.scalar`、`cell.scalar`、用于运动的 `cell.vector` 和日程时钟的 `global.scalar`，环境快照可携带多个带类型的场。[项目级物种目录](archive/engine-stages/project-schedule.md)、动态胶囊尺寸、演示性分裂和科学 profile 的营养生命周期已建立；新 profile 已有条件物性目录与声明式状态规则，任务服务支持已保存边界的同域守恒重划；无映射的状态明确拒绝。膜材料库存按用户决定不实现。后续每次扩展仍需保存格式迁移、旧图兼容检查和新功能的数值验证。
