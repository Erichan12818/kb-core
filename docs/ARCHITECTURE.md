# Architecture

kb-core is a retrieval-first memory system for coding agents. It keeps durable knowledge in a local vault and Qdrant collection, then gives agents direct evidence at session start or on demand.

## Data Flow

1. Capture writes raw text, URLs, or files under `kb_root/raw_files`.
2. Ingest chunks content, skips likely secrets, embeds dense and sparse vectors, and writes metadata into Qdrant.
3. Recall performs hybrid retrieval plus lightweight reranking, then returns source chunks and metadata without summarizing them away.
4. The catalog loops maintain a human-readable map of the corpus.

## Design Philosophy

kb-core separates evidence retrieval from answer generation. It stores raw source chunks, searchable metadata, and a navigable catalog, then gives those materials to a stronger coding agent without pre-chewing them through a weaker summarizer.

The three-loop design keeps the system useful as the corpus grows:

- Loop 1 adds new knowledge incrementally instead of rebuilding the whole map by hand.
- Loop 2 controls taxonomy entropy with guarded audit proposals and small automatic cleanup rules.
- Loop 3 renders the current state into plain Markdown so humans and session-start hooks can inspect it.

## Loop 1: Incremental Indexing

Loop 1 runs after ingest. It scans Qdrant payloads, groups chunks back into source files, classifies new or changed files, computes related-file links, and writes `state/INDEX.json`.

The loop is additive by default. It updates title, summary, topics, relation hints, and review metadata, but leaves broader taxonomy reshaping to the audit loop.

## Loop 2: Tiered Taxonomy Audit

Loop 2 reviews the accumulated index and taxonomy. It calculates overloaded categories, single-use tags, orphan files, stale review dates, and high-similarity groups.

Low-risk tag operations can be applied automatically with guardrails:

- tag-only changes, never category moves;
- both tags must already exist;
- retired tags must have low usage;
- each audit has a small maximum number of automatic merges;
- forbidden tags from `taxonomy_policy.md` can be removed.

Higher-risk category splits, category merges, and duplicate-file decisions are reported for human review.

## Loop 3: Catalog Rendering

Loop 3 renders `catalog/KB_CATALOG.md` and per-category MOC files from `INDEX.json` and `TAXONOMY.json`. It does not call an LLM. Its job is to make the memory graph inspectable in normal Markdown tools and useful for session priming.

## Consumption Surfaces

- CLI and HTTP API for smoke tests, scripts, and simple automation.
- MCP server for agent tools such as recall, add, and health.
- Claude Code plugin for SessionStart priming and on-demand recall.
- Markdown catalog for humans and project-level context.

## Failure Model

kb-core is designed to fail open for agent workflows. Missing NAS mounts, unavailable Qdrant, absent Ollama, notification failures, or missing optional cloud keys should produce a short warning or empty result rather than block the caller.
