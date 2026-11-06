# Connection & Patient Binding

## What you need

- The server's FHIR R4 **base URL** (for example `https://fhir.example.org/r4`).
- A **static access token** the server accepts as a bearer credential (an
  API key issued by the portal).
- The **remote patient id** of the record you are allowed to pull.

## Connecting

1. **Settings → Integrations → FHIR Client → Connect**.
2. Fill the form: instance name, base URL, the access token, and the
   remote patient id. Choose `None / tokenless` as the authorization mode
   only when the server allows unauthenticated reads (a local test
   server).
3. Save. The token is sealed into the encrypted credential store
   immediately — the instance configuration shown in the UI never
   contains it.

## The patient binding

The remote patient id is **required**. Health Assistant scopes every
search to `patient=<id>`; a connector instance without a binding refuses
to pull. If you do not know the id, ask the portal for the FHIR `Patient/{id}`
of your account (it is usually visible in the portal's FHIR viewer).

## Check Connection

The instance's **Check Connection** action validates the three layers in
order and stops at the first broken one:

1. **Credential** — the sealed token decrypts and is not revoked.
2. **Server** — the base URL answers a FHIR search.
3. **Patient binding** — `Patient/{id}` exists on the server.

Each layer reports what to fix (reconnect, correct the URL, correct the
id).

## Rotating the token

Submit the config form again with the new token (leave the field blank to
keep the current one). The sealed credential is rotated in place under
the current encryption key; the previous ciphertext becomes unreadable.
If the portal **revokes** the token server-side, the next sync fails
closed — the instance flips to an error state and asks you to reconnect
(see [Security](security.md)).
