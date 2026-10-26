"""Human-facing CLI messages; protocol names and JSON keys are not translated."""
from __future__ import annotations

CLI_MESSAGES = {
    "en": {
        "description": "Offline RAG evidence auditing and source-withdrawal verification",
        "usage": "usage: ", "positional": "positional arguments", "options": "options",
        "help": "Show this help message and exit", "commands": "commands",
        "db": "SQLite evidence store", "lang": "Interface and Markdown report language (default: zh)",
        "output": "Write to this file instead of standard output", "force": "Replace an existing export file",
        "demo": "Create the replayable built-in case", "inspect": "Audit JSON / M4-M5 JSONL without storing it",
        "import": "Import evidence without overwriting existing revisions", "list": "List local cases",
        "replay": "Execute withdrawal and replay from a file or stored bundle_id",
        "report": "Export a stored intervention report", "export": "Export a full evidence bundle including source text",
        "verify": "Verify stored objects and the event chain", "benchmark": "Run the synthetic controlled evaluation",
        "input": "Input evidence path (replay also accepts a stored bundle_id)", "label": "Optional stored case label",
        "reports": "List reports instead of cases", "withdraw": "source_id@revision; may be repeated",
        "query": "Replace the replay query set; may be repeated", "format": "Export format",
        "report_id": "Stored report identifier", "bundle_id": "Stored bundle identifier",
        "checkpoint": "External event-chain checkpoint to verify",
        "exists": "Output already exists: {path}; use --force to replace it",
        "protected": "Output must not overwrite the evidence database, its sidecars, or the input evidence",
        "too_large": "Input exceeds 25 MiB", "error": "Error: {message}", "parse_error": "{prog}: error: {message}\n",
    },
    "zh": {
        "description": "离线 RAG 证据审计与来源撤回验证",
        "usage": "用法：", "positional": "位置参数", "options": "选项",
        "help": "显示帮助并退出", "commands": "命令",
        "db": "SQLite 证据库", "lang": "界面与 Markdown 报告语言（默认：zh）",
        "output": "写入该文件，替代标准输出", "force": "替换已有的导出文件",
        "demo": "创建可回放的内置案件", "inspect": "审计 JSON / M4-M5 JSONL，不写数据库",
        "import": "不可覆盖地导入证据", "list": "列出本地案件",
        "replay": "从文件或已存 bundle_id 实际撤回并回放",
        "report": "导出已存干预报告", "export": "导出完整证据包，包含原文",
        "verify": "验证存储对象及事件链", "benchmark": "运行自编受控验收案例",
        "input": "证据文件路径（replay 也接受已存 bundle_id）", "label": "可选的案件标签",
        "reports": "列出报告而非案件", "withdraw": "source_id@revision，可重复指定",
        "query": "替换回放查询集，可重复指定", "format": "导出格式",
        "report_id": "已存报告标识", "bundle_id": "已存证据包标识",
        "checkpoint": "用于验证的外部事件链检查点",
        "exists": "输出已存在：{path}；使用 --force 替换",
        "protected": "输出不能覆盖证据数据库、其 sidecar 或输入证据",
        "too_large": "输入超过 25 MiB", "error": "错误：{message}", "parse_error": "{prog}：错误：{message}\n",
    },
}


def cli_text(lang: str, key: str, **values: object) -> str:
    return CLI_MESSAGES["en" if lang == "en" else "zh"][key].format(**values)
