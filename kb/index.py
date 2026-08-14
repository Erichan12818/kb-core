#!/usr/bin/env python3
"""
index_core.py — 自我演進知識目錄：核心引擎（共用庫）

被三迴圈共用：
  index_update.py   Loop 1 — ingest 時分類 + 關係 + 寫 INDEX/payload
  taxonomy_audit.py Loop 2 — 週審分類法（孤兒/過期/高相似/重整建議 → 報告）
  catalog.py        Loop 3 — 由 INDEX 渲染 KB_CATALOG.md + 各類 MOC

設計：KB_INDEX_MECHANISM_DESIGN.md
路由：交由 llm_router 按 kb_config.yaml 決定；sensitive 一律不出機。
"""
import os, re, json, datetime
from collections import defaultdict
from pathlib import Path
from qdrant_client import QdrantClient, models
from .config import cfg
from . import store
from . import llm as llm_router

KB_ROOT   = cfg("kb_root")
INDEX_PATH    = os.path.join(KB_ROOT, "state", "INDEX.json")
TAXONOMY_PATH = os.path.join(KB_ROOT, "state", "TAXONOMY.json")
CAT_DIR       = os.path.join(KB_ROOT, "catalog")

QDRANT_HOST, QDRANT_PORT, COLLECTION = cfg("qdrant.host"), cfg("qdrant.port"), cfg("qdrant.collection")
QDRANT_TIMEOUT = cfg("qdrant.timeout_batch")
NEAR_DUP, TOP_RELATED = 0.98, 3
MIN_CHARS = 200
REVIEW_DAYS = 90
META_SKIP = {"MEMORY.md", "CLAUDE.md"}                 # 索引/指針檔，詞彙廣泛重疊，不參與去重
ECHO_HINTS = ("唔好用檔名", "主題標籤", "8到20字", "40字內", "2到6字")

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------- 使用者分類政策 ----------
TAXONOMY_POLICY_PATHS = [
    os.path.join(KB_ROOT, "taxonomy_policy.md"),
    os.path.expanduser("~/KnowledgeBase/taxonomy_policy.md"),
]

def load_taxonomy_policy():
    """讀取使用者分類指引。NAS 未掛載或文件不存在時靜默回空字串。"""
    import logging
    for path in TAXONOMY_POLICY_PATHS:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
        except (FileNotFoundError, OSError, PermissionError) as e:
            logging.debug("load_taxonomy_policy: 跳過 %s (%s)", path, e)
    return ""


# ---------- LLM 分類 ----------
PROMPT = """你係知識庫分類助手。仔細閱讀下面文件，用文件嘅【實際內容】抽出分類資料。
唔好照抄下面格式說明，要用真實內容填。只輸出 JSON：
{{"title": "...", "summary": "...", "topics": ["...", "..."]}}
規則：title=8到20字繁體中文標題(香港用語、唔好用檔名)；summary=一句繁體中文摘要40字內；topics=3到6個繁體中文主題標籤每個2到6字。

【文件 category={cat}】
{text}"""

def _is_echo(s):
    return any(h in str(s) for h in ECHO_HINTS)

