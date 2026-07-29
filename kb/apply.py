#!/usr/bin/env python3
"""Loop 2 人閘執行器：預演、落實或駁回結構化分類建議。

鐵律：category 嘅 single source of truth 係 ``raw_files/<category>/`` 資料夾
本身。鏈路係 ingest 由資料夾第一層推 category → 寫入 Qdrant payload →
scan_collection 由 payload 讀返 → 砌 INDEX.json。因此 apply 只可以「搬檔 +
重跑 pipeline」，絕對不可以直接改 INDEX.json 或 Qdrant payload——嗰樣係重複
邏輯，而且下次 ingest 會由資料夾推返、直接打回原形。

真正執行前會快照 state；搬檔中途失敗會即時逐項還原。
"""
import argparse
import contextlib
import datetime
import io
import shutil
from pathlib import Path

from .config import cfg
from . import proposals


KB_ROOT = Path(cfg("kb_root"))
RAW = KB_ROOT / "raw_files"
NOTES = KB_ROOT / "notes"
TRASH = KB_ROOT / "trash"


def _resolve_within(path, root, label, problems):
    """Resolve symlinks/``..`` and reject paths that leave their intended root."""
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        problems.append(
            f"路徑越界（{label} 必須位於 {root_resolved} 之下）：{resolved}"
        )
        return None
    return resolved


def _display_path(path):
    try:
        return path.relative_to(KB_ROOT.resolve())
    except ValueError:
        return path


def preflight(proposal):
    """驗證建議並回 ``(ok, problems, plan)``；plan 項為 ``(src, dst, kind)``。"""
    problems, plan = [], []
    if not RAW.is_dir():
        return False, [f"raw_files 唔存在（{RAW}）"], []

    action = proposal.get("action")
    params = proposal.get("params") or {}
    if action == "move_files":
        for move in params.get("moves", []):
            try:
                filename = move["file"]
                source_category = move["from_category"]
                target_category = move["to_category"]
            except (KeyError, TypeError):
                problems.append("move_files 參數不完整")
                continue
            source = _resolve_within(
                RAW / source_category / filename,
                RAW,
                "raw source",
                problems,
            )
            destination = _resolve_within(
                RAW / target_category / filename,
                RAW,
                "raw destination",
                problems,
            )
            note_source = _resolve_within(
                NOTES / source_category / filename,
                NOTES,
                "notes source",
                problems,
            )
            note_destination = _resolve_within(
                NOTES / target_category / filename,
                NOTES,
                "notes destination",
                problems,
            )
            if None in (source, destination, note_source, note_destination):
                continue
            if not source.exists():
                problems.append(f"來源檔唔存在：{_display_path(source)}")
                continue
            if destination.exists():
                problems.append(
                    f"目標已有同名檔（唔會覆蓋）：{_display_path(destination)}"
                )
                continue
            plan.append((source, destination, "raw"))
            if note_source.exists() and not note_destination.exists():
                plan.append((note_source, note_destination, "notes"))
    elif action == "archive_files":
        for item in params.get("files", []):
            try:
                filename = item["file"]
                source_category = item["from_category"]
            except (KeyError, TypeError):
                problems.append("archive_files 參數不完整")
                continue
            source = _resolve_within(
                RAW / source_category / filename,
                RAW,
                "raw source",
                problems,
            )
            destination = _resolve_within(
                TRASH / source_category / filename,
                TRASH,
                "trash destination",
                problems,
            )
            note_source = _resolve_within(
                NOTES / source_category / filename,
                NOTES,
                "notes source",
                problems,
            )
            note_destination = _resolve_within(
                TRASH / "notes" / source_category / filename,
                TRASH,
                "notes trash destination",
                problems,
            )
            if None in (source, destination, note_source, note_destination):
                continue
            if not source.exists():
                problems.append(f"來源檔唔存在：{_display_path(source)}")
                continue
            if destination.exists():
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                destination = _resolve_within(
                    TRASH / source_category / f"{stamp}-{filename}",
                    TRASH,
                    "trash destination",
                    problems,
                )
                if destination is None:
                    continue
            plan.append((source, destination, "raw"))
            if note_source.exists():
                plan.append((note_source, note_destination, "notes"))
    else:
        problems.append(f"未支援嘅 action：{action}")

    if not plan and not problems:
        problems.append("冇嘢可做（plan 係空）")
    return not problems, problems, plan


def do_moves(plan):
    """逐項搬檔；任何一步失敗即按相反次序還原。"""
    done = []
    for source, destination, kind in plan:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            done.append((source, destination, kind))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}（搬 {source.name} 失敗）"
            for original, moved, _ in reversed(done):
                try:
                    shutil.move(str(moved), str(original))
                except Exception:
                    pass
            return False, [], error
    return True, done, ""


