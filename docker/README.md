# Health Assistant - Docker Utilities & Cheat Sheet

This directory contains the Docker configuration for Health Assistant — the
family's reference full stack (deployment.md / ADR-0022). Docker Compose is
the **production supervisor** (honcho is dev-only): every `Procfile.dev`
process maps to a service (uvicorn → `backend`; celery worker/beat/flower →
own services; vite → served by the `frontend` image).

For full installation instructions see [docs/INSTALL.md](../docs/INSTALL.md)
and [docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md); for host-based
development use `../scripts/run-dev.sh` with the dev-db stack below.

## File map

| File | Purpose |
|---|---|
| `docker-compose.dev-db.yml` | Dev infrastructure only (Postgres + Redis) for host-based `scripts/run-dev.sh`. Creates `neuronection_health` + `neuronection_health_test` + the family roles. |
| `docker-compose.dev.yml` | Full stack built from source (hot reload, debug logging) — local testing and small staging. |
| `docker-compose.standalone.yml` | Canonical single-host self-host stack: postgres + redis + app services + **bundled nginx**, TLS-ready, `backup` sidecar behind `--profile backup`. |
| `docker-compose.prod.yml` | Production services; proxy handled externally; app ports bound to `127.0.0.1`; honors `REGISTRY`/`REPOSITORY`/`IMAGE_TAG` (and `STACK_NAME` to run a second stack side-by-side). |
| `Dockerfile` / `Dockerfile.worker` / `Dockerfile.frontend` | Backend (API + migrate + flower), worker, and SPA images. |
| `init-db.sql` | First-boot extensions bootstrap (timescaledb, pgcrypto, pg_trgm). |
| `init-roles.sh` | First-boot role bootstrap: `neuronection_health_app` runtime role + grants (+ optional `POSTGRES_TEST_DB`). |
| `nginx.conf` | HTTP-only reverse proxy (loopback / VPN use). |
| `nginx-TLS.conf` | TLS-terminating variant (certbot webroot ACME, HSTS, TLSv1.2/1.3). |
| `fhir-test-server/` | Local HAPI FHIR R4 server for offline FHIR-path testing. |
| `recreate-dev-db.sh` | Nuke + recreate the dev-db Postgres volume. |

