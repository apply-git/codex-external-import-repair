#!/usr/bin/env python3
"""Prepare and record formal thread-archive attempts without editing Codex storage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _common import (
    KNOWN_REPAIR_SIGNATURE,
    SCHEMA_VERSION,
    SafetyError,
    atomic_write_json,
    configure_console,
    default_codex_home,
    has_extended_prefix,
    load_json,
    load_registry,
    normalize_db_path,
    now_iso,
    path_is_within,
    query_thread_rows,
    selected_registry_records,
    sha256_json,
    source_kind,
    sqlite_ro,
    verify_sqlite,
)


PLAN_KIND = "codex_external_import_formal_archive_plan"
FAILURE_KIND = "codex_external_import_archive_failures"


def build_plan(codex_home: Path, *, kind: str, batch: str) -> dict[str, Any]:
    codex_home = codex_home.resolve()
    _, by_id = load_registry(codex_home)
    selected = selected_registry_records(by_id, kind=kind, batch=batch)
    ids = sorted(selected)
    with sqlite_ro(codex_home / "state_5.sqlite") as connection:
        verify_sqlite(connection)
        rows = query_thread_rows(connection, ids)
    sessions_root = (codex_home / "sessions").resolve()
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for thread_id in ids:
        record = selected[thread_id]
        row = rows.get(thread_id)
        if row is None:
            skipped.append({"id": thread_id, "reason": "missing_db_row"})
            continue
        stored = str(row["rollout_path"] or "")
        path = normalize_db_path(stored)
        if row["archived"]:
            skipped.append({"id": thread_id, "reason": "already_archived"})
            continue
        if not path.is_file() or not path_is_within(path, sessions_root):
            skipped.append({"id": thread_id, "reason": "active_storage_inconsistent"})
            continue
        items.append(
            {
                "id": thread_id,
                "source_kind": source_kind(str(record.get("source_path", ""))),
                "extended_path_prefix": has_extended_prefix(stored),
                "read_success": False,
                "attempts": [],
                "final_status": "pending",
            }
        )
    return {
        "kind": PLAN_KIND,
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "codex_home": str(codex_home),
        "selection": {"source_kind": kind, "batch": batch},
        "instructions": (
            "逐筆使用產品正式 thread archive 工具；失敗時先 read_thread，"
            "再做第二次 archive。以 record 子命令記錄每次結果。"
        ),
        "items": items,
        "skipped": skipped,
    }


def validate_plan(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("kind") != PLAN_KIND:
        raise SafetyError("不是此 Skill 產生的正式封存計畫。")
    if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(payload.get("items"), list):
        raise SafetyError("正式封存計畫 schema 不相容。")
    return payload


def record_result(
    plan_path: Path,
    *,
    thread_id: str,
    status: str,
    error: str | None,
    read_success: bool,
) -> dict[str, Any]:
    plan = validate_plan(load_json(plan_path))
    match = next((item for item in plan["items"] if item.get("id") == thread_id), None)
    if match is None:
        raise SafetyError("thread ID 不在正式封存計畫中。")
    if status == "failed" and not error:
        raise SafetyError("記錄 failed 時必須提供 --error。")
    match["attempts"].append(
        {
            "attempted_at": now_iso(),
            "status": status,
            "error": error if status == "failed" else None,
        }
    )
    if read_success:
        match["read_success"] = True
    match["final_status"] = status
    plan["updated_at"] = now_iso()
    atomic_write_json(plan_path, plan)
    return plan


def plan_summary(plan: dict[str, Any]) -> dict[str, int]:
    counts = {"pending": 0, "success": 0, "failed": 0, "repair_eligible": 0}
    for item in plan["items"]:
        final = item.get("final_status", "pending")
        counts[final] = counts.get(final, 0) + 1
        failures = [x for x in item.get("attempts", []) if x.get("status") == "failed"]
        last_error = str(failures[-1].get("error", "")) if failures else ""
        if (
            len(failures) >= 2
            and item.get("read_success") is True
            and item.get("extended_path_prefix") is True
            and "os error 2" in last_error.lower()
            and final == "failed"
        ):
            counts["repair_eligible"] += 1
    return counts


def export_failures(plan_path: Path, output: Path) -> dict[str, Any]:
    plan = validate_plan(load_json(plan_path))
    eligible: list[str] = []
    rejected: list[dict[str, str]] = []
    for item in plan["items"]:
        failures = [x for x in item.get("attempts", []) if x.get("status") == "failed"]
        last_error = str(failures[-1].get("error", "")) if failures else ""
        reasons: list[str] = []
        if item.get("final_status") != "failed":
            reasons.append("final_status_not_failed")
        if len(failures) < 2:
            reasons.append("fewer_than_two_failures")
        if item.get("read_success") is not True:
            reasons.append("read_thread_not_confirmed")
        if item.get("extended_path_prefix") is not True:
            reasons.append("no_extended_path_signature")
        if "os error 2" not in last_error.lower():
            reasons.append("last_error_not_os_error_2")
        if reasons:
            if item.get("final_status") == "failed":
                rejected.append({"id": item["id"], "reason": ",".join(reasons)})
        else:
            eligible.append(item["id"])
    if not eligible:
        raise SafetyError("沒有符合已知修復特徵的失敗項目；不得進入底層修復。")
    payload = {
        "kind": FAILURE_KIND,
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "codex_home": plan["codex_home"],
        "repair_signature": KNOWN_REPAIR_SIGNATURE,
        "source_plan_sha256": sha256_json(plan),
        "eligible_failed_ids": eligible,
        "rejected": rejected,
    }
    atomic_write_json(output.resolve(), payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="建立並記錄正式 thread 封存嘗試")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="建立待由產品正式封存工具處理的精確清單")
    plan.add_argument("--codex-home", type=Path, default=default_codex_home())
    plan.add_argument("--source-kind", choices=("all", "claude", "cursor"), default="all")
    plan.add_argument("--batch", default="latest")
    plan.add_argument("--output", type=Path, required=True)

    record = sub.add_parser("record", help="記錄一次正式 archive 呼叫結果")
    record.add_argument("--plan", type=Path, required=True)
    record.add_argument("--id", required=True)
    record.add_argument("--status", choices=("success", "failed"), required=True)
    record.add_argument("--error")
    record.add_argument("--read-success", action="store_true")

    summary = sub.add_parser("summary", help="顯示計畫目前結果")
    summary.add_argument("--plan", type=Path, required=True)

    export = sub.add_parser("export-failures", help="輸出符合已知 workaround 的失敗清單")
    export.add_argument("--plan", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    configure_console()
    args = parse_args()
    try:
        if args.command == "plan":
            payload = build_plan(args.codex_home, kind=args.source_kind, batch=args.batch)
            atomic_write_json(args.output.resolve(), payload)
            print(f"正式封存計畫：{len(payload['items'])} 筆；略過 {len(payload['skipped'])} 筆。")
            print("此步驟沒有修改 Codex 對話、資料庫或 rollout。")
            print(f"計畫：{args.output.resolve()}")
        elif args.command == "record":
            payload = record_result(
                args.plan.resolve(),
                thread_id=args.id,
                status=args.status,
                error=args.error,
                read_success=args.read_success,
            )
            print(plan_summary(payload))
        elif args.command == "summary":
            payload = validate_plan(load_json(args.plan.resolve()))
            print(plan_summary(payload))
        elif args.command == "export-failures":
            payload = export_failures(args.plan.resolve(), args.output)
            print(f"可進入受控修復：{len(payload['eligible_failed_ids'])} 筆。")
            print(f"失敗清單：{args.output.resolve()}")
        return 0
    except SafetyError as exc:
        print(f"安全停止：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
