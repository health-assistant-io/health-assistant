# Capability RPC — the T2 seam (capability-rpc/1)

**Status:** spec ratified with ADR-0040 (2026-10-15, clause 13); generalizes
ADR-0030's shape into a host-neutral family contract. Message structural
gate: [`capability-rpc.schema.json`](./capability-rpc.schema.json).
Vocabularies (methods, error codes, limits): the
[extension charter](./extension-charter.json). Implementation tier: **T2
imported code only** — one sandboxed subprocess per extension.

Implementations may differ (Electron `utilityProcess` on desktop; a Python
subprocess on career/study); outputs may not (ADR-0040 clause 22 — the
fixture wall is the parity gate).

## 1. Transport & framing

- **Line-delimited JSON**: one UTF-8 JSON object per line, `\n`-terminated,
  over the process's stdio (pipes) or a host-provided local socket/pipe.
- **One line ≤ `limits.rpcLineMaxBytes`** (1 MiB). A larger line, invalid
  JSON, or invalid UTF-8 is a protocol violation: the call it belongs to
  fails with `protocol` and the peer may kill the process after repeated
  violations.
- Both directions share one message shape (§3). No multiplexing beyond
  correlation ids; no streaming responses — bulk data rides `items.emit`
  chunks or paginated results.
- The host speaks first (`hello`), the extension must answer before any
  capability call (§2).

## 2. Versioning & lifecycle

- Protocol version: `capability-rpc/1`, carried as `"v": 1` on every
  message. A version mismatch at handshake ⇒ the host terminates the
  sandbox and self-disables the extension with `unsupported` — never
  half-runs.
- **One process per extension** (ADR-0030 clause 1). Concurrent live
  sandboxes are capped by the host profile. **Sandbox death is extension
  failure, never host failure**: crash or watchdog kill self-disables the
  extension with a surfaced reason.
- Lifecycle: host spawns → `ext.invoke {op:"hello"}` → extension result
  (declares its manifest id/version/digest) → idle → capability calls and
  `ext.invoke` runs → `ext.dispose` (graceful) or SIGKILL after the
  dispose deadline.
- The extension never learns host secrets, env or filesystem layout: the
  process spawns with a scrubbed minimal environment (ADR-0011 §6
  discipline), cwd inside its sandbox dir.

## 3. Messages

Every message: `{ v: 1, type, id, ... }` — `type` ∈ `call | result |
error | event`; `id` correlates call→result/error.

| type | direction | carries | meaning |
|---|---|---|---|
| `call` | both | `method`, `params` | invoke a method |
| `result` | responder | `result` | success payload |
| `error` | responder | `method`, `error` | typed failure (§8) |
| `event` | ext→host | `method` (`evt.*`), `params` | progress / log |

The method vocabulary is **closed** (charter `rpc`):

- extension→host capability calls: `cap.net.fetch`, `cap.state.get`,
  `cap.state.set`, `cap.items.emit`, `cap.secrets.need`
- host→extension: `ext.invoke`, `ext.dispose`
- extension→host events: `evt.progress`, `evt.log`

An unknown method is refused with `unsupported` — never dispatched, never
reflected.

## 4. Capability calls (extension → host)

All capability calls are **host-mediated**: the sandbox has no sockets, no
fs, no env escape. Every call is validated at the seam, quota-checked,
watchdogged and **audited (one audit row per capability call)**.

### `cap.net.fetch` — the only network path

```
params:  { url, method? ("GET"|"HEAD" default "GET"), headers? (object, ≤16),
           body? (string ≤ 64 KiB), maxBytes? (≤ host cap) }
result:  { status, headers (≤32), body (string, ≤ maxBytes), finalUrl }
```

- Served by the **host-side polite runtime** (`contracts/polite_http.py`,
  Phase 1): SSRF guard (resolve-then-connect, reserved ranges, every
  address checked), full-jitter retry, `Retry-After`, robots.txt, size
  caps.
- **Scoped to the manifest's declared `domains` — every hop**: redirects
  are re-validated per hop (D23); a hop outside scope ⇒ `net-denied`
  (non-retryable config-class).
- **Connectors never open sockets.** `net` exists at the seam only as
  this call (D14). The sandbox itself has no network access at the OS
  level — the host IS the perimeter.

### `cap.state.get` / `cap.state.set` — extension-owned state

```
get params:  { key (≤128 chars, pattern ^[a-z][a-z0-9_.-]{0,127}$) }
get result:  { value | null }
set params:  { key, value (JSON ≤ limits.stateMaxBytes) }
set result:  { bytes }
```

Host-stored, quota'd (`limits.stateMaxBytes` per instance), evictable
(charter `state` capability). Distinct from sync cursors
(`instance.syncState`), which never cross this seam.

### `cap.items.emit` — result streaming

