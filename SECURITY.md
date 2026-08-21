# Security policy

## Supported version

Security fixes are applied to the current `main` branch.

## Reporting a vulnerability

Do not include real Codex thread IDs, source paths, conversation titles, rollout contents, database files, registry files, backups, tokens, or other personal data in a public issue.

Use GitHub's private vulnerability reporting or Security Advisory feature when available. If that channel is unavailable, open a minimal public issue describing only the affected version and a sanitized symptom, then wait for a private follow-up channel.

## Safety boundary

This project reads local Codex state and can modify it only in explicitly confirmed repair, rollback, or backup-cleanup modes. A report that proposes bypassing exact allowlists, process-closure checks, backups, hash verification, interactive confirmations, path containment, schema checks, or transactional updates will not be accepted without an equally strong replacement safeguard.
