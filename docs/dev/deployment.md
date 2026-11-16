# Deployment Guide

Self-hosting Health Assistant (T1 — one VM, cloud or on-prem; TimescaleDB
inside). The canonical stack is the production compose file: postgres +
redis + backend + worker + beat + frontend (+ nginx on the standalone
flavor), each in its own container with its own restart policy and
healthcheck.

The stack implements the family deployment contract (the deployment contract / the deployment contract
§3–§5, the backup policy, the credential policy): one-shot `migrate` + provisioning, split
owner/app DB credentials, `/health` + `/ready` probes, derived cookie
`Secure`, Flower behind an opt-in profile, and encrypted off-host-capable
backups with a run ledger.

> Health is **T1-only** (the deployment contract §7): TimescaleDB backs the supported
> analytics; no managed DB, no managed containers. Vanilla PostgreSQL
> degrades trends/anomalies to empty instead of failing.

---

## Quick start (Docker Compose)

```bash
git clone https://github.com/neuronection/health-assistant.git
cd health-assistant/core

./scripts/install.sh            # generates .env (all secrets), pulls images, boots
# or manually:
cp .env.example .env            # set every REQUIRED secret (see below)
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d
```

- `docker/docker-compose.standalone.yml` — canonical single-host stack with
  the bundled nginx (HTTP + TLS variant via `docker/nginx-TLS.conf`).
- `docker/docker-compose.prod.yml` — the same services without nginx, for
  BYO reverse proxies.

### Credential split (the credential policy)

Two DB roles, two distinct secrets:

| Role | Used by | Env |
|---|---|---|
| **owner** (DDL) | the one-shot `migrate` service, host-side `scripts/backup.sh` / `restore.sh` | `POSTGRES_PASSWORD` (the image bootstrap secret) → `HA_MIGRATIONS_DATABASE_URL` |
| **app** (DML only) | `backend`, `worker`, `beat` | `HA_DB_PASSWORD` → `HA_DATABASE_URL` |

The serving containers never receive the owner DSN or password, so a leaked
app container cannot `CREATE` or `DROP` anything. The db init
(`docker/init-roles.sh`) refuses a shared secret; the `migrate` job re-verifies
the split (`scripts/provision_db.py post` checks the app role holds no
SUPERUSER/CREATEROLE/CREATEDB and no CREATE on schema public, and fails the
deploy loudly otherwise).

The the credential policy, three states, enforced where DDL happens (alembic `env.py`):

| State | Config | Behaviour |
|---|---|---|
| legacy | no migrations URL, no split marker | allowed with a loud warning; not managed-ready |
| split | `HA_MIGRATIONS_DATABASE_URL` + `HA_DB_SPLIT=1` (what the compose files set) | supported |
| broken | `HA_DB_SPLIT=1` without the migrations URL | refuses to migrate |

### Migrations + provisioning (one-shot)

`migrate` runs once per deploy as the **owner**, in order:

1. `scripts/provision_db.py pre` — extensions (`timescaledb`
   availability-checked, `pgcrypto`, `pg_trgm`), app role + grants +
   `ALTER DEFAULT PRIVILEGES`, the `backup_runs` ledger table;
2. `alembic upgrade head` (ends by publishing the reached head into the
   app-readable `migration_state` marker that `/ready` reads);
3. `scripts/setup_checkpoints.py` — the LangGraph checkpoint tables (owner
   DDL; the runtime role is DML-only);
4. `scripts/provision_db.py post` — default-privilege re-assert + app-role
   privilege verification.

`backend`/`worker`/`beat` start only after `migrate` completed successfully
(`condition: service_completed_successfully`) and never run DDL. Re-run
manually with `docker compose … run --rm migrate`. Legacy installs that
predate the split keep booting with a loud warning (the credential policy, three states):
add `HA_DB_PASSWORD` (distinct) to your `.env` to move to the supported
split state.

### Readiness vs liveness

- `GET /health` — liveness: 200 unless required object storage fails;
  Redis/storage degradation rides the payload, never the status code.