def run_pipeline():
    """直接呼叫 kb.ingest 與 kb.index_update 的 main，唔依賴部署 venv 路徑。"""
    from . import index_update, ingest

    output = []
    for name, module in (("ingest", ingest), ("index_update", index_update)):
        print(f"⚙️  跑 kb.{name} …")
        stream = io.StringIO()
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                module.main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            text = stream.getvalue().strip()
            output.append(f"--- kb.{name} (rc={code}) ---\n{text}")
            if code:
                return False, "\n".join(output)
        except Exception as exc:
            text = stream.getvalue().strip()
            output.append(
                f"--- kb.{name} ({type(exc).__name__}) ---\n{text}\n{exc}"
            )
            return False, "\n".join(output)
        else:
            text = stream.getvalue().strip()
            output.append(f"--- kb.{name} (rc=0) ---\n{text}")
        if text:
            print("\n".join(text.splitlines()[-6:]))
    return True, "\n".join(output)


def apply_proposal(pid, dry_run=False):
    doc = proposals.load()
    proposal = proposals.get(doc, pid)
    if not proposal:
        return False, f"搵唔到待決建議 {pid}"
    if proposal.get("status") != "pending":
        return False, f"{pid} 已經係 {proposal.get('status')}"

    ok, problems, plan = preflight(proposal)
    print(f"\n🧾 {proposal['id']} — {proposal['title']}")
    print(f"   動作 {proposal['action']} ｜ 風險 {proposal['risk']}")
    if proposal.get("rationale"):
        print(f"   理由：{proposal['rationale']}")
    print(f"\n   將會搬 {len(plan)} 個檔：")
    for source, destination, kind in plan:
        print(
            f"   - [{kind}] {_display_path(source)}"
            f" → {_display_path(destination)}"
        )
    if problems:
        print("\n   ⚠️ 問題：")
        for problem in problems:
            print(f"   - {problem}")
    if not ok:
        return False, "預檢唔過，冇郁過任何嘢：\n" + "\n".join(problems)
    if dry_run:
        return (
            True,
            f"（dry-run）預檢通過，會搬 {len(plan)} 個檔 + "
            "重跑 ingest/index_update。冇郁過任何嘢。",
        )

    backup = proposals.backup_state(pid)
    print(f"\n💾 已快照 state → {backup}")
    ok, done, error = do_moves(plan)
    if not ok:
        return False, f"搬檔失敗，已全部還原：{error}"
    print(f"📦 已搬 {len(done)} 個檔")

    ok, log = run_pipeline()
    if not ok:
        proposal["pipeline_ok"] = False
        proposal["backup"] = backup
        proposals.resolve(
            doc,
            pid,
            "applied",
            note="pipeline 未跑完，需人手重跑 ingest/index_update",
        )
        proposals.save(doc)
        return False, (
            "檔已搬好，但 pipeline 未跑完。\n"
            f"備份：{backup}\n請重跑 `kb ingest` 及 `python -m kb.index_update`。\n\n"
            f"{log[-600:]}"
        )

    proposal["backup"] = backup
    proposal["pipeline_ok"] = True
    proposals.resolve(
        doc, pid, "applied", note=f"搬 {len(done)} 檔 + pipeline 已重跑"
    )
    proposals.save(doc)
    return (
        True,
        f"✅ {pid} 已落實：搬咗 {len(done)} 個檔，Qdrant/INDEX/MOC 已重生。"
        f"備份：{backup}",
    )


def reject_proposal(pid, note=""):
    doc = proposals.load()
    ok, message = proposals.resolve(
        doc, pid, "rejected", note=note or "人手駁回"
    )
    if ok:
        proposals.save(doc)
        return True, f"🚫 {pid} 已駁回，下輪唔會再提"
    return False, message


def list_pending():
    doc = proposals.load()
    pending = doc.get("proposals", [])
    if not pending:
        print("✅ 冇待決建議")
        return True
    print(f"🚦 {len(pending)} 條待決建議：\n")
    for proposal in pending:
        print(
            f"  {proposal['id']} ｜ 風險 {proposal['risk']} ｜ {proposal['title']}"
        )
        if proposal.get("rationale"):
            print(f"     理由：{proposal['rationale']}")
        for move in proposal["params"].get("moves", []):
            print(
                f"     - {move['file']}：{move['from_category']}"
                f" → {move['to_category']}"
            )
        for item in proposal["params"].get("files", []):
            print(
                f"     - 封存 {item.get('file')}（{item.get('from_category')}）"
            )
        print()
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kb proposals")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("id")
    apply_parser.add_argument("--dry-run", action="store_true")
    reject_parser = sub.add_parser("reject")
    reject_parser.add_argument("id")
    reject_parser.add_argument("note", nargs="*")
    args = parser.parse_args(argv)

    if args.command == "list":
        list_pending()
        return 0
    if args.command == "apply":
        ok, message = apply_proposal(args.id, dry_run=args.dry_run)
    else:
        ok, message = reject_proposal(args.id, " ".join(args.note).strip())
    print("\n" + message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
