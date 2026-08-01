#!/usr/bin/env python3
"""Small stdio MCP server exposing kb_recall, kb_add, and kb_status."""
from __future__ import annotations

import datetime
import json
import sys
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from .config import cfg
from . import store

SERVER_VERSION = "0.2.1"
_RECALL: dict[str, Any] = {}
_LOCK = threading.Lock()

TOOLS = [
    {
        "name": "kb_recall",
        "description": "Search the private KB for prior decisions, project context, and notes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "category": {"type": "string"},
                "top_k": {"type": "integer", "default": 4, "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kb_add",
        "description": "Save text or a URL to the private KB and queue incremental ingest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "category": {"type": "string", "default": "inbox"},
                "title": {"type": "string"},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kb_status",
        "description": "Check storage, Qdrant, Ollama, index freshness, and KB integrity.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def _text(value: str, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": value}], "isError": error}


def _components() -> dict[str, Any]:
    if _RECALL:
        return _RECALL
    with _LOCK:
        if not _RECALL:
            from fastembed import SparseTextEmbedding, TextEmbedding
            from qdrant_client import QdrantClient, models
            from . import recall

            _RECALL.update(
                client=store.connect(recall.QDRANT_TIMEOUT),
                models=models,
                dense=TextEmbedding(recall.DENSE_MODEL),
                sparse=SparseTextEmbedding(recall.SPARSE_MODEL),
                recall=recall,
            )
    return _RECALL


def _recall(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return _text("query cannot be empty", True)
    category = str(args.get("category") or "").strip() or None
    try:
        top_k = max(1, min(int(args.get("top_k") or cfg("recall.top_k", 4)), 20))
        c = _components()
        hits = c["recall"].retrieve(
            c["client"], c["models"], c["dense"], c["sparse"], query, category, top_k
        )
        c["recall"].log_query(query, category, hits)
        lines = [f"## KB recall: {query}", f"> {len(hits)} raw result(s)", ""]
        for index, hit in enumerate(hits, 1):
            payload = hit.payload or {}
            lines.extend(
                [
                    f"[{index}] {payload.get('category')}/{payload.get('source_file')} (score {hit.score:.3f})",
                    str(payload.get("text") or "").strip(),
                    "",
                ]
            )
        return _text("\n".join(lines).rstrip())
    except Exception as exc:
        return _text(f"KB unavailable ({type(exc).__name__})")


def _add(args: dict[str, Any]) -> dict[str, Any]:
    content = str(args.get("content") or "").strip()
    if not content:
        return _text("content cannot be empty", True)
    try:
        from .add import add_entry

        result = add_entry(
            content,
            category=str(args.get("category") or "inbox"),
            title=str(args.get("title") or "").strip() or None,
            ingest=True,
            async_ingest=True,
        )
        return _text(f"Saved to {result['file']}; incremental ingest queued.")
    except Exception as exc:
        return _text(f"KB add failed ({type(exc).__name__}: {exc})", True)


def _status(_: dict[str, Any]) -> dict[str, Any]:
    try:
        from .health import check

        lines = [f"## KB status ({datetime.datetime.now():%Y-%m-%d %H:%M:%S})", ""]
        for name, (ok, detail) in check().items():
            lines.append(f"- {'OK' if ok else 'FAIL'} {name}: {detail}")

        index_path = Path(cfg("kb_root")) / "state" / "INDEX.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        values = data.values() if isinstance(data, dict) else data
        counts = Counter(str(item.get("category") or "uncategorized") for item in values)
        lines.extend(["", f"Documents: {sum(counts.values())}"])
        lines.extend(f"- {category}: {count}" for category, count in sorted(counts.items()))
        return _text("\n".join(lines))
    except Exception as exc:
        return _text(f"KB status unavailable ({type(exc).__name__}: {exc})")


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "kb_recall":
        return _recall(args)
    if name == "kb_add":
        return _add(args)
    if name == "kb_status":
        return _status(args)
    raise KeyError(name)


def _handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        protocol = (request.get("params") or {}).get("protocolVersion", "2024-11-05")
        result = {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "kb-mcp", "version": SERVER_VERSION},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params") or {}
        try:
            result = _call(str(params.get("name")), params.get("arguments") or {})
        except KeyError:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Unknown tool"}}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Unknown method"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = _handle(json.loads(line))
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Invalid request: {exc}"},
            }
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
