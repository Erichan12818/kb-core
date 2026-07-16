#!/usr/bin/env python3
"""KB 通知薄層：command / webhook / off 三模式自動選擇。"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request

from .config import cfg

USER_AGENT = "kb-toolchain-notify/1.0"


def _warn(msg):
    print(f"⚠️ 通知略過：{msg}", file=sys.stderr)


def _send_command(command, source_id, title, message):
    subprocess.run(
        [command, source_id, title, message],
        check=False,
        timeout=60,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _send_webhook(webhook_url, title, message):
    fmt = (cfg("notify.format", "discord") or "discord").lower()
    if fmt == "slack":
        payload = {"text": f"*{title}*\n{message}"}
    else:
        payload = {"content": f"**{title}**\n{message}"}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()


def send(source_id, title, message):
    """按 config 自動送通知；任何錯誤只警告一行，永不拋出。"""
    try:
        command = os.path.expanduser(cfg("notify.command", "") or "")
        if command and os.path.isfile(command) and os.access(command, os.X_OK):
            _send_command(command, str(source_id), str(title), str(message))
            return True

        webhook_url = cfg("notify.webhook_url", "") or ""
        if webhook_url:
            _send_webhook(webhook_url, str(title), str(message))
            return True
    except Exception as e:
        _warn(f"{type(e).__name__}: {e}")
        return False
    return False


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    send_ap = sub.add_parser("send")
    send_ap.add_argument("source_id")
    send_ap.add_argument("title")
    send_ap.add_argument("message")
    args = ap.parse_args()

    if args.cmd == "send":
        send(args.source_id, args.title, args.message)


if __name__ == "__main__":
    main()
