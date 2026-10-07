"""Application settings, loaded from environment variables (prefix ``GEO_``)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MB = 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")

    app_name: str = "Geospatial File Measurement API"
    environment: str = "development"
    log_level: str = "INFO"

    data_dir: Path = Path("data")
    database_url: str | None = Field(
        default=None,
        description="SQLAlchemy URL. Defaults to a SQLite database inside data_dir.",
    )

    max_upload_bytes: int = 50 * MB
    max_archive_members: int = 100
    max_extracted_bytes: int = 250 * MB
    max_compression_ratio: int = 200
    max_features: int = 100_000

    # Largest tolerated relative scale error of a projected CRS before
    # measurements are re-projected into a locally appropriate CRS.
    max_scale_distortion: float = 0.005

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'app.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
