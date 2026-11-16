# Object storage — health host map

Health keeps document bytes, generated previews/edits, anatomy figures, seed
packs, import archives and export archives behind the family object-storage
contract. The backend is deployment configuration: `local` (the
`HA_UPLOAD_DIR` volume) by default, `s3` (AWS or any S3-compatible endpoint)
or `azure_blob`. A declared cloud profile never resolves to `local`.

The database stores a server-generated **object key**, never a path. The
document API carries no filesystem location: downloads and previews go
through the authenticated route, which either streams the object or redirects
to a short-lived presigned URL.

<!-- BEGIN family-object-storage (generated; edit the family canonical) -->

## Configuration

All variables are prefixed `HA_`; nothing unprefixed is read. `.env` and
the process environment both populate the host settings, and the storage
bootstrap maps the resolved values into the contract — application code
never reads the raw environment.

| Variable | Values | Default |
|---|---|---|
| `HA_CLOUD_PROFILE` | `aws` / `azure` / `gcp` / `cloud` / empty / `none` / `local` / `self_hosted` | empty (the last three mean "no profile") |
| `HA_OBJECT_STORAGE_BACKEND` | `local` / `s3` / `azure_blob` | profile default, else `local` |
| `HA_OBJECT_STORAGE_REQUIRED` | bool | `true` with a cloud profile |
| `HA_OBJECT_STORAGE_LOCAL_ROOT` | path | host data directory |
| `HA_OBJECT_STORAGE_MAX_MB` | 1–1024 | `50` (host ceiling) |
| `HA_OBJECT_STORAGE_PRESIGN_TTL` | 60–3600 | 300 |
| `HA_S3_BUCKET` / `_REGION` / `_ENDPOINT_URL` | provider config | — / SDK default / AWS |
| `HA_S3_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY` / `_SESSION_TOKEN` | static credentials (all or none) | SDK chain |
| `HA_S3_PATH_STYLE` / `_KMS_KEY_ID` / `_PREFIX` | provider config | path-style when an endpoint is set / AWS-managed key / empty |
| `HA_S3_SSE` | `AES256` / `aws:kms` / `none` | `AES256` for cloud/AWS; `none` for a custom endpoint outside a cloud profile |
| `HA_S3_VERIFY_TLS` / `_CONNECT_TIMEOUT` / `_READ_TIMEOUT` / `_MAX_ATTEMPTS` | transport | `true` / 5 / 30 / 3 |
| `HA_AZURE_STORAGE_CONTAINER` / `_CONNECTION_STRING` / `_ACCOUNT_URL` / `_ACCOUNT_NAME` | provider config | — |
| `HA_AZURE_STORAGE_CREDENTIAL` | account key, SAS, or `managed_identity` | SDK default |
| `HA_AZURE_STORAGE_PREFIX` | object key prefix | empty |
| `HA_AZURE_CONNECT_TIMEOUT` / `_READ_TIMEOUT` / `_MAX_ATTEMPTS` | transport | 5 / 30 / 3 |

Resolution law:

- **Desktop is always local.** A desktop entrypoint with a cloud profile,
  `s3` or `azure_blob` is a boot error: there is no bucket, SDK or storage
  configuration on the desktop.
- **Cloud never runs local.** A server entrypoint with `aws`, `azure`,
  `gcp` or `cloud` never resolves to `local`; requesting `local` under any
  cloud profile is a boot error.
- **Server defaults to local.** With no profile and no requested backend,
  a server boots on the mounted local volume. `s3` / `azure_blob` is
  selected explicitly; an `azure` profile may select `s3` for an
  S3-compatible gateway.
- **Required forbids local.** `HA_OBJECT_STORAGE_REQUIRED=true` rejects
  `local` even without a cloud profile, and a cloud profile implies
  required.

The app builds and health-probes the store before it starts serving.
`probe=True` performs a cheap write/unlink under a local root,
`HeadBucket` for S3 and container properties for Azure, so a bad bucket,
container, credential or non-TLS cloud endpoint fails boot instead of the
first upload. The only sanctioned `probe=False` boot is the documented
read-only rollback window in the migration runbook.

**Upload ceiling.** Leave `HA_OBJECT_STORAGE_MAX_MB` unset unless the
deployment intentionally lowers the cap. `Health Assistant` binds unset to its
`50` MB host upload ceiling; the contract default is **100**
for hosts that do not bind one. Never pin 100 in compose to "match the
default" — an empty value is what binds the host ceiling.

