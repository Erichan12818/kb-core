#!/usr/bin/env python3
"""Container worker loop for kb-core schedules.

No crond dependency: this keeps a tiny in-process while/sleep loop and reads
schedule values from kb_config.yaml.
"""
import datetime as _dt
import time

from .config import cfg


def _parse_daily(value, fallback):
    text = str(value or fallback).strip()
    try:
        hh, mm = text.split(":", 1)
        return int(hh), int(mm)
    except Exception:
        return _parse_daily(fallback, "03:10")


def _parse_weekly(value, fallback):
    text = str(value or fallback).strip()
    parts = text.split()
    day = parts[0] if len(parts) == 2 else "Sun"
    hh, mm = _parse_daily(parts[1] if len(parts) == 2 else fallback.split()[1], "04:00")
    days = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
    return days.get(day[:3].title(), 6), hh, mm


def _run(label, func):
    try:
        print(f"[{_dt.datetime.now().isoformat(timespec='seconds')}] run {label}", flush=True)
        func()
    except SystemExit as e:
        print(f"[{label}] exit: {e}", flush=True)
    except Exception as e:
        print(f"[{label}] skipped: {type(e).__name__}: {e}", flush=True)


def _today_key(label):
    return f"{label}:{_dt.date.today().isoformat()}"


def main():
    import sys
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        print("usage: python -m kb.worker")
        print("Runs the kb-core schedule loop from kb_config.yaml.")
        return

    import kb.audit as audit
    import kb.catalog as catalog
    import kb.eval as kbeval
    import kb.health as health
    import kb.ingest as ingest

    health_every = int(cfg("schedule.health_interval_seconds", 14400) or 14400)
    ingest_h, ingest_m = _parse_daily(cfg("schedule.ingest_daily"), "03:10")
    catalog_h, catalog_m = _parse_daily(cfg("schedule.catalog_daily"), "03:20")
    audit_wd, audit_h, audit_m = _parse_weekly(cfg("schedule.audit_weekly"), "Sun 04:00")
    eval_wd, eval_h, eval_m = _parse_weekly(cfg("schedule.eval_weekly"), "Sun 04:30")

    last_health = 0.0
    done = set()
    print("kb-worker started", flush=True)
    while True:
        now = _dt.datetime.now()
        ts = time.time()
        if ts - last_health >= health_every:
            _run("health", health.main)
            last_health = ts
        if (now.hour, now.minute) == (ingest_h, ingest_m) and _today_key("ingest") not in done:
            _run("ingest", ingest.main)
            done.add(_today_key("ingest"))
        if (now.hour, now.minute) == (catalog_h, catalog_m) and _today_key("catalog") not in done:
            _run("catalog", catalog.main)
            done.add(_today_key("catalog"))
        if now.weekday() == audit_wd and (now.hour, now.minute) == (audit_h, audit_m) and _today_key("audit") not in done:
            _run("audit", audit.main)
            done.add(_today_key("audit"))
        if now.weekday() == eval_wd and (now.hour, now.minute) == (eval_h, eval_m) and _today_key("eval") not in done:
            _run("eval", kbeval.main)
            done.add(_today_key("eval"))
        if len(done) > 20:
            done = {x for x in done if x.endswith(_dt.date.today().isoformat())}
        time.sleep(30)


if __name__ == "__main__":
    main()
