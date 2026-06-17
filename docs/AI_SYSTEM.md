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
- **Search Available Biomarkers:** Discover metrics, slugs, and data types (Telemetry vs Clinical) using Regex support.
- **Fetch Aggregated Trends:** Query TimescaleDB for high-frequency telemetry (Heart Rate, Steps) with OHLC downsampling.
- Read raw OCR document extractions.
- Fetch recent biomarker values and historical trends (for discrete clinical labs).
- Query active medications and catalogs.
- Look up specific clinical visit details and notes.
- Write updates to examination notes.

### Streaming & Context Awareness
The `AIAssistanceService` utilizes a dynamic reasoning loop (defaulting to 20 iterations) to allow the LLM to recursively call tools before returning a final answer. Responses are streamed via WebSockets or Server-Sent Events (SSE) to the frontend, complete with inline citations linking back to the precise clinical entities.

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
