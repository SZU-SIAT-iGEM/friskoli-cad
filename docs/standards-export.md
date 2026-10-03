# 标准导出支持范围

当前提供 OMEX v1 容器与有条件的 SBOL3 Component 子集。原生设计包保持无损；有损格式不替代原生包，不通过外壳或扩展名宣称完整空间模型兼容其他求解器。

## OMEX

```powershell
python -m friskoli_cad.standards_export D:/records/completed.friskoli D:/records/completed.omex
```

输出文件必须不存在。导出器先校验原生 ZIP，再把原始字节作为 `design.friskoli` 保留，附根 `manifest.xml`、`checksums.json` 和机器可读 `loss-report.json`。清单声明 OMEX 本体、每一个文件及 MIME/COMBINE 格式 URI。不设置可执行 master，因为当前包没有可供外部工具执行的标准实验。

## SBOL Component 子集

选用 SBOL 时额外安装标准维护组织的 Python 实现，固定本次验证版本：

```powershell
python -m pip install sbol3==1.2.0.post0
python -m friskoli_cad.standards_export D:/records/completed.friskoli D:/records/components.omex --components D:/records/components.json
```

安装发布包时也可使用 `python -m pip install "friskoli-cad[standards]"`；可选依赖锁定本次已测的 `sbol3==1.2.0.post0`。没有此依赖仍可导出不含 SBOL 的 OMEX 和对应损失报告。

`components.json` 为对象数组。每个对象必须且只能包含 `identity`、`name`、`types`、`roles`、`derived_from`。identity 是用户持有或明确认可的唯一 HTTP(S) URI；types 是真实类型 ontology URI 数组，不能为空；roles 是明确的角色 URI 数组（可以为空）；derived_from 是非空来源 URI 数组。用户必须为生物身份和类型声明提供依据。

输出 `components.nt` 只映射这些显式的 Component 属性，附原始组件输入和单独 SBOL 损失报告。pySBOL3 逐次执行 Document.validate，序列化后回读再验证并对照身份、名称、类型、角色和来源；验证失败会拒绝生成。支持的是 pySBOL3 1.2 实现的 SBOL 3.0.1 核心属性，核对 3.1 规范后没有使用 3.1 新增能力。

表型 assembly、数值参数、模块名称或 chassis 描述**不会自动转换成 DNA、真实菌株或分子身份**。没有明确 Component 信息时，仅导出 OMEX 原生归档。当前子集不接收 sequence/feature/interaction，不制造空白序列；有这些额外字段会拒绝而非静默丢失。

## 明确未支持

SBML：尚无完成语义映射和外部数值对照的动力学子模型；不把空间 ABM 当成 ODE。

SED-ML：尚无经过外部复现验证的 model/algorithm 组合。

GenBank/FASTA：当前组件适配器不含序列导出，特别是没有序列时不能生成序列文件。

OMEX 的 loss-report 逐项给出上述格式的 `unsupported`；SBOL 报告列出 `exact` 和 `omitted` 的路径、原因与影响。未实现的映射不产生标准文件。容器/结构验证、语义回读和外部数值复现是分别记录的检查，前两项通过不表示第三项完成。

## 官方依据（2026-10-02 核查）

- [SBOL 3.1.0 正式规范](https://sbolstandard.org/docs/SBOL3.1.0.pdf)，6.1、6.2、6.4：Component 类型、来源及可选序列的语义；该子集只使用既有核心属性。
- [pySBOL3 官方代码](https://github.com/SynBioDex/pySBOL3) 与 [验证文档](https://pysbol3.readthedocs.io/en/latest/validation.html)：维护组织实现、validate 和回读机制。
- [COMBINE Archive v1 官方规范](https://raw.githubusercontent.com/combine-org/combine-specifications/main/specifications/files/omex.version-1.pdf)：ZIP、根 manifest、location/format/master；OMEX 有效不等于内部模型可执行。

本次没有声称通过独立 OMEX 第三方验证器或 SBML/SED-ML 求解器复现。SBOL 官方工具测试结果见 [交付检查](archive/verification/verification-n5-delivery.md)。

## 本地 HTTP 接口

`POST /api/design/standards`，Content-Type 为 `application/json`，请求：

```json
{"payload": "完整原生设计payload对象，此处仅示意", "format": "omex"}
```

`payload` 必须是与 `/api/design/export` 一致的完整对象，不能实际发送示意字符串。`format` 支持：

- `omex`：返回 `application/zip`，附件名 `design.omex`；包含原生包和损失报告。
- `loss-report`：返回对应 `application/json`，附件名 `loss-report.json`；只读，不提交求解任务。
- `sbol3`：必须另提供非空 `components` 数组，返回 `design-sbol3.zip`，包含 `components.nt`、原始组件声明和损失报告。归档记录对应原生包 SHA-256，但不以 SBOL 代替原生包。

`components` 也可用于 OMEX 和其损失报告，使用前述显式 Component 字段。缺省时请省略该字段，不传 null。未支持的 SBML/SED-ML/GenBank/FASTA 返回 422，JSON 包含 `error.code=standards.unsupported` 与 `loss_report`；不会产生假文件。SBOL 可选依赖缺失返回 503 `standards.dependency_missing`。

接口复用原生包完整校验与 64 MiB 请求上限；标准归档最大 64 MiB 压缩、128 MiB 展开。SBOL 输入额外限 256 个 Component/1 MiB，超限返回 413。`GET /api/capabilities` 的 `design.standards` 公布接口、可用格式、SBOL 条件和未支持原因。第三方 SBOL 校验是本地调用，无联网身份查询，也不上传设计。

2026-10-02 本地 HTTP 验证：`test_standards_http.py`、`test_wiki_standards.py` 与 `test_design_http.py` 共 33 项通过（39.88 s）。包含真实 HTTP ZIP/JSON 往返、原生 payload 保留、pySBOL3 回读、可选依赖缺失、拒绝未支持格式、请求/组件/输出资源上限及 capabilities；未启动求解任务。这项记录不替代外部模型求解器复现。
