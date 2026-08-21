#!/usr/bin/env python3
"""Synthetic end-to-end tests for the skill scripts; writes only to a temp folder."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import uuid
from pathlib import Path

from _common import atomic_write_json, configure_console, sqlite_ro
from archive_imported_threads import build_plan, export_failures, record_result
from audit_external_imports import audit
from cleanup_repair_backup import delete_copies, inspect_backup
from repair_orphaned_archives import repair, rollback
from verify_archive_state import verify


def extended_path(path: Path) -> str:
    value = str(path.resolve())
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="codex-external-import-repair-") as temp:
        codex_home = Path(temp) / ".codex"
        sessions = codex_home / "sessions" / "2026" / "08" / "20"
        archived = codex_home / "archived_sessions"
        sessions.mkdir(parents=True)
        archived.mkdir(parents=True)
        ids = [str(uuid.uuid4()) for _ in range(3)]
        db = sqlite3.connect(codex_home / "state_5.sqlite")
        db.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, "
            "archived INTEGER, archived_at INTEGER)"
        )
        records = []
        for index, thread_id in enumerate(ids):
            rollout = sessions / f"rollout-test-{thread_id}.jsonl"
            rollout.write_text(
                json.dumps({"thread_id": thread_id}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            db.execute(
                "INSERT INTO threads(id, rollout_path, archived, archived_at) VALUES(?,?,0,NULL)",
                (thread_id, extended_path(rollout)),
            )
            records.append(
                {
                    "source_path": f"C:\\fake\\.claude\\{thread_id}.jsonl",
                    "content_sha256": "0" * 64,
                    "imported_thread_id": thread_id,
                    "imported_at": 1787158621,
                    "source_modified_at": 1787158600000000000 + index,
                    "title": f"private-{index}",
                }
            )
        db.commit()
        db.close()
        atomic_write_json(
            codex_home / "external_agent_session_imports.json",
            {"records": records, "detected_connector_records": []},
        )
        (codex_home / "session_index.jsonl").write_text("", encoding="utf-8")

        result = audit(codex_home, kind="claude", batch="latest", include_titles=False)
        assert result["selected_count"] == 3
        assert result["counts"] == {"active_consistent": 3}
        assert all("title" not in item for item in result["records"])

        plan_path = Path(temp) / "formal-plan.json"
        plan = build_plan(codex_home, kind="claude", batch="latest")
        atomic_write_json(plan_path, plan)
        assert len(plan["items"]) == 3
        for thread_id in ids:
            record_result(
                plan_path,
                thread_id=thread_id,
                status="failed",
                error="failed to archive thread: 系統找不到指定的檔案。 (os error 2)",
                read_success=False,
            )
            record_result(
                plan_path,
                thread_id=thread_id,
                status="failed",
                error="failed to archive thread: 系統找不到指定的檔案。 (os error 2)",
                read_success=True,
            )
        failure_path = Path(temp) / "failures.json"
        failure = export_failures(plan_path, failure_path)
        assert set(failure["eligible_failed_ids"]) == set(ids)

        backup = repair(
            codex_home,
            failure_path,
            interactive=False,
            enforce_closed=False,
        )
        assert backup is not None
        verification = verify(codex_home, ids)
        assert verification["counts"] == {"archived_consistent": 3}
        backup_report, copies = inspect_backup(codex_home, backup)
        assert backup_report["remaining_rollout_copies"] == 3
        assert len(copies) == 3
        cleanup = delete_copies(codex_home, backup, interactive=False)
        assert cleanup["deleted_rollout_copies"] == 3
        assert inspect_backup(codex_home, backup)[0]["remaining_rollout_copies"] == 0

        rollback(codex_home, backup, interactive=False, enforce_closed=False)
        verification = verify(codex_home, ids)
        assert verification["counts"] == {"active_consistent": 3}
        with sqlite_ro(codex_home / "state_5.sqlite") as connection:
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    print("SELF_TEST_OK")


if __name__ == "__main__":
    configure_console()
    run()
