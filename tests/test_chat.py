import pytest

from kb import chat


def _hit(n, text="content", category="notes"):
    return {
        "source_file": f"doc-{n}.md",
        "source_path": f"raw_files/{category}/doc-{n}.md",
        "category": category,
        "score": 1.0,
        "text": text,
    }


def test_disabled_until_a_chat_role_is_configured(monkeypatch):
    monkeypatch.setattr(chat, "cfg", lambda key, default=None: {})
    assert chat.is_enabled() is False
    with pytest.raises(RuntimeError, match="未啟用"):
        chat.answer("anything", [_hit(1)])


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        ({}, False),
        ({"chat": {}}, False),
        ({"chat": {"provider": "cloud"}}, False),          # 冇 model
        ({"chat": {"model": "some-model"}}, False),        # 冇 provider
        ({"chat": {"provider": "cloud", "model": "m"}}, True),
    ],
)
def test_is_enabled_requires_both_provider_and_model(monkeypatch, roles, expected):
    monkeypatch.setattr(chat, "cfg", lambda key, default=None: roles)
    assert chat.is_enabled() is expected


def test_context_numbering_matches_returned_sources():
    hits = [_hit(1, "first"), _hit(2, "second"), _hit(3, "third")]
    context, sources = chat.build_context(hits)
    assert [s["n"] for s in sources] == [1, 2, 3]
    for source in sources:
        assert f"[{source['n']}]" in context


def test_truncation_never_leaves_a_source_the_model_did_not_see():
    """A citation pointing at an excerpt outside the prompt would be unverifiable."""
    hits = [_hit(i, "x" * 400) for i in range(1, 11)]
    context, sources = chat.build_context(hits, max_chars=900)
    assert len(sources) < len(hits), "這個案例本來就要觸發截斷"
    for source in sources:
        assert source["text"][:50] in context


def test_prompt_forbids_answering_from_model_memory():
    prompt = chat.build_prompt("問題", "[1] 來源：a.md\n內容")
    assert "只可以" in prompt
    assert "唔准用你自己嘅知識補" in prompt
    assert "搵唔到" in prompt


def test_history_is_capped_so_old_turns_cannot_crowd_out_evidence():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn-{i}"}
        for i in range(20)
    ]
    prompt = chat.build_prompt("現在的問題", "[1] 片段", history)
    assert "turn-19" in prompt
    assert "turn-0" not in prompt


@pytest.mark.parametrize(
    ("answer_text", "expected"),
    [
        ("根據資料 [1] 同 [2]。", [1, 2]),
        ("重複引用 [1] 又 [1]。", [1]),
        ("完全冇引用。", []),
        ("亂數 [99]。", [99]),
    ],
)
def test_cited_numbers(answer_text, expected):
    assert chat.cited_numbers(answer_text) == expected


def _enable(monkeypatch, reply):
    monkeypatch.setattr(
        chat, "cfg",
        lambda key, default=None: {"chat": {"provider": "cloud", "model": "m"}},
    )
    monkeypatch.setattr(chat.llm, "chat", lambda *a, **k: reply)
    monkeypatch.setattr(chat.llm, "last_route_label", lambda default="LLM": "cloud/m")


def test_answer_reports_citations_and_grounding(monkeypatch):
    _enable(monkeypatch, "第一點 [1]，第二點 [2]。")
    result = chat.answer("問題", [_hit(1), _hit(2)])
    assert result["cited"] == [1, 2]
    assert result["grounded"] is True
    assert result["route"] == "cloud/m"
    assert len(result["sources"]) == 2


def test_uncited_prose_is_flagged_not_hidden(monkeypatch):
    """Prose with no citation is the shape a hallucination takes."""
    _enable(monkeypatch, "呢個聽落好合理，但完全冇引用任何片段。")
    result = chat.answer("問題", [_hit(1)])
    assert result["cited"] == []
    assert result["grounded"] is False


def test_citation_beyond_the_supplied_excerpts_is_separated_out(monkeypatch):
    _enable(monkeypatch, "根據 [1] 同埋 [7]。")
    result = chat.answer("問題", [_hit(1)])
    assert result["cited"] == [1]
    assert result["invalid_cited"] == [7]


def test_no_hits_still_answers_rather_than_crashing(monkeypatch):
    _enable(monkeypatch, "知識庫入面搵唔到。")
    result = chat.answer("問題", [])
    assert result["sources"] == []
    assert result["grounded"] is False
