#!/usr/bin/env python3
"""Read-only post-archive verification across registry, SQLite, and rollout files."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from _common import (
    SCHEMA_VERSION,
    SafetyError,
    atomic_write_json,
    configure_console,
    default_codex_home,
    extract_ids,
    load_json,
    load_registry,
    normalize_db_path,
    now_iso,
    path_is_within,
    query_thread_rows,
    selected_registry_records,
    session_index_hits,
    sqlite_ro,
    verify_sqlite,
)


def verify(codex_home: Path, ids: list[str]) -> dict[str, Any]:
    codex_home = codex_home.resolve()
    _, registry = load_registry(codex_home)
    with sqlite_ro(codex_home / "state_5.sqlite") as connection:
        verify_sqlite(connection)
        rows = query_thread_rows(connection, ids)
    sessions = (codex_home / "sessions").resolve()
    archived = (codex_home / "archived_sessions").resolve()
    counts: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    for thread_id in ids:
        row = rows.get(thread_id)
        in_registry = thread_id in registry
        if row is None:
            state = "missing_db_row"
            exists = False
            archived_flag = None
        else:
            path = normalize_db_path(str(row["rollout_path"] or ""))
            exists = path.is_file()
            archived_flag = bool(row["archived"])
            if archived_flag and exists and path_is_within(path, archived):
                state = "archived_consistent"
            elif not archived_flag and exists and path_is_within(path, sessions):
                state = "active_consistent"
            elif not exists:
                state = "rollout_missing"
            else:
                state = "path_or_status_mismatch"
        counts[state] += 1
        records.append(
            {
                "id": thread_id,
                "registry_present": in_registry,
                "db_row_present": row is not None,
                "archived": archived_flag,
                "rollout_exists": exists,
                "storage_state": state,
            }
        )
    return {
        "kind": "codex_external_import_archive_verification",
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "target_count": len(ids),
        "counts": dict(sorted(counts.items())),
        "session_index_hit_count": len(session_index_hits(codex_home, ids, require_file=False)),
        "app_list_verification_required": True,
        "records": records,
    }


def resolve_ids(args: argparse.Namespace) -> list[str]:
    if args.ids_file:
        ids = extract_ids(load_json(args.ids_file.resolve()))
    else:
        _, registry = load_registry(args.codex_home.resolve())
        selected = selected_registry_records(
            registry, kind=args.source_kind, batch=args.batch
        )
        ids = sorted(selected)
    if not ids:
        raise SafetyError("沒有選到可驗證的 exact IDs。")
    return ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="唯讀驗證外部匯入 thread 封存狀態")
    parser.add_argument("--codex-home", type=Path, default=default_codex_home())
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ids-file", type=Path, help="計畫、failure manifest、backup manifest 或 ID 陣列")
    group.add_argument("--batch", help="latest、all、原始 imported_at 或完整 ISO 時間")
    parser.add_argument("--source-kind", choices=("all", "claude", "cursor"), default="all")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    configure_console()
    args = parse_args()
    try:
        ids = resolve_ids(args)
        result = verify(args.codex_home, ids)
        if args.output:
            atomic_write_json(args.output.resolve(), result)
        if args.json:
            from _common import print_json

            print_json(result)
        else:
            print("唯讀驗證完成；沒有修改資料。")
            print(f"  exact IDs：{result['target_count']}")
            for key, value in result["counts"].items():
                print(f"  {key}：{value}")
            print(f"  session_index 命中：{result['session_index_hit_count']}")
            print("  尚需由主代理檢查 Codex active／archived 清單。")
        return 0
    except SafetyError as exc:
        print(f"安全停止：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
