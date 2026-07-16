# kb-core Quickstart

kb-core is the distributable package form of the NAS-to-Qdrant KB toolchain: a local Qdrant-backed long-term memory layer for coding agents.

## 1. Prepare config

```bash
cp config/kb_config.example.yaml config/kb_config.yaml
```

The default compose config uses:

- KB root: `./vault` bind-mounted to `/vault`
- Qdrant: compose service `qdrant:6333`
- API: `127.0.0.1:8377` on the host
- Hugging Face model cache: named volume `hf_cache`

For cloud classification, set an OpenAI-compatible key in the shell that starts Compose:

```bash
export DEEPSEEK_API_KEY=...
```

For fully local LLM fallback/audit/vision, start the Ollama profile:

```bash
docker compose --profile local-llm up -d
```

Then pull the models inside the Ollama service as needed:

```bash
docker compose exec ollama ollama pull qwen2.5:14b
docker compose exec ollama ollama pull gemma3:12b
```

## 2. Start

```bash
docker compose up -d
```

The embedding model is not baked into the image. First ingest downloads it into the `hf_cache` volume.

## 3. Add the first note

```bash
curl -s http://127.0.0.1:8377/add   -H 'Content-Type: application/json'   -d '{"content":"kb-core smoke note: Qdrant keeps durable agent memory.","category":"smoke","title":"kb-core smoke"}'
```

The API queues ingest in the background. To force a synchronous ingest from the container:

```bash
docker compose run --rm kb-api oneshot ingest
```

## 4. Recall

```bash
curl -s http://127.0.0.1:8377/recall   -H 'Content-Type: application/json'   -d '{"query":"durable agent memory","top_k":3}'
```

## 5. Health

```bash
curl -s http://127.0.0.1:8377/health
```

CLI equivalents:

```bash
docker compose run --rm kb-api oneshot add "a local note" --category inbox
docker compose run --rm kb-api oneshot recall "local note" --json
docker compose run --rm kb-api oneshot health --status
```

## 6. Agent integration

Point future MCP/Claude Code plugin layers at:

- add endpoint: `POST http://127.0.0.1:8377/add`
- recall endpoint: `POST http://127.0.0.1:8377/recall`
- health endpoint: `GET http://127.0.0.1:8377/health`

The detailed MCP/plugin packaging belongs to the P3 specs.
