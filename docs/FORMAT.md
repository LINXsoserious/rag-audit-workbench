# 证据协议 1.0

输入采用 UTF-8 JSON，JSONL 则先经导入器转换为同一结构。单次输入上限 25 MiB；不接受 NaN、Infinity 或非字符串对象键。结构校验与内容审计是两步：格式合法不代表证据完整或可信。

## Bundle

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `schema_version` | 字符串 | 固定为 `"1.0"` |
| `bundle_id` | 字符串 | 由规范化内容生成的稳定包标识；推荐交给构建器 / 存储层生成 |
| `sources` | 数组 | 所有可提供的来源版本 |
| `artifacts` | 数组 | 分块、索引或历史管线提供的其他派生产物 |
| `runs` | 数组 | 查询条件、选择过程和实际上下文 |
| `metadata` | 对象 | 后端配置、导入来源、接入能力与覆盖说明 |

规范化 JSON 使用键排序、UTF-8、无多余空白，并禁止非有限数字。原文 SHA-256 对 `text.encode("utf-8")` 计算，不先去空格、改换行或 Unicode 规范化。包与文本哈希承担不同作用。

推荐用构建器生成完整的可回放包，而非手写索引与哈希：

```python
import json
from pathlib import Path

from rag_audit.backend import build_bundle
from rag_audit.schema import make_source

sources = [
    make_source("guide", "借阅规则：图书可借阅三十天。", revision="1", title="借阅规则", trust="trusted"),
    make_source("notice", "馆内通知：闭馆前十五分钟停止办理借书。", revision="1", title="馆内通知"),
]
bundle = build_bundle(sources, ["图书可以借阅多少天？"])
Path("my-bundle.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
```

## Source：不可覆盖的来源版本

| 字段 | 类型 / 取值 | 规则 |
| --- | --- | --- |
| `source_id` | 非空字符串 | 逻辑来源标识；不含 `@` |
| `revision` | 非空字符串 | 内容版本；不含 `@` |
| `title` | 字符串 | 展示标题；默认使用 `source_id` |
| `text` | 字符串，可空 | 保留原始文本 |
| `sha256` | 字符串 | 原文 UTF-8 字节的 SHA-256 十六进制摘要 |
| `trust` | `trusted` / `unreviewed` / `untrusted` | 来自外部元数据；默认 `unreviewed` |
| `status` | `active` / `revoked` / `quarantined` | 默认 `active`；检索只采用允许的活动来源 |

稳定引用为 `source_id@revision`，如 `guide@1`。不同内容必须使用不同版本；相同来源版本出现内容冲突时，存储层拒绝覆盖。信任是外部声明，不是内容真实性证明。审计不会根据正文中的权限指令修改 `trust`。

## Artifact：内容支持与计算依赖

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `artifact_id` | 非空字符串 | 包内唯一产物标识 |
| `kind` | 非空字符串 | 如 `chunk`、`index`；历史适配器可提供图或路径等类型 |
| `source_refs` | 字符串数组 | 支持该产物的来源版本集合 |
| `dependencies` | 字符串数组 | 该产物依赖的其他 `artifact_id` |
| `start` / `end` | 整数，可选 | 原文区间 `[start, end)` |
| `text` | 字符串，可选 | 对应原文切片或派生文本 |
| `metadata` | 对象，可选 | 处理配置、快照和导入证据 |

来源支持和计算依赖不是同一关系。支持引用缺失、依赖断裂、循环、来源状态不匹配等问题由审计器报告；不要为了通过校验而补造来源。

原生后端的产物类型为 `source`、`chunk`、`bm25_index` 和 `context`。上下文产物的 `metadata.content_dependencies` 记录实际内容依赖，`metadata.selection_dependencies` 记录影响选择的索引依赖；二者均属于 `dependencies`。索引依赖全库分块，但没有进入上下文的来源不能因此被标作实际提交内容。历史边可用 `metadata.support_spans` 数组记录多来源支持；每项包含 `source_ref`、`start`、`end`、`text`。

外部缓存可声明 `metadata.cache_binding={"input_sha256": "...", "processor_version": "...", "config_sha256": "..."}`。首版只核验字段形态，不重算任意外部处理器的输入绑定。即使字段齐全，也必须显示 `not_verified` 且 `coverage.complete=false`，不能宣称缓存复用已经通过验证；没有外部缓存的原生后端不受此项限制影响。

