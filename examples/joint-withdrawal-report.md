# RAG 源撤回审计报告

执行状态：executed
基线证据包：a8638d58349e0ef1cf60fa1bf0b6d93e6b09353b9c0a548ceb2c79d0f72ec96a
报告 ID：119fb61137d4b783f998e59a90b2d9ae707036eb5249d2ba08e79a5630075142

本报告描述指定输入、配置和干预下的系统变化。它不单独证明来源恶意或识别攻击者。

## 撤回来源

- copied-note-a@1
- copied-note-b@1

## 完整重建对照

一致：True

### 查询：星桥项目 报销 截止日 deadline

排序发生变化：True
完整重建一致：True
撤回前上下文：chunk:b9c28217fc8a2f6d6124887213632a94e885be2550b4c9fac3b8fbad76d48348, chunk:f16b9b50c9f4777b20aed772d6f9cc66e17b81b5296535048e48b857af9f651e
撤回后上下文：chunk:949a53fb4ace7d09efec7d0ae5223868b47f65430e18632c14f21c0694ed565d, chunk:7311ca15663a626d2710dac6281fbf66f239a8517f22e6c59150f959629ec5bf

未启用生成模型；本项只报告检索与上下文变化，不计算答案恢复。

### 查询：海岬档案 archive retention

排序发生变化：False
完整重建一致：True
撤回前上下文：chunk:e31ce8c77b6d04978e2786d5192881bcdc92721f400b9ce5c01f248d280915c1
撤回后上下文：chunk:e31ce8c77b6d04978e2786d5192881bcdc92721f400b9ce5c01f248d280915c1

未启用生成模型；本项只报告检索与上下文变化，不计算答案恢复。

### 查询：zzzxqvnohit

排序发生变化：False
完整重建一致：True
撤回前上下文：
撤回后上下文：

未启用生成模型；本项只报告检索与上下文变化，不计算答案恢复。

## 失效传播与成本

```json
{
  "invalidation": {
    "affected_artifact_ids": [
      "bm25_index:1441b654b69c597b3fcdacb26cc2e15ea00cbd92238fd92c7cab9cb9ba9891ab",
      "chunk:b9c28217fc8a2f6d6124887213632a94e885be2550b4c9fac3b8fbad76d48348",
      "chunk:f16b9b50c9f4777b20aed772d6f9cc66e17b81b5296535048e48b857af9f651e",
      "context:run:24e87012a019f158bd8a8c3e05ee2903c52bec101313406123b5447bd543b02a",
      "context:run:748ae2db91af4925eedf5338bb456fc0fa1d042df239edce6a69ec17a1faa9b6",
      "context:run:b08bd2dc575df1b54653794fd03b899b687fd0236e58b5b8d53565c5b802a7c1",
      "source:copied-note-a@1",
      "source:copied-note-b@1"
    ],
    "recomputed": {
      "contexts": true,
      "corpus_statistics": true,
      "full_reference_rechunk": true,
      "index": true,
      "ranking": true
    },
    "removed_artifact_ids": [
      "bm25_index:1441b654b69c597b3fcdacb26cc2e15ea00cbd92238fd92c7cab9cb9ba9891ab",
      "chunk:b9c28217fc8a2f6d6124887213632a94e885be2550b4c9fac3b8fbad76d48348",
      "chunk:f16b9b50c9f4777b20aed772d6f9cc66e17b81b5296535048e48b857af9f651e",
      "context:run:24e87012a019f158bd8a8c3e05ee2903c52bec101313406123b5447bd543b02a",
      "context:run:748ae2db91af4925eedf5338bb456fc0fa1d042df239edce6a69ec17a1faa9b6",
      "context:run:b08bd2dc575df1b54653794fd03b899b687fd0236e58b5b8d53565c5b802a7c1",
      "source:copied-note-a@1",
      "source:copied-note-b@1"
    ],
    "retained_source_refs": [
      "archive@1",
      "policy@1",
      "self-claim@1"
    ]
  },
  "metrics": {
    "answer_metrics_available": false,
    "context_changed_count": 1,
    "empty_context_count": 1,
    "full_rebuild_seconds": 0.0009068999788723886,
    "generation_calls": 0,
    "incremental_seconds": 0.0007829000242054462,
    "query_count": 3,
    "ranking_changed_count": 1,
    "removed_chunk_count": 2,
    "retained_chunk_count": 3,
    "total_seconds": 0.0032221999717876315,
    "withdrawn_source_count": 2
  }
}
```

完整输入、精确参数和新证据包请查同名 JSON 报告。哈希仅支持一致性检查，不证明原始记录真实。
