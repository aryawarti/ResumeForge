"""Application settings.

Local development and the free-tier deployment target differ only in the URLs
here: Postgres is Postgres whether it comes from docker-compose or Neon, and
the storage layer speaks S3 whether that is MinIO or Cloudflare R2. Keeping
the difference to configuration is what makes the two environments the same
code path.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_prefix="FORGE_",
        extra="ignore",
    )

    # -- application ---------------------------------------------------
    environment: str = "development"
    debug: bool = True
    secret_key: str = Field(
        default="dev-only-change-me",
        description="Signing key for access and refresh tokens.",
    )
    cors_origins: list[str] = ["http://localhost:4200"]

    # -- database ------------------------------------------------------
    database_url: str = "postgresql+asyncpg://forge:forge@localhost:5432/resumeforge"

    # -- object storage (S3-compatible: MinIO locally, R2 in production)
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_bucket: str = "resumeforge"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_region: str = "auto"
    presigned_url_ttl_seconds: int = 900

    # -- Claude --------------------------------------------------------
    anthropic_api_key: str | None = None
    model: str = "claude-opus-5"
    # Reading a posting and planning edits is judgement-heavy work where a
    # wrong call costs a rejected edit or a missed match, so the pipeline
    # runs at high effort by default rather than the cheapest setting.
    effort: str = "high"
    max_tokens: int = 16000

    # -- compilation ---------------------------------------------------
    compile_backend: str = "auto"
    tectonic_path: str = str(BACKEND_ROOT / ".tools" / "tectonic.exe")
    compile_timeout_seconds: int = 90
    max_compile_attempts: int = 3
    max_gap_fill_rounds: int = 2
    max_trim_rounds: int = 3

    # -- worker --------------------------------------------------------
    # Render's free tier has no background worker service, so generation runs
    # in-process. One concurrent job keeps the web process responsive on a
    # 0.1 CPU / 512 MB instance.
    worker_concurrency: int = 1
    job_poll_interval_seconds: float = 2.0
    job_stale_after_seconds: int = 600

    @property
    def tectonic_binary(self) -> str:
        path = Path(self.tectonic_path)
        if path.exists():
            return str(path)
        alt = path.with_suffix("") if path.suffix else path.with_suffix(".exe")
        return str(alt) if alt.exists() else "tectonic"


@lru_cache
def get_settings() -> Settings:
    return Settings()