```
params:  { items: [ ≤ 256 per call, each ≤ 32 KiB JSON ] }
result:  { accepted }
```

Items are **untrusted observations**: the host validates each against the
kind's item contract (per-kind schema + caps) before anything downstream
sees them. Emitted-but-invalid items count against the extension's error
budget.

### `cap.secrets.need` — resolution status, never a value

```
params:  { slot }
result:  { resolved: true, injectedAs: "<param-name>" }   // host injects into the NEXT host-side call
error:   needs-secret                                      // the user must supply it
```

**Secrets never cross the seam.** The extension asks whether a slot is
resolvable; the host either confirms it will inject the value host-side
(into a subsequent `cap.net.fetch` auth header, a spawn env, etc.) or
returns the typed `needs-secret` error. There is no message shape that
can carry a secret value toward the sandbox — structurally impossible,
like inline tokens in source descriptors.

## 5. Host→extension calls

### `ext.invoke`

```
params:  { op (≤64 chars), input (JSON ≤ 256 KiB) }
result:  { output (JSON ≤ 1 MiB over potentially multiple items.emit calls) }
```

The host starts a unit of work (`hello`, a run tick, a tool execution, a
contribution evaluation). Ops are declared by the kind refinement
(mini-app: pipeline steps; connector: fetch; chat-tools: tool bodies).
The wall-clock watchdog measures each invoke.

### `ext.dispose`

Graceful shutdown: flush state, final events, exit 0. Deadline is
host-owned (seconds); then SIGKILL. Uninstall/tombstone also tears the
sandbox down (ADR-0030 clause 5).

## 6. Events (extension → host)

- `evt.progress` — `{ percent?, messageKey?, payloadBytes? }`: run
  progress; rendered by host chrome, never raw prose (messageKey resolves
  through manifest i18n).
- `evt.log` — `{ level ("debug"|"info"|"warn"|"error"), message (≤ 500
  chars) }`: bounded diagnostics; the host rate-limits per extension and
  never renders log content as UI prose.

Events are observations only — never instructions (ADR-0011 §7
discipline).

## 7. Frozen module surface, watchdogs, quotas

- **Frozen module surface**: sandbox code imports the vendored SDK + the
  capability client (+ pure helpers) only — no DOM, no Node/OS builtins,
  no fetch, no dynamic import escape. Imports are size- and
  complexity-budgeted at import time (ADR-0030 clause 2).
- **Watchdogs per call**: wall-clock (host-configurable, default 30 s per
  `ext.invoke`/capability call) and memory (per-process cap). A watchdog
  kill = crash = self-disable.
- **Per-extension quotas**: calls/minute, bytes/day (net + items + state
  combined), concurrent calls ≤ 1 per sandbox (calls are serialized per
  process). Quota exhaustion ⇒ `budget` (retryable after-config-change),
  never a silent drop.
- **One audit row per capability call**: method, args hash, outcome,
  duration, extension id + digest. An agent pathway that bypasses the
  audit funnel is built wrong (ADR-0011 §8).

## 8. Error envelope (the charter enum)

Every failure is `{ code, detail, retryable, retryAfterMs? }` — `code`
from the charter's 15-code enum, `retryable` from the closed sub-enum,
`retryAfterMs` at the envelope (the one retry-hint slot). `detail` is a
**bounded object** (7E wire truth; D3 — no string form, and the old
JSON-encoded-string detail is deleted): declared keys only —
`message` (≤ 400, the host diagnostic for logs/typed machine surfaces),
`path`, `field`, `id`, `packId`, `runId`, `slot`, `host`, `hop`, `method`,
`op`, `stage`, `engine`, `expected`, `actual`, `limit`, `used` — each
capped, the serialized object capped at `limits.errorDetailMaxChars`.
Construction
keeps only declared keys, truncates `message` first to fit the total cap,
then drops non-`message` keys. **Product UI copy is keyed by `code`**
(host-localized TEXT) and may interpolate the data keys; `message` is
never rendered raw as product copy. Mapping is identity: the seam, the
result envelope's `partial_errors` and every product surface share one
vocabulary (ADR-0040 clause 4). No raw exception strings cross an
extension boundary anywhere.

## 9. Conformance

Cross-host conformance fixtures live with the charter
(`extension-contract.fixtures.json` rpc cases today; the Phase 5 Python
runner adds the ADR-0030 escape-matrix ports — loop, memory bomb, path
wander, secret sniffing contained; crash = self-disable, host
unaffected).

## 10. Non-goals (fixed by ADR-0040)

No code-driven UI intents in v1 (declarative contributions first —
ruling 10); no cross-extension communication; no shared sandbox hosts;
no auto-updates (no signatures ⇒ no auto-apply at all, D22); no
filesystem capability calls in v1 (append-only future major).
