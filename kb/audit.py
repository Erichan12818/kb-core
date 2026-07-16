#!/usr/bin/env python3
"""
taxonomy_audit.py — Loop 2：分類法自我重整（週審，分級自動）

讀 INDEX + TAXONOMY → 算統計（孤兒/過期/高相似/標籤頻率）→ LLM 提重整建議
→ 寫 catalog/TAXONOMY_REVIEW_<date>.md 報告 + Discord 推播重點建議。

風險分級（2026-07-03 升級）：
  低風險 tag 操作 → 自動執行：①合併同義標籤（護欄：兩個 tag 都真實存在、
    被合併方使用 ≤2 次、每次最多 10 組）②清除 policy 禁止標籤。
  類別結構變動（拆/合類）→ 一律只建議，等人拍板。
用法：python taxonomy_audit.py [--dry-run]（dry-run 只報告不執行）
"""
import os, re, sys, json, datetime
from collections import defaultdict, Counter
from pathlib import Path
from . import index as ic
from . import llm as llm_router
from . import notify as kb_notify

OVERLOAD = 15         # 一類超過此數 → 提議拆分
MERGE_FREQ_CAP = 2    # 被合併 tag 使用次數上限（低風險門檻）
MAX_MERGES = 10       # 每次審計最多自動合併組數

def llm_suggest(payload_text):
    _policy = ic.load_taxonomy_policy()
    _policy_prefix = ("【使用者分類指引】\n" + _policy + "\n\n") if _policy else ""
    prompt = (
        _policy_prefix
        + "你係知識庫架構審計員。下面係一個本地知識庫嘅分類統計。"
        "請用繁體中文(香港用語)俾出具體、可執行嘅重整建議，分四節：\n"
        "1. 類別重整（邊類該拆/合，點拆）\n2. 標籤詞彙清理（冗餘/同義/該退役嘅 tag）\n"
        "3. 高相似群處置（真重複定共用模板）\n4. 過期/孤兒跟進\n"
        "每點一句、附理由。冇問題就寫「無建議」。唔好客套。\n\n" + payload_text)
    try:
        return llm_router.chat("audit", prompt).strip()
    except Exception as e:
        return f"(LLM 建議生成失敗：{e})"


def llm_structured(payload_text):
    """第二輪：要 LLM 出結構化 JSON（tag 合併/重點建議），供自動執行同推播用。"""
    prompt = (
        "你係知識庫架構審計員。根據下面統計，只輸出 JSON（唔好其他文字）：\n"
        '{"tag_merges": [{"keep": "保留的tag", "retire": "被合併的同義tag"}],\n'
        ' "highlights": ["重點建議一句", "重點建議一句"]}\n'
        "規則：tag_merges 只放真正同義/重複嘅標籤對（例：向量庫 vs 向量數據庫），"
        "唔肯定就唔好放；highlights 係俾人喺手機睇嘅 2-3 條最重要行動建議，"
        "每條 ≤30 字繁體中文。\n\n" + payload_text)
    try:
        raw = llm_router.chat("audit", prompt, json_mode=True)
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0)) if m else {}
        merges = [x for x in d.get("tag_merges", [])
                  if isinstance(x, dict) and x.get("keep") and x.get("retire")]
        highlights = [str(h).strip() for h in d.get("highlights", []) if str(h).strip()][:3]
        return merges, highlights
    except Exception:
        return [], []


def load_forbidden_tags():
    """從 taxonomy_policy.md 嘅「禁止標籤」節抽 tag 清單（無 policy 就空集）。"""
    policy = ic.load_taxonomy_policy()
    if not policy:
        return set()
    m = re.search(r"##\s*禁止標籤.*?(?=\n##|\Z)", policy, re.S)
    if not m:
        return set()
    tags = set()
    for ln in m.group(0).splitlines():
        mm = re.match(r"\s*-\s*([^（(#\s][^（(]*)", ln)
        if mm:
            tags.add(mm.group(1).strip())
    return tags


