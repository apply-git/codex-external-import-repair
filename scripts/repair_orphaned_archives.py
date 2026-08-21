#!/usr/bin/env python3
"""Fail-closed repair for the known Windows extended-path archive failure."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from _common import (
    KNOWN_REPAIR_SIGNATURE,
    SCHEMA_VERSION,
    SafetyError,
    atomic_write_json,
    configure_console,
    default_codex_home,
    extract_ids,
    has_extended_prefix,
    load_json,
    load_registry,
    normalize_db_path,
    now_iso,
    path_is_within,
    query_thread_rows,
    require_codex_closed,
    session_index_hits,
    sha256_file,
    source_kind,
    sqlite_ro,
    verify_sqlite,
)


TOOL_VERSION = 2
FAILURE_KIND = "codex_external_import_archive_failures"
BACKUP_KIND = "codex_external_import_repair_backup"


def load_failure_manifest(path: Path, codex_home: Path) -> tuple[dict[str, Any], list[str]]:
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("kind") != FAILURE_KIND:
        raise SafetyError("不是正式封存流程產生的 failure manifest。")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SafetyError("failure manifest schema 不相容。")
    if payload.get("repair_signature") != KNOWN_REPAIR_SIGNATURE:
        raise SafetyError("錯誤特徵不是已知 Windows extended-path/os error 2 類型。")
    if Path(str(payload.get("codex_home", ""))).resolve() != codex_home.resolve():
        raise SafetyError("failure manifest 的 Codex home 與目前目標不一致。")
    ids = extract_ids(payload)
    if not ids:
        raise SafetyError("failure manifest 沒有 exact IDs。")
    return payload, ids


def build_plan(codex_home: Path, failure_manifest: Path) -> dict[str, Any]:
    codex_home = codex_home.resolve()
    failure_payload, ids = load_failure_manifest(failure_manifest.resolve(), codex_home)
    _, registry = load_registry(codex_home)
    missing_registry = sorted(set(ids) - set(registry))
    if missing_registry:
        raise SafetyError(f"registry 缺少 {len(missing_registry)} 個 exact IDs。")
    unsupported = [
        thread_id
        for thread_id in ids
        if source_kind(str(registry[thread_id].get("source_path", ""))) not in {"claude", "cursor"}
    ]
    if unsupported:
        raise SafetyError("至少一筆不是可辨識的 Claude／Cursor 外部匯入來源。")

    hits = session_index_hits(codex_home, ids, require_file=True)
    if hits:
        raise SafetyError(
            f"session_index.jsonl 命中 {len(hits)} 個目標；此版本不會猜測如何改索引。"
        )

    db_path = codex_home / "state_5.sqlite"
    with sqlite_ro(db_path) as connection:
        verify_sqlite(connection)
        rows = query_thread_rows(connection, ids)
    missing_rows = sorted(set(ids) - set(rows))
    if missing_rows:
        raise SafetyError(f"SQLite 缺少 {len(missing_rows)} 個 exact thread rows。")

    sessions_root = (codex_home / "sessions").resolve()
    archive_root = (codex_home / "archived_sessions").resolve()
    pending: list[dict[str, Any]] = []
    already_archived: list[str] = []
    for thread_id in ids:
        row = rows[thread_id]
        stored = str(row["rollout_path"] or "")
        current = normalize_db_path(stored)
        if row["archived"]:
            if not current.is_file() or not path_is_within(current, archive_root):
                raise SafetyError(f"已封存 row 與檔案位置不一致：{thread_id}")
            already_archived.append(thread_id)
            continue
        if not has_extended_prefix(stored):
            raise SafetyError(f"待修復 row 不含已知 \\\\?\\ 路徑特徵：{thread_id}")
        if not current.is_file():
            raise SafetyError(f"待修復 rollout 不存在：{thread_id}")
        if not path_is_within(current, sessions_root):
            raise SafetyError(f"待修復 rollout 不在 sessions 範圍：{thread_id}")
        if thread_id not in current.name:
            raise SafetyError(f"rollout 檔名與 thread ID 不一致：{thread_id}")
        destination = archive_root / current.name
        if destination.exists():
            raise SafetyError(f"archived_sessions 已有同名檔案：{thread_id}")
        pending.append(
            {
                "id": thread_id,
                "source": str(current),
                "destination": str(destination),
                "original_rollout_path": stored,
                "original_archived": int(row["archived"]),
                "original_archived_at": row["archived_at"],
                "size": current.stat().st_size,
                "sha256": sha256_file(current),
            }
        )
    return {
        "kind": BACKUP_KIND,
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "repair_signature": KNOWN_REPAIR_SIGNATURE,
        "created_at": now_iso(),
        "codex_home": str(codex_home),
        "failure_manifest": str(failure_manifest.resolve()),
        "failure_manifest_payload": failure_payload,
        "target_ids": ids,
        "pending": pending,
        "already_archived": already_archived,
        "session_index_matches": [],
    }


def create_backup(codex_home: Path, plan: dict[str, Any]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = codex_home / "manual_repair_backups"
    backup = root / f"external-import-archive-{stamp}"
    if backup.exists():
        raise SafetyError(f"備份資料夾已存在：{backup}")
    rollouts = backup / "rollouts"
    rollouts.mkdir(parents=True)

    source_db = sqlite3.connect(codex_home / "state_5.sqlite")
    backup_db = sqlite3.connect(backup / "state_5.before-repair.sqlite")
    try:
        source_db.backup(backup_db)
        verify_sqlite(backup_db)
    finally:
        backup_db.close()
        source_db.close()

    shutil.copy2(
        codex_home / "external_agent_session_imports.json",
        backup / "external_agent_session_imports.json",
    )
    shutil.copy2(codex_home / "session_index.jsonl", backup / "session_index.jsonl")
    failure_source = Path(plan["failure_manifest"])
    shutil.copy2(failure_source, backup / "archive-failures.json")

    for item in plan["pending"]:
        source = Path(item["source"])
        copied = rollouts / source.name
        shutil.copy2(source, copied)
        if sha256_file(copied) != item["sha256"]:
            raise SafetyError(f"rollout 備份 SHA-256 不一致：{item['id']}")
    atomic_write_json(backup / "manifest.json", plan)
    return backup


def check(codex_home: Path, failure_manifest: Path) -> dict[str, Any]:
    plan = build_plan(codex_home, failure_manifest)
    return {
        "pending": len(plan["pending"]),
        "already_archived": len(plan["already_archived"]),
        "target_count": len(plan["target_ids"]),
        "signature": plan["repair_signature"],
    }


def repair(
    codex_home: Path,
    failure_manifest: Path,
    *,
    interactive: bool = True,
    enforce_closed: bool = True,
) -> Path | None:
    codex_home = codex_home.resolve()
    if enforce_closed:
        require_codex_closed()
    plan = build_plan(codex_home, failure_manifest)
    pending = plan["pending"]
    if not pending:
        print("failure manifest 中的 exact IDs 已全部封存，無需底層修復。")
        return None
    print(f"即將修復 {len(pending)} 筆已通過已知特徵檢查的外部匯入工作階段。")
    if interactive:
        answer = input("請輸入 ARCHIVE 後按 Enter：").strip()
        if answer != "ARCHIVE":
            raise SafetyError("確認文字不符；尚未建立備份或修改資料。")
    if enforce_closed:
        require_codex_closed()

    backup = create_backup(codex_home, plan)
    archive_root = codex_home / "archived_sessions"
    archive_root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        codex_home / "state_5.sqlite", timeout=5.0, isolation_level=None
    )
    moved: list[tuple[Path, Path]] = []
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        verify_sqlite(connection)
        connection.execute("BEGIN IMMEDIATE")
        archived_at = int(time.time())
        for item in pending:
            current = connection.execute(
                "SELECT rollout_path, archived FROM threads WHERE id=?", (item["id"],)
            ).fetchone()
            if not current or current[1] != 0 or str(current[0]) != item["original_rollout_path"]:
                raise SafetyError(f"修復前狀態已改變：{item['id']}")
            source = Path(item["source"])
            destination = Path(item["destination"])
            if not source.is_file() or destination.exists():
                raise SafetyError(f"修復前檔案狀態已改變：{item['id']}")
            os.replace(source, destination)
            moved.append((source, destination))
            changed = connection.execute(
                "UPDATE threads SET rollout_path=?, archived=1, archived_at=? "
                "WHERE id=? AND archived=0 AND rollout_path=?",
                (
                    str(destination),
                    archived_at,
                    item["id"],
                    item["original_rollout_path"],
                ),
            ).rowcount
            if changed != 1:
                raise SafetyError(f"SQLite 未精確更新一列：{item['id']}")
        connection.commit()
    except Exception:
        try:
            connection.rollback()
        finally:
            for source, destination in reversed(moved):
                if destination.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(destination, source)
        raise
    finally:
        connection.close()

    with sqlite_ro(codex_home / "state_5.sqlite") as verify_connection:
        verify_sqlite(verify_connection)
        rows = query_thread_rows(verify_connection, plan["target_ids"])
    failures: list[str] = []
    for item in pending:
        row = rows.get(item["id"])
        source = Path(item["source"])
        destination = Path(item["destination"])
        if not row or row["archived"] != 1 or str(row["rollout_path"]) != str(destination):
            failures.append(item["id"])
        elif source.exists() or not destination.is_file():
            failures.append(item["id"])
        elif sha256_file(destination) != item["sha256"]:
            failures.append(item["id"])
    if failures:
        raise SafetyError(
            f"修復後仍有 {len(failures)} 筆不一致；請勿開啟 Codex，改執行 rollback。"
        )
    atomic_write_json(
        backup / "repair-result.json",
        {
            "status": "success",
            "completed_at": now_iso(),
            "repaired_count": len(pending),
            "repaired_ids": [item["id"] for item in pending],
        },
    )
    print(f"修復成功：{len(pending)} 筆。")
    print(f"備份：{backup}")
    return backup


def rollback(
    codex_home: Path,
    backup: Path,
    *,
    interactive: bool = True,
    enforce_closed: bool = True,
) -> None:
    codex_home = codex_home.resolve()
    backup = backup.resolve()
    if enforce_closed:
        require_codex_closed()
    manifest = load_json(backup / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("kind") != BACKUP_KIND:
        raise SafetyError("不是此 Skill 建立的修復備份。")
    if Path(str(manifest.get("codex_home", ""))).resolve() != codex_home:
        raise SafetyError("備份來源與目前 Codex home 不一致。")
    result = load_json(backup / "repair-result.json")
    if not isinstance(result, dict) or result.get("status") != "success":
        raise SafetyError("備份沒有成功修復紀錄。")
    if (backup / "rollback-result.json").exists():
        previous = load_json(backup / "rollback-result.json")
        if isinstance(previous, dict) and previous.get("status") == "success":
            raise SafetyError("此備份已成功 rollback，禁止重複執行。")
    pending = manifest.get("pending")
    if not isinstance(pending, list) or not pending:
        raise SafetyError("manifest 沒有可 rollback 的項目。")
    print(f"即將 rollback {len(pending)} 筆 exact IDs。")
    if interactive:
        answer = input("請輸入 ROLLBACK 後按 Enter：").strip()
        if answer != "ROLLBACK":
            raise SafetyError("確認文字不符；尚未修改資料。")
    if enforce_closed:
        require_codex_closed()

    source_db = sqlite3.connect(codex_home / "state_5.sqlite")
    backup_db = sqlite3.connect(backup / "state_5.before-rollback.sqlite")
    try:
        source_db.backup(backup_db)
        verify_sqlite(backup_db)
    finally:
        backup_db.close()
        source_db.close()

    connection = sqlite3.connect(
        codex_home / "state_5.sqlite", timeout=5.0, isolation_level=None
    )
    moved: list[tuple[Path, Path]] = []
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        verify_sqlite(connection)
        connection.execute("BEGIN IMMEDIATE")
        for item in pending:
            current = connection.execute(
                "SELECT rollout_path, archived FROM threads WHERE id=?", (item["id"],)
            ).fetchone()
            source = Path(item["source"])
            destination = Path(item["destination"])
            if not current or current[1] != 1 or str(current[0]) != str(destination):
                raise SafetyError(f"rollback 前 row 狀態不符：{item['id']}")
            if not destination.is_file() or source.exists():
                raise SafetyError(f"rollback 前檔案狀態不符：{item['id']}")
            if sha256_file(destination) != item["sha256"]:
                raise SafetyError(f"目前 archived rollout 雜湊已改變：{item['id']}")
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination, source)
            moved.append((destination, source))
            changed = connection.execute(
                "UPDATE threads SET rollout_path=?, archived=?, archived_at=? "
                "WHERE id=? AND archived=1 AND rollout_path=?",
                (
                    item["original_rollout_path"],
                    item["original_archived"],
                    item["original_archived_at"],
                    item["id"],
                    str(destination),
                ),
            ).rowcount
            if changed != 1:
                raise SafetyError(f"SQLite 未精確 rollback 一列：{item['id']}")
        connection.commit()
    except Exception:
        try:
            connection.rollback()
        finally:
            for destination, source in reversed(moved):
                if source.exists() and not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, destination)
        raise
    finally:
        connection.close()
    atomic_write_json(
        backup / "rollback-result.json",
        {"status": "success", "completed_at": now_iso(), "restored_count": len(pending)},
    )
    print(f"rollback 成功：{len(pending)} 筆。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="受控修復已知 Windows 外部匯入封存錯誤")
    sub = parser.add_subparsers(dest="command", required=True)
    check_cmd = sub.add_parser("check", help="唯讀驗證 failure manifest 與底層狀態")
    check_cmd.add_argument("--codex-home", type=Path, default=default_codex_home())
    check_cmd.add_argument("--failure-manifest", type=Path, required=True)
    repair_cmd = sub.add_parser("repair", help="備份後修復 exact IDs")
    repair_cmd.add_argument("--codex-home", type=Path, default=default_codex_home())
    repair_cmd.add_argument("--failure-manifest", type=Path, required=True)
    rollback_cmd = sub.add_parser("rollback", help="依指定備份回復 exact IDs")
    rollback_cmd.add_argument("--codex-home", type=Path, default=default_codex_home())
    rollback_cmd.add_argument("--backup", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    configure_console()
    args = parse_args()
    try:
        if args.command == "check":
            result = check(args.codex_home, args.failure_manifest)
            print("唯讀修復預檢通過；沒有修改任何資料。")
            for key, value in result.items():
                print(f"  {key}：{value}")
        elif args.command == "repair":
            repair(args.codex_home, args.failure_manifest)
        elif args.command == "rollback":
            rollback(args.codex_home, args.backup)
        return 0
    except SafetyError as exc:
        print(f"安全停止：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消；未完成的資料庫交易會回滾。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"未預期錯誤：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("請保留輸出，不要自行重跑寫入模式。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