跨度按 **Python Unicode 字符** 计数。字符串 `A中B` 的中间字符跨度是 `[1, 2)`；不使用 UTF-8 字节偏移，也不使用 JavaScript 的 UTF-16 长度。必须满足 `0 <= start <= end <= len(source.text)`。只有绑定精确原文的跨度才可定位；若文本经过清洗或归一化，应记录新版本及相应映射，不能沿用旧位置。

## Run：执行与选择过程

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `run_id` | 非空字符串 | 运行标识 |
| `execution` | 字符串 | 原生执行状态或 `imported_history`；保留适配器实际状态 |
| `backend` | 字符串 | 首版可执行后端为 `native-bm25-v1` |
| `query` | 字符串 | 查询原文 |
| `config` | 对象 | 分词、分块、排序与上下文预算配置 |
| `index_sha256` | 字符串 | 查询时采用的索引快照哈希 |
| `candidates` | 对象数组 | 实际候选及其 ID、分数、来源等已提供信息 |
| `context_ids` | 字符串数组 | 实际选入上下文的产物 ID，保留顺序 |
| `context_text` | 字符串 | 真正提交的上下文；原生后端用双换行连接分块 |
| `answer` | 任意 JSON / `null` | 记录的输出；原生首版固定为 `null` |
| `answer_kind` | 字符串 | 首版为 `not_generated`；规则决策不能标作 LLM 生成 |

`native-bm25-v1` 默认配置为 `chunk_size=400`、`chunk_overlap=40`、`top_k=4`、`context_budget=1600`、`k1=1.5`、`b=0.75` 与 `tokenizer="unicode-cjk-bigram-v1"`。上下文预算按字符计算并包含分块间双换行。包级配置保存在 `metadata.backend_config`；不要手改运行配置后继续引用旧索引哈希。候选项包括 `artifact_id`、`source_ref`、从 1 开始的 `rank` 与 `score`；原生运行的 `execution="executed"`。

候选集合、选入上下文和生成输出是三个层次。历史适配器没有提供的字段应保持缺失或明确的不足状态；空值不能被解释成已证明没有影响。

## 审计结果

`audit_bundle(bundle)` 返回：

- `findings`：带 `code`、`severity`、`message` 与证据位置的问题记录。
- `counts`：严重程度与问题数量统计。
- `coverage`：当前输入及适配器能够检查的范围、历史状态与不足信息。
- `passed`：本次已执行的审计是否通过；不表示所有安全属性均已验证。

结构校验不会替代哈希、来源引用、跨度、上下文与依赖审计。发现完整性错误时，应保留原始输入并修复或重新采集，不能伪造匹配证据后继续回放。

## 撤回报告

`Workbench.replay(bundle_id, withdrawn_refs, queries=None)` 自动保存报告。传入版本引用列表，可联合撤回；不传新查询时沿用原查询。历史导入和不受支持的后端不得标作本次执行。

| 字段 | 含义 |
| --- | --- |
| `report_id` / `baseline_bundle_id` | 已保存报告与基线的标识 |
| `withdrawn_refs` | 实际请求撤回的来源版本集合 |
| `execution` | 回放执行状态 |
| `replayed_bundle` | 处理后的来源、派生产物和新运行记录 |
| `queries` | 各查询的 `before`、`after`、`reference`、`equivalent`、`context_added`、`context_removed`、`ranking_changed` |
| `validation.equivalent_to_full_rebuild` | 是否与独立完整重建的规范化结果一致 |
| `invalidation` | 失效传播与重建范围 |
| `metrics` | 检索、上下文和成本指标；未接 LLM 时无答案恢复率 |
| `audit_before` / `audit_after` | 服务附带的干预前后审计结果 |

`reference` 是相同剩余来源集合从零构建后的查询结果，不是外部正确答案。完整重建一致仅支持该后端、配置和输入范围内的机械正确性；不证明结果内容正确、来源恶意或现实攻击成立。

## 账本与导出

SQLite 保存来源版本、证据包、报告及追加事件。`verify_ledger()` 检查规范化载荷、引用哈希和事件链；可保存外部检查点加强后续复核。哈希链不证明原始采集真实，也不能抵抗数据库和全部检查点被同步重写。

JSON 导出包含机器复核所需的完整报告与证据快照，Markdown 用于阅读，仍可能包含查询与来源引用。“分享摘要”由 `report.sharing_summary(report)` 生成，只保留哈希、白名单数值和验证结果，不含原文、查询、标签或来源 ID，且 `replayable=false`。分享摘要不能完整回放。另行分享脱敏副本时应说明删除了什么；脱敏改变输入内容，不能继续把副本视为原始包的完整回放材料。
