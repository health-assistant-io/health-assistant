# Cloud calendars — implementation notes

Family law: ADR-0052 + the family cloud-connector standard. This page is the
**health host map**; the canonical catalog and engines are vendored
byte-identically from the family contract canonical and are read-only here.

## Where the code lives

| Piece | Path |
|---|---|
| Vendored catalog + engines (read-only) | `backend/app/extensions/cloud/` |
| Models | `backend/app/models/calendar_binding.py` |
| Service | `backend/app/services/calendar/` — `credentials.py`, `mapping.py`, `sync.py`, `sanitize.py`, `net.py`, `url_custody.py` |
| API | `backend/app/api/v1/endpoints/calendar.py` |
| Migration | `backend/alembic/versions/0009_calendar_bindings.py` |
| Settings surface | `frontend/src/components/integrations/CloudCalendarsCard.tsx` |
| Fixture wall | `backend/tests/cloud_conformance.py` (run by `test_cloud_contract.py`) |

## Mapping law (host-owned — the engine knows nothing about patients)

- `examination_date` → all-day, **exclusive end** (`date + 1 day`).
- Clinical events → timed when the type is a point, all-day otherwise.
- UID namespace: `health:examination:<id>`, `health:clinical-event:<id>`.
- Every projected event carries `SOURCE_CATEGORY` (`NEURONECTION`) at **all**
  redaction levels — it is attribution, never detail.
- Redaction is the only exit path (`mapping._redact`): `neutral` (default) →
  `Medical appointment`; `none` → `Busy`; `full` → real title/notes/location,
  consent-gated.

## Security invariants

- **No token-shaped column.** Credentials live in `calendar_binding_credentials`
  sealed with `nx_auth.atrest` under `calendar:<id>:oauth|password|token`.
- **Inbound is attributable-only** (`is_attributable`) — keyword lookalikes
  are unverified suggestions for the confirmation surface, never records.
- **`sanitize.py`** strips query strings/userinfo from every error detail
  before it reaches `last_error`, a log row or an API response.
- **`net.GuardedCalendarClient`** resolve-then-pins every hop, caps size, and
  types `net-denied` — a user-supplied URL is a server-side fetch primitive.
- **Bind-CSRF:** OAuth `state` is bound to the authenticated principal,
  single-use, ~10 min TTL; the callback verifies and burns it.

## Deployment: OAuth client registration (one per instance)

Health is server-first (class S), so the callback is a URL on **this**
deployment: `{APP_URL}/api/v1/calendar/oauth/callback`. Providers register
exact redirect URIs and allow no wildcards, so each self-hosted instance
registers its own client — a one-time admin task, never a per-user one:

| Provider | Client type | Redirect to register |
|---|---|---|
| Google | Web application | `<APP_URL>/api/v1/calendar/oauth/callback` |
| Microsoft | Web platform | `<APP_URL>/api/v1/calendar/oauth/callback` |

A web-application registration is a **confidential** client, so both need a
client secret. Configure the deployment once (JSON maps keyed by provider id):

```bash
CALENDAR_OAUTH_CLIENT_IDS='{"google":"…","microsoft":"…"}'
CALENDAR_OAUTH_CLIENT_SECRETS='{"google":"…","microsoft":"…"}'
```

The id is required to start a flow (a missing one is a typed `needs-config`
refusal). The secret is optional per provider and is sent **only** to the token
endpoint at exchange/refresh time — it belongs to the instance, so it is never
written into `calendar_binding_credentials`; that slot holds tokens only.
Omitting it keeps a pure-PKCE exchange for a public client.

(Desktop Assistant is the other half of the model: its loopback redirect is
identical for every installation, so the project ships **one** public client
id and every user just clicks Sign in.)

## Tests

`test_calendar_mapping.py` (redaction + marker) · `test_calendar_credentials.py`
(custody) · `test_calendar_api.py` (authz + ownership) · `test_calendar_sync.py`
(reconcile) · `test_calendar_security.py` (the A–I security findings) ·
`test_cloud_contract.py` (the vendored fixture wall).

Run against an isolated DB to avoid clobbering a dev one:
`POSTGRES_DB=<name>_test POSTGRES_PORT=5435 pytest tests/test_calendar_*.py`.

## Identity & the delta fast path (E9 / M4)

Two rules that only bite in production:

**The link row is the identity authority.** `CalendarEventLink.uid` is the
LOCAL uid (`health:examination:<id>`); the provider's
handle lives in `remote_id`/`etag`. This is load-bearing: **Graph's `iCalUId`
is server-generated and read-only**, so the provider can hand back a different
uid for an event we created. Matching by uid alone split one event into
`unlink` + `create-remote` — a duplicate every sync. The remote side is
matched by write handle, falling back to uid.

**Orphan repair.** A local record with no link may already have a remote twin
(the link write was lost in a crash between the provider write and our
commit). `reconcile` matches those by content hash and emits `LINK_EXISTING`
— attach, never create. Graph has no idempotency key, so this is what closes
the crash window there.

**A delta cursor is a probe, never the remote view.** A delta listing returns
only *changed* events. Feeding it to `reconcile` as the remote set reads every
unchanged event as "vanished" and unlinks it. The cursor is used to
short-circuit the run when the provider reports no changes; any change
(including a tombstone) falls through to the full window; a dead cursor is
dropped and the run resyncs — never a failure.

## Gotchas

- The vendored `cloud_*` modules are excluded from ruff/mypy (they are
  canonical code, byte-identical to the family contract) — matching the
  `contract.py`/`polite_http.py` precedent. Do not "fix" lint there.
- `TokenVault` methods are `seal`/`unseal`/`rotate`/`clear`.
- `TokenClient` must be memoised per binding or single-flight refresh is lost.
- Do not store the provider's uid in the link row — it is not an identity.
- Do not feed a delta result to `reconcile` as the remote view.
- Change the contract in the family canonical, then run the family sync
  tooling — never edit a vendored copy.
