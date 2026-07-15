from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    project_name: str = "Agentic HRMS"
    app_version: str = "0.1.0"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"

    database_url: str = Field(
        default="postgresql+psycopg://hrms:hrms@localhost:5432/hrms",
        validation_alias="DATABASE_URL",
    )

    jwt_secret_key: str = Field(default="change-me-before-production", validation_alias="JWT_SECRET_KEY")
    jwt_algorithm: str = "HS256"
    # Was missing its env alias, so ACCESS_TOKEN_EXPIRE_MINUTES in .env was
    # silently ignored and this always stayed at 60 regardless of config.
    access_token_expire_minutes: int = Field(default=60, validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, validation_alias="REFRESH_TOKEN_EXPIRE_DAYS")

    admin_email: str = Field(default="admin@example.com", validation_alias="ADMIN_EMAIL")
    admin_password: str = Field(default="ChangeMe123!", validation_alias="ADMIN_PASSWORD")
    admin_name: str = Field(default="Super Admin", validation_alias="ADMIN_NAME")

    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_intent_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_INTENT_MODEL")
    openai_intent_enabled: bool = Field(default=True, validation_alias="OPENAI_INTENT_ENABLED")
    intent_confidence_threshold: float = Field(default=0.55, validation_alias="INTENT_CONFIDENCE_THRESHOLD")

    # Was hardcoded with no env alias, so every deployment shared the same
    # localhost-only origin list regardless of environment. Now reads from
    # CORS_ORIGINS as a comma-separated string, e.g.:
    #   CORS_ORIGINS=https://app.example.com,https://admin.example.com
    # Falls back to the local dev origins when unset, so local dev workflow
    # is unchanged.
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        validation_alias="CORS_ORIGINS",
    )

    resume_storage_dir: str = Field(default="storage/resumes", validation_alias="RESUME_STORAGE_DIR")
    max_resume_upload_mb: int = Field(default=10, validation_alias="MAX_RESUME_UPLOAD_MB")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Allow CORS_ORIGINS to be set as a plain comma-separated string in
        .env, rather than requiring JSON-list syntax. Leaves list/None values
        (e.g. the Python-side default) untouched."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()