def auto_apply_tag_ops(index, tax, merges, tagfreq, today):
    """低風險 tag 操作自動執行。回 (applied_log, affected_files)。
    護欄：只動 tag 唔動 category；被合併 tag 使用 ≤MERGE_FREQ_CAP 次；
    兩個 tag 都要真實存在；每次上限 MAX_MERGES 組。"""
    applied, affected = [], set()
    vocab = set(tagfreq)
    rename = {}   # retire -> keep

    for mg in merges[:MAX_MERGES]:
        keep, retire = str(mg["keep"]).strip(), str(mg["retire"]).strip()
        if not keep or not retire or keep == retire:
            continue
        if keep not in vocab or retire not in vocab:
            continue                      # LLM 幻覺 tag → 唔郁
        if tagfreq[retire] > MERGE_FREQ_CAP:
            continue                      # 影響面太大 → 留人手
        rename[retire] = keep
        applied.append(f"合併標籤：「{retire}」→「{keep}」（{retire} 用過 {tagfreq[retire]} 次）")

    forbidden = load_forbidden_tags() & vocab
    for t in sorted(forbidden):
        rename[t] = None                  # None = 直接移除
        applied.append(f"清除禁止標籤：「{t}」（用過 {tagfreq[t]} 次）")

    if not rename:
        return [], set()

    # 套用到 INDEX
    for fn, e in index.items():
        topics = e.get("topics") or []
        new = []
        for t in topics:
            t2 = rename.get(t, t)
            if t2 and t2 not in new:
                new.append(t2)
        if new != topics:
            e["topics"] = new
            affected.add(fn)

    # 套用到 TAXONOMY（subtopics + vocab），bump version + changelog
    for c, ent in (tax.get("categories") or {}).items():
        subs = []
        for t in ent.get("subtopics", []):
            t2 = rename.get(t, t)
            if t2 and t2 not in subs:
                subs.append(t2)
        ent["subtopics"] = sorted(subs)
    vocab_new = []
    for t in tax.get("tag_vocabulary", []):
        t2 = rename.get(t, t)
        if t2 and t2 not in vocab_new:
            vocab_new.append(t2)
    tax["tag_vocabulary"] = sorted(vocab_new)
    tax["version"] = int(tax.get("version", 1)) + 1
    tax["updated_at"] = today
    tax.setdefault("changelog", []).append(
        {"version": tax["version"], "date": today,
         "change": "Loop2 auto-apply: " + "；".join(applied)})
    return applied, affected


def sync_payload_topics(index, affected):
    """把改咗 topics 嘅檔同步返 Qdrant payload（rerank 靠 idx_topics）。失敗唔阻斷。"""
    if not affected:
        return True
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(host=ic.QDRANT_HOST, port=ic.QDRANT_PORT, timeout=ic.QDRANT_TIMEOUT)
        for fn in affected:
            e = index[fn]
            ic.enrich_payload(client, e["category"], e.get("source_file", fn),
                              e.get("title", fn), e.get("summary", ""), e.get("topics", []))
        return True
    except Exception as e:
        print(f"⚠️ Qdrant payload 同步失敗（{type(e).__name__}），INDEX 已改，payload 待下次成功執行同步")
        return False

