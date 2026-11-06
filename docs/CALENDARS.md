# Cloud calendars

Publish examinations and clinical events to a calendar **you** own — Google,
Microsoft 365, Nextcloud, iCloud, Fastmail, generic CalDAV, or a read-only
ICS subscription. Detail is redacted by default; nothing leaves this app
until you say so.

> Providers, scopes and destination hosts come from a shared catalog — this
> app never hand-writes a provider list or a scope string.

## What gets published

| Record | Calendar event |
|---|---|
| Examination (`examination_date`) | all-day, on that date |
| Clinical event (onset → resolution) | timed when the type is a point in time, otherwise all-day |

Every published event carries a `NEURONECTION` marker so the app can tell its
own events from a stranger's. That marker is **attribution, not detail** — it
is present at every redaction level and carries no health information.

## Redaction — the detail ladder

This is the decision that matters. Choose per calendar:

| Profile | What the calendar shows | Use when |
|---|---|---|
| **`neutral`** *(default)* | `Medical appointment` — no title, notes, location or category | You want to see *that* you are busy, not *why* |
| `none` | `Busy` — a bare time block | Maximum privacy |
| `full` *(opt-in)* | the real title, notes and location | Only if you accept sending health detail to the provider |

`full` requires an explicit confirmation — it is never enabled silently.

## Inbound: attributable-only

An event on the remote calendar becomes a record here **only** when it is
attributable:

- it carries the `NEURONECTION` marker, or
- its id was minted by this app, or
- this app already links it, or
- **you** opted in (per-calendar `acceptAll`, or you listed the categories
  yourself).

A title that merely *looks* clinical is never written automatically. It is
surfaced as an **unverified suggestion** for you to confirm or dismiss — in a
health record, a false positive is a real problem.

## Binding a calendar

Settings → Integrations → **Cloud calendars**.

1. **Bind a calendar** — pick the provider, direction, and redaction level.
2. Fill the provider fields. The form is generated from the catalog, so a new
   provider needs no app update.
3. **OAuth providers** (Google, Microsoft) open the provider's consent screen.
   Scopes are shown **verbatim** — never paraphrased. If the app later needs
   more, you are asked again; if it needs less, the removal is shown too.
4. **App-password providers** (Nextcloud, iCloud, Fastmail, CalDAV) use a
   password you create at the provider. Revoke it there to disconnect.

Use **Test** for a read-only check, **Sync now** to run a sync, and the trash
button to disconnect. Disconnecting revokes at the provider where possible,
deletes every stored credential, and removes the link. **Your clinical records
stay.**

## Where credentials live

Only in this server's sealed credential store, encrypted at rest. A binding
row stores a **pointer** and metadata — never a token. Logs, audit rows, API
responses, exports and backups exclude them.

A private ICS feed URL carries a token in its query string, so the full URL is
stored as a secret and only the address (scheme + host + path) appears in the
row or the UI.

## Status

Status values: `ok`, `degraded`, `failing`, `stale`, plus
`needs-auth` and `needs-secret` when a credential needs attention. A failing
sync stops and reports — it never retries forever.

## Privacy summary

- Detail is redacted by default (`neutral`).
- Full detail is opt-in and confirmed.
- No clinical title, note or location reaches a provider unless you enabled
  `full`.
- No PHI appears in logs, audit rows, notifications or exports.
- Two-way sync is opt-in, and inbound is attributable-only.
