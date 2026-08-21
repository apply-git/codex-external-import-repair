# Codex External Import Repair

[繁體中文](README.md) | [English](README.en.md)

## 繁體中文

這是一個非官方、以安全為優先的 Codex Skill，用來稽核並處理 Claude Code 或 Cursor 匯入 Codex 後，出現無法封存、封存數量不一致，或 Windows `os error 2` 的工作階段。

它預設只讀。一般封存會優先使用 Codex 正式提供的工作階段封存介面；只有完全符合已知故障特徵的精確 thread IDs，才允許進入底層修復。

> [!WARNING]
> 底層修復會備份並修改 Codex 本機 SQLite 與 rollout 檔案。請先閱讀安全限制，且只在工具完成所有檢查、Codex／ChatGPT 已完全關閉、並再次確認後執行。

### 能做什麼

- 唯讀交叉檢查 external-agent registry、SQLite、active／archived rollout 與 session index。
- 依匯入來源與批次建立精確 thread ID 清單，不依標題猜測。
- 協助先做單筆正式封存，再處理經確認的批次。
- 只針對已知的 `os error 2` 與 Windows extended-path 特徵建立修復計畫。
- 修復前執行 schema、路徑、碰撞、SHA-256 與 SQLite 完整性檢查。
- 提供備份、修復後驗證、指定備份 rollback，以及受限的備份副本清理。

### 不會做什麼

- 不永久刪除已封存對話。
- 不把 registry 刪除當成封存方法。
- 不依標題、日期或模糊條件直接修改工作階段。
- 不處理未符合完整已知錯誤特徵的未知故障。
- 不繞過互動式 `ARCHIVE`、`ROLLBACK` 或備份清理確認。

### 系統需求

- Windows 10 或 Windows 11。
- Python 3.10 以上，不需要第三方 Python 套件。
- 可讀取目前使用者的 Codex 本機資料目錄。
- 寫入修復與 rollback 時，必須完全關閉 Codex／ChatGPT。

### 安裝

在 Codex 中請 `$skill-installer` 從本 repo 安裝：

```text
Use $skill-installer to install https://github.com/apply-git/codex-external-import-repair
```

也可以手動 clone 到使用者 Skill 目錄：

```powershell
git clone https://github.com/apply-git/codex-external-import-repair "$HOME\.agents\skills\codex-external-import-repair"
```

若 Skill 沒有立即出現，請重新啟動 Codex。

### 使用

先要求唯讀稽核：

```text
使用 $codex-external-import-repair 唯讀稽核最新一批 Claude 匯入工作階段，不要修改任何檔案。
```

完整處理順序為：

1. `audit`：只讀辨識匯入批次與狀態。
2. `archive-one`：先用正式介面測試一筆。
3. `archive-batch`：取得批次範圍確認後，用正式介面逐筆封存。
4. `repair-plan`：只有正式封存至少失敗兩次、thread 可讀，且符合已知 Windows 錯誤特徵時才建立 failure manifest。
5. `repair`：完全關閉 Codex／ChatGPT後，備份並修復 exact IDs；需要輸入 `ARCHIVE`。
6. `verify`：交叉驗證 registry、SQLite、檔案與產品清單。

詳細指令與停止條件請閱讀 [SKILL.md](SKILL.md)。儲存契約、Windows 路徑判斷及復原界線分別位於 [references/storage-contract.md](references/storage-contract.md)、[references/windows-path-canonicalization.md](references/windows-path-canonicalization.md) 與 [references/safety-and-recovery.md](references/safety-and-recovery.md)。

### 安全模型

- 預設只讀；寫入需要獨立、即時的使用者確認。
- 修復對象必須來自 failure manifest 的 exact allowlist。
- 修復前使用 SQLite backup API 建立備份，並執行 `PRAGMA quick_check`。
- 檔案移動前後以 SHA-256 驗證；資料庫更新使用條件式交易。
- 任一檢查失敗即停止；部分失敗時嘗試自動回移。
- rollback 必須指定單一備份資料夾，不會猜測或整顆覆蓋目前資料庫。

### 限制

Codex 的本機儲存格式屬於產品內部實作，未來可能改變。本 Skill 發現 registry 格式、SQLite schema、檔案位置或錯誤特徵不同時，會安全停止。`os error 2` 與 extended path 的關聯是依已觀察特徵建立的修復條件，不宣稱是所有封存錯誤的唯一根因。

本專案不是 OpenAI 官方產品，也不代表 OpenAI、Anthropic 或 Cursor。

### 開發與驗證

```powershell
python -X utf8 scripts/self_test.py
python -m compileall -q scripts
```

`self_test.py` 只在系統暫存目錄建立合成 registry、SQLite 與 rollout，測試 audit、兩次失敗記錄、repair、verify、cleanup 與 rollback。

## License

MIT. See [LICENSE](LICENSE).
