# 开发与复现

需要 Python 3.11+；前端测试需要 Node.js 22+。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,standards,research]"
npm ci
.\start.ps1 -NoBrowser
```

Linux/macOS 在激活虚拟环境后使用 `python -m pip install -e ".[dev,standards,research]"` 和 `python -m friskoli_cad.replay_service --port 8765`。

## 检查与打包

```powershell
.\.venv\Scripts\python -m pytest tests -q
npm test
.\.venv\Scripts\python -m build --wheel
```

`standards` 安装 SBOL 测试依赖，`research` 安装研究脚本的绘图依赖。GPU 测试需要对应硬件和 `cuda11`；CPU 可以完成普通运行。`npm ci` 安装 IndexedDB 测试依赖，避免持久化测试因缺包跳过。应用运行时不依赖 Node.js。

修改引擎、模型或预设源码后，停止旧服务并重新运行 `start.ps1`，再用服务当前的 version lock 提交任务。运行中的服务保留启动时的源码锁；修改源码后直接提交会报告 `task.source_changed`。

当前 wheel 生成在 `dist/`。`docs/first-release/artifacts/` 保存 2026-10-04 的交付记录，重现当前版本请从当前源码重新打包。仿真输出写入 `runs/`，不会进入 Git。

## 文件目录

- `src/friskoli_cad/`：当前程序、网页资源和八个可运行预设。
- `tests/`：当前合同、科学计算、任务服务和前端测试。
- `research/`：芯片、中心底物与研究导出脚本；小实验串行运行。
- `docs/`：从[文档索引](docs/README.md)进入当前指南和科学说明。
- `archive/legacy-runtime/` 与 `docs/archive/`：历史实现、旧预设和阶段记录，不属于当前运行入口。

中心场默认 DX=1 µm。默认发布预设使用 strong 释放和 responsive 信号参数；旧研究的 seed、网格、参数及结果应按各自日期阅读。新的科学结论需要新的运行记录。
