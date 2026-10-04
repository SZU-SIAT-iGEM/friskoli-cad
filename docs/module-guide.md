# 模块与当前案例

第一版采用 modular-spatial-v1。正式 ID/version、端口、单位、来源和检查状态来自 Catalog 0.5；完整列表见[模块索引](module-reference.md)，当前支持范围见[唯一支持矩阵](first-release/support.md)。旧开发执行器已删除。

欢迎页提供模块基础、材料与生命周期、MCP 梯度/零梯度，以及 PTS A/B 小域/中域及各自匹配对照。模块基础适合第一次运行；中心案例是固定表面酶的研究近似，不能称为完整来源模型。

在 Workflow 中连接已注册模块。端口的 shape、quantity、unit、species 和 population owner 必须相容；same_step 输入必须由同一步先完成的模块提供，previous_step 输入读取上一步已提交值。声明的阶段限制依赖关系。检查会按路径指出错误，不自动替换机制。

系统负责共享场、库存、几何、RNG、身份事件和原子提交。模块读取明确授权的资源，提交输出、状态和 effects。基础控制、生长、分裂、死亡与 PTS/MCP 都遵守 Module 0.2 / StepContext / ModuleProposal。

普通生长使用 growth.linear_elongation 或显式营养库存机制；geometry.capsule_derived 读取实际几何；division.volume_adder 提出分裂，系统检查可用位置、库存继承和 max_cells。平台能力由对应测试证明，不代替来源特定生命周期的科学验收。

空间对象是 graph 的显示与编辑适配器。隐藏或锁定编辑对象不会改变已提交任务。参数要填写数值、单位和实际依据；Catalog 的 tested 只表示列出的数值检查。研究方程、近似与未核实参数见[科学范围](first-release/science.md)。
