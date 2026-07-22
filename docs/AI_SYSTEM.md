# AI System & Configuration Guide

This document describes the design, configuration, and extensibility of the AI processing system in Health Assistant.

## 1. Core Architecture

The AI system is built on a **Unified Factory Pattern**, decoupling clinical logic from specific AI providers.

### Key Components

| Component | Responsibility |
|-----------|----------------|
| `AIProviderService` (`app/ai/providers/service.py`) | Central "Brain" for model resolution. Handles multitenancy, priorities, and model instantiation. |
| `LangChainOCRProcessor` (`app/ai/processors/ocr/`) | Generic vision processor that converts images/PDFs/DICOMs into Markdown text. |
| `LangChainStructuredExtractor` (`app/ai/processors/nlp/`) | Generic NLP extractor that maps Markdown text to structured FHIR medical entities. |
| `AIAssistanceService` (`app/ai/assistance/service.py`) | Orchestrator for the Agentic Chatbot and "Magic Fill" features. Manages session context and routing. |
| `app/ai/agents/` | The agentic loop + HITL plumbing: `chat_agent.py` (reasoning loop, tool dispatch, streaming), `hitl.py` (resume-continuation contract + `[HITL RESOLUTION FEEDBACK]` formatting), `prompts.py` (system prompt assembly). |
| `app/ai/tools/` | LangChain tools (DB queries, document retrieval) that the AI assistant can invoke via `get_tools(db, tenant_id, patient_id, examination_id=None)`. |
| `MedicalProcessingService` (`app/ai/pipeline/service.py`) | Orchestrator for complex clinical logic (unit conversion, ontology matching, persistence). |
| `app/workers/ai_tasks.py` | Celery worker task definitions that delegate to the above services. |

---

## 2. Configuration & Model Resolution

Health Assistant uses a strict, database-driven configuration for all AI processors. This ensures consistency and auditability across multitenant deployments.

### Data Models
- **Providers:** Define the API endpoint and credentials (e.g., OpenAI, custom vLLM server).
- **Models:** Define the specific model string (e.g., `gpt-4o`, `gemini-1.5-pro`), context window (`max_tokens`), and `temperature`. Multiple models can belong to a single provider.
- **Task Assignments:** Map specific application tasks (e.g., `ocr`, `nlp`, `medication_interaction`) to a specific Provider + Model combination.

### Resolution Logic
When a task (e.g., `ocr`) is executed for a specific `tenant_id`:
1. **Tenant Assignment**: Check for a specific task assignment for that tenant and task type.
2. **Global Assignment**: If none, check for a system-wide task assignment (where `tenant_id` is NULL).
3. **Global Default Fallback**: Look for an assignment with the task type `default`.

*Note: While database configuration is strongly recommended for production, the processors (OCR and NLP) will safely fall back to environment variables (e.g., `OPENAI_API_KEY`, `OCR_PROVIDER`) if no database assignments are found.*

### UI Management
Administrators can manage AI settings via the UI at `/settings/ai-config`:
1. **Add a Provider:** Specify the API Base URL and API Key.
2. **Add Models:** For each provider, define available models, token limits, and desired temperature (e.g., `0.7` for NLP, `0.0` for structured extraction).
3. **Assign Tasks:** Map system tasks to the desired model.

### API Key Security
The `api_key` field has special handling that other provider fields do not:

- **At rest:** values are encrypted with Fernet via `INTEGRATION_SECRET_KEY` (the same key used by integrations/MCP secrets). Encrypted values are stored with an `enc::` prefix so legacy plaintext rows continue to work during migration. `INTEGRATION_SECRET_KEY` is required in production — `encrypt_secret` **fails closed** (raises `RuntimeError` rather than storing cleartext) when it is unset outside dev/test (audit A11); dev/test keeps a loud-warning plaintext fallback.
- **On response:** every `AIProviderResponse` / `AIProviderWithModelsResponse` masks the key to `***<last4>` via a `model_validator(mode="after")`. The companion boolean `has_api_key` indicates whether a key is configured. The plaintext is never serialized over the API.
- **On write:** `AIProviderService.create_provider` encrypts any provided key. `update_provider` treats the following as "no change" to preserve the existing key: `api_key` absent (via `exclude_unset`), `api_key=None` (explicit clear), `api_key=""` (explicit clear), `api_key="***xxxx"` (the masked form the UI re-sent). Any other string is encrypted and stored.
- **At use:** the LLM factory reads plaintext via `AIProviderModel.get_api_key_plaintext()` — the only sanctioned accessor. `AIProviderModel.to_dict()` returns the encrypted form so accidental serialization paths (logs, audit trails) never leak it.

