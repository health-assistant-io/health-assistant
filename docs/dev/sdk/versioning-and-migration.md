# Versioning & Migration

## The version surface

Three version axes, each append-only in practice:

| Axis | Where | Policy |
|---|---|---|
| `apiVersion` | the manifest envelope (`"extension/v1"`) | the charter register — one version for the family contract's envelope shape; validated at registration |
| `kindVersion` | the manifest (`1`) | per-kind artifact version — the bundle cohesive with the engine's readers; bumped when the *kind's* contract gains required members |
| `version` | per extension (`semver`) | the extension's own version; shown in the catalog, advertised as `latest_sdks` for the bridge SDKs |

## Append-only vocabularies

Everything a client or engine can branch on is a closed list that only
grows:

- **capabilities** (the charter taxonomy — health hosts `sync`, `push`,
  `notify`, `chat-tools`, `secrets`, `state`, `inbound-webhook`,
  `inbound-api`, `net`, `llm`),
- **the 15 error codes + the `retryable` sub-vocabulary**,
- **the bridge's `BridgeError` codes** (machine-pinned: the published
  schema's `errors.codes` is extracted from the live code — a code
  change without the schema regeneration fails CI),
- **`bridge_features`** (clients treat absence as "unavailable" and
  degrade, so adding flags is safe; removing/renaming is not),
- **the connector data classes** (`itemSchema` names).

Adding to a vocabulary = the schema + the doc table in the same commit.

## The no-shims doctrine

The family's destructive re-authoring law, as applied on this host:

- The historical `IntegrationError` class tree (Auth/RateLimit/Data/
  Config) was **replaced** by the typed `ExtensionError` model — no
  aliases, no back-compat shims.
- The historical `supports_*` probing hooks are gone — the engine asks
  the registry (manifest declarations). Compatibility at the engine edge
  is exactly one `_declared` resolver mapping legacy hook names onto
  capability/data-class declarations; *new code* must declare.
- The old flat manifests (`domain`/`name`/`integration_type`/`icon`) were
  **re-authored** onto the envelope, never converted at runtime.
- The per-extension docs trees keep the legacy category-list shape until
  a reader upgrade lands: a chaptered set (the docs-tree contract's
  chaptered form — the one this set's `docs-tree.json` declares) needs
  the in-app reader to consume it; if you migrate a builtin tree, land
  the reader change in the same commit.

## Breaking-change rules

1. **Manifest envelope changes** are never automatic — an invalid
   manifest self-disables with a logged reason at registration (never
   half-runs).
2. **Capability/data-class additions** (extension starts declaring more)
   are append-only; existing instances keep their state (`user_config`,
   cursors, machine secrets survive process-restarts and upgrades).
3. **Config schema changes** — additive properties are optional and
   default; removing/renaming a field strands stored config; give a
   migration in the config flow (read the old key → write the new one)
   or a version note.
4. **Bridge wire contract changes** — the route/payload schema is
   regenerated and SDK model layers follow in the same commit (the CI
   drift gate), so a wire change can never ship with stale clients.
5. **`HA_*` settings renames** — prefixed-only; the retired env names
   (e.g. the single `SECRET_KEY`, `INTEGRATION_SECRET_KEY`) are gone, no
   env aliasing.

## What happens to an installed instance on a release

Boot re-runs registration — manifests validated, code reloaded,
`setup()` re-called with the stored global config; instances keep their
`user_config` (seals are keyed to the instance id, not to app version),
their sync cursors, and their machine secrets. A released extension that
drops a hook it used to declare just stops being called for it; nothing
crashes elsewhere around that instance's row. New bundled extensions
appear in the catalog without migration.

## The underscore-domain alias

Health's historical underscore domains (REST paths, stored `provider`
values, the package name) survive as the product alias derived
mechanically from the envelope id (`domain_of`); the charter's hyphenated
id is canonical for new authoring and the registry accepts both for
lookups. Don't mint a second aliasing convention in a new extension.
