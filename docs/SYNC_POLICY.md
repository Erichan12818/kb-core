# Upstream and deployments

This repository is the upstream and the source of truth. A running installation
is a downstream deployment of it, not a fork to develop in.

## The rule

**Any enhancement lands on both sides in the same piece of work — never one
side alone.** A fix made only in a deployment is a fix that disappears at the
next release; a fix made only upstream is one the deployment does not have
until someone remembers to pull it. Both halves belong to the same task, and
neither is finished until both are done.

In practice:

1. Write the change here first, with a test where it is testable.
2. Apply it to the deployment in the same session, adapted to its paths and
   schedules.
3. If operational pressure forces the deployment to change first, open an issue
   here the same day recording the source file, the change, and why — then port
   it before the task is considered closed.

## What is shared and what is not

Shared: retrieval, ingest, indexing, taxonomy audit, the human gate, the API,
the CLI, and the web UI. If a change would help anyone else running kb-core, it
belongs here.

Deployment-specific: notification targets and channels, absolute storage paths,
scheduler units (launchd, systemd, cron), and integrations with a private
dashboard or control panel. These stay in the deployment. When the product
needs any of them, it takes them as configuration or as an optional hook rather
than hard-coding one site's choice.

Product-only: surfaces that exist because a downloader has no coding agent
attached. A deployment that already has agents querying the knowledge base
gains nothing from a chat window, so features of that kind are built here and
deliberately not ported downstream. The test is whether the feature serves the
knowledge base itself or only the way a particular audience reaches it.

## Module map

Deployments derived from the original single-directory toolchain use flat
script names. The mapping to this package:

| Deployment script | Upstream module |
|---|---|
| `ingest.py` | `kb/ingest.py` |
| `index_update.py` | `kb/index_update.py` |
| `catalog.py` | `kb/catalog.py` |
| `taxonomy_audit.py` | `kb/audit.py` |
| `taxonomy_apply.py` | `kb/apply.py` |
| `proposals_core.py` | `kb/proposals.py` |
| `kb_recall.py` | `kb/recall.py` |
| `kb_health.py` | `kb/health.py` |

The package also carries `kb/api.py`, `kb/mcp.py`, and `kb/static/ui.html`,
which have no flat-script equivalent.