def main():
    index = ic.load_index()
    if not index:
        raise SystemExit("❌ 無 INDEX.json，先跑 index_update.py")
    tax = ic.load_taxonomy() or {}
    today = datetime.date.today().isoformat()

    by_cat = defaultdict(list)
    tagfreq = Counter()
    orphans, stale, dup_clusters = [], [], []
    for fn, e in index.items():
        by_cat[e["category"]].append(fn)
        tagfreq.update(e.get("topics", []))
        if not [r for r in e.get("related", []) if not r["cross_cat"]]:
            orphans.append(fn)
        if e.get("review_by") and e["review_by"] < today:
            stale.append(fn)
        if e.get("near_duplicates"):
            dup_clusters.append((fn, [r["file"] for r in e["near_duplicates"]]))

    singletons = sorted([t for t, n in tagfreq.items() if n == 1])

    # 組統計文字餵 LLM
    P = [f"分類統計（{today}）：", f"- 總 {len(index)} 檔 / {len(by_cat)} 類"]
    for c in sorted(by_cat):
        flag = "  ⚠️過載" if len(by_cat[c]) > OVERLOAD else ""
        P.append(f"- 類 {c}：{len(by_cat[c])} 檔{flag}；標題例：" +
                 "、".join(index[f].get("title", f) for f in by_cat[c][:4]))
    P.append(f"- 標籤總數 {len(tagfreq)}，僅用一次嘅單例標籤 {len(singletons)} 個：" + "、".join(singletons[:30]))
    P.append(f"- 孤兒檔（同類內無關聯）{len(orphans)}：" + "、".join(orphans[:8]))
    P.append(f"- 過期檔（過複檢期）{len(stale)}：" + "、".join(stale[:8]))
    P.append(f"- 高相似群 {len(dup_clusters)}：" + "；".join(f"{a}↔{b}" for a, b in dup_clusters[:6]))
    payload_text = "\n".join(P)

    print("🔎 統計完成，呼叫 LLM 出重整建議…")
    advice = llm_suggest(payload_text)
    merges, highlights = llm_structured(payload_text)

    # 低風險 tag 操作自動執行（--dry-run 跳過）
    dry_run = "--dry-run" in sys.argv
    applied, affected = ([], set()) if dry_run else auto_apply_tag_ops(index, tax, merges, tagfreq, today)
    if applied:
        ic.save_index(index)
        ic.save_taxonomy(tax)
        sync_payload_topics(index, affected)
        ic.render_catalog(index, today)
        print(f"🤖 自動執行 {len(applied)} 項 tag 清理，影響 {len(affected)} 檔")
    elif dry_run and merges:
        print(f"（dry-run：跳過 {len(merges)} 組候選合併）")

    R = [f"# 🧮 TAXONOMY 重整審查報告 — {today}",
         "> 由 taxonomy_audit.py 自動產生。低風險 tag 操作已自動執行；類別結構變動需人手拍板。", "",
         "## 一、統計快照", "```", payload_text, "```", "",
         "## 二、已自動執行（低風險 tag 操作）"]
    R += [f"- {a}" for a in applied] if applied else ["- 無"]
    R += ["", "## 三、LLM 重整建議（類別結構部分需人手）", advice, "",
          "## 四、待人手決定", "- [ ] 是否採納類別拆/合建議",
          "- [ ] 高相似群逐一裁定（真重複→封存其一 / 共用模板→保留）",
          "- [ ] 過期檔逐一處理（改來源 / 延期 / 封存）", ""]
    out = os.path.join(ic.CAT_DIR, f"TAXONOMY_REVIEW_{today}.md")
    Path(ic.CAT_DIR).mkdir(parents=True, exist_ok=True)
    Path(out).write_text("\n".join(R), encoding="utf-8")
    print(f"✅ Loop2 報告 → {out}")
    print(f"   孤兒 {len(orphans)} ｜ 過期 {len(stale)} ｜ 高相似群 {len(dup_clusters)} ｜ 單例標籤 {len(singletons)}")

    # 統一推播（經 kb_notify；失敗不影響報告產出）
    try:
        lines = [f"孤兒 {len(orphans)} / 過期 {len(stale)} / "
                 f"高相似群 {len(dup_clusters)} / 單例標籤 {len(singletons)}"]
        if applied:
            lines.append(f"🤖 已自動執行 {len(applied)} 項 tag 清理（詳見報告）")
        if highlights:
            lines.append("重點建議：")
            marks = "①②③"
            lines += [f"{marks[i]} {h}" for i, h in enumerate(highlights)]
        lines.append(f"完整報告：catalog/TAXONOMY_REVIEW_{today}.md")
        lines.append("開 KB session 講「執行審查建議 ①」即可落實")
        kb_notify.send("kbtoolchain.taxonomy", f"🧮 TAXONOMY 重整審查 {today}", "\n".join(lines))
    except Exception as e:
        print(f"⚠️ Discord 推播失敗：{type(e).__name__}: {e}")

if __name__ == "__main__":
    main()
