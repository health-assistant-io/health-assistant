# Integration SDK overview

Health Assistant's extensibility surface is a **server-class host for the
family extension charter** (one contract across the Neuronection products):
an *extension* is a versioned artifact — a validated **manifest**
(`extension/v1` envelope: kind `connector` or `bridge`, declared
capabilities, provenance, i18n, a config schema with `x-secret` credential
marking) plus, for bundled extensions, `provider.py` + `config_flow.py`
implementation code. The subsystem lives at `backend/app/extensions/` —
the vendored family contract modules (`contract.py`, `polite_http.py`,
`conformance.py`), the host registry, the polite transport runtime, the
authoring SDK (`app/extensions/sdk/`) and the bundled extensions under
`builtin/`.

The mental model in three bullets:

- **What an extension may do is declared, not discovered.** The manifest
  gates everything at registration — the `capabilities` list (the closed
  charter taxonomy) and, for the pull families, the connector kind's
  **data classes** (the `itemSchema` property names: `observation`,
  `clinical_event`, `examination`, `document`, `medication`, `allergy`,
  `immunization`, `catalog_proposal`, `hitl_proposal`). The historical
  `supports_*` probing hooks are gone — declared data, never probes.
- **Outbound HTTP always rides the polite transport.** The SSRF-guarded,
  retrying, rate-limit-aware runtime is injected as your
  `self._http_client`; providers never open sockets of their own.
- **The engine writes through the canonical services.** Providers return
  typed payloads (`ObservationCreate`, `ClinicalEventCreate`, …); the sync
  pipeline maps, dedups and persists them — a provider never touches a
  table directly.

## The five bundled extensions

| Extension | id (kind) | Demonstrates |
|---|---|---|
| Developer Dummy Data | `dev-dummy` (bridge) | The full surface: pull/push, inbound webhook, two-way API, chat tools, notifications, clinical events, examinations, catalog/HITL proposals, documents. The reference provider and the contract test-bed. |
| Health Assistant Bridge | `health-assistant-bridge` (bridge) | The signed two-way API for mobile/headless clients + the published sub-API schema; official Python/TypeScript/Kotlin client SDKs ship beside it. |
| FHIR Server (Hospital Sync) | `fhir-server` (connector) | SMART-on-FHIR OAuth (PKCE + dynamic client registration), tokenless FHIR, pull and push (conditional update). |
| MCP Client | `mcp-client` (connector) | An MCP client connection — remote tools exposed to the chat assistant. |
| Universal Webhook | `webhook` (bridge) | The generic inbound webhook receiver with reusable parsers. |

## When to write an extension vs a core feature

**Write an extension when** the thing you want is a *data source or inbound
surface that varies per deployment or per user*: another wearable, a
national health portal, a hospital FHIR server, a custom webhook sender, a
mobile/headless client. Extensions are filesystem drop-ins under
`backend/app/extensions/builtin/{domain}/` — new integrations land without
touching any endpoint, because every hook is opt-in and the engine reads
the registry.

**Write a core feature when** the behaviour must be tenant- or
platform-wide and needs deeper service integration: the biomarker engine,
notification fan-out and the document pipeline are core because every
deployment needs them and they hold clinical law (tenant isolation, audit,
FHIR validation). Don't reach for an extension to add a utility endpoint —
extensions exist to move *external* data in and out.

**Imported extensions (git sources) are not part of health's host profile
today** — the steady state is bundled-only, admin-curated. An extension
author is effectively a contributor to this repo, which is why the authoring
bar is "reviewed with the product". External clients meet the *already
written* surface: the bridge kind and its client SDKs are how outside
parties join the data — see the
[bridge recipe](examples-and-use-cases.md#recipe-5--the-bridge-as-the-mobile-headless-transport)
and the per-SDK quickstarts under `integrations/health-assistant-bridge/`.

## Where to go next

- Impatient? The [Quickstart](quickstart.md) takes you from empty scratch
  database to synced observations in about ten minutes.
- Building an extension? Read [Core Concepts](core-concepts.md), then
  [Authoring & Configuration](authoring-and-configuration.md), then the
  [recipes](examples-and-use-cases.md).
- Using the app an integration ships in? Start at the user side:
  [Connecting an integration](../../user/connect-integrations.md),
  [What you grant when you connect](../../user/integration-security.md),
  [Troubleshooting](../../user/integration-troubleshooting.md).
