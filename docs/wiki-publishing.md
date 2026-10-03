# 发布静态计算记录

静态 Wiki 从一个已完成的原生设计包，或完整版“Export run JSON”导出的独立空间任务记录构建，包含过去计算的完整记录，不安装或调用求解内核。浏览者可以选择运行与 seed，播放或拖动时间帧，在共享 CAD 三维视口中查看菌体、已支持的环境对象和 XY/XZ/YZ 场截面，并读取每帧指标、场/事件/通道 JSON、冻结输入与版本来源。需要编辑或重新计算时，使用页面中的完整版下载链接或本地安装说明。

## 准备数据

1. 在完整版创建设计，至少保留两个候选和明确对照，完成需要比较的 seed 批次。
2. 检查结果、参数来源和限制，然后导出 `.friskoli` 原生设计包。公开发布前自行确认其中的实验数据可以公开；导出器不自动脱敏，也不上传文件。
3. 若包里混有失败、取消、不完整或缺帧运行，Wiki 构建会拒绝。保留这些历史记录于原始归档，为公开演示另导出全部完成的记录。完整运行中的未定义指标保持 null。

## 构建与打开

在已安装 Friskoli 的 Python 环境运行，输出目录必须尚不存在：

```powershell
python -m friskoli_cad.wiki_export D:/records/completed.friskoli D:/publish/friskoli-wiki --download-url https://YOUR-PUBLISHED-SITE/full-version
python -m http.server 8881 --directory D:/publish/friskoli-wiki
```

用浏览器打开 `http://127.0.0.1:8881`。第二条命令只是普通静态文件服务；可以由任意静态网站托管整个目录。必须替换实际下载地址，示意地址不代表项目已发布。不要用 `file://` 打开：浏览器通常限制本地 JSON 的 fetch。

没有公开下载地址时省略 `--download-url`，页面显示本地安装说明，绝不生成虚假下载链接。独立任务无需创建设计或候选归属：

```powershell
python -m friskoli_cad.wiki_export D:/records/completed.result.json D:/publish/standalone-wiki
```

支持异步空间 task contract 0.3/0.4/0.5 的 Export run JSON，不接受只含轨迹、没有冻结 submission/task/manifest 的文件。独立记录不展示候选比较报告，原始 JSON 字节完整保留并提供下载；CSV 包括每帧时间、索引与原始指标 JSON。

目录包括入口、本地脚本与样式、共享三维 viewer、Three.js 及许可证、显示适配目录、示例清单、原始设计包或独立运行文件、完整 JSON、适用的比较报告、CSV 和 SHA-256 清单。原始下载文件字节保持不变。脚本读取发布目录内的数据与资源，无外部 CDN、Run、编辑、上传、求解、插件安装或 backend 调用。`viewer-catalog.json` 仅提供导出软件的显示适配器，不能替代冻结科学版本锁。

保存的阅读位置按记录和运行恢复；页面隐藏会暂停播放。时间滑条使用浏览器原生键盘与触控交互。三维场景复用 `SpatialViewport`，截面读取保存场数组及其真实格距；原始三维坐标、场数组及全通道可在完整帧中查看和下载。当前一次构建只发布一个设计包或独立任务作为一个示例，设计运行列表保留该包全部候选和重复。真实样例、四尺寸浏览器检查与实体设备限制见[共享 viewer 验收](archive/verification/verification-n5-delivery.md)。

## 来源与完整性

构建器复用原生包校验，额外核验冻结项目与回放 domain/run、保存帧日程、最终时间、每帧时间、事件连续性和浓度数组形状。独立任务另核验 task/manifest/replay 三处 document/scientific/registry SHA-256、完成步数和最终时间。完整 JSON 保留输入、seed、版本锁、参数证据、历史 task/manifest 和实际结果；注册表若原始包包含则保留。历史环境若未记录，明确为 unknown，不用构建机器信息冒充计算机器。文件哈希证明文件一致性，不证明记录作者或实验真实性。

这套验证可以拒绝损坏/不完整记录，不能把构造参数变成实验标定，不能证明第三方编辑过的 JSON 是真实实验。

## 发布前检查

核验公开许可证和真实完整版下载地址，测量目录总字节、最大 JSON、加载耗时与浏览器内存。网络下载分块显示进度，随后仍累积完整 JSON 并一次解析；按帧/块增量解析、按需加载与有界缓存尚未实现。按桌面、平板、手机横竖屏检查文本、滚动、时间滑条、键盘焦点与旋转后位置。浏览器尺寸模拟、手势逻辑测试和实体设备实测需分开记录。验证记录见 [N5 交付检查](archive/verification/verification-n5-delivery.md)。


Task 0.5 的场预览带独立field_domain与volume_mean标记，不能把预览当作完整计算分辨率。完整末帧NPZ是任务服务单独提供的artifact；静态记录中的链接和摘要不表示NPZ二进制已经打包进Wiki。需公开原始场时应另行保存并核对下载文件的SHA-256。完整模拟的验收状态以[当前N5记录](archive/verification/verification-n5-simulation.md)为准。
