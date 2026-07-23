# API Access Layers

Health Assistant exposes three distinct API surfaces, each with a different
audience, contract, and authentication model. This doc is the decision guide
for which surface to use and the reference for how the layers are separated —
important for any self-hosted, open-source deployment that wants to connect an
external system without coupling to internals.

The short version: the **web frontend and mobile app** talk to an internal
domain REST API; **external clinical systems** (EHRs, labs, HL7 importers)
talk to a canonical FHIR R4 facade with OAuth2 + SMART-on-FHIR scopes; and
**specific connected platforms** (wearables, push-based services) are wired
through the Integrations Framework. Each layer is independently versioned and
can evolve without breaking the others.

## The three layers at a glance

```
                       ┌──────────────────────────────────────────────┐
   Web frontend ──────▶│  Layer 1 — Internal domain REST API          │
   Mobile companion ──▶│  /api/v1/*  (ORM-shape, 298 handlers)         │
   (session JWT only)  │  token_kind=session ONLY                      │
                       │  tenant + patient scoped, role-based          │
                       └──────────────────────────────────────────────┘
                       ┌──────────────────────────────────────────────┐
   3rd-party systems ─▶│  Layer 2 — FHIR R4 facade (PUBLIC)            │
   EHR / HL7 / labs ──▶│  /api/v1/fhir/R4/*  (canonical FHIR, 20 res)  │
   (OAuth2 + SMART)    │  token_kind=api, aud=health-assistant-api     │
                       │  SMART scope-enforced per interaction         │
                       └──────────────────────────────────────────────┘
                       ┌──────────────────────────────────────────────┘
   Wearables / push ──▶│  Layer 3 — Integrations Framework             │
   Specific partners ─▶│  integrations/*  (config flow + sync engine)  │
   (per-provider)      │  HMAC webhooks, run_sync pipeline              │
                       └──────────────────────────────────────────────┘
```

| Concern | Layer 1 — Domain REST | Layer 2 — FHIR R4 facade | Layer 3 — Integrations |
|---|---|---|---|
| Audience | Web UI + mobile app | External clinical systems | Specific connected platforms |
| Shape | ORM-shape (snake_case, app fields) | Canonical FHIR R4 (camelCase, `fhir.resources`-validated) | Provider-native → FHIR Observation internally |
| Auth | Session JWT (HS256, role) | OAuth2 client-credentials + SMART scopes | Per-instance config / OAuth / HMAC |
| Versioning guarantee | Free to move with the UI | Stable + versioned (the integrator SLA) | Per-integration `manifest.json` version |
| Scoping | Tenant + role + ownership | Tenant + SMART scopes + optional patient-compartment | Owner + tenant |
| Stability contract | Internal (no external SLA) | **Public** (the integrator contract) | Per-provider |

## Which surface should I use?

- **Building the web UI or the mobile companion app?** → Layer 1. Use the
  domain REST endpoints (`/patients/*`, `/observations/*`, ...) with a
  session JWT obtained from `/auth/login`. The response shape is ORM-tailored
  for the UI (snake_case, app fields like `biomarker_id`,
  `normalized_value`).