## Transfer and security

**Uploads are server-mediated.** The host authenticates and authorizes
the caller, reads the body under `HA_OBJECT_STORAGE_MAX_MB`, validates
the content type and extension, sniffs the bytes, then calls
`store.put(key, data, content_type=..., metadata=...)` and persists the
key (never a path). The contract refuses presigned **PUT**: a browser
writing directly to the bucket bypasses authorization, the byte cap and
the audit trail. Do not add one later.

**Downloads have two legal shapes.** Use `store.stream(key)` through an
authenticated route (local, or a cloud backend where presigning is
unavailable), or `store.presign_get(key, ttl, download_filename=...,
inline=False)` which redirects to the provider for a short TTL.

- **GET-only presigning.** Presigned URLs are for reads; there is no
  presigned write.
- **TTL 60–3600 seconds.** The default is 300. Re-check authorization
  when the URL is minted; never log or store the URL.
- **`attachment` + `no-store` by default.** A presigned response carries
  `Content-Disposition: attachment` and `Cache-Control: no-store`.
- **Inline is an explicit image-only opt-in.** Set `inline=True` only for
  the image MIME types the host allows; never for HTML, SVG or XML.
- **`nosniff` always.** Streaming and redirected responses both carry
  `X-Content-Type-Options: nosniff`.
- **A vanished object is a 404.** A database row whose object is gone
  returns 404 when the stream opens; the ownership check still runs first.
- **Azure identity limits presigning.** A managed identity cannot sign a
  SAS; a SAS credential can access but not presign. In both cases the
  contract raises `UnsupportedOperation` and the host streams through the
  app instead of minting a broken URL.

## Cloud deployment checklist

Prerequisites:

- a **private, TLS-only** bucket or container: no public access, no
  plaintext HTTP endpoint;
- a **prefix per product and environment** (`HA_S3_PREFIX` /
  `HA_AZURE_STORAGE_PREFIX`, for example `prod/`), so one bucket can
  serve staging and production without collisions;
- a **least-privilege identity**: an instance/task role or managed
  identity over static keys, with list and object verbs only on the
  deployment prefix.

Deploy from the repository root with the production and cloud overlays:

```bash
docker compose -f docker/docker-compose.prod.yml \
               -f docker/docker-compose.cloud.yml up -d
```

**Success** means the app boots, its storage health probe passes, and an
upload followed by a download works end to end. **Failure** is a boot
error, not a degraded start: a missing bucket/container, missing or
one-sided static credentials, a non-HTTPS cloud endpoint or `SSE=none`
under a cloud profile stops the process.

- **Leave the max-MB cap unset.** An unset
  `HA_OBJECT_STORAGE_MAX_MB` binds to `Health Assistant`'s
  `50` MB host ceiling; the contract default is 100 for hosts
  that do not set one. Pin a lower value only intentionally.
- **The web image ships both extras.** Install `object-storage-s3` and
  `object-storage-azure` in the web image; desktop packaging installs
  neither. One image can therefore boot an S3 or Azure deployment.
- **An empty bucket/container is ready.** A newly provisioned, empty
  target needs no migration.
- **Existing data needs migration.** Pointing a cloud profile at an
  existing local volume without the `migrate-objects` tool serves empty
  objects. Follow the migration runbook below.
- **Do not mount the uploads volume** in a cloud deployment: the
  container is stateless for object bytes. Provider durability and
  versioning cover them; the database backup sidecar does not copy
  objects.

## Provider examples

**AWS S3 — instance/task role (recommended)**

Prerequisites: a private, TLS-only bucket in the chosen region. Grant the
instance/task role `s3:ListBucket` on the bucket and
`s3:GetObject`/`s3:PutObject`/`s3:DeleteObject` on the deployment prefix.
With a customer-managed KMS key, also grant `kms:GenerateDataKey` and
`kms:Decrypt`.

```env
HA_CLOUD_PROFILE=aws
HA_OBJECT_STORAGE_BACKEND=s3
HA_S3_BUCKET=product-prod
HA_S3_REGION=eu-central-1
HA_S3_PREFIX=prod/
# HA_S3_SSE=aws:kms
# HA_S3_KMS_KEY_ID=arn:aws:kms:...
```

No static keys belong in env when the instance/task role supplies them.

**AWS S3 — static keys + KMS**

