# Agent handoff

This file is the durable coordination record for Codex, Claude Code, and human maintainers. Update it whenever a branch materially changes the repository state.

## Current state

- Repository: `Erichan12818/kb-core` (private).
- Default branch: `main`.
- Baseline release: annotated tag `v0.1.0` at commit `c7555fd`.
- Active integration branch: `agent/add-ci-and-claude-handoff`.
- License: MIT, Copyright (c) 2026 Eric Chan.
- Container image and GitHub Release: not published.

## Completed baseline

- Core Python package, unified `bin/kb` CLI, HTTP API, worker, and stdio MCP server.
- Qdrant-backed hybrid recall with evolving catalog and taxonomy workflows.
- Claude Code plugin with MCP registration, session-start priming, and recall skill.
- Generic configuration examples and Docker Compose deployment.
- Private vault, local config, logs, JSONL, environment files, and caches excluded from Git.
- Local validation of Python compile, JSON, shell scripts, Compose, Claude plugin manifest, MCP initialize/tools-list, fail-open session priming, and sensitive-data patterns.

## Integration work in progress

This branch adds:

- `CLAUDE.md` so Claude Code automatically loads repository-specific working rules.
- This shared handoff record.
- A dependency-light MCP protocol test.
- GitHub Actions CI for compile, unit smoke tests, metadata, shell syntax, and Compose validation.

## Validation on this branch

- Python compile: passed.
- MCP protocol unit tests: 3 passed.
- Plugin JSON, YAML, and shell syntax: passed.
- Docker Compose configuration: passed without starting the daemon.
- `claude plugin validate plugins/claude-code`: passed.
- Git whitespace and sensitive-data pattern scans: passed.
- Claude Code CLI `2.1.126` authentication: Claude.ai Pro login confirmed.
- Claude Code read-only review: deferred because the subscription limit was reached before inference; the CLI reported a 23:50 Asia/Hong_Kong reset and zero token usage.

## Decisions

- Git history and repository documents are the cross-agent synchronization layer; private GUI transcripts are not copied into the repository.
- `main` remains the stable branch. Changes should arrive through focused pull requests.
- CI must not need live Qdrant, model downloads, API keys, or a private vault.
- Public visibility, container publishing, and a GitHub Release require a separate readiness decision.

## Next steps

1. Review and merge the CI/handoff pull request after checks pass.
2. Configure branch protection for `main`, requiring the CI `verify` job and pull requests.
3. Prepare a draft GitHub Release for `v0.1.0` without publishing it.
4. Perform a separate public-readiness review before changing repository visibility.

## Known blockers

- Branch protection cannot require the new CI check reliably until its workflow exists on the default branch.
- No container registry or release channel has been approved.
- Claude Code can resume its independent review after the current subscription window resets; no API or repository fix is required.
