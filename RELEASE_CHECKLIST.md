# Release Checklist

This directory is a private release staging area. It has no Git remote and no commit yet.

## Completed

- Monorepo layout with core package and `plugins/claude-code`.
- MIT License retained.
- Private vault, local config, logs, JSONL, environment files, and bytecode excluded.
- Generic packaged MCP server: `python -m kb.mcp` / `kb mcp`.
- Claude Code plugin manifest validation.
- Python compile validation.
- Compose configuration validation.
- MCP initialize and tools-list handshake.
- Private path, LAN address, and credential-pattern scan.

## Maintainer decisions before publication

- Confirm the public repository and product name (`kb-core` is the working name).
- Confirm `Copyright (c) 2026 Eric Chan` in the MIT License.
- Choose the GitHub owner/organization and repository visibility.
- Decide whether the first release is `v0.1.0` and whether to publish a container image.
- Review launch copy and select channels.

## Final commands

```bash
cp config/kb_config.example.yaml config/kb_config.yaml
python3 -m compileall -q kb
docker compose config --quiet
claude plugin validate plugins/claude-code
git add -n .
```

Do not add a remote, commit, push, or publish until the maintainer decisions above are confirmed.
