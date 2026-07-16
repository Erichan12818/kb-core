# Contributing

Keep changes small, local-first, and fail-open. kb-core should remain useful without a hosted service, and unavailable optional dependencies must not break agent startup.

Do not commit real vault data, `config/kb_config.yaml`, logs, JSONL traces, `.env` files, API keys, webhook URLs, private host names, or personal machine paths.

Prefer the Python standard library unless a dependency is already part of the project. Keep comments concise; English and Traditional Chinese comments are both acceptable when they clarify non-obvious behavior.

Open pull requests with a focused problem statement, the smallest practical diff, and the verification commands you ran.

Before sending a change, run:

```bash
python -m py_compile kb/*.py
rg -n "192\\.168\\.|/Users/|/Volumes/|discord(?:app)?\\.com/api/webhooks|sk-[A-Za-z0-9]" .
```
