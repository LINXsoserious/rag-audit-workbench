# 首版验证与复核

本文件记录工程验证范围。受控案例是自编夹具，不是外部榜单、真实攻击发现率或 LLM 答案恢复结果。

## v0.2 bilingual verification

2026-10-26: the complete Windows / Python 3.12.3 suite passed **172 tests**. Localization checks cover English UI/replay, Chinese-English state retention, translated frontend option identities and selected strings, CLI language placement/help/errors, Markdown language selection and stable machine JSON. The actual browser workflow loaded a Chinese case, switched to English, correctly refreshed the case/full-source labels, and executed a joint withdrawal matching an independent full rebuild. Switching back retained the saved report. Original source text and queries were kept unchanged. See [the English workflow screenshot](assets/overview-en.jpg).

The following original verification record describes v0.1; later CI results apply to their exact commits.

2026-10-26 在 Windows / Python 3.12.3 本地完整运行：**157 passed**（含 9 项页面测试）。wheel 与 sdist 构建成功；wheel 在独立空虚拟环境中安装后，示例、审计、联合撤回、账本验证和受控验收均运行成功。页面检查使用 Streamlit 1.66.1。

## 本地复核方法

```powershell
python -m pip install -e ".[ui,dev]"
python -m pytest -q
python -m build
python -m rag_audit benchmark --output controlled-evaluation.json
```

`benchmark` 的退出码检查全部机械验收条件，而不只看重建等价。查看 [可复核结果](../examples/controlled-evaluation.json) 和 [完整干预报告](../examples/joint-withdrawal-report.json)。重新生成公开案例可运行 `python scripts/generate_examples.py --force`；它只覆盖脚本中列明的 7 个示例文件。

## 验证内容

- 对 5 个来源的全部 31 个非空撤回子集，核验增量失效路径与从原文重新分块的完整构建结果。
- 复制便笺的单源撤回仍保留错误证据；联合撤回后采用预期证据，同时保留正常档案查询。
- 移除全部来源作为负对照，不能计为有效恢复；无匹配词项查询仅验证空上下文，不验证语义拒答能力。
- 同样移除两个来源时穷举全部 10 个组合，公开每组结果，避免挑选单个随机对照。
- 正常来源修订可以合法改变事实，不能简单把恢复旧结果视为正确。
- 未匹配查询、未进候选的来源也能通过全局长度统计改变其他候选的排名；这证明给定参考后端的机制，不自动推广为 GraphRAG 实验结论。
- 记录、分数、配置、上下文和索引篡改会被核验；删除 native 标记不能绕过对应检查。
- 数据库、sidecar、输入证据以及硬链接别名不能被 CLI 强制导出覆盖。
- 删除来源登记行会导致链校验失败，后续写入不能借此覆盖源版本。
- 历史缺失上下文或来源时保留覆盖不足；内容依赖与选择依赖断裂均能定位。
- 缓存绑定仅核验字段形态，完整字段仍是未验证；首版不把它当成已验证缓存复用。
- 页面经过真实操作与 Streamlit AppTest 检查，包含示例、错误导入、问题定位、联合撤回、新查询报告切换、分享摘要和大量问题分页。

所有真实生成调用次数为 0，答案恢复与自动投毒检测误报率均为 `null`。手动指定撤回来源不能当成自动检测的成功率。

## 真实格式兼容性

另外在作者本地以只读方式导入完整的既有研究记录，未重新运行旧实验，未将其私有原文打包：

| 记录 | 导入来源数 | 产物数 | 运行数 | 完整性 error | 覆盖边界 |
| --- | ---: | ---: | ---: | ---: | --- |
| M4 | 2252 | 9006 | 200 | 0 | 原日志未记录实际上下文，200 项警告 |
| M5 | 2856 | 20111 | 504 | 0 | 基线缺来源与跨度，且未记录实际上下文；17094 项警告 |

这些计数验证导入器处理当前日志布局的能力，不证明原实验效果或记录真实。公开仓库仅提供独立编写的同格式小夹具。大量问题按级别、类型筛选并每页显示 25 条。

## CLI 状态与事件链

退出码 `0` 表示当前操作成功且要求的检查通过，`1` 表示检查失败或审计覆盖不足，`2` 表示输入或执行错误。`import` 即使返回 `1` 也保留证据，便于继续取证。`--force` 只能替换普通导出文件，不能绕过证据路径保护。

事件链应配合独立保存的外部检查点：`rag-audit verify --expected-checkpoint HASH` 检查当前链尾是否与该检查点完全相同；后续合法追加后链尾自然变化，需要保存新检查点。这不是对旧检查点“仍为历史前缀”的证明接口。没有独立锚点时，整个数据库被完全改写可能无法仅凭本地链检测。

CI 配置包含 Ubuntu / Python 3.11 与 Windows / Python 3.12；具体成功状态应以仓库对应提交的 Actions 结果为准。
