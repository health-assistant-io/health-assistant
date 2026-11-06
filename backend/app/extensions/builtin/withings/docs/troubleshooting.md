# Troubleshooting

## The instance shows a reconnect / error state

**Cause:** the sealed credential is broken — you revoked the app's access
on the Withings side, the refresh token was rejected, the client secret
was rotated in the Withings partner console without updating the
instance, or the encryption key changed (a restored backup without its
key).

**Fix:** if the secret changed, submit the config form with the new one
(rotation reseals in place). Otherwise click **Authorize** again and
re-run the consent round-trip; the next sync resumes from the last
successful cursor.

## The instance sits in "pending" and never authorizes

Work through the layers:

1. **Client id/secret** — confirm both against the Withings partner
   console (a typo fails the exchange, not the redirect).
2. **Callback URL** — the partner app must register your deployment's
   `…/api/v1/integrations/withings/oauth/callback` address exactly.
3. **Check Connection** — reports the first broken layer (config,
   credential, Withings reachability).

## Pulls return nothing

1. **Credential revoked** — the instance would show an error state, not
   an empty success.
2. **Cursor already past the data** — the initial window (default 90
   days) may exclude older measurements. Use **Reset Cursors** to re-pull
   the full window; dedup guarantees nothing is written twice.
3. **Devices not synced** — the Withings app itself must have uploaded
   the measurements first; Health Assistant only reads what Withings
   stores (Debug mode shows the exact pull window).

## A measurement landed "raw" without a biomarker

**Cause:** its measure type has no mapping in the local catalog. This is
recorded, not dropped — the sync log names the Withings measure type.

**Fix:** define the biomarker for that measure type (the catalog
proposal flow). Future pulls map automatically; the raw rows remain as
history.

## The sync log mentions rejected measurements

Withings returned measure groups that were oversized or had the wrong
shape; they were refused before mapping and counted in the debug log. If
the rejection count grows on every sync, check the Withings support
status; the rest of the pull is unaffected.

## Syncs stop for an hour after heavy use

**Cause:** the Withings API rate-limited the pull. Health Assistant
honors the provider's retry hint: the failure is recorded with a
`next_retry_at` backoff and the next cycle resumes automatically — no
action needed.
