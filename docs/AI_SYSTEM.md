# AI System & Configuration Guide

This document describes the design, configuration, and extensibility of the AI processing system in Health Assistant.

## 1. Core Architecture

The AI system is built on a **Unified Factory Pattern**, decoupling clinical logic from specific AI providers.

### Key Components

| Component | Responsibility |
|-----------|----------------|
| `AIProviderService` (`app/ai/providers/service.py`) | Central "Brain" for model resolution. Handles multitenancy, priorities, and hands resolved config to the model factory. |
| Model factory (`app/ai/chat_models.py`) | The **only** module allowed to construct LangChain chat models. Provider-type → builder dispatch via `app/ai/providers/registry.py`. The OpenAI builder routes the token cap by endpoint (shared wire policy): api.openai.com gets `max_completion_tokens` (modern models reject `max_tokens`), other base URLs keep `max_tokens` via `CompatibleChatOpenAI` (vendored `app/ai/wire_params.py`). Temperature is omit-unless-configured: unset sends nothing (the only value reasoning families accept). Model rows can set `chat_transport='responses'` to ride the Responses API (OpenAI endpoint only, store-less) — the gpt-6 remedy for reasoning + function tools; otherwise set reasoning effort `none`. |
| `LangChainOCRProcessor` (`app/ai/processors/ocr/`) | Generic vision processor that converts images/PDFs/DICOMs into Markdown text. Receives its chat model injected. |
| `LangChainStructuredExtractor` (`app/ai/processors/nlp/`) | Generic NLP extractor that maps Markdown text to structured FHIR medical entities. Receives its chat model injected. |
| STT client (`app/ai/assistance/stt.py`) | Gateway-owned narrow exception: raw `httpx` multipart against `{api_base}/audio/transcriptions` (LangChain has no transcription chat model). Resolved via `get_stt_target`, capability-gated (`audio_input`). |
| Graphs package (`app/ai/graphs/`) | LangGraph flows: one module per flow, typed State. Nodes call the gateway, never models directly. |
| Checkpoint store (`app/ai/graphs/checkpointer.py`) | Process-lifespan-held `AsyncPostgresSaver`: pool opened once in the FastAPI lifespan, boot-time idempotent `setup()`, **drain-before-close** on shutdown (in-flight graph runs finish before the pool closes). |
| Checkpoint pruning (`app/workers/ai_tasks.py::prune_checkpoint_threads`, daily beat) | The checkpoint tables have no timestamp columns, so thread age derives from the owning chat session — the bare `thread_id` == session id or the `{session_id}:{branch_tip_id}` form branched turns use: sessions idle beyond the `checkpoints` row in `retention_policies` (default 30 days, operator-editable; the retired `AI_CHECKPOINT_RETENTION_DAYS` env var is gone) prune all their threads, and orphan threads whose owning session row is gone are pruned too. No-op until the tables exist; HITL cards live in `proposals` and are never touched. |
| `AIAssistanceService` (`app/ai/assistance/service.py`) | Orchestrator for the Agentic Chatbot and "Magic Fill" features. Manages session context and routing. |
| `app/ai/agents/` | The agentic loop + HITL plumbing: `chat_agent.py` (reasoning loop, tool dispatch, streaming), `hitl.py` (resume-continuation contract, `[HITL RESOLUTION FEEDBACK]` formatting + the untrusted-payload screening seam), `prompts.py` (system prompt assembly). |
| `app/ai/tools/` | LangChain tools (DB queries, document retrieval) that the AI assistant can invoke via `get_tools(db, tenant_id, patient_id, examination_id=None, user_id=None)`. |
| `MedicalProcessingService` (`app/ai/pipeline/service.py`) | Orchestrator for complex clinical logic (unit conversion, ontology matching, persistence). |
| `app/workers/ai_tasks.py` | Celery worker task definitions that delegate to the above services. |

---

## 2. Configuration & Model Resolution

Health Assistant uses a strict, database-driven configuration for all AI processors. This ensures consistency and auditability across multitenant deployments.

### Data Models
- **Providers:** Define the API endpoint and credentials (e.g., OpenAI, custom vLLM server). `provider_type` names the backend: `openai` (covers every OpenAI-compatible endpoint), `mock`, the processor backends (`tesseract`, `spacy`) or `decision`. The reserved-but-unwired types (`anthropic`, `azure_openai`, `bedrock`, `ollama`) are refused on create/update with `422 provider_type_unwired`; an unrecognized string still falls back to the OpenAI-compatible builder with a logged warning.
- **Models:** Define the specific model string (e.g., `gpt-4o`, `gemini-1.5-pro`), context window (`max_tokens`), `temperature`, and an optional **reasoning effort** (`settings.reasoning_effort`: `none`/`minimal`/`low`/`medium`/`high`). Reasoning-family models (GPT-5 / o-series) reject function-tool requests on chat completions unless reasoning effort is `none` — set it on the model row to serve the chat task; leave unset for provider defaults. Multiple models can belong to a single provider.
- **Task Definitions (`ai_task_defs`):** The task vocabulary is a **seeded lookup table**, not a CHECK constraint and not a free-form string. Rows are seeded at boot from the one TaskDef registry (`backend/app/ai/providers/task_registry.py`), so adding an AI task is a registry entry + a row — never a migration. Ids are deterministic (uuid5 over the task code), and a parity test asserts *seed ≡ registry* (`tests/test_ai_task_registry.py`).
- **Task Assignments:** Map a task (`ai_task_defs.id`, e.g. `ocr`, `chat`, `define_anatomy_graph`) to a Provider + Model at one configuration scope. A **specialist** row may declare its own fallback pair (`fallback_provider_id`/`fallback_model_id`); a **capability default** may not — a default *is* the fallback (the single-slot rule).

