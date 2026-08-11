#!/usr/bin/env python3
"""Reading and writing the settings a desktop user can change from the UI.

Turning on the Ask tab used to mean opening kb_config.yaml in a text editor,
uncommenting a role, and exporting an environment variable — three steps in two
places, none of them discoverable from the app. This module is the backing for
a Settings panel that does the same thing through a form.

Two rules shape what is here:

The API key never goes into kb_config.yaml. That file is the one a user is
likely to paste into an issue or commit to a dotfiles repo. The key is written
to a separate 0600 file inside the vault and referenced by ``key_env_file``,
which is the indirection kb.llm already supports.

The key never travels back to the browser. :func:`read_settings` reports
whether a key is present, never what it is — the same rule kb.chat states for
the provider path.
"""
import os
from pathlib import Path

from .config import cfg, config_path, reload as reload_config

CHAT_ROLE = "chat"
SECRETS_FILENAME = "secrets.env"

# Written into the config header so the next person to open the file by hand
# knows why the comments they wrote are gone.
_HEADER = (
    "# Almanac configuration.\n"
    "# Managed by the in-app Settings panel; hand edits are preserved but\n"
    "# comments are not rewritten. Restart the app after editing by hand.\n"
)


def secrets_path():
    """The 0600 file holding provider keys, inside the vault."""
    return Path(cfg("kb_root")) / "state" / SECRETS_FILENAME


def _provider_conf():
    providers = cfg("llm.providers", {}) or {}
    conf = providers.get("cloud")
    return conf if isinstance(conf, dict) else {}


def has_api_key():
    """True when the configured key resolves — from the key file or the env."""
    from .llm import _load_env

    conf = _provider_conf()
    key_name = conf.get("key_env") or ""
    if not key_name:
        return False
    return bool(_load_env(conf.get("key_env_file"), key_name))


def read_settings():
    """Current settings for the UI. Never includes the key itself."""
    conf = _provider_conf()
    roles = cfg("llm.roles", {}) or {}
    chat_role = roles.get(CHAT_ROLE) if isinstance(roles.get(CHAT_ROLE), dict) else {}
    return {
        "base_url": conf.get("base_url") or "",
        "model": chat_role.get("model") or cfg("llm.classify_cloud", "") or "",
        "key_env": conf.get("key_env") or "DEEPSEEK_API_KEY",
        "has_api_key": has_api_key(),
        "chat_enabled": bool(chat_role.get("provider") and chat_role.get("model")),
        "config_path": str(config_path()),
        "vault": str(cfg("kb_root")),
    }


def _write_secret(key_name, value):
    """Store one key in the vault's secrets file, replacing any prior value.

    Written 0600 before any content reaches it, so the key is never briefly
    readable by other accounts on a shared machine.
    """
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            name = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
            if name.removeprefix("export ").strip() == key_name:
                continue
            lines.append(line)
    lines.append(f"{key_name}={value}")

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _validate(base_url, model, chat_enabled):
    if chat_enabled and not model:
        return "A model name is required to turn on Ask."
    if base_url and not str(base_url).startswith(("http://", "https://")):
        return "The provider URL must start with http:// or https://."
    return None


def write_settings(base_url=None, model=None, api_key=None, chat_enabled=None):
    """Apply settings to kb_config.yaml and the secrets file.

    Returns (ok, message, settings). Only the fields supplied are touched, so
    saving the form without retyping the key leaves the stored key alone.
    """
    try:
        import yaml
    except ImportError:
        return False, "PyYAML is not available, so settings cannot be saved.", read_settings()

    current = read_settings()
    base_url = current["base_url"] if base_url is None else str(base_url).strip()
    model = current["model"] if model is None else str(model).strip()
    chat_enabled = current["chat_enabled"] if chat_enabled is None else bool(chat_enabled)

    error = _validate(base_url, model, chat_enabled)
    if error:
        return False, error, current

    key_name = current["key_env"] or "DEEPSEEK_API_KEY"
    api_key = (api_key or "").strip()
    if chat_enabled and not api_key and not current["has_api_key"]:
        return False, f"An API key is required to turn on Ask ({key_name}).", current

    path = config_path()
    raw = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return False, f"Could not read the existing config: {exc}", current
    if not isinstance(raw, dict):
        return False, "The existing config is not a mapping; not overwriting it.", current

    llm = raw.setdefault("llm", {})
    providers = llm.setdefault("providers", {})
    cloud = providers.setdefault("cloud", {})
    cloud.setdefault("type", "openai_compat")
    if base_url:
        cloud["base_url"] = base_url
    cloud["key_env"] = key_name

    if api_key:
        try:
            cloud["key_env_file"] = str(_write_secret(key_name, api_key))
        except OSError as exc:
            return False, f"Could not save the API key: {exc}", current

    roles = llm.setdefault("roles", {})
    if not isinstance(roles, dict):
        roles = {}
        llm["roles"] = roles
    if chat_enabled:
        chat_role = roles.setdefault(CHAT_ROLE, {})
        chat_role["provider"] = "cloud"
        chat_role["model"] = model
    else:
        roles.pop(CHAT_ROLE, None)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _HEADER + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except OSError as exc:
        return False, f"Could not write {path}: {exc}", current

    reload_config()
    return True, "Settings saved.", read_settings()
