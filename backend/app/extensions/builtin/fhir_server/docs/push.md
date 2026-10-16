# Push — Outbound Observations

The push sends local Health Assistant observations back to the remote FHIR server. It uses **FHIR conditional update** so the operation is idempotent — re-pushing the same row updates it in place rather than creating a duplicate.

## How it works

For each local observation eligible to push, Health Assistant issues:

```
PUT /Observation?identifier=urn:healthassistant:observation|<local-uuid>
```

with the canonical FHIR body. The result:

- The **subject** is rewritten from `Patient/<local>` to `Patient/<remote>` so the server files it under the right person.
- A stable **identifier** (`urn:healthassistant:observation|<local-uuid>`) lets the server match an existing resource and update it — created on first push, updated on every subsequent push.
- The server-assigned `id` and `meta.versionId` are dropped from the body so the server owns them.
- `412 Precondition Failed` is treated as **skipped** (no change needed).

## What gets excluded

Two filters keep the push sensible:

- **Echo exclusion** — observations originally sourced from *this* integration (performer references `Integration/{id}`) are never pushed back. This prevents a pull → push loop.
- **Standard-coding filter** — only LOINC/SNOMED-coded observations are pushed. Custom-coded biomarkers (proprietary wearable IDs) have no hospital terminology and are dropped.

## Provenance

After a successful push, Health Assistant best-effort `POST`s a FHIR **Provenance** resource to the server (targeting the just-pushed observation, agent = the HA Device, `activity = CREATE`). This satisfies hospital regulatory-audit expectations. A Provenance failure (the server doesn't support it, 404/405, network error) is logged and **never aborts the push**.

## Insufficient-scope detection

If the authorization token lacks write scope, the server returns `403 insufficient_scope`. Health Assistant detects this, **stops the push immediately**, and surfaces an actionable message: re-authorize so the SMART consent screen requests `patient/*.write` (see [Authorization & Connection](connection.md)).

## Token resilience

A 401 mid-push (the token expired between the liveness check and the PUT — a race) triggers a single force-refresh and retry. If it still fails, that row is counted as an error and the batch continues — one bad row doesn't abort the rest.

## Push resilience (cursor)

The push cursor (`last_pushed_at`) **only advances past successfully-pushed rows**. If every row failed, the cursor stays put and the next cycle retries the full window. This prevents silent data loss on transient failures.

## Manual actions

| Action | What it does |
|--------|--------------|
| **Push Now** | Runs a push immediately (bypasses `sync_direction`). Returns created / updated / skipped / errors counts. |
| **Push Preview** | A dry run — lists the observations that *would* be pushed (with their codes/values), plus the echo and coding exclusion counts, without sending anything. |
| **Reset Cursors** | Clears `last_pushed_at` so the next push re-sends the full configured window. |

## Out of scope

- **Pushing non-Observation resources** (Conditions, Encounters, etc. outbound) is intentionally not built. Hospitals rarely accept externally-written clinical data, and the Observation push covers the "share my data back" case. Build only if a concrete partner asks.
- **mTLS / Basic / API-key auth** to the server. SMART + tokenless covers the common cases; other modes are added only when a real server requires them.

## See also

- [Pull — Full Patient Record](pull.md)
- [Authorization & Connection](connection.md)
