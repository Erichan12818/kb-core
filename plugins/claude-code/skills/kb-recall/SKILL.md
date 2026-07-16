---
name: kb-recall
description: 從本地 RAG 知識庫（NAS Qdrant kb_hybrid_v2）檢索相關片段。當用戶問過往決策/筆記/專案背景/安裝步驟/架構/操作指南，或問「知識庫有冇講過X」「之前係點搞」「KB 查吓」「recall from KB」，或 session 開頭的「📚 本專案知識庫」清單顯示有相關條目時觸發。
version: 1.0.0
metadata:
  author: self-built
  project: nas-to-qdrant RAG（全域）
---

# kb-recall — 從知識庫取 context 接入當前工作

把 KB 的相關片段拉返當前 session，**由你（session 內的強模型）自行判斷**，
而唔係叫本機 qwen 預先消化。這是「RAG 接入工作流」的取用端。

## 兩層機制

1. **Session-start priming（自動，由 plugin SessionStart hook 提供）**
   每次開 session，若當前專案資料夾對應到某 KB category，session 開頭會自動印
   「📚 本專案知識庫（N 篇）」清單（title + summary）。你開場就知 KB 有咩。
   無需做任何事 — 它已經喺 context 入面。

2. **On-demand recall（本 skill，按需）**
   要某條的細節、或要跨專案語意搜尋時，主動執行：

   ```bash
   "$CLAUDE_PLUGIN_ROOT/scripts/kb_recall.sh" "<問題>" [--category <cat>] [--top-k N]
   ```

   未設定 `KB_HOME` 時，先提示用戶設定：

   ```bash
   export KB_HOME="$HOME/Developer/kb-core"
   ```

## 點用

- **知道專案** → 加 `--category`（如 `--category trading`）收窄、減噪。
- **跨專案 / 唔確定** → 唔加 category，全庫混合檢索。
- **要多啲上下文** → `--top-k 6`（預設 4）。
- **要機器可讀** → `--json`（串接其他步驟時用）。

輸出係**原始片段**（來源檔 + score + 內文），未經 LLM 消化。你讀完自己綜合、判斷、
答用戶 — 你比本機 qwen 強，所以餵你原文好過餵你嚼過的結果。

## 何時主動 recall（唔使等人叫）

- 做某專案的技術任務前，session 開頭 priming 清單顯示有相關安裝/SOP/架構條目 → 先 recall 嗰篇。
- 用戶問「之前點搞」「有冇記低」「照舊嗰個做法」→ recall。
- 用戶問過往決策、筆記、專案背景、架構取捨、安裝步驟、操作指南 → recall。
- 你對某個專案內部決策/慣例冇把握，但 priming 顯示 KB 有 → recall 而唔係靠估。

## 紀律

- recall 係讀取，唔改 KB。要入庫用 KB 的 `kb_add` 流程或 MCP `kb_add` tool。
- 檢索唔到 → 照實同用戶講「KB 無相關記錄」，唔好作。
- NAS 未掛 / Qdrant 連不上 → kb_recall 會印一行提示自己 exit 0；照樣唔好作，提示用戶 KB 不可用。
