#!/usr/bin/env python3
"""
kb_eval.py — KB recall quality smoke evaluation.

Runs a small YAML-defined query set against kb_recall.retrieve and prints a
Markdown or JSON report. Qdrant/NAS failures are non-blocking: print a warning
and exit 0, matching the recall/session-start behavior.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from importlib import metadata

from . import store
from .recall import (
    COLLECTION,
    DENSE_MODEL,
    QDRANT_HOST,
    QDRANT_PORT,
    QDRANT_TIMEOUT,
    SPARSE_MODEL,
    retrieve,
    retrieve_two_stage,
)

DEFAULT_TOP_K = 3
DEFAULT_QUERIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_queries.yaml")


def load_yaml(path):
    try:
        import yaml
    except ImportError:
        print("⚠️ 缺少 PyYAML，請先安裝：pip install PyYAML", file=sys.stderr)
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    queries = data.get("queries") or []
    if not isinstance(queries, list):
        raise ValueError("eval YAML 格式錯誤：queries 必須是 list")
    return queries


def init_retriever():
    try:
        from fastembed import SparseTextEmbedding, TextEmbedding
        from qdrant_client import QdrantClient, models

        client = store.connect(QDRANT_TIMEOUT)
        if not client.collection_exists(COLLECTION):
            raise RuntimeError(f"collection not found: {COLLECTION}")
        dense = TextEmbedding(DENSE_MODEL)
        sparse = SparseTextEmbedding(SPARSE_MODEL)
        return client, models, dense, sparse
    except Exception as e:
        print(f"⚠️ KB 評估略過：Qdrant 檢索不可用（{type(e).__name__}）：{e}")
        sys.exit(0)


def runtime_versions():
    versions = {}
    for package in ("fastembed", "qdrant-client"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def hit_to_dict(hit, rank):
    payload = hit.payload or {}
    return {
        "rank": rank,
        "category": payload.get("category"),
        "source_file": payload.get("source_file"),
        "score": round(hit.score, 3),
        "title": payload.get("idx_title") or payload.get("title") or "",
        "text": payload.get("text", "") or "",
    }


def text_blob(hit):
    parts = [
        hit.get("category") or "",
        hit.get("source_file") or "",
        hit.get("title") or "",
        hit.get("text") or "",
    ]
    return "\n".join(parts).lower()


def keyword_status(expected_keywords, hits, top_k):
    found = []
    blobs = "\n".join(text_blob(h) for h in hits[:top_k])
    for kw in expected_keywords:
        if str(kw).lower() in blobs:
            found.append(kw)
    if not expected_keywords:
        label = "n/a"
    elif len(found) == len(expected_keywords):
        label = "all"
    elif found:
        label = "partial"
    else:
        label = "none"
    return {
        "status": label,
        "found": found,
        "missing": [kw for kw in expected_keywords if kw not in found],
        "hit": bool(found),
    }


def category_rank(expected_category, hits):
    for idx, hit in enumerate(hits, start=1):
        if hit.get("category") == expected_category:
            return idx
    return None


def evaluate_query(
    query_def,
    retriever,
    top_k,
    use_expected_category_filter,
    two_stage=False,
):
    client, models, dense, sparse = retriever
    query = (query_def.get("query") or "").strip()
    expected_category = query_def.get("expected_category")
    expected_keywords = query_def.get("expected_keywords") or []
    category_filter = expected_category if use_expected_category_filter else None
    category_guess = None
    confidence = 1.0 if category_filter else 0.0
    if two_stage:
        outcome = retrieve_two_stage(
            client, models, dense, sparse, query, None, top_k
        )
        hits = outcome["hits"]
        category_guess = outcome["category_guess"]
        confidence = outcome["confidence"]
    else:
        hits = retrieve(
            client, models, dense, sparse, query, category_filter, top_k
        )
    results = [hit_to_dict(hit, i + 1) for i, hit in enumerate(hits)]
    top_category = results[0].get("category") if results else None
    category_hit = top_category == expected_category
    rank = category_rank(expected_category, results)
    keywords = keyword_status(expected_keywords, results, top_k)
    return {
        "id": query_def.get("id"),
        "query": query,
        "expected_category": expected_category,
        "expected_keywords": expected_keywords,
        "notes": query_def.get("notes"),
        "category_guess": category_guess,
        "confidence": confidence,
        "top_category": top_category,
        "category_rank": rank,
        "category_hit": category_hit,
        "category_hit_at_k": rank is not None,
        "keyword_hit": keywords["hit"],
        "keyword_status": keywords["status"],
        "found_keywords": keywords["found"],
        "missing_keywords": keywords["missing"],
        "hits": results,
    }


def render_markdown(report):
    lines = [
        "# KB Recall 品質評估報告",
        f"執行時間：{report['run_at']}",
        "",
        "## 總覽",
        f"- 測試問題數：{report['total']}",
        f"- category 命中率：{report['category_hits']}/{report['total']}（top-1 屬 expected_category）",
        f"- category@{report['top_k']} 命中率：{report['category_hits_at_k']}/{report['total']}（top-{report['top_k']} 含 expected_category）",
        f"- keyword 命中率：{report['keyword_hits']}/{report['total']}（top-{report['top_k']} 含任一 expected_keyword）",
        f"- 檢索模式：{report['mode']}",
        f"- runtime：fastembed {report['runtime_versions'].get('fastembed')}, qdrant-client {report['runtime_versions'].get('qdrant-client')}",
        "",
        "## 逐題結果",
    ]

    for item in report["results"]:
        category_mark = "✅ 命中" if item["category_hit"] else "❌ 未中"
        if item["keyword_status"] == "all":
            keyword_mark = "✅ 全中"
        elif item["keyword_status"] == "partial":
            keyword_mark = "⚠️ 部分"
        elif item["keyword_status"] == "none":
            keyword_mark = "❌ 未中"
        else:
            keyword_mark = "n/a"
        keywords = ", ".join(str(kw) for kw in item["expected_keywords"])

        lines.extend([
            f"### [{item['id']}] {item['query']}",
            f"expected_category: {item['expected_category']} | {category_mark} | rank: {item['category_rank'] or 'n/a'}",
            f"expected_keywords: {keywords} | {keyword_mark}",
            (
                f"category_guess: {item['category_guess'] or 'none'}"
                f" | confidence: {item['confidence']:.3f}"
            ),
            f"Top-{report['top_k']} 結果：",
        ])
        if not item["hits"]:
            lines.append("（無結果）")
        for hit in item["hits"]:
            source = hit.get("source_file") or ""
            title = hit.get("title") or ""
            text = " ".join((hit.get("text") or "").strip().split())[:100]
            lines.append(
                f"{hit['rank']}. {hit.get('category')}/{source} (score {hit['score']}) — {title}"
            )
            if text:
                lines.append(f"   > {text}")
        lines.extend(["---"])
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default=DEFAULT_QUERIES, help="eval_queries.yaml 路徑")
    ap.add_argument("--category", default=None, help="只測某 expected_category")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="每題取回片段數")
    ap.add_argument(
        "--use-expected-category-filter",
        action="store_true",
        help="每題用 expected_category 作 metadata filter，對照分類內檢索品質",
    )
    ap.add_argument(
        "--two-stage",
        action="store_true",
        help="不用 expected category；先由 TAXONOMY 猜類別，否則搜尋全庫",
    )
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    args = ap.parse_args()
    if args.use_expected_category_filter and args.two_stage:
        ap.error("--two-stage 與 --use-expected-category-filter 不可同時使用")

    queries = load_yaml(args.queries)
    if args.category:
        queries = [q for q in queries if q.get("expected_category") == args.category]

    retriever = init_retriever()
    try:
        results = [
            evaluate_query(
                q,
                retriever,
                args.top_k,
                args.use_expected_category_filter,
                args.two_stage,
            )
            for q in queries
        ]
    except Exception as e:
        print(f"⚠️ KB 評估略過：Qdrant 檢索不可用（{type(e).__name__}）：{e}")
        sys.exit(0)
    report = {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "queries_path": args.queries,
        "category_filter": args.category,
        "use_expected_category_filter": args.use_expected_category_filter,
        "two_stage": args.two_stage,
        "mode": (
            "two-stage taxonomy routing"
            if args.two_stage
            else (
                "expected_category filter"
                if args.use_expected_category_filter
                else "global"
            )
        ),
        "top_k": args.top_k,
        "total": len(results),
        "category_hits": sum(1 for r in results if r["category_hit"]),
        "category_hits_at_k": sum(1 for r in results if r["category_hit_at_k"]),
        "keyword_hits": sum(1 for r in results if r["keyword_hit"]),
        "runtime_versions": runtime_versions(),
        "results": results,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))


if __name__ == "__main__":
    main()
