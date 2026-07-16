# Security Scan

Date: 2026-07-16

Scope: complete monorepo staging, including `plugins/claude-code` and packaged `kb.mcp`.

This pass checked the release package for:

- private LAN addresses in the 192-dot-168 range;
- private absolute user and NAS mount paths;
- maintainer names and other personal release metadata;
- key-like strings, API key names, bearer token fields, webhook fields, and common secret-token patterns;
- Discord-compatible webhook URLs.

## Result

No red items remain in executable defaults, docs, or examples.

Remaining matches are either empty placeholders, documented environment-variable names, localhost/container bind addresses, defensive secret-scanner patterns, evaluation text, or human release/legal metadata that the P4 spec explicitly leaves for the maintainer to decide.

## Cleaned Items

| File | Line | Finding | Risk | Action |
| --- | ---: | --- | --- | --- |
| `kb/config.py` | 38 | `llm.providers.cloud.key_env_file` default pointed at a personal dotenv path. | Red | Replaced with an empty string so dotenv loading is opt-in. |
| `kb/config.py` | 140 | Legacy `deepseek_env_file` fallback pointed at a personal dotenv path. | Red | Replaced with an empty string while preserving backwards-compatible config keys. |
| `plugins/claude-code/scripts/*` | - | Dogfood wrappers contained a maintainer-specific fallback path. | Red | Release staging now requires generic `KB_HOME` and resolves the monorepo package. |
| `plugins/claude-code/.claude-plugin/marketplace.json` | 4 | Marketplace owner used a personal account handle. | Metadata | Replaced with `kb-core contributors`; MIT copyright remains explicit legal metadata. |

## Reviewed Safe Matches

| File | Line | Finding | Judgment | Action |
| --- | ---: | --- | --- | --- |
| `LICENSE` | 3 | Copyright holder name. | Human/legal release item, not executable config or a secret. | Left unchanged; the maintainer must decide final LICENSE/copyright before publication. |
| `config/kb_config.example.yaml` | 23 | `DEEPSEEK_API_KEY` environment-variable name. | Safe example placeholder; no key material. | Left unchanged. |
| `config/kb_config.example.yaml` | 44 | Empty `webhook_url` field. | Safe placeholder. | Left unchanged. |
| `config/kb_config.example.yaml` | 49 | Empty `api.token` field. | Safe placeholder. | Left unchanged. |
| `QUICKSTART.md` | 21 | `export DEEPSEEK_API_KEY=...`. | Safe documentation placeholder. | Left unchanged. |
| `docs/CONFIG.md` | 25 | `DEEPSEEK_API_KEY` environment-variable name. | Safe documentation of config field. | Left unchanged. |
| `docs/CONFIG.md` | 38 | `notify.webhook_url` field description. | Safe documentation; warns not to commit real values. | Left unchanged. |
| `docs/CONFIG.md` | 42 | `api.token` field description. | Safe documentation of optional bearer token. | Left unchanged. |
| `docs/CONFIG.md` | 58 | Provider key environment-variable guidance. | Safe documentation; no secret value. | Left unchanged. |
| `docs/CONTRIBUTING.md` | 5 | Warning about API keys and webhook URLs. | Safe contributor guidance. | Left unchanged. |
| `docs/CONTRIBUTING.md` | 15 | Suggested grep pattern includes secret regex examples. | Safe contributor guidance. | Left unchanged. |
| `docs/TAXONOMY_POLICY.md` | 28 | Advises not to put secrets in policy files. | Safe documentation. | Left unchanged. |
| `docs/TAXONOMY_POLICY.md` | 82 | Mentions access tokens and webhook URLs as skip criteria. | Safe documentation. | Left unchanged. |
| `kb/config.py` | 37 | `DEEPSEEK_API_KEY` environment-variable name. | Generic default field name; no secret value. | Left unchanged. |
| `kb/config.py` | 55 | Empty `webhook_url` default. | Safe placeholder. | Left unchanged. |
| `kb/config.py` | 61 | Empty `api.token` default. | Safe placeholder. | Left unchanged. |
| `kb/config.py` | 139 | `DEEPSEEK_API_KEY` legacy fallback name. | Generic environment-variable name; no secret value. | Left unchanged. |
| `kb/ingest.py` | 40-47 | Secret/key/token detection comments and regex patterns. | Defensive scanner code. | Left unchanged. |
| `kb/notify.py` | 2, 29-59 | Webhook notification implementation. | Runtime feature with empty default; no URL value. | Left unchanged. |
| `kb/api.py` | 85-88 | Optional bearer token check. | Auth implementation; no token value. | Left unchanged. |
| `kb/health.py` | 11 | Notification mode comment. | Safe implementation comment. | Left unchanged. |
| `kb/mcp.py` | - | MCP exposes add, recall, and status operations. | Expected integration surface; no credentials or deployment paths embedded. | Added as generic package module. |
| `plugins/claude-code/.mcp.json` | - | Uses `${CLAUDE_PLUGIN_ROOT}` to start the wrapper. | Claude Code runtime variable, not a local absolute path. | Left unchanged. |
| `plugins/claude-code/scripts/*` | - | Uses `KB_HOME` and optional `KB_MCP_PYTHON`. | Runtime configuration names with no embedded values. | Left unchanged. |
| `config/eval_queries.yaml` | 28-30 | Evaluation phrase includes the word `token`. | Test fixture text, not a credential. | Left unchanged. |
| `kb/eval_queries.yaml` | 28-30 | Evaluation phrase includes the word `token`. | Test fixture text, not a credential. | Left unchanged. |

## Acceptance Checks

The release-blocking private deployment patterns were re-scanned after cleanup:

```bash
rg -n --hidden --no-ignore -S "<private LAN/user-path/NAS-path patterns>" <kb-core-path>
```

Result: no release-blocking matches. The only private-pattern hit outside this report is the
example scan command in `docs/CONTRIBUTING.md` itself.

Secret-like terms were reviewed with:

```bash
rg -n --hidden --no-ignore -S "<personal-info and secret-like patterns>" <kb-core-path>
```

Result: only the reviewed safe matches listed above. The staging contains no `vault`,
`config/kb_config.yaml`, `.env`, JSONL, bytecode, private IP address, or absolute user path.
