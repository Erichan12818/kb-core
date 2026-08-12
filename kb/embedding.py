#!/usr/bin/env python3
"""Where the embedding models are loaded from, and where they are kept.

fastembed defaults its cache to ``tempfile.gettempdir()``. On macOS that is
``/var/folders/<hash>/T``, which the OS is free to reclaim during periodic
maintenance or under disk pressure — so a working install can silently lose
2.3GB of model and spend the next launch re-downloading it while every search
fails. The app looks fine and the reason it is not working is invisible, which
is the same failure shape as the silent-hang bug this project already fixed.

So the cache is pinned to a real per-platform cache directory instead. Six call
sites construct embedders; they all come through here so the location cannot
drift apart again.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .config import cfg

_DIRNAME = "kb-core"
_migrated = False


def cache_dir():
    """Durable location for the model files, created on demand.

    ``embedding.cache_dir`` overrides it, for deployments that keep large
    artefacts on a specific volume.
    """
    configured = cfg("embedding.cache_dir", "") or ""
    if configured:
        path = Path(os.path.expanduser(str(configured)))
    else:
        path = _default_cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _default_cache_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / _DIRNAME / "Cache" / "models"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / _DIRNAME / "models"
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / _DIRNAME / "models"


def _legacy_cache_dir():
    """Where fastembed put the models before this module existed."""
    return Path(tempfile.gettempdir()) / "fastembed_cache"


def _migrate_legacy_cache(target):
    """Move an existing temp-dir cache rather than re-downloading 2.3GB.

    Best effort by definition: this is a cache, so any failure just means the
    models download again into the new location.
    """
    global _migrated
    if _migrated:
        return
    _migrated = True
    legacy = _legacy_cache_dir()
    try:
        if not legacy.is_dir() or any(target.iterdir()):
            return
        for entry in legacy.iterdir():
            shutil.move(str(entry), str(target / entry.name))
        legacy.rmdir()
    except OSError:
        pass


def _prepare():
    target = cache_dir()
    _migrate_legacy_cache(target)
    return str(target)


def dense(model_name):
    from fastembed import TextEmbedding

    return TextEmbedding(model_name, cache_dir=_prepare())


def sparse(model_name):
    from fastembed import SparseTextEmbedding

    return SparseTextEmbedding(model_name, cache_dir=_prepare())


def pair(dense_model, sparse_model):
    """Both embedders, which is what every indexing and query path wants."""
    return dense(dense_model), sparse(sparse_model)
