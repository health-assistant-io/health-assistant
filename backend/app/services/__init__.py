"""Service-layer package.

Historical re-exports were removed (no callers used them — all imports go
through direct module paths like ``from app.services.anomaly_detector import
AnomalyDetector``). New services should be imported by path, not added here.
"""
