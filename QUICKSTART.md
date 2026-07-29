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

To use a cloud model for classification (and for the optional Ask tab), put the
key in a `.env` file next to `compose.yaml`. Compose passes that file into the
containers, and `.env` is git-ignored:

```bash
echo 'DEEPSEEK_API_KEY=sk-...' > .env
```

The variable name has to match the `key_env` of the provider you configured in
`kb_config.yaml`, so any OpenAI-compatible service works — just name it
accordingly.

Without a key, ingest still indexes your files and search still works; you just
get no generated titles or topic tags.

### Optional: ask questions in the UI

kb-core is retrieval-only by default. If you want it to answer in prose instead
of only showing excerpts, add a `chat` role under `llm.roles` in
`kb_config.yaml`:

```yaml
llm:
  roles:
    chat:
      provider: cloud
      model: deepseek-v4-flash
```

An **Ask** tab then appears in the UI. Every answer is generated only from the
excerpts retrieved for that question, and each one is shown beneath the reply
with its citation number. An answer that cites nothing is flagged as
unsupported rather than presented as fact.

If a coding agent is already querying this knowledge base, leave this off — the
agent is better served by raw excerpts than by a summary of them.

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

The local web interface is available at:

```text
http://127.0.0.1:8377/ui
```

It provides retrieval-only search, pending-proposal review, and health status.
It displays raw excerpts and source paths; it does not generate answers.

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

## Network security

The host port is intentionally bound to `127.0.0.1` by default. kb-core v0.1
does not provide built-in authentication suitable for exposing the UI directly
to a LAN or the public internet. If LAN access is required, keep kb-core behind
your own authenticated reverse proxy and configure that proxy's transport and
access controls before changing the bind address.
