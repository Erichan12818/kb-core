#!/usr/bin/env python3
"""
taxonomy_audit.py — Loop 2：分類法自我重整（週審，分級自動）

讀 INDEX + TAXONOMY → 算統計（孤兒/過期/高相似/標籤頻率）→ LLM 提重整建議
→ 寫 catalog/TAXONOMY_REVIEW_<date>.md 報告 + 可選 notify hook。

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
from . import proposals as pc

OVERLOAD = 15         # 一類超過此數 → 提議拆分
MERGE_FREQ_CAP = 2    # 被合併 tag 使用次數上限（低風險門檻）
MAX_MERGES = 10       # 每次審計最多自動合併組數
MIN_SPLIT = 3         # 拆出的新類至少要有咁多檔，否則唔值得開新類

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


def build_split_context(index, by_cat):
    """為過載類建立完整檔案清單；冇過載類就唔呼叫 LLM。"""
    overloaded = [
        category
        for category in sorted(by_cat)
        if len(by_cat[category]) > OVERLOAD
    ]
    if not overloaded:
        return "", overloaded
    lines = []
    for category in overloaded:
        lines.append(
            f"\n【類 {category}】共 {len(by_cat[category])} 檔"
            f"（超過載線 {OVERLOAD}）："
        )
        for filename in sorted(by_cat[category]):
            entry = index[filename]
            tags = "、".join(entry.get("topics", [])[:5]) or "—"
            lines.append(
                f"- {filename} ｜ {entry.get('title', '')} ｜ 標籤：{tags}"
            )
    return "\n".join(lines), overloaded


def llm_proposals(split_ctx, over_cats, index, by_cat, today):
    """生成拆類建議，並擋 LLM 幻覺檔名、不安全類名和過細拆分。"""
    if not split_ctx:
        return []
    prompt = (
        "你係知識庫架構審計員。下面係過載類別嘅完整檔案清單。"
        "請判斷邊啲檔其實屬於一個獨立主題、應該拆去自己嘅類。只輸出 JSON：\n"
        '{"proposals": [{"title": "拆分建議標題", "rationale": "點解要拆，一兩句",\n'
        '  "moves": [{"file": "檔名.md", "from_category": "原類", '
        '"to_category": "目標類"}]}]}\n'
        "規則：\n"
        "- file 必須逐字照抄清單入面嘅檔名，唔准自己作\n"
        f"- 每個 to_category 至少要夾夠 {MIN_SPLIT} 個檔先值得開，唔夠就唔好提\n"
        "- to_category 用簡短英文小階或中文詞，會變成資料夾名\n"
        "- 只拆主題真係獨立嘅檔；唔肯定就唔好放。"
        '冇嘢好拆就出 {"proposals": []}\n'
        "- 唔好客套，唔好解釋 JSON 以外嘅嘢\n\n"
        + split_ctx
    )
    try:
        raw = llm_router.chat("audit", prompt, json_mode=True)
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0)) if match else {}
    except Exception as exc:
        print(f"⚠️ 結構化建議生成失敗：{type(exc).__name__}: {exc}")
        return []

    output = []
    for proposal in data.get("proposals", []) or []:
        if not isinstance(proposal, dict):
            continue
        moves, seen = [], set()
        for move in proposal.get("moves", []) or []:
            if not isinstance(move, dict):
                continue
            filename = str(move.get("file", "")).strip()
            target = str(move.get("to_category", "")).strip()
            entry = index.get(filename)
            if not entry:
                print(f"   ⚠️ 丟棄：LLM 提及嘅檔唔存在於 INDEX（{filename}）")
                continue
            source = entry["category"]
            if source not in over_cats:
                print(
                    f"   ⚠️ 丟棄：{filename} 唔喺過載類（{source}），今輪唔郁"
                )
                continue
            if not target or target == source:
                continue
            if "/" in target or ".." in target or target.startswith("."):
                print(f"   ⚠️ 丟棄：目標類名唔安全（{target}）")
                continue
            if filename in seen:
                continue
            seen.add(filename)
            moves.append(
                {
                    "file": filename,
                    "from_category": source,
                    "to_category": target,
                }
            )

        target_counts = Counter(move["to_category"] for move in moves)
        moves = [
            move
            for move in moves
            if target_counts[move["to_category"]] >= MIN_SPLIT
        ]
        if not moves:
            continue
        source_counts = Counter(move["from_category"] for move in moves)
        drop = False
        for category, count in source_counts.items():
            if len(by_cat[category]) - count < MIN_SPLIT:
                print(
                    f"   ⚠️ 丟棄：搬走 {count} 檔會令原類 {category} "
                    f"剩低唔夠 {MIN_SPLIT} 檔"
                )
                drop = True
        if drop:
            continue

        output.append(
            pc.make(
                "move_files",
                str(proposal.get("title") or "拆分過載類別").strip()[:80],
                str(proposal.get("rationale") or "").strip()[:300],
                {"moves": moves},
                risk="high",
                today=today,
            )
        )
    return output


def _stem_wo_date(filename):
    """去掉 YYYY-MM-DD- 前綴，用嚟辨認同系列檔。"""
    return re.sub(r"^\d{4}-\d{2}-\d{2}-", "", filename)


def _is_series(left, right):
    """同系列不同期數唔係重複，必須在交俾 LLM 前硬擋。"""
    return _stem_wo_date(left) == _stem_wo_date(right)


def _best_match(index, filename):
    """回傳該檔 near_duplicates 中相似分最高的對手。"""
    duplicates = (index.get(filename) or {}).get("near_duplicates") or []
    if not duplicates:
        return None
    return max(duplicates, key=lambda item: item.get("score", 0))["file"]


def build_dup_context(index):
    """建立去重候選對，A↔B 去重並排除週報等同系列內容。"""
    seen, pairs = set(), []
    for filename, entry in index.items():
        for duplicate in entry.get("near_duplicates", []):
            other = duplicate["file"]
            if other not in index:
                continue
            key = frozenset((filename, other))
            if key in seen:
                continue
            seen.add(key)
            if _is_series(filename, other):
                print(f"   ⏭️  同系列，唔當重複：{filename} ↔ {other}")
                continue
            pairs.append(
                (
                    filename,
                    other,
                    duplicate.get("score"),
                    duplicate.get("peak"),
                )
            )
    if not pairs:
        return "", []

    lines = []
    for left, right, score, peak in pairs:
        left_entry, right_entry = index[left], index[right]
        lines.append(
            f"\n【候選對】相似分 {score}（峰值 {peak}）\n"
            f"  A: {left}\n"
            f"     類：{left_entry['category']}｜標題："
            f"{left_entry.get('title', '')}\n"
            f"     摘要：{left_entry.get('summary', '')}\n"
            f"     標籤：{'、'.join(left_entry.get('topics', [])) or '—'}\n"
            f"  B: {right}\n"
            f"     類：{right_entry['category']}｜標題："
            f"{right_entry.get('title', '')}\n"
            f"     摘要：{right_entry.get('summary', '')}\n"
            f"     標籤：{'、'.join(right_entry.get('topics', [])) or '—'}"
        )
    return "\n".join(lines), pairs


def llm_archive_proposals(dup_ctx, pairs, index, today):
    """只為真重複建立封存建議，並以候選對、系列及最高相似對手作護欄。"""
    if not dup_ctx:
        return []
    prompt = (
        "你係知識庫架構審計員。下面係向量相似度偏高嘅檔案對。"
        "請分辨邊啲係【真重複】（同一份嘢寫兩次，內容實質相同，保留一份就夠），"
        "邊啲只係【格式相似】（各自有獨立內容，兩份都必須保留）。\n"
        "⚠️ 相似分高唔等於重複。以下情況一律唔算重複：\n"
        "・同一內容俾唔同工具／受眾嘅平行版本\n"
        "・總覽文檔 vs 子模組參考\n"
        "・同系列不同期數（週報、批次記錄）\n"
        "只有保留一份唔會損失資訊先算真重複。只輸出 JSON：\n"
        '{"proposals": [{"title": "標題", "rationale": "點解係真重複",\n'
        '  "archive": [{"file": "要封存嗰個檔名", '
        '"keep": "保留嗰個檔名"}]}]}\n'
        "- file / keep 必須逐字照抄上面出現過嘅檔名\n"
        "- 保留內容較完整、較新嗰份；唔肯定就唔好放\n"
        '全部都唔係真重複就出 {"proposals": []}\n\n'
        + dup_ctx
    )
    try:
        raw = llm_router.chat("audit", prompt, json_mode=True)
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0)) if match else {}
    except Exception as exc:
        print(f"⚠️ 封存建議生成失敗：{type(exc).__name__}: {exc}")
        return []

    valid_pairs = {frozenset((left, right)) for left, right, _, _ in pairs}
    output, claimed = [], set()
    for proposal in data.get("proposals", []) or []:
        if not isinstance(proposal, dict):
            continue
        files = []
        for item in proposal.get("archive", []) or []:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("file", "")).strip()
            keep = str(item.get("keep", "")).strip()
            if filename not in index or keep not in index:
                print(
                    f"   ⚠️ 丟棄：LLM 提及嘅檔唔存在（{filename} / {keep}）"
                )
                continue
            if filename == keep:
                continue
            if frozenset((filename, keep)) not in valid_pairs:
                print(
                    f"   ⚠️ 丟棄：{filename} ↔ {keep} "
                    "唔係今輪嘅高相似對，唔憑空封存"
                )
                continue
            if _is_series(filename, keep):
                print(f"   ⚠️ 丟棄：{filename} ↔ {keep} 係同系列不同期數")
                continue
            best_match = _best_match(index, filename)
            if best_match and best_match != keep:
                print(
                    f"   ⚠️ 丟棄：{filename} 最相似嘅係 {best_match}，"
                    f"唔係 {keep}——LLM 揀錯咗配對對手"
                )
                continue
            if filename in claimed or keep in claimed:
                print(
                    f"   ⚠️ 丟棄：{filename}/{keep} 已被同輪其他建議佔用，"
                    "避免連環封存"
                )
                continue
            claimed.add(filename)
            claimed.add(keep)
            files.append(
                {
                    "file": filename,
                    "from_category": index[filename]["category"],
                    "keep": keep,
                }
            )
        if not files:
            continue
        output.append(
            pc.make(
                "archive_files",
                str(proposal.get("title") or "封存重複檔案").strip()[:80],
                str(proposal.get("rationale") or "").strip()[:300],
                {"files": files},
                risk="high",
                today=today,
            )
        )
    return output


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
    dry_run = "--dry-run" in sys.argv
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

    # 類別結構變動屬高風險：只生成結構化 pending 建議，等人閘處理。
    proposal_doc = pc.load()
    fresh = []

    split_context, overloaded_categories = build_split_context(index, by_cat)
    if split_context:
        print(
            f"🧩 過載類 {overloaded_categories} → 生成結構化拆類建議…"
        )
        fresh += llm_proposals(
            split_context,
            overloaded_categories,
            index,
            by_cat,
            today,
        )

    duplicate_context, duplicate_pairs = build_dup_context(index)
    if duplicate_context:
        print(
            f"🔍 高相似候選對 {len(duplicate_pairs)} 組 "
            "→ 判斷真重複 vs 格式相似…"
        )
        fresh += llm_archive_proposals(
            duplicate_context, duplicate_pairs, index, today
        )

    proposal_doc, added_count = pc.merge_pending(proposal_doc, fresh, today)
    if added_count and not dry_run:
        pc.save(proposal_doc)
        print(f"📝 新增 {added_count} 條待決建議 → {pc.PROPOSALS_PATH}")
    elif added_count:
        print(f"（dry-run：會新增 {added_count} 條待決建議，未寫入）")
    else:
        print("📝 無新建議（已提過／人已駁回／未過護欄）")
    pending = proposal_doc.get("proposals", [])

    # 低風險 tag 操作自動執行（--dry-run 跳過）
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
          "## 四、待人手決定（可一撳落實）"]
    if pending:
        R.append(
            "> 用 `kb proposals apply <id>` 落實，"
            "`kb proposals apply <id> --dry-run` 預演，"
            "或 `kb proposals reject <id> [note]` 駁回。"
        )
        R.append("")
        for proposal in pending:
            R.append(f"### `{proposal['id']}` — {proposal['title']}")
            R.append(
                f"- **風險**：{proposal['risk']}　"
                f"**動作**：{proposal['action']}"
            )
            if proposal.get("rationale"):
                R.append(f"- **理由**：{proposal['rationale']}")
            for move in proposal["params"].get("moves", []):
                R.append(
                    f"  - `{move['file']}`：{move['from_category']} "
                    f"→ **{move['to_category']}**"
                )
            for item in proposal["params"].get("files", []):
                keep = (
                    f"，保留 `{item['keep']}`" if item.get("keep") else ""
                )
                R.append(
                    f"  - 封存 `{item.get('file')}`"
                    f"（{item.get('from_category')}）{keep}"
                )
            R.append("")
    else:
        R += ["- 無結構化待決建議", ""]
    R += ["## 五、其他人手跟進（未結構化）",
          "- [ ] 高相似群逐一裁定（真重複→封存其一 / 共用模板→保留）",
          "- [ ] 過期檔逐一處理（改來源 / 延期 / 封存）", ""]
    out = os.path.join(ic.CAT_DIR, f"TAXONOMY_REVIEW_{today}.md")
    Path(ic.CAT_DIR).mkdir(parents=True, exist_ok=True)
    Path(out).write_text("\n".join(R), encoding="utf-8")
    print(f"✅ Loop2 報告 → {out}")
    print(f"   孤兒 {len(orphans)} ｜ 過期 {len(stale)} ｜ 高相似群 {len(dup_clusters)} ｜ 單例標籤 {len(singletons)}")

    # 可選通知 hook；未配置就靜默略過，失敗不影響報告產出。
    try:
        lines = [f"孤兒 {len(orphans)} / 過期 {len(stale)} / "
                 f"高相似群 {len(dup_clusters)} / 單例標籤 {len(singletons)}"]
        if applied:
            lines.append(f"🤖 已自動執行 {len(applied)} 項 tag 清理（詳見報告）")
        if pending:
            lines.append(f"🚦 {len(pending)} 條待決建議：")
            lines += [f"・{p['id']} {p['title']}" for p in pending[:3]]
        if highlights:
            lines.append("重點建議：")
            marks = "①②③"
            lines += [f"{marks[i]} {h}" for i, h in enumerate(highlights)]
        lines.append(f"完整報告：catalog/TAXONOMY_REVIEW_{today}.md")
        if pending:
            lines.append("落實：kb proposals apply <id>")
        kb_notify.send("kbtoolchain.taxonomy", f"🧮 TAXONOMY 重整審查 {today}", "\n".join(lines))
    except Exception as e:
        print(f"⚠️ 通知失敗：{type(e).__name__}: {e}")

if __name__ == "__main__":
    main()
