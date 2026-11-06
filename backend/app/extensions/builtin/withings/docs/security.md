# Security

## Where the credentials live

The client secret and the OAuth access/refresh token pair are stored
**only** in the encrypted credential store (`integration_credentials`),
in one sealed slot:

- the slot is sealed with the platform's at-rest cipher and **bound to
  the owning instance** — a ciphertext copied into another instance
  cannot decrypt;
- the instance configuration (`user_config`) carries no credential
  material — only the non-secret client id and, after authorization, the
  Withings user binding;
- the slot's expiry columns carry the access and refresh token lifetimes;
  every refresh reseals the slot in place under the current key and
  stamps a rotation marker — the old ciphertext becomes unreadable once
  its key leaves the rotation ring.

## Fail-closed pulls

Every sync validates the credential **before** any data flows. A missing,
revoked, or undecryptable slot stops the sync with a typed authentication
failure. An expired access token is refreshed first; a refresh that the
provider rejects (for example because you revoked the app's access on the
Withings side) fails closed the same way — the instance flips to an error
state with a reconnect prompt. The connector never degrades to an
unauthenticated pull.

Disconnecting the instance revokes the sealed slot locally, so a deleted
instance leaves no usable credential behind. (Withings' token-revocation
webservice requires its partner HMAC signature scheme, which this
connector deliberately does not implement — the remote tokens simply
expire: the access token in hours, the refresh token on its next
rotation.)

## Tenant isolation

Pulled data is written under the instance's tenant and patient, and every
read path in the platform filters by tenant. A measurement pulled for one
tenant's instance is invisible to every other tenant — including the
instance configuration and sync logs.

## What this connector does not do

- **No push.** Local data never leaves Health Assistant through this
  connector.
- **No shared Withings app.** You connect your own partner app — the
  client secret is yours, sealed, and rotatable.
- **No sleep/activity pulls.** Flagged, not half-wired (see
  [Pull](pull.md)).

## Hostile-server posture

All outbound calls ride the platform's guarded HTTP runtime: redirects
are re-validated per hop, private/loopback addresses are refused, and
error details are capped and typed. Measure payloads are size- and
shape-checked before mapping.
