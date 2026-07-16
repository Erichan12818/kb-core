# Release Checklist

The first private repository baseline is published as annotated tag `v0.1.0` at commit `c7555fd`.

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

## Confirmed maintainer decisions

- Product and repository name: `kb-core`.
- License: MIT, `Copyright (c) 2026 Eric Chan`.
- GitHub repository: `Erichan12818/kb-core`, private.
- Initial version: `v0.1.0`.
- Container image: not published.

## Decisions still required

- Review launch copy and select public channels.
- Complete a public-readiness review before changing visibility.
- Select a container registry before publishing an image.

## Final commands

```bash
cp config/kb_config.example.yaml config/kb_config.yaml
python3 -m compileall -q kb
docker compose config --quiet
claude plugin validate plugins/claude-code
git add -n .
```

Do not publish a GitHub Release, container image, or public repository without explicit maintainer approval.
