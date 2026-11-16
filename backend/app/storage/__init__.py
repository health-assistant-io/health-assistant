"""Family object-storage adoption (health host).

The vendored contract lives in :mod:`app.storage.object_storage` (synced
byte-identically from the family canonical; never edited here). This package
adds the health-specific seams:

* :mod:`app.storage.bootstrap` — resolve ``HA_*`` Settings into the contract,
  build/probe the process store (API and Celery workers), close it on exit.
* :mod:`app.storage.documents` — the ``DocumentStorage`` facade for document
  bytes, exports, anatomy figures and import archives.
* :mod:`app.storage.artifacts` — key builders and dual-read helpers for the
  non-document byte roots (anatomy, seed packs, exports, imports).
"""