```env
HA_CLOUD_PROFILE=aws
HA_OBJECT_STORAGE_BACKEND=s3
HA_S3_BUCKET=product-prod
HA_S3_REGION=eu-central-1
HA_S3_ACCESS_KEY_ID=...
HA_S3_SECRET_ACCESS_KEY=...
HA_S3_SSE=aws:kms
HA_S3_KMS_KEY_ID=arn:aws:kms:...
HA_S3_PREFIX=prod/
```

Prefer the role over static keys; rotate them and never commit them.

**S3-compatible endpoint (MinIO/R2/B2/Wasabi/Ceph)**

Cloud profile — the contract requires SSE `AES256` or `aws:kms`:

```env
HA_CLOUD_PROFILE=cloud
HA_OBJECT_STORAGE_BACKEND=s3
HA_S3_BUCKET=product
HA_S3_ENDPOINT_URL=https://minio.example.com
HA_S3_PATH_STYLE=true
HA_S3_ACCESS_KEY_ID=...
HA_S3_SECRET_ACCESS_KEY=...
HA_S3_SSE=AES256
HA_S3_PREFIX=prod/
```

Gateway that rejects the AWS SSE header — run it as self-host object
storage instead of a cloud profile:

```env
HA_OBJECT_STORAGE_REQUIRED=true
HA_OBJECT_STORAGE_BACKEND=s3
HA_S3_BUCKET=product
HA_S3_ENDPOINT_URL=https://minio.example.com
HA_S3_PATH_STYLE=true
HA_S3_ACCESS_KEY_ID=...
HA_S3_SECRET_ACCESS_KEY=...
HA_S3_SSE=none
HA_S3_PREFIX=prod/
```

Outside a cloud profile, a custom endpoint defaults to `SSE=none`; many
S3-compatible gateways reject the AWS SSE header. Set `AES256` only when
the gateway supports it. A cloud profile always requires
`AES256`/`aws:kms`.

**Azure Blob — managed identity (recommended)**

Prerequisites: the container exists and is private/TLS-only. The identity
needs **Storage Blob Data Contributor** on the container (or account).
`azure-identity` is in the web image. This contract presigns only with an
account key; a managed identity therefore streams downloads through the
app.

```env
HA_CLOUD_PROFILE=azure
HA_OBJECT_STORAGE_BACKEND=azure_blob
HA_AZURE_STORAGE_CONTAINER=product-data
HA_AZURE_STORAGE_ACCOUNT_URL=https://product.blob.core.windows.net
HA_AZURE_STORAGE_ACCOUNT_NAME=product
HA_AZURE_STORAGE_CREDENTIAL=managed_identity
HA_AZURE_STORAGE_PREFIX=prod/
```

Do not put a raw expiring token in env; the `managed_identity` sentinel
selects `DefaultAzureCredential` (managed or workload identity).

**Azure Blob — connection string or account key**

Connection string:

```env
HA_CLOUD_PROFILE=azure
HA_OBJECT_STORAGE_BACKEND=azure_blob
HA_AZURE_STORAGE_CONTAINER=product-data
HA_AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=product;AccountKey=...;EndpointSuffix=core.windows.net
HA_AZURE_STORAGE_PREFIX=prod/
```

Account-key alternative (no connection string):

```env
HA_CLOUD_PROFILE=azure
HA_OBJECT_STORAGE_BACKEND=azure_blob
HA_AZURE_STORAGE_CONTAINER=product-data
HA_AZURE_STORAGE_ACCOUNT_URL=https://product.blob.core.windows.net
HA_AZURE_STORAGE_ACCOUNT_NAME=product
HA_AZURE_STORAGE_CREDENTIAL=<account-key>
HA_AZURE_STORAGE_PREFIX=prod/
```

A SAS token works for access but cannot presign (downloads stream); a
connection string or account key can presign. Account keys and SAS
tokens in env are secrets; prefer managed identity. Enable Azure soft
delete and versioning.

**Azure with an S3-compatible gateway (MinIO on Azure)**

```env
HA_CLOUD_PROFILE=azure
HA_OBJECT_STORAGE_BACKEND=s3
HA_S3_BUCKET=product
HA_S3_ENDPOINT_URL=https://minio.example.internal
HA_S3_ACCESS_KEY_ID=...
HA_S3_SECRET_ACCESS_KEY=...
HA_S3_PATH_STYLE=true
HA_S3_SSE=AES256
HA_S3_PREFIX=prod/
```

