#!/usr/bin/env python3
"""Read-only audit of external-agent imports and Codex thread storage."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from _common import (
    SCHEMA_VERSION,
    SafetyError,
    atomic_write_json,
    configure_console,
    default_codex_home,
    has_extended_prefix,
    load_registry,
    normalize_db_path,
    now_iso,
    path_is_within,
    query_thread_rows,
    running_codex_processes,
    selected_registry_records,
    session_index_hits,
    source_kind,
    sqlite_ro,
    timestamp_to_iso,
    verify_sqlite,
)


def audit(
    codex_home: Path,
    *,
    kind: str,
    batch: str,
    include_titles: bool,
) -> dict[str, Any]:
    codex_home = codex_home.resolve()
    registry, by_id = load_registry(codex_home)
    selected = selected_registry_records(by_id, kind=kind, batch=batch)
    ids = sorted(selected)
    db_path = codex_home / "state_5.sqlite"
    with sqlite_ro(db_path) as connection:
        verify_sqlite(connection)
        rows = query_thread_rows(connection, ids)

    sessions_root = (codex_home / "sessions").resolve()
    archive_root = (codex_home / "archived_sessions").resolve()
    records: list[dict[str, Any]] = []
    states: Counter[str] = Counter()
    for thread_id in ids:
        registry_record = selected[thread_id]
        row = rows.get(thread_id)
        item: dict[str, Any] = {
            "id": thread_id,
            "source_kind": source_kind(str(registry_record.get("source_path", ""))),
            "imported_at": timestamp_to_iso(registry_record.get("imported_at")),
            "source_modified_at": timestamp_to_iso(registry_record.get("source_modified_at")),
            "content_sha256_present": bool(registry_record.get("content_sha256")),
            "db_row_present": row is not None,
        }
        if include_titles:
            item["title"] = registry_record.get("title")
        if row is None:
            item.update(
                {
                    "archived": None,
                    "rollout_exists": False,
                    "storage_state": "missing_db_row",
                    "extended_path_prefix": False,
                }
            )
            states["missing_db_row"] += 1
        else:
            stored = str(row["rollout_path"] or "")
            path = normalize_db_path(stored)
            exists = path.is_file()
            archived = bool(row["archived"])
            if exists and archived and path_is_within(path, archive_root):
                state = "archived_consistent"
            elif exists and not archived and path_is_within(path, sessions_root):
                state = "active_consistent"
            elif not exists:
                state = "rollout_missing"
            else:
                state = "path_or_status_mismatch"
            item.update(
                {
                    "archived": archived,
                    "archived_at": timestamp_to_iso(row["archived_at"]),
                    "rollout_exists": exists,
                    "storage_state": state,
                    "extended_path_prefix": has_extended_prefix(stored),
                }
            )
            states[state] += 1
        records.append(item)

    index_hits = session_index_hits(codex_home, ids, require_file=False)
    return {
        "kind": "codex_external_import_audit",
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "selection": {"source_kind": kind, "batch": batch},
        "registry_total_records": len(registry.get("records", [])),
        "selected_count": len(ids),
        "counts": dict(sorted(states.items())),
        "session_index_present": (codex_home / "session_index.jsonl").is_file(),
        "session_index_hit_count": len(index_hits),
        "codex_processes_detected": running_codex_processes(),
        "privacy": {"titles_included": include_titles, "source_paths_included": False},
        "records": records,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="唯讀稽核 Codex 外部 Agent 匯入工作階段")
    parser.add_argument("--codex-home", type=Path, default=default_codex_home())
    parser.add_argument("--source-kind", choices=("all", "claude", "cursor"), default="all")
    parser.add_argument(
        "--batch",
        default="all",
        help="all、latest、原始 imported_at，或完整 ISO 時間",
    )
    parser.add_argument("--include-titles", action="store_true", help="明確要求時才輸出標題")
    parser.add_argument("--output", type=Path, help="可選：將完整 JSON 寫入指定路徑")
    parser.add_argument("--json", action="store_true", help="在 stdout 顯示完整 JSON")
    return parser.parse_args()


def main() -> int:
    configure_console()
    args = parse_args()
    try:
        result = audit(
            args.codex_home,
            kind=args.source_kind,
            batch=args.batch,
            include_titles=args.include_titles,
        )
        if args.output:
            atomic_write_json(args.output.resolve(), result)
        if args.json:
            from _common import print_json

            print_json(result)
        else:
            print("唯讀稽核完成；沒有修改任何檔案或資料庫。")
            print(f"  registry 總筆數：{result['registry_total_records']}")
            print(f"  本次選取：{result['selected_count']}")
            for key, value in result["counts"].items():
                print(f"  {key}：{value}")
            print(f"  session_index 目標命中：{result['session_index_hit_count']}")
            print(
                "  Codex／ChatGPT："
                + ("仍在執行" if result["codex_processes_detected"] else "未偵測到")
            )
            if args.output:
                print(f"  JSON：{args.output.resolve()}")
        return 0
    except SafetyError as exc:
        print(f"安全停止：{exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
