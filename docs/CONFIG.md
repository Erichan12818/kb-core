# Configuration

kb-core loads configuration from `KB_CONFIG` when set. Otherwise it looks for `kb/kb_config.yaml` beside the Python package. Docker Compose sets `KB_CONFIG=/config/kb_config.yaml` and `KB_ROOT=/vault`.

Copy `config/kb_config.example.yaml` to `config/kb_config.yaml` before running Compose.

## Fields

| Field | Purpose | Default |
| --- | --- | --- |
| `kb_root` | Root directory for vault data, raw files, state, catalog, notes, trash, and `taxonomy_policy.md`. | `./vault` |
| `qdrant.host` | Qdrant host name or IP. Use `qdrant` inside Compose and `localhost` for a local process. | `localhost` |
| `qdrant.port` | Qdrant HTTP port. | `6333` |
| `qdrant.collection` | Hybrid collection name. | `kb_hybrid_v2` |
| `qdrant.timeout_batch` | Longer Qdrant timeout for ingest, indexing, and batch jobs. | `60` |
| `qdrant.timeout_interactive` | Shorter timeout for recall and health paths. | `15` |
| `embedding.dense_model` | Dense embedding model used by ingest and recall. | `intfloat/multilingual-e5-large` |
| `embedding.sparse_model` | Sparse embedding model name for hybrid retrieval. | `Qdrant/bm25` |
| `embedding.dense_dim` | Dense vector size. Must match the chosen dense model and collection. | `1024` |
| `chunking.size` | Text chunk size for ingest. | `500` |
| `chunking.overlap` | Chunk overlap for ingest. | `50` |
| `llm.providers.<name>.type` | Provider adapter. Supported values are `ollama` and `openai_compat`. | varies |
| `llm.providers.<name>.url` | Ollama base URL for `ollama` providers. | `http://localhost:11434` |
| `llm.providers.<name>.base_url` | OpenAI-compatible API base URL. | `https://api.deepseek.com/v1` |
| `llm.providers.<name>.key_env` | Environment variable that contains the API key. | `DEEPSEEK_API_KEY` |
| `llm.providers.<name>.key_env_file` | Optional dotenv-style file to read before checking the environment. Empty disables file loading. | empty |
| `llm.providers.<name>.extra_body` | Extra request body sent to compatible APIs. | provider-specific |
| `llm.roles.classify.provider` | Provider used for normal classification. | `cloud` |
| `llm.roles.classify.model` | Model used for normal classification. | `deepseek-v4-flash` |
| `llm.roles.classify.fallback.provider` | Provider used if classification must stay local or cloud parsing fails. | `local` |
| `llm.roles.classify.fallback.model` | Local fallback classification model. | `qwen2.5:14b` |
| `llm.roles.audit.provider` | Provider used by taxonomy audit. | `local` |
| `llm.roles.audit.model` | Model used by taxonomy audit. | `qwen2.5:14b` |
| `llm.roles.vision.provider` | Provider used for image sidecar generation. | `local` |
| `llm.roles.vision.model` | Vision model for image description and OCR. | `gemma3:12b` |
| `llm.sensitive_provider` | Provider used when content is marked sensitive. | `local` |
| `notify.command` | Optional local command for notifications. Empty disables command notifications. | empty |
| `notify.webhook_url` | Optional Discord-compatible webhook URL. Keep real values out of git. | empty |
| `notify.format` | Notification payload format. | `discord` |
| `api.host` | HTTP API bind host. Compose uses `0.0.0.0` inside the container and maps to localhost on the host. | `127.0.0.1` |
| `api.port` | HTTP API port. | `8377` |
| `api.token` | Optional bearer token required by the API. Empty disables auth. | empty |
| `recall.top_k` | Default number of recall results. | `4` |
| `recall.cat_boost` | Score boost for project/category matches. | `0.6` |
| `recall.topic_boost` | Score boost for topic matches. | `0.2` |
| `recall.fetch_mult` | Candidate expansion multiplier before reranking. | `5` |
| `schedule.health_interval_seconds` | Worker health-check interval. | `14400` |
| `schedule.ingest_daily` | Daily ingest time in `HH:MM`. | `03:10` |
| `schedule.catalog_daily` | Daily catalog render time in `HH:MM`. | `03:20` |
| `schedule.audit_weekly` | Weekly taxonomy audit time in `Day HH:MM`. | `Sun 04:00` |
| `schedule.eval_weekly` | Weekly retrieval evaluation time in `Day HH:MM`. | `Sun 04:30` |
| `capture.url_fetcher` | Optional executable that takes a URL and prints markdown to stdout. Empty disables URL capture; text notes are unaffected. | `''` |

## URL capture

kb-core does not bundle an HTML-to-markdown extractor, because which one gives
usable output depends on the sites you save. Point `capture.url_fetcher` at any
executable that accepts a URL as its single argument and writes markdown to
stdout, and `kb add <url>` will use it. With the setting empty, adding text
works normally and adding a URL returns an error saying so.

## Environment Overrides

- `KB_CONFIG`: absolute or relative path to a YAML config file.
- `KB_ROOT`: overrides `kb_root`.
- `KB_VISION_MODEL`: overrides the configured vision model.
- Provider-specific key variables such as `DEEPSEEK_API_KEY` are read by name from `llm.providers.*.key_env`.
