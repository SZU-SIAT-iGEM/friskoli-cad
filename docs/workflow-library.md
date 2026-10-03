# Workflow 机制模板库

机制模板保存一个菌群的完整机制图、参数和来源、初始几何、观测通道及外部输入要求。模板不包含可执行插件，不代表 DNA 元件或经过标定的物种。

在 Workflow 打开模板库，选择菌群，填写可编辑的名称、ID、版本与来源，保存其机制。导入 assembly JSON 先经服务端 `validate_assembly`，通过后才存入本机库。相同 `ID@version` 的相同内容视为重复；不同内容必须更换 ID 或版本，不能静默覆盖。

本机库限制为 50 项、10 MiB。浏览器存储失败或原有记录损坏时保留原内容，绝不自动清空。删除可在本次对话框撤回；机制应用由主工作区统一记录，因此可用工作区 Undo 恢复。模板库的删除撤回与科学项目的 Undo 是两件事。

官方示例目录从现有科学模板目录中的全部模板调用 `extract_assembly` 提取，不另写科学图。每项包含内容 SHA-256、精确 module_manifests、模板版本与 `example` 来源；参数为 constructed/un-calibrated。profile 不匹配时禁用 Apply，实际外部环境、端口及模块依赖由现有 `apply_assembly` 严格校验；不会暗中创建/替换外场。因不同项目缺少输入而拒绝应用时，可打开对应完整科学示例理解所需环境。

## 接口集成

`showAssemblyLibrary({groups, selectedGroupId, executionProfile, extract, apply, validate, officials, revisionGuard})` 返回原生 dialog。

- `groups` 为当前 Project groups 对象；`selectedGroupId` 为默认菌群。
- `extract(groupId, metadata)` 异步返回 assembly。
- `validate(assembly)` 异步返回已验证 assembly（也可以无返回值，但拒绝必须抛错）。
- `apply(groupId, assembly)` 由调用方执行服务端 apply，并在版本检查后以统一 `edit` 提交，纳入 Undo。
- `officials` 为 `official_assemblies()` 的数组，各项包含 `assembly` 和 `sha256`。
- `revisionGuard()` 在请求前后返回草稿是否仍匹配打开对话框时版本。调用方必须在 apply 回包后、真正 edit 前再次检查，防止旧请求覆盖新草稿。

服务端与主前端已接入 `POST /api/assemblies/extract`、`POST /api/assemblies/apply`、`POST /api/assemblies/validate` 和 `GET /api/assemblies`。目录仅返回数据，不安装或执行外部代码。模板托管、升级冲突和引用重写预览等剩余工作见[清单 U01](archive/planning/remaining-work.md)。