### The one task registry (the AI task registry work)
`backend/app/ai/providers/task_registry.py` declares each task once as a `TaskDef` `{id, label_key, capability, trigger, phi_class, accepts_priority, decision_kinds?, enabled, route, capability_default}`. Everything else is derived and never hand-maintained:

```
TaskDef registry (Python, the source of truth)
   ├─ TaskType enum            → backend/app/ai/providers/task_types.py   (generated)
   ├─ capability map / setup slot map → providers/capabilities.py, providers/setup.py (derived functions)
   ├─ ai_task_defs rows        → seeded at boot (data, not DDL) + parity test
   └─ frontend task list       → frontend/src/generated/taskRegistry.ts   (generated)
```

`scripts/sync-task-registry.mjs` emits both projections (`--ts`, `--py`; `--check`/`--check-py` are the drift gates); `scripts/check-task-registry.sh` runs in CI (`task-registry.yml`) and fails on projection drift, a reappearing hand-maintained map (`TASK_REQUIRED_CAPABILITY` / `_SLOT_TASK_TYPES` / a literal task list), a label key missing from any shipped locale, or a non-LLM engine task leaking back into the AI layer. The completeness wall asserts every registry task has a UI label and a declared capability default, so a task can no longer exist in the enum while being invisible and unassignable (the `define_anatomy_graph` failure mode).

**Taxonomy (the AI task taxonomy):** `medication_interaction` (RxNorm) and `anomaly_detection` (analytics) are **not** AI tasks — they have no provider, model or prompt and live in `backend/app/services/engine/engine_task_registry.py` as background engine tasks.

### Resolution Logic — one implementation
Precedence exists **once**, in `backend/app/ai/providers/resolution.py` (the decision funnel supplies only its capability/PHI predicate — it does not own a second chain):

1. **Specialist primary** — the active assignment for the task itself, most specific scope first: USER > TENANT > SYSTEM (an explicit `CASE`, not the old alphabetical-enum trick).
2. **Specialist fallback** — the `fallback_provider_id`/`fallback_model_id` pair on that specialist row (columns, never extra rows).
3. **Capability default** — the task declared as the capability default for the task's capability: `default` (text), `ocr` (vision), `transcription` (stt), `embed` (embeddings), `decision` (decision slots).
4. **Unavailable** — `None` (the chat gateway then uses environment settings; the decision funnel fails open).

An **empty** specialist slot inherits the capability default; a **configured-but-unusable** slot is reported as unavailable instead of being silently replaced (the "no third hop" rule). `priority` no longer exists anywhere: app-side ordering is gone and the database enforces the single-slot law.

### Assignment law (enforced by the database)
`ai_task_assignments` rows are **revoked, never deleted** (`revoked_at`; "active" means exactly `revoked_at IS NULL`). A partial unique index enforces **one active row per `(tenant_id, user_id, task_type_id)`** with `NULLS NOT DISTINCT` — required because system rows are `(NULL, NULL, task)` and tenant rows `(tenant, NULL, task)`, which plain NULL-distinct uniqueness would treat as distinct. Capability defaults need no second index: each default *is* a task (`default`/`ocr`/`transcription`/`embed`/`decision`), so the same index is the `(tenant, user, capability)` law. A second active row is a `409 assignment_conflict` (`IntegrityError` at the DB, not an app-side check). **PG15+** is required (`tests` + CI run PG16).

*Note: While database configuration is strongly recommended for production, the processors (OCR and NLP) still fall back to environment variables (e.g. `OPENAI_API_KEY`, `OCR_PROVIDER`) when no usable assignment exists.*

### Per-AI-call audit (`ai_call_logs`)
Every model call writes **one row** — not one per user turn: a three-tool agent loop is three rows. Emission happens at the two seams where a model is actually invoked:

- **the LangChain model factory** (`app/ai/providers/service.py` builds every chat/OCR/NLP model) attaches `AICallAuditHandler` (a LangChain callback handler). Instance-level callbacks survive `bind_tools` and cover `invoke`/`ainvoke`/`stream`/`astream`, so tool-calling loops, streaming chunks and structured-output calls are all counted by construction;
- **the decision funnel** (`app/ai/decide/funnel.py`) calls the shared `record_call` helper with the transport's usage payload (the decide SDK is not a LangChain model).

| column | meaning |
|---|---|
| `task_type_id` | FK → `ai_task_defs` (the registry vocabulary) |
| `route` | closed vocabulary: `chat \| decision \| embed \| stt \| ocr \| extract` (native PG enum) |
| `provider_id` / `model_id` | `SET NULL` on delete — the audit outlives config |
| `tenant_id` / `user_id` / `patient_id` | tenant cascade on erasure; no patient FK (the row outlives the patient) |
| `started_at` / `latency_ms` | wall-clock start + measured duration |
| `prompt_tokens` / `completion_tokens` | provider-reported usage when available (nullable) |
| `cost_micros` | **integer micro-units**, computed from the model row's declared `settings.cost_per_1k_{input,output}_micros` — no hardcoded price table and no float money; `0` when the deployment declared none |
| `status` | `ok \| error \| timeout \| cancelled` (native PG enum) |
| `error_class` | the typed machine class (e.g. `APITimeoutError`), never a message or prompt fragment |
| `trace_id` | correlation id minted at the gateway (shared by every call from one model construction) |
| `meta` + `meta_schema_version` | open payload (model name, `streamed`); **never prompts or completions** (PHI) |

