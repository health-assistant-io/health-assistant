import secrets
from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import os
from typing import Optional
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

    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        candidate = parent / ".env"
        if candidate.is_file():
            return str(candidate)

    return None


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Health Assistant"
    VERSION: str = "0.3.0-rc.6"
    APP_ENV: str = "development"
    DEBUG: bool = False

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
            if active_password in weak_passwords:
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

    # Security
    # SECRET_KEY signs JWTs. It must be explicitly provided in production.
    # Read via pydantic (Optional[str] = None) rather than os.getenv at class
    # definition time — the os.getenv default baked in the value before the
    # prod-guard validator below could reject it, making it untestable and
    # inconsistent with how VAPID keys are handled (audit C7).
    SECRET_KEY: Optional[str] = None

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
        return self

    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24

    # URLs
    FRONTEND_URL: str = "http://localhost:5173"
    APP_URL: str = "http://localhost:8000"

    # AI/OCR - OpenAI Compatible API (used as fallback if no database configuration exists)
    OCR_PROVIDER: str = "openai"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_API_BASE: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4-vision-preview"
    OPENAI_MAX_TOKENS: int = 65536
    OPENAI_TIMEOUT: int = 30

    # AI Agent
    AI_AGENT_MAX_ITERATIONS: int = 20

    # MCP Client integration (see integrations/mcp_client/)
    # Pydantic-read (audit C7) — same rationale as SECRET_KEY above.
    INTEGRATION_SECRET_KEY: Optional[str] = None

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

    MCP_STDIO_ALLOWED_COMMANDS: str = "npx,uvx,python,python3,node"
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
