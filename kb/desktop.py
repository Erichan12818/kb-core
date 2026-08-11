#!/usr/bin/env python3
"""Desktop entry point: one process, no Docker, no terminal.

The Compose deployment runs an API and a worker against a Qdrant service. A
desktop build cannot: the embedded store admits a single holder, so everything
lives in this one process — the HTTP server on a loopback port, the schedules on
a background thread, and the browser pointed at the local UI.

First launch has nothing to show for a few minutes while the embedding model
downloads. Reporting that on the console is not enough: a Finder double-click
attaches no console, which is how the first release turned every startup
failure into a silent hang. Progress goes to a log file in the vault, failures
go to a native dialog, and the browser is not opened until the server actually
answers — see kb.desktop_report.
"""
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from .desktop_report import fatal, report, set_log_path

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


def pointer_path():
    """Fixed location of the file naming which vault to open.

    The vault location cannot live in kb_config.yaml, because that file lives
    *inside* the vault — finding it already requires knowing the answer. This
    one file stays at the per-platform default no matter where the data goes.
    """
    return default_vault() / "vault_location.txt"


def read_vault_pointer():
    """The configured vault, or None when the default is in use."""
    try:
        raw = pointer_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    return Path(os.path.expanduser(raw))


def write_vault_pointer(path):
    """Point future launches at ``path``; an empty value restores the default."""
    pointer = pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    if not path:
        pointer.unlink(missing_ok=True)
        return None
    resolved = Path(os.path.expanduser(str(path)))
    pointer.write_text(str(resolved) + "\n", encoding="utf-8")
    return resolved


def resolve_vault(explicit=None):
    """Where this launch reads and writes: --vault, then the pointer, then default."""
    if explicit:
        return Path(os.path.expanduser(explicit))
    return read_vault_pointer() or default_vault()


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


def warm_models():
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


def run_schedules():
    """The worker loop, in-process. Failure here must not take the UI down."""
    try:
        from . import worker

        worker.main()
    except Exception as e:
        report(f"Background schedule stopped: {type(e).__name__}: {e}")


def wait_until_serving(port, timeout=90.0):
    """Block until the loopback server answers, so the browser opens on a page.

    Opening the browser on a fixed 1.5s timer raced the server: on a cold start
    the user got a connection-refused page and no reason to try again.
    """
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except urllib.error.HTTPError:
            # Answering at all is the signal; an unhealthy body still means the
            # port is live and the UI is worth opening.
            return True
        except Exception:
            time.sleep(0.4)
    return False


def open_when_ready(port, url):
    if wait_until_serving(port):
        webbrowser.open(url)
    else:
        report(f"Server did not become reachable; open {url} manually.")


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="kb-core", description="kb-core desktop")
    parser.add_argument("--vault", help="data directory (default: per-platform app data)")
    parser.add_argument("--port", type=int, help="loopback port for the UI")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--no-schedule", action="store_true", help="do not run background jobs")
    args = parser.parse_args(argv)

    vault = resolve_vault(args.vault)

    # The log is opened before anything that can fail, so that a launch which
    # dies during configuration still leaves a trace to read afterwards.
    try:
        log = set_log_path(vault / "logs" / "kb-core.log")
    except Exception:
        log = None

    try:
        config_path = ensure_config(vault)
        # kb.config reads these at import time, so they have to be set first.
        os.environ["KB_CONFIG"] = str(config_path)
        os.environ["KB_ROOT"] = str(vault)
        prepare_vault(vault)
    except OSError as e:
        fatal(
            "Cannot use the data folder",
            f"{vault}\n\n{type(e).__name__}: {e}\n\n"
            "If the app was opened straight from the download, move it to "
            "Applications first.",
            log,
        )
        return 1

    from .config import cfg
    from . import store

    port = args.port or int(cfg("api.port", 8377) or 8377)
    url = f"http://127.0.0.1:{port}/ui"

    report(f"Vault:  {vault}")
    report(f"Store:  {store.describe()}")
    report(f"Config: {config_path}")
    if log:
        report(f"Log:    {log}")

    threading.Thread(target=warm_models, daemon=True).start()
    if not args.no_schedule:
        threading.Thread(target=run_schedules, daemon=True).start()
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(port, url), daemon=True).start()

    report(f"Open {url}")
    from . import api

    try:
        api.main(["--port", str(port), "--host", "127.0.0.1"])
    except OSError as e:
        # Almost always the port already being held — including by a second
        # copy of this app, which the embedded store cannot share.
        fatal(
            "Cannot start the server",
            f"Port {port} is not available.\n\n{type(e).__name__}: {e}\n\n"
            "Almanac may already be running. Open " + url + " to check.",
            log,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
