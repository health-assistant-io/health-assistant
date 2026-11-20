# Backup, restore & migration — Health Assistant (self-host)

Conforms to the family backup-migration doc template.
Cross-VM mechanics live in the canonical runbook:
[`neuronection/self-host` → docs/migration.md](https://github.com/neuronection/self-host/blob/main/docs/migration.md).

## What one backup contains

`bash scripts/backup.sh [output-dir]` produces an **encrypted**
`health-assistant-<timestamp>.tar.gz`:

- `database.dump` — `pg_dump` custom format of the Postgres database
- `uploads.tar.gz` — the uploads volume (documents, OCR artifacts)
- `manifest.json` — contents + versions (ledger row in `backup_runs.jsonl`)

Archives never contain secrets; the root `.env` is excluded on purpose.

## Off-host destination (set in the root `.env` or the shell)

`BACKUP_DESTINATION` receives every archive — a local path is
local-only (non-DR: label it as such); off-host destinations
(S3/Azure/rclone-style) make any VM replaceable. Schedule with cron —
nightly is a sane default for health data.

## Restore

```bash
bash scripts/restore.sh <archive.tar.gz> [--yes]
```

Replaces the database and the uploads volume (the script stops the
stack, restores, restarts, and waits for readiness). Encrypted archives
decrypt first with the local age identity (`AGE_IDENTITY`, default
`~/.config/age/keys.txt`).

## The data-key rule

`HA_DATA_KEY` in the root `.env` encrypts columns at rest. It is **not**
in the archive — when moving to another VM, copy the root `.env` (0600)
to the new install **before** restoring, then fix host-specific values
(`APP_URL` — drives Host validation, the setup link and CORS — plus
ports and `HA_TRUSTED_PROXY_COUNT` when behind a proxy). Full runbook:
[self-host migration guide](https://github.com/neuronection/self-host/blob/main/docs/migration.md).

## Restore drill

Once a month (and after any version change): restore the latest archive
into a scratch install (`--env-only` + old `.env` + `restore.sh`), log
in, open one real document, then delete the scratch. If the drill
fails, the backup is not a backup — health data is exactly the data you
do not want to discover that with.
