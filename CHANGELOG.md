# Changelog

## 0.2.0 — 2026-10-26

- Add complete English and Simplified Chinese READMEs with language links.
- Add a persistent interface language selector; translate application controls, explanations, display labels, and downloads without translating source evidence or queries.
- Preserve cases, selected sources, withdrawal sets, query edits, and saved replay reports when switching languages. Filter and artifact option identities are independent of displayed text.
- Add CLI `--lang en|zh` before commands or after subcommands, localized help and error prefixes, and selectable Markdown report language.
- Keep JSON and sharing-summary schemas, evidence identities, replay behavior, exit codes, and database/input overwrite protections stable.
- Add bilingual workflow, state-retention, catalog-parity, and machine-protocol regression tests.

GraphRAG rebuilding, real LLM generation, and answer recovery remain unimplemented.

## 0.1.0 — 2026-10-26

- Initial offline evidence auditing, native BM25 source-withdrawal replay, independent full-rebuild comparison, SQLite ledger, CLI, and local interface.