- **An external clinical system, EHR, lab, or HL7 importer?** → Layer 2. Use
  the FHIR R4 facade (`/api/v1/fhir/R4/*`) with an OAuth2 client-credentials
  token carrying SMART-on-FHIR scopes. See
  [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md) and the
  [Authentication & scopes](#authentication--scopes) section below.
- **Connecting a wearable, a push-based service, or a platform with its own
  OAuth or polling model?** → Layer 3. Write an integration module under
  `integrations/` using the SDK. See
  [INTEGRATIONS_FRAMEWORK.md](INTEGRATIONS_FRAMEWORK.md) and
  [INTEGRATIONS_SDK.md](INTEGRATIONS_SDK.md).
- **A non-FHIR partner with no provider-specific sync model?** → Today, use
  Layer 2 (the facade is the only public REST surface). A curated non-FHIR
  REST subset exists only if/when a concrete partner requires it; it is not
  built speculatively.

> The domain REST API (Layer 1) is **not** a public API. It returns internal
> ORM-shape JSON intended for first-party clients. External integrators must
> use the FHIR facade. This separation lets the UI evolve freely without
> breaking external consumers, and keeps the external attack surface small
> and standards-based.

## Why three layers (and not one)

Coupling external consumers to the same API the frontend uses is a common
trap: every UI-driven refactor breaks integrators, internal fields leak out,
and a leaked token grants tenant-wide access. Health Assistant separates the
concerns instead:

- **Stability** — Layer 2 is the versioned contract external systems depend
  on; Layer 1 can change with the UI because only first-party clients use it.
- **Standards** — Layer 2 speaks canonical FHIR R4, the lingua franca of
  healthcare interoperability, so integrators don't have to learn a bespoke
  contract.
- **Least privilege** — Layer 2 tokens carry SMART scopes (`system/*.read`,
  `patient/Observation.write`, ...) so an external client only reaches the
  resources it was granted. Layer 1 session tokens are role-scoped to a
  signed-in user.
- **Encapsulation** — the Integrations Framework (Layer 3) handles the
  messy per-provider realities (OAuth flows, polling, HMAC webhooks, rate
  limits) and normalizes everything to FHIR Observations internally. It is
  not a general-purpose API gateway — each integration is a dedicated module
  for one platform.

## Authentication & scopes

### Layer 1 — session JWT

The frontend authenticates with `POST /auth/login` and receives a signed
session JWT (HS256). Every request sends `Authorization: Bearer <jwt>`. The
token carries `user_id`, `tenant_id`, `role`, and `sub`; trust is in the token
(no DB lookup per request). Tenant and patient scoping is enforced on every
endpoint — see [TENANCY_AND_USER_MANAGEMENT.md](TENANCY_AND_USER_MANAGEMENT.md).

Session tokens are rejected on the FHIR facade (Layer 2 is external-only) and
are the only kind accepted on the domain REST API.

### Layer 2 — OAuth2 client-credentials + SMART-on-FHIR scopes

External systems authenticate with the OAuth2 **client credentials** grant
(RFC 6749 §4.4), the standard machine-to-machine flow:

1. An administrator registers an OAuth2 client for the external system
   (`POST /oauth/clients`) and receives a `client_id` + `client_secret`. The
   client is bound to one tenant and granted a set of SMART scopes.
2. The external system calls `POST /oauth/token` with its credentials and
   receives a short-lived access token (JWT).
3. The token is sent as `Authorization: Bearer <token>` against
   `/api/v1/fhir/R4/*`. The token carries `aud=health-assistant-api`,
   `iss`, the granted `scope`, `client_id`, and `tenant_id`.

**SMART scope syntax:** `<context>/<resource>.<permission>`

- `context` — `system` (tenant-level backend service), `user` (on behalf of a
  user — future), or `patient` (restricted to one bound patient).
- `resource` — any registered FHIR resource type, or `*` for all.
- `permission` — `read`, `write`, or `*`.

Examples:
- `system/Observation.read` — read biomarker readings at the tenant level.
- `system/*.read` — read-only across all resources the client may see.
- `patient/Observation.write` — write observations for the bound patient only.

Every facade interaction is scope-checked: a read/search needs a `.read`
scope, a create/update/delete needs a `.write` scope. A token with the wrong
scope gets a `403` FHIR `OperationOutcome`. The
`GET /fhir/R4/metadata` CapabilityStatement and
`GET /.well-known/smart-configuration` advertise the supported scopes and
grant types. See [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md) for the full surface.

> The user-facing OAuth2 authorize flow (`grant_type=authorization_code`,
> PKCE, `launch` context) is on the roadmap; today the facade serves backend
> service flows via client-credentials.

### Layer 3 — per-provider credentials

Each integration stores its own credentials (encrypted at rest) and manages
its own auth model — an OAuth2 token for a wearable, an API key for a lab, or
an HMAC-shared-secret webhook. See
[INTEGRATIONS_FRAMEWORK.md](INTEGRATIONS_FRAMEWORK.md).

## Versioning & stability

| Layer | Policy |
|---|---|
| Layer 1 (domain REST) | Internal — may change in any release. No external stability guarantee. |
| Layer 2 (FHIR facade) | Versioned (`/api/v1/fhir/R4/*`). Breaking changes require a new major version or a deprecation window. This is the public contract. |
| Layer 3 (integrations) | Each integration carries a `version` in its `manifest.json`; breaking changes bump that version. |

## See also

- [REST API Reference](API.md) — the Layer 1 domain endpoint reference
  (frontend / mobile).
- [FHIR R4 Facade](FHIR_R4_FACADE.md) — the Layer 2 public interop surface,
  SMART scopes, and CapabilityStatement.
- [Integrations Framework](INTEGRATIONS_FRAMEWORK.md) — Layer 3, per-provider
  sync.
- [Integrations SDK](INTEGRATIONS_SDK.md) — building an integration module.
- [Users, Tenants & Roles](TENANCY_AND_USER_MANAGEMENT.md) — the tenant +
  role model that underpins all three layers.
