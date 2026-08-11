#!/usr/bin/env python3
"""Making a desktop launch visible when there is no terminal attached.

A Finder double-click gives the process no stdout, no stderr and no stdin. The
first release shipped its entire progress and error reporting through `print()`
and `input()`, so a launch that failed produced nothing at all on screen — the
process sat there owning no window while the user waited on a 2.3GB download
that was never happening.

Three channels, in order of how hard they are to miss:

  log_path()  a file that survives the process, for diagnosis after the fact
  report()    a line to both stdout and that file
  alert()     a native dialog, the only channel a double-click user will see

Nothing here may raise. A reporting layer that fails while reporting a failure
is worse than no reporting at all.
"""
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

APP_NAME = "kb-core"
DISPLAY_NAME = "Almanac"  # what the user sees; APP_NAME is the engine/data-folder name

_lock = threading.Lock()
_log_path = None


def set_log_path(path):
    """Point the file channel at the vault. Safe to call more than once."""
    global _log_path
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            _log_path = path
        _write(f"--- {APP_NAME} started {datetime.now(timezone.utc).isoformat()} ---")
    except Exception:
        pass
    return _log_path


def log_path():
    return _log_path


def _write(line):
    with _lock:
        target = _log_path
    if target is None:
        return
    try:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def report(message):
    """One progress line, to every channel that exists."""
    line = f"[{APP_NAME}] {message}"
    try:
        print(line, flush=True)
    except Exception:
        # A frozen GUI build can have a closed stdout; the file still works.
        pass
    _write(line)


def is_windowless():
    """True when nothing the process prints can reach a human.

    A Finder launch has no controlling terminal. Checking stdout rather than
    the platform keeps `kb-core` in a shell behaving like a normal CLI.
    """
    try:
        return not sys.stdout or not sys.stdout.isatty()
    except Exception:
        return True


def alert(title, message):
    """Put a failure on screen. Falls back to the log if no GUI is reachable."""
    _write(f"[{APP_NAME}] ALERT {title}: {message}")
    detail = message if len(message) <= 1200 else message[:1200] + "…"
    try:
        if sys.platform == "darwin":
            # osascript rather than a GUI toolkit: it is always present, and it
            # works from a process that has not finished starting up.
            #
            # Two forms, because a plain `display dialog` is shown by osascript
            # itself, and osascript owns no GUI when it is spawned by a process
            # the window server does not consider frontmost — it fails in
            # milliseconds. Handing the dialog to System Events puts it up
            # regardless. The plain form is tried first: it needs no Apple
            # Events permission, so when it works it is the quieter path.
            body = _as_applescript(detail)
            head = _as_applescript(title)
            common = (
                'display dialog {msg} with title {title} '
                'buttons {{"OK"}} default button "OK" with icon caution'
            ).format(msg=body, title=head)
            for label, script in (
                ("direct", common),
                ("system-events", f'tell application "System Events" to {common}'),
            ):
                result = subprocess.run(
                    ["/usr/bin/osascript", "-e", script],
                    timeout=300,
                    check=False,
                    capture_output=True,
                )
                if result.returncode == 0:
                    return True
                # Never swallow this: a failed alert is the failure that hides
                # every other failure.
                _write(
                    f"[{APP_NAME}] alert via {label} failed rc={result.returncode} "
                    f"{result.stderr.decode('utf-8', 'replace').strip()[:300]}"
                )
            return False
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, detail, title, 0x10)
            return True
        for tool, argv in (
            ("zenity", ["zenity", "--error", "--title", title, "--text", detail]),
            ("kdialog", ["kdialog", "--title", title, "--error", detail]),
        ):
            try:
                subprocess.run(argv, timeout=300, check=False, capture_output=True)
                return True
            except FileNotFoundError:
                continue
    except Exception:
        pass
    return False


def _as_applescript(text):
    """Quote a Python string as an AppleScript literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def fatal(title, message, log=None):
    """Report a launch-ending failure through whatever the user can see.

    In a terminal this stays a printed message. From Finder it becomes a dialog,
    because the alternative — the behaviour this replaces — is a process that
    exits without ever having drawn anything.
    """
    report(f"{title}: {message}")
    log = log or _log_path
    if log:
        report(f"Full log: {log}")
    if is_windowless():
        body = message
        if log:
            body += f"\n\nFull log:\n{log}"
        alert(f"{DISPLAY_NAME} — {title}", body)
