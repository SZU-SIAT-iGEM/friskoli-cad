# 隔离安装与独立用户记录

Python 3.11+。交付的 wheel 在 `artifacts/friskoli_cad-0.2.0-py3-none-any.whl`；SHA-256 由[制品清单](artifacts/manifest.json)记录。请在源码目录外建立一个空文件夹，把 wheel 放进去，然后运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install .\friskoli_cad-0.2.0-py3-none-any.whl
.\.venv\Scripts\python -m friskoli_cad.replay_service --port 8765
```

浏览器打开 http://127.0.0.1:8765。第一次安装需要下载依赖；依赖与 wheel 安装后，CPU 求解及前端可本地使用。

独立用户按下面流程操作，记录实际发生的事情。G7 需要未参与开发的人完成；自动 UI 操作和本文作者的检查分别列出。

1. 在欢迎页载入 Modular foundation，确认看到初始对象。
2. 在 Workflow 或属性中改一个数值参数，记录原值、新值及理由。
3. 用短配置运行一次，记录 run ID、状态、dt、steps 和 seed。
4. 进入 Design，枚举两个参数值，使用 seeds 1、2；生成并运行，比较完整结果。
5. 在 Results → Data 读取指标，切换保存帧；选中一个菌体并 Focus cell。
6. 导出 portable research package .friskoli。记录文件名与大小。
7. 关闭原来的服务进程；在另一个空目录启动安装版，File → Open 重读这个结果包。
8. 核对 run ID、帧数、指标、冻结参数、seed 及请求的浓度场。记录是否能脱离原服务读取。

回传文字包括：测试者是否参与开发、操作系统/Python/浏览器版本、每步成功或失败、run ID、导出包名、问题与耗时。不补写未执行的步骤。实体触屏操作另列设备和手势；浏览器模拟尺寸不记作实体设备实测。
