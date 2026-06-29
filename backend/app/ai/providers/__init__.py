"""AI subsystem.

Aggregated home for all AI/LLM code. Subpackages:

- ``providers``  — provider/model/task-assignment config, enums, registry, factory
- ``processors`` — OCR / NLP extraction backends (moved in a later phase)
- ``pipeline``   — document extraction orchestration (moved in a later phase)
- ``agents``     — the chat agent reasoning loop + HITL (extracted in a later phase)
- ``tools``      — chatbot tools (split in a later phase)
- ``assistance`` — interactive form-fillers / icon generation (split in a later phase)
- ``schemas``    — Pydantic schemas for the AI subsystem (consolidated in a later phase)

This top-level ``__init__`` is intentionally EMPTY: no eager re-exports, to
avoid import cycles between ``agents`` <-> ``tools`` <-> ``providers``. Import
from the specific subpackage instead.
"""
