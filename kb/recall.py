#!/usr/bin/env python3
"""
kb_recall.py — 純檢索（retrieval-only，唔叫 qwen 生成）

與 query_rag.py 分別：query_rag 用 qwen2.5:14b 預先消化片段；kb_recall 只做混合檢索，
把原始片段直接吐返俾「呼叫方」（session 內的 Claude/Opus）自行推理。
理由：session 內的模型遠強過本機 qwen，餵原始片段比餵 qwen 嚼過的結果好。

這是「RAG 接入工作流」的基礎原語：任何 session / skill / 排程都可叫它取 KB context。

用法：
  python kb_recall.py "你的問題"
  python kb_recall.py "問題" --category trading        # 只查某類
  python kb_recall.py "問題" --top-k 6                  # 取多啲片段
  python kb_recall.py "問題" --json                     # 機器可讀輸出
NAS 未掛 / Qdrant 連不上 → 印一行提示後 exit 0（唔拋例外，唔阻斷呼叫方）。
"""
import os, sys, json, argparse, datetime
from .config import cfg

QDRANT_HOST, QDRANT_PORT = cfg("qdrant.host"), cfg("qdrant.port")
COLLECTION   = cfg("qdrant.collection")
QDRANT_TIMEOUT = cfg("qdrant.timeout_interactive")
DENSE_MODEL  = cfg("embedding.dense_model")
SPARSE_MODEL = cfg("embedding.sparse_model")
TOP_K        = cfg("recall.top_k")
RECALL_LOG   = os.path.join(cfg("kb_root"), "state", "recall_log.jsonl")


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?")
    ap.add_argument("--category", default=None)
    ap.add_argument("--top-k", type=int, default=TOP_K)
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
        hits = retrieve(client, models, dense, sparse, q, a.category, a.top_k)
    except Exception as e:
        # 連不上 / 未掛 → 唔阻斷呼叫方
        print(f"⚠️ KB 檢索不可用（{type(e).__name__}）：{e}")
        sys.exit(0)

    log_query(q, a.category, hits)

    results = [{
        "rank": i + 1,
        "category": h.payload.get("category"),
        "source_file": h.payload.get("source_file"),
        "score": round(h.score, 3),
        "text": h.payload.get("text", ""),
        "idx_title": h.payload.get("idx_title"),
    } for i, h in enumerate(hits)]

    if a.json:
        print(json.dumps({"query": q, "category": a.category, "hits": results},
                         ensure_ascii=False, indent=2))
        return

    if not results:
        print(f"🔍 KB 無相關片段：{q}" + (f"（類 {a.category}）" if a.category else ""))
        return

    print(f"## 📚 KB 檢索：{q}" + (f"  ［類 {a.category}］" if a.category else ""))
    print(f"> {len(results)} 個片段（混合檢索 RRF，未經 LLM 消化，供你自行判斷）\n")
    for r in results:
        head = f"[{r['rank']}] {r['category']}/{r['source_file']}  (score {r['score']})"
        if r["idx_title"]:
            head += f" — {r['idx_title']}"
        print(head)
        print(r["text"].strip())
        print()


if __name__ == "__main__":
    main()
