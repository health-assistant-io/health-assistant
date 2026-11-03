# What you grant when you connect an integration

Connecting an integration is a trust act. Here is what you actually hand
over — and where the fences are. This page states the family consent
model as it applies to the app; the law itself lives on the extension
platform.

## What an integration can do

Every integration declares its **capabilities** — a fixed vocabulary
validated before the extension can even enable. Reading the card before
you connect tells you exactly which of these you're granting:

| Capability | Grants |
|---|---|
| `sync` | Pulls data from upstream on the instance's interval |
| `push` | Pushes local data back out to the upstream server (e.g. to a FHIR server) |
| `notify` | Sends you notifications (threshold alerts, summaries, review prompts) — you can mute per kind |
| `chat-tools` | Provides tools the AI assistant may call; each tool carries a risk class and heavier classes ride approval cards |
| `inbound-webhook` | Provisions a signed webhook endpoint (health data in, sender-authenticated) |
| `inbound-api` | Provisions a full signed two-way API (a mobile client reads and writes through it) |
| `secrets` | Stores credentials — only in encrypted form |

If a capability isn't on the card, the machinery behind it never runs for
that integration — the platform gates on the declaration, not on probing.

## Where your data goes — and never goes

- An integration instance is **bound to one patient**. Reads and writes
  top out at that patient's data — every path re-checks the binding;
  asking for someone else's rows is indistinguishable from asking for
  rows that don't exist (both answer "not found").
- Outbound HTTP from extensions always rides the platform's guarded
  transport: private/internal addresses (including cloud metadata
  endpoints) are blocked before any request leaves the process.
- A pulled document is indistinguishable from one you uploaded yourself —
  same storage, same extraction pipeline, provenance preserved on the
  row.
- The AI assistant sees integration data through the same tools you see —
  an integration can't silently grant the model a private channel.

## The secrets

- **Your credentials** (API keys, OAuth tokens, environment secrets)
  live **encrypted at rest**; they are masked as `***` in the UI and
  decrypted only at the moment the integration uses them.
- **Machine secrets** (`api_secret`, `webhook_secret`) are generated per
  connection at creation, shown **once**, and stored encrypted too. If
  one leaks (a lost laptop, a fork), rotate it — the old one dies
  immediately.
- The integration's UUID is an *identifier*, not a credential: a URL
  without a valid signature proves nothing and is rejected.

## What is *in* the product vs connected from outside

Health Assistant's bundled integrations are reviewed with the product —
no third-party code is loaded into your server. External parties (your
phone, a scraper, a browser extension) never execute code here; they
speak the published bridge protocol — signed requests, typed responses,
closed error vocabulary — over the connection you created.

## The audit trail

Writes flown through the integrations are stamped with their provenance
(source integration + upstream stable ids), land through the same
validation and RBAC as interactive edits, and dedup so a re-sync never
duplicates your history.