An explicit `s3` backend is allowed under the `azure` profile; use this
only when you actually run an S3 gateway. The azure cloud profile
requires SSE `AES256`/`aws:kms`; if the gateway rejects SSE, use the
self-host S3-compatible block above (no cloud profile,
`HA_OBJECT_STORAGE_REQUIRED=true`, `SSE=none`).

## Migration (local → object storage)

1. Provision the bucket/container, prefix and least-privilege identity.
2. **Keep the running app on `local`; run the migration process with the
   target backend.** The app service stays `HA_OBJECT_STORAGE_BACKEND=local`
   until the flip, but the one-shot migration command is started with
   `HA_OBJECT_STORAGE_BACKEND=s3` or `azure_blob` (and that process's
   bucket/container/prefix/credential variables). `migrate-objects
   --dry-run` prints the resolved backend and bucket/container and lists
   local files, keys, unreadable/missing files and collisions. It never
   stages bytes into a local root for later import: bytes go directly to the
   bucket/container the app will use.
3. **Quiesce writes** (maintenance window) or run dual-write. Copying while
   users upload can miss files that arrive after the enumeration.
4. Run `migrate-objects --apply` (idempotent, key-addressed, resumable
   through a migration manifest). It writes each object and verifies size
   and sha256. A manifest is bound to one host, source root and target
   (backend/bucket/prefix/endpoint); a different target is refused until
   the operator passes `--rebind`, which resets transferred entries to
   `pending` and requires a fresh `--apply`/`--verify`.
5. Re-run `--apply` until the delta is **zero**, then run
   `migrate-objects --verify`, which re-reads each object and compares the
   digest (and re-hashes any source that still exists). Any `missing`,
   `unreadable`, `collision` or `failed` entry is **blocking**: the command
   exits non-zero even when other objects verified. Only entries reported
   as `skipped` (no stable owner key, regenerable) are allowed.
6. Backfill the database **before** the flip: `migrate-objects
   --backfill-db` replaces absolute paths with keys and scrubs paths stored
   inside JSON. A dual-read helper (key first, then the legacy path under
   the mounted root) covers the transition. The legacy branch works only
   while the old volume is mounted. The command refuses to run unless
   `--verify` is clean.
7. Flip `HA_OBJECT_STORAGE_BACKEND` to `s3` or `azure_blob` and drop the
   migration-only overrides from the app service.
   - **Self-host:** keep the local root read-only for one release. A
     rollback release against that volume must boot with `probe=False`
     (the local health probe writes a `.health` file and would fail on a
     read-only mount).
   - **Cloud:** there is no legacy uploads volume, so the flip is final
     once `--verify` and the backfill are clean.
8. Remove the read-only volume after a clean release and a spot check.

An empty, newly provisioned bucket/container is ready to use without
migration; existing local data needs the `migrate-objects` tool. Never
rewrite objects in place.

**Memory and limits.** The contract's `put` takes bytes, so the tool reads
one whole object at a time. Keep every object under the configured
`HA_OBJECT_STORAGE_MAX_MB` / host ceiling; an entry over the cap fails
with a clear error instead of exhausting memory. On workers, budget temp
space and memory for one object per concurrent process.

<!-- END family-object-storage -->

## Storage status card

Admin → System Settings → Storage is a read-only operator card for system
admins, not a provider wizard. It shows the resolved backend,
bucket/container, key prefix, region and endpoint host, SSE mode, per-object
cap, presign TTL and the live health row. There is deliberately no credential
field, provider tile or backend switch: storage is deployment configuration,
and a switch is a verified migration, not a save button.

- API: `GET /api/v1/admin/system/storage` (system-admin only; non-admins get
  403). It reuses the cached, bounded `StorageHealthProbe` behind `/health`.
- `?refresh=true` drops the 30 s health cache first, so **Test connection**
  performs a real probe instead of repeating a snapshot.
- The response carries no credentials, connection strings, presigned URLs or
  full endpoint URL — only the endpoint host.
- The family `app.storage` settings-map domain (scope `instance (admin)`,
  secrets `none`) is declared in `contracts/settings-map.json`; the frontend
  conformance test pins this host's system nav to that declaration.

## Migrate objects (runbook)

`backend/scripts/migrate_objects.py` copies pre-contract bytes into the
configured store and then backfills the rows that still point at the old
volume:

