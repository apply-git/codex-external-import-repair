#!/usr/bin/env python3
"""Shared, standard-library-only helpers for external import archive tooling."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
KNOWN_REPAIR_SIGNATURE = "windows_extended_path_archive_os_error_2"
REQUIRED_THREAD_COLUMNS = {"id", "rollout_path", "archived", "archived_at"}


class SafetyError(RuntimeError):
    """A deliberate fail-closed stop."""


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def default_codex_home() -> Path:
    return Path.home() / ".codex"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def normalize_db_path(value: str) -> Path:
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def has_extended_prefix(value: str) -> bool:
    return value.startswith("\\\\?\\")


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafetyError(f"無法安全讀取 JSON：{path}：{exc}") from exc


def validate_uuid(value: str) -> str:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise SafetyError(f"thread ID 不是有效 UUID：{value}") from exc
    return value


def source_kind(source_path: str) -> str:
    lowered = source_path.lower().replace("/", "\\")
    if "\\.claude\\" in lowered or lowered.endswith("\\.claude"):
        return "claude"
    if "\\cursor\\" in lowered or "\\.cursor\\" in lowered:
        return "cursor"
    return "unknown"


def timestamp_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = float(value)
        if abs(number) >= 1e17:
            number /= 1e9
        elif abs(number) >= 1e14:
            number /= 1e6
        elif abs(number) >= 1e11:
            number /= 1e3
        return datetime.fromtimestamp(number, tz=timezone.utc).astimezone().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def timestamp_sort_value(value: Any) -> float:
    try:
        number = float(value)
        if abs(number) >= 1e17:
            number /= 1e9
        elif abs(number) >= 1e14:
            number /= 1e6
        elif abs(number) >= 1e11:
            number /= 1e3
        return number
    except (TypeError, ValueError):
        return float("-inf")


def load_registry(codex_home: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    path = codex_home / "external_agent_session_imports.json"
    payload = load_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise SafetyError("匯入登錄缺少 records 陣列，已停止。")
    by_id: dict[str, dict[str, Any]] = {}
    for item in payload["records"]:
        if not isinstance(item, dict):
            continue
        thread_id = item.get("imported_thread_id")
        if not thread_id:
            continue
        validate_uuid(str(thread_id))
        if thread_id in by_id:
            raise SafetyError(f"匯入登錄出現重複 thread ID：{thread_id}")
        by_id[str(thread_id)] = item
    return payload, by_id


@contextmanager
def sqlite_ro(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.is_file():
        raise SafetyError(f"找不到必要資料庫：{path}")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def verify_sqlite(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if not result or result[0] != "ok":
        raise SafetyError("SQLite PRAGMA quick_check 未通過，已停止。")
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "threads" not in tables:
        raise SafetyError("SQLite schema 缺少 threads table，已停止。")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
    missing = REQUIRED_THREAD_COLUMNS - columns
    if missing:
        raise SafetyError(f"SQLite threads schema 已改變，缺少：{', '.join(sorted(missing))}")


def query_thread_rows(connection: sqlite3.Connection, ids: Iterable[str]) -> dict[str, sqlite3.Row]:
    target = tuple(dict.fromkeys(str(x) for x in ids))
    if not target:
        return {}
    placeholders = ",".join("?" for _ in target)
    rows = connection.execute(
        "SELECT id, rollout_path, archived, archived_at "
        f"FROM threads WHERE id IN ({placeholders})",
        target,
    ).fetchall()
    return {str(row["id"]): row for row in rows}


def session_index_hits(codex_home: Path, ids: Iterable[str], *, require_file: bool) -> list[str]:
    path = codex_home / "session_index.jsonl"
    if not path.is_file():
        if require_file:
            raise SafetyError("找不到 session_index.jsonl，無法證明修復契約，已停止。")
        return []
    target = tuple(dict.fromkeys(str(x) for x in ids))
    hits: set[str] = set()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            for thread_id in target:
                if thread_id in line:
                    hits.add(thread_id)
    return sorted(hits)


def running_codex_processes() -> list[str]:
    if os.name != "nt":
        return []
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ["無法確認 Codex 是否已關閉"]
    names: list[str] = []
    for row in csv.reader(result.stdout.splitlines()):
        if not row:
            continue
        name = row[0].strip()
        lowered = name.lower()
        if lowered in {"codex.exe", "chatgpt.exe"} or lowered.startswith("openai.codex"):
            names.append(name)
    return sorted(set(names), key=str.lower)


def require_codex_closed() -> None:
    names = running_codex_processes()
    if names:
        raise SafetyError(
            "仍偵測到 Codex／ChatGPT 程序。請完全關閉後再執行寫入模式；"
            "本工具不會與桌面程式同時修改資料。"
        )


def extract_ids(payload: Any) -> list[str]:
    values: list[str] = []
    if isinstance(payload, list):
        values = [str(x) for x in payload]
    elif isinstance(payload, dict):
        for key in ("eligible_failed_ids", "failed_ids", "target_ids", "ids"):
            if isinstance(payload.get(key), list):
                values = [str(x) for x in payload[key]]
                break
        if not values and isinstance(payload.get("items"), list):
            for item in payload["items"]:
                if isinstance(item, dict) and item.get("id"):
                    values.append(str(item["id"]))
        if not values and isinstance(payload.get("records"), list):
            for item in payload["records"]:
                if isinstance(item, dict) and item.get("imported_thread_id"):
                    values.append(str(item["imported_thread_id"]))
    values = list(dict.fromkeys(values))
    for value in values:
        validate_uuid(value)
    return values


def selected_registry_records(
    by_id: dict[str, dict[str, Any]],
    *,
    kind: str = "all",
    batch: str = "all",
) -> dict[str, dict[str, Any]]:
    chosen = {
        thread_id: record
        for thread_id, record in by_id.items()
        if kind == "all" or source_kind(str(record.get("source_path", ""))) == kind
    }
    if batch == "all":
        return chosen
    if batch == "latest":
        if not chosen:
            return {}
        latest_raw = max(
            (record.get("imported_at") for record in chosen.values()),
            key=timestamp_sort_value,
        )
        return {k: v for k, v in chosen.items() if v.get("imported_at") == latest_raw}
    return {
        k: v
        for k, v in chosen.items()
        if str(v.get("imported_at")) == batch or timestamp_to_iso(v.get("imported_at")) == batch
    }


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
