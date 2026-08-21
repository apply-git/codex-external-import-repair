# Windows extended-path 封存錯誤

只在正式 archive 回報 `os error 2`，但 registry、SQLite row 與 rollout 都存在時讀取。

## 已知特徵

本 Skill 唯一支援的底層 workaround 特徵為：

```text
正式 archive 至少失敗兩次
+ read_thread 成功
+ 最後錯誤包含 os error 2
+ rollout_path 以 \\?\ 開頭
+ 來源檔案實際存在於 sessions
```

`\\?\C:\...` 是 Windows extended-length path。檔案存在卻回報找不到時，可能是不同元件對 extended path、一般絕對路徑與 containment 檢查的 canonicalization 不一致。

這是依事件證據與相似工程案例形成的高可信推論，不是所有 Codex archive 錯誤的通用根因。錯誤特徵不同時只做 audit，不套用 repair。

## 正規化規則

- `\\?\C:\path` → `C:\path`
- `\\?\UNC\server\share` → `\\server\share`

正規化只用於檔案存在性與 containment 比較。修復前仍保存原始 `rollout_path`，讓條件式 SQL 能確認狀態沒有漂移。

## 相關公開案例

- OpenAI Codex issue #21376：external-agent import 與 thread 記錄。
- codex-plugin-cc issue #513：Windows `\\?\` path normalization 與匯入狀態。
- OpenAI Codex issue #20882：Windows path canonicalization。
- OpenAI Codex issue #33815：thread-store 路徑範圍驗證與 archive error。

Issue 是相似工程線索，不等同於產品保證。
