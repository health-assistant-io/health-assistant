# Troubleshooting

## Instance stuck on PENDING

A SMART instance stays `PENDING` until you complete the **Authorize** step. The flow:

1. Save the instance with **Authorization = SMART**.
2. Click **Authorize** on the instance detail page.
3. Pick the patient on the hospital's consent screen.
4. The callback stores the encrypted tokens and flips the instance to `ACTIVE`.

If nothing happens after Authorize, the callback likely failed — check the **Debug Console** (toggle Debug Mode on the instance) for the SMART discovery / DCR / token-exchange errors. Common causes: the server URL is wrong, or the server doesn't actually implement SMART (use **None** mode for a vanilla HAPI FHIR).

## Insufficient scope on push

Pushing returns `403 insufficient_scope` — the authorization token lacks write permissions.

**Cause:** the instance was authorized when `sync_direction` was pull-only (requesting `patient/*.read`), then switched to include push.

**Fix:** re-click **Authorize**. The new consent screen requests `patient/*.write`. The push stops on the first insufficient-scope error and surfaces an actionable message.

## 401 token races

A pull or push occasionally logs a "401 race" then succeeds on retry. This is expected: the access token was valid when checked but expired between the check and the request. Health Assistant force-refreshes and retries once automatically — no action needed. If 401s persist, the refresh token may have been revoked; re-authorize.

## No data pulled

- **Remote patient mismatch** — the configured remote patient id doesn't exist on the server, or you're querying the wrong patient. Use **Find Patient** to confirm/repick ([Selecting the Remote Patient](patient-selection.md)).
- **None mode, no patient set** — tokenless mode with no `remote_patient_id` runs unscoped; some servers reject that. Set the remote patient.
- **Cursor past the data** — the `_lastUpdated` cursor is ahead of the data you expect. Click **Reset Cursors** to re-pull the full window.
- **Record type deselected** — check **Record Types to Pull** in the config includes the resource type you expect.
- **Empty server** — verify with **Check Connection** that the server is reachable and supports the resource type; the sandbox at `r4.smarthealthit.org` resets its data periodically.

## Connection failures

- **HAPI FHIR 404s on `/.well-known/smart-configuration`** — HAPI doesn't implement SMART. Use **None** mode.
- **`http://` URL rejected** — production servers should use HTTPS. Local dev (`localhost`) may use HTTP.
- **Trailing slash / path** — the FHIR Base URL should be the root, e.g. `https://r4.smarthealthit.org` (no trailing `/fhir` unless your server requires it). Health Assistant normalizes a trailing slash.

## Documents not extracting

A pulled DocumentReference creates a document row but OCR doesn't run / extracted data is missing:

- The attachment fetch failed (server returned 404/403 for the Binary). The pull drops unreachable attachments rather than aborting; check the Debug Console for "Attachment fetch failed".
- The per-sync byte cap (50 MiB) or item cap (20 docs) was hit; excess documents are dropped with a warning until the next sync.

## Debug Console

Toggle **Debug Mode** on the instance to capture structured payloads for every step — the search URLs and params, response status codes, per-resource mapping decisions, cursor advances, and HTTP headers (Authorization redacted). The console is the fastest way to see exactly what the integration is doing. Turn it off when done to avoid log growth.