**Backfill existing plaintext rows** after enabling API-key encryption:
```bash
cd backend && PYTHONPATH=. python scripts/encrypt_existing_api_keys.py --dry-run
cd backend && PYTHONPATH=. python scripts/encrypt_existing_api_keys.py
```

**Scope checks:** every `/ai-config/providers/*` and `/ai-config/models/*` endpoint enforces USER/TENANT/SYSTEM scope via `verify_provider_access` / `verify_model_access`. `fetch-external-models` additionally rejects `api_base` values that point at loopback / private / link-local addresses in production (`DEBUG=False`).

---

## 3. Generic Processors (Dependency Injection)

Both `LangChainOCRProcessor` and `LangChainStructuredExtractor` are designed to be provider-agnostic. They receive a pre-configured `BaseChatModel` from the factory.

### Supported Providers (via LangChain)
- **OpenAI-Compatible APIs:** Native support (Vision + Structured Output + Tool Calling). This includes direct OpenAI usage, or Local LLMs (vLLM, Ollama, etc.) by configuring the `api_base` in the UI.
- **Anthropic**: Support via `langchain-anthropic` (requires adding dependency).

---

## 4. Agentic Chatbot Architecture

Health Assistant features a deeply integrated Agentic AI Copilot designed to provide interactive clinical insights. 

### Tool-Calling Capabilities
The `app/ai/tools/` package dynamically binds functions to the LangChain model based on the current `tenant_id` and `patient_id` (via `get_tools(db, tenant_id, patient_id, examination_id=None)`). Individual tools are registered with the `@register_chat_tool` decorator and receive a `ToolContext`. These tools allow the LLM to:
- **Search Available Biomarkers:** Discover metrics, IDs, and data types (Telemetry vs Clinical) using hybrid search (trigram + full-text + alias matching, fused via Reciprocal Rank Fusion). Matches name/slug/code AND aliases (so "TSH" finds "Thyroid Stimulating Hormone") AND description/info text (so "blood sugar" finds biomarkers whose description mentions it).
- **Search Medications:** Discover drugs in the medication catalog using hybrid search (trigram + FTS over name/description/indications). Symptom-style queries work: "headache" finds drugs whose indications mention headache.
- **Search Allergens:** Discover allergens in the allergy catalog using hybrid search (trigram + FTS over name/description/typical_reactions). Symptom queries work: "hives" finds allergens whose typical reactions mention hives.
- **Search Catalogs:** Cross-catalog discovery in one call — returns globally RRF-ranked hits across biomarkers, medications, vaccines, allergies, anatomy, diseases, and clinical event types, each with a `matched_on` provenance list + a `ts_headline` snippet showing the match context.
- **Explore Catalog Relations:** Multi-hop knowledge-graph traversal ("which organ does this biomarker affect?", "what diseases does this vaccine prevent?").
- **Fetch Aggregated Trends:** Query TimescaleDB for high-frequency telemetry (Heart Rate, Steps) with OHLC downsampling using UUIDs.
- Read raw OCR document extractions.
- Fetch recent biomarker values and historical trends (for discrete clinical labs using UUIDs).
- Query active medications, allergies, and catalogs.
- Look up specific clinical visit details and notes.
- Write updates to examination notes.
- **Propose clinical write actions** (human-in-the-loop): render review cards for creating clinical events, adding biomarker results to an examination, adding medications, recording allergies, etc. The user must explicitly confirm; the AI never writes directly (see §4.1 below).

### Streaming & Context Awareness
The `AIAssistanceService` utilizes a dynamic reasoning loop (defaulting to 20 iterations) to allow the LLM to recursively call tools before returning a final answer. Responses are streamed via WebSockets or Server-Sent Events (SSE) to the frontend, complete with inline citations linking back to the precise clinical entities.

### 4.1 Human-in-the-Loop (HITL) Proposals

Beyond the single direct write tool (`update_examination_notes`), the chatbot can **propose** write actions via the `propose_*` tool family. **The AI never writes clinical data.** A `propose_*` tool only builds a draft and returns a `{"__hitl__": true, "task": {...}}` marker; the streaming layer detects it, **proactively saves** the task immediately (surviving stream interruptions), and emits a `[HITL_TASK]` SSE event; the frontend renders a **review card** (compact summary) which opens a **modal** with the full prefilled form; the human edits and explicitly **Approves**, which commits through the **canonical, tenant-scoped, RBAC-enforced REST endpoint** (identical to a manual create). A dedicated `/resolve` endpoint then records the outcome (`confirmed` / `dismissed`) into the chat audit log — it performs no writes.

**Auto-resume continuation:** after the user resolves task card(s), the agent automatically gets a **continuation turn** via `POST /sessions/{id}/resume`. The backend reads the resolved outcomes from `tasks` JSONB (never trusted from the client), builds a structured `[HITL RESOLUTION FEEDBACK]` message with per-task status/result/error, and streams a new response. The agent acknowledges what was saved, can chain dependent proposals (e.g. define biomarker → add it to exam), and respects dismissed items. Guardrails: fires at most once per message (`resumedMessageIds` ref), race-gated against concurrent user messages, suppressed on session reloads.

