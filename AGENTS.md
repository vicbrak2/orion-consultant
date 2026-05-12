## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Also use the local `integrated-ai-development` skill for Codex/Claude aligned work across `orion-consultant` and `llm-trade-nx`.

Rules:
- ALWAYS read graphify-out/GRAPH_REPORT.md before reading any source files, running grep/glob searches, or answering codebase questions. The graph is your primary map of the codebase.
- IF graphify-out/wiki/index.md EXISTS, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- For changes that affect both repos, query both graphs before editing.
- For architecture, API contracts, persistence, metrics, or deploy changes, automatically run `.\scripts\reindex.ps1` from `C:\Users\USER\Repo` before finalizing the task.
- In the final response, state whether Notion sync ran or was skipped (depends on `NOTION_TOKEN` and `NOTION_DB_ID`).
