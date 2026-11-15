"""Application settings, loaded from environment variables with the KB_ prefix."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KB_",
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    issuer: str = "http://localhost:8080/realms/kb"
    resource_url: str = "http://localhost:8000/mcp"
    jwks_uri: str = "http://localhost:8080/realms/kb/protocol/openid-connect/certs"
    database_url: str = "postgresql://kb:kb@localhost:5432/kb"

    # Week 2 uses an in-memory store so the tools run before Docker is installed.
    # Switch to "postgres" once the compose stack is up.
    storage_backend: Literal["memory", "postgres"] = "memory"
    seed_demo_data: bool = True

    # Week 1/2 run without Keycloak. Turn this on once the realm is imported.
    auth_enabled: bool = False

    # Development identity used only when auth is disabled.
    dev_user: str = "alice"
    dev_sub: str = "dev-alice"
    dev_department: str = "engineering"
    dev_roles: str = "employee"
    dev_scopes: str = "kb.read kb.write"

    # Per-subject tool-call budget. 0 disables the limiter.
    rate_limit_per_minute: int = 60

    log_level: str = "INFO"
    log_json: bool = False
    host: str = "0.0.0.0"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
