# kb-core Quickstart

kb-core is the distributable package form of the NAS-to-Qdrant KB toolchain: a local Qdrant-backed long-term memory layer for coding agents.

This page covers the Compose deployment, which is what you want for a NAS or a
machine that stays on.

**If you just want to try it, don't start here.** Download the desktop build
instead — no config, no Docker, no Python, and it opens its own UI:

- [macOS (Apple silicon)](https://github.com/Erichan12818/kb-core/releases/latest/download/kb-core-macos-arm64.zip)
- [Linux (x86-64)](https://github.com/Erichan12818/kb-core/releases/latest/download/kb-core-linux-x64.tar.gz)

Unpack it, run it, and it serves <http://127.0.0.1:8377/ui>. First launch
downloads a ~2.3GB embedding model; progress goes to
`~/Library/Application Support/kb-core/logs/kb-core.log` on macOS. The macOS
build is signed and notarized as of v0.2.2, so opening it is the normal
double-click-then-confirm flow — no extra steps.

Capture and search need no configuration at all. To turn on the optional Ask
tab, use the **Settings** tab in the app: fill in a provider URL, a model, and
an API key, and tick "Turn on the Ask tab". The key is written to a `0600` file
in your vault (`state/secrets.env`) rather than into the config file, and takes
effect without a restart. Editing `kb_config.yaml` by hand still works and is
what the rest of this page describes.

Settings also covers where documents come from and go:

- **Extra folders to read** — point at existing folders, including external
  drives, instead of copying files into the vault. They are read strictly
  read-only; nothing is moved or modified. A folder that is not mounted is
  skipped, and what was already indexed from it is kept rather than deleted.
- **Save new notes to** — where notes captured through `kb_add` (including
  from a coding agent over MCP) are written. Wherever it points is always read
  back, so those notes stay indexed and catalogued.
- **Scan for new documents** — index now instead of waiting for the nightly
  run. Output from the run is shown in the panel.

Readable formats: `.txt` `.md` `.pdf` `.docx` `.xlsx` `.pptx` `.csv` `.json`
`.yaml`. The legacy binary `.doc`, `.xls` and `.ppt` are not supported. Files
above `ingest.max_file_mb` (25MB by default) are skipped.

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

The **Ask** tab then becomes usable; until a chat role is configured it shows
setup instructions instead of a question box. Every answer is generated only
from the excerpts retrieved for that question, and each one is shown beneath
the reply with its citation number. An answer that cites nothing is flagged as
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
