# 外部匯入儲存契約

只在需要解釋 audit 結果、判斷 schema drift 或規劃修復時讀取。

## 來源與 join key

`external_agent_session_imports.json` 的 `records[*].imported_thread_id` 是 registry 與 `state_5.sqlite.threads.id` 的 join key。

必要欄位：

| 欄位 | 用途 | 注意事項 |
|---|---|---|
| `source_path` | 判斷 Claude／Cursor 來源 | 預設不輸出完整路徑 |
| `content_sha256` | 來源內容指紋 | 只回報是否存在，不公開內容 |
| `imported_thread_id` | Codex thread UUID | 必須唯一且可解析 |
| `imported_at` | 匯入批次時間 | 常見為 Unix seconds |
| `source_modified_at` | 原來源修改時間 | 曾觀察為 Unix nanoseconds；依量級解析 |
| `title` | 顯示標題 | 預設不輸出 |

## SQLite 與 rollout

最低相容 schema：`threads(id, rollout_path, archived, archived_at)`。

一致狀態：

| 狀態 | DB | 實體位置 |
|---|---|---|
| Active | `archived=0` | `sessions/**/rollout-*.jsonl` |
| Archived | `archived=1` | `archived_sessions/rollout-*.jsonl` |

`rollout_path`、`archived`、`archived_at` 與實體位置必須同步變更。缺少任何一層都不是可接受的封存結果。

## session_index

不同 Codex 版本可能使用不同索引策略。本 Skill 的已知 repair 契約只支援「目標 IDs 在 `session_index.jsonl` 中零命中」的狀態。若命中，停止並重新研究當前版本；不得直接刪索引行。

## 隱私輸出

- Audit 預設只輸出 UUID、來源種類、時間、布林狀態與計數。
- 不輸出對話正文、完整來源路徑、connector 詳情或無關標題。
- 使用者要求標題時，仍只限所選批次。
