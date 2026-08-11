#!/usr/bin/env python3
"""Frozen-build entry point.

PyInstaller starts a script, not a module, so this is the thin shim that hands
control to kb.desktop. It also makes the failure that actually happens to users
readable: a bundle missing a lazily imported dependency otherwise dies with a
bare traceback nobody sees.

The first release ended both failure paths with `input("Press Enter to close…")`.
Launched from Finder there is no stdin, so `input()` raised EOFError on the spot
and the process vanished — taking with it the traceback it had just printed to a
stdout that was not connected to anything. Failures now go through
kb.desktop_report, which puts them in a dialog when there is no terminal.
"""
import sys
import traceback

ISSUES_URL = "https://github.com/Erichan12818/kb-core/issues"


def _fallback_report(title, detail):
    """Report a failure that happened before kb.desktop_report could be imported."""
    print(f"{title}\n\n{detail}", file=sys.stderr, flush=True)
    try:
        if sys.stdout and sys.stdout.isatty():
            return
        if sys.platform == "darwin":
            import subprocess

            body = f"{title}\n\n{detail}"[:1200]
            quoted = (
                '"'
                + body.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                + '"'
            )
            subprocess.run(
                [
                    "/usr/bin/osascript",
                    "-e",
                    f'display dialog {quoted} with title "Almanac" '
                    'buttons {"OK"} default button "OK" with icon stop',
                ],
                check=False,
                capture_output=True,
                timeout=300,
            )
    except Exception:
        pass


def main():
    try:
        from kb.desktop import main as desktop_main
    except Exception:
        _fallback_report(
            "Almanac could not start",
            "A required component is missing from this build.\n\n"
            f"{traceback.format_exc()}\n"
            f"Please report this at {ISSUES_URL}",
        )
        return 1

    try:
        return desktop_main() or 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        try:
            from kb.desktop_report import fatal, log_path

            fatal(
                "Almanac stopped unexpectedly",
                f"{traceback.format_exc()}\nPlease report this at {ISSUES_URL}",
                log_path(),
            )
        except Exception:
            _fallback_report("Almanac stopped unexpectedly", traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
