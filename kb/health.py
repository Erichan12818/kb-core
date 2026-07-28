#!/usr/bin/env python3
"""
kb_health.py — KB 全鏈路健康檢查（只在狀態變化時推播，避免騷擾）

檢查四層：
  1. KB root / raw_files 可用
  2. Qdrant 可達 + collection 有 points
  3. Ollama 可達（分類/圖片轉化靠佢）
  4. INDEX.json 新鮮度（超過 N 日未更新 = Loop1 可能斷咗）

推播：經 kb_notify（command / webhook / off），source_id=kbtoolchain.health。
狀態存 state 檔，只有「好→壞」或「壞→好」轉變先推播；持續壞唔重複嘈。

用法：
  python3 kb_health.py            # 檢查 + 必要時推播
  python3 kb_health.py --status   # 只印狀態唔推播
零外部依賴（stdlib only），可直接俾 launchd 跑。
"""
import os, sys, json, urllib.request, urllib.error, datetime
from pathlib import Path
from .config import cfg
from . import notify as kb_notify

KB_ROOT    = cfg("kb_root")
INDEX_PATH = os.path.join(KB_ROOT, "state", "INDEX.json")
QDRANT_URL = f"http://{cfg('qdrant.host')}:{cfg('qdrant.port')}/collections/{cfg('qdrant.collection')}"
OLLAMA_URL = f"{cfg('llm.ollama_url').rstrip('/')}/api/tags"
STATE_PATH = os.path.join(KB_ROOT, "state", ".kb_health_state.json")
INDEX_STALE_DAYS = 3
SOURCE_ID  = "kbtoolchain.health"


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _vault_is_empty():
    """True when no source file has ever been added.

    A brand new install has no collection and no INDEX.json, and reporting that
    as a failure makes a working install look broken on first run. The same
    artifacts missing while raw_files has content is a real problem, so the two
    cases have to be told apart rather than both painted red.
    """
    raw_root = os.path.join(KB_ROOT, "raw_files")
    if not os.path.isdir(raw_root):
        return True
    for _, _, files in os.walk(raw_root):
        if any(not f.startswith(".") for f in files):
            return False
    return True


def _local_provider_in_use():
    """True when any configured role (or its fallback) routes to the local provider."""
    if str(cfg("llm.sensitive_provider", "local")).strip() == "local":
        return True
    for role in (cfg("llm.roles", {}) or {}).values():
        if not isinstance(role, dict):
            continue
        if role.get("provider") == "local":
            return True
        fallback = role.get("fallback")
        if isinstance(fallback, dict) and fallback.get("provider") == "local":
            return True
    return False


def check():
    """回 {check_name: (ok: bool, detail: str)}"""
    out = {}
    empty = _vault_is_empty()

    out["nas"] = (os.path.isdir(os.path.join(KB_ROOT, "raw_files")),
                  KB_ROOT)

    try:
        d = _get(QDRANT_URL)
        pts = d["result"]["points_count"]
        if pts == 0 and empty:
            out["qdrant"] = (True, "已連上，未有資料（尚未加入任何筆記）")
        else:
            out["qdrant"] = (pts > 0, f"{pts} points")
    except urllib.error.HTTPError as e:
        # 404 means the collection has not been created yet — expected until the
        # first ingest, but suspicious once raw_files has content.
        if e.code == 404 and empty:
            out["qdrant"] = (True, "已連上，collection 未建（尚未加入任何筆記）")
        elif e.code == 404:
            out["qdrant"] = (False, "collection 未建，但 raw_files 已有內容——請跑 ingest")
        else:
            out["qdrant"] = (False, f"HTTP {e.code}")
    except Exception as e:
        out["qdrant"] = (False, f"{type(e).__name__}")

    try:
        _get(OLLAMA_URL, timeout=3)
        out["ollama"] = (True, "up")
    except Exception as e:
        # Ollama backs the optional local-llm profile. It being absent is a
        # deployment choice, not a fault, so only call it a failure when the
        # config actually routes a role to the local provider.
        if _local_provider_in_use():
            out["ollama"] = (
                False,
                "連唔上，但 config 有角色路由到 local——"
                "開 local-llm profile，或者改 llm.roles 全部行 cloud",
            )
        else:
            out["ollama"] = (True, "未啟用（可選 local-llm profile）")

    try:
        age_days = (datetime.datetime.now().timestamp() - os.path.getmtime(INDEX_PATH)) / 86400
        out["index_fresh"] = (age_days <= INDEX_STALE_DAYS, f"{age_days:.1f} 日前更新")
    except OSError:
        if empty:
            out["index_fresh"] = (True, "尚未建立（未加入任何筆記）")
        else:
            out["index_fresh"] = (False, "INDEX.json 不存在/讀不到")

    out["integrity"] = check_integrity()
    return out


