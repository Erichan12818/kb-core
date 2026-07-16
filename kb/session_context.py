#!/usr/bin/env python3
"""
kb_session_context.py — SessionStart KB priming（RAG 接入工作流 · 第一層）

每次開新 session，按當前專案資料夾（cwd）對應 KB category，讀 INDEX.json，
印出「本專案知識庫有咩」清單（title + summary）。令每個 session 一開就知道
KB 對呢個專案藏咗咩，唔使再人手記得去查。

刻意唔做語意檢索 / 唔載入 embedding 模型：session-start 階段冇明確 query，
而且每次開 session 都載 e5-large 太重。語意檢索留俾 kb_recall.py（按需、值得先付成本）。

設計原則（與 relay-context.sh 一致）：NAS 未掛 / 無 INDEX / 無對應 category
→ 一律靜默 exit 0，絕不干擾 session 啟動。

由 SessionStart hook 經 stdin 收 JSON（含 cwd）。亦可 fallback 用 os.getcwd()。
"""
import sys, os, json, re
from .config import cfg

KB_ROOT    = cfg("kb_root")
INDEX_PATH = os.path.join(KB_ROOT, "state", "INDEX.json")
HERE       = os.path.dirname(os.path.abspath(__file__))
MAP_PATH   = os.path.join(HERE, "kb_project_map.json")
MAX_FILES  = 20   # 印太多反而嘈；超過只印頭幾條 + 提示總數


def resolve_cwd():
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if raw:
        try:
            cwd = json.loads(raw).get("cwd")
            if cwd:
                return cwd, True
        except Exception:
            pass
    return os.getcwd(), False


def resolve_category(cwd):
    base = os.path.basename(os.path.normpath(cwd))
    try:
        m = json.load(open(MAP_PATH, encoding="utf-8"))
    except Exception:
        m = {}
    if base in m and not base.startswith("_"):
        return m[base]
    return base  # fallback：basename 當 category 試（無對應檔就自然空清單）


def split_signals(value):
    return [s.lower() for s in re.split(r"[-_\s,]+", value or "") if len(s) > 2]


def resolve_signals(cwd, cat, has_stdin_cwd):
    if has_stdin_cwd:
        signals = split_signals(os.path.basename(os.path.normpath(cwd)))
    elif os.environ.get("SESSION_TOPICS"):
        signals = split_signals(os.environ.get("SESSION_TOPICS", ""))
    else:
        signals = []
    signals.extend(split_signals(cat))
    return sorted(set(signals))


def relevance_score(entry, signals):
    topics = entry.get("topics") or []
    if isinstance(topics, (list, tuple)):
        topics_text = " ".join(str(t) for t in topics)
    else:
        topics_text = str(topics)
    haystack = " ".join([
        str(entry.get("title") or ""),
        str(entry.get("summary") or ""),
        topics_text,
    ]).lower()
    return sum(1 for signal in set(signals) if len(signal) > 2 and signal in haystack)


def main():
    try:
        cwd, has_stdin_cwd = resolve_cwd()
        cat = resolve_category(cwd)
        if not cat:
            return
        idx = json.load(open(INDEX_PATH, encoding="utf-8"))
    except Exception:
        return  # NAS 未掛 / 無 INDEX → 靜默

    files = [(fn, e) for fn, e in idx.items() if e.get("category") == cat]
    if not files:
        return  # 呢個專案 KB 未有內容 → 唔印

    signals = resolve_signals(cwd, cat, has_stdin_cwd)
    scored = [(fn, e, relevance_score(e, signals)) for fn, e in files]
    starred = [
        (fn, e, score)
        for fn, e, score in sorted(
            scored,
            key=lambda kv: (kv[2], kv[1].get("indexed_at", "")),
            reverse=True,
        )
        if score >= 2
    ][:3]
    starred_names = {fn for fn, _, _ in starred}

    # 新近優先
    files.sort(key=lambda kv: kv[1].get("indexed_at", ""), reverse=True)

    print(f"=== 📚 本專案知識庫（category: {cat}，{len(files)} 篇）===")
    print("> 需要細節時用：`python ~/Developer/nas-to-qdrant/kb_recall.py \"問題\" --category " + cat + "`")

    if starred:
        print("★ 可能相關（按關鍵詞匹配）")
        for fn, e, _score in starred:
            title = e.get("title") or fn
            summary = (e.get("summary") or "").strip()
            line = f"── {title}"
            if summary:
                line += f"｜{summary}"
            print(line)
        print()

    remaining = [(fn, e) for fn, e in files if fn not in starred_names]
    for fn, e in remaining[:MAX_FILES]:
        title = e.get("title") or fn
        summary = (e.get("summary") or "").strip()
        line = f"── {title}"
        if summary:
            line += f"｜{summary}"
        print(line)
    if len(remaining) > MAX_FILES:
        print(f"…（另有 {len(remaining) - MAX_FILES} 篇，用 kb_recall 檢索）")


if __name__ == "__main__":
    main()
