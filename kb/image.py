#!/usr/bin/env python3
"""
kb_image.py — 圖片轉化記錄（image → markdown sidecar）

掃 raw_files 內圖片，用本機視覺模型（Ollama llava）生成描述 + 轉錄圖中文字，
產出同名 sidecar（photo.png → photo.png.md）。sidecar 係普通 .md，
自然行走現有管線（ingest 嵌入 / Loop1 分類 / catalog 渲染），原圖保留不動。

路由哲學與 index_core 一致：圖片一律本機模型處理（不出機），敏感圖安全。

用法：
  python kb_image.py                # 掃全庫，增量（有 sidecar 且圖未變就跳過）
  python kb_image.py <圖片路徑>      # 只轉一張
  python kb_image.py --force        # 重生所有 sidecar
  python kb_image.py --dry-run      # 只列會處理邊啲

降級：NAS 未掛 / Ollama 未起 → 印一行提示後 exit 0（不阻斷呼叫方/排程）。
"""
import os, sys, json, base64, datetime, argparse, subprocess, tempfile
from pathlib import Path

import requests
from .config import cfg
from . import llm as llm_router

KB_ROOT   = cfg("kb_root")
RAW_ROOT  = os.path.join(KB_ROOT, "raw_files")
OLLAMA_BASE_URL = cfg("llm.ollama_url").rstrip("/")
OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/chat"
OLLAMA_TAGS_URL = f"{OLLAMA_BASE_URL}/api/tags"
# 實測（2026-07-03）：llava:7b 中文完全讀唔到；gemma3:12b 中文描述+OCR 合格
VISION_MODEL = cfg("llm.roles.vision.model")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
HEIC_EXT  = {".heic", ".heif"}   # 先 sips 轉 jpg 再餵模型

PROMPT = (
    "你係知識庫圖片轉錄助手。仔細觀察呢張圖片，用繁體中文（香港用語）輸出兩部分：\n"
    "1. 【描述】圖片內容係咩（場景/物件/圖表重點），3 句內。\n"
    "2. 【圖中文字】完整轉錄圖片入面所有可見文字（保持原語言，逐行列出）；無文字就寫「（無文字）」。\n"
    "只輸出呢兩部分，唔好加開場白。"
)


def ollama_up():
    try:
        requests.get(OLLAMA_TAGS_URL, timeout=3)
        return True
    except Exception:
        return False


def to_jpeg_if_heic(path: Path):
    """HEIC → 臨時 jpg（macOS sips）。回 (實際餵模型的路徑, 是否臨時檔)。"""
    if path.suffix.lower() not in HEIC_EXT:
        return path, False
    tmp = Path(tempfile.mkstemp(suffix=".jpg")[1])
    r = subprocess.run(["sips", "-s", "format", "jpeg", str(path), "--out", str(tmp)],
                       capture_output=True, timeout=60)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"sips 轉換失敗: {r.stderr.decode()[:120]}")
    return tmp, True


def describe(path: Path) -> str:
    feed, is_tmp = to_jpeg_if_heic(path)
    try:
        b64 = base64.b64encode(feed.read_bytes()).decode()
    finally:
        if is_tmp:
            feed.unlink(missing_ok=True)
    return llm_router.chat("vision", PROMPT, images=[b64]).strip()


def sidecar_path(img: Path) -> Path:
    return img.with_name(img.name + ".md")


def needs_work(img: Path, force: bool) -> bool:
    sc = sidecar_path(img)
    if force or not sc.exists():
        return True
    return img.stat().st_mtime > sc.stat().st_mtime   # 圖片有更新


def convert(img: Path) -> Path:
    today = datetime.date.today().isoformat()
    rel = os.path.relpath(img, KB_ROOT)
    content = describe(img)
    body = (f"<!-- 來源(圖片): {rel} ｜ 轉換: kb_image.py {VISION_MODEL} @ {today} -->\n\n"
            f"# 圖片記錄：{img.name}\n\n{content}\n")
    sc = sidecar_path(img)
    sc.write_text(body, encoding="utf-8")
    return sc


def find_images(root: str):
    exts = IMAGE_EXT | HEIC_EXT
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and p.suffix.lower() in exts and not p.name.startswith("."):
            yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="只轉呢張圖（省略=掃全庫）")
    ap.add_argument("--force", action="store_true", help="重生所有 sidecar")
    ap.add_argument("--dry-run", action="store_true", help="只列會處理邊啲")
    a = ap.parse_args()

    if not os.path.isdir(RAW_ROOT):
        print(f"⚠️ NAS 未掛載（{RAW_ROOT}），跳過圖片轉化")
        return
    if not ollama_up():
        print(f"⚠️ Ollama 未起（{OLLAMA_BASE_URL}），跳過圖片轉化")
        return

    targets = [Path(a.path)] if a.path else list(find_images(RAW_ROOT))
    todo = [p for p in targets if needs_work(p, a.force)]
    if not todo:
        print(f"✅ 圖片轉化：{len(targets)} 張圖全部已有 sidecar，無需處理")
        return

    print(f"🖼️  待轉化 {len(todo)}/{len(targets)} 張（模型 {VISION_MODEL}）")
    if a.dry_run:
        for p in todo:
            print(f"  - {os.path.relpath(p, KB_ROOT)}")
        return

    ok = fail = 0
    for i, p in enumerate(todo, 1):
        try:
            sc = convert(p)
            ok += 1
            print(f"  [{i}/{len(todo)}] ✅ {os.path.relpath(sc, KB_ROOT)}")
        except Exception as e:
            fail += 1
            print(f"  [{i}/{len(todo)}] ❌ {p.name}: {type(e).__name__}: {e}")
    print(f"完成：{ok} 成功 / {fail} 失敗。sidecar 會由下次 ingest 自動入庫。")


if __name__ == "__main__":
    main()