**Continue button (partial resume):** if the user answers some cards but leaves others pending, a "Continue (N unanswered)" button appears. Clicking it triggers the resume with partial answers — the LLM is told which items were skipped and instructed not to auto-repropose them. When ALL cards are resolved, auto-resume fires immediately (no button needed).

**Parallel proposals:** the agent may emit multiple independent `propose_*` calls in a single turn (e.g. "add medications X, Y, Z" → three parallel review cards). Dependent actions (one requires another to be committed first) are split across turns — the auto-resume handles the chaining naturally.

**Current task types:**

| Task type | Tool | Status |
|---|---|---|
| `create_clinical_event` | `propose_create_clinical_event` | ✅ shipped |
| `add_biomarker_to_examination` | `propose_record_biomarker_result` | ✅ shipped |
| `add_medication` (+ schedule) | `propose_prescribe_medication` | ✅ shipped |
| `add_allergy` | `propose_record_allergy` | ✅ shipped |
| `create_biomarker_definition` | `propose_define_biomarker` | ✅ shipped |
| `create_medication_definition` | `propose_define_medication` | ✅ shipped |
| `create_allergy_definition` | `propose_define_allergy` | ✅ shipped |
| `generate_anatomy_graph` | `propose_anatomy_graph_generation` | ✅ shipped (AI-defined anatomy sub-graphs via the anatomy import pipeline; see [SEEDING_AND_DEMOS.md §6.4](SEEDING_AND_DEMOS.md#64-ai-driven-graph-expansion)) |
| `create_examination` | — | ⏸ deferred (exams are upload-driven; low chat value) |

> **Naming note:** the tool *function* names use scope-explicit verbs
> (`define_*` for catalog definitions, `prescribe_*`/`record_*` for
> patient-instance writes) so the LLM picks the right one. The `task_type`
> routing strings (left column) stay stable for alignment with the integration
> SDK's `proposal_type` discriminator.

**Catalog vs instance disambiguation:** the system prompt has a dedicated section that pairs the verbs and gives the LLM concrete trigger phrases ("X isn't in the system yet" → `propose_define_*`; "I'm taking X" / "my latest Y was Z" → `propose_prescribe_*` / `propose_record_*`). When intent is genuinely ambiguous the LLM is told to ASK one clarifying question rather than guess wrong.

**Link proposals (create + link in one confirmation):** four of the six `propose_*` tools accept an optional `links: List[dict]` argument — `propose_create_clinical_event`, `propose_prescribe_medication`, `propose_define_biomarker`, `propose_define_medication`. Each item is `{dst_type, dst_id, relation, properties?}` referencing an existing catalog item or instance. The backend validates each link against `LINK_SCHEMA` (a `(src_type, dst_type) → [ConceptRelationType]` matrix in `app/ai/tools/propose_link.py` — single source of truth), resolves the destination endpoint via `concept_endpoint_resolver`, and stores `{dst, relation, properties, duplicate_of}` under `proposed_payload.links`. Invalid combinations are silently dropped (drop-and-report — kept vs dropped count surfaces in the tool result so the LLM self-corrects on the next turn). The LLM discovers valid pairings via the `get_link_schema` tool; the frontend `<LinksSection>` component uses `GET /api/v1/concept-edges/schema` to filter destination + relation dropdowns per the same matrix. The form's Approve click triggers the primary create, then `createLinksFor(srcType, newId, links)` POSTs each edge — all in one user confirmation.

Task statuses use the `HitlTaskStatus(str, Enum)` enum (`PROPOSED | CONFIRMED | FAILED | DISMISSED`) with a `terminal()` classmethod. The frontend mirrors this via `TERMINAL_HITL_STATUSES` in `registry.tsx`.

Adding a task type touches **none** of the protocol, DB, or endpoints — only one `propose_*` tool, one frontend handler, one registry line, and (usually) one extracted headless form. The full recipe + the `[HITL_TASK]`/`__hitl__`/resume contract are described in this section.

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

Adding `ask_user` touched **none** of the protocol — same `__hitl__` marker, same `[HITL_TASK]` sentinel, same `tasks` JSONB, same `/resolve` + `/resume`. The only new code is one tool (`backend/app/ai/tools/ask_user.py`), one frontend handler (`frontend/src/components/ai/hitl/handlers/AskUserHandler.tsx`), and the `inline` flag on the registry's `HitlTaskHandler`. No new picker component — the existing `CatalogItemPicker` and `InstancePicker` are reused so popovers portal correctly and the UX matches the rest of the app.

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
