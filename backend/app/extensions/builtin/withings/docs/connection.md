# Connection & Authorization

## What you need

- A **Withings partner app** of your own (free at
  [partner.withings.com](https://partner.withings.com)): its **client id**
  and **client secret**, with your deployment's callback URL registered.
- A Withings account with at least one device that has synced data.

## Connecting

1. **Settings → Integrations → Withings → Connect**.
2. Fill the form: instance name, the partner app's **client id** and
   **client secret**, and the initial pull window. The secret is sealed
   into the encrypted credential store immediately — the instance
   configuration shown in the UI never contains it.
3. Save. The instance sits in *pending* with an **Authorize** banner.
4. Click **Authorize**. Health Assistant redirects you to Withings'
   consent page; grant access and you are returned to the instance
   landing. The authorization code is exchanged server-side, the
   access/refresh token pair joins the sealed credential slot, and the
   Withings user binding is recorded.
5. **Sync now** — the first pull covers the configured window.

## The account binding

The Withings user id returned by the authorization is stored with the
instance as non-secret metadata. One instance follows one Withings
account; a household with two scales creates one instance each.

## Check Connection

The instance's **Check Connection** action validates the layers in order
and stops at the first broken one:

1. **Config** — a client id is present.
2. **Credential** — the sealed slot decrypts, is not revoked, and the
   access token is live (a due refresh runs as part of the check).
3. **Withings** — the measure endpoint answers a bounded probe call.

## Rotating the client secret

Submit the config form again with the new secret (leave the field blank
to keep the current one). The sealed credential is rotated in place under
the current encryption key; the previous ciphertext becomes unreadable.
If you revoke the app's access on Withings' side, the next refresh fails
closed — the instance flips to an error state and asks you to reconnect
(see [Security](security.md)).
