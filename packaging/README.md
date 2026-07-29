# Desktop builds

The desktop build is one process with no Docker: the vector store runs
in-process against a directory, the HTTP server listens on loopback, and the
schedules run on a background thread. That is what `qdrant.mode: embedded`
buys, and it is the only mode the desktop build uses.

## Build

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pyinstaller
.venv/bin/pyinstaller packaging/kb-core.spec --noconfirm
```

Output is `dist/kb-core.app` on macOS, `dist/kb-core/` elsewhere. Roughly
185MB — the embedding model is *not* bundled (~2.3GB, separately licensed), so
the first launch downloads it and reports progress instead of looking hung.

Build on the platform you are shipping to; PyInstaller does not cross-compile.

## What a user gets

Launch it and it creates a vault in the platform's app-data directory, writes a
starter config, serves the UI on `127.0.0.1:8377`, and opens a browser. Nothing
to install, nothing to configure, no terminal — though a console window stays
open, because the first-run download deserves to be visible.

LLM features are off until a role is added to `kb_config.yaml`. Capture and
search work without one; notes just get no generated titles or topic tags, and
the Ask tab stays hidden.

## Unsigned builds

The bundle is not code-signed, so macOS Gatekeeper blocks it on first open, and
SmartScreen warns on Windows. Users have to right-click → Open (macOS) or
choose "Run anyway" (Windows). Signing needs a paid developer account on both
platforms; until then, say so on the download page rather than letting people
hit the wall unprepared.

## Embedded mode admits one process

The store directory takes a single holder. Everything the desktop build does
happens in one process, including ingest — `kb.add` runs it on a thread rather
than spawning a subprocess when the store is embedded, because a second process
would fail on the lock.

Server mode is still correct for Compose and NAS deployments, where the API and
the worker are separate processes sharing one Qdrant service.
