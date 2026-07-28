#!/usr/bin/env python3
"""KB 最小 HTTP API：add、recall、health 與 proposals 人閘。"""
import argparse
import datetime
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import cfg

_RECALL = {}
_RECALL_LOCK = threading.Lock()
_PROPOSAL_LOCK = threading.Lock()
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
    return {
        name: {"ok": ok, "detail": detail}
        for name, (ok, detail) in results.items()
    }


def _proposal_status():
    with _PROPOSAL_LOCK:
        return dict(_PROPOSAL_JOB)


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
        from fastembed import TextEmbedding, SparseTextEmbedding
        from . import recall as kb_recall

        _RECALL.update({
            "client": QdrantClient(
                host=kb_recall.QDRANT_HOST,
                port=kb_recall.QDRANT_PORT,
                timeout=kb_recall.QDRANT_TIMEOUT,
            ),
            "models": models,
            "dense": TextEmbedding(kb_recall.DENSE_MODEL),
            "sparse": SparseTextEmbedding(kb_recall.SPARSE_MODEL),
            "kb_recall": kb_recall,
        })
        return _RECALL


def _recall(query, category=None, top_k=None):
    comps = _recall_components()
    kb_recall = comps["kb_recall"]
    hits = kb_recall.retrieve(
        comps["client"], comps["models"], comps["dense"], comps["sparse"],
        query, category, top_k or kb_recall.TOP_K,
    )
    kb_recall.log_query(query, category, hits)
    return [{
        "rank": i + 1,
        "category": h.payload.get("category"),
        "source_file": h.payload.get("source_file"),
        "score": round(h.score, 3),
        "text": h.payload.get("text", ""),
        "idx_title": h.payload.get("idx_title"),
    } for i, h in enumerate(hits)]


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
        if self.path == "/health":
            self._send_json(200, _health_json())
        elif self.path == "/proposals":
            from . import proposals

            doc = proposals.load()
            self._send_json(
                200,
                {
                    "updated_at": doc.get("updated_at"),
                    "proposals": doc.get("proposals", []),
                },
            )
        elif self.path == "/proposals/status":
            self._send_json(200, _proposal_status())
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        if not self._require_auth():
            return
        try:
            body = self._read_json()
        except Exception as e:
            self._send_json(400, {"error": f"invalid_json: {e}"})
            return

        if self.path == "/add":
            self._post_add(body)
        elif self.path == "/recall":
            self._post_recall(body)
        elif self.path == "/proposals":
            self._post_proposals(body)
        else:
            self._send_json(404, {"error": "not_found"})

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
        category = body.get("category")
        try:
            hits = _recall(query, category=category, top_k=top_k)
            self._send_json(200, {"query": query, "category": category, "hits": hits})
        except Exception as e:
            self._send_json(503, {
                "query": query,
                "category": category,
                "hits": [],
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=cfg("api.host", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(cfg("api.port", 8377)))
    args = ap.parse_args()

    host = args.host or "127.0.0.1"
    server = ThreadingHTTPServer((host, args.port), Handler)
    print(f"kb_api listening on http://{host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
