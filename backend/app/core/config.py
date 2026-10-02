# ruff: noqa: SIM102 -- long immutable strings; reflow when touched
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_file() -> str | None:
    """Locate the .env file for Pydantic Settings.

    Precedence:
      1. HA_ENV_FILE env var — explicit path from the launcher (best practice,
         Twelve-Factor: the orchestrator tells the app where its config lives).
      2. Walk up from this file's location to find the nearest .env — robust
         against CWD changes and directory restructuring (no magic depth).
      3. None — fall back to real env vars only (production-correct; docker
         and k8s inject env vars directly, no .env file needed).

    Set HA_ENV_FILE in run-dev.sh, docker-compose, systemd, or your IDE to
    point at a non-default location.
    """
    explicit = os.getenv("HA_ENV_FILE")
    if explicit:
        return explicit

    # Audit 2026-08 C-5: outside development the tree walk-up is disabled —
    # a baked-in .env inside a container (or a stray file above the app dir)
    # would otherwise be silently loaded and could downgrade every boot
    # guard. Production must set HA_ENV_FILE explicitly or use real env vars.
    app_env = os.getenv("HA_APP_ENV", "development")
    if app_env not in ("development", "test", "testing"):
        return None

    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        candidate = parent / ".env"
        if candidate.is_file():
            return str(candidate)

    return None


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Health Assistant"
    VERSION: str = "0.8.0"
    HA_APP_ENV: str = "development"
    DEBUG: bool = False

    # --- Identity & auth (identity-auth §16 `HA_*` names) ----------------
    # Init-only instance facts (§4/§13): HA_AUTH_MODE / HA_DEMO_MODE are
    # consumed ONLY when initializing an empty database (they seed
    # `instance_settings.auth_mode` / `demo_mode`); post-init they are
    # ignored with a loud warning — the DB is authoritative. Reads fail
    # closed: unknown/missing auth_mode ⇒ `authenticated`, demo_mode ⇒ false.
    # Never configured? Defaults: authenticated, no demo.
    HA_AUTH_MODE: str = "authenticated"
    # HA_DEMO_MODE — when true (at init), the instance is a demo instance:
    # `instance_settings.demo_mode=true`, the app auto-seeds a demo tenant +
    # user (scripts/seed_demo.py on boot) and POST /auth/demo-login admits
    # the credential-free `demo` principal (tokens carry auth_mode="demo").
    # Intended for public/screenshot demos behind a firewall; NEVER enable on
    # an instance that holds real data — it bypasses authentication entirely.
    # Orthogonal to HA_APP_ENV so it composes with the production boot-guards
    # (the demo docker compose runs HA_APP_ENV=production + HA_DEMO_MODE=true).
    HA_DEMO_MODE: bool = False
    # The demo credentials (single source of truth; the demo compose,
    # capture tooling and deploy workflow all read these names).
    HA_DEMO_EMAIL: str = "demo@healthassistant.local"
    HA_DEMO_PASSWORD: str = "Demo1234!"

    # Database
    POSTGRES_USER: str = "admin"
    # No insecure default password — must be supplied via env. The production
    # validator below refuses to boot with empty/known-weak credentials outside
    # development environments.
    POSTGRES_PASSWORD: str = ""
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    # deployment.md / ADR-0022 naming: neuronection_<product>. Demo/test flavors use
    # neuronection_health_demo / neuronection_health_test[_gwN] via env.
    POSTGRES_DB: str = "neuronection_health"
    DATABASE_URL: str | None = None  # gate-allow: DATABASE_URL (live health env name — plan 23 D3 triage)

    @model_validator(mode="after")
    def assemble_db_connection(self) -> "Settings":
        if not self.DATABASE_URL:
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{self.POSTGRES_USER}:"
                f"{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:"
                f"{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )
        return self

    @model_validator(mode="after")
    def _validate_db_credentials(self) -> "Settings":
        """Refuse to boot in non-dev environments with insecure database
        credentials. Catches common weak values so a misconfigured production
        instance fails fast instead of silently running exploitable creds.
        """
        weak_passwords = {"", "admin123", "password", "postgres", "secret", "changeme"}

        # We know DATABASE_URL is constructed by the time this runs.
        # Extract the actual password being used.
        import urllib.parse

        parsed_url = urllib.parse.urlparse(self.DATABASE_URL)
        active_password = parsed_url.password or ""

        if self.HA_APP_ENV not in ("development", "test", "testing"):
            if active_password in weak_passwords or active_password == "secure_password_here":
                raise ValueError(
                    "A strong database password must be provided in the DATABASE_URL "
                    f"for HA_APP_ENV={self.HA_APP_ENV!r}. Refusing to boot with insecure "
                    "database credentials."
                )
        return self

    DATABASE_POOL_SIZE: int = 10

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_URL: str | None = None

    @model_validator(mode="after")
    def assemble_redis_connection(self) -> "Settings":
        if not self.REDIS_URL:
            self.REDIS_URL = f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}"
        return self

    @model_validator(mode="after")
    def _validate_debug_flag(self) -> "Settings":
        """Audit 2026-08 CFG-M4: DEBUG=true outside dev/test refuses to boot.

        DEBUG enables SQLAlchemy ``echo`` (every SQL statement WITH bound
        parameters — PHI — lands in logs) and verbose 500 details; a
        ``production`` + ``DEBUG=true`` misconfiguration previously booted
        fine and silently logged patient data.
        """
        if self.DEBUG and self.HA_APP_ENV not in ("development", "test", "testing"):
            raise ValueError(
                f"DEBUG=true is not allowed with HA_APP_ENV={self.HA_APP_ENV!r} — it "
                "logs SQL bound parameters (PHI) and leaks error internals. "
                "Set DEBUG=false or HA_APP_ENV=development."
            )
        return self

    # --- Per-purpose key family (identity-auth §8; plan 16 H4) ---------------
    # Three independent per-instance secrets, each 32+ random bytes:
    # HA_SESSION_KEY signs session JWTs (and the api/invite/download product
    # kinds — see app.core.keys for the map), HA_REFRESH_KEY signs refresh
    # JWTs only, HA_DATA_KEY is the Fernet at-rest key and never signs
    # anything. No key is derived from another and no two purposes share
    # key material — the former single all-purpose SECRET_KEY is retired
    # from all signing (every pre-H4 JWT dies at deploy; users re-login),
    # and the pre-H4 INTEGRATION_SECRET_KEY env names are gone with it
    # (rename them in .env — same values, the sealed ring keeps
    # decrypting; the boot guards below refuse missing/weak/placeholder/
    # cross-purpose values, so a stale legacy .env fails loudly, not
    # silently). Dev/test fall back to per-process ephemeral keys
    # (logins do not survive a restart).
    HA_SESSION_KEY: str | None = None
    HA_REFRESH_KEY: str | None = None
    # Fernet key material (base64 32 bytes). The padded ``Fernet.generate_key()``
    # form is the canonical shape; the kit's unpadded token form is accepted
    # too (app.core.encryption normalizes the padding).
    HA_DATA_KEY: str | None = None
    # Prior Fernet keys (comma-separated) accepted for DECRYPTION only so
    # ciphertext sealed before a rotation keeps decrypting; the primary
    # HA_DATA_KEY always encrypts (the rotation ring — see
    # integrations/sdk/secrets.py and app/core/encryption.py).
    HA_DATA_KEY_PREVIOUS: str = ""

    # First-run setup-token guard — see dev/audits/setup-token-modes.md.
    # ``log``     (default) — print one-time token to container logs; required
    #                          for non-localhost, non-dev requests.
    # ``env``     — seed the token from SETUP_BOOTSTRAP_TOKEN (no random mint);
    #                the launcher URL is then composed with ?token=<value> so
    #                storefronts get a one-click flow with no log-grep.
    # ``time``    — tokenless for SETUP_TOKEN_GRACE_MINUTES after first boot,
    #                then required (lazy-falls-back to ``log`` if no env token).
    # ``disabled`` — never require; only safe behind a firewall / VPN / 127.0.0.1
    #                bind. Logs a security warning on every fresh boot.
    SETUP_TOKEN_MODE: str = "log"
    SETUP_BOOTSTRAP_TOKEN: str | None = None
    SETUP_TOKEN_GRACE_MINUTES: int = 30

    @model_validator(mode="after")
    def _validate_setup_token_mode(self) -> "Settings":
        """Resolve + sanity-check the first-run setup-token mode.

        - Rejects unknown mode names early so a typo doesn't silently fall
          through to a dangerous default.
        - ``env`` with an empty SETUP_BOOTSTRAP_TOKEN falls back to ``log``
          with a warning (instead of refusing to boot — keeps stores safe
          against launcher-side misconfiguration).
        """
        import logging

        allowed = {"log", "env", "time", "disabled"}
        if self.SETUP_TOKEN_MODE not in allowed:
            raise ValueError(
                f"SETUP_TOKEN_MODE must be one of {sorted(allowed)}; got {self.SETUP_TOKEN_MODE!r}."
            )
        if self.SETUP_TOKEN_MODE == "env" and not self.SETUP_BOOTSTRAP_TOKEN:
            logging.warning(
                "SETUP_TOKEN_MODE=env but SETUP_BOOTSTRAP_TOKEN is empty — "
                "falling back to 'log' mode. Set SETUP_BOOTSTRAP_TOKEN or "
                "choose another mode."
            )
            self.SETUP_TOKEN_MODE = "log"
        if self.SETUP_TOKEN_GRACE_MINUTES < 1:
            raise ValueError("SETUP_TOKEN_GRACE_MINUTES must be >= 1 minute.")
        return self

    @model_validator(mode="after")
    def _warn_demo_mode(self) -> "Settings":
        """Loud warning + explicit opt-in gate when HA_DEMO_MODE is on.

        Demo mode exposes /auth/demo-login (credential-free login as the
        demo user) — an authentication bypass by design. In any non-dev
        HA_APP_ENV it additionally requires
        ``DEMO_MODE_ACCEPT_UNAUTHENTICATED=true`` so a single flipped env
        var (or a baked-in .env) cannot silently open a real instance
        (audit 2026-08 CFG-H6). This boot guard is env-level on purpose:
        the runtime admission of demo tokens is state-derived from the DB
        fact (identity-auth §4/§13 — see app/core/instance_state.py).
        """
        if self.HA_DEMO_MODE:
            import logging

            if self.HA_APP_ENV not in ("development", "test", "testing"):
                accept = os.getenv("DEMO_MODE_ACCEPT_UNAUTHENTICATED", "").strip().lower()
                if accept not in ("1", "true", "yes"):
                    raise ValueError(
                        "DEMO_MODE=true in HA_APP_ENV="
                        f"{self.HA_APP_ENV!r} refuses to boot: demo-login is a "
                        "credential-free authentication bypass. If this is a "
                        "throwaway public demo, set "
                        "DEMO_MODE_ACCEPT_UNAUTHENTICATED=true explicitly."
                    )
            logging.warning(
                "\n══════════════════════════════════════════════════════\n"
                " ⚠️  DEMO MODE IS ENABLED\n"
                " The app will auto-login anyone as the demo user (%s)\n"
                " with NO credentials. Authentication is effectively off.\n"
                " NEVER use this for an instance that holds real health data.\n"
                "══════════════════════════════════════════════════════",
                self.HA_DEMO_EMAIL,
            )
        return self

    # Known placeholder values that must never boot as real secrets in
    # production (audit 2026-08 CFG-H1) — the .env.example literals plus a
    # few obvious defaults.
    _PLACEHOLDER_SECRETS: ClassVar[frozenset] = frozenset(
        {
            "change_this_to_a_secure_random_string",
            "changeme",
            "change_me",
            "change-this",
            "placeholder",
            "replace_me",
            "replace-me",
            "todo",
            "insecure",
            "secure_password_here",
            "your_secret_key_here",
            "your-secret-key",
            "secret",
            "password",
            "admin123",
        }
    )

    @staticmethod
    def _is_acceptable_secret(value: str) -> bool:
        """A production secret must not be a known placeholder and must
        carry meaningful entropy (>=32 chars, not a simple repeated/short
        pattern)."""
        import re as _re

        v = (value or "").strip()
        if len(v) < 32:
            return False
        if v.lower() in Settings._PLACEHOLDER_SECRETS:
            return False
        # Reject trivially weak compositions (all same char, all digits).
        if len(set(v.lower())) <= 4:
            return False
        if v.isdigit():
            return False
        return not _re.fullmatch(r"[a-z]+", v.lower())

    @staticmethod
    def _is_valid_fernet_material(value: str) -> bool:
        """True when ``value`` is 32 bytes of urlsafe-base64 key material.

        Accepts both the padded ``Fernet.generate_key()`` form and the
        auth-kit's unpadded token form — the same two shapes
        ``app.core.encryption.fernet_from_data_key`` normalizes. Kept here
        (stdlib-only) so the boot guard never imports the crypto stack.
        """
        import base64 as _b64

        v = (value or "").strip()
        if not v:
            return False
        try:
            raw = _b64.urlsafe_b64decode(v + "=" * (-len(v) % 4))
        except (ValueError, TypeError):
            return False
        return len(raw) == 32

    @model_validator(mode="after")
    def _validate_signing_keys(self) -> "Settings":
        """Identity-auth §8 (plan 16 H4): per-purpose signing keys.

        - ``HA_SESSION_KEY`` / ``HA_REFRESH_KEY`` are REQUIRED on servers
          (non-dev HA_APP_ENV): missing, placeholder or weak (<32 chars /
          trivial entropy) values refuse to boot.
        - A partial pin (exactly one of the two set) fails closed in every
          environment — session and refresh must never be minted from an
          accidentally half-migrated key ring.
        - dev/test with neither set: per-process ephemeral keys (logins do
          not survive a restart), matching the retired SECRET_KEY fallback.
        """
        keys = {"HA_SESSION_KEY": self.HA_SESSION_KEY, "HA_REFRESH_KEY": self.HA_REFRESH_KEY}
        pinned = [name for name, value in keys.items() if value]
        if len(pinned) == 1:
            missing = [name for name in keys if name not in pinned]
            raise ValueError(
                f"partial signing-key pin: {', '.join(missing)} is missing — "
                "provide both HA_SESSION_KEY and HA_REFRESH_KEY or neither "
                "(identity-auth §8: session and refresh sign with separate, "
                "independently pinned keys)."
            )
        for name, value in keys.items():
            if not value:
                if self.HA_APP_ENV in ("development", "test", "testing"):
                    import logging

                    logging.warning(
                        "No %s provided; generating an ephemeral one for "
                        "development. Logins will not survive restarts.",
                        name,
                    )
                    setattr(self, name, secrets.token_urlsafe(32))
                else:
                    raise ValueError(
                        f"A strong {name} must be provided via environment "
                        f"variables for HA_APP_ENV={self.HA_APP_ENV!r} (identity-auth "
                        "§8: signing keys are per-purpose and env-pinned on "
                        "servers). Refusing to boot without one."
                    )
            elif self.HA_APP_ENV not in ("development", "test", "testing") and (
                value.strip().lower() in self._PLACEHOLDER_SECRETS
                or not self._is_acceptable_secret(value)
            ):
                raise ValueError(
                    f"{name} looks like a placeholder or is too weak (need >= 32 "
                    "chars with real entropy). Generate one with: python -c "
                    '"from secrets import token_urlsafe; print(token_urlsafe(48))". '
                    "Refusing to boot with a publicly-known signing key."
                )
        return self

    JWT_ALGORITHM: str = "HS256"
    # §16 lifetimes (identity-auth §8): 60-min session access tokens
    # (24h hard cap — a stolen session token is revocable via the jti
    # store + `ver`, but defense in depth keeps the unrevocable window
    # small; 24h was far too long for PHI), 7-day rolling refresh with a
    # 30-day absolute cap enforced via `auth_sessions`.
    HA_AUTH_ACCESS_TTL_MINUTES: int = 60
    HA_AUTH_REFRESH_TTL_DAYS: int = 7
    HA_AUTH_REFRESH_ABSOLUTE_DAYS: int = 30
    # §7 lockout: 5 consecutive failures ⇒ 423 for 15 minutes.
    HA_AUTH_LOCKOUT_THRESHOLD: int = 5
    HA_AUTH_LOCKOUT_MINUTES: int = 15
    # §12/§16: gates POST /auth/register (health is invite-only by
    # construction — the flag additionally disables the register route).
    HA_REGISTRATION_ENABLED: bool = True
    # §10 cookie sessions (plan 16 H3): browsers authenticate with the
    # HttpOnly ``nx_access`` / ``nx_refresh`` cookies plus the JS-readable
    # ``nx_csrf`` double-submit cookie; user clients keep Bearer (§9 —
    # the Android/integrations HMAC bridge is unchanged). ``HA_COOKIE_SECURE``
    # also switches the access cookie to the ``__Host-`` prefix (TLS
    # deployments — the prefix demands Secure + Path=/).
    HA_COOKIE_SECURE: bool = False
    # SameSite knob (§10 default Lax). ``none`` requires Secure — browsers
    # reject SameSite=None without it.
    HA_COOKIE_SAMESITE: str = "lax"
    # §10 WS Origin gate: comma-separated origin allow-list for the
    # WebSocket handshake. Empty/None = same-origin (request Host) +
    # APP_URL/FRONTEND_URL (the CORS list; the dev LAN regex applies in
    # development).
    HA_WS_ALLOWED_ORIGINS: str | None = None

    @model_validator(mode="after")
    def _validate_auth_lifetimes(self) -> "Settings":
        """§8 lifetime invariants — fail fast on impossible configurations."""
        if not 1 <= self.HA_AUTH_ACCESS_TTL_MINUTES <= 24 * 60:
            raise ValueError("HA_AUTH_ACCESS_TTL_MINUTES must be 1..1440 (24h hard cap per §8).")
        if self.HA_AUTH_REFRESH_TTL_DAYS < 1:
            raise ValueError("HA_AUTH_REFRESH_TTL_DAYS must be >= 1.")
        if self.HA_AUTH_REFRESH_ABSOLUTE_DAYS < self.HA_AUTH_REFRESH_TTL_DAYS:
            raise ValueError(
                "HA_AUTH_REFRESH_ABSOLUTE_DAYS must be >= HA_AUTH_REFRESH_TTL_DAYS "
                "(rolling window inside the absolute cap)."
            )
        if self.HA_AUTH_LOCKOUT_THRESHOLD < 1 or self.HA_AUTH_LOCKOUT_MINUTES < 1:
            raise ValueError("HA_AUTH_LOCKOUT_THRESHOLD / HA_AUTH_LOCKOUT_MINUTES must be >= 1.")
        if self.HA_COOKIE_SAMESITE.lower() not in ("lax", "strict", "none"):
            raise ValueError("HA_COOKIE_SAMESITE must be one of lax|strict|none (§10 default Lax).")
        if self.HA_COOKIE_SAMESITE.lower() == "none" and not self.HA_COOKIE_SECURE:
            raise ValueError(
                "HA_COOKIE_SAMESITE=none requires HA_COOKIE_SECURE=true "
                "(browsers reject SameSite=None without Secure)."
            )
        return self

    # OAuth2 / SMART-on-FHIR — the FHIR R4 facade is the public interop
    # surface; external systems authenticate via the client-credentials grant
    # (RFC 6749 §4.4) with SMART scopes. See docs/API_LAYERS.md.
    OAUTH_ACCESS_TOKEN_TTL_MINUTES: int = 60
    # Audience api tokens must carry. Session JWTs (frontend) have no ``aud``
    # and are rejected on the facade; api tokens without this audience are
    # rejected everywhere. (The ``iss`` claim is the product slug "health" on
    # every token kind per identity-auth §8 — the old OAUTH_ISSUER override
    # is retired.)
    OAUTH_AUDIENCE: str = "health-assistant-api"

    # URLs
    # The frontend/PWA origin (dev default matches the Vite dev port 3000 — the
    # same default as integrations.py `_frontend_origin()`). Separate from
    # APP_URL (the OAuth issuer / backend URL); used by /config/public for the
    # mobile app's frontend-origin deep links.
    FRONTEND_URL: str = "http://localhost:3000"
    APP_URL: str = "http://localhost:8000"

    # Audit 2026-08 API-L1: API docs (Swagger/Redoc) are dev-only unless an
    # operator explicitly enables them (e.g. behind an authenticated gateway).
    ENABLE_API_DOCS: bool = False

    # Audit 2026-08 AUTH-H1 + §16: number of TRUSTED reverse-proxy hops that
    # append to X-Forwarded-For (nginx/traefik + their LB). 0 = direct
    # exposure (the header is ignored; the socket peer is the rate-limit
    # identity). Set to 1 when exactly one trusted proxy fronts the app.
    HA_TRUSTED_PROXY_COUNT: int = 0

    # §16 rate-limit ceilings (career reference): per-bucket, requests per
    # minute. When set (non-null) a bucket ceiling overrides the per-route
    # code defaults; ``0`` disables the bucket. Unset ⇒ route defaults only
    # (the pre-§16 behavior — see app/core/rate_limit.py).
    HA_RATELIMIT_ENABLED: bool = True
    HA_RATELIMIT_AUTH: int | None = None
    HA_RATELIMIT_AUTH_EMAIL: int | None = None
    HA_RATELIMIT_AI: int | None = None
    HA_RATELIMIT_MCP: int | None = None
    HA_RATELIMIT_DEFAULT: int | None = None

    # AI/OCR - OpenAI Compatible API (used as fallback if no database configuration exists)
    OCR_PROVIDER: str = "openai"
    OPENAI_API_KEY: str | None = None
    OPENAI_API_BASE: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4-vision-preview"
    OPENAI_MAX_TOKENS: int = 65536
    OPENAI_TIMEOUT: int = 30

    # AI Agent
    AI_AGENT_MAX_ITERATIONS: int = 20
    # AI Graphs (LangGraph) — checkpoint retention for the prune beat job.
    # Checkpoint rows have no timestamp columns (langgraph-checkpoint-postgres
    # 3.x), so age is derived from the owning chat session's last activity:
    # threads whose session is idle (or deleted) longer than this are pruned.
    AI_CHECKPOINT_RETENTION_DAYS: int = 30

    # AI Chat — multimodal image attachments (vision models). Limits protect
    # against oversized payloads (base64 in JSON) and token blowups.
    AI_CHAT_MAX_IMAGES: int = 4
    AI_CHAT_MAX_IMAGE_BYTES: int = 8 * 1024 * 1024  # 8 MiB per image (decoded)

    # AI Chat — speech-to-text (voice input). Audio is transcribed server-side
    # then discarded (never persisted); these limits guard the upload size +
    # the transcription call timeout.
    AI_STT_MAX_AUDIO_BYTES: int = 20 * 1024 * 1024  # 20 MiB compressed audio
    AI_STT_TIMEOUT_SECONDS: int = 60
    # Default STT model when no DB assignment exists (OpenAI-compatible API).
    OPENAI_STT_MODEL: str = "whisper-1"

    @model_validator(mode="after")
    def _validate_data_key(self) -> "Settings":
        """Identity-auth §8 (plan 16 H4): the DATA_KEY family (Fernet at rest).

        ``HA_DATA_KEY`` is required on servers
        and must be valid 32-byte key material in every environment; dev/test
        fall back to an ephemeral key. It never signs anything (see
        ``_validate_key_separation``) and ``HA_DATA_KEY_PREVIOUS`` carries
        prior keys for decrypt-only rotation.
        """
        if not self.HA_DATA_KEY:
            if self.HA_APP_ENV in ("development", "test", "testing"):
                import logging

                from cryptography.fernet import Fernet

                logging.warning(
                    "No HA_DATA_KEY provided; generating an ephemeral one for "
                    "development. Connected integrations will break on restart."
                )
                self.HA_DATA_KEY = Fernet.generate_key().decode()
            else:
                raise ValueError(
                    f"A valid HA_DATA_KEY (Fernet key) must be provided via environment "
                    f"variables for HA_APP_ENV={self.HA_APP_ENV!r}. Refusing to boot "
                    "without one."
                )
        elif not self._is_valid_fernet_material(self.HA_DATA_KEY):
            raise ValueError(
                "HA_DATA_KEY must be 32 bytes of urlsafe-base64 key material "
                '(a Fernet key). Generate with: python -c "from '
                "cryptography.fernet import Fernet; "
                'print(Fernet.generate_key().decode())"'
            )
        for prev in filter(None, (k.strip() for k in self.HA_DATA_KEY_PREVIOUS.split(","))):
            if not self._is_valid_fernet_material(prev):
                raise ValueError(
                    "HA_DATA_KEY_PREVIOUS contains invalid Fernet "
                    "key material — every entry must be 32-byte urlsafe base64."
                )
        return self

    @model_validator(mode="after")
    def _validate_key_separation(self) -> "Settings":
        """Identity-auth §8: no key material is shared across purposes.

        Runs after the dev fallbacks above, so it sees the effective ring:
        session, refresh and data keys must be pairwise distinct — a value
        reused across purposes would couple the compromise of one to all
        (the single-SECRET_KEY status quo H4 retires).
        """
        keys = (self.HA_SESSION_KEY, self.HA_REFRESH_KEY, self.HA_DATA_KEY)
        names = ("HA_SESSION_KEY", "HA_REFRESH_KEY", "HA_DATA_KEY")
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                if keys[i] and keys[j] and keys[i] == keys[j]:
                    raise ValueError(
                        f"{names[i]} and {names[j]} must be distinct values "
                        "(identity-auth §8: keys are separated per purpose — "
                        "no value may serve two purposes)."
                    )
        return self

    # Audit 2026-08 C-4: STDIO spawn = local code execution. Disabled by
    # default — operators must consciously enable it AND (recommended) run
    # the workers in an isolated container. Even when enabled, interpreters
    # that trivially execute arbitrary strings (python/python3/node -c/-e)
    # are rejected at the arg level (see mcp_client/security.py).
    MCP_STDIO_ALLOWED_COMMANDS: str = ""
    MCP_MAX_SERVERS_PER_USER: int = 5
    MCP_MAX_TOTAL_STDIO: int = 20
    MCP_REQUEST_TIMEOUT: float = 30.0
    MCP_TOOL_RESULT_MAX_BYTES: int = 65536
    INTEGRATION_MAX_TOOLS_PER_SESSION: int = 20
    MCP_CONNECTION_IDLE_TIMEOUT: int = 900
    MCP_PER_INSTANCE_CONCURRENCY: int = 4
    MCP_ALLOW_INSECURE_HTTP: bool = False

    # File Storage
    UPLOAD_DIR: str = "/var/healthassistant/uploads"  # gate-allow: UPLOAD_DIR (live health env name — plan 23 D3 triage)
    MAX_UPLOAD_SIZE: int = 50  # MB

    # Email
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@healthassistant.local"

    # Web Push (VAPID)
    # Generate using: vapid --gen
    # Declared as plain Optional[str] so pydantic-settings can pick up the
    # VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY env vars the standard way. The
    # previous ``os.getenv(...)`` defaults bypassed pydantic and made the
    # prod-guard validator below ineffective (the os.getenv value was baked
    # in at class-definition time, before any test could monkeypatch env).
    VAPID_PUBLIC_KEY: str | None = None
    VAPID_PRIVATE_KEY: str | None = None
    VAPID_ADMIN_EMAIL: str = "admin@healthassistant.local"

    @model_validator(mode="after")
    def _validate_vapid_keys(self) -> "Settings":
        """VAPID keys are required in production for Web Push delivery.

        In development / test, missing keys are tolerated — Web Push is
        silently skipped (``send_web_push`` returns False with a warning).
        In production, refusing to boot surfaces operator misconfiguration
        early instead of letting push notifications silently fail forever.
        """
        if self.HA_APP_ENV in ("development", "test", "testing"):
            return self
        if not self.VAPID_PUBLIC_KEY or not self.VAPID_PRIVATE_KEY:
            raise ValueError(
                f"VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY must be provided via "
                f"environment variables for HA_APP_ENV={self.HA_APP_ENV!r}. Generate "
                "with `vapid --gen` or `npx web-push generate-vapid-keys`. "
                "Refusing to boot without them."
            )
        return self

    # Ports (for docker)
    BACKEND_PORT: int = 8000
    FRONTEND_PORT: int = 3000
    FLOWER_PORT: int = 5555

    model_config = SettingsConfigDict(
        env_file=_resolve_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


settings = get_settings()


# Dev-only CORS / WS-Origin pattern: any local or LAN origin (localhost,
# 127.0.0.1, RFC1918 ranges). Shared by the CORS middleware and the §10
# WebSocket Origin gate (app.api.v1.endpoints.websockets) so the two
# surfaces never drift.
DEV_LAN_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+"
    r"|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?$"
)
