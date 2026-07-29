#!/usr/bin/env python3
"""
kb_recall.py — 純檢索（retrieval-only，唔叫 qwen 生成）

與 query_rag.py 分別：query_rag 用 qwen2.5:14b 預先消化片段；kb_recall 只做混合檢索，
把原始片段直接吐返俾「呼叫方」（session 內的 Claude/Opus）自行推理。
理由：session 內的模型遠強過本機 qwen，餵原始片段比餵 qwen 嚼過的結果好。

這是「RAG 接入工作流」的基礎原語：任何 session / skill / 排程都可叫它取 KB context。

用法：
  python kb_recall.py "你的問題"
  python kb_recall.py "問題" --category infrastructure  # 只查某類
  python kb_recall.py "問題" --top-k 6                  # 取多啲片段
  python kb_recall.py "問題" --json                     # 機器可讀輸出
NAS 未掛 / Qdrant 連不上 → 印一行提示後 exit 0（唔拋例外，唔阻斷呼叫方）。
"""
import argparse
import datetime
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from .config import cfg

QDRANT_HOST, QDRANT_PORT = cfg("qdrant.host"), cfg("qdrant.port")
COLLECTION   = cfg("qdrant.collection")
QDRANT_TIMEOUT = cfg("qdrant.timeout_interactive")
DENSE_MODEL  = cfg("embedding.dense_model")
SPARSE_MODEL = cfg("embedding.sparse_model")
TOP_K        = cfg("recall.top_k")
RECALL_LOG   = os.path.join(cfg("kb_root"), "state", "recall_log.jsonl")
TAXONOMY_PATH = Path(cfg("kb_root")) / "state" / "TAXONOMY.json"

# Taxonomy matching is deliberately conservative: a weak semantic nearest
# neighbour is not enough to constrain retrieval. Values are cosine/lexical
# scores in [0, 1], followed by the required lead over the runner-up.
CATEGORY_CONFIDENCE_THRESHOLD = 0.72
CATEGORY_MARGIN_THRESHOLD = 0.04

_TAXONOMY_VECTOR_CACHE = {
    "signature": None,
    "model_id": None,
    "categories": (),
    "vectors": (),
}


