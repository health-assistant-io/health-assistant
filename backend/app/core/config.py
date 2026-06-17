import secrets
from pydantic import model_validator
from pydantic_settings import BaseSettings
import os
from typing import Optional, Any
from functools import lru_cache


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Health Assistant"
    VERSION: str = "0.2.1-rc.2"
    APP_ENV: str = "development"
    DEBUG: bool = False

    # Database
    POSTGRES_USER: str = "admin"
    POSTGRES_PASSWORD: str = "admin123"
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
    SECRET_KEY: str = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
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
    VAPID_PUBLIC_KEY: Optional[str] = os.getenv("VAPID_PUBLIC_KEY")
    VAPID_PRIVATE_KEY: Optional[str] = os.getenv("VAPID_PRIVATE_KEY")
    VAPID_ADMIN_EMAIL: str = "admin@healthassistant.local"

    # Ports (for docker)
    BACKEND_PORT: int = 8000
    FRONTEND_PORT: int = 3000
    FLOWER_PORT: int = 5555

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


settings = get_settings()