def _parse(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no json")
    d = json.loads(m.group(0))
    title = str(d.get("title", "")).strip()[:40]
    topics = [str(t).strip()[:16] for t in d.get("topics", []) if str(t).strip()][:6]
    if _is_echo(title) or any(_is_echo(t) for t in topics):
        raise ValueError("echo")
    return title, str(d.get("summary", "")).strip()[:80], topics

def classify(category, text, sensitivity):
    """回 (title|None, summary, topics, via)。失敗回 None title 讓上層退用檔名。"""
    if len(text.strip()) < MIN_CHARS:
        return None, "(內容過短，未生成標籤)", [], "skip"
    _policy = load_taxonomy_policy()
    _policy_section = ("\n\n【使用者分類指引】\n" + _policy) if _policy else ""
    prompt = PROMPT.format(cat=category, text=text[:8000]) + _policy_section
    sensitivities = [sensitivity]
    if sensitivity != "sensitive":
        sensitivities.append("sensitive")          # JSON/echo 壞回應時，照舊退本機 qwen
    for route_sensitivity in sensitivities:
        for _ in range(2):                       # echo/parse 失敗重試一次
            try:
                raw = llm_router.chat("classify", prompt, json_mode=True, sensitivity=route_sensitivity)
                return (*_parse(raw), llm_router.last_route_label())
            except Exception:
                continue
    return None, "(分類失敗，已退用檔名)", [], "fail"

# ---------- Qdrant 掃描 / payload ----------
def scan_collection(client):
    """回 {key: {source_file, category, source_path, sensitivity, hash, vectors:[...], chunk_count}}
    key 通常=source_file；同名檔跨類碰撞時用 "category/source_file" 消歧，避免 INDEX 互相覆蓋。"""
    grouped = defaultdict(lambda: {"vectors": [], "chunk_count": 0})
    off = None
    while True:
        pts, off = client.scroll(COLLECTION, limit=256, offset=off,
                                 with_payload=True, with_vectors=["dense"])
        for p in pts:
            pl = p.payload or {}
            fn = pl.get("source_file", "?")
            cat = pl.get("category", "?")
            v = p.vector["dense"] if isinstance(p.vector, dict) else p.vector
            rec = grouped[(cat, fn)]
            rec["vectors"].append(v); rec["chunk_count"] += 1
            rec.setdefault("category", cat)
            rec.setdefault("source_path", pl.get("source_path", ""))
            rec.setdefault("sensitivity", pl.get("sensitivity", ""))
            rec["hash"] = pl.get("file_hash", "")
        if off is None:
            break
    name_count = defaultdict(int)
    for (_, fn) in grouped:
        name_count[fn] += 1
    files = {}
    for (cat, fn), rec in grouped.items():
        rec["source_file"] = fn
        key = fn if name_count[fn] == 1 else f"{cat}/{fn}"
        if name_count[fn] > 1:
            print(f"⚠️ 檔名碰撞：{fn} 出現於多個 category，INDEX key 消歧為 {key}")
        files[key] = rec
    return files

def compute_relations(files):
    """用 dense 向量算每檔關係：agg=排名(廣度)、peak=去重(單片最高)、cross_cat 標記。"""
    client = store.connect(QDRANT_TIMEOUT)
    cat_of = {fn: f["category"] for fn, f in files.items()}
    # payload 只有 basename；碰撞時按鄰居 category 解回正確 key
    name_to_keys = defaultdict(list)
    for k, f in files.items():
        name_to_keys[f.get("source_file", k)].append(k)

    def _resolve(nb_name, nb_cat):
        keys = name_to_keys.get(nb_name)
        if not keys:
            return nb_name
        if len(keys) == 1:
            return keys[0]
        for k in keys:
            if files[k]["category"] == nb_cat:
                return k
        return keys[0]

    out = {}
    for fn, f in files.items():
        agg = defaultdict(float); peak = defaultdict(float)
        for v in f["vectors"]:
            for h in client.query_points(COLLECTION, query=v, using="dense",
                                          limit=8, with_payload=True).points:
                nb = _resolve(h.payload.get("source_file", "?"),
                              h.payload.get("category", "?"))
                if nb == fn:
                    continue
                cat_of.setdefault(nb, h.payload.get("category", "?"))
                agg[nb] += h.score; peak[nb] = max(peak[nb], h.score)
        scored = sorted(((nb, agg[nb]/max(1, len(f["vectors"])), peak[nb]) for nb in agg),
                        key=lambda x: -x[1])
        related = [{"file": nb, "score": round(s, 3), "peak": round(pk, 3),
                    "cross_cat": cat_of.get(nb) != f["category"]} for nb, s, pk in scored[:TOP_RELATED]]
        # 去重候選：同類 + peak≥0.98 + 長度相近 + 非索引檔（真去重交 Loop 2 / LLM 確認）
        def dup_ok(r):
            if r["peak"] < NEAR_DUP or r["cross_cat"]:
                return False
            if fn in META_SKIP or r["file"] in META_SKIP:
                return False
            other = files.get(r["file"], {}).get("chunk_count", 1)
            ratio = other / max(1, f["chunk_count"])
            return 0.5 <= ratio <= 2.0
        out[fn] = {"related": related, "near_duplicates": [r for r in related if dup_ok(r)]}
    return out

def enrich_payload(client, category, source_file, title, summary, topics):
    """把 title/summary/topics 寫入該檔所有切片 payload（Track A 精準檢索用）。"""
    client.set_payload(COLLECTION, payload={"idx_title": title, "idx_summary": summary, "idx_topics": topics},
        points=models.Filter(must=[
            models.FieldCondition(key="category", match=models.MatchValue(value=category)),
            models.FieldCondition(key="source_file", match=models.MatchValue(value=source_file))]))

# ---------- INDEX / TAXONOMY 讀寫 ----------
def load_index():
    """讀 INDEX.json；壞檔（例如寫入中途俾人 kill 咗）當冇嚟處理，唔好整個 app 卡死。

    save_index() 而家用返 atomic_write_text，新寫入唔會再整成咁；但已經存在
    嘅壞檔（呢個保護加之前寫低嘅）仲係要呢度接住，等下次 catalog rebuild
    自動由 Qdrant 重新推算，唔使人手介入修檔。"""
    if not os.path.exists(INDEX_PATH):
        return {}
    try:
        return json.loads(Path(INDEX_PATH).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

def save_index(index):
    from .config import atomic_write_text
    atomic_write_text(INDEX_PATH, json.dumps(index, ensure_ascii=False, indent=2))

def load_taxonomy():
    if not os.path.exists(TAXONOMY_PATH):
        return None
    try:
        return json.loads(Path(TAXONOMY_PATH).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

def save_taxonomy(tax):
    from .config import atomic_write_text
    atomic_write_text(TAXONOMY_PATH, json.dumps(tax, ensure_ascii=False, indent=2))

def seed_taxonomy_from_index(index, today):
    """首次：由現有 index 推出 categories + tag 詞彙表。"""
    cats = defaultdict(set); vocab = set()
    for e in index.values():
        for t in e.get("topics", []):
            cats[e["category"]].add(t); vocab.add(t)
    return {"version": 1, "updated_at": today,
            "categories": {c: {"desc": "", "subtopics": sorted(ts), "aliases": []} for c, ts in cats.items()},
            "tag_vocabulary": sorted(vocab),
            "changelog": [{"version": 1, "date": today, "change": "seed from index"}]}

def sync_taxonomy_with_index(index, today):
    """非破壞性同步：INDEX 新出現的 category/topics 併入 TAXONOMY。
    只做加法（新類別、新標籤），不刪不改既有 desc/aliases——瘦身與合併交 Loop2 審計。
    回 (taxonomy, changed)。"""
    tax = load_taxonomy()
    if tax is None:
        tax = seed_taxonomy_from_index(index, today)
        save_taxonomy(tax)
        return tax, True

    cats = tax.setdefault("categories", {})
    vocab = set(tax.get("tag_vocabulary", []))
    added_cats, added_tags = [], 0
    for e in index.values():
        c = e.get("category")
        if not c:
            continue
        if c not in cats:
            cats[c] = {"desc": "", "subtopics": [], "aliases": []}
            added_cats.append(c)
        subs = set(cats[c].get("subtopics", []))
        for t in e.get("topics", []):
            if t not in subs:
                subs.add(t); added_tags += 1
            vocab.add(t)
        cats[c]["subtopics"] = sorted(subs)

    if not added_cats and not added_tags:
        return tax, False
    tax["tag_vocabulary"] = sorted(vocab)
    tax["version"] = int(tax.get("version", 1)) + 1
    tax["updated_at"] = today
    change = f"sync from index: +{len(added_cats)} categories {added_cats}, +{added_tags} tags"
    tax.setdefault("changelog", []).append(
        {"version": tax["version"], "date": today, "change": change})
    save_taxonomy(tax)
    return tax, True

# ---------- 渲染（Loop 3）----------
def _link(fn):
    return f"[[{fn[:-3]}]]" if fn.endswith(".md") else f"[[{fn}]]"

def _rel_str(r):
    return f"{_link(r['file'])}（跨類·待 LLM 確認）" if r["cross_cat"] else f"{_link(r['file'])}（{r['score']}）"

def render_catalog(index, today=None):
    today = today or datetime.date.today().isoformat()
    by_cat = defaultdict(list)
    for fn, e in index.items():
        by_cat[e["category"]].append(fn)
    Path(CAT_DIR).mkdir(parents=True, exist_ok=True)

    for cat, fns in by_cat.items():
        L = [f"# 📂 {cat} — 知識地圖（MOC）",
             f"> 自動生成 {today} ｜ {len(fns)} 檔 ｜ LLM 標籤 + Qdrant 關係。Obsidian 打開即見 graph。", ""]
        tag_idx = defaultdict(list)
        for fn in sorted(fns):
            e = index[fn]
            for t in e.get("topics", []):
                tag_idx[t].append(fn)
            L += [f"## {_link(fn)} — {e.get('title') or fn}",
                  f"> {e.get('summary', '')}",
                  f"- **主題**：" + "、".join(e.get("topics") or ["—"]),
                  f"- **關聯**：" + ("、".join(_rel_str(r) for r in e.get("related", [])) or "—")]
            if e.get("near_duplicates"):
                L.append("- 🔍 **高相似候選（待 LLM 確認真重複 vs 共用模板）**：" +
                         "、".join(_link(r['file']) for r in e["near_duplicates"]))
            if e.get("review_by"):
                L.append(f"- *複檢期：{e['review_by']}*")
            L.append("")
        L += ["---", "## 🏷️ 標籤索引", ""]
        for t in sorted(tag_idx):
            L.append(f"- **{t}**：" + "、".join(_link(f) for f in tag_idx[t]))
        Path(os.path.join(CAT_DIR, f"_MOC_{cat}.md")).write_text("\n".join(L), encoding="utf-8")

    M = [f"# 📚 KB 主目錄（MOC）", f"> 自動生成 {today} ｜ {len(by_cat)} 類 ｜ {len(index)} 檔", ""]
    for cat in sorted(by_cat):
        M.append(f"## 📂 {cat}（{len(by_cat[cat])} 檔）→ `_MOC_{cat}.md`")
        for fn in sorted(by_cat[cat]):
            e = index[fn]
            M.append(f"- {_link(fn)} — {e.get('title') or fn}　<sub>{'、'.join(e.get('topics', [])[:3])}</sub>")
        M.append("")
    Path(os.path.join(CAT_DIR, "KB_CATALOG.md")).write_text("\n".join(M), encoding="utf-8")
    return len(by_cat), len(index)
