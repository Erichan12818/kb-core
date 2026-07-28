# kb-core 同步政策

`kb-core` 是工具鏈的 upstream 與 source of truth；`~/Developer/nas-to-qdrant`
只是屋企環境的 downstream 部署。新通用功能應先進入 `kb-core`。如果因營運需要先在
`nas-to-qdrant` 修改，改動屬通用功能時，必須在完成當日開一個 kb-core issue，清楚標記
「待移植」、來源檔案和改動目的。

| downstream (`nas-to-qdrant/`) | upstream (`kb-core/`) |
|---|---|
| `ingest.py` | `kb/ingest.py` |
| `taxonomy_audit.py` | `kb/audit.py` |
| `kb_recall.py` | `kb/recall.py` |
| `index_update.py` | `kb/index_update.py` |
| `catalog.py` | `kb/catalog.py` |
| `kb_health.py` | `kb/health.py` |
| `proposals_core.py` | `kb/proposals.py` |
| `taxonomy_apply.py` | `kb/apply.py` |

移植只涵蓋可重用的產品功能。以下屋企部署細節不屬移植範圍：Discord notify 目標、
`/Volumes/home` 等 NAS 路徑、Ordis 控制台整合，以及 launchd plist。產品端如需通知，
只接通用、可選的 notify hook；如需路徑或排程，則由配置與部署層提供。
