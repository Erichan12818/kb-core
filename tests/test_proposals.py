import pytest

from kb import apply as kb_apply
from kb import audit, proposals


def _move_proposal(
    filename="source.md",
    action="move_files",
    to_category="new",
):
    return proposals.make(
        action,
        "移動測試",
        "測試護欄",
        {
            "moves": [
                {
                    "file": filename,
                    "from_category": "old",
                    "to_category": to_category,
                }
            ]
        },
        today="2026-07-28",
    )


def _set_apply_root(monkeypatch, tmp_path):
    monkeypatch.setattr(kb_apply, "KB_ROOT", tmp_path)
    monkeypatch.setattr(kb_apply, "RAW", tmp_path / "raw_files")
    monkeypatch.setattr(kb_apply, "NOTES", tmp_path / "notes")
    monkeypatch.setattr(kb_apply, "TRASH", tmp_path / "trash")
    kb_apply.RAW.mkdir(parents=True)


def test_fingerprint_is_stable_for_same_content():
    first = {
        "moves": [
            {"file": "b.md", "from_category": "one", "to_category": "three"},
            {"file": "a.md", "from_category": "one", "to_category": "two"},
        ]
    }
    reordered = {
        "moves": [
            {"file": "a.md", "from_category": "changed", "to_category": "two"},
            {"file": "b.md", "from_category": "one", "to_category": "three"},
        ]
    }
    assert proposals.fingerprint("move_files", first) == proposals.fingerprint(
        "move_files", reordered
    )


def test_rejected_fingerprint_does_not_return_to_pending():
    doc = proposals._blank()
    original = _move_proposal()
    doc["proposals"].append(original)
    ok, _ = proposals.resolve(doc, original["id"], "rejected", "唔拆")
    assert ok

    same_again = _move_proposal()
    merged, added = proposals.merge_pending(
        doc, [same_again], today="2026-07-29"
    )
    assert added == 0
    assert merged["proposals"] == []


@pytest.mark.parametrize(
    ("proposal", "setup", "expected"),
    [
        (
            _move_proposal("hallucinated.md"),
            lambda root: None,
            "來源檔唔存在",
        ),
        (
            _move_proposal(),
            lambda root: (
                (root / "raw_files" / "old").mkdir(parents=True),
                (root / "raw_files" / "old" / "source.md").write_text("old"),
                (root / "raw_files" / "new").mkdir(parents=True),
                (root / "raw_files" / "new" / "source.md").write_text("new"),
            ),
            "目標已有同名檔",
        ),
        (
            _move_proposal(action="delete_everything"),
            lambda root: None,
            "未支援嘅 action",
        ),
        (
            _move_proposal(to_category="../../../tmp/evil"),
            lambda root: (
                (root / "raw_files" / "old").mkdir(parents=True),
                (root / "raw_files" / "old" / "source.md").write_text("old"),
            ),
            "路徑越界",
        ),
    ],
)
def test_preflight_blocks_unsafe_proposals(
    monkeypatch, tmp_path, proposal, setup, expected
):
    _set_apply_root(monkeypatch, tmp_path)
    setup(tmp_path)
    ok, problems, plan = kb_apply.preflight(proposal)
    assert not ok
    assert any(expected in problem for problem in problems)
    assert plan == []


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (
            "2026-07-06-weekly-social-intel.md",
            "2026-07-13-weekly-social-intel.md",
            True,
        ),
        (
            "2026-06-18-webull-openapi-skill.md",
            "2026-06-18-webull-openapi-readme.md",
            False,
        ),
        ("skill-baoyu-translate.md", "skill-baoyu-url-to-markdown.md", False),
    ],
)
def test_is_series_real_cases(left, right, expected):
    assert audit._is_series(left, right) is expected


def test_best_match_uses_highest_real_similarity_score():
    readme = "2026-06-18-webull-openapi-readme.md"
    openapi_skill = "2026-06-18-webull-openapi-skill.md"
    trading_skill = "skill-trading.md"
    index = {
        readme: {
            "near_duplicates": [
                {"file": trading_skill, "score": 0.694},
                {"file": openapi_skill, "score": 1.12},
            ]
        }
    }
    assert audit._best_match(index, readme) == openapi_skill


def test_resolve_only_accepts_pending_and_status_must_not_be_prechanged():
    doc = proposals._blank()
    proposal = _move_proposal()
    doc["proposals"].append(proposal)

    proposal["status"] = "applied"
    ok, message = proposals.resolve(doc, proposal["id"], "applied")

    assert not ok
    assert "已經係 applied" in message
    assert proposal in doc["proposals"]
    assert doc["history"] == []
