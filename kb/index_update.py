#!/usr/bin/env python3
"""
index_update.py — Loop 1：增量更新知識目錄（ingest 後自動觸發，或手動跑）

- 只對「新/改」檔叫 LLM 分類（貴）；關係對全庫重算（平，無 LLM）。
- 寫 INDEX.json + 各檔 payload（idx_title/summary/topics）+ 渲染 MOC（Loop 3）。
- 首次自動 seed TAXONOMY.json。
用法：python index_update.py [--all]   （--all 強制重分類全部）
"""
import sys, datetime
from qdrant_client import QdrantClient
from pathlib import Path
from . import index as ic
from . import store

def main():
    force_all = "--all" in sys.argv
    today = datetime.date.today()
    review_by = (today + datetime.timedelta(days=ic.REVIEW_DAYS)).isoformat()

    client = store.connect(ic.QDRANT_TIMEOUT)
    files = ic.scan_collection(client)
    if not files:
        sys.exit("❌ collection 無資料，先跑 ingest.py")
    old = ic.load_index()

    # 判斷要重分類邊啲檔（新 / hash 變 / 之前退標籤 / --all）
    changed = []
    for fn, f in files.items():
        prev = old.get(fn)
        if force_all or not prev or prev.get("hash") != f["hash"] or not prev.get("topics"):
            changed.append(fn)

    print(f"📚 全庫 {len(files)} 檔；需重分類 {len(changed)} 檔" + ("（--all 全量）" if force_all else "（增量）"))

    # (1) 分類（只 changed）
    labels = {}
    for i, fn in enumerate(sorted(changed), 1):
        f = files[fn]
        try:
            text = Path(f["source_path"]).read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = fn
        title, summary, topics, via = ic.classify(f["category"], text, f["sensitivity"])
        base = f.get("source_file", fn)
        stem = base[:-3] if base.endswith(".md") else base
        labels[fn] = {"title": title or stem, "summary": summary, "topics": topics}
        if topics:
            ic.enrich_payload(client, f["category"], base, title or stem, summary, topics)
        print(f"  [{i}/{len(changed)}] {f['category']}/{fn[:32]} → 「{title or stem}」 {topics}  ←{via}")

    # (2) 關係（全庫重算，平）
    print("🔗 計算全庫關係…")
    rel = ic.compute_relations(files)

    # (3) 砌 INDEX：changed 用新標籤，其餘沿用舊標籤；關係一律更新
    index = {}
    for fn, f in files.items():
        lab = labels.get(fn) or {k: old.get(fn, {}).get(k) for k in ("title", "summary", "topics")}
        index[fn] = {
            "source_file": f.get("source_file", fn), "category": f["category"], "sensitivity": f["sensitivity"],
            "title": lab.get("title") or (fn[:-3] if fn.endswith(".md") else fn),
            "summary": lab.get("summary") or "", "topics": lab.get("topics") or [],
            "related": rel[fn]["related"], "near_duplicates": rel[fn]["near_duplicates"],
            "review_by": old.get(fn, {}).get("review_by", review_by) if fn not in changed else review_by,
            "hash": f["hash"], "indexed_at": today.isoformat(),
        }
    ic.save_index(index)

    # taxonomy 同步（首次 seed；其後把新 category/topics 併入，瘦身交 Loop2）
    _, tax_changed = ic.sync_taxonomy_with_index(index, today.isoformat())
    if tax_changed:
        print(f"🌱 TAXONOMY.json 已同步（新 category/topics 併入）")

    ncat, nfile = ic.render_catalog(index, today.isoformat())
    print(f"\n✅ Loop1 完成：{nfile} 檔 / {ncat} 類")
    print(f"   INDEX → {ic.INDEX_PATH}")
    print(f"   主目錄 → {ic.CAT_DIR}/KB_CATALOG.md ｜ 各類 _MOC_<cat>.md")

if __name__ == "__main__":
    main()