- `GET /ready` — readiness: 200 only after boot completed, the database is
  reachable **at this release's Alembic head**, and **required** object
  storage answers. Anything else is `503` so a proxy/monitor pulls a
  half-migrated or still-booting instance out of rotation.
  Soft signals (Redis, Celery workers, queue backlog) are reported in the
  payload and never flip the status. Negative tests pin this:
  `backend/tests/test_ready_endpoint.py`.

  How "at head" is checked: `0016_rls` revokes `alembic_version` from the
  runtime role (the app must never read the version row), so every Alembic
  run ends by publishing the reached head into `migration_state` — a
  single-row, app-readable marker (`alembic/env.py`). `/ready` compares
  that marker with the head compiled into the release; a fresh database
  without a migrate yet, a stale migrate, or an unreachable DB all fail
  the check.

Both routes are exposed through the bundled nginx.

### Cookies (the deployment contract §1.6)

Cookie `Secure` is **derived, never static**: `APP_URL` served over
`https://`, or the explicit `HA_TLS=true` flag, makes auth cookies
`Secure` (+ `__Host-` prefix). LAN/VPN deployments on plain HTTP derive
`Secure=false` and login keeps working. Only set `HA_COOKIE_SECURE`
explicitly to override the derivation.

### Flower — opt-in (the deployment contract §1.7)

Flower (`--profile flower`) is off by default; it exposes task payloads
(health data) and is an operator tool. The bundled nginx resolves the
`flower` upstream lazily (Docker's embedded DNS + a variable `proxy_pass`),
so profiling Flower off never breaks nginx startup — `/flower/` simply
502s while disabled. Enable with:

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml --profile flower up -d
```

Flower requires `FLOWER_USER`/`FLOWER_PASSWORD` (basic auth) in every
flavor.

---

## Backups (the backup policy)

- **T1 (cloud VM):** off-host is REQUIRED. Run the host-side
  `scripts/backup.sh` from a host timer (systemd `OnCalendar` or cron —
  snippet at the top of the script). It dumps the database, archives the
  uploads volume (when local storage is active), **encrypts** (health/PHI:
  `encrypted: required` — set `BACKUP_ENCRYPTION_KEY`, or
  `BACKUP_AGE_RECIPIENT` with `age`), uploads **off-host**
  (`BACKUP_DESTINATION=s3://…` / `azure://…` / an rclone remote; tools
  `aws`/`azcopy`/`rclone` auto-detected), and writes a **`backup_runs`
  ledger row** (started/finished/status/bytes/destination/checksum/
  error_class). A stale ledger row (> 2× interval) must raise your external
  alert.
- **Same-host dumps are NOT disaster recovery.** The compose `backup`
  sidecar (`--profile backup`) writes to the same host's disk — it is a
  local convenience and deployments relying on it must be labelled
  `non-DR`. On-prem with local FS only may be labelled `non-DR`; T1 cloud
  may not.
- Keep the encryption key in the operator's key ring — never next to the
  backups it protects.
- Restore: `scripts/restore.sh` (see `docker/README.md` → *Backup &
  restore drill*). The throwaway-target restore design (throwaway target first, Timescale
  pre/post, app-role re-grant, RLS-aware verification) supersedes it; until
  then treat restores as a drill procedure, not a one-liner.

---

## On-prem parity

The same compose files, `.env` surface, probes and backup script run on an
on-prem VM — the differences are the host, the object-store backend
(`HA_OBJECT_STORAGE_BACKEND=local`, or MinIO via the S3 endpoint vars) and
TLS material (internal CA allowed). No cloud-only code path.

## Operations

- `scripts/update-docker.sh` — pull + migrate + recreate (the update path
  without a terminal).
- First boot: the setup wizard provisions the first admin
  (`SETUP_BOOTSTRAP_TOKEN` must be set — a template fails without it).
- Support tiers: this compose path is **best-effort** for health today
  (see `docs/deployment-tiers.json`); cloud-VM and Terraform paths are
  **community-supported**. Claims live in that file, not in README prose.
