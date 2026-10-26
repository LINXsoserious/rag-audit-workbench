# 外部日志与后端接入

接入分为两种能力：**把已有记录转换成可审计证据**，以及**在撤回后真正重建并重新运行**。只有第二种能力经过独立验证后，才应开放执行按钮。首版可执行后端仅为 `native-bm25-v1`。

## 1. 导入已有记录

统一入口是 `load_input(content: bytes, filename: str) -> dict`。`.json` 接收协议 1.0 的 bundle，`.jsonl` 接收受支持的 M4/M5 历史记录布局。扩展名用于选择解析方式，不赋予数据额外信任。

```python
from pathlib import Path

from rag_audit.importers import load_input
from rag_audit.service import Workbench

input_path = Path("history.jsonl")
bundle = load_input(input_path.read_bytes(), input_path.name)
workbench = Workbench(".rag-audit/evidence.sqlite")
bundle_id = workbench.ingest(bundle, label=input_path.name)
print(workbench.audit(bundle_id))
```

受支持的历史记录会保留原有原文、跨度、来源支持、路径及运行信息，并明确标记 `execution="imported_history"`。只有日志中实际存在的内容才进入证据链。缺少原文、索引条件、候选分数、生成器或某个派生阶段时，必须保留覆盖不足，不能反向从最终回答猜造来源。

页面可以比较任意两条已经保存的运行记录。baseline / counterfactual 的变化只作为历史观察；导入操作没有重新执行原后端，首版页面和回放服务不据此声称已完成新的干预实验。

输入不是“任意 JSONL 自动兼容”。新布局应先按字段语义编写转换器，再以固定夹具测试。解析器拒绝无法识别的结构，并显示错误原因。

## 2. 给自己的文本建立可回放包

如果已有原始文本，又不要求复刻原系统，可以显式选择本项目的 CPU 检索后端，作为一个新实验：

```python
from rag_audit.backend import build_bundle
from rag_audit.schema import make_source
from rag_audit.service import Workbench

sources = [
    make_source("manual", "服务窗口每周一至周五开放。", revision="2026-10", trust="trusted"),
    make_source("temporary-note", "临时通知：周三下午暂停服务。", revision="1", trust="unreviewed"),
]
bundle = build_bundle(sources, ["周三下午服务窗口开放吗？"])
workbench = Workbench(".rag-audit/evidence.sqlite")
bundle_id = workbench.ingest(bundle, label="自己的原文 / 新 BM25 实验")
report = workbench.replay(bundle_id, ["temporary-note@1"])
print(report["validation"])
```

这验证新建 BM25 管线的来源撤回。它不复现原 GraphRAG 或原生成模型，也不继承旧实验的效果结论。评估答案、攻击构造标签应另存于评估材料，不能混进检索语料、候选排序或保护决策。

## 3. 映射外部管线的最小清单

| 外部数据 | 映射目标 | 无法提供时 |
| --- | --- | --- |
| 精确原文、逻辑 ID、版本、信任与状态 | `sources` | 缺原文时报告覆盖不足，不伪造文本 |
| 分块、抽取实体 / 关系、路径、摘要、缓存 | `artifacts` | 只声明实际提供的产物类型 |
| 内容由哪些文档支持 | `artifact.source_refs` | 空集合保留为未知，不视作无需来源 |
| 计算依赖哪些上游产物 | `artifact.dependencies` | 无法声明时不能声称依赖失效闭合 |
| 精确原文位置 | `start` / `end` | 必须转换为 Unicode 字符跨度；不猜位置 |
| 候选、分数、过滤、Top-K 与上下文预算 | `runs` 的候选与配置 | 不从最终上下文反推候选全集 |
| 实际提交上下文 | `context_ids` / `context_text` | 无真实提交记录就声明无法核验 |
| 生成输出、模型和配置 | `answer` / `answer_kind` / 元数据 | 规则决策与未生成结果单独标记 |

Microsoft GraphRAG 的文档、文本单元、实体、关系和社区报告可分别映射为来源与产物，但其 parquet 导入、真实抽取、实体合并、社区划分和摘要重建属于后续阶段。单纯建立引用边不能宣称实现该系统的撤回。

## 4. 增加一个真实回放后端

首版没有可热插拔的第三方执行器注册系统。增加后端需要在项目中实现并评审以下契约，不能只把 `backend` 名称改为 `native-bm25-v1` 来绕过能力检查：

缓存的 `metadata.cache_binding` 需包含 `input_sha256`、`processor_version`、`config_sha256`。首版只验证这些字段的形态；实际绑定即使字段完整仍为 `not_verified`，审计覆盖为不完整。要证明缓存可复用，还需要适配器独立重算输入与处理配置的绑定。

1. **构建。** 根据明确的活动源集合、固定处理配置和版本创建全部派生状态，返回协议 1.0 的 bundle。
2. **查询。** 记录原查询、候选排序、实际上下文、索引 / 图快照及所有会影响结果的配置。
3. **撤回。** 从源版本开始计算失效，重新计算受影响的索引、图、社区、摘要、缓存和候选；需要全量构建时如实使用全量构建。
4. **独立参照。** 另行从剩余源集合从零构建并查询。不能把同一个缓存结果复制两次后称为完整重建对照。
5. **等价性。** 比较稳定来源 / 产物 ID、候选排序、分数容差、上下文与适配器相关结构；时间戳或随机运行 ID 可以单独排除。
6. **能力与失败。** 清楚记录能审计、能重建和未执行的环节。依赖缺失、模型不可用、配置不兼容时返回明确错误，不静默改换算法。
7. **保护效果。** 用独立标签比较目标查询与正常查询，报告分母、空上下文和成本。随机生成器需要记录模型版本、采样参数、种子或重复运行差异。

最小后端验收应包含：4–6 个来源的撤回子集；联合撤回；同分排序；全局检索统计变化；隔离 / 已撤回来源残留；空知识库；配置与摄取顺序；多来源支持的保留；无法重建时明确失败。图后端的共享支持、实体合并与社区摘要需要额外专项验证，不能沿用 BM25 的通过结论。

## 5. 解释结果

“出现在候选中”“进入上下文”“撤回后结果改变”“答案符合参考事实”“来源恶意”是不同判断。适配器应分别提供证据，而不是从前一项自动推出后一项。候选集合只有人工选择的几个来源时，报告不得称其为全局最小原因集合。

公开适配器使用自编或有权分享的夹具，不把私有研究目录、服务数据库、密钥、模型缓存或完整外部语料打包到仓库。第三方代码需要保留许可证与来源；本项目首版实现不复制旧项目代码。
