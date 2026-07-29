#!/usr/bin/env python3
"""Desktop entry point: one process, no Docker, no terminal.

The Compose deployment runs an API and a worker against a Qdrant service. A
desktop build cannot: the embedded store admits a single holder, so everything
lives in this one process — the HTTP server on a loopback port, the schedules on
a background thread, and the browser pointed at the local UI.

First launch has nothing to show for a few minutes while the embedding model
downloads, so it reports progress on the console it was started from and in the
window itself, rather than looking hung.
"""
import os
import sys
import threading
import webbrowser
from pathlib import Path

APP_NAME = "kb-core"


def default_vault():
    """Where a desktop install keeps its data, per-platform."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def ensure_config(vault):
    """Write a desktop config on first launch; leave an existing one alone.

    Desktop defaults differ from Compose in the ways that matter: the store is
    embedded, and no LLM role is configured, so a fresh install never reports a
    provider it cannot reach.
    """
    config_path = vault / "kb_config.yaml"
    if config_path.exists():
        return config_path
    vault.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join([
            "# kb-core desktop configuration.",
            "# Edit and restart the app to apply changes.",
            f"kb_root: {vault}",
            "qdrant:",
            "  mode: embedded",
            "  collection: kb_hybrid_v2",
            "api:",
            "  host: 127.0.0.1",
            "  port: 8377",
            "llm:",
            "  providers:",
            "    cloud:",
            "      type: openai_compat",
            "      base_url: https://api.deepseek.com/v1",
            "      key_env: DEEPSEEK_API_KEY",
            "  roles: {}",
            "    # Add a role to turn on LLM features. classify gives notes",
            "    # titles and topic tags; chat adds the Ask tab. Without either,",
            "    # capture and search work and nothing calls out to a provider.",
            "    #",
            "    # classify:",
            "    #   provider: cloud",
            "    #   model: deepseek-v4-flash",
            "    # chat:",
            "    #   provider: cloud",
            "    #   model: deepseek-v4-flash",
            "",
        ]),
        encoding="utf-8",
    )
    return config_path


def prepare_vault(vault):
    for name in ("raw_files", "state", "notes", "catalog", "trash"):
        (vault / name).mkdir(parents=True, exist_ok=True)


def warm_models(report):
    """Load the embedding models so the first search is not the one that waits.

    This is the ~2.3GB download on a fresh install. It happens on a background
    thread; the UI is usable for browsing before it finishes.
    """
    try:
        report("Preparing the search index (first run downloads ~2.3GB)…")
        from fastembed import TextEmbedding, SparseTextEmbedding

        from .config import cfg

        TextEmbedding(cfg("embedding.dense_model"))
        SparseTextEmbedding(cfg("embedding.sparse_model"))
        report("Ready.")
    except Exception as e:
        report(f"Could not prepare the search index: {type(e).__name__}: {e}")


def run_schedules(report):
    """The worker loop, in-process. Failure here must not take the UI down."""
    try:
        from . import worker

        worker.main()
    except Exception as e:
        report(f"Background schedule stopped: {type(e).__name__}: {e}")


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="kb-core", description="kb-core desktop")
    parser.add_argument("--vault", help="data directory (default: per-platform app data)")
    parser.add_argument("--port", type=int, help="loopback port for the UI")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--no-schedule", action="store_true", help="do not run background jobs")
    args = parser.parse_args(argv)

    vault = Path(os.path.expanduser(args.vault)) if args.vault else default_vault()
    config_path = ensure_config(vault)
    # kb.config reads these at import time, so they have to be set first.
    os.environ["KB_CONFIG"] = str(config_path)
    os.environ["KB_ROOT"] = str(vault)
    prepare_vault(vault)

    def report(message):
        print(f"[{APP_NAME}] {message}", flush=True)

    from .config import cfg
    from . import store

    port = args.port or int(cfg("api.port", 8377) or 8377)
    url = f"http://127.0.0.1:{port}/ui"

    report(f"Vault:  {vault}")
    report(f"Store:  {store.describe()}")
    report(f"Config: {config_path}")

    threading.Thread(target=warm_models, args=(report,), daemon=True).start()
    if not args.no_schedule:
        threading.Thread(target=run_schedules, args=(report,), daemon=True).start()
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    report(f"Open {url}")
    from . import api

    api.main(["--port", str(port), "--host", "127.0.0.1"])


if __name__ == "__main__":
    main()
