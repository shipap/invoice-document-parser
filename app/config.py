from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    provider: str = "demo"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    api_token: SecretStr | None = None
    max_upload_mb: int = Field(default=8, ge=1, le=32)
    max_pdf_pages: int = Field(default=30, ge=1, le=100)
    max_text_chars: int = Field(default=60000, ge=1000, le=100000)
    extraction_timeout_seconds: int = Field(default=20, ge=1, le=60)
    requests_per_minute: int = Field(default=20, ge=1, le=1000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
