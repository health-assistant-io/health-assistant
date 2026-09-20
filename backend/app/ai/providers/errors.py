"""§15 BYOK provider-error classifier.

Maps a failed setup fetch onto the family i18n error enum
(``invalid_key | insufficient_credit | new_user_quota | region_unavailable |
timeout | local_not_running | unknown``) plus the ``suspected_vendor``
mis-paste hint from the most-specific-first prefix table.

Canonical text: dev/guidelines/ai-features.md §15 ("Error routing").
Endpoints surface ``code`` + ``suspected_vendor`` verbatim — clients route
them through i18n; the raw vendor message never replaces the enum.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx

from .presets import guess_preset_for_key


class ProviderErrorCode(str, Enum):
    """The §15 error enum (closed vocabulary — i18n keys downstream)."""

    INVALID_KEY = "invalid_key"
    INSUFFICIENT_CREDIT = "insufficient_credit"
    NEW_USER_QUOTA = "new_user_quota"
    REGION_UNAVAILABLE = "region_unavailable"
    TIMEOUT = "timeout"
    LOCAL_NOT_RUNNING = "local_not_running"
    UNKNOWN = "unknown"


CONNECTION_FAILURE_PATTERN = re.compile(
    r"ECONNREFUSED|connection refused|connect call failed|failed to establish"
    r"|ENOTFOUND|ECONNRESET|network",
    re.IGNORECASE,
)
REGION_PATTERN = re.compile(
    r"region|country|geo|not available in|unavailable in your|unsupported_country",
    re.IGNORECASE,
)
CREDIT_PATTERN = re.compile(
    r"insufficient_quota|insufficient (?:credit|quota|balance)|billing",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClassifiedProviderError:
    code: ProviderErrorCode
    suspected_vendor: Optional[str] = None


def extract_error_status(message: str) -> Optional[int]:
    """Pull ``status NNN`` out of an httpx error string (``None`` if absent)."""
    match = re.search(r"status (\d{3})", message)
    return int(match.group(1)) if match else None


def classify_provider_error(
    error: Exception,
    *,
    local_provider: bool,
    api_key: Optional[str] = None,
    attempted_preset: Optional[str] = None,
) -> ClassifiedProviderError:
    """Classify a setup-fetch failure into the §15 enum."""
    message = str(error)
    if isinstance(error, httpx.TimeoutException):
        return ClassifiedProviderError(code=ProviderErrorCode.TIMEOUT)
    if (
        isinstance(error, httpx.TransportError)
        or CONNECTION_FAILURE_PATTERN.search(message)
    ) and local_provider:
        return ClassifiedProviderError(code=ProviderErrorCode.LOCAL_NOT_RUNNING)

    status: Optional[int] = None
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
    else:
        status = extract_error_status(message)

    if status == 401:
        classified = ClassifiedProviderError(code=ProviderErrorCode.INVALID_KEY)
        if attempted_preset:
            guess = guess_preset_for_key(api_key)
            if guess and guess != attempted_preset:
                classified = ClassifiedProviderError(
                    code=ProviderErrorCode.INVALID_KEY, suspected_vendor=guess
                )
        return classified
    if status == 402 or CREDIT_PATTERN.search(message):
        return ClassifiedProviderError(code=ProviderErrorCode.INSUFFICIENT_CREDIT)
    if status == 429:
        return ClassifiedProviderError(code=ProviderErrorCode.NEW_USER_QUOTA)
    if status == 403 and REGION_PATTERN.search(message):
        return ClassifiedProviderError(code=ProviderErrorCode.REGION_UNAVAILABLE)
    return ClassifiedProviderError(code=ProviderErrorCode.UNKNOWN)
