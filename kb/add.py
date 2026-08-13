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


def notes_root():
    """Where documents written through this tool are saved.

    Defaults to the vault's raw_files. ``capture.notes_dir`` moves it somewhere
    the user chose — a synced folder, a project directory, a shared drive — so
    agent-written notes can live where the rest of their work lives.

    Read at call time so a change in Settings applies without a restart, and
    resolved through the same helper everywhere so the write path and the read
    path cannot disagree about where notes are.
    """
    configured = str(cfg("capture.notes_dir", "") or "").strip()
    if configured:
        return Path(os.path.expanduser(configured))
    return Path(cfg("kb_root")) / "raw_files"


def safe_category(category):
    """A single folder name, never a path.

    ``category`` reaches this from an MCP tool call, so it is untrusted input:
    without stripping separators and dot segments, a category of ``../..`` would
    write outside the notes directory entirely.
    """
    name = str(category or "inbox").strip().replace("\\", "/")
    name = name.split("/")[-1] if "/" in name else name
    name = re.sub(r"[^\w一-鿿.\- ]", "", name).strip(". ")
    return name or "inbox"


def unique_path(directory, filename):
    """Never overwrite an existing note; add a counter instead.

    Two notes captured the same day with the same title are ordinary — losing
    the first one silently is not.
    """
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for n in range(2, 1000):
        candidate = directory / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"太多同名檔案：{filename}")
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
    """把 URL/文字存入筆記目錄，回入庫結果；外部依賴錯誤交由 caller 決定處理。"""
    root = notes_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"筆記目錄寫唔到：{root}（{type(e).__name__}: {e}）"
            "\n如果嗰個位置係外置碟或者網絡碟，請先掛載，或者喺設定改返。"
        ) from e

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

    category = safe_category(category)
    out_dir = root / category
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = unique_path(out_dir, f"{today}-{slugify(final_title)}.md")
    out_path.write_text(body, encoding="utf-8")
    rel_file = f"{category}/{out_path.name}"

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


def add_file(data, filename, category="inbox", ingest=True, async_ingest=False):
    """Save an uploaded file's bytes as-is; the caller already has the finished document.

    Unlike add_entry(), which always wraps content in a generated .md, this
    keeps the original filename and extension — that is what tells kb.ingest
    which loader to use (.docx/.xlsx/.pptx/.pdf/...), and a colleague's
    finished report should not have to be retyped into a text box to reach
    the index.
    """
    from . import ingest as kb_ingest

    root = notes_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"筆記目錄寫唔到：{root}（{type(e).__name__}: {e}）"
            "\n如果嗰個位置係外置碟或者網絡碟，請先掛載，或者喺設定改返。"
        ) from e

    cap = kb_ingest.max_file_bytes()
    if len(data) > cap:
        raise ValueError(f"檔案太大（{len(data)} bytes），上限係 {cap} bytes")

    # The name is client-supplied — take only the leaf, and only the
    # characters slugify() already treats as safe for a category, so it
    # cannot carry a path (e.g. "../../x") out of the upload folder.
    safe_name = os.path.basename(filename or "upload")
    stem, ext = os.path.splitext(safe_name)
    safe_stem = slugify(stem) or "upload"
    ext = ext.lower()
    readable = ext in kb_ingest.SUPPORTED_EXT

    category = safe_category(category)
    out_dir = root / category
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = unique_path(out_dir, f"{safe_stem}{ext}")
    out_path.write_bytes(data)
    rel_file = f"{category}/{out_path.name}"

    if ingest and readable:
        _run_ingest(rel_file, background=async_ingest)

    return {
        "file": rel_file,
        "path": str(out_path),
        "bytes": len(data),
        "readable": readable,
        "ingest": bool(ingest and readable),
        "async_ingest": bool(ingest and readable and async_ingest),
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
