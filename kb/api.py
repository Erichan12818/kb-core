#!/usr/bin/env python3
"""KB 最小 HTTP API：add、recall、health 與 proposals 人閘。"""
import argparse
import datetime
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .config import cfg
from . import store

UI_PATH = Path(__file__).with_name("static") / "ui.html"
_RECALL = {}
_RECALL_LOCK = threading.Lock()
_PROPOSAL_LOCK = threading.Lock()
_SCAN_LOCK = threading.Lock()
_CATALOG_LOCK = threading.Lock()
# One job of each kind at a time: the embedded store admits a single writer,
# and two concurrent runs would fight over the same manifest/INDEX.
_SCAN_JOB = {
    "running": False,
    "state": "idle",
    "ok": None,
    "message": "",
    "output": [],
    "started_at": None,
    "finished_at": None,
}
_CATALOG_JOB = dict(_SCAN_JOB)
_PROPOSAL_JOB = {
    "running": False,
    "state": "idle",
    "id": None,
    "ok": None,
    "message": "",
    "started_at": None,
    "finished_at": None,
}


def _json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _health_json():
    from . import health as kb_health

    results = kb_health.check()
    checks = {
        name: {"ok": ok, "detail": detail}
        for name, (ok, detail) in results.items()
    }
    # Keep the original five top-level check keys for existing HTTP clients.
    return {
        **checks,
        "checks": checks,
        "directories": _directory_stats(),
    }


def _directory_stats():
    root = Path(cfg("kb_root"))
    stats = {}
    for name in ("raw_files", "notes", "catalog", "trash", "state"):
        directory = root / name
        files = 0
        size = 0
        if directory.is_dir():
            for dirpath, _, filenames in os.walk(directory):
                for filename in filenames:
                    files += 1
                    try:
                        size += (Path(dirpath) / filename).stat().st_size
                    except OSError:
                        pass
        stats[name] = {
            "exists": directory.is_dir(),
            "files": files,
            "bytes": size,
        }
    return stats


def _proposal_status():
    with _PROPOSAL_LOCK:
        return dict(_PROPOSAL_JOB)


def _scan_status():
    with _SCAN_LOCK:
        return dict(_SCAN_JOB)


class _Tee:
    """Write through to the original stream while recording a copy.

    ingest reports through print(), and a Finder launch has nowhere for that to
    go — so the scan records it for the UI. It must *tee* rather than redirect:
    sys.stdout is process-wide, so swapping it out would swallow every other
    thread's output for the duration of the scan, including the HTTP log.
    """

    def __init__(self, original, buffer, owner):
        self._original = original
        self._buffer = buffer
        # Only the scan's own thread contributes to the recorded output;
        # everything else (request logs, the schedule thread) passes straight
        # through. Without this the UI would show unrelated lines.
        self._owner = owner

    def write(self, text):
        if threading.get_ident() == self._owner:
            self._buffer.write(text)
        if self._original is not None:
            try:
                self._original.write(text)
            except Exception:
                pass  # a frozen GUI build can have a closed stdout
        return len(text)

    def flush(self):
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass

    def isatty(self):
        return bool(self._original is not None and getattr(self._original, "isatty", bool)())


def _run_job(lock, job, target, ok_message, running_message):
    """Run ``target`` in a background thread, tee-ing its print() output.

    Shared by scan (ingest) and catalog rebuild (index_update) — same shape,
    same failure modes (SystemExit on "nothing to do", exceptions on real
    failure), same reason to keep the request log flowing through undisturbed.
    """
    import io
    import sys as _sys

    buffer = io.StringIO()
    owner = threading.get_ident()
    saved_out, saved_err = _sys.stdout, _sys.stderr
    _sys.stdout = _Tee(saved_out, buffer, owner)
    _sys.stderr = _Tee(saved_err, buffer, owner)
    try:
        target()
        ok, message = True, ok_message
    except SystemExit as exc:
        ok, message = False, str(exc) or "Stopped."
    except Exception as exc:
        ok, message = False, f"{type(exc).__name__}: {exc}"
    finally:
        _sys.stdout, _sys.stderr = saved_out, saved_err

    output = buffer.getvalue().strip().splitlines()
    with lock:
        job.update({
            "running": False,
            "state": "completed" if ok else "failed",
            "ok": ok,
            "message": message,
            "output": output[-40:],
            "finished_at": datetime.datetime.now().isoformat(timespec="seconds"),
        })