def check_integrity():
    """數據鏈路完整性：raw_files ↔ ingest_manifest ↔ INDEX 對齊 + 檔名碰撞。
    NAS 未掛時回 (True, 'NAS 未掛，跳過')——唔重複報 nas 檢查嘅故障。
    全新未用過嘅 vault 同樣跳過：state 檔未生成係預期，唔係損毀。"""
    raw_root = os.path.join(KB_ROOT, "raw_files")
    if not os.path.isdir(raw_root):
        return (True, "NAS 未掛，跳過")
    if _vault_is_empty():
        return (True, "尚未加入任何筆記，跳過")
    try:
        manifest = json.loads(Path(os.path.join(KB_ROOT, "state", "ingest_manifest.json")).read_text())
        index = json.loads(Path(INDEX_PATH).read_text())
    except Exception as e:
        return (False, f"state 檔讀不到：{type(e).__name__}")

    ingestable = {".txt", ".md", ".json", ".yaml", ".yml", ".pdf"}   # 同 ingest.py SUPPORTED_EXT
    raw, base_seen, collisions = [], {}, []
    for dirpath, dirs, files in os.walk(raw_root):
        for f in files:
            if f.startswith(".") or os.path.splitext(f)[1].lower() not in ingestable:
                continue                    # 圖片等由 sidecar 代表，唔直接 ingest
            p = os.path.join(dirpath, f)
            raw.append(p)
            if f in base_seen:
                collisions.append(f)
            base_seen[f] = p

    problems = []
    ghost = [p for p in manifest if p not in set(raw)]          # manifest 有、實體無
    missing = [p for p in raw if p not in manifest]             # 實體有、未 ingest
    if ghost:
        problems.append(f"manifest 幽靈 {len(ghost)}")
    if missing:
        problems.append(f"未 ingest {len(missing)}")
    if collisions:
        problems.append(f"檔名碰撞 {len(collisions)}（{collisions[0]}…）")
    # INDEX 條目數同 manifest 差距大 = 編目斷鏈（碰撞消歧 key 會令兩者略有出入，容差 3）
    if abs(len(index) - len(manifest)) > 3:
        problems.append(f"INDEX {len(index)} vs manifest {len(manifest)} 差距異常")

    if problems:
        return (False, "；".join(problems))
    return (True, f"raw {len(raw)} = manifest {len(manifest)} ≈ INDEX {len(index)}，無碰撞")


def load_state():
    try:
        return json.loads(Path(STATE_PATH).read_text())
    except Exception:
        return {}


def save_state(s):
    # On a fresh vault state/ does not exist yet, and the first scheduled health
    # run would otherwise die before it could report anything.
    Path(STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_PATH).write_text(json.dumps(s, ensure_ascii=False))


def notify(title, msg):
    kb_notify.send(SOURCE_ID, title, msg)


def main():
    status_only = "--status" in sys.argv
    results = check()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    for name, (ok, detail) in results.items():
        lines.append(f"{'✅' if ok else '❌'} {name}: {detail}")
    print(f"KB 健康檢查 {now}")
    print("\n".join(lines))

    if status_only:
        return

    prev = load_state().get("failing", [])
    failing = sorted(name for name, (ok, _) in results.items() if not ok)

    newly_broken = [n for n in failing if n not in prev]
    recovered    = [n for n in prev if n not in failing]

    if newly_broken:
        detail = "；".join(f"{n}: {results[n][1]}" for n in newly_broken)
        notify(f"❌ KB 健康檢查：{len(newly_broken)} 項故障", detail)
    if recovered and not failing:
        notify("✅ KB 健康檢查：全部恢復", f"已恢復：{'、'.join(recovered)}")
    elif recovered:
        notify(f"🔶 KB 部分恢復：{'、'.join(recovered)}",
               f"仍故障：{'、'.join(failing)}")

    save_state({"failing": failing, "checked_at": now})


if __name__ == "__main__":
    main()
