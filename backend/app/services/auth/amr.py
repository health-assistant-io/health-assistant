"""Authentication-context vocabulary shared by SSO and step-up (program 12C1).

OIDC providers describe *how* the user authenticated with ``amr`` (RFC 8176)
and ``acr`` (OIDC core §2). Two consumers need the same answer to one
question — "does this evidence a second factor?":

* the SSO callback, deciding whether the IdP already satisfied the tenant
  MFA policy;
* the step-up service, deciding whether a federated sign-in is fresh enough
  to prove presence for a sensitive action.

The set lives here (not in either consumer) so the two can never drift:
a provider-agnostic, conservative reading — an RFC 8176 *second-factor*
``amr`` value, or an ``acr`` that names multi-factor or carries a numeric
level ≥ 2. Absence is never evidence, and ``pwd`` alone is single-factor.
"""

from __future__ import annotations

from collections.abc import Iterable

#: RFC 8176 ``amr`` values that evidence a second factor. ``pwd`` alone is
#: single-factor and is deliberately absent.
SECOND_FACTOR_AMR = frozenset(
    {
        "mfa",
        "otp",
        "hwk",
        "swk",
        "sms",
        "tel",
        "fpt",
        "face",
        "iris",
        "retina",
        "vbm",
        "voice",
        "biometric",
        "kba",
        "pin",
        "oob",
        "pop",
        "wia",
    }
)


def amr_evidences_second_factor(amr: Iterable[str] | None, acr: str | None = None) -> bool:
    """Whether these authentication claims evidence a second factor.

    ``amr`` membership in :data:`SECOND_FACTOR_AMR` wins outright; otherwise
    an ``acr`` naming multi-factor (``urn:…:acr:mfa``, ``mfa``,
    ``multifactor``) or a numeric level ≥ 2 counts. Anything else — including
    absent claims — answers ``False``.
    """
    values = {str(value).strip().lower() for value in (amr or ()) if value}
    if values & SECOND_FACTOR_AMR:
        return True
    label = (acr or "").strip().lower()
    if not label:
        return False
    if "mfa" in label or "multi-factor" in label or "multifactor" in label:
        return True
    try:
        return int(float(label)) >= 2
    except ValueError:
        return False


__all__ = ["SECOND_FACTOR_AMR", "amr_evidences_second_factor"]