def log_query(query, category, hits):
    """真實查詢落帳（供日後轉化為 eval 題庫 + 使用分析）。任何失敗靜默。"""
    try:
        os.makedirs(os.path.dirname(RECALL_LOG), exist_ok=True)
        with open(RECALL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                "query": query, "category": category,
                "n_hits": len(hits),
                "top_score": round(hits[0].score, 3) if hits else None,
                "top_source": (hits[0].payload or {}).get("source_file") if hits else None,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


# lexical rerank 權重：查詢明確提到 category 名 = 強訊號；命中文件 topics = 弱訊號
CAT_BOOST, TOPIC_BOOST, FETCH_MULT = cfg("recall.cat_boost"), cfg("recall.topic_boost"), cfg("recall.fetch_mult")


def _lex_boost(query_lower, payload):
    """查詢與 payload 元數據的詞面重疊加分（純字串，零成本）。"""
    boost = 0.0
    cat = (payload.get("category") or "").lower()
    if cat and cat in query_lower:
        boost += CAT_BOOST
    topics = payload.get("idx_topics") or []
    hits = sum(1 for t in topics if t and str(t).lower() in query_lower)
    boost += TOPIC_BOOST * min(hits, 3)   # 封頂，防長 topics 文件霸榜
    return boost


def load_taxonomy(path=None):
    """Load the runtime taxonomy; a missing/invalid file means no category guess."""
    taxonomy_path = Path(path or TAXONOMY_PATH)
    try:
        data = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    categories = data.get("categories")
    return categories if isinstance(categories, dict) else {}


def taxonomy_categories(path=None):
    """Return stable, display-ready category names from TAXONOMY.json."""
    return sorted(str(name) for name in load_taxonomy(path) if str(name).strip())


def _normalize(value):
    return re.sub(
        r"[\W_]+",
        "",
        unicodedata.normalize("NFKC", str(value)).casefold(),
        flags=re.UNICODE,
    )


def _taxonomy_terms(categories):
    terms_by_category = {}
    frequency = Counter()
    for category, entry in categories.items():
        entry = entry if isinstance(entry, dict) else {}
        terms = [category]
        terms.extend(entry.get("aliases") or [])
        terms.extend(entry.get("subtopics") or [])
        normalized = {
            _normalize(term)
            for term in terms
            if term is not None and _normalize(term)
        }
        terms_by_category[category] = normalized
        frequency.update(normalized)
    return terms_by_category, frequency


def _lexical_category_scores(query, categories):
    """Score only vocabulary that actually exists in the runtime taxonomy."""
    query_normalized = _normalize(query)
    terms_by_category, frequency = _taxonomy_terms(categories)
    scores = {}
    for category, terms in terms_by_category.items():
        evidence = []
        category_term = _normalize(category)
        if category_term and category_term in query_normalized:
            evidence.append(1.0)
        entry = categories.get(category) or {}
        aliases = {
            _normalize(alias)
            for alias in entry.get("aliases") or []
            if _normalize(alias)
        }
        for term in terms:
            if term == category_term or term not in query_normalized:
                continue
            if len(term) < 2 and not term.isascii():
                continue
            if len(term) < 3 and term.isascii():
                continue
            base = 0.94 if term in aliases else min(0.86, 0.68 + len(term) * 0.025)
            evidence.append(base / math.sqrt(frequency[term]))
        # Independent matching terms reinforce each other without exceeding 1.
        scores[category] = 1.0 - math.prod(1.0 - item for item in evidence)
    return scores


def _taxonomy_documents(categories):
    names, documents = [], []
    for category, entry in categories.items():
        entry = entry if isinstance(entry, dict) else {}
        parts = [category, entry.get("desc") or ""]
        parts.extend(entry.get("aliases") or [])
        parts.extend(entry.get("subtopics") or [])
        names.append(category)
        documents.append("；".join(str(part) for part in parts if part))
    return names, documents


def _taxonomy_signature():
    try:
        stat = TAXONOMY_PATH.stat()
        return str(TAXONOMY_PATH), stat.st_mtime_ns, stat.st_size
    except OSError:
        return str(TAXONOMY_PATH), None, None


def _category_vectors(dense, categories):
    names, documents = _taxonomy_documents(categories)
    model_id = id(dense)
    signature = _taxonomy_signature()
    cache = _TAXONOMY_VECTOR_CACHE
    if (
        cache["signature"] == signature
        and cache["model_id"] == model_id
        and cache["categories"] == tuple(names)
    ):
        return names, cache["vectors"]
    vectors = tuple(dense.embed([f"passage: {document}" for document in documents]))
    cache.update(
        {
            "signature": signature,
            "model_id": model_id,
            "categories": tuple(names),
            "vectors": vectors,
        }
    )
    return names, vectors


def _cosine(left, right):
    numerator = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(0.0, min(1.0, numerator / (left_norm * right_norm)))


def guess_category(query, dense=None, taxonomy=None):
    """Return ``(category | None, confidence)`` from runtime taxonomy content.

    Exact/lexical evidence is combined with a semantic comparison against one
    document per category. The winner must clear both an absolute threshold and
    a margin over the runner-up; otherwise callers should search globally.
    """
    categories = taxonomy if taxonomy is not None else load_taxonomy()
    if not categories:
        return None, 0.0

    lexical = _lexical_category_scores(query, categories)
    semantic = {category: 0.0 for category in categories}
    if dense is not None:
        try:
            query_vector = list(dense.query_embed([f"query: {query}"]))[0]
            names, vectors = _category_vectors(dense, categories)
            semantic.update(
                {
                    category: _cosine(query_vector, vector)
                    for category, vector in zip(names, vectors)
                }
            )
        except Exception:
            # Lexical matching remains useful if taxonomy embedding is unavailable.
            pass

    combined = {
        category: max(lexical.get(category, 0.0), semantic.get(category, 0.0))
        for category in categories
    }
    ranked = sorted(combined.items(), key=lambda item: (-item[1], item[0]))
    winner, confidence = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    confident = (
        confidence >= CATEGORY_CONFIDENCE_THRESHOLD
        and confidence - runner_up >= CATEGORY_MARGIN_THRESHOLD
    )
    return (winner if confident else None), round(confidence, 3)


def retrieve(client, models, dense, sparse, query, category, top_k):
    dv = list(dense.query_embed([f"query: {query}"]))[0]
    sv = list(sparse.embed([query]))[0]
    qfilter = None
    if category:
        qfilter = models.Filter(must=[models.FieldCondition(
            key="category", match=models.MatchValue(value=category))])
    fetch_k = max(top_k * FETCH_MULT, 20)
    pts = client.query_points(
        collection_name=COLLECTION,
        prefetch=[
            models.Prefetch(query=dv.tolist(), using="dense", limit=fetch_k),
            models.Prefetch(query=models.SparseVector(
                indices=sv.indices.tolist(), values=sv.values.tolist()), using="sparse", limit=fetch_k),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=qfilter, limit=fetch_k, with_payload=True,
    ).points
    # lexical rerank：RRF 分數 + 元數據詞面加分（指定 category 時無需再 boost）
    if not category:
        ql = query.lower()
        for p in pts:
            p.score = p.score + _lex_boost(ql, p.payload or {})
        pts.sort(key=lambda p: -p.score)
    return pts[:top_k]


def retrieve_two_stage(
    client,
    models,
    dense,
    sparse,
    query,
    category,
    top_k,
    force_global=False,
):
    """Retrieve with explicit filtering or conservative taxonomy-driven routing."""
    if category:
        return {
            "hits": retrieve(
                client, models, dense, sparse, query, category, top_k
            ),
            "category_guess": None,
            "confidence": 1.0,
            "grouped": False,
        }

    guessed, confidence = (None, 0.0)
    if not force_global:
        guessed, confidence = guess_category(query, dense=dense)
    hits = retrieve(
        client,
        models,
        dense,
        sparse,
        query,
        guessed,
        top_k,
    )
    return {
        "hits": hits,
        "category_guess": guessed,
        "confidence": confidence,
        "grouped": guessed is None,
    }


def hit_to_result(hit, rank):
    payload = hit.payload or {}
    source_path = payload.get("source_path") or ""
    try:
        source_path = str(
            Path(source_path).resolve().relative_to(Path(cfg("kb_root")).resolve())
        )
    except (OSError, TypeError, ValueError):
        source_path = source_path or str(
            Path("raw_files")
            / str(payload.get("category") or "")
            / str(payload.get("source_file") or "")
        )
    return {
        "rank": rank,
        "category": payload.get("category"),
        "source_file": payload.get("source_file"),
        "source_path": source_path,
        "score": round(hit.score, 3),
        "text": payload.get("text", ""),
        "idx_title": payload.get("idx_title"),
        "idx_summary": payload.get("idx_summary"),
    }


def group_results(results):
    grouped = {}
    for result in results:
        category = result.get("category") or "_uncategorized"
        grouped.setdefault(category, []).append(result)
    return [
        {"category": category, "hits": hits}
        for category, hits in grouped.items()
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("--category", default=None)
    ap.add_argument("--top-k", type=int, default=TOP_K)
    ap.add_argument(
        "--global",
        dest="force_global",
        action="store_true",
        help="略過 category 猜測，搜尋全庫並按 category 分組",
    )
    ap.add_argument("--json", action="store_true", help="輸出 JSON 而非 markdown")
    a = ap.parse_args()
    q = (a.query or "").strip()
    if not q:
        sys.exit("⚠️ 問題不能為空")

    try:
        from qdrant_client import QdrantClient, models
        from fastembed import TextEmbedding, SparseTextEmbedding
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=QDRANT_TIMEOUT)
        dense = TextEmbedding(DENSE_MODEL)
        sparse = SparseTextEmbedding(SPARSE_MODEL)
        outcome = retrieve_two_stage(
            client,
            models,
            dense,
            sparse,
            q,
            a.category,
            a.top_k,
            force_global=a.force_global,
        )
        hits = outcome["hits"]
    except Exception as e:
        # 連不上 / 未掛 → 唔阻斷呼叫方
        print(f"⚠️ KB 檢索不可用（{type(e).__name__}）：{e}")
        sys.exit(0)

    log_query(q, a.category or outcome["category_guess"], hits)

    results = [hit_to_result(hit, i + 1) for i, hit in enumerate(hits)]
    response = {
        "query": q,
        "category": a.category,
        "category_guess": outcome["category_guess"],
        "confidence": outcome["confidence"],
        "hits": results,
        "groups": group_results(results) if outcome["grouped"] else [],
    }

    if a.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return

    if not results:
        print(f"🔍 KB 無相關片段：{q}" + (f"（類 {a.category}）" if a.category else ""))
        return

    display_category = a.category or outcome["category_guess"]
    print(f"## 📚 KB 檢索：{q}" + (f"  ［類 {display_category}］" if display_category else ""))
    if outcome["category_guess"]:
        print(
            f"> 已收窄至 {outcome['category_guess']} 類"
            f"（confidence {outcome['confidence']:.3f}；用 --global 搜全庫）"
        )
    elif not a.category:
        print(
            f"> category 未達收窄門檻（confidence {outcome['confidence']:.3f}）；"
            "以下為全庫結果，按類別排列"
        )
    print(f"> {len(results)} 個片段（混合檢索 RRF，未經 LLM 消化，供你自行判斷）\n")
    sections = group_results(results) if outcome["grouped"] else [
        {"category": display_category, "hits": results}
    ]
    for section in sections:
        if outcome["grouped"]:
            print(f"### {section['category']}")
        for r in section["hits"]:
            head = f"[{r['rank']}] {r['category']}/{r['source_file']}  (score {r['score']})"
            if r["idx_title"]:
                head += f" — {r['idx_title']}"
            print(head)
            print(r["text"].strip())
            print()


if __name__ == "__main__":
    main()
