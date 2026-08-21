# 安全、備份與復原契約

在 repair、rollback 或 cleanup-backup 前讀取。

## Repair 不變量

1. 目標是 failure manifest 中的 exact IDs，不用前綴或日期模糊匹配。
2. Codex／ChatGPT 完全關閉。
3. Registry、DB row、rollout 三方存在，DB schema 與 session_index 符合已知契約。
4. 來源位於 `sessions`，目的位於 `archived_sessions`，目的檔不存在。
5. 搬移前後 SHA-256 相同。
6. SQLite 使用 `BEGIN IMMEDIATE` 與條件式單列 update。
7. 任何例外都 rollback DB，並把已搬檔案逐一移回。

## 備份內容

修復前建立：

- `state_5.before-repair.sqlite`（SQLite backup API）
- `external_agent_session_imports.json`
- `session_index.jsonl`
- `archive-failures.json`
- `manifest.json`
- `rollouts/` exact 內容副本

成功後另有 `repair-result.json`；rollback 前另建 `state_5.before-rollback.sqlite`。

## Rollback

- 必須指定 exact backup folder。
- 只恢復 manifest 中 rows 與 rollout；不整顆覆蓋目前 DB。
- 目前 archived rollout 的 SHA-256 必須仍與 manifest 相同。
- 成功後寫 `rollback-result.json`，同一備份不得重複 rollback。

## 備份清理

只允許逐檔刪除 `rollouts/` 中、manifest 列出且 SHA-256 相符的副本。清理前先證明目前 archived 內容仍完整。

保留 metadata 與 SQLite 備份。不要用遞迴刪除命令，不要刪整個 backup folder。

刪除 rollout 副本後，若再永久刪除已封存對話，repair backup 無法提供內容還原。外部 Agent 原始資料是否仍存在，需另外確認。
