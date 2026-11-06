"""Health's Content-Security-Policy — one policy body for every response.

The SPA document is served outside FastAPI (vite in dev, ``serve`` in the
production frontend image, nginx as the bundled gateway), so this module is
the single source for the policies the *backend* emits, and the SPA serving
configs carry the identical directive set (``frontend/public/serve.json`` for
the production image; ``frontend/vite.config.ts`` carries the dev variant that
keeps HMR working). ``tests/test_csp_headers.py`` pins the two prod copies
together so they cannot drift.

Deviations from the family template (``nx_auth.headers.CSP_TEMPLATE``) are
health-specific and deliberate:

* ``img-src``/``media-src``/``worker-src`` allow ``blob:`` — document, DICOM,
  atlas and examination previews are blob URLs built in the browser.
* ``frame-ancestors 'self'`` instead of ``'none'`` — the inline PDF preview
  embeds ``/api/v1/documents/{id}/download`` in a same-origin iframe, exactly
  like the per-route ``X-Frame-Options: SAMEORIGIN`` relaxation on that route.
  Cross-origin framing stays blocked.
"""

from __future__ import annotations

DOCUMENT_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';"
    " img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self' data:;"
    " connect-src 'self'; worker-src 'self' blob:; manifest-src 'self';"
    " object-src 'none'; base-uri 'self'; form-action 'self';"
    " frame-ancestors 'self'"
)

__all__ = ["DOCUMENT_CSP"]
