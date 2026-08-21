# Codex External Import Repair

[繁體中文](README.md) | [English](README.en.md)

An unofficial, safety-first Codex skill for auditing and handling sessions that cannot be archived, have inconsistent archive counts, or trigger Windows `os error 2` after being imported from Claude Code or Cursor.

It is read-only by default. Normal archive operations use the session archive interface officially provided by Codex first. Low-level repair is allowed only for exact thread IDs that fully match a known failure signature.

> [!WARNING]
> Low-level repair backs up and modifies Codex's local SQLite database and rollout files. Read the safety boundaries first, and run it only after the tool has completed every check, Codex/ChatGPT has been fully closed, and you have confirmed the operation again.

## What It Can Do

- Cross-check the external-agent registry, SQLite database, active/archived rollouts, and session index in read-only mode.
- Build an exact thread ID list from the import source and batch, without guessing from conversation titles.
- Help test one session through the supported archive interface before processing a confirmed batch.
- Create a repair plan only for the known `os error 2` and Windows extended-path signature.
- Validate the schema, paths, collisions, SHA-256 hashes, and SQLite integrity before repair.
- Provide backups, post-repair verification, rollback from a specified backup, and restricted cleanup of backup copies.

## What It Does Not Do

- It does not permanently delete archived conversations.
- It does not treat deleting registry entries as an archive method.
- It does not modify sessions based only on titles, dates, or fuzzy criteria.
- It does not handle unknown failures that do not match the complete known signature.
- It does not bypass the interactive `ARCHIVE`, `ROLLBACK`, or backup-cleanup confirmations.

## Requirements

- Windows 10 or Windows 11.
- Python 3.10 or later; no third-party Python packages are required.
- Read access to the current user's local Codex data directory.
- Codex/ChatGPT must be fully closed before any repair or rollback that writes data.

## Installation

In Codex, ask `$skill-installer` to install this repository:

```text
Use $skill-installer to install https://github.com/apply-git/codex-external-import-repair
```

Alternatively, clone it manually into the user's Skill directory:

```powershell
git clone https://github.com/apply-git/codex-external-import-repair "$HOME\.agents\skills\codex-external-import-repair"
```

Restart Codex if the Skill does not appear immediately.

## Usage

Start with a read-only audit:

```text
Use $codex-external-import-repair to audit the latest batch of sessions imported from Claude in read-only mode. Do not modify any files.
```

The complete workflow is:

1. `audit`: Identify the imported batch and its state in read-only mode.
2. `archive-one`: Test one session through the supported archive interface.
3. `archive-batch`: After the batch scope is confirmed, archive each session through the supported interface.
4. `repair-plan`: Create a failure manifest only after the supported archive operation has failed at least twice, the thread remains readable, and the known Windows failure signature is present.
5. `repair`: After Codex/ChatGPT is fully closed, back up and repair the exact IDs; entering `ARCHIVE` is required.
6. `verify`: Cross-check the registry, SQLite database, files, and product list.

See [SKILL.md](SKILL.md) for detailed commands and stop conditions. The storage contract, Windows path rules, and recovery boundaries are documented in [references/storage-contract.md](references/storage-contract.md), [references/windows-path-canonicalization.md](references/windows-path-canonicalization.md), and [references/safety-and-recovery.md](references/safety-and-recovery.md).

## Safety Model

- Read-only by default; every write requires separate, current user confirmation.
- Repair targets must come from the failure manifest's exact allowlist.
- Before repair, the tool creates a backup through the SQLite backup API and runs `PRAGMA quick_check`.
- SHA-256 verifies files before and after they are moved; database updates use conditional transactions.
- Any failed check stops the operation. If a partial failure occurs, the tool attempts to move files back automatically.
- Rollback requires one specific backup directory. It never guesses or overwrites the entire current database.

## Limitations

Codex's local storage format is an internal product implementation and may change. This Skill stops safely if it encounters a different registry format, SQLite schema, file location, or failure signature. The relationship between `os error 2` and extended paths is a repair condition based on observed behavior; it is not claimed to be the only cause of all archive failures.

This project is not an official OpenAI product and is not affiliated with or endorsed by OpenAI, Anthropic, or Cursor.

## Development and Verification

```powershell
python -X utf8 scripts/self_test.py
python -m compileall -q scripts
```

`self_test.py` creates only synthetic registry, SQLite, and rollout data inside the system temporary directory. It tests audit, recording two failures, repair, verification, cleanup, and rollback.

## License

MIT. See [LICENSE](LICENSE).
