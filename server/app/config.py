import ssl

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None, extra="ignore", hide_input_in_errors=True
    )
    # Upgrading from the old default: ALTER DATABASE testlab RENAME TO agentlab;
    database_url: str = "postgresql+asyncpg://localhost/agentlab"
    app_env: str = "development"
    app_encryption_key: str | None = None
    cors_origins: str = "http://localhost:3000"
    agentlab_allow_insecure_local: bool = False
    # Lets callers without AGENTLAB_API_KEY use the default Meta key, from localhost only.
    agentlab_unauthenticated_default_key: bool = False
    agentlab_api_key: str | None = Field(default=None, exclude=True, repr=False)
    testlab_api_key: str | None = Field(default=None, exclude=True, repr=False)
    meta_api_key: SecretStr | None = None
    meta_base_url: str | None = None
    meta_model: str | None = None
    credential_rate_limit: int = Field(20, ge=1)
    chat_rate_limit: int = Field(30, ge=1)

    @model_validator(mode="after")
    def check_settings(self):
        if self.testlab_api_key and not self.agentlab_api_key:
            raise ValueError("Rename TESTLAB_API_KEY to AGENTLAB_API_KEY")
        if "*" in [origin.strip() for origin in self.cors_origins.split(",")]:
            raise ValueError("Use explicit CORS origins")
        if self.app_env == "production" and not self.app_encryption_key:
            raise ValueError("APP_ENCRYPTION_KEY is required in production")
        if self.app_env == "production" and self.agentlab_unauthenticated_default_key:
            raise ValueError("The unauthenticated default key is development-only")
        if self.app_env == "production" and self.agentlab_allow_insecure_local:
            raise ValueError("Insecure localhost URLs are development-only")
        if self.app_env == "production" and not self.agentlab_api_key:
            raise ValueError("AGENTLAB_API_KEY is required in production")
        if self.app_encryption_key:
            try:
                Fernet(self.app_encryption_key)
            except Exception as exc:
                raise ValueError(
                    "APP_ENCRYPTION_KEY must be a valid Fernet key"
                ) from exc
        return self

    def connection(self):
        url = make_url(self.database_url).set(drivername="postgresql+asyncpg")
        query = dict(url.query)
        mode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        args = {"timeout": 20}
        if mode:
            # Keep server certificate validation enabled for remote connections.
            args["ssl"] = (
                ssl.create_default_context()
                if mode in {"require", "verify-ca", "verify-full"}
                else mode
            )
        if "-pooler" in (url.host or ""):
            args["statement_cache_size"] = 0
            args["prepared_statement_cache_size"] = 0
        return url.set(query=query), args
