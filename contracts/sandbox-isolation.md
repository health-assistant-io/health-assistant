# Sandbox isolation — graded posture (the law)

**Status:** ratified with plan 39 v2 (owner decision D-39, 2026-10-20).
**Scope:** how imported code (T2) is contained on every platform, and
what each containment grade may honestly claim. The trust law is
unchanged — imported code never runs with host trust (ADR-0040 /
ADR-0011 / ADR-0030); this contract defines the *mechanism* per
platform and the disclosure that goes with it.

## 1. Why containment exists (the fact the grades encode)

The capability-RPC seam limits what the host *hands* a bundle; it is not
a boundary. An uncontained child process can use raw OS APIs behind the
seam: read the OS keyring / SSH keys / tokens, open raw sockets
(bypassing the seam's no-net rule), read and write user files. Realm and
vm marshalling are explicitly not security boundaries. Without
containment we are asking hostile code to behave — which is why the
grade is declared data, never assumed.

## 2. Posture vocabulary (closed)

`t2Isolation` is one of exactly three values — the vocabulary is closed
and mirrored in `extension-charter.json` host profiles
(`t2IsolationByPlatform`), `isolation.py` and the desktop TS mirror:

- **`kernel`** — the OS stops the child: filesystem, network and process
  access denied outside what the wrapper explicitly grants. This is the
  only grade that may be called a sandbox in product copy.
- **`process`** — a hardened separate process: scrubbed environment, no
  inherited credentials/handles, confined working directory, resource
  limits (memory/CPU), kill-on-close lifetime. It **does not** deny
  network or filesystem access at the kernel; the consent surface must
  say exactly that. Defense in depth, honestly labeled.
- **`none`** — no containment primitive on this platform; T2 is
  disabled with a surfaced remedy. Never run weak.

## 3. Policy (D-39)

1. A host runs T2 only at `kernel`, or at `process` with the posture
   disclosed in the consent surface and the per-platform opt-in recorded
   in the host profile. `none` ⇒ T2 self-disables with the remedy.
2. **Server-mode hosts that accept imported code require `kernel`** —
   fail closed; there is no process-grade server posture.
3. Posture is reported from **detection**, never from a constant. A
   profile claiming `kernel` while the primitive is unavailable is a
   conformance failure (wall-pinned).

## 4. Requirements per grade (normative)

### 4.1 `kernel` (bwrap reference; other primitives must be equivalent)

- `--unshare-all --die-with-parent --clearenv`;
- read-only binds only: system paths (`/usr /lib /lib64 /bin /sbin`),
  the interpreter and its prefix, the child script's directory; nothing
  else;
- extension content mounted read-only at `/ext`; argv rewritten so the
  child protocol sees `/ext`;
- tmpfs working directory (`/work`) — the only writable path; `PATH` and
  `HOME` pinned into it;
- **no network namespace route**;
- the child re-derives the content digest before executing anything
  (independent of the wrapper);
- wall-clock, memory and CPU watchdogs per call; orphans die with the
  parent.

### 4.2 `process`

- minimal environment allowlist (no host env inheritance, no `PATH`
  beyond the interpreter's own, no credentials);
- `cwd` confined to the workdir; all handles/sockets not explicitly
  passed are closed;
- memory + CPU limits and a kill-on-close lifetime (Job Object on
  Windows, rlimits/session elsewhere);
- **the disclosure states the negative:** network and filesystem are not
  kernel-denied at this grade.

### 4.3 `none`

- T2 self-disables typed (`unsupported`) with the remedy text; the
  product says so in the consent/safety copy.

## 5. The escape matrix (fixtures)

`sandbox-isolation.fixtures.json` is the normative probe set; the same
cases run against **every** backend on every platform. Groups:
fs-read-outside, fs-write-outside, net-connect, net-dns, proc-spawn,
env-read, watchdog-kill, orphan-after-parent-death, memory-bomb,
cpu-loop. Expected outcome per grade: `denied` (the boundary holds),
`contained` (watchdog/kill limits the blast), or `disclosed` (this
grade does not claim it — allowed in the sandbox, must be disclosed).
A backend is `kernel` only when every `denied`/`contained` expectation
for the `kernel` column passes against it.

## 6. Convergence (one law, many implementations)

- `isolation.py` is the canonical builder module (vendored
  byte-identically to every Python host by
  `sync-extension-contract.sh`); the desktop TS mirror
  (`src/main/apps/sandbox/isolation.ts`) implements the same builders
  and is outcome-pinned to the same fixtures.
- The fixtures are the wall: implementations may differ, outcomes may
  not. The dev wall asserts the vocabulary closure, fixture shape and
  `posture()` honesty; each platform runs the matrix as a named blocking
  step in `family-convergence.yml`.