def _start_job(lock, job, thread_name, target, running_message, ok_message):
    with lock:
        if job["running"]:
            return False, dict(job)
        job.update({
            "running": True,
            "state": "running",
            "ok": None,
            "message": running_message,
            "output": [],
            "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
        })
        status = dict(job)
    threading.Thread(
        target=_run_job, args=(lock, job, target, ok_message, running_message),
        name=thread_name, daemon=True,
    ).start()
    return True, status


def _scan_target():
    from . import ingest as kb_ingest

    kb_ingest.main()


def _start_scan():
    return _start_job(_SCAN_LOCK, _SCAN_JOB, "kb-scan", _scan_target,
                       "Scanning…", "Scan finished.")


def _catalog_target():
    from . import index_update

    index_update.main()


def _start_catalog_rebuild():
    return _start_job(_CATALOG_LOCK, _CATALOG_JOB, "kb-catalog", _catalog_target,
                       "Rebuilding catalog…", "Catalog rebuilt.")


def _catalog_status():
    with _CATALOG_LOCK:
        return dict(_CATALOG_JOB)


def _run_proposal_apply(pid):
    from . import apply as kb_apply

    try:
        ok, message = kb_apply.apply_proposal(pid)
    except Exception as exc:
        ok = False
        message = f"{type(exc).__name__}: {exc}"
    with _PROPOSAL_LOCK:
        _PROPOSAL_JOB.update(
            {
                "running": False,
                "state": "completed" if ok else "failed",
                "ok": ok,
                "message": message,
                "finished_at": datetime.datetime.now().isoformat(
                    timespec="seconds"
                ),
            }
        )


def _start_proposal_apply(pid):
    with _PROPOSAL_LOCK:
        if _PROPOSAL_JOB["running"]:
            return False, dict(_PROPOSAL_JOB)
        _PROPOSAL_JOB.update(
            {
                "running": True,
                "state": "running",
                "id": pid,
                "ok": None,
                "message": "",
                "started_at": datetime.datetime.now().isoformat(
                    timespec="seconds"
                ),
                "finished_at": None,
            }
        )
        status = dict(_PROPOSAL_JOB)
    threading.Thread(
        target=_run_proposal_apply,
        args=(pid,),
        name=f"kb-proposal-{pid}",
        daemon=True,
    ).start()
    return True, status


def _recall_components():
    if _RECALL:
        return _RECALL
    with _RECALL_LOCK:
        if _RECALL:
            return _RECALL
        from qdrant_client import QdrantClient, models
        from . import embedding
        from . import recall as kb_recall

        _RECALL.update({
            "client": store.connect(kb_recall.QDRANT_TIMEOUT),
            "models": models,
            "dense": embedding.dense(kb_recall.DENSE_MODEL),
            "sparse": embedding.sparse(kb_recall.SPARSE_MODEL),
            "kb_recall": kb_recall,
        })
        return _RECALL


