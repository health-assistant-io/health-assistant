# Connecting an integration

Integrations connect Health Assistant to your data sources: wearables,
hospital FHIR servers, custom webhook senders, your own mobile or backend
clients. Every integration is reviewed with the product and ships inside
Health Assistant — nothing third-party is loaded into your server.

## The catalog

**Settings → Integrations** lists what's available. Five integrations
ship today:

| Integration | What it does |
|---|---|
| Developer Dummy Data | A reference integration that generates sample data — useful to preview the whole flow |
| Health Assistant Bridge | A signed two-way connection for your mobile app or custom client |
| FHIR Server (Hospital Sync) | Connects to a hospital EHR via OAuth2 (SMART-on-FHIR) or a tokenless local FHIR server |
| MCP Client | Exposes an external tool server's tools to the AI assistant |
| Universal Webhook | Receives signed webhooks from any sender you control |

## Connecting — the flow

1. **Open the integration** from the catalog and read its documentation
   (each card carries a Documentation dialog with setup guidance).
2. **Create an instance** — a connection bound to one patient. A user
   with several patients (or several wearable accounts) creates one
   instance each; per-user limits apply per integration.
3. **Fill the config form** — what it asks depends on the integration:
   an instance name, a sync interval, which metrics to generate, or only
   a server URL for OAuth integrations.
4. **Authorize** (OAuth integrations only) — the instance sits in
   *pending* with an "Authorize" banner until the server's OAuth
   round-trip finishes with the upstream.
5. **Sync** — "Sync now" pulls immediately; the background cycle
   thereafter follows each instance's own interval.

## Machine secrets (bridges and webhooks)

Connections that *receive* data from machines (the Bridge, webhooks) are
provisioned with an HMAC signing secret at creation. Health Assistant
shows it **exactly once** — in the success box right after you create the
instance. Store it in your client immediately:

- it never appears in plain text again (the UI shows `***`),
- every machine request must be signed with it — unsigned traffic is
  rejected,
- the **Show pairing code** action gives the whole connection string
  (`base_url | integration_id | api_secret`) for the mobile app's
  scanner; regenerating rotates the secret immediately, which signs out
  previously paired devices.

## After connecting

- The **instance detail** page shows status, last sync, and the custom
  actions the integration provides (Show Status, Reset Sync Cursor,
  Test Connection, Push now, …).
- **Debug mode** toggles the Debug Console for raw payload inspection.
- **Notifications** tab (when the integration reports kinds) — fine-tune
  which alerts you receive; each notification also carries an inline
  "Turn off this kind".

See also [What you grant when you connect](integration-security.md) and
[Troubleshooting](integration-troubleshooting.md).
