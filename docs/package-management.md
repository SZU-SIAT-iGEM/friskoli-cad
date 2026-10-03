# 本机包管理

包用于版本化分发参数、物种属性、模型数据和受信任的可执行模块。预览与安装分开；打开 ZIP 预览不会运行里面的 Python。项目通过精确版本和 SHA-256 锁引用已安装内容，不靠包名选择“当前最新版”。本能力面向 `modular-spatial-v1`。

工作区包管理入口先显示来源、许可证、内容类型和 hash，安装需明确操作。安装本身只校验并复制文件。明确加载项目的依赖目录时，才执行已安装且通过 hash 检查的模块工厂；不要把这个过程称为安全沙箱。离线编辑保留包锁，求解需要本机已经安装所需包。

## 文件合同 0.1.0

ZIP 根目录包含 `package.json`。所有成员使用规范的相对 POSIX 路径；拒绝越界、大小写冲突、重复名、符号链接、加密成员和不可移植的 Windows 保留名。压缩文件不超过 32 MiB，展开内容不超过 64 MiB，最多 1024 个成员。

```json
{
  "package_version": "0.1.0",
  "id": "example",
  "version": "1.0.0",
  "api_version": "0.1.0",
  "kind": "executable",
  "license": "MIT",
  "source": "explicit source URL or document reference",
  "dependencies": {"other": ">=1.0.0,<2.0.0"},
  "files": {"module.json": "sha256 of manifest bytes", "module.py": "sha256 of code bytes"},
  "modules": [{"manifest": "module.json", "implementation": "module.py", "factory": "create"}]
}
```

以上 hash 为说明文字，构建包时必须换成文件实际的 64 位十六进制摘要。每个文件都必须列入 `files`，唯独 `package.json` 本身除外；整个 ZIP 的 hash 则进入项目锁。模块 ID 必须位于包 namespace 下，如 `example.sensor`。工厂返回值的 manifest 必须与预览的 manifest 逐值一致，并提供模块执行合同。

数据包使用 `kind: "data"`，`implementation` 和 `factory` 均为 null；它不会向可执行 registry 增加 Python 实现。数据文件仍完整校验和版本化，不能因为数据包保存了一份模块声明就宣称该模块已经可以计算。

包内文件可按预览时的归档 hash 单独读取或下载，再由对应的项目/assembly 导入器明确使用。读取文件检查完整依赖锁与安装内容，不导入 Python。数据包不会擅自将文件内容解释为新的方程或覆盖当前项目。

## 依赖解析与安装目录

版本使用三段整数。支持精确版本，以及逗号连接的 `==`、`>=`、`<=`、`>`、`<` 约束。解析器联合求解所有依赖并选取满足约束的最高版本，拒绝循环和不可满足的组合，最终锁定具体版本与内容。相同 ID/version 不允许被另一份字节内容覆盖。

默认目录为 Windows `%LOCALAPPDATA%/Friskoli-CAD/packages`，其他环境使用用户本地数据目录；可以通过 `FRISKOLI_PACKAGE_DIR` 明确指定。仓库不保存安装缓存、用户包或运行数据。项目 pin 和其他已安装包引用会阻止卸载，避免正在使用的项目突然失去依赖。

## 服务与 CLI

| 操作 | HTTP |
| --- | --- |
| 列表 | `GET /api/packages` |
| 预览 | `POST /api/packages/preview`，`archive_base64` |
| 安装 | `POST /api/packages/install`，同一归档和预览所得 `expected_sha256` |
| 解析并锁定 | `POST /api/packages/resolve`，`requirements`、可选 `pin_id` |
| 卸载 | `POST /api/packages/uninstall`，`id`、`version` |
| 读取成员 | `POST /api/packages/file`，`id`、`version`、`path`、`expected_sha256` |
| 读取项目实际目录和实现锁 | `POST /api/catalog/project`，`project` |

CLI 入口为 `python -m friskoli_cad.packages`，提供 `preview`、`install`、`list`、`lock`、`uninstall`。安装同样要求 `--sha256` 与预览结果一致；`--store` 可选择独立测试目录。源码示例与反例见 `tests/test_packages.py`。

验证覆盖：预览/安装不执行、完整依赖求解、版本冲突、pin 卸载保护、坏路径/坏 hash、安装后篡改，以及新 Python 进程按项目锁运行外部模块。该验证检查执行与复现合同，不替外部科学模块提供数学正确性或实验标定背书。