def _recall(query, category=None, top_k=None, force_global=False):
    comps = _recall_components()
    kb_recall = comps["kb_recall"]
    outcome = kb_recall.retrieve_two_stage(
        comps["client"], comps["models"], comps["dense"], comps["sparse"],
        query, category, top_k or kb_recall.TOP_K, force_global=force_global,
    )
    hits = outcome["hits"]
    effective_category = category or outcome["category_guess"]
    kb_recall.log_query(query, effective_category, hits)
    results = [
        kb_recall.hit_to_result(hit, index + 1)
        for index, hit in enumerate(hits)
    ]
    return {
        "category_guess": outcome["category_guess"],
        "confidence": outcome["confidence"],
        "hits": results,
        "groups": (
            kb_recall.group_results(results) if outcome["grouped"] else []
        ),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "KBAPI/0.1"

    def log_message(self, fmt, *args):
        print("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def _send_json(self, status, payload):
        data = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        token = cfg("api.token", "") or ""
        if not token:
            return True
        return self.headers.get("Authorization") == f"Bearer {token}"

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _require_auth(self):
        if self._authorized():
            return True
        self._send_json(401, {"error": "unauthorized"})
        return False

    def do_GET(self):
        if not self._require_auth():
            return
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(200, _health_json())
        elif path == "/ui":
            try:
                self._send_html(200, UI_PATH.read_bytes())
            except OSError as exc:
                self._send_json(
                    500,
                    {"error": f"UI unavailable ({type(exc).__name__}): {exc}"},
                )
        elif path == "/taxonomy":
            from .recall import taxonomy_categories

            self._send_json(200, {"categories": taxonomy_categories()})
        elif path == "/capabilities":
            # Lets the UI hide surfaces this deployment has not turned on,
            # instead of showing a tab that only ever returns an error.
            from . import chat as kb_chat

            self._send_json(200, {"chat": kb_chat.is_enabled()})
        elif path == "/settings":
            from . import settings as kb_settings

            self._send_json(200, kb_settings.read_settings())
        elif path == "/proposals":
            from . import proposals

            doc = proposals.load()
            self._send_json(
                200,
                {
                    "updated_at": doc.get("updated_at"),
                    "proposals": doc.get("proposals", []),
                },
            )
        elif path == "/proposals/status":
            self._send_json(200, _proposal_status())
        elif path == "/scan":
            self._send_json(200, _scan_status())
        elif path == "/catalog":
            self._send_json(200, _catalog_status())
        elif path == "/documents":
            self._get_documents()
        else:
            self._send_json(404, {"error": "not_found"})

    def _get_documents(self):
        """The tag/catalog browser's data: every entry in INDEX.json."""
        from . import index as kb_index

        try:
            index = kb_index.load_index()
        except Exception as exc:
            self._send_json(503, {"error": f"{type(exc).__name__}: {exc}"})
            return
        docs = [
            {
                "key": key,
                "source_file": entry.get("source_file"),
                "category": entry.get("category"),
                "title": entry.get("title") or entry.get("source_file"),
                "summary": entry.get("summary") or "",
                "topics": entry.get("topics") or [],
                "sensitivity": entry.get("sensitivity"),
                "indexed_at": entry.get("indexed_at"),
            }
            for key, entry in index.items()
        ]
        docs.sort(key=lambda d: (d["category"] or "", d["title"] or ""))
        self._send_json(200, {"documents": docs, "count": len(docs)})

    def do_POST(self):
        if not self._require_auth():
            return
        try:
            body = self._read_json()
        except Exception as e:
            self._send_json(400, {"error": f"invalid_json: {e}"})
            return

        path = urlsplit(self.path).path
        if path == "/add":
            self._post_add(body)
        elif path == "/recall":
            self._post_recall(body)
        elif path == "/proposals":
            self._post_proposals(body)
        elif path == "/chat":
            self._post_chat(body)
        elif path == "/settings":
            self._post_settings(body)
        elif path == "/scan":
            started, status = _start_scan()
            self._send_json(202 if started else 409, status)
        elif path == "/catalog":
            started, status = _start_catalog_rebuild()
            self._send_json(202 if started else 409, status)
        else:
            self._send_json(404, {"error": "not_found"})

    def _post_settings(self, body):
        """Apply settings from the UI form. The key is write-only over HTTP."""
        from . import settings as kb_settings

        ok, message, current, restart_needed = kb_settings.write_settings(body)
        self._send_json(
            200 if ok else 400,
            {
                "ok": ok,
                "message": message,
                "settings": current,
                "restart_needed": restart_needed,
            },
        )

    def _post_chat(self, body):
        """Answer from retrieved excerpts only. Disabled unless a chat role is configured."""
        from . import chat as kb_chat

        question = (body.get("question") or "").strip()
        if not question:
            self._send_json(400, {"error": "question required"})
            return
        if not kb_chat.is_enabled():
            self._send_json(503, {
                "error": "chat_disabled",
                "message": "對話功能未啟用：kb_config.yaml 嘅 llm.roles 未設 chat 角色。",
            })
            return
        history = body.get("history")
        history = history if isinstance(history, list) else []
        category = body.get("category")
        try:
            top_k = int(body.get("top_k") or 6)
        except (TypeError, ValueError):
            top_k = 6
        try:
            found = _recall(question, category=category, top_k=top_k)
            result = kb_chat.answer(question, found["hits"], history)
            result["category_guess"] = found.get("category_guess")
            self._send_json(200, result)
        except Exception as e:
            self._send_json(503, {
                "error": f"{type(e).__name__}",
                "message": str(e)[:400],
            })

    def _post_add(self, body):
        content = (body.get("content") or "").strip()
        if not content:
            self._send_json(400, {"error": "content required"})
            return
        category = (body.get("category") or "inbox").strip() or "inbox"
        title = body.get("title")
        try:
            from . import add as kb_add
            result = kb_add.add_entry(content, category=category, title=title, ingest=True, async_ingest=True)
            self._send_json(201, {"file": result["file"], "path": result["path"], "ingest": "queued"})
        except Exception as e:
            self._send_json(503, {"error": f"KB 入庫不可用（{type(e).__name__}）：{e}"})

    def _post_recall(self, body):
        query = (body.get("query") or "").strip()
        if not query:
            self._send_json(400, {"error": "query required"})
            return
        try:
            top_k = int(body.get("top_k") or cfg("recall.top_k"))
        except Exception:
            top_k = cfg("recall.top_k")
        category = str(body.get("category") or "").strip() or None
        force_global = body.get("global") is True
        try:
            result = _recall(
                query,
                category=category,
                top_k=top_k,
                force_global=force_global,
            )
            self._send_json(
                200,
                {"query": query, "category": category, **result},
            )
        except Exception as e:
            self._send_json(503, {
                "query": query,
                "category": category,
                "category_guess": None,
                "confidence": 0.0,
                "hits": [],
                "groups": [],
                "error": f"KB 檢索不可用（{type(e).__name__}）：{e}",
            })

    def _post_proposals(self, body):
        operation = str(body.get("op") or "").strip().lower()
        pid = str(body.get("id") or "").strip()
        note = str(body.get("note") or "").strip()
        if operation not in ("apply", "reject", "dryrun"):
            self._send_json(
                400, {"error": "op must be apply, reject, or dryrun"}
            )
            return
        if not pid:
            self._send_json(400, {"error": "id required"})
            return

        if operation == "apply":
            started, status = _start_proposal_apply(pid)
            if not started:
                self._send_json(
                    409, {"error": "proposal job already running", "job": status}
                )
                return
            self._send_json(202, {"accepted": True, "job": status})
            return

        if _proposal_status()["running"]:
            self._send_json(
                409,
                {
                    "error": "proposal job already running",
                    "job": _proposal_status(),
                },
            )
            return

        from . import apply as kb_apply

        try:
            if operation == "dryrun":
                ok, message = kb_apply.apply_proposal(pid, dry_run=True)
            else:
                ok, message = kb_apply.reject_proposal(pid, note)
            self._send_json(
                200 if ok else 409,
                {"ok": ok, "op": operation, "id": pid, "message": message},
            )
        except Exception as exc:
            self._send_json(
                500,
                {
                    "ok": False,
                    "op": operation,
                    "id": pid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=cfg("api.host", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(cfg("api.port", 8377)))
    args = ap.parse_args(argv)

    host = args.host or "127.0.0.1"
    server = ThreadingHTTPServer((host, args.port), Handler)
    print(f"kb_api listening on http://{host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
