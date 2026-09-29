# N1 发布包验证记录

日期：2026-09-29。验证对象是当时从工作区复制到系统 Temp 的源码快照。
本记录不等同于最终提交或发布版本的字节级验证；之后的代码、资源、依赖或构建工具变化
都可能改变 wheel，最终发布须按其实际内容重新构建和核验。

## 实际构建和安装

复制 `src/`、`pyproject.toml`、`README.md` 至独立 Temp/source，排除 Python 缓存
和 egg-info。仓库根目录当时无项目 LICENSE；源码内的 KaTeX、Three 许可证随资源复制。
构建和安装只在 Temp 中进行，未修改全局 Python，主仓库没有产生 build、dist 或 egg-info。
仓库原有的 `web/vendor/three/build` 是第三方分发资源，不是本次构建输出。

| 项目 | 实际记录 |
| --- | --- |
| 任务目录 | 系统 Temp 下 `friskoli-n1-wheel-cakcwyys` |
| 构建 Python | bundled Python 3.12.14 |
| 构建工具 | setuptools 84.0.0、wheel 0.48.0 |
| wheel | `friskoli_cad-0.1.0-py3-none-any.whl` |
| 大小 / ZIP 成员 | 1,587,722 bytes / 145 |
| SHA-256 | `08e7f726276e166a1ccb7a3badfa82c1c8c8973c58528a89f4bdc47c49ff71f0` |
| 安装运行 Python | 系统 Python 3.11.9 |
| 运行依赖 | numpy 2.1.2、jsonschema 4.26.0 |
| wheel 声明依赖 | numpy >=1.26,<3；jsonschema >=4.23,<5 |

以下为实际执行的成功命令，当前目录为上述 Temp 任务目录。构建时关闭网络索引和
自动依赖安装，使用已存在的构建工具；pip 真正安装 wheel 到独立 `install/`。

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
& 'C:/Users/Wu Shangru/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -m pip wheel --no-index --disable-pip-version-check --no-deps --no-build-isolation --wheel-dir dist ./source
python -m pip install --no-index --disable-pip-version-check --no-deps --no-compile --target ./install ./dist/friskoli_cad-0.1.0-py3-none-any.whl
$env:PYTHONPATH=(Resolve-Path ./install).Path
python ./verify_installed.py
```

初次 pip 操作曾因参数未写 `./source` 被当作分发名称而失败；修正后沙箱又拒绝
pip 写临时构建目录。正式申请提升权限后同一本地构建命令成功，未安装全局包。
失败尝试的 pip 临时目录也位于系统 Temp；没有把失败输出记为成功验证。

## 安装目录资源核验

确认 `friskoli_cad.__file__` 位于 Temp/install/friskoli_cad，运行目录不在源码库中。
以 `importlib.resources` 读取资源，并核对复制快照与安装目录 JSON 文件名集合：

| 资源 | 验证结果 |
| --- | --- |
| default_registry().catalog | modules 与 entries 均为 19 |
| engine/declarations | 4 个 JSON 全部读取、解析 |
| protocol/schemas | 11 个 JSON 全部读取、解析 |
| 新增关键 Schema | catalog、task-draft、workspace-v0.3 均在包内；workspace-v0.2 保留 |
| examples | registry_readout.project.json、workspace_3d.project.json 两份均在包内 |
| KaTeX 主资源 | katex.mjs、katex.min.css、LICENSE、README.md 均非空可读 |
| KaTeX 字体 | 60 个，覆盖 ttf、woff、woff2 |
| CSS 字体引用 | 60 个均为本地路径，全部指向可读取资源 |
| Three 许可证 | wheel 内有 web/vendor/three/LICENSE |

本步骤的 JSON 解析不代替各合同的 Schema/语义测试。

## 安装包 HTTP 与示例运行

从安装包导入 ReplayHandler，以 ThreadingHTTPServer 绑定 `127.0.0.1:0`，
系统当次分配端口 54071。逐一请求后核对响应内容与安装包资源字节、Content-Type。

| 检查 | 实际结果 |
| --- | --- |
| STATIC_FILES 全部静态路由 | 87 条全部 HTTP 200、字节和 Content-Type 一致 |
| GET /api/catalog | HTTP 200，内容与 default_registry().catalog 一致 |
| GET /api/examples/registry-readout | HTTP 200，与安装包内 registry 示例一致 |
| GET /api/example-project | HTTP 200，与安装包内旧例一致 |
| registry 示例 validate_project、build_replay 与 POST /api/replay | 成功，dt_s=0.1、steps=2、3 帧、最终时间 0.2 s |
| 旧例 validate_project、build_replay 与 POST /api/replay | 成功，dt_s=0.1、steps=2、3 帧、最终时间 0.2 s |
| 两例 HTTP 与直接调用 | 各自的 snapshots 逐帧一致，HTTP execution.status=completed |

验证完成后 server.shutdown、server_close、thread.join 均执行，确认服务线程退出。

## 证据位置与限制

脚本 `verify_installed.py`、结果 `verification.json`、构建核查 `build-audit.json`
保存在当次 Temp 任务目录，不作为永久仓库依赖。文件可能随系统临时目录清理而移除；
复现时应建立新的 Temp 目录并执行等价检查。

- 这是 wheel 安装目录验证，运行依赖沿用系统现有版本，未验证全新机器联网安装。
- 只验证所列短示例和资源分发，不证明长期运行、全部操作系统或任意模型规模。
- 资源可读取与 HTTP 200 不等同于浏览器公式渲染、手势或实体设备测试。
- 异步 task Schema 仅为草案资源；本次运行仍为同步 replay，不代表 N2 服务实现。
- 后续快照对比时 README 已变化；当次完整源码及 vendor 字节仍一致。
  本记录不对之后的最终提交作字节相同保证。
- 根目录尚无项目 LICENSE，第三方许可证齐全不等于项目发布许可证已确定。
