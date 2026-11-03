# Integration troubleshooting

Symptom → cause → fix. The developer-side diagnostics are a page of
their own for extension authors.

## Connecting and status

| Symptom | Cause | Fix |
|---|---|---|
| "Integration is not enabled by system admin." on connect | your deployment's admin disabled the integration | Ask the admin to re-enable it (**Admin → Integrations**) |
| The instance sits *pending* with an "Authorize" banner | OAuth flow not finished (or it failed) | Click the banner to re-run authorization; check the FHIR server accepts dynamic client registration |
| Instance shows `error` after a sync | upstream rejected our credentials (token expired/revoked, wrong key) | Re-authorize or re-enter the credential; the detail names the failure class |
| Sync Now says "try again in a moment" | a sync is already running for that instance (or a recent rate-limit cooldown) | Wait; it's protecting against double-writes |
| Sync says `partial`, some records dropped | malformed data failed FHIR validation before landing | See the sync detail / Debug Console; fix data shape at the source |

## Bridging (mobile / custom clients)

| Symptom | Cause | Fix |
|---|---|---|
| Client gets 401 "no X-Api-Signature header" | client didn't sign — the connection requires the machine secret | Re-pair: **Show pairing code** → reconfigure the app with the connection code |
| Client gets 401/400 "Invalid or expired request signature" | stale secret (rotated) or device clock skew | Regenerate the pairing code / sync the clock (±5-min window) |
| Pairing code shows, app rejects it | old secret — rotation invalidates previous codes | Regenerate + re-scan |
| Pushed lab report appears twice | your sender reuses no stable report id | Send the upstream's stable id as the exam `id` — re-pushes then dedup |
| Cursor stuck / full re-pull wanted | client not sending a fresh cursor, or you want history again | Use the **Reset Sync Cursor** action on the instance |

## Data surprises

| Symptom | Cause | Fix |
|---|---|---|
| Heart rate not on the biomarker list but counted | heart rate is telemetry-routed (TimescaleDB) | It merges into dashboards/trends; per-reading raw data lives in the telemetry store |
| Mood / blood-type rows show text values | categorical observations are stored as strings (`valueString`) on purpose | — |
| "AI mapping service is currently unavailable" | the AI provider used for ontology mapping isn't reachable | Check **Settings → AI**; already-cached mappings still let syncs land |

## FAQ

**Can I connect the same integration twice?** Yes, within the
integration's per-user cap — one instance per patient or per account.

**Where did the machine secret requirement come from?** Security
hardening: a URL's UUID identifies a connection but proves nothing; all
machine traffic is signed.

**Who may see my integrations?** Their instances belong to the user that
created them; data lands on the bound patient; tenant admins see the
audit trail, not your config secrets.

**Can I turn off just one alert type?** Yes — a per-kind mute rides both
each notification (inline "Turn off this kind") and the instance's
Notifications tab.
