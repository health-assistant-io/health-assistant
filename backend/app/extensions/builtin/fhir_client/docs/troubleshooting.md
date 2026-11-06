# Troubleshooting

## The instance shows a reconnect / error state

**Cause:** the sealed credential is revoked, expired server-side, or the
encryption key changed (a restored backup without its key).

**Fix:** open the instance, submit the config form with a fresh token
from the portal. Rotation reseals the credential in place and the next
sync resumes from the last successful cursors.

## The sync fails with "no remote_patient_id"

**Cause:** the patient binding is missing — this connector never pulls
unscoped.

**Fix:** edit the instance and set the remote FHIR patient id (see
[Connection](connection.md)).

## Check Connection reports "patient binding not found"

**Cause:** the bound id does not exist on the server (typo, wrong
environment, or the portal re-assigned ids).

**Fix:** confirm the id in the portal's FHIR viewer and update the
instance.

## Pulls return nothing

Work through the layers:

1. **Credential revoked** — the instance would show an error state, not
   an empty success.
2. **Cursor already past the data** — the initial window (default 12
   months) may exclude older records. Use **Reset Cursors** to re-pull
   the full window; dedup guarantees nothing is written twice.
3. **Server filters** — confirm the same search with `patient=<id>` and
   `_lastUpdated=gt<cursor>` returns data in the portal's FHIR viewer
   (Debug mode shows the exact URL and params).

## A lab result landed "raw" without a biomarker

**Cause:** its LOINC code has no mapping in the local catalog. This is
recorded, not dropped — the sync log lists the code.

**Fix:** propose/define the biomarker for that code (the catalog
proposal flow). Future pulls map automatically; the raw rows remain as
history.

## The sync log mentions rejected resources

The server returned resources that were oversized or had the wrong shape;
they were refused before mapping and counted in the debug log. If the
rejection count grows on every sync, check the portal for malformed
records; the rest of the pull is unaffected.
