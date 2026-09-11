"""Application settings.

Local development and the free-tier deployment target differ only in the URLs
here: Postgres is Postgres whether it comes from docker-compose or Neon, and
the storage layer speaks S3 whether that is MinIO or Cloudflare R2. Keeping
the difference to configuration is what makes the two environments the same
code path.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# The docker-compose database. Also how production detects that
# FORGE_DATABASE_URL was never set.
DEFAULT_DATABASE_URL = "postgresql+asyncpg://forge:forge@localhost:5432/resumeforge"


def normalise_database_url(url: str) -> str:
    """Accept a Postgres connection string exactly as a provider displays it.

    Neon, Render and Supabase hand out ``postgres://`` or ``postgresql://``
    URLs carrying libpq parameters. The async engine needs the asyncpg driver
    named, and asyncpg refuses ``sslmode`` and ``channel_binding`` as keyword
    arguments -- so the pasted string fails on first connect with "unexpected
    keyword argument 'sslmode'", which reads like a bug in the app rather than
    a format mismatch. Rewriting it here gives the app and Alembic the same
    corrected form.

    Pooled hosts (Neon's ``-pooler``, Supabase's pooler) also get asyncpg's
    prepared-statement cache turned off: under transaction pooling, a statement
    prepared on one server connection may not exist on the next.
    """
    url = url.strip()
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    if scheme in {"postgres", "postgresql"}:
        scheme = "postgresql+asyncpg"
    elif scheme != "postgresql+asyncpg":
        # SQLite, or a deliberately chosen different driver.
        return url

    parts = urlsplit(f"{scheme}://{rest}")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))

    sslmode = query.pop("sslmode", None)
    # libpq-only; asyncpg negotiates SCRAM channel binding on its own.
    query.pop("channel_binding", None)
    if sslmode and "ssl" not in query:
        query["ssl"] = sslmode

    pooled = "pooler" in (parts.hostname or "")
    if pooled and "prepared_statement_cache_size" not in query:
        query["prepared_statement_cache_size"] = "0"

    return urlunsplit(
        (scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


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
    # A JSON list or a comma-separated string -- see _parse_origins.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:4200"]
    # Optional. Vercel gives every preview deployment its own hostname, which a
    # fixed list cannot anticipate, e.g. ^https://resumeforge-[a-z0-9-]+\.vercel\.app$
    cors_origin_regex: str | None = None

    # -- database ------------------------------------------------------
    # Paste the connection string as your provider shows it; see
    # normalise_database_url for what gets rewritten and why.
    database_url: str = DEFAULT_DATABASE_URL

    # -- object storage (optional, any S3-compatible service) -----------
    # Unset turns PDF download off. Tailoring still works and the .tex is still
    # offered, so storage can be added after the first deploy rather than
    # blocking it. See storage_configured.
    s3_endpoint_url: str | None = None
    s3_bucket: str = "resumeforge"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_region: str = "auto"
    presigned_url_ttl_seconds: int = 900

    # -- model provider ------------------------------------------------
    # "anthropic" is the reference target and what the prompts were written
    # against. "groq" runs the same pipeline on open models behind an
    # OpenAI-compatible endpoint; the guards are code either way, so the
    # no-hallucination guarantee does not depend on which is selected.
    llm_provider: str = "anthropic"

    anthropic_api_key: str | None = None
    model: str = "claude-opus-5"

    # -- Groq ----------------------------------------------------------
    groq_api_key: str | None = None
    # gpt-oss-120b is the largest instruction-following model on Groq that
    # supports strict constrained decoding, which the typed edit vocabulary
    # depends on.
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Reading a posting and planning edits is judgement-heavy work where a
    # wrong call costs a rejected edit or a missed match, so the pipeline
    # runs at high effort by default rather than the cheapest setting.
    effort: str = "high"
    max_tokens: int = 16000

    # -- compilation ---------------------------------------------------
    compile_backend: str = "auto"
    tectonic_path: str = str(
        BACKEND_ROOT
        / ".tools"
        / ("tectonic.exe" if sys.platform == "win32" else "tectonic")
    )
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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: Any) -> Any:
        """Accept a JSON list or a comma-separated string.

        A hosting dashboard invites pasting one bare URL. Declared as a plain
        list, pydantic-settings would try to JSON-decode that and refuse to
        start, with an error that never mentions CORS.
        """
        if isinstance(value, str):
            text = value.strip()
            value = json.loads(text) if text.startswith("[") else text.split(",")
        if isinstance(value, (list, tuple)):
            # The browser's Origin header never ends in a slash, so a pasted
            # trailing slash would make every request fail CORS silently.
            origins = (str(item).strip().rstrip("/") for item in value)
            return [origin for origin in origins if origin]
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalise_database_url(cls, value: Any) -> Any:
        return normalise_database_url(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _check_production(self) -> "Settings":
        """Refuse to start in production with settings that are unsafe or cannot work.

        HS256 keys shorter than 32 bytes weaken the signature, and the
        development default is public. Failing at startup beats issuing
        forgeable tokens.
        """
        if self.environment not in {"development", "test"}:
            if self.secret_key == "dev-only-change-me":
                raise ValueError(
                    "FORGE_SECRET_KEY is still the development default; "
                    "set a real one before deploying"
                )
            if len(self.secret_key.encode()) < 32:
                raise ValueError(
                    "FORGE_SECRET_KEY must be at least 32 bytes for HS256"
                )
            if not self.database_url or self.database_url == DEFAULT_DATABASE_URL:
                # Otherwise the first symptom is "connection refused" to
                # localhost from inside a container, which points everywhere
                # except at the missing environment variable.
                raise ValueError(
                    "FORGE_DATABASE_URL is not set; use your Neon (or other "
                    "Postgres) connection string"
                )
        return self

    @property
    def tectonic_binary(self) -> str:
        path = Path(self.tectonic_path)
        if path.exists():
            return str(path)
        alt = path.with_suffix("") if path.suffix else path.with_suffix(".exe")
        return str(alt) if alt.exists() else "tectonic"

    @property
    def storage_configured(self) -> bool:
        """Whether PDFs can be stored at all.

        Checked before any storage call rather than discovered by failing one:
        an unreachable endpoint costs a connect timeout per retry, and on a
        free instance that would add seconds to every generation.
        """
        return bool(self.s3_endpoint_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
