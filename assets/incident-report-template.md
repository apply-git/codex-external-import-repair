# 外部 Agent 匯入封存事件報告

## 1. 摘要與最後狀態

- 事件日期／時區：
- Codex 版本與 Windows 版本：
- 匯入來源：Claude Code／Cursor
- Registry 總筆數／本次批次：
- 最後 active／archived／missing／mismatch：

## 2. 使用者可見症狀

- 正式封存錯誤：
- 原生 Codex thread 是否正常：
- 匯入 thread 是否可讀：

## 3. 證據鏈

| 層級 | 檢查 | 結果 |
|---|---|---|
| Registry | records、來源、imported_at、exact IDs | |
| Thread tool | 單筆／批次 archive、read_thread | |
| SQLite | rows、archived、rollout_path | |
| Files | sessions／archived_sessions、SHA-256 | |
| App list | active／archived 分頁計數 | |

## 4. 事實與推論分界

- 已驗證事實：
- 高可信推論：
- 尚未證實：

## 5. 處理方式與批准點

- Audit：
- 單筆正式封存：
- 批次正式封存：
- Failure manifest：
- Repair／rollback：
- Backup cleanup：

## 6. 測試、踩坑與修正

| 問題 | 表象 | 原因 | 防線 |
|---|---|---|---|
| | | | |

## 7. 復原界線

- 保留的備份：
- 已刪除的副本：
- 永久刪除風險：

## 8. 參考資料

- OpenAI Import from another agent
- OpenAI Build skills／Build plugins
- OpenAI Codex App Server README
- 相關官方 GitHub issues
