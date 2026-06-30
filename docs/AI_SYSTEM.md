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

### API Key Security (v0.3.0+)
The `api_key` field has special handling that other provider fields do not:

- **At rest:** values are encrypted with Fernet via `INTEGRATION_SECRET_KEY` (the same key used by integrations/MCP secrets). Encrypted values are stored with an `enc::` prefix so legacy plaintext rows continue to work during migration. Set `INTEGRATION_SECRET_KEY` in production — if unset, keys are stored in plaintext with a loud warning.
- **On response:** every `AIProviderResponse` / `AIProviderWithModelsResponse` masks the key to `***<last4>` via a `model_validator(mode="after")`. The companion boolean `has_api_key` indicates whether a key is configured. The plaintext is never serialized over the API.
- **On write:** `AIProviderService.create_provider` encrypts any provided key. `update_provider` treats the following as "no change" to preserve the existing key: `api_key` absent (via `exclude_unset`), `api_key=None` (explicit clear), `api_key=""` (explicit clear), `api_key="***xxxx"` (the masked form the UI re-sent). Any other string is encrypted and stored.
- **At use:** the LLM factory reads plaintext via `AIProviderModel.get_api_key_plaintext()` — the only sanctioned accessor. `AIProviderModel.to_dict()` returns the encrypted form so accidental serialization paths (logs, audit trails) never leak it.

**Backfill existing plaintext rows** after deploying v0.3.0:
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
- **Search Available Biomarkers:** Discover metrics, IDs, and data types (Telemetry vs Clinical) using trigram similarity search.
- **Search Medications:** Discover drugs in the medication catalog using trigram similarity search.
- **Fetch Aggregated Trends:** Query TimescaleDB for high-frequency telemetry (Heart Rate, Steps) with OHLC downsampling using UUIDs.
- Read raw OCR document extractions.
- Fetch recent biomarker values and historical trends (for discrete clinical labs using UUIDs).
- Query active medications and catalogs.
- Look up specific clinical visit details and notes.
- Write updates to examination notes.
- **Propose clinical write actions** (human-in-the-loop): render review cards for creating clinical events, adding biomarker results to an examination, adding medications, etc. The user must explicitly confirm; the AI never writes directly (see §4.1 below).

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
| `add_biomarker_to_examination` | `propose_add_biomarker_to_examination` | ✅ shipped |
| `add_medication` (+ schedule) | `propose_add_medication` | ✅ shipped |
| `create_biomarker_definition` | `propose_create_biomarker_definition` | ✅ shipped |
| `create_medication_definition` | `propose_create_medication_definition` | ✅ shipped |
| `create_examination` | — | ⏸ deferred (exams are upload-driven; low chat value) |

Task statuses use the `HitlTaskStatus(str, Enum)` enum (`PROPOSED | CONFIRMED | FAILED | DISMISSED`) with a `terminal()` classmethod. The frontend mirrors this via `TERMINAL_HITL_STATUSES` in `registry.tsx`.

Adding a task type touches **none** of the protocol, DB, or endpoints — only one `propose_*` tool, one frontend handler, one registry line, and (usually) one extracted headless form. The full recipe + the `[HITL_TASK]`/`__hitl__`/resume contract are described in this section.

**Security model:** AI proposes, human discharges; untrusted prefill is re-validated client- *and* server-side; `/resolve` is idempotent (409 on re-resolve) and verifies session ownership; `/resume` reads outcomes from the DB (never trusts the client) and verifies ownership; the full proposed-vs-committed diff is audited in the `ChatMessage.tasks` JSONB column; resume summaries trim large payloads to short identifying fields only (no PHI leakage).

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
Add the task type to the `task_types` list in `AIProviderService.get_config_summary` so it appears in the UI.

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
