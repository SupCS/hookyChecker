from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///hooky_checker.db"
    public_api_url: str = "http://localhost:8000"
    railway_public_domain: str | None = None
    business_timezone: str = "Asia/Tbilisi"
    drop_relative_threshold: float = Field(default=0.20, ge=0, le=1)
    drop_absolute_threshold: float = Field(default=10, ge=0)

    @property
    def effective_public_api_url(self) -> str:
        if self.railway_public_domain:
            return f"https://{self.railway_public_domain}"
        return self.public_api_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
