# Contributing

Contributions are welcome when they preserve the project's narrow safety boundary.

## Before opening a pull request

1. Do not include real thread IDs, local absolute paths, conversation content, registries, SQLite databases, rollout files, or repair backups.
2. Keep audit mode read-only.
3. Preserve exact-ID allowlists and all hard-stop conditions for write modes.
4. Do not add non-interactive flags that bypass `ARCHIVE`, `ROLLBACK`, or backup-cleanup confirmation.
5. Add or update synthetic tests for behavior changes.
6. Run:

   ```powershell
   python -X utf8 scripts/self_test.py
   python -m compileall -q scripts
   ```

## Pull requests

Explain the failure signature being addressed, why existing hard stops are insufficient, and how the change remains safe when Codex storage formats drift. Use synthetic fixtures only.
