#!/usr/bin/env python3
"""LLM provider 路由抽象。

按 kb_config.yaml 嘅 llm.roles 將 classify/audit/vision 路由到 Ollama 或
OpenAI-compatible provider。sensitive 內容一律走本機 provider。
"""
import os
import re
from pathlib import Path

import requests

from .config import cfg

_LAST_ROUTE = None


def _load_env(path, key):
    if not key:
        return None
    if path:
        try:
            for ln in Path(os.path.expanduser(path)).read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(rf"\s*(?:export\s+)?{re.escape(key)}\s*=\s*['\"]?([^'\"\n]+)", ln)
                if m:
                    return m.group(1).strip()
        except FileNotFoundError:
            pass
    return os.environ.get(key)


def _role_cfg(role):
    roles = cfg("llm.roles", {})
    if role not in roles:
        raise ValueError(f"未知 LLM role: {role}")
    return roles[role] or {}


def _sensitive_target(role_conf):
    provider = cfg("llm.sensitive_provider", "local")
    fallback = role_conf.get("fallback") or {}
    audit = _role_cfg("audit")
    return provider, fallback.get("model") or audit.get("model")


def _targets(role, sensitivity):
    role_conf = _role_cfg(role)
    if sensitivity == "sensitive":
        provider, model = _sensitive_target(role_conf)
        return [(provider, model)]

    out = [(role_conf.get("provider"), role_conf.get("model"))]
    fallback = role_conf.get("fallback")
    if isinstance(fallback, dict):
        out.append((fallback.get("provider"), fallback.get("model")))
    return out


def _ollama(provider_conf, model, prompt, images, json_mode):
    url = provider_conf.get("url", "http://localhost:11434").rstrip("/") + "/api/chat"
    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = images
    payload = {
        "model": model,
        "stream": False,
        "messages": [msg],
        "options": {"temperature": 0.2},
    }
    if json_mode:
        payload["format"] = "json"
    r = requests.post(url, timeout=300 if images else 180, json=payload)
    r.raise_for_status()
    return r.json()["message"]["content"]


def _openai_compat(provider_conf, model, prompt, images, json_mode):
    if images:
        raise ValueError("openai_compat provider 尚未支援 images")
    key = _load_env(provider_conf.get("key_env_file"), provider_conf.get("key_env"))
    if not key:
        raise RuntimeError(f"缺少 {provider_conf.get('key_env')}")
    url = provider_conf.get("base_url", "").rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    extra = provider_conf.get("extra_body") or {}
    if isinstance(extra, dict):
        payload.update(extra)
    r = requests.post(url, timeout=60, headers={"Authorization": f"Bearer {key}"}, json=payload)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call(provider_name, model, prompt, images, json_mode):
    if not provider_name or not model:
        raise ValueError("LLM provider/model 未配置完整")
    providers = cfg("llm.providers", {})
    provider_conf = providers.get(provider_name)
    if not isinstance(provider_conf, dict):
        raise ValueError(f"未知 LLM provider: {provider_name}")
    ptype = provider_conf.get("type")
    if ptype == "ollama":
        out = _ollama(provider_conf, model, prompt, images, json_mode)
        _set_last_route(provider_name, model)
        return out
    if ptype == "openai_compat":
        out = _openai_compat(provider_conf, model, prompt, images, json_mode)
        _set_last_route(provider_name, model)
        return out
    raise ValueError(f"未知 LLM provider type: {ptype}")


def _set_last_route(provider_name, model):
    global _LAST_ROUTE
    _LAST_ROUTE = (provider_name, model)


def last_route_label(default="LLM"):
    if not _LAST_ROUTE:
        return default
    provider_name, model = _LAST_ROUTE
    if provider_name == "local":
        return "本機 qwen" if str(model).startswith("qwen") else f"本機 {model}"
    if provider_name == "cloud":
        return "雲 deepseek" if "deepseek" in str(model).lower() else f"雲 {model}"
    return f"{provider_name} {model}"


def chat(role, prompt, images=None, json_mode=False, sensitivity="public") -> str:
    """role in {classify,audit,vision}，按 config 路由到 provider+model。"""
    last_err = None
    for provider_name, model in _targets(role, sensitivity):
        try:
            return _call(provider_name, model, prompt, images, json_mode)
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("LLM router 無可用 target")
