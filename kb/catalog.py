#!/usr/bin/env python3
"""
catalog.py — Loop 3：由 INDEX.json 渲染 KB_CATALOG.md + 各類 _MOC_<cat>.md

（2026-06-19 升級：舊版只 scroll Qdrant 出切片統計表；新版改由自我演進目錄 INDEX 渲染
 帶 [[wikilink]] 的 MOC，供 Obsidian graph。純渲染，唔叫 LLM。）
用法：python catalog.py
"""
from . import index as ic

def main():
    index = ic.load_index()
    if not index:
        raise SystemExit("❌ 無 INDEX.json，先跑 index_update.py（會自動建）")
    ncat, nfile = ic.render_catalog(index)
    print(f"✅ 目錄已渲染：{nfile} 檔 / {ncat} 類")
    print(f"   主目錄 → {ic.CAT_DIR}/KB_CATALOG.md")
    print(f"   各類 MOC → {ic.CAT_DIR}/_MOC_<cat>.md")

if __name__ == "__main__":
    main()