| Legacy location | Object key |
|---|---|
| `documents.file_path` under `HA_UPLOAD_DIR` | `tenants/<tenant>/documents/<id><ext>` |
| `<upload_dir>/anatomy_figures/<slug>[-source].<ext>` | `tenants/system/anatomy/<slug>[-source].<ext>` |
| `<upload_dir>/exports/<job-id>[.<stem>].<ext>` | the export key (archive or manifest) |
| `<upload_dir>/seed-packs/<source>/<digest>.zip` | `tenants/system/seed-packs/<source>/<digest>.zip` |

Import archives and exports without a job/tenant row are reported as skipped:
they are re-uploadable or regenerable, and no key is guessed. Anatomy figures,
exports and seed packs reuse the runtime key builders in
`app/storage/artifacts.py`; document keys use the `DocumentStorage` builder.

The running **app and workers stay on `HA_OBJECT_STORAGE_BACKEND=local`**, but
the **migration process runs with the target backend** (`s3` or `azure_blob`)
and that process's `HA_S3_*`/`HA_AZURE_STORAGE_*` values. `--dry-run` prints
the resolved backend and bucket/container. Never stage bytes into a local root
and then point the app at an empty bucket; copy directly to the target both
services will use.

```bash
cd backend

# 1. Plan: reports mapped files, missing/unreadable files and collisions.
python scripts/migrate_objects.py --dry-run

# 2. Copy; idempotent, resumable, manifest saved after every entry.
python scripts/migrate_objects.py --apply
python scripts/migrate_objects.py --apply      # re-run until the delta is zero

# 3. Re-read every object and compare size + sha256. Any blocking entry
#    (missing/unreadable/collision/failed) makes this exit 1.
python scripts/migrate_objects.py --verify

# 4. Backfill the database only after a clean verify.
python scripts/migrate_objects.py --backfill-db

# 5. Flip the API and worker storage configuration together.
```

`--backfill-db` sets `documents.object_key` (leaving `file_path` for the
durable dual-read), rewrites legacy `anatomy_figures/...` values on
`anatomy_figures.image_path` / `source_image_path`, and replaces legacy
`export_jobs.file_path` / `manifest_path` values with verified keys.
Relative legacy `scan.pdf` and `exports/...` values are handled explicitly;
an already-keyed value is preserved. It is idempotent and refuses to run
without a clean `--verify`.
Until it runs, the document facade and `ArtifactStorage` read key-first and
fall back to the legacy local location; after it runs the object-first path is
the normal path. The legacy branch works only while the old volume is mounted.
A worker deployment must have the same store configured before the flip, or
OCR/DICOM jobs will not find the objects.

The manifest defaults to
`<upload_dir>/../migrations/object-storage-manifest.json` (`--manifest PATH`
overrides it). It records each source path relative to the upload root, key,
size, sha256, status, error and the **redacted target** (backend,
bucket/container, prefix, endpoint host — no credentials), plus the host and
source root. It is written atomically (directory fsync included). A different
target, host or source root is refused; `--rebind` adopts the current target
and resets transferred entries to `pending`, requiring a fresh
`--apply`/`--verify`. A corrupt manifest aborts the run; `--force` starts a
fresh ledger. `--force` is also required when the target prefix already holds
objects the manifest does not own; dry-run only warns. Equal or nested
source/target roots are refused. Output never includes an absolute source
path, and one whole object is read per `put` (keep it under the cap).

Rollback (self-host): keep the old uploads volume for one release and flip the
API and worker back to `local`; a read-only rollback volume needs a boot with
the local health probe disabled (`probe=False`). A cloud profile has no legacy
volume, so its flip is final after `--verify` and the backfill are clean.
Health is server-only; there is no desktop build to run this on.

## Deployment profiles (AWS / Azure)

The cloud overlay requires **both** the profile and the backend, so a
deployment cannot start on an empty interpolation default that silently
means `local`:

```bash
# AWS
HA_CLOUD_PROFILE=aws HA_OBJECT_STORAGE_BACKEND=s3 \
docker compose --env-file .env -f docker/docker-compose.prod.yml \
               -f docker/docker-compose.cloud.yml up -d

# Azure
HA_CLOUD_PROFILE=azure HA_OBJECT_STORAGE_BACKEND=azure_blob \
docker compose --env-file .env -f docker/docker-compose.prod.yml \
               -f docker/docker-compose.cloud.yml up -d
```

