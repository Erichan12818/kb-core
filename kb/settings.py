#!/usr/bin/env python3
"""Reading and writing the settings a desktop user can change from the UI.

Turning on the Ask tab used to mean opening kb_config.yaml in a text editor,
uncommenting a role, and exporting an environment variable — three steps in two
places, none of them discoverable from the app. This module is the backing for
a Settings panel that does the same thing through a form.

Three rules shape what is here:

The API key never goes into kb_config.yaml. That file is the one a user is
likely to paste into an issue or commit to a dotfiles repo. The key is written
to a separate 0600 file inside the vault and referenced by ``key_env_file``,
which is the indirection kb.llm already supports.

The key never travels back to the browser. :func:`read_settings` reports
whether a key is present, never what it is — the same rule kb.chat states for
the provider path.

Settings that cannot take effect until the process restarts say so rather than
appearing to apply. The vault location is the sharpest case: it is read once at
launch, and changing it does not move the data that is already in the old one.

Deliberately not exposed here: the embedding models, vector dimension, and
chunking. Changing any of those invalidates every vector already stored, so the
knowledge base would have to be re-indexed to be searchable again — that is a
migration, not a setting. The Qdrant mode/host/port are likewise a deployment
decision the desktop build has already made. ``api.host`` stays out because
widening it past loopback exposes an unauthenticated knowledge base to the
network.
"""
import os
from pathlib import Path

from .config import cfg, config_path, reload as reload_config

CHAT_ROLE = "chat"
SECRETS_FILENAME = "secrets.env"

# Fields that only take effect on the next launch, reported to the UI so it can
# say so at the point of saving rather than leaving the user to wonder.
RESTART_FIELDS = ("vault", "api_port", "url_fetcher")

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


def _is_removable(path):
    """Whether a path sits on a mounted volume rather than the system disk.

    Not a hard block: an external drive is a legitimate place for a large
    vault. It is surfaced because the single-holder lock the embedded store
    relies on has been seen not to be enforced on some external filesystems.
    """
    try:
        return str(Path(path).resolve()).startswith("/Volumes/")
    except OSError:
        return False


def _model_cache_path():
    """Where the ~2.3GB embedding models are kept. Reported, not editable here."""
    from . import embedding

    try:
        return str(embedding.cache_dir())
    except OSError as exc:
        return f"(unavailable: {exc})"


def _read_sources():
    """Folders the user pointed at, with whether each is reachable right now.

    Reporting availability matters: an unplugged drive is a normal state, not
    an error, and the UI has to be able to say so rather than look broken.
    """
    out = []
    for entry in cfg("sources", []) or []:
        raw = entry.get("path") if isinstance(entry, dict) else entry
        if not raw:
            continue
        path = Path(os.path.expanduser(str(raw)))
        try:
            available = path.is_dir()
        except OSError:
            available = False
        out.append({
            "path": str(path),
            "available": available,
            "removable": _is_removable(path),
        })
    return out


def read_settings():
    """Current settings for the UI. Never includes the key itself."""
    from . import desktop

    conf = _provider_conf()
    roles = cfg("llm.roles", {}) or {}
    chat_role = roles.get(CHAT_ROLE) if isinstance(roles.get(CHAT_ROLE), dict) else {}
    vault = str(cfg("kb_root"))
    pointer = desktop.read_vault_pointer()
    return {
        # Storage
        "vault": vault,
        "vault_is_default": pointer is None,
        "vault_default": str(desktop.default_vault()),
        "vault_removable": _is_removable(vault),
        "config_path": str(config_path()),
        "secrets_path": str(secrets_path()),
        "index_path": str(Path(vault) / "state" / "qdrant"),
        "model_cache_path": _model_cache_path(),
        "sources": _read_sources(),
        # Ask
        "base_url": conf.get("base_url") or "",
        "model": chat_role.get("model") or cfg("llm.classify_cloud", "") or "",
        "key_env": conf.get("key_env") or "DEEPSEEK_API_KEY",
        "has_api_key": has_api_key(),
        "chat_enabled": bool(chat_role.get("provider") and chat_role.get("model")),
        # Search and server
        "top_k": cfg("recall.top_k", 4),
        "api_port": cfg("api.port", 8377),
        "url_fetcher": cfg("capture.url_fetcher", "") or "",
        "notes_dir": cfg("capture.notes_dir", "") or "",
        "notes_dir_effective": _notes_dir_effective(),
    }


def _notes_dir_effective():
    """Where notes actually land, whether or not a custom folder is set."""
    from .add import notes_root

    try:
        return str(notes_root())
    except Exception as exc:  # config in a bad state should not break the page
        return f"(unavailable: {exc})"


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


def _coerce_int(value, label, low, high, current):
    if value is None or value == "":
        return current, None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return current, f"{label} must be a whole number."
    if not low <= number <= high:
        return current, f"{label} must be between {low} and {high}."
    return number, None


def _check_vault(candidate):
    """Validate a proposed vault directory. Returns (resolved_path, error)."""
    path = Path(os.path.expanduser(str(candidate))).resolve()
    if not path.is_absolute():
        return None, "The vault location must be an absolute path."
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return None, f"Cannot use that folder: {exc}"
    if not os.access(path, os.W_OK):
        return None, f"That folder is not writable: {path}"
    return path, None


