#!/usr/bin/env python3
"""Loop 2 人閘：結構化建議的 schema、指紋去重與本地狀態讀寫。"""
import datetime
import hashlib
import json
from pathlib import Path

from .config import atomic_write_text, cfg


KB_ROOT = Path(cfg("kb_root"))
PROPOSALS_PATH = KB_ROOT / "state" / "PROPOSALS.json"
BACKUP_DIR = KB_ROOT / "state" / "backups"

ACTIONS = ("move_files", "archive_files")
STATUSES = ("pending", "applied", "rejected")


def _blank():
    return {"updated_at": None, "proposals": [], "history": []}


def load():
    """讀 PROPOSALS.json；不存在或壞檔時回空結構，讓空 vault 可直接起步。"""
    try:
        doc = json.loads(Path(PROPOSALS_PATH).read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return _blank()
        doc.setdefault("proposals", [])
        doc.setdefault("history", [])
        return doc
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return _blank()


def save(doc):
    doc["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    atomic_write_text(PROPOSALS_PATH, json.dumps(doc, ensure_ascii=False, indent=2))


def fingerprint(action, params):
    """由決定性內容建立指紋，不包含日期、id 或文案。"""
    if action == "move_files":
        key = sorted(
            f"{move.get('file')}>{move.get('to_category')}"
            for move in params.get("moves", [])
        )
    elif action == "archive_files":
        key = sorted(str(item.get("file")) for item in params.get("files", []))
    else:
        key = [json.dumps(params, ensure_ascii=False, sort_keys=True)]
    raw = action + "|" + "|".join(key)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def rejected_fingerprints(doc):
    return {
        item.get("fingerprint")
        for item in doc.get("history", [])
        if item.get("status") == "rejected"
    }


def applied_fingerprints(doc):
    return {
        item.get("fingerprint")
        for item in doc.get("history", [])
        if item.get("status") == "applied"
    }


def new_id(today, seq):
    return f"P-{today}-{seq:02d}"


def make(action, title, rationale, params, risk="high", today=None, seq=1):
    today = today or datetime.date.today().isoformat()
    return {
        "id": new_id(today, seq),
        "action": action,
        "risk": risk,
        "status": "pending",
        "title": title,
        "rationale": rationale,
        "params": params,
        "fingerprint": fingerprint(action, params),
        "created_at": today,
    }


def get(doc, pid):
    for proposal in doc.get("proposals", []):
        if proposal.get("id") == pid:
            return proposal
    return None


def resolve(doc, pid, status, note=""):
    """只處理 pending 建議，移出清單並記入 history；回 ``(ok, message)``。"""
    if status not in ("applied", "rejected"):
        return False, f"不支援的狀態：{status}"
    proposal = get(doc, pid)
    if not proposal:
        return False, f"搵唔到建議 {pid}"
    if proposal.get("status") != "pending":
        return False, f"{pid} 已經係 {proposal.get('status')}，唔會重複處理"

    proposal["status"] = status
    proposal["resolved_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    if note:
        proposal["note"] = note
    doc["proposals"] = [
        item for item in doc["proposals"] if item.get("id") != pid
    ]
    doc.setdefault("history", []).append(proposal)
    return True, f"{pid} → {status}"


def merge_pending(doc, fresh, today=None):
    """合併新建議，略過已駁回、已落實或已 pending 的相同指紋。"""
    today = today or datetime.date.today().isoformat()
    skip = rejected_fingerprints(doc) | applied_fingerprints(doc)
    existing = {
        proposal.get("fingerprint") for proposal in doc.get("proposals", [])
    }
    added = 0
    seq = len(doc.get("proposals", [])) + 1
    for proposal in fresh:
        fp = proposal.get("fingerprint")
        if fp in skip or fp in existing:
            continue
        proposal["id"] = new_id(today, seq)
        doc.setdefault("proposals", []).append(proposal)
        existing.add(fp)
        seq += 1
        added += 1
    return doc, added


def backup_state(tag):
    """執行前快照 INDEX/TAXONOMY/manifest；失敗會拋錯以阻止執行。"""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = Path(BACKUP_DIR) / f"{stamp}-{tag}"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("INDEX.json", "TAXONOMY.json", "ingest_manifest.json"):
        source = Path(KB_ROOT) / "state" / name
        if source.exists():
            destination.joinpath(name).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )
    return str(destination)