The overlay removes the uploads volume from **both** the backend and the
worker and passes the same provider configuration to each. The worker image
installs both provider extras, so it can import the SDK and materialize a
task payload. The `backup` sidecar sees the backend and keeps PostgreSQL
dumps while skipping the uploads archive.

**AWS / PHI.** A private, TLS-only bucket with Block Public Access on,
versioning on, server access logging and a noncurrent-version lifecycle
rule (versioning alone leaves deleted PHI readable). Prefer an
instance/task role over static keys: `s3:ListBucket` on the bucket (prefix
condition), `s3:GetObject`/`s3:PutObject`/`s3:DeleteObject` on the
deployment prefix, and `kms:GenerateDataKey` + `kms:Decrypt` when
`HA_S3_SSE=aws:kms`. The SSE default is `AES256`; under a cloud profile
`SSE=none` is a boot error. Choose a region that satisfies data residency
and prefer a private/VPC endpoint.

**Azure.** One private container with soft delete and versioning. Assign
the managed/workload identity **Storage Blob Data Contributor** on the
container scope and set
`HA_AZURE_STORAGE_CREDENTIAL=managed_identity` (no raw token in env). The
identity cannot presign, so PHI downloads stream through the app with the
same short-lived download token. A connection string/account key is the
fallback and can presign.

## Boot health and observability

Boot builds and probes the store (API and every worker process) before
serving: a missing bucket/container, one-sided static credentials, a
non-HTTPS cloud endpoint or `SSE=none` raises a `StorageConfigError` and
stops the process. The successful boot logs one redacted line:

```
object storage: backend=s3 bucket/container=<bucket> prefix=prod sse=AES256
```

No access key, secret, session token, connection string or endpoint URL is
logged.

`GET /health` reports the storage row:

```json
{
  "status": "healthy",
  "database": "connected",
  "redis": "connected",
  "storage": {"backend": "s3", "status": "ok", "required": true}
}
```

- The probe is cached for 30 s, so the compose healthcheck (every 30 s)
  does not hammer the provider.
- The provider call is bounded to about 5 s (a daemon thread + join), so a
  slow SDK timeout cannot outlive the container healthcheck; a timeout is
  reported like a transient provider error (degraded, or 503 when storage
  is required). Only contract errors and `OSError` count as storage state;
  an unexpected exception surfaces as a 500 instead of a fake outage.
- A transient provider failure **degrades open**: HTTP 200 with
  `status: degraded` and `storage.status: degraded`; liveness stays green
  while the provider recovers.
- HTTP 503 (`status: unhealthy`, `storage.status: unavailable`) only when
  storage is required (a cloud profile or `HA_OBJECT_STORAGE_REQUIRED=true`)
  or the failure has been sustained for 300 s.
- `detail` carries the contract error class name only. A missing
  bucket/container is the boot error above, not a runtime 404.

## Provider smoke (release gate)

Normal PRs run `local` only; the manual provider smoke is
`.github/workflows/cloud-smoke.yml` (`workflow_dispatch`, AWS and Azure
jobs). Repository configuration: `secrets.AWS_ACCESS_KEY_ID`,
`secrets.AWS_SECRET_ACCESS_KEY`, `vars.AWS_S3_TEST_BUCKET`,
`vars.AWS_S3_TEST_REGION`, `secrets.AZURE_STORAGE_CONNECTION_STRING`,
`vars.AZURE_STORAGE_TEST_CONTAINER`.

```bash
cd health-assistant/core
AWS_PROFILE=study-test HA_TEST_PROVIDER_SMOKE=1 \
HA_OBJECT_STORAGE_BACKEND=s3 HA_S3_BUCKET=study-assistant-test \
HA_S3_REGION=eu-north-1 HA_S3_PREFIX=test/health-smoke \
uv run --directory backend --with boto3 \
  pytest -q tests/test_object_storage_provider_smoke.py -k s3

HA_TEST_PROVIDER_SMOKE=1 HA_OBJECT_STORAGE_BACKEND=azure_blob \
HA_AZURE_STORAGE_CONTAINER=study-data-tst-01 \
HA_AZURE_STORAGE_CONNECTION_STRING="$(your dotenv reader)" \
HA_AZURE_STORAGE_PREFIX=test/health-smoke \
uv run --directory backend --with azure-storage-blob \
  pytest -q tests/test_object_storage_provider_smoke.py -k azure
```

Never print or commit the connection string. The smoke runs
`run_conformance(store)` against the real backend before a release.

## Family gates

