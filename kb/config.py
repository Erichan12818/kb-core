#!/usr/bin/env python3
"""KB 工具鏈集中配置載入器。

PyYAML 可用時讀 kb_config.yaml；不可用或配置缺失時，退回可分發本機預設值。
"""
import copy
import os
from pathlib import Path

DEFAULTS = {
    "kb_root": "./vault",
    "qdrant": {
        # server = talk to a Qdrant service; embedded = run in-process against
        # a local directory (no server, no Docker, single process only).
        "mode": "server",
        "path": "",
        "host": "localhost",
        "port": 6333,
        "collection": "kb_hybrid_v2",
        "timeout_batch": 60,
        "timeout_interactive": 15,
    },
    "embedding": {
        "dense_model": "intfloat/multilingual-e5-large",
        "sparse_model": "Qdrant/bm25",
        "dense_dim": 1024,
    },
    "chunking": {
        "size": 500,
        "overlap": 50,
    },
    "llm": {
        "providers": {
            "local": {
                "type": "ollama",
                "url": "http://localhost:11434",
            },
            "cloud": {
                "type": "openai_compat",
                "base_url": "https://api.deepseek.com/v1",
                "key_env": "DEEPSEEK_API_KEY",
                "key_env_file": "",
                "extra_body": {"thinking": {"type": "disabled"}},
            },
        },
        "roles": {
            "classify": {
                "provider": "cloud",
                "model": "deepseek-v4-flash",
                "fallback": {"provider": "local", "model": "qwen2.5:14b"},
            },
            "audit": {"provider": "local", "model": "qwen2.5:14b"},
            "vision": {"provider": "local", "model": "gemma3:12b"},
        },
        "sensitive_provider": "local",
    },
    "notify": {
        "command": "",
        "webhook_url": "",
        "format": "discord",
    },
    "api": {
        "host": "127.0.0.1",
        "port": 8377,
        "token": "",
    },
    "recall": {
        "top_k": 4,
        "cat_boost": 0.6,
        "topic_boost": 0.2,
        "fetch_mult": 5,
    },
    "schedule": {
        "health_interval_seconds": 14400,
        "ingest_daily": "03:10",
        "catalog_daily": "03:20",
        "audit_weekly": "Sun 04:00",
        "eval_weekly": "Sun 04:30",
    },
}

_CACHE = None


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    if not isinstance(override, dict):
        return out
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _config_path():
    env_path = os.environ.get("KB_CONFIG")
    if env_path:
        return Path(os.path.expanduser(env_path))
    return Path(__file__).with_name("kb_config.yaml")


def _read_yaml(path):
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _load():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    data = _deep_merge(DEFAULTS, _read_yaml(_config_path()))
    _normalize_llm(data)
    if os.environ.get("KB_ROOT"):
        data["kb_root"] = os.environ["KB_ROOT"]
    if os.environ.get("KB_VISION_MODEL"):
        llm = data.setdefault("llm", {})
        llm.setdefault("roles", {}).setdefault("vision", {})["model"] = os.environ["KB_VISION_MODEL"]
        llm["vision"] = os.environ["KB_VISION_MODEL"]
    _CACHE = data
    return _CACHE


def _normalize_llm(data):
    """兼容 P1-1 舊 llm path，同時以 P1-2 roles/providers 為準。"""
    llm = data.setdefault("llm", {})
    providers = llm.setdefault("providers", {})
    roles = llm.setdefault("roles", {})

    local_model = llm.get("classify_local", roles.get("audit", {}).get("model", "qwen2.5:14b"))
    cloud_model = llm.get("classify_cloud", roles.get("classify", {}).get("model", "deepseek-v4-flash"))
    vision_model = llm.get("vision", roles.get("vision", {}).get("model", "gemma3:12b"))
    ollama_url = llm.get("ollama_url", providers.get("local", {}).get("url", "http://localhost:11434"))
    key_env = llm.get("deepseek_env", providers.get("cloud", {}).get("key_env", "DEEPSEEK_API_KEY"))
    key_env_file = llm.get("deepseek_env_file", providers.get("cloud", {}).get("key_env_file", ""))

    providers.setdefault("local", {})
    providers["local"].setdefault("type", "ollama")
    providers["local"].setdefault("url", ollama_url)

    providers.setdefault("cloud", {})
    providers["cloud"].setdefault("type", "openai_compat")
    providers["cloud"].setdefault("base_url", "https://api.deepseek.com/v1")
    providers["cloud"].setdefault("key_env", key_env)
    providers["cloud"].setdefault("key_env_file", key_env_file)
    providers["cloud"].setdefault("extra_body", {"thinking": {"type": "disabled"}})

    roles.setdefault("classify", {})
    roles["classify"].setdefault("provider", "cloud")
    roles["classify"].setdefault("model", cloud_model)
    roles["classify"].setdefault("fallback", {"provider": "local", "model": local_model})
    roles.setdefault("audit", {"provider": "local", "model": local_model})
    roles.setdefault("vision", {"provider": "local", "model": vision_model})
    if "classify_cloud" in llm:
        roles["classify"]["model"] = cloud_model
    if "classify_local" in llm:
        roles["classify"].setdefault("fallback", {})["provider"] = "local"
        roles["classify"]["fallback"]["model"] = local_model
        roles.setdefault("audit", {})["model"] = local_model
        roles["audit"].setdefault("provider", "local")
    if "vision" in llm:
        roles.setdefault("vision", {})["model"] = vision_model
        roles["vision"].setdefault("provider", "local")
    llm.setdefault("sensitive_provider", "local")

    # 舊 caller 仍可讀到現有 path，避免健康檢查等周邊腳本被新 schema 破壞。
    llm["classify_local"] = roles["classify"].get("fallback", {}).get("model", local_model)
    llm["classify_cloud"] = roles["classify"].get("model", cloud_model)
    llm["vision"] = roles["vision"].get("model", vision_model)
    llm["ollama_url"] = providers["local"].get("url", ollama_url)
    llm["deepseek_env"] = providers["cloud"].get("key_env", key_env)
    llm["deepseek_env_file"] = providers["cloud"].get("key_env_file", key_env_file)


def config_path():
    """Where the active configuration is read from and written back to."""
    return _config_path()


def reload():
    """Drop the cache so the next cfg() call re-reads the file.

    Settings written through the UI take effect without a restart only because
    every consumer calls cfg() at use time rather than caching at import.
    """
    global _CACHE
    _CACHE = None
    return _load()


def cfg(path, default=None):
    cur = _load()
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