def _clean_sources(value):
    """Normalise the newline-separated folder list from the form.

    A path that is not reachable right now is accepted rather than rejected:
    the whole point of the feature is external drives, and one is expected to
    be absent most of the time. Only a path that exists and is a *file* is
    refused, since that is a typo rather than an unplugged disk.
    """
    if isinstance(value, str):
        entries = [line.strip() for line in value.splitlines()]
    elif isinstance(value, list):
        entries = [str(v).strip() for v in value]
    else:
        return None, "Sources must be a list of folders."

    out, seen = [], set()
    for entry in entries:
        if not entry:
            continue
        path = Path(os.path.expanduser(entry))
        if not path.is_absolute():
            return None, f"Source folders must be absolute paths: {entry}"
        try:
            if path.exists() and not path.is_dir():
                return None, f"Not a folder: {entry}"
        except OSError:
            pass  # unreachable volume — allowed, see docstring
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out, None


def write_settings(payload):
    """Apply settings to kb_config.yaml, the secrets file, and the vault pointer.

    Returns (ok, message, settings, restart_needed). Only the fields supplied
    are touched, so saving the form without retyping the key leaves the stored
    key alone.
    """
    try:
        import yaml
    except ImportError:
        return False, "PyYAML is not available, so settings cannot be saved.", read_settings(), False

    from . import desktop

    payload = payload or {}
    current = read_settings()

    def given(name):
        return payload.get(name) if name in payload else None

    base_url = current["base_url"] if given("base_url") is None else str(payload["base_url"]).strip()
    model = current["model"] if given("model") is None else str(payload["model"]).strip()
    chat_enabled = (
        current["chat_enabled"]
        if given("chat_enabled") is None
        else bool(payload["chat_enabled"])
    )
    url_fetcher = (
        current["url_fetcher"]
        if given("url_fetcher") is None
        else str(payload["url_fetcher"]).strip()
    )

    sources = [s["path"] for s in current["sources"]]
    if given("sources") is not None:
        sources, error = _clean_sources(payload["sources"])
        if error:
            return False, error, current, False

    top_k, error = _coerce_int(given("top_k"), "Results per search", 1, 50, current["top_k"])
    if error:
        return False, error, current, False
    api_port, error = _coerce_int(given("api_port"), "Port", 1024, 65535, current["api_port"])
    if error:
        return False, error, current, False

    if chat_enabled and not model:
        return False, "A model name is required to turn on Ask.", current, False
    if base_url and not base_url.startswith(("http://", "https://")):
        return False, "The provider URL must start with http:// or https://.", current, False
    if url_fetcher and not Path(os.path.expanduser(url_fetcher)).exists():
        return False, f"No such URL fetcher: {url_fetcher}", current, False

    notes_dir = current["notes_dir"] if given("notes_dir") is None else str(payload["notes_dir"]).strip()
    if notes_dir:
        # Unlike a read source, this one is written to — so it has to be
        # reachable and writable now, not merely plausible.
        resolved, error = _check_vault(notes_dir)
        if error:
            return False, f"Notes folder: {error}", current, False
        notes_dir = str(resolved)

    key_name = current["key_env"] or "DEEPSEEK_API_KEY"
    api_key = str(payload.get("api_key") or "").strip()
    if chat_enabled and not api_key and not current["has_api_key"]:
        return False, f"An API key is required to turn on Ask ({key_name}).", current, False

    # The vault is resolved before the config write so a bad path fails before
    # anything has been changed.
    new_vault = None
    vault_given = given("vault")
    if vault_given is not None:
        requested = str(payload["vault"]).strip()
        if not requested:
            new_vault = ""  # restore the default
        else:
            resolved, error = _check_vault(requested)
            if error:
                return False, error, current, False
            if str(resolved) != current["vault"]:
                new_vault = resolved

    path = config_path()
    raw = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return False, f"Could not read the existing config: {exc}", current, False
    if not isinstance(raw, dict):
        return False, "The existing config is not a mapping; not overwriting it.", current, False

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
            return False, f"Could not save the API key: {exc}", current, False

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

    raw["sources"] = sources
    raw.setdefault("recall", {})["top_k"] = top_k
    raw.setdefault("api", {})["port"] = api_port
    capture = raw.setdefault("capture", {})
    capture["url_fetcher"] = url_fetcher
    capture["notes_dir"] = notes_dir

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _HEADER + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except OSError as exc:
        return False, f"Could not write {path}: {exc}", current, False

    notes = []
    if new_vault is not None:
        try:
            desktop.write_vault_pointer(new_vault)
        except OSError as exc:
            return False, f"Could not save the vault location: {exc}", read_settings(), False
        if new_vault == "":
            notes.append(f"Vault reset to the default ({current['vault_default']}).")
        else:
            notes.append(
                f"Vault will be {new_vault} after a restart. Your existing notes stay "
                f"in {current['vault']} — Almanac does not move them."
            )
            if _is_removable(new_vault):
                notes.append(
                    "That location is on a removable volume: keep it mounted "
                    "before launching, and never run two copies against it."
                )

    reload_config()
    settings = read_settings()
    restart_needed = bool(notes) or api_port != current["api_port"] or url_fetcher != current["url_fetcher"]
    message = " ".join(["Settings saved."] + notes)
    if restart_needed and not notes:
        message += " Restart Almanac for the changed settings to take effect."
    return True, message, settings, restart_needed