The vendored contract, its fixture table and this page's generated block are
checked by the family adoption gate with `--check --strict`: a local edit to
a vendored copy, a stale generated block or a missing host fails alignment.
Adoption is complete, so none of the three hosts reports "pending" any more.

## Host shape

| Seam | Where | Behaviour |
|---|---|---|
| App bootstrap | `app/storage/bootstrap.py`, `app/main.py` | Resolve `HA_*` settings, probe the backend, expose `app.state.objects` / `app.state.document_storage`, close on shutdown. A bad bucket, container or credential refuses boot. |
| Worker bootstrap | `app/workers/celery_app.py` | `worker_process_init` builds/probes the same store in every Celery worker process (no `app.state`); the process singleton is `app.storage.bootstrap.current_store()`. |
| Document facade | `app/storage/documents.py` | `put/read/stream/delete/materialize` plus an `anyio.to_thread` async bridge. Object first, legacy `documents.file_path` second while the local volume is mounted. |
| Artefacts | `app/storage/artifacts.py` | Key builders and dual-read helpers for exports, imports, anatomy figures and the seed-pack cache. |
| Bulk row | `documents.object_key` (nullable, indexed) | New uploads/edits/restores write it. The migration only adds the column; existing rows keep `file_path` and are read through the dual-read/worker fallback until the migration tool copies bytes and backfills keys. |

Health is a server-only product: the contract entrypoint is always `server`,
so the desktop local-only branch never applies.

## Keys

All keys are built by the contract (`tenant_key` / `join_key`), are opaque,
ASCII and server-generated. A user filename is never a key.

| Root | Key |
|---|---|
| document bytes | `tenants/<tenant>/documents/<document-id><safe-ext>` |
| export archive | `tenants/<tenant>/exports/<job-id>.zip` (or `.fhir.json`, `.catalog.json`) |
| export manifest | `tenants/<tenant>/exports/<job-id>.manifest.json` — its own object, never a sidecar file |
| import archive | `tenants/<tenant>/imports/<job-id><safe-ext>` |
| anatomy figure | `tenants/system/anatomy/<slug>[-source]<ext>` |
| seed-pack cache | `tenants/system/seed-packs/<source-id>/<digest>.zip` |

The extension is sanitized for MIME/type detection only. The facade refuses a
stored key that escapes the row's `tenants/<tenant>/documents/` prefix, so a
cross-tenant row cannot be coerced into reading another tenant's object.

## Processors and temp files

Celery/DICOM/OCR/Pillow code needs real paths. The worker materializes the
object into a private `0600` temp file for the duration of the call and
removes it on every exit path — success, provider error, partial stream:

- `ocr_document(document_id, object_key, tenant_id, user_id)` materializes
  inside the worker; the task payload is a key, never a durable path.
- DICOM metadata and PDF/DICOM preview conversion materialize for the call.
- Image edits materialize the source, render into a temporary directory,
  write the result back through the store, and remove both temps.
- Backup import materializes the tenant-scoped archive in the worker and
  deletes the object when the job finishes.
- Bulk export builds the ZIP in memory and uploads it; the manifest is a
  second object under the same prefix.

No temp file is durable storage and no temp path is sent in a task payload.
An already-queued pre-contract message that still carries an absolute path is
read as-is (rolling upgrade), but new dispatches always send keys.

## Upload cap

`MAX_UPLOAD_SIZE` (default 50 MB) is the host ceiling. When
`HA_OBJECT_STORAGE_MAX_MB` is unset the store binds to
`max(MAX_UPLOAD_SIZE, HA_EXPORT_MAX_MB)`; uploads always enforce
`min(MAX_UPLOAD_SIZE, contract)`. There are never two independent ceilings
for one upload. `HA_EXPORT_MAX_MB` (default 1024, range 1–1024) bounds an
export archive — a full backup is much larger than one upload — and the store
accepts the larger of the two. Exports are written to a private temp file,
size-checked, then uploaded; they are not buffered whole in memory twice.

## PHI policy

- Private bucket/container, no public ACLs. TLS in transit; a cloud profile
  rejects a non-https endpoint and a disabled TLS verification flag.
- Server-side encryption is mandatory under a cloud profile (`AES256` or
  `aws:kms` with a KMS key ARN). Azure uses provider-managed encryption at
  rest. Credentials come from an instance/task role, a managed identity or
  the host secret store — never the database or a URL.