Writes are **fail-soft** (a failed audit insert is logged and dropped — it never breaks the model call), rows carry no PHI payload, and retention is the `operational` class owned by the plan-6 retention job. `GET /ai-config/usage?window_days=N` (tenant-scoped, admin-only) aggregates the table per task: calls, errors, tokens, `cost_micros`, average latency.

**Health delta worth knowing:** the embeddings (`run_embed`) and speech-to-text (`transcribe_audio`) paths are direct HTTP clients outside the LangChain gateway; the `embed`/`stt` routes are already in the vocabulary, and their emission lands when those seams grow the tenant/trace context plumbing (tracked as a follow-up, not a silent omission).

### UI Management
Administrators can manage AI settings via the UI at `/settings/ai-config`:
1. **Add a Provider:** Specify the API Base URL and API Key.
2. **Add Models:** For each provider, define available models, token limits, and desired temperature (e.g., `0.7` for NLP, `0.0` for structured extraction).
3. **Assign Tasks:** Map system tasks to the desired model.

### API Key Security
The `api_key` field has special handling that other provider fields do not:

- **At rest:** values are encrypted with Fernet via `HA_DATA_KEY` (the same key used by integrations/MCP secrets) using the shared at-rest cipher from the [`neuronection-auth-kit`](https://github.com/neuronection/auth-kit) package (`nx_auth.atrest`, git-SHA-pinned in the workspace `pyproject.toml`). Encrypted values are stored with an `enc::` prefix so legacy plaintext rows continue to work during migration. `HA_DATA_KEY` is required in production — `encrypt_secret` **fails closed** (raises `RuntimeError` rather than storing cleartext) when it is unset outside dev/test; dev/test keeps a loud-warning plaintext fallback.
- **On response:** every `AIProviderResponse` / `AIProviderWithModelsResponse` masks the key to a bare `***` via a `model_validator(mode="after")`. The mask is presence-only: it never decrypts the stored value and never reveals a last-4 fragment (audit 2026-10-18 F18). The companion boolean `has_api_key` indicates whether a key is configured. The plaintext is never serialized over the API.
- **On write:** `AIProviderService.create_provider` encrypts any provided key. `update_provider` treats the following as "no change" to preserve the existing key: `api_key` absent (via `exclude_unset`), `api_key=None` (explicit clear), `api_key=""` (explicit clear), `api_key="***"` (the masked form the UI re-sent; any `***`-prefixed value is treated as masked). Any other string is encrypted and stored.
- **At use:** the LLM factory reads plaintext via `AIProviderModel.get_api_key_plaintext()` — the only sanctioned accessor. `AIProviderModel.to_dict()` returns the encrypted form so accidental serialization paths (logs, audit trails) never leak it.

**Backfill existing plaintext rows** after enabling API-key encryption:
```bash
cd backend && PYTHONPATH=. python scripts/encrypt_existing_api_keys.py --dry-run
cd backend && PYTHONPATH=. python scripts/encrypt_existing_api_keys.py
```

**Scope checks:** every `/ai-config/providers/*` and `/ai-config/models/*` endpoint enforces USER/TENANT/SYSTEM scope via `verify_provider_access` / `verify_model_access`. The task-resolution reads (`GET /ai-config/task-assignments/active/{task_type}`, `GET /ai-config/default-for-task/{task_type}`) apply the same discipline to their caller-supplied `tenant_id`/`user_id` parameters through `check_requested_scope`: a SYSTEM_ADMIN reads any layer, every other role only its own tenant, and a non-self `user_id` is refused (403) before any resolution runs. `fetch-external-models` additionally rejects `api_base` values that point at loopback / private / link-local addresses in production (`DEBUG=False`).

### BYOK one-click provider setup

Health implements the platform-standard BYOK setup contract. One call takes a user from no provider to working defaults. **Scope-aware (health delta):** the SAME setup runs against every config layer — SYSTEM (system admins, via `/admin/system/ai-config`), TENANT (tenant admins) and USER (personal, the default). `ProviderSetupRequest.scope` + `check_scope_access` guard the layer; adoption and slot gap-fill stay strictly within the requested scope (a USER setup never touches SYSTEM/TENANT rows and vice versa), and `set-default` binds at the provider's own scope.

- **Canonical data** lives in `contracts/ai-presets.json` and is synced into `backend/app/ai/providers/presets_data.py` by `scripts/sync-ai-presets.mjs` (GENERATED — never hand-edited; `scripts/check-byok-contract.sh` gates drift). The hand-written overlay `backend/app/ai/providers/presets.py` maps canonical wire types onto health's `ProviderType` (`openai_compatible` → `openai`) and records per-app enablement deltas: `gemini` (no wired `google` builder) and `anthropic` (reserved stub) are disabled with reasons — enabling them later is a one-line overlay change once the matching factory branch lands.
- **Setup semantics** (`backend/app/ai/providers/setup.py`): fetch-first validation (the key is verified against the vendor's real catalog before anything persists), curated allowlists matched exact-or-snapshot-suffix, append-union on re-run (user models are never deleted), gap-fill only into empty/dead slots, `preset_key` stamping + adoption of manual rows (same type + base, earliest first). Options body (the five-bind body): `curated_ids / bind_chat / bind_vision / bind_stt / bind_decision`.
- **Slot mapping (health delta):** derived from the task registry — the shared chat slot binds the text capability default (`default`), vision binds `ocr`, STT binds `transcription`, embeddings bind `embed`, and `bind_decision` gap-fills only the `decision` default slot (kind slots inherit, the decision-default rule). Setup never writes a fallback pair and never sets the PHI opt-in.
- **Decision-only presets** (wire type `decision`, e.g. the docs-verified `typesafe`) validate via the **decision probe** — one minimal typed question under the abort timeout; a classified failure persists nothing. They appear in `ALL_PRESETS` (setup-acceptable) and, since the §13.3 exposure switch flipped (2026-10-13), on the tile surface like every preset. `decision_models[]` from the canonical catalog persist as `capabilities=["decision"]` + `decision_transport="systemone"` rows; `bind_decision` gap-fills each empty declared decision slot at the setup's own scope, never a live one, and the created rows are never PHI-opted-in. Keyless local presets (Ollama: `nimble`/`tev1`/`clef`/`clef-flash`) surface decision rows the same way and resolve keylessly at funnel time (explicit sentinel key — the official SDK requires a non-empty key).
- **Secrets:** keys follow the existing encrypted-at-rest pattern (`encrypt_secret` on `ai_providers.api_key`) — never plaintext, never logged; preset bases are SSRF-guarded like the manual path. Setup touches catalogs only — no PHI flows through it, and no telemetry captures prompts or keys during setup.
- **Errors** are classified into a stable i18n-backed enum (`invalid_key | insufficient_credit | new_user_quota | region_unavailable | timeout | local_not_running | unknown`) with `suspected_vendor` mis-paste hints; the endpoint returns `{code, suspected_vendor, message}` on 422 and the UI routes them through i18n.
- **Model capabilities** use the standard platform vocabulary `text | vision | tools | stt | tts | embeddings | decision`. Transcription is the `stt` capability; `ai_providers.preset_key` (nullable; NULL = manual row) records the provider preset. `ai_models.decision_transport` (`'systemone'` seam, NULL = not a decision model) and `ai_task_assignments.phi_allowed` (the PHI scope guard — decision assignments are PHI-excluded by default, explicit opt-in only) are part of `0001_baseline`. The `decision` capability is explicit-only (never inferred from a model name) and serves the four `decision_*` task slots exclusively.
- **Tests:** the uniform contract matrix — the 14 BYOK cases + the decision/probe/PHI cases (23 total) — lives in `backend/tests/test_ai_provider_setup_contract.py` (mock transport, no network); the funnel conformance suite in `backend/tests/test_decide_conformance.py`.

### Decision models

Decision models are ordinary provider models serving the four `decision_*` task slots through the **decide funnel** (`backend/app/ai/decide/`) — never through the chat/OCR/NLP factories:

- **Transport registry:** one registration per decision transport (`systemone` via `typesafe-sdk`, AI-layer confined). Resolution uses the SAME precedence chain as every other task (specialist kind primary → specialist kind fallback → the `decision` capability default → unavailable; scope cascade USER > TENANT > SYSTEM) through `providers/resolution.py`; an unassigned slot is a resolution result (`off`), never an error, and resolution never falls back to the `default` **chat** task (D4 slot separation — the `decision` default slot is a different task).
- **D2b capability routing is enforced:** a model serves a slot only when it advertises `decision` AND its transport declares the slot's capability; `decision_open_args` is vocabulary-only and never assignable.
- **PHI scope guard (health-specific):** decision assignments are PHI-excluded by default (`ai_task_assignments.phi_allowed = false`). A decision dispatch marked `phi_adjacent` resolves only assignments carrying the explicit opt-in; otherwise the decision point does not run (fail-open). The opt-in lives on the task-assignment API + the settings checkbox — setup gap-fill and set-default never set it.
- **Fail-open + audit:** an unassigned/failing slot means the decision point does not run (byte-identical to pre-feature behavior). Every invocation writes **one `ai_call_logs` row** (route `decision`, task id, provider/model ids, latency, token usage, status, trace id) — the structured log line stays as a live diagnostic, the table is the durable audit (see § *Per-AI-call audit*).
- **UI:** the *Decision models* group in AI settings (task assignment) carries the verbatim cloud privacy notice at first assignment; setup's re-run review offers the `bind_decision` fill switch when the preset declares decision models.

---

## 3. Generic Processors (Dependency Injection)

Both `LangChainOCRProcessor` and `LangChainStructuredExtractor` are provider-agnostic and receive their chat model **injected** — they never construct one themselves. `AIProviderService.get_ocr_processor` / `get_nlp_extractor` resolve the scoped task assignment and build the `BaseChatModel` through the factory (`app/ai/chat_models.py`), as does every other model consumer via `get_llm`.

### Supported Providers (via LangChain)
- **OpenAI-Compatible APIs:** Native support (Vision + Structured Output + Tool Calling). This includes direct OpenAI usage, or Local LLMs (vLLM, Ollama, etc.) by configuring the `api_base` in the UI with `provider_type: openai` (Ollama's OpenAI-compatible endpoint).
- **Reserved, not wired:** `anthropic`, `azure_openai`, `bedrock`, `ollama` have `ProviderType` members but their builders raise `NotImplementedError`. Provider/model write endpoints now **refuse them at write time** with `422 {"code": "provider_type_unwired", "message": …}` (`app/ai/providers/registry.py::UNWIRED_PROVIDER_TYPES`), so no configuration row can advertise a provider that cannot run; enabling one is a factory branch + a set removal in the same change. The BYOK preset overlay reflects the same state — `anthropic`/`gemini` are disabled with recorded reasons in `DISABLED_PRESET_REASONS`.

---

## 4. Agentic Chatbot Architecture

Health Assistant features a deeply integrated Agentic AI Copilot designed to provide interactive clinical insights. 

### Tool-Calling Capabilities
The `app/ai/tools/` package dynamically binds functions to the LangChain model based on the current `tenant_id` and `patient_id` (via `get_tools(db, tenant_id, patient_id, examination_id=None, user_id=None)`). Individual tools are registered with the `@register_chat_tool` decorator and receive a `ToolContext`. These tools allow the LLM to:
- **Search Available Biomarkers:** Discover metrics, IDs, and data types (Telemetry vs Clinical) using hybrid search (trigram + full-text + alias matching, fused via Reciprocal Rank Fusion). Matches name/slug/code AND aliases (so "TSH" finds "Thyroid Stimulating Hormone") AND description/info text (so "blood sugar" finds biomarkers whose description mentions it).
- **Search Medications:** Discover drugs in the medication catalog using hybrid search (trigram + FTS over name/description/indications). Symptom-style queries work: "headache" finds drugs whose indications mention headache.
- **Search Allergens:** Discover allergens in the allergy catalog using hybrid search (trigram + FTS over name/description/typical_reactions). Symptom queries work: "hives" finds allergens whose typical reactions mention hives.
- **Search Catalogs:** Cross-catalog discovery in one call — returns globally RRF-ranked hits across biomarkers, medications, vaccines, allergies, anatomy, diseases, and clinical event types, each with a `matched_on` provenance list + a `ts_headline` snippet showing the match context.
- **Explore Catalog Relations:** Multi-hop knowledge-graph traversal ("which organ does this biomarker affect?", "what diseases does this vaccine prevent?"). Token-safe: `max_depth` clamped to 1–3, optional `types` filter (e.g. only anatomy neighbors), and a `truncated` flag when the edge cap (150) is hit so the LLM can narrow further.
- **Fetch Aggregated Trends:** Query TimescaleDB for high-frequency telemetry (Heart Rate, Steps) with OHLC downsampling using UUIDs.
- Read raw OCR document extractions.
- Fetch recent biomarker values and historical trends (for discrete clinical labs using UUIDs).
- Query active medications, allergies, and catalogs.
- Look up specific clinical visit details and notes.
- Write updates to examination notes.
- **Propose clinical write actions** (human-in-the-loop): render review cards for creating clinical events, adding biomarker results to an examination, adding medications, recording allergies, etc. The user must explicitly confirm; the AI never writes directly (see §4.1 below).

### Streaming & Context Awareness
The `AIAssistanceService` runs a dynamic reasoning **graph** (defaulting to 20 iterations) that lets the LLM recursively call tools before returning a final answer. Responses are streamed via SSE to the frontend, complete with inline citations linking back to the precise clinical entities.

### Reasoning engine
A single LangGraph StateGraph (`app/ai/graphs/chat_agent.py`): `agent_step` (LLM + streamed deltas) → `tool_exec` (tool execution, HITL interception, proactive persistence) → conditional routers (`max_iterations` cap) → `finalize` (final save). Custom StateGraph rather than `create_agent`: the HITL trimmed-feedback interception and conditional save semantics are not hostable in middleware without behavior risk. The legacy `run_reasoning_loop` generator was decommissioned (deleted together with the `AI_AGENT_ENGINE` flag/dial).

**Streaming vocabulary (one family contract):** the chat stream emits the standard flow-event vocabulary — `flow_started`, `node_started`/`node_finished`, `delta` (text), `tool_call` (with `id`/`status`/`args`/`result`), `flow_finished`, `flow_failed` (with `code`/`message`/`retryable`) — plus health's payload events: `citation` (a data-source tool name), `task` (an interactive HITL card), `interrupt` (the resumable ask_user pause; health deliberately does **not** emit the terminal `flow_interrupted` — there is no server-broadcast stop), and `session` (a freshly auto-created session id). Every SSE frame is one JSON object with the event name in `event`; the closed set lives in `app/ai/flow_events.py` (`FlowEvent`), pinned by `tests/test_chat_flow_events.py`. The chat UI renders a live `FlowStatusCard` while a graph turn runs, hosting the interactive HITL review card in the card's detail slot; persisted cards render exactly as before once the turn completes. The former sentinel protocol (`[TOOL_CALL_*]`, `[CITATION]`, `[HITL_TASK]` in content) and its `?flow_events` gate were deleted outright — no backwards compatibility.

### HITL interrupts (graph engine)
`ask_user` **pauses the turn** at the question card (terminal-card SSE): the card + assistant message are persisted proactively (survives restarts — the run is checkpointed on the turn's thread via the lifespan-held store), then the side-effect-free `await_user` node raises `interrupt()`. `/resume` delivers the resolution summary as `Command(resume=...)` — the answers land as the `ask_user` `ToolMessage` and the model continues the same conversation; `HitlTask` rows in `chat_messages.tasks` + idempotent 409s stay the only dedup. `propose_*` keeps continue-after-propose. Fault tolerance (ratified): `agent_step` retries transient `ConnectionError`/`TimeoutError`; `tool_exec` is pinned to a single attempt (clinical tools are not idempotent); errors bubble to the SSE error classifier. Checkpointed state is serializable-only — the LLM runnable, tools, and service ride in `config.configurable` and are re-supplied by the resume caller.

### Chat branching (family branching contract)
Messages form a branch tree per session (`chat_messages.parent_id` + per-node `active_child_id`, `chat_sessions.active_root_id`; service-enforced pointers, no FKs). The visible conversation is the active-path walk; `GET /sessions/{id}/messages` returns it decorated with variant info (`parent_id`, `variant_index`, `variant_count`, `sibling_ids`), and history reconstruction replays the walk (never raw row order, never checkpoints). Branch endpoints follow the frozen family shapes: `POST /messages/{id}/edit` (new user sibling + pointer flip + fresh turn; the old subtree stays a switchable variant), `POST /messages/{id}/regenerate` (new assistant sibling under the target user message — never overwrite), `POST /messages/{id}/select` (level-flip exactly one pointer, returns the re-walked path), `GET /sessions/{id}/tree` (read-only projection).

**Thread strategy (health ruling, the family branching contract):** a turn's LangGraph thread is derived from its branch — the bare session id on the un-branched spine (legacy threads and pre-H2 pauses keep working), `{session_id}:{edited.id}` on an edit branch (root-most user-marker rule), or `{session_id}:{reroll_anchor.id}` for regenerated lineages; the resume side derives the SAME key from the card-bearing message (cards attach to messages, not thread state), so a paused `ask_user` on one branch survives turns streamed on any other. **Per-session single-flight turn lock:** one streaming turn per session at a time — the lock is per-session, not per-branch; a concurrent turn on any branch is rejected with 409 before any row is written. **Turn admission vs abandoned pauses:** a fresh turn never submits input onto a thread paused at an ask_user interrupt (LangGraph would apply the input as a state update and re-pause the stale task, swallowing the turn); instead the turn supersedes the abandoned pause (thread deleted, turn runs clean) and the still-visible card degrades to the existing fresh-continuation resume path. Re-rolls under the same user message share that anchor's thread — a re-roll deliberately supersedes its predecessor's pause.

### 4.1 Human-in-the-Loop (HITL) Proposals

The chatbot can **propose** write actions via the `propose_*` tool family. **The AI never writes clinical data.** A `propose_*` tool only builds a draft and returns a `{"__hitl__": true, "task": {...}}` marker; the streaming layer detects it, **proactively saves** the card immediately (surviving stream interruptions), and emits a `task` payload event; the frontend renders a **review card** (compact summary) which opens a **modal** with the full prefilled form; the human edits and explicitly **Approves**, which commits through the **canonical, tenant-scoped, RBAC-enforced REST endpoint** (identical to a manual create). A dedicated `/resolve` endpoint then records the outcome (`confirmed` / `dismissed`) — it performs no writes.

**One proposal store (schema plan 5).** A card is a row in the `proposals` table — `source='chat'`, `source_ref` = the assistant message id, `ordinal` = card order — never JSONB on the message (`chat_messages.tasks` is gone, with no replacement column). `kind_id` points at the seeded `proposal_kinds` vocabulary (`create_event`, `create_examination`, `add_medication`, `add_biomarker_to_examination` plus the catalog/chat kinds); `kind.card_variant` is the family HITL card-contract key the UI registers a handler under, so the card contract is unchanged. `payload` is the versioned card document (`payload_schema_version`), `final_payload` the user-approved shape (validated against the kind's contract schema before any server-side write), and `resolution_trace` the audit trail (who resolved what when). Extension proposals land in the same table with `source='extension'` and `source_ref` = the integration instance id; approving one runs the kind's server-side resolver and transitions `proposed → approved → applied` (or `failed`).

**State machine + idempotent resolve.** The closed state machine is
`proposed | approved | rejected | applied | failed | expired | superseded`,
enforced in one module (`app/services/proposals/proposal_state.py`) with typed errors.
Resolve is idempotent: repeating the recorded outcome returns the stored result
(200); a conflicting decision on a terminal row is `409`. An expiry sweep
(`app.workers.tasks.expire_proposals`, 15-minute beat) resolves stale pending
proposals to `expired`.

**Auto-resume continuation:** after the user resolves task card(s), the agent automatically gets a **continuation turn** via `POST /sessions/{id}/resume`. The backend reads the resolved outcomes from the `proposals` rows (never trusted from the client), builds a structured `[HITL RESOLUTION FEEDBACK]` message with per-card status/result/error, and streams a new response. `final_payload` enters that prompt only through the screening seam (`screen_hitl_value`: control-character strip, whitespace collapse, length bounds, prompt-injection neutralization) — user-edited draft data is untrusted input. The agent acknowledges what was saved, can chain dependent proposals (e.g. define biomarker → add it to exam), and respects dismissed items. Guardrails: fires at most once per message (`resumedMessageIds` ref), race-gated against concurrent user messages, suppressed on session reloads.

**Continue button (partial resume):** if the user answers some cards but leaves others pending, a "Continue (N unanswered)" button appears. Clicking it triggers the resume with partial answers — the LLM is told which items were skipped and instructed not to auto-repropose them. When ALL cards are resolved, auto-resume fires immediately (no button needed).

**Parallel proposals:** the agent may emit multiple independent `propose_*` calls in a single turn (e.g. "add medications X, Y, Z" → three parallel review cards). Dependent actions (one requires another to be committed first) are split across turns — the auto-resume handles the chaining naturally.

**Current task types:**

| Task type | Tool | Status |
|---|---|---|
| `create_clinical_event` | `propose_create_clinical_event` | shipped |
| `add_biomarker_to_examination` | `propose_record_biomarker_result` | shipped |
| `add_medication` (+ schedule) | `propose_prescribe_medication` | shipped |
| `add_allergy` | `propose_record_allergy` | shipped |
| `create_biomarker_definition` | `propose_define_biomarker` | shipped |
| `create_medication_definition` | `propose_define_medication` | shipped |
| `create_allergy_definition` | `propose_define_allergy` | shipped |
| `generate_anatomy_graph` | `propose_anatomy_graph_generation` | shipped (AI-defined anatomy sub-graphs via the `define_anatomy_graph` assistance task + `/anatomy/import` pipeline. Dedup at two layers: the LLM is prompted to search the existing catalog first (`search_catalogs` + `explore_catalog_relations`), and the tool embeds a pre-flight existing-graph snapshot so the generation fills gaps. The graph is generated at **proposal time** so the review card opens instantly; the frontend renders a **Graph/Table view** with proposed nodes (indigo) vs existing nodes (slate) color-coded. SYSTEM_ADMIN-gated. See [SEEDING_AND_DEMOS.md §6.4](SEEDING_AND_DEMOS.md#64-ai-driven-graph-expansion)) |
| `create_examination` | — | deferred (exams are upload-driven; low chat value) |

> **Naming note:** the tool *function* names use scope-explicit verbs
> (`define_*` for catalog definitions, `prescribe_*`/`record_*` for
> patient-instance writes) so the LLM picks the right one. The `task_type`
> routing strings (left column) stay stable for alignment with the integration
> SDK's `proposal_type` discriminator.

**Catalog vs instance disambiguation:** the system prompt has a dedicated section that pairs the verbs and gives the LLM concrete trigger phrases ("X isn't in the system yet" → `propose_define_*`; "I'm taking X" / "my latest Y was Z" → `propose_prescribe_*` / `propose_record_*`). When intent is genuinely ambiguous the LLM is told to ASK one clarifying question rather than guess wrong.

**Link proposals (create + link in one confirmation):** six of the seven write-action `propose_*` tools accept an optional `links: List[dict]` argument — `propose_create_clinical_event`, `propose_prescribe_medication`, `propose_define_biomarker`, `propose_define_medication`, `propose_record_allergy`, `propose_define_allergy` (only `propose_record_biomarker_result` lacks it — a biomarker observation is a row on an exam, not a graph node).

- Each link item is `{dst_type, dst_id, relation, properties?}` referencing an existing catalog item or instance.
- The backend validates each link against `LINK_SCHEMA` — a `(src_type, dst_type) → [ConceptRelationType]` matrix in `app/ai/tools/propose_link.py` (single source of truth) — resolves the destination endpoint via `concept_endpoint_resolver`, and stores `{dst, relation, properties, duplicate_of}` under `proposed_payload.links`.
- Invalid combinations are silently dropped (drop-and-report — the kept-vs-dropped count surfaces in the tool result so the LLM self-corrects on the next turn).
- The LLM discovers valid pairings via the `get_link_schema` tool; the frontend `<LinksSection>` component uses `GET /api/v1/concept-edges/schema` to filter destination + relation dropdowns per the same matrix. Approve triggers the primary create, then `createLinksFor(srcType, newId, links)` POSTs each edge — all in one user confirmation.

The family HITL card-contract **wire** status is `HitlTaskStatus` (`PROPOSED | CONFIRMED | FAILED | DISMISSED`) with a `terminal()` classmethod; the frontend mirrors it via `TERMINAL_HITL_STATUSES` in `registry.tsx`. Persistence uses the richer seven-state `ProposalStatus` machine, mapped in `app/services/proposals/proposal_state.py` (`applied→confirmed`, `rejected/expired/superseded→dismissed`).

Adding a task type touches **none** of the protocol, endpoints, or the DB schema — one `propose_*` tool, one frontend handler, one registry line, one entry in `contracts/hitl-proposal-kinds.json` (boot-seeded as a `proposal_kinds` row), and (usually) one extracted headless form. The full recipe + the `task` payload event / `__hitl__` / resume contract are described in this section.

**Security model:** AI proposes, human discharges; untrusted prefill is re-validated client- *and* server-side; `/resolve` is idempotent (409 on re-resolve) and verifies session ownership; `/resume` reads outcomes from the DB (never trusts the client) and verifies ownership; the full proposed-vs-committed diff is audited in the `ChatMessage.tasks` JSONB column; resume summaries trim large payloads to short identifying fields only (no PHI leakage).

### 4.2 Asking Clarifying Questions (`ask_user`)

The HITL proposal cards in §4.1 are write-action drafts. When the LLM needs **information** from the user before it can proceed — picking the right biomarker to link, choosing which of several missing catalog items to create, or clarifying a free-text detail — it calls `ask_user`, a sibling HITL task type that renders an **inline question card** in the chat scrollback. The user answers, submits, and the agent receives the structured answers on the next turn via the standard `[HITL RESOLUTION FEEDBACK]`.

**Question kinds (closed set):**

| Kind | UI primitive | Answer shape |
|---|---|---|
| `freetext` | textarea / input | `string` |
| `single_choice` | radio list | `string` (chosen `value`) |
| `multi_choice` | checkbox list (with min/max) | `string[]` |
| `catalog_ref` | catalog picker (reuses `CatalogItemPicker`) | `{id, name, slug, type}` or `null` (array if `multi`) |
| `instance_ref` | patient-scoped instance picker (reuses `InstancePicker`) | `{id, ...}` or `null` (array if `multi`) |

**Hard caps** keep the LLM's payload + the resume prompt within sane token budgets: ≤8 questions per `ask_user` call, ≤12 options per choice question, ≤6 candidates per ref question, freetext answers trimmed to 200 chars in the resume summary.

**Server-side candidate snapshot.** For `catalog_ref` / `instance_ref` questions, the tool pre-resolves the top candidates server-side and embeds them under `question.candidates`. The frontend pickers render them on the initial query (zero round-trip on open); the user can re-query via debounced live search.

**Read-only and notification-free.** Unlike `propose_*`, an `ask_user` resolution performs no REST write — the answers go to the LLM only (the "AI never writes" model is preserved). It also skips the inbox notification — questions are conversational, not work-to-clear.

**Inline rendering with shared pickers.** The `ask_user` handler is registered with `inline: true` in the frontend HITL registry — the form renders directly in the card body (no modal, no "Review & Edit" button). The form owns its full footer (Submit / Skip). The `catalog_ref` / `instance_ref` question kinds render via the **existing** `CatalogItemPicker` and `InstancePicker` (the same components used elsewhere in the app) so the popover portals above the card and the UX stays consistent. `clinical_event_type` is mapped to `concept` + `conceptKind='event_category'` on the frontend; `instance_ref.entity_type='clinical_event'` is mapped to the frontend `InstanceType='event'`.

**Composition with `propose_*`.** `ask_user` and `propose_*` tasks may co-exist in the same turn (the reasoning loop already accumulates tasks in `all_tasks`). This enables the multi-step creation pattern below.

**Multi-step creation (primary entity + related links):** when the user wants to create a primary entity that should link to related concepts/biomarkers (e.g. a medication that TREATS a disease, AFFECTS a biomarker), the LLM uses a 4-turn pattern powered by the `discover_missing_related` discovery tool:

1. **DISCOVER** — `discover_missing_related(primary_type, primary_name, related=[{type, name, suggested_relation}, ...])` returns which items exist and which are missing, in one round-trip.
2. **ASK** — if any related items are missing, emit ONE `ask_user` with a `multi_choice` question listing them (label=name, detail=type + suggested_relation). The user picks the subset.
3. **DEFINE** — on the resume turn (with the picks), emit parallel `propose_define_*` calls for the chosen missing items. STOP.
4. **LINK** — on the second resume turn (with all defines confirmed and their ids), emit the primary `propose_define_*` with `links[]` populated from the confirmed ids + the `suggested_relation` values.

Adding `ask_user` touched **none** of the protocol — same `__hitl__` marker, same `task` payload event, same `tasks` JSONB, same `/resolve` + `/resume`. The only new code is one tool (`backend/app/ai/tools/ask_user.py`), one frontend handler (`frontend/src/components/ai/hitl/handlers/AskUserHandler.tsx`), and the `inline` flag on the registry's `HitlTaskHandler`. No new picker component — the existing `CatalogItemPicker` and `InstancePicker` are reused so popovers portal correctly and the UX matches the rest of the app.

---

## 5. How to Create New AI Functionalities

### Step 1: Define the Schema
All structured AI outputs must be defined as Pydantic models in `backend/app/ai/schemas/nlp.py`.

```python
class RadiologySummary(BaseModel):
    findings: str
    impression: str
    suggested_followup_days: int
```

### Step 2: Register a New Task Type
Add the value to the `TaskType` enum in `backend/app/ai/providers/enums.py`. `TaskType` is the single source of truth — `AIProviderService.get_config_summary` iterates `TaskType.all_values()` to populate the assignment matrix in the UI (`/settings/ai-config`), so a new enum value automatically appears as an assignable task. Do **not** edit any list inside `get_config_summary` — that pattern was removed.

### Step 3: Implement the Service Logic
Add a method to `MedicalProcessingService` (`backend/app/ai/pipeline/service.py`) to handle the specific clinical logic.

```python
async def process_radiology(self, text: str, tenant_id: UUID):
    llm = await self.ai_provider_service.get_llm("radiology_summary", tenant_id)
    structured_llm = llm.with_structured_output(RadiologySummary)
    return await structured_llm.ainvoke(text)
```

### Step 4: Create the Celery Task
Define the background task in `backend/app/workers/ai_tasks.py` using the `@async_task` decorator.

```python
@celery_app.task(bind=True)
@async_task
async def summarize_radiology(self, document_id: str):
    db, engine = get_async_session()  # session is fresh; engine is worker-scoped singleton
    try:
        async with db:
            service = MedicalProcessingService(db)
            # ... fetch doc, run service, update DB ...
    finally:
        await db.close()  # never call engine.dispose() per task — it's shared
```

---

## 6. Background Task Patterns

We use an `@async_task` decorator to bridge Celery's synchronous execution with FastAPI's asynchronous logic:

```python
def async_task(func):
    """Handles event loop management."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(func(*args, **kwargs))
        finally:
            loop.close()
    return wrapper
```

**DB engine pattern (important)**: `get_async_engine()` returns a **worker-scoped singleton** `AsyncEngine` (lazy-init, thread-safe) using `poolclass=NullPool`. `get_async_session()` binds a fresh session to that shared engine. Each task creates its own session and closes it in a `finally` block (`await db.close()`); the engine is **never** disposed per-task — it's disposed only on Celery's `worker_process_shutdown` signal.

`NullPool` is what makes this safe alongside per-task event loops: every session checks out a fresh DB connection and closes it on session close, so no asyncpg connection ever outlives the loop that created it. Without this, asyncpg protocol transports bound to a previous task's closed loop would resurface in the next task via the shared pool as `RuntimeError: Event loop is closed` / `Future attached to a different loop`.
