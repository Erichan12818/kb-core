#!/usr/bin/env python3
"""Grounded chat over the knowledge base.

This is the one place kb-core generates prose instead of returning excerpts, and
it only exists for people who have no coding agent attached. It is off unless a
provider is configured, and it never answers from the model's own memory: the
retrieved excerpts are the only permitted source, and every claim has to carry
the number of the excerpt it came from.

Deployments that already have agents querying the knowledge base should leave
this disabled — the agent doing the reasoning is better served by raw excerpts.

The API key lives in the deployment's own config or environment. It is never
returned to the browser, and the only place it travels is the provider the
operator chose.
"""
import re

from .config import cfg
from . import llm

ROLE = "chat"
MAX_CONTEXT_CHARS = 6000
MAX_HISTORY_TURNS = 6


SYSTEM_RULES = """你係一個知識庫問答助手。你**只可以**根據下面提供嘅資料片段回答。

規則：
1. 每個講法後面要標明來源編號，格式 [1]、[2]。一句用咗幾個片段就標幾個。
2. 資料片段冇提到嘅嘢，就直接講「知識庫入面搵唔到」。**唔准用你自己嘅知識補**，
   亦都唔准推測。寧願答唔到，都好過答錯。
3. 片段之間有矛盾就講出嚟，唔好自己揀一個當真。
4. 用戶用咩語言問就用咩語言答。
5. 簡潔。唔使覆述問題，唔使客套。"""


def is_enabled():
    """True when the operator has pointed the chat role at a provider."""
    roles = cfg("llm.roles", {}) or {}
    role = roles.get(ROLE)
    if not isinstance(role, dict):
        return False
    return bool(role.get("provider") and role.get("model"))


def build_context(hits, max_chars=MAX_CONTEXT_CHARS):
    """Render hits as numbered excerpts, and return the sources that fit.

    Truncation matters: a citation pointing at an excerpt the model never saw is
    worse than a missing answer, so the returned sources are exactly the ones
    included in the prompt.
    """
    blocks, sources, used = [], [], 0
    for index, hit in enumerate(hits, 1):
        text = (hit.get("text") or "").strip()
        if not text:
            continue
        header = (
            f"[{index}] 來源：{hit.get('source_path') or hit.get('source_file') or '?'}"
            f"（類別：{hit.get('category') or '—'}）"
        )
        block = f"{header}\n{text}"
        if used + len(block) > max_chars and blocks:
            break
        blocks.append(block)
        used += len(block)
        sources.append({
            "n": index,
            "source_file": hit.get("source_file"),
            "source_path": hit.get("source_path"),
            "category": hit.get("category"),
            "score": hit.get("score"),
            "text": text,
        })
    return "\n\n".join(blocks), sources


def build_prompt(question, context, history=None):
    parts = [SYSTEM_RULES, "", "── 資料片段 ──", context or "（冇搵到任何相關片段）", ""]
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        role = "用戶" if turn.get("role") == "user" else "助手"
        content = (turn.get("content") or "").strip()
        if content:
            parts.append(f"{role}：{content}")
    parts += ["", f"用戶：{question}", "助手："]
    return "\n".join(parts)


def cited_numbers(answer):
    """Citation markers the model actually used."""
    return sorted({int(n) for n in re.findall(r"\[(\d{1,2})\]", answer or "")})


def answer(question, hits, history=None):
    """Return ``{answer, sources, cited, route, grounded}``.

    ``grounded`` is False when the model wrote prose without citing anything,
    which is the shape a hallucinated answer takes. The caller surfaces that
    rather than hiding it.
    """
    if not is_enabled():
        raise RuntimeError(
            "對話功能未啟用。喺 kb_config.yaml 嘅 llm.roles 加一個 chat 角色，"
            "指定 provider 同 model（同時要設好嗰個 provider 嘅 key_env）。"
        )
    context, sources = build_context(hits)
    text = llm.chat(ROLE, build_prompt(question, context, history)).strip()
    cited = cited_numbers(text)
    valid = {s["n"] for s in sources}
    return {
        "answer": text,
        "sources": sources,
        "cited": [n for n in cited if n in valid],
        "invalid_cited": [n for n in cited if n not in valid],
        "route": llm.last_route_label(),
        "grounded": bool(cited and sources),
    }
