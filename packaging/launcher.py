#!/usr/bin/env python3
"""Frozen-build entry point.

PyInstaller starts a script, not a module, so this is the thin shim that hands
control to kb.desktop. It also makes the failure that actually happens to users
readable: a bundle missing a lazily imported dependency otherwise dies with a
bare traceback in a console window that closes immediately.
"""
import sys
import traceback


def main():
    try:
        from kb.desktop import main as desktop_main
    except Exception:
        traceback.print_exc()
        print(
            "\nkb-core could not start: a required component is missing from "
            "this build.\nPlease report the traceback above at "
            "https://github.com/Erichan12818/kb-core/issues",
            file=sys.stderr,
        )
        input("\nPress Enter to close…")
        return 1
    try:
        desktop_main()
    except KeyboardInterrupt:
        return 0
    except Exception:
        traceback.print_exc()
        input("\nkb-core stopped. Press Enter to close…")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
