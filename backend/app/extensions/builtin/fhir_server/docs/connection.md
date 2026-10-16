# Authorization & Connection

The FHIR Server integration supports two authorization modes. Pick one when configuring the instance — it determines whether you run the SMART round-trip or connect tokenless.

## SMART-on-FHIR (standalone launch)

For hospitals, Epic/Cerner, and any server that advertises `/.well-known/smart-configuration`. Health Assistant runs the **standalone (Patient) launch** flow with **Dynamic Client Registration (DCR)** — you enter only the server URL; no client ID or secret to obtain out-of-band.

**The flow:**

1. Save the instance with **Authorization = SMART**. The instance is created `PENDING`.
2. Click **Authorize**. Health Assistant:
   - Discovers the SMART config (`/.well-known/smart-configuration`).
   - Registers a client via DCR (RFC 7591), generating a PKCE pair.
   - Redirects you to the hospital's authorize endpoint — **you pick the patient on the hospital's consent screen here.**
3. The callback exchanges the code for tokens, encrypts them (Fernet) at rest, and flips the instance to `ACTIVE`.

The resolved patient id from the launch is stored in the encrypted token blob and used as the remote sync target (unless you override it — see [Selecting the Remote Patient](patient-selection.md)).

### Scopes (read vs write)

- **Pull only** (`sync_direction = pull_only` / `both` without push): requests `patient/*.read`, `openid`, `fhirUser`, `offline_access`.
- **Push enabled** (`sync_direction = both` / `push_only`): additionally requests `patient/*.write`.

The consent screen reflects the requested scopes. **If you change `sync_direction` to include push after the initial authorization, re-authorize** so the write scope is granted — otherwise the first push fails with an `insufficient_scope` error (see [Troubleshooting](troubleshooting.md)).

Health Assistant requests `offline_access` so a refresh token is issued; tokens are refreshed transparently on expiry and on a 401 race.

## None / tokenless

For local or open FHIR servers that don't implement SMART (e.g. a vanilla [HAPI FHIR](https://hapiproject.org/) in Docker). No authorize step, no token, no Redis/Fernet dependency.

- The instance goes straight to `ACTIVE`.
- Searches run unauthenticated.
- You must tell Health Assistant **which remote patient** to sync — either via the **Find Patient** picker or by entering a *Remote FHIR Patient ID* in the config (see [Selecting the Remote Patient](patient-selection.md)). Without it, pulls run unscoped.

> Vanilla HAPI FHIR does **not** serve `/.well-known/smart-configuration`, so use **None** mode for it. The SMART Health IT sandbox (`https://r4.smarthealthit.org`) is the simplest server that supports the full SMART round-trip.

## Check Connection

The **Check Connection** action does a `GET {base}/metadata` and summarizes the CapabilityStatement:

- Server reachability + HTTP status.
- FHIR version + server software name/version.
- The list of resource types the server supports.
- (SMART mode) confirms the stored token still authenticates.
- The currently-linked remote patient.

Use it to verify the URL, confirm the server is up, and see what resources are available before relying on a sync.

## See also

- [Overview](overview.md)
- [Selecting the Remote Patient](patient-selection.md)
- [Troubleshooting](troubleshooting.md)