Ops scripts (repo `scripts/`): `run-docker.sh` (dev stack),
`install.sh` / `update-docker.sh` (standalone first deploy / refresh),
`lib-docker.sh` (shared helpers), `reset-dev-db.sh` (dev volume reset),
`backup.sh` / `restore.sh` (instance backup + restore — see the
[restore drill](#backup--restore-drill) below).

## Compose project name & volumes

Every compose file pins an explicit top-level `name:` — the Compose project
name — so volume and network prefixes are deterministic no matter which
directory `docker compose` runs from. Before the pin, the project defaulted
to the compose file's directory, which scattered volumes across the
accidental `docker_*` prefix (e.g. `docker_postgres_data-dev1`).

| Compose file | Project (`name:`) | Volume prefix |
|---|---|---|
| `docker-compose.dev.yml` | `health-assistant` | `health-assistant_*` |
| `docker-compose.dev-db.yml` | `health-assistant-dev` | `health-assistant-dev_*` |
| `docker-compose.standalone.yml` | `health-assistant` | `health-assistant_*` |
| `docker-compose.prod.yml` | `${STACK_NAME:-health-assistant}` | `<stack name>_*` |
| `fhir-test-server/docker-compose.yml` | `health-assistant-fhir-test` | `health-assistant-fhir-test_*` |
| `../demo/docker-compose.demo.yml` | `${STACK_NAME:-ha-demo}` | `ha-demo_*` |

`STACK_NAME` therefore isolates a whole second prod/test stack — containers,
named volumes and the network — not just container names.

**One-time migration of legacy `docker_*` volumes:** Docker has no
`docker volume rename`, so adoption stops the legacy project (volumes are
preserved by `down`) and **copies** each volume into a correctly-labeled one
under the new prefix, using the stack's own Postgres image; the legacy copy
is removed only after a successful copy (a failed copy aborts loudly and
rolls the empty target back, so data is never silently stranded).

- **Standalone** (`install.sh` / `update-docker.sh`): automatic on the next
  run — stops the legacy stack once (only when legacy volumes exist), copies
  `docker_postgres_data` / `docker_redis_data` / `docker_uploads`, continues.
- **Prod / test CI**: automatic in `.gitea/workflows/deploy.yml` on the next
  deploy (legacy project = deploy-dir basename, e.g. `health_assistant`).
- **Demo CI**: automatic in `demo/.gitea/workflows/deploy.yml` — legacy
  volumes are *removed* instead (demo data is synthetic and re-seeded).
- **Dev workstations**: nothing runs automatically. Either wipe
  (`./scripts/reset-dev-db.sh` — dev DBs are disposable) or copy manually
  with the stack down (leave the final `docker volume rm` until you have
  verified the copy):

```bash
# dev-db volumes (docker_* → health-assistant-dev_*)
for v in postgres_data-dev1 redis_data-dev1; do
  docker volume create --label com.docker.compose.project=health-assistant-dev \
    --label com.docker.compose.volume="$v" "health-assistant-dev_$v"
  docker run --rm --entrypoint sh -v "docker_$v":/from:ro -v "health-assistant-dev_$v":/to \
    timescale/timescaledb:latest-pg16 -c 'cp -a /from/. /to/'
  docker volume rm "docker_$v"
done
# all-in-one dev stack uploads (docker_uploads → health-assistant_uploads)
docker volume create --label com.docker.compose.project=health-assistant \
  --label com.docker.compose.volume=uploads health-assistant_uploads
docker run --rm --entrypoint sh -v docker_uploads:/from:ro -v health-assistant_uploads:/to \
  timescale/timescaledb:latest-pg16 -c 'cp -a /from/. /to/'
docker volume rm docker_uploads
```

## Databases & roles (ADR-0022)

Web mode runs **PostgreSQL 16** — health keeps its TimescaleDB-optional
image (`timescale/timescaledb:latest-pg16`; the migrations degrade
gracefully when TimescaleDB features are unavailable). Naming follows
deployment.md:

| Item | Value |
|---|---|
| Database | `neuronection_health` (test: `neuronection_health_test`, demo: `neuronection_health_demo` in `../demo/`) |
| Roles | `neuronection_health_owner` (owns schema, runs migrations/DDL) + `neuronection_health_app` (runtime: CONNECT + DML only — never DDL) |
| Env vars | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `HA_DATABASE_URL` (URL wins when set). The `POSTGRES_*` parts are the infra container names shared with the compose files; the assembled/pinned URL carries the `HA_` prefix (plan 20 D2). |

**Two-role split — what exactly happens here:** `docker/init-roles.sh`
runs once, on first boot of an empty data volume, and grants the app role
DML-only privileges (plus `ALTER DEFAULT PRIVILEGES` so tables the owner
creates during migrations are covered automatically). The compose stacks
then wire **two URLs**: the shared `x-backend-env` anchor connects
backend/worker/beat as `neuronection_health_app` (least privilege), while the
one-shot `migrate` service overrides `HA_DATABASE_URL` to run
`alembic upgrade head` as `neuronection_health_owner` — the compose-level
equivalent of career/study's `SA_MIGRATIONS_DATABASE_URL` entrypoint
pattern (health runs migrations as a separate gated service instead of an
image entrypoint). Both roles share the single `POSTGRES_PASSWORD`, so the
env surface stays at the law's four vars; the split is privilege-based,
not credential-based. Consequence: the app containers hold the owner
password (the migrate service must, to run alembic), while the runtime
role can never alter the schema.

**Migrating an existing checkout (pre-2026-09 layout):** databases were
`health_assistant`/`health_assistant_test` with a single `admin` user, on
the `timescale/timescaledb:latest-pg14` image. To move an existing
instance:

1. Take a backup: `scripts/backup.sh` (works against the old stack — point
   `HA_COMPOSE_FILE` at the previous compose file if it moved).
2. Update `.env`: `POSTGRES_DB=neuronection_health` (drop any `POSTGRES_USER`
   override so the new owner role is used).
3. Recreate the volume: `docker compose --env-file .env -f docker/docker-compose.standalone.yml down -v`
   (dev: `./scripts/reset-dev-db.sh`, which also wants
   `POSTGRES_USER=neuronection_health_owner` in the root `.env`).
4. Bring the stack back up and restore: `scripts/restore.sh
   backups/health-assistant-<stamp>.tar.gz --yes`. The restore re-applies
   the runtime-role grants, so an `admin`-era dump comes back owned by
   `neuronection_health_owner` with `neuronection_health_app` DML intact.

### Renaming `neuro_*` → `neuronection_*` (ADR-0022 amendment, 2026-09-30)

The family datastore prefix was spelled out (ADR-0022 revision history):
databases `neuronection_health` (+ `neuronection_health_test` /
`neuronection_health_demo`), roles `neuronection_health_owner` /
`neuronection_health_app`. **Existing installations migrate automatically**
(guarded, one-time, no-op when the names already match):

- `scripts/install.sh` / `scripts/update-docker.sh` run
  `migrate_legacy_db_names()` before the stack boots: start postgres,
  rename a `neuro_health` (or pre-ADR `health_assistant`) database and the
  `neuro_health_*` roles.
- The prod/test and demo deploy workflows do the same mid-deploy.
- PostgreSQL refuses to rename the session's own user, so the owner role is
  renamed through a throwaway `ha_db_migrator` superuser (dropped in the
  same step) — you'll see it in the log.
- **Dev databases** are disposable: `./scripts/reset-dev-db.sh` recreates
  `neuronection_health` + `neuronection_health_test` fresh.
- An `admin`-era volume (pre role-split) has no `neuro_health_*` roles to
  rename — use the "Migrating an existing checkout" steps above.

Manual equivalent:

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d postgres
C="docker compose --env-file .env -f docker/docker-compose.standalone.yml exec -T postgres psql -d postgres -v ON_ERROR_STOP=1"
$C -U neuro_health_owner -c 'ALTER DATABASE neuro_health RENAME TO neuronection_health;'
$C -U neuro_health_owner -c 'ALTER DATABASE neuro_health_test RENAME TO neuronection_health_test;'   # if present
$C -U neuro_health_owner -c 'ALTER ROLE neuro_health_app RENAME TO neuronection_health_app;'
$C -U neuro_health_owner -c 'CREATE ROLE ha_db_migrator LOGIN SUPERUSER;'
$C -U ha_db_migrator -c 'ALTER ROLE neuro_health_owner RENAME TO neuronection_health_owner;'
$C -U neuronection_health_owner -c 'DROP ROLE ha_db_migrator;'
```

### Upgrading from pg14

PostgreSQL data directories are **not** compatible across major versions —
pointing the existing volume at `latest-pg16` refuses to start. The
supported path is dump → fresh volume → restore (the exact steps above);
`pg_dump` from a v14 client and `pg_restore` into v16 is a supported
upgrade route. The extension set (timescaledb, pgcrypto, pg_trgm) is
available on the `latest-pg16` tag, so nothing else changes.

## Dev infrastructure (host-based development)

```bash
docker compose --env-file .env -f docker/docker-compose.dev-db.yml up -d
# → Postgres (TimescaleDB, pg16) on 127.0.0.1:${POSTGRES_PORT:-5432}
#   Redis on 127.0.0.1:${REDIS_PORT:-6379}
./scripts/run-dev.sh
```

- Ports come from the root `.env` (`POSTGRES_PORT` / `REDIS_PORT`; this
  machine's family slot is 5435/6382 — see `dev/guidelines/dev-ports.md`).
  Both bind **loopback only** (audit 2026-08 CFG-M2).
- The init scripts create the `neuronection_health` database, the family roles,
  and the companion `neuronection_health_test` database (pytest refuses any DB
  not ending in `_test`; per-xdist-worker DBs follow
  `neuronection_health_test_gwN`).
- Host-based dev connects as the owner role: set
  `POSTGRES_USER=neuronection_health_owner` in the root `.env` (the runtime app
  role `neuronection_health_app` exists for the containerized flavors and for
  DML-only tooling).
- Existing volume from the `admin`/`health_assistant` era? Run
  `./scripts/reset-dev-db.sh` after updating `.env` — the old volume keeps
  its original bootstrap user.
- If you already run Redis on the host, start Postgres only:
  `docker compose --env-file .env -f docker/docker-compose.dev-db.yml up -d postgres-dev1`.
- For an existing volume missing the test DB:

  ```bash
  docker compose --env-file .env -f docker/docker-compose.dev-db.yml exec -T postgres-dev1 \
    psql -U neuronection_health_owner -d neuronection_health <<'SQL'
  SELECT 'CREATE DATABASE neuronection_health_test OWNER neuronection_health_owner'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'neuronection_health_test')\gexec
  SQL
  ```

### Local FHIR server (Stage 2 testing) — `fhir-test-server/docker-compose.yml`

```bash
docker compose -f fhir-test-server/docker-compose.yml up -d
```

A local **HAPI FHIR R4** server for offline testing of the FHIR pull path
(`fhir_search` + `fhir_observation_to_create`) against real FHIR search,
pagination, and `OperationOutcome`.

Verify it's up: `curl http://localhost:${HAPI_PORT:-8080}/fhir/metadata | head`

## Self-hosting (standalone flavor)

```bash
# 1. Secrets: generate .env (see .env.example) — at minimum
#    POSTGRES_PASSWORD, REDIS_PASSWORD, FLOWER_PASSWORD,
#    HA_SESSION_KEY, HA_REFRESH_KEY, HA_DATA_KEY.
python3 scripts/setup_env.py          # or copy .env.example → .env and edit
# 2. Images: standalone deploys pre-built images from REGISTRY/REPOSITORY/
#    IMAGE_TAG (defaults follow the release workflow's publish target).
#    For a from-source full stack use docker-compose.dev.yml instead.
# 3. Bring it up (+ scheduled backups):
docker compose --env-file .env -f docker/docker-compose.standalone.yml --profile backup up -d
# → http://localhost  (nginx routes / → SPA, /api/ → backend, /flower/ → Flower)
```

- Migrations run automatically: the one-shot `migrate` service (owner role)
  gates every app service via `service_completed_successfully` — never a
  manual step for a normal upgrade.
- Required secrets are `:?`-guarded (`POSTGRES_PASSWORD`, `REDIS_PASSWORD`,
  `HA_SESSION_KEY`, `HA_REFRESH_KEY`, `FLOWER_PASSWORD`) so `up` fails loud
  and early.
- Refresh an existing install: `scripts/update-docker.sh`
  (`--no-pull`, `--no-wait`, `-h`); first deploy helper: `scripts/install.sh`.
- Second stack on the same host (prod flavor): set `STACK_NAME` — it
  isolates the whole compose project (container names, named volumes, the
  bridge network), not just container names.

### Production flavor (`docker-compose.prod.yml`)

Same services, no bundled nginx (terminate TLS with your own proxy or use
standalone), app ports bound to `127.0.0.1` (`BACKEND_BIND`/`FLOWER_BIND`
to override — know what you are doing), resource limits on backend/worker.

## TLS

The default `nginx.conf` is HTTP-only — use it only behind a VPN or on
loopback. Health data, login credentials and bearer tokens must never cross
the network in cleartext (audit 2026-08 CFG-H2). For internet-facing
deployments:

1. Mount `nginx-TLS.conf` over `nginx.conf` (uncomment the commented
   volume + 443 lines in the compose file) and provide certs at
   `docker/certs/fullchain.pem` + `privkey.pem` (certbot webroot renewals
   answer on port 80 via `/.well-known/acme-challenge/`).
2. Set `SERVER_NAME` in the conf to your domain.
3. HSTS, TLSv1.2/1.3 and a 60 MB body cap are preconfigured.

## Backup & restore drill

**Backup is a service, not a ritual** (deployment.md): the `backup`
sidecar (standalone + prod, `--profile backup`) writes timestamped
`db-*.dump` (`pg_dump -Fc` as `neuronection_health_owner`) + `uploads-*.tar.gz`
archives into `docker/backups/` every `BACKUP_INTERVAL_HOURS`, keeping
`BACKUP_KEEP` of each. Host-side one-shots (used by the drill and for
off-machine copies) are `scripts/backup.sh` →
`backups/health-assistant-<stamp>.tar.gz` (manifest + `database.dump` +
`uploads.tar.gz`) and `scripts/restore.sh`.

**Drill — run it before you rely on it:**

```bash
# 1. Seed data — start the stack and create something you can recognize.
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d
docker compose --env-file .env -f docker/docker-compose.standalone.yml run --rm backend \
  python scripts/create_system_admin.py \
  --email drill@healthassistant.local --password 'correct-horse-battery'
docker compose --env-file .env -f docker/docker-compose.standalone.yml exec -T postgres \
  psql -U neuronection_health_owner -d neuronection_health -c 'SELECT count(*) FROM users;'

# 2. Back up.
scripts/backup.sh                       # → backups/health-assistant-<stamp>.tar.gz

# 3. Destroy the data (--profile backup also stops the backup sidecar, which
#    otherwise keeps the volumes in use).
docker compose --env-file .env -f docker/docker-compose.standalone.yml --profile backup down -v

# 4. Restore (nothing is touched without --yes / FORCE=1). Start only the
#    database first; the restore stops the backup sidecar itself so its dump
#    tick cannot race pg_restore.
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d postgres
scripts/restore.sh backups/health-assistant-<stamp>.tar.gz --yes

# 5. Verify the data is back through the API.
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d
curl -s -X POST http://localhost/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=drill@healthassistant.local&password=correct-horse-battery'   # → 200 + access_token
```

`scripts/restore.sh` prints exactly what it destroys (the `neuronection_health`
database + the `uploads` volume) and refuses to run without `--yes` (or
`FORCE=1`). It uses `pg_restore --clean --if-exists --no-owner` and then
re-applies the `neuronection_health_app` runtime grants, so restoring over a
running instance works, and so pre-split (`admin`-era) dumps come back
usable. Point `HA_COMPOSE_FILE` / `HA_COMPOSE_PROJECT` at another flavor
(prod, or a differently-named project) when needed. The demo stack
(`../demo/`) is disposable — no backup service; the scripts work against
any running stack (`POSTGRES_DB=neuronection_health_demo` there).

**Sizing the backup window:** `pg_dump -Fc` of a small-institute database
(single-digit GB with documents in the uploads volume rather than the DB)
takes seconds-to-minutes; run the sidecar at `BACKUP_INTERVAL_HOURS=24`
(off-peak) and ship `backups/*.tar.gz` off-machine — the sidecar keeps
`BACKUP_KEEP` copies locally and does **not** guard against losing the
host.

## Sizing & exposure guidance

Defaults are tuned for a small self-hosted instance (family/institute,
≤ a few dozen users):

| Knob | Default | Guidance |
|---|---|---|
| `BACKEND_MEMORY` / `BACKEND_CPUS` | 1G / 1.0 | Raise for many concurrent users; the API is mostly I/O-bound. |
| `WORKER_MEMORY` / `WORKER_CPUS` | 2G / 2.0 | The hungry one: OCR + document extraction. Raise before raising `CELERY_WORKER_CONCURRENCY` (default 2; recycle via `CELERY_MAX_TASKS_PER_CHILD=100`). |
| Redis | 256 MB, allkeys-lru | Broker + cache + OAuth state; enough for small instances. |
| Postgres | unbounded | Shares host memory; a few GB of shared_buffers goes a long way at this scale. Documents live in the `uploads` volume, not the DB. |
| `LOG_MAX_SIZE` / `LOG_MAX_FILE` | 10m / 3 | Per-container json-file rotation — total ≈ 3×10 MB × ~8 containers. |

Exposure rules (already wired, keep them when editing):

- Postgres and Redis publish **loopback-only** in every flavor. Never bind
  them to a public interface; peering happens inside the compose network.
- The standalone nginx is the only service that should publish 80/443.
  The prod flavor binds backend/frontend/flower to `127.0.0.1` — put your
  own TLS proxy in front (`BACKEND_BIND`/`FLOWER_BIND` overrides exist
  but assume you know what you are doing).
- **Flower requires basic auth in every flavor** (audit H6):
  `FLOWER_USER`/`FLOWER_PASSWORD` gate the UI itself (`--basic-auth`), and
  the standalone nginx routes `/flower/` to it. Flower shows task payloads
  and arguments — treat it as staff-only surface; on internet-facing
  deployments consider dropping the `/flower/` location entirely.
- The demo stack (`../demo/`) runs `neuronection_health_demo` on an internal,
  zero-egress network — never restore demo data into a production
  instance and vice versa.

## Docker CLI cheat sheet

```bash
docker compose --env-file .env -f docker/docker-compose.standalone.yml ps                 # stack status
docker compose --env-file .env -f docker/docker-compose.standalone.yml logs -f backend    # follow logs
docker compose --env-file .env -f docker/docker-compose.standalone.yml logs -f worker
docker compose --env-file .env -f docker/docker-compose.standalone.yml exec backend bash  # app shell
docker compose --env-file .env -f docker/docker-compose.standalone.yml exec -T postgres \
    psql -U neuronection_health_owner -d neuronection_health                            # DB shell
docker compose --env-file .env -f docker/docker-compose.standalone.yml exec -T postgres \
    pg_isready -U neuronection_health_owner -d neuronection_health                      # DB health
docker compose --env-file .env -f docker/docker-compose.standalone.yml run --rm migrate   # manual migration (owner role)
docker compose --env-file .env -f docker/docker-compose.standalone.yml up -d --profile backup   # enable scheduled backups
docker compose --env-file .env -f docker/docker-compose.dev-db.yml up -d                  # dev infra only
docker compose --env-file .env -f docker/docker-compose.dev.yml up --build                # from-source full stack
docker compose --env-file .env -f docker/docker-compose.standalone.yml down               # stop (volumes preserved)
docker compose --env-file .env -f docker/docker-compose.standalone.yml down -v            # DESTRUCTIVE: wipe volumes
```
