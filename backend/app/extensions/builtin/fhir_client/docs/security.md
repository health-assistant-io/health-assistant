# Security

## Where the token lives

The access token is stored **only** in the encrypted credential store
(`integration_credentials`):

- it is sealed with the platform's at-rest cipher and **bound to the
  owning instance** — a ciphertext copied into another instance cannot
  decrypt;
- the instance configuration (`user_config`) carries no credential
  material, and the UI never shows the token;
- rotation reseals in place under the current key and stamps a rotation
  marker; the old ciphertext becomes unreadable once its key leaves the
  rotation ring.

## Fail-closed pulls

Every sync validates the credential **before** any data flows. A missing,
revoked, or undecryptable token stops the sync with a typed
authentication failure — the instance flips to an error state with a
reconnect prompt. The connector never degrades to an unauthenticated
pull when a credential is expected.

## Tenant isolation

Pulled data is written under the instance's tenant and patient, and every
read path in the platform filters by tenant. A record pulled for one
tenant's instance is invisible to every other tenant — including the
instance configuration and sync logs.

## What this connector does not do

- **No push.** Local data never leaves Health Assistant through this
  connector; the outbound direction belongs to the FHIR Server
  integration and the FHIR facade.
- **No SMART-on-FHIR.** No OAuth round-trip is implemented here; the
  connector speaks static bearer credentials (or tokenless, explicitly
  chosen).
- **No unscoped pulls.** A missing patient binding is a configuration
  error, not a mode.

## Hostile-server posture

All outbound calls ride the platform's guarded HTTP runtime: redirects
are re-validated per hop, private/loopback addresses are refused, and
error details are capped and typed. Resource payloads from the remote
server are size- and shape-checked before mapping.
