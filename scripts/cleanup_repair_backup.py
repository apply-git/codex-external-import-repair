#!/usr/bin/env python3
"""Safely remove only rollout content copies from a successful repair backup."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _common import (
    SafetyError,
    atomic_write_json,
    configure_console,
    default_codex_home,
    load_json,
    normalize_db_path,
    now_iso,
    path_is_within,
    query_thread_rows,
    sha256_file,
    sqlite_ro,
    verify_sqlite,
)


BACKUP_KIND = "codex_external_import_repair_backup"


def inspect_backup(codex_home: Path, backup: Path) -> tuple[dict[str, Any], list[Path]]:
    codex_home = codex_home.resolve()
    backup = backup.resolve()
    root = (codex_home / "manual_repair_backups").resolve()
    if not path_is_within(backup, root) or backup == root:
        raise SafetyError("備份路徑不在 Codex manual_repair_backups 的子資料夾內。")
    manifest = load_json(backup / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("kind") != BACKUP_KIND:
        raise SafetyError("不是此 Skill 建立的修復備份。")
    if Path(str(manifest.get("codex_home", ""))).resolve() != codex_home:
        raise SafetyError("備份來源與目前 Codex home 不一致。")
    result = load_json(backup / "repair-result.json")
    if not isinstance(result, dict) or result.get("status") != "success":
        raise SafetyError("沒有成功修復紀錄，禁止清理 rollout 副本。")
    rollback_path = backup / "rollback-result.json"
    if rollback_path.exists():
        rollback = load_json(rollback_path)
        if isinstance(rollback, dict) and rollback.get("status") == "success":
            raise SafetyError("此修復已 rollback，禁止用此流程清理。")
    pending = manifest.get("pending")
    if not isinstance(pending, list) or not pending:
        raise SafetyError("manifest 沒有 pending 項目。")
    ids = [str(item.get("id")) for item in pending]
    with sqlite_ro(codex_home / "state_5.sqlite") as connection:
        verify_sqlite(connection)
        rows = query_thread_rows(connection, ids)
    if set(rows) != set(ids):
        raise SafetyError("目前 SQLite 已缺少目標 rows，禁止清理副本。")

    rollouts_root = (backup / "rollouts").resolve()
    copies: list[Path] = []
    for item in pending:
        thread_id = str(item["id"])
        row = rows[thread_id]
        live = normalize_db_path(str(row["rollout_path"] or ""))
        if row["archived"] != 1 or not live.is_file():
            raise SafetyError(f"目前已封存內容不完整：{thread_id}")
        if sha256_file(live) != item["sha256"]:
            raise SafetyError(f"目前已封存內容 SHA-256 改變：{thread_id}")
        expected_copy = rollouts_root / Path(item["source"]).name
        if not path_is_within(expected_copy, rollouts_root):
            raise SafetyError(f"備份副本路徑越界：{thread_id}")
        if expected_copy.exists():
            if not expected_copy.is_file():
                raise SafetyError(f"預期 rollout 副本不是檔案：{thread_id}")
            if sha256_file(expected_copy) != item["sha256"]:
                raise SafetyError(f"備份 rollout 副本 SHA-256 不一致：{thread_id}")
            copies.append(expected_copy)
    extra = []
    if rollouts_root.is_dir():
        expected_names = {Path(item["source"]).name for item in pending}
        extra = [p for p in rollouts_root.iterdir() if p.name not in expected_names]
    if extra:
        raise SafetyError("rollouts 備份資料夾包含 manifest 以外項目，禁止清理。")
    report = {
        "backup": str(backup),
        "manifest_items": len(pending),
        "remaining_rollout_copies": len(copies),
        "live_archived_verified": len(pending),
        "metadata_preserved": [
            "manifest.json",
            "repair-result.json",
            "state_5.before-repair.sqlite",
            "external_agent_session_imports.json",
            "session_index.jsonl",
            "archive-failures.json",
        ],
    }
    return report, copies


def delete_copies(codex_home: Path, backup: Path, *, interactive: bool = True) -> dict[str, Any]:
    report, copies = inspect_backup(codex_home, backup)
    if not copies:
        print("rollout 備份副本已是 0，無需刪除。")
        return report
    print(f"即將永久刪除 {len(copies)} 個 repair backup rollout 副本。")
    print("不會刪除 manifest、修復紀錄、SQLite 備份或目前已封存內容。")
    if interactive:
        answer = input("請輸入 DELETE_BACKUP_COPIES 後按 Enter：").strip()
        if answer != "DELETE_BACKUP_COPIES":
            raise SafetyError("確認文字不符；沒有刪除任何副本。")
    deleted = 0
    for path in copies:
        path.unlink()
        deleted += 1
    after, remaining = inspect_backup(codex_home, backup)
    if remaining:
        raise SafetyError("刪除後仍有 rollout 副本，請停止並回報。")
    result = {
        "status": "success",
        "completed_at": now_iso(),
        "deleted_rollout_copies": deleted,
        "remaining_rollout_copies": 0,
        "metadata_preserved": after["metadata_preserved"],
    }
    atomic_write_json(backup.resolve() / "cleanup-result.json", result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="檢查或刪除修復備份中的 rollout 內容副本")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "delete-rollout-copies"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--codex-home", type=Path, default=default_codex_home())
        cmd.add_argument("--backup", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    configure_console()
    args = parse_args()
    try:
        if args.command == "check":
            result, _ = inspect_backup(args.codex_home, args.backup)
            print("唯讀備份檢查通過；沒有刪除任何資料。")
        else:
            result = delete_copies(args.codex_home, args.backup)
        for key, value in result.items():
            print(f"  {key}：{value}")
        return 0
    except SafetyError as exc:
        print(f"安全停止：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
