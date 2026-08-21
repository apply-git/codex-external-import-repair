---
name: codex-external-import-repair
description: 稽核並安全處理 Claude Code 或 Cursor 匯入 Codex 後無法封存、封存數量不一致或出現 Windows os error 2 的工作階段。預設唯讀；不適用於一般對話整理、永久刪除或沒有 external-agent import 證據的問題。
---

# Codex 外部匯入封存修復

把「來源辨識、正式封存、已知 Windows 路徑錯誤修復、驗證與備份清理」分成不同批准階段。優先使用產品正式 thread archive 介面；只有完全符合已知錯誤特徵的 exact IDs 才能進入底層修復。

## 預設行為

沒有明確寫入要求時，只執行 `audit`：

```text
python scripts/audit_external_imports.py --source-kind claude --batch latest
```

- 不顯示 title 或完整 source_path，除非使用者明確要求。
- 不修改 registry、SQLite、session_index、rollout 或 Codex 清單。
- 先回報選取筆數、active／archived／missing／mismatch 計數及 imported_at 批次。

需要理解欄位與儲存關係時，讀 [references/storage-contract.md](references/storage-contract.md)。

## 模式選擇

### `audit`

讀取 `external_agent_session_imports.json`、`state_5.sqlite`、`sessions`、`archived_sessions` 與 `session_index.jsonl`，交叉驗證 exact IDs。

若使用者要保存 JSON，才加 `--output <使用者指定路徑>`。`--include-titles` 需要另外明確授權。

### `archive-one`

先由 `archive_imported_threads.py plan` 建立精確清單，再挑一筆用目前產品提供的 thread archive 工具封存；不可直接改 SQLite。

1. 搜尋並使用 host 提供的 `set_thread_archived`、`list_archived_threads`、`read_thread` 等同等 thread 工具。
2. 單筆成功後，從 archived 清單與 `verify_archive_state.py` 同時驗證。
3. 工具不可用時停止並回報；不要自行猜 app-server endpoint。

### `archive-batch`

取得使用者對批次範圍的確認後，才逐筆呼叫正式 archive。使用計畫檔記錄每次結果：

```text
python scripts/archive_imported_threads.py plan --source-kind claude --batch latest --output <plan.json>
python scripts/archive_imported_threads.py record --plan <plan.json> --id <UUID> --status success
```

若一筆失敗：

1. 記錄第一次 failed 與完整錯誤摘要。
2. `read_thread` 證明 thread 可讀。
3. 再做一次正式 archive。
4. 第二次仍失敗才用 `--read-success` 記錄。

不要把 API 回覆當唯一驗收；完成後重新列 archived 清單並執行 `verify`。

### `repair-plan`

只有同時符合以下條件才輸出 failure manifest：

- 正式 archive 至少失敗兩次。
- `read_thread` 成功。
- 最後錯誤含 `os error 2`。
- DB `rollout_path` 帶 `\\?\` extended-path 前綴。
- registry、threads row 與 rollout 三方都存在。

```text
python scripts/archive_imported_threads.py export-failures --plan <plan.json> --output <failures.json>
python scripts/repair_orphaned_archives.py check --failure-manifest <failures.json>
```

若任何條件不符，停止底層修復。讀 [references/windows-path-canonicalization.md](references/windows-path-canonicalization.md) 判斷已知特徵；不要把推論寫成產品已證實的唯一根因。

### `repair`

這是高風險寫入模式。必須在操作前重新向使用者確認，並要求完全關閉 Codex／ChatGPT：

```text
python scripts/repair_orphaned_archives.py repair --failure-manifest <failures.json>
```

腳本必須完成：exact allowlist、schema 檢查、`PRAGMA quick_check`、路徑 containment、目的碰撞、SHA-256、SQLite backup API、`BEGIN IMMEDIATE`、`os.replace`、條件式 row update、失敗自動回移與修復後驗證。

不可繞過 `ARCHIVE` 確認字串，不可新增非互動強制參數。

### `verify`

```text
python scripts/verify_archive_state.py --ids-file <plan-or-manifest.json>
```

驗收四層：

1. registry 仍含 exact IDs。
2. SQLite `archived=1` 且 `rollout_path` 指向 archived 區。
3. rollout 僅在 `archived_sessions` 且內容存在。
4. Codex active／archived 清單計數一致；必要時重啟後再查。

側欄殘影不是失敗證據；以 thread 內容狀態、正式清單、DB 與實體檔案交叉判定。

### `rollback`

指定單一備份資料夾，不使用「猜最近一份」：

```text
python scripts/repair_orphaned_archives.py rollback --backup <backup-folder>
```

操作前另外確認並要求 Codex／ChatGPT 關閉。Rollback 只恢復 manifest 中的 exact rows 與 rollout，不整顆覆蓋現有資料庫。

### `cleanup-backup`

清理備份與修復是兩個不同批准動作。先唯讀檢查：

```text
python scripts/cleanup_repair_backup.py check --backup <backup-folder>
```

只有使用者明確要求刪除 rollout 副本後，才執行：

```text
python scripts/cleanup_repair_backup.py delete-rollout-copies --backup <backup-folder>
```

只可逐檔刪除 manifest 列出的副本；保留 manifest、repair-result、registry、session_index 與 SQLite 備份。不可遞迴刪除整個備份資料夾。讀 [references/safety-and-recovery.md](references/safety-and-recovery.md) 確認復原界線。

## 硬性停止條件

- Registry 格式、threads schema 或檔案位置與契約不同。
- exact ID 不在所選外部匯入批次，或來源不是 Claude／Cursor。
- `session_index.jsonl` 命中待修復 IDs。
- 目的檔碰撞、SHA-256 不符、rollout 不存在或路徑越界。
- Codex／ChatGPT 在寫入模式仍執行。
- 正式 archive 尚未做單筆測試或不足兩次已記錄失敗。
- 錯誤不是已知 `os error 2 + \\?\` 特徵。
- 使用者要求的是永久刪除已封存對話；這是另一個不可逆流程，不由本 Skill 執行。

## 驗證腳本

修改本 Skill 後執行：

```text
python scripts/self_test.py
python <skill-creator>/scripts/quick_validate.py <本 Skill 路徑>
```

`self_test.py` 只在系統暫存目錄建立合成 registry、SQLite 與 rollout，完整測試 audit → 兩次失敗記錄 → repair → verify → cleanup → rollback。

需要產出事件報告時，以 [assets/incident-report-template.md](assets/incident-report-template.md) 為內容骨架，不要把模板當成操作授權。
