#!/usr/bin/env python3
"""
kb_add.py — 「丟即入」知識庫入口引擎（Scene01 #1#2，gateway 無關）

任何前端都可呼叫呢個引擎把內容入庫：
  - Hermes（Discord 丟連結/文字）
  - Claude Code（headless `claude -p` 或排程）
  - hotkey / Shortcuts / watcher

用法：
  python kb_add.py "https://example.com/article"            # 網頁 → 抓 → 入庫
  python kb_add.py "一段文字筆記" --category notes           # 純文字 → 入庫
  python kb_add.py "<url>" --category reference --no-ingest  # 只存檔，等排程灌
"""
import os, re, sys, subprocess, threading, datetime, argparse, urllib.parse
from pathlib import Path
from .config import cfg

KB_ROOT  = cfg("kb_root")
RAW_ROOT = os.path.join(KB_ROOT, "raw_files")
# Fetching a URL as clean markdown needs an external extractor; which one is a
# deployment choice, so it is configured rather than assumed. Text capture works
# without it.
URL_FETCHER = os.path.expanduser(str(cfg("capture.url_fetcher", "") or ""))

URL_RE = re.compile(r"^https?://", re.I)

def slugify(s, maxlen=50):
    s = re.sub(r"[^\w一-鿿\- ]", "", s or "").strip().replace(" ", "-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:maxlen] or "untitled"

PATHLIKE_RE = re.compile(r"^(https?://|file://|www\.|[/~]|[A-Za-z]:\\)", re.I)

def derive_title(text, maxlen=50):
    """從內容取可讀標題：跳過路徑/URL/註解行，避免產出垃圾檔名。"""
    for ln in (text or "").strip().splitlines():
        ln = ln.strip().lstrip("#").strip()
        if not ln or ln.startswith("<!--") or PATHLIKE_RE.match(ln):
            continue
        return ln[:maxlen]
    return "untitled"

def fetch_url(url):
    """Run the configured extractor and return markdown on stdout."""
    if not URL_FETCHER:
        raise RuntimeError(
            "URL 擷取未設定。喺 kb_config.yaml 設 capture.url_fetcher 指向一個"
            "「收 URL 參數、喺 stdout 出 markdown」嘅可執行檔；"
            "未設定時純文字入庫照用。"
        )
    if not os.path.exists(URL_FETCHER):
        raise RuntimeError(f"搵唔到 capture.url_fetcher 指定嘅程式：{URL_FETCHER}")
    out = subprocess.run([URL_FETCHER, url], capture_output=True, text=True, timeout=180)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"抓網頁失敗：{(out.stderr or '空輸出')[:300]}")
    return out.stdout

def _ingest_log(rel_file):
    """Open the async ingest log. A background ingest that dies takes the note
    with it, and the caller has already been told "queued", so neither stream
    may be discarded."""
    path = Path(cfg("kb_root")) / "state" / "ingest_async.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    log = open(path, "a", encoding="utf-8")
    log.write(
        f"\n===== {datetime.datetime.now().isoformat(timespec='seconds')} "
        f"ingest for {rel_file} =====\n"
    )
    log.flush()
    return log


def _run_ingest(rel_file, background):
    """Run ingest, as a thread or a subprocess depending on the store.

    The embedded store admits one process, so spawning a second one would fail
    on the directory lock — there, ingest has to share this process. Against a
    server a subprocess is preferable: it cannot take the caller down with it.
    """
    from . import store

    if store.is_embedded():
        log = _ingest_log(rel_file)

        def run():
            from contextlib import redirect_stdout, redirect_stderr
            from . import ingest as kb_ingest
            try:
                with redirect_stdout(log), redirect_stderr(log):
                    kb_ingest.main()
            except BaseException as e:          # SystemExit included
                log.write(f"ingest ended: {type(e).__name__}: {e}\n")
            finally:
                log.flush()

        if background:
            threading.Thread(target=run, name="kb-ingest", daemon=True).start()
        else:
            run()
        return

    if background:
        log = _ingest_log(rel_file)
        subprocess.Popen(
            [sys.executable, "-m", "kb.ingest"], stdout=log, stderr=subprocess.STDOUT
        )
    else:
        subprocess.run([sys.executable, "-m", "kb.ingest"])


def add_entry(content, category="inbox", title=None, ingest=True, async_ingest=False):
    """把 URL/文字存入 raw_files，回入庫結果；外部依賴錯誤交由 caller 決定處理。"""
    Path(RAW_ROOT).mkdir(parents=True, exist_ok=True)

    today = datetime.date.today().isoformat()
    content = content or ""
    if URL_RE.match(content.strip()):
        url = content.strip()
        md = fetch_url(url)
        m = re.search(r"^#\s+(.+)$", md, re.M)
        final_title = title or (m.group(1).strip() if m else urllib.parse.urlparse(url).netloc)
        body = f"<!-- 來源(URL): {url} ｜ 抓取: {today} by kb_add.py -->\n\n" + md
    else:
        final_title = title or derive_title(content)
        body = f"<!-- 來源(文字) ｜ 加入: {today} by kb_add.py -->\n\n" + content

    out_dir = Path(RAW_ROOT) / category
    out_dir.mkdir(parents=True, exist_ok=True)
    fn = f"{today}-{slugify(final_title)}.md"
    out_path = out_dir / fn
    out_path.write_text(body, encoding="utf-8")
    rel_file = f"{category}/{fn}"

    if ingest:
        _run_ingest(rel_file, background=async_ingest)

    return {
        "file": rel_file,
        "path": str(out_path),
        "title": final_title,
        "bytes": len(body.encode("utf-8")),
        "chars": len(body),
        "ingest": bool(ingest),
        "async_ingest": bool(ingest and async_ingest),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("content", help="URL 或一段文字")
    ap.add_argument("--category", default="inbox")
    ap.add_argument("--title", default=None)
    ap.add_argument("--no-ingest", action="store_true")
    a = ap.parse_args()

    try:
        result = add_entry(a.content, a.category, a.title, ingest=not a.no_ingest)
    except RuntimeError as e:
        sys.exit(f"❌ {e}")

    print(f"✅ 已存：{result['file']}  ({result['chars']} 字)")
    if a.no_ingest:
        print("（--no-ingest：等每日排程或手動 ingest）")
    else:
        print("🔄 增量灌庫已完成")

if __name__ == "__main__":
    main()
