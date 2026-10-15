# VENDORED FILE — family canonical (ADR-0031 / ADR-0038). Source of truth:
# dev/contracts/wire_params.py; synced byte-identical to
# backend/app/ai/wire_params.py in study-assistant, health-assistant/core
# and career-assistant by dev/scripts/sync-wire-params.sh, sha-checked by
# dev/scripts/verify-wiring.sh, accepted by the shared fixture table
# (dev/contracts/wire-params.fixtures.json — vendored alongside, loaded by
# every repo's wire-params contract test). NEVER edit the vendored copy
# in-repo — change the dev canonical, then re-run the sync (contract
# changes = one dev commit + one sync run, never per-repo edits).
"""Family wire-parameter policy (ADR-0038) — the shared core.

One canonical implementation of the rules every model factory applies
when turning configuration into provider payloads:

* **The token cap routes by endpoint, never by model id.**
  ``api.openai.com`` speaks ``max_completion_tokens`` (accepted by every
  OpenAI chat model, old and new — ``max_tokens`` is deprecated and
  hard-rejected on the gpt-5 family and everything after it); every
  other base URL keeps ``max_tokens``, the OpenAI-compatible lingua
  franca (Ollama, LM Studio, llama.cpp, LocalAI, vLLM, OpenRouter…).
  Model ids are the wrong signal — gateway prefixes (``openai/…``),
  chat variants and future families make every id regex lie.
* **An explicit provider override beats detection**
  (``auto | max_tokens | max_completion_tokens | none``).
* **No self-heal** — a wire mismatch is a configuration error and
  surfaces as the provider's own 400; a retry would re-send the same
  billed payload for a deterministic misconfiguration.

``CompatibleChatOpenAI`` is the Python mechanic: ``langchain-openai``
renames ``max_tokens`` to ``max_completion_tokens`` unconditionally, and
the ``_default_params`` skip restores the classic name for compatible
endpoints. The per-repo vocabulary-wall tests (each repo's
``test_wire_params_contract.py``, driven by the shared fixture table)
are the acceptance tests for this module. The TypeScript counterpart is
desktop-assistant's ``resolveMaxTokensParam`` + ``wireTokenParamFetch``
— same policy, JS-native mechanic, accepted by the same fixtures.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from langchain_openai import ChatOpenAI

OPENAI_API_HOST = "api.openai.com"

#: Provider-facing knob: ``auto`` = endpoint routing (the rules below);
#: the explicit values pin the wire name; ``none`` sends no cap at all.
MaxTokensParamOverride = Literal["auto", "max_tokens", "max_completion_tokens", "none"]

#: The resolved wire parameter.
WireTokenParam = Literal["max_tokens", "max_completion_tokens", "none"]


def is_openai_endpoint(base_url: str | None) -> bool:
    """True when the base URL points at OpenAI's own API.

    The endpoint decides the wire contract. Declared provider types
    cannot: OpenAI itself is commonly registered as ``openai_compatible``
    (desktop's presets do), and any row may be pointed at a proxy.
    """
    try:
        return urlsplit(base_url or "").hostname == OPENAI_API_HOST
    except ValueError:
        return False


def is_openai_wire(provider_type: str, base_url: str | None) -> bool:
    """Should payloads use OpenAI's modern wire contract?

    The endpoint decides: a base URL at OpenAI's API rides the modern
    wire whatever the row declares, and any other base URL rides the
    compatible wire — with one carve-out, an ``openai``-typed provider
    with no base URL counts as OpenAI's own default endpoint. Declared
    types never override a concrete base URL (any row can be pointed at
    a proxy that forwards unchanged, but only the endpoint is trusted).
    """
    if is_openai_endpoint(base_url):
        return True
    return provider_type == "openai" and not (base_url or "").strip()


def resolve_max_tokens_param(
    provider_type: str,
    base_url: str | None,
    override: str | None = None,
) -> WireTokenParam:
    """Resolve the wire parameter for the output-token cap.

    An explicit override (anything but ``None``/``"auto"``) wins over
    detection; ``"none"`` means the cap is not sent at all.
    """
    if override in ("max_tokens", "max_completion_tokens", "none"):
        return override  # type: ignore[return-value]
    if is_openai_wire(provider_type, base_url):
        return "max_completion_tokens"
    return "max_tokens"


class CompatibleChatOpenAI(ChatOpenAI):
    """ChatOpenAI that keeps ``max_tokens`` on the wire.

    ``ChatOpenAI`` renames the ``max_tokens`` parameter to
    ``max_completion_tokens`` in the request payload — fine for OpenAI
    itself, but OpenAI-compatible endpoints reject the newer name.
    Skipping the rename restores the wire contract these providers
    always had. Repos that add their own ``ChatOpenAI`` subclass stack it
    via MRO (``class CompatibleCaChatOpenAI(CaChatOpenAI,
    CompatibleChatOpenAI)``) so both overrides apply.
    """

    @property
    def _default_params(self) -> dict[str, Any]:
        # Skip ChatOpenAI's override; BaseChatOpenAI leaves the name alone.
        return super(ChatOpenAI, self)._default_params


def token_cap_kwargs(provider_type: str, cap: int, base_url: str | None = None) -> dict[str, int]:
    """Token-cap kwargs for a plain REST chat payload, per wire contract.

    For the httpx/fetch ping paths that bypass LangChain; the chat-model
    factories apply the same split through the SDK mechanics.
    """
    if resolve_max_tokens_param(provider_type, base_url) == "max_completion_tokens":
        return {"max_completion_tokens": cap}
    return {"max_tokens": cap}
