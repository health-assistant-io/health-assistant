"""Service-layer package.

Historical re-exports were removed (no callers used them — all imports go
through direct module paths like ``from app.services.analytics import
get_biomarker_trends``). New services should be imported by path, not added
here.
"""
