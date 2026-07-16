# kb-memory Claude Code plugin

Claude Code plugin for a private kb-core / NAS-to-Qdrant long-term KB. It packages:

- SessionStart priming hook
- `kb-recall` skill
- MCP server registration for `kb_recall`, `kb_add`, and `kb_status`

## Requirements

This plugin does not bundle Python, models, or a virtualenv. Install and verify the kb-core
monorepo first, then expose it with `KB_HOME`:

```bash
export KB_HOME="$HOME/Developer/kb-core"
```

Optionally point the plugin at a specific Python runtime. It otherwise tries `.venv`, `venv`,
then `python3`:

```bash
export KB_MCP_PYTHON="$HOME/Developer/kb-core/.venv/bin/python"
```

If `KB_HOME` is not set, the SessionStart hook prints one line and exits 0:

```text
KB 未接通：設定 KB_HOME=<kb-core 路徑> 或跑 kb-core 安裝
```

## Install

Add this local plugin directory as a marketplace, then install the plugin:

```text
/plugin marketplace add ~/Developer/kb-core/plugins/claude-code
/plugin install kb-memory
```

## Notes

- Do not remove the existing manual `settings.json` hook until the plugin has been tested in a new Claude Code session.
- `.mcp.json` starts the packaged `kb.mcp` server through the plugin wrapper.
- The hook is intentionally fail-open: unavailable NAS/Qdrant/Ollama/config prints at most one setup hint or stays quiet, and never blocks session startup.
