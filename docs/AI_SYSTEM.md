# AI System & Configuration Guide

This document describes the design, configuration, and extensibility of the AI processing system in Health Assistant.

## 1. Core Architecture

The AI system is built on a **Unified Factory Pattern**, decoupling clinical logic from specific AI providers.

### Key Components

| Component | Responsibility |
|-----------|----------------|
| `AIProviderService` | Central "Brain" for model resolution. Handles multitenancy, priorities, and model instantiation. |
| `LangChainOCRProcessor` | Generic vision processor that converts images/PDFs/DICOMs into Markdown text. |
| `LangChainStructuredExtractor` | Generic NLP extractor that maps Markdown text to structured FHIR medical entities. |
| `AIAssistanceService` | Orchestrator for the Agentic Chatbot and "Magic Fill" features. Manages session context and routing. |
| `ChatbotTools` | Defines the LangChain tools (DB queries, document retrieval) that the AI assistant can invoke. |
| `MedicalProcessingService` | Orchestrator for complex clinical logic (unit conversion, ontology matching, persistence). |
| `tasks.py` | Celery worker task definitions that delegate to the above services. |

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
The `ChatbotTools` class dynamically binds functions to the LangChain model based on the current `tenant_id` and `patient_id`. These tools allow the LLM to:
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

Adding a task type touches **none** of the protocol, DB, or endpoints — only one `propose_*` tool, one frontend handler, one registry line, and (usually) one extracted headless form. Full recipe + the `[HITL_TASK]`/`__hitl__`/resume contract: see the **`hitl-task-cards`** skill.

**Security model:** AI proposes, human discharges; untrusted prefill is re-validated client- *and* server-side; `/resolve` is idempotent (409 on re-resolve) and verifies session ownership; `/resume` reads outcomes from the DB (never trusts the client) and verifies ownership; the full proposed-vs-committed diff is audited in the `ChatMessage.tasks` JSONB column; resume summaries trim large payloads to short identifying fields only (no PHI leakage).

---

## 5. How to Create New AI Functionalities

### Step 1: Define the Schema
All structured AI outputs must be defined as Pydantic models in `backend/app/schemas/ai_nlp.py`.

```python
class RadiologySummary(BaseModel):
    findings: str
    impression: str
    suggested_followup_days: int
```

### Step 2: Register a New Task Type
Add the task type to the `task_types` list in `AIProviderService.get_config_summary` so it appears in the UI.

### Step 3: Implement the Service Logic
Add a method to `MedicalProcessingService` to handle the specific clinical logic.

```python
async def process_radiology(self, text: str, tenant_id: UUID):
    llm = await self.ai_provider_service.get_llm("radiology_summary", tenant_id)
    structured_llm = llm.with_structured_output(RadiologySummary)
    return await structured_llm.ainvoke(text)
```

### Step 4: Create the Celery Task
Define the background task in `backend/app/workers/tasks.py` using the `@async_task` decorator.

```python
@celery_app.task(bind=True)
@async_task
async def summarize_radiology(self, document_id: str):
    async with AsyncSessionFactory() as db:
        service = MedicalProcessingService(db)
        # ... fetch doc, run service, update DB ...
```

---

## 6. Background Task Patterns

We use an `@async_task` decorator to bridge Celery's synchronous execution with FastAPI's asynchronous logic:

```python
def async_task(func):
    """Handles event loop management and database session safety."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(func(*args, **kwargs))
        finally:
            loop.close()
    return wrapper
```

This ensures that every background task has its own clean event loop and doesn't leak database connections.
