from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    database_url: str = "postgresql+asyncpg://localhost/testlab"
    app_env: str = "development"
    app_encryption_key: str | None = None
    cors_origins: str = "http://localhost:3000"
    testlab_fake_llm: bool = False
    testlab_allow_insecure_local: bool = False
    openrouter_referer: str | None = None
    openrouter_title: str | None = None
    credential_rate_limit: int = Field(20, ge=1)
    chat_rate_limit: int = Field(30, ge=1)

    @model_validator(mode="after")
    def check_settings(self):
        if "*" in self.cors_origins.split(","):
            raise ValueError("Use explicit CORS origins")
        if self.app_env == "production" and not self.app_encryption_key:
            raise ValueError("APP_ENCRYPTION_KEY is required in production")
        return self

    def connection(self):
        url = make_url(self.database_url).set(drivername="postgresql+asyncpg")
        query = dict(url.query)
        mode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        args = {"timeout": 20}
        if mode:
            # Keep server certificate validation enabled for remote connections.
            import ssl

            args["ssl"] = (
                ssl.create_default_context()
                if mode in {"require", "verify-ca", "verify-full"}
                else mode
            )
        if "-pooler" in (url.host or ""):
            args["statement_cache_size"] = 0
            args["prepared_statement_cache_size"] = 0
        return url.set(query=query), args