- Enable bucket/container **versioning** and a lifecycle rule. A
  noncurrent-version expiry is required for erasure: deleting an object
  leaves old versions readable until they expire. Keep server access logging
  on, and put a per-tenant byte/count budget in host policy (the per-object
  cap is not a quota).
- Presigned downloads are GET-only. Health clamps **every PHI presign to at
  most 300 seconds** at the mint route, re-authorizes at that moment, records
  a `record_access` audit event, and answers `attachment` +
  `Cache-Control: no-store` + `X-Content-Type-Options: nosniff`. Only true
  attachment downloads are presigned (`inline=False`); inline PDF/text/image
  previews stream through the app so those headers are never lost. Backends
  that cannot presign stream through the authenticated route instead.
- Application-level encryption around object bytes is optional defence in
  depth. If an operator enables it, range requests/streaming must be disabled
  and every read decrypts server-side; record that choice in the deployment
  notes.

## Migration

Revision `0025_document_object_key` only adds the nullable indexed column. It
does **not** backfill keys or copy bytes: a keyed row whose object was never
copied would make the worker prefer an unreadable key. Existing rows keep
`file_path`; the facade dual-reads it and the worker falls back to the local
path when the keyed object is absent. The one-shot migration tool owns the
copy → sha verify → key backfill → flip sequence.

## Extension content caveat

Extension packages/content under `HA_UPLOAD_DIR` (`app/extensions/lifecycle.py`)
are not yet behind the object contract. A cloud deployment that installs
extensions must still mount a small data volume for that root, or wait for
the extension-content adoption. Document/export/import/anatomy bytes never
use that path.

## Backup and restore boundary

The host-side `scripts/backup.sh` archives the uploads volume and therefore
skips it under an object backend (object bytes are provider-durable — the
database dump + ledger row still happen, and encryption remains required
per the backup policy). The compose `backup` sidecar keeps taking PostgreSQL dumps
and skips the uploads archive when the profile is not local — it is a
**same-host convenience copy labelled `non-DR`**; the T1 bar is the host
timer running `scripts/backup.sh` with `BACKUP_ENCRYPTION_KEY` and an
off-host `BACKUP_DESTINATION` (`s3://` / `azure://` / rclone), recording a
`backup_runs` ledger row per run. Object durability, versioning and the
lifecycle rule cover the bytes; a separate object-backup job is out of
scope.

## Container notes

The backend and worker images install both provider extras; the local
backend needs neither. `docker-compose.cloud.yml` removes the uploads volume
from the stateless services, requires a cloud profile **and** an
object-storage backend, applies an SSE default and path-style addressing, and
leaves database backups intact. In the local compose files the worker mounts
`uploads:/app/uploads` too — a worker without the volume cannot read a local
document. Budget worker temp space as
`concurrency × max object size`, and budget **worker memory** for one archive
at a time: the contract's `put` takes bytes, so an export ZIP is written to a
private temp file (bounded by `HA_EXPORT_MAX_MB`, max 1024 MB) and read once
for the upload. A truly streaming upload would need a family contract change;
the current design deliberately keeps the 1 GiB ceiling and the one-read
memory cost.

## Tests

- `tests/test_object_storage_contract.py` — the vendored fixture wall and
  local conformance.
- `tests/test_storage_health.py` — the cached health row (healthy,
  degrade-open, required-fails, sustained, recovery), the redacted boot log
  and the missing-container boot error.
- `tests/test_storage_document_facade.py` — keys, object-first dual-read,
  `0600` materialization cleanup, tenant scoping, PHI presign clamp and the
  async off-loop bridge.
- `tests/test_storage_worker_materialization.py` — key → temp → cleanup,
  missing object and partial-stream failure leave no temp.
- `tests/test_storage_path_leak.py` — no absolute path in document model or
  response shapes.
- `tests/test_storage_migration_0025.py` — the revision is DDL-only: it adds
  the nullable indexed column and never backfills keys.
- `tests/test_storage_import_key_payload.py` — the import task receives a key.
- `tests/test_object_storage_provider_smoke.py` — opt-in real S3/Azure
  conformance (`HA_TEST_PROVIDER_SMOKE=1` plus provider env); skips cleanly.

Run the local wall with:

```bash
cd backend
uv run pytest tests/test_object_storage_contract.py tests/test_storage_*.py
```

A real-provider run is an explicit release step, never a normal-PR
dependency. The provider smoke is skipped without credentials.
