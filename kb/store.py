#!/usr/bin/env python3
"""Where the vector store lives.

Two modes, chosen by ``qdrant.mode``:

``server`` (default)
    Talk to a Qdrant service over the network. This is what Compose runs and
    what a NAS deployment wants — several processes can share one store.

``embedded``
    Run Qdrant in-process against a directory under the vault. No server, no
    Docker, no ports. This is what makes a single-file desktop build possible.

The embedded store keeps its data in one local directory and **only one process
may hold it at a time**. The API and the worker are separate processes, so a
deployment running both has to use server mode; a desktop build runs one
process and does not. Rather than let that surface as a confusing lock error
deep in a request, :func:`connect` explains it.
"""
import os
import threading
from pathlib import Path

from .config import cfg

# The embedded store allows one holder of its directory. Call sites open clients
# freely — which is correct against a server — so in embedded mode they all have
# to receive the same one, or the second caller in a single run hits the lock.
_EMBEDDED = {"client": None}
_EMBEDDED_LOCK = threading.Lock()

_EMBEDDED_LOCK_HINT = (
    "嵌入式模式嘅資料目錄同一時間只可以俾一個 process 開。"
    "如果你同時行緊 API 同 worker（例如 docker compose），"
    "要用 qdrant.mode: server；嵌入式係設計俾單一 process 嘅桌面版用。"
)


def mode():
    return str(cfg("qdrant.mode", "server") or "server").strip().lower()


def is_embedded():
    return mode() == "embedded"


def storage_path():
    """Directory holding the embedded store; defaults to inside the vault."""
    configured = cfg("qdrant.path", "") or ""
    if configured:
        return Path(os.path.expanduser(str(configured)))
    return Path(cfg("kb_root")) / "state" / "qdrant"


def connect(timeout=None):
    """Return a QdrantClient for the configured mode."""
    from qdrant_client import QdrantClient

    if is_embedded():
        with _EMBEDDED_LOCK:
            if _EMBEDDED["client"] is not None:
                return _EMBEDDED["client"]
            path = storage_path()
            path.mkdir(parents=True, exist_ok=True)
            try:
                _EMBEDDED["client"] = QdrantClient(path=str(path))
            except RuntimeError as e:
                if "already accessed" in str(e).lower() or "lock" in str(e).lower():
                    raise RuntimeError(f"{e}\n\n{_EMBEDDED_LOCK_HINT}") from e
                raise
            return _EMBEDDED["client"]

    return QdrantClient(
        host=cfg("qdrant.host"),
        port=cfg("qdrant.port"),
        timeout=timeout if timeout is not None else cfg("qdrant.timeout_batch"),
    )


def describe():
    """One line for health output and startup logs."""
    if is_embedded():
        return f"embedded ({storage_path()})"
    return f"{cfg('qdrant.host')}:{cfg('qdrant.port')}"
