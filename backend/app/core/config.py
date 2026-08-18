import secrets
from pathlib import Path
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import os
from typing import Optional, ClassVar
from functools import lru_cache


def _resolve_env_file() -> Optional[str]:
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
    app_env = os.getenv("APP_ENV", "development")
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
    VERSION: str = "0.4.3"
    APP_ENV: str = "development"
    DEBUG: bool = False

    # Demo mode — when true, the app auto-seeds a demo tenant + user (via
    # scripts/seed_demo.py on boot) and exposes POST /auth/demo-login so the
    # frontend can sign in with NO credentials. Intended for public/screenshot
    # demos behind a firewall; NEVER enable on an instance that holds real
    # data — it bypasses authentication entirely. Orthogonal to APP_ENV so it
    # composes with the production boot-guards (the demo docker compose runs
    # APP_ENV=production + DEMO_MODE=true). See dev/audits + CHANGELOG.
    DEMO_MODE: bool = False
    # The demo user. Aliased to the legacy HA_DEMO_EMAIL / HA_DEMO_PASSWORD
    # env names so the existing demo docker compose + UI capture tooling keep
    # working unchanged (single source of truth for "the demo credentials").
    DEMO_USER_EMAIL: str = Field(
        default="demo@healthassistant.local",
        validation_alias=AliasChoices("DEMO_USER_EMAIL", "HA_DEMO_EMAIL"),
    )
    DEMO_USER_PASSWORD: str = Field(
        default="Demo1234!",
        validation_alias=AliasChoices("DEMO_USER_PASSWORD", "HA_DEMO_PASSWORD"),
    )

    # Database
    POSTGRES_USER: str = "admin"
    # No insecure default password — must be supplied via env. The production
    # validator below refuses to boot with empty/known-weak credentials outside
    # development environments.
    POSTGRES_PASSWORD: str = ""
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "health_assistant"
    DATABASE_URL: Optional[str] = None

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

        if self.APP_ENV not in ("development", "test", "testing"):
            if (
                active_password in weak_passwords
                or active_password == "secure_password_here"
            ):
                raise ValueError(
                    "A strong database password must be provided in the DATABASE_URL "
                    f"for APP_ENV={self.APP_ENV!r}. Refusing to boot with insecure "
                    "database credentials."
                )
        return self

    DATABASE_POOL_SIZE: int = 10

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_URL: Optional[str] = None

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
        if self.DEBUG and self.APP_ENV not in ("development", "test", "testing"):
            raise ValueError(
                f"DEBUG=true is not allowed with APP_ENV={self.APP_ENV!r} — it "
                "logs SQL bound parameters (PHI) and leaks error internals. "
                "Set DEBUG=false or APP_ENV=development."
            )
        return self

    # Security
    # SECRET_KEY signs JWTs. It must be explicitly provided in production.
    # Read via pydantic (Optional[str] = None) rather than os.getenv at class
    # definition time — the os.getenv default baked in the value before the
    # prod-guard validator below could reject it, making it untestable and
    # inconsistent with how VAPID keys are handled (audit C7).
    SECRET_KEY: Optional[str] = None

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
    SETUP_BOOTSTRAP_TOKEN: Optional[str] = None
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
                f"SETUP_TOKEN_MODE must be one of {sorted(allowed)}; "
                f"got {self.SETUP_TOKEN_MODE!r}."
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
        """Loud warning + explicit opt-in gate when DEMO_MODE is on.

        Demo mode exposes /auth/demo-login (credential-free login as the
        demo user, ADMIN role) — an authentication bypass by design. In any
        non-dev APP_ENV it additionally requires
        ``DEMO_MODE_ACCEPT_UNAUTHENTICATED=true`` so a single flipped env
        var (or a baked-in .env) cannot silently open a real instance
        (audit 2026-08 CFG-H6).
        """
        if self.DEMO_MODE:
            import logging

            if self.APP_ENV not in ("development", "test", "testing"):
                accept = (
                    os.getenv("DEMO_MODE_ACCEPT_UNAUTHENTICATED", "").strip().lower()
                )
                if accept not in ("1", "true", "yes"):
                    raise ValueError(
                        "DEMO_MODE=true in APP_ENV="
                        f"{self.APP_ENV!r} refuses to boot: demo-login is a "
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
                self.DEMO_USER_EMAIL,
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
        if _re.fullmatch(r"[a-z]+", v.lower()):
            return False
        return True

    @model_validator(mode="after")
    def _validate_secret_key(self) -> "Settings":
        """Ensure a valid SECRET_KEY is provided, falling back to an ephemeral one in dev only."""
        if not self.SECRET_KEY:
            if self.APP_ENV in ("development", "test", "testing"):
                import logging

                logging.warning(
                    "No SECRET_KEY provided; generating an ephemeral one for development. Logins will not survive restarts."
                )
                self.SECRET_KEY = secrets.token_urlsafe(32)
            else:
                raise ValueError(
                    f"A strong SECRET_KEY must be provided via environment variables for APP_ENV={self.APP_ENV!r}. "
                    "Refusing to boot without one."
                )
        if self.APP_ENV not in ("development", "test", "testing") and (
            self.SECRET_KEY.strip().lower() in self._PLACEHOLDER_SECRETS
            or not self._is_acceptable_secret(self.SECRET_KEY)
        ):
            raise ValueError(
                "SECRET_KEY looks like a placeholder or is too weak (need >= 32 "
                "chars with real entropy). Generate one with: python -c "
                '"from secrets import token_urlsafe; print(token_urlsafe(48))". '
                "Refusing to boot with a publicly-known signing key."
            )
        return self

    JWT_ALGORITHM: str = "HS256"
    # Short-lived stateless access tokens (audit 2026-08 M4): a stolen
    # session token is revocable via the jti store, but defense in depth
    # keeps the unrevocable window small. 24h was far too long for PHI.
    JWT_EXPIRATION_HOURS: int = 1

    # OAuth2 / SMART-on-FHIR — the FHIR R4 facade is the public interop
    # surface; external systems authenticate via the client-credentials grant
    # (RFC 6749 §4.4) with SMART scopes. See docs/API_LAYERS.md.
    OAUTH_ACCESS_TOKEN_TTL_MINUTES: int = 60
    # Issuer claim stamped on api tokens. Falls back to APP_URL when empty so
    # a single-instance deploy works without extra config.
    OAUTH_ISSUER: str = ""
    # Audience api tokens must carry. Session JWTs (frontend) have no ``aud``
    # and are rejected on the facade; api tokens without this audience are
    # rejected everywhere.
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

    # AI/OCR - OpenAI Compatible API (used as fallback if no database configuration exists)
    OCR_PROVIDER: str = "openai"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_API_BASE: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4-vision-preview"
    OPENAI_MAX_TOKENS: int = 65536
    OPENAI_TIMEOUT: int = 30

    # AI Agent
    AI_AGENT_MAX_ITERATIONS: int = 20

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

    # MCP Client integration (see integrations/mcp_client/)
    # Pydantic-read (audit C7) — same rationale as SECRET_KEY above.
    INTEGRATION_SECRET_KEY: Optional[str] = None
    # Previous keys accepted for decryption during rotation (comma-separated
    # Fernet keys). The primary ``INTEGRATION_SECRET_KEY`` is always used to
    # *encrypt*; these are only tried on decrypt so existing ciphertext keeps
    # working after a key rotation. See integrations/sdk/secrets.py.
    INTEGRATION_SECRET_KEY_PREVIOUS: str = ""

    @model_validator(mode="after")
    def _validate_integration_secret_key(self) -> "Settings":
        """Ensure INTEGRATION_SECRET_KEY is provided in production."""
        if not self.INTEGRATION_SECRET_KEY:
            if self.APP_ENV in ("development", "test", "testing"):
                from cryptography.fernet import Fernet
                import logging

                logging.warning(
                    "No INTEGRATION_SECRET_KEY provided; generating an ephemeral one for development. Connected integrations will break on restart."
                )
                self.INTEGRATION_SECRET_KEY = Fernet.generate_key().decode()
            else:
                raise ValueError(
                    f"A valid INTEGRATION_SECRET_KEY must be provided via environment variables for APP_ENV={self.APP_ENV!r}. "
                    "Refusing to boot without one."
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
    UPLOAD_DIR: str = "/var/healthassistant/uploads"
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
    VAPID_PUBLIC_KEY: Optional[str] = None
    VAPID_PRIVATE_KEY: Optional[str] = None
    VAPID_ADMIN_EMAIL: str = "admin@healthassistant.local"

    @model_validator(mode="after")
    def _validate_vapid_keys(self) -> "Settings":
        """VAPID keys are required in production for Web Push delivery.

        In development / test, missing keys are tolerated — Web Push is
        silently skipped (``send_web_push`` returns False with a warning).
        In production, refusing to boot surfaces operator misconfiguration
        early instead of letting push notifications silently fail forever.
        """
        if self.APP_ENV in ("development", "test", "testing"):
            return self
        if not self.VAPID_PUBLIC_KEY or not self.VAPID_PRIVATE_KEY:
            raise ValueError(
                f"VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY must be provided via "
                f"environment variables for APP_ENV={self.APP_ENV!r}. Generate "
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


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


settings = get_settings()
