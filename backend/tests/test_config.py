"""Settings that production depends on, tested the way they are really set.

In production these values arrive as strings pasted into a hosting dashboard,
not as Python literals. So the tests go through environment variables wherever
that path differs -- it is the one that can fail.
"""

from __future__ import annotations

import os
import sys

import pytest

from app.config import Settings, normalise_database_url

NEON = "ep-cool-rain-a5b6c7.us-east-2.aws.neon.tech"
NEON_POOLED = "ep-cool-rain-a5b6c7-pooler.us-east-2.aws.neon.tech"
PRODUCTION = {"environment": "production", "secret_key": "s" * 48}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's shell and backend/.env out of these tests."""
    for key in list(os.environ):
        if key.startswith("FORGE_"):
            monkeypatch.delenv(key, raising=False)


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


# --- database URL -----------------------------------------------------------


@pytest.mark.parametrize(
    ("pasted", "expected"),
    [
        pytest.param(
            f"postgresql://app:pw@{NEON}/neondb?sslmode=require&channel_binding=require",
            f"postgresql+asyncpg://app:pw@{NEON}/neondb?ssl=require",
            id="neon-direct",
        ),
        pytest.param(
            f"postgresql://app:pw@{NEON_POOLED}/neondb?sslmode=require",
            f"postgresql+asyncpg://app:pw@{NEON_POOLED}/neondb"
            "?ssl=require&prepared_statement_cache_size=0",
            id="neon-pooled",
        ),
        pytest.param(
            "postgres://app:pw@dpg-abc123-a/resumeforge",
            "postgresql+asyncpg://app:pw@dpg-abc123-a/resumeforge",
            id="render-internal",
        ),
        pytest.param(
            "postgresql://app:p%40ss%25word@host:5432/db",
            "postgresql+asyncpg://app:p%40ss%25word@host:5432/db",
            id="encoded-password-untouched",
        ),
        pytest.param(
            "postgresql+asyncpg://app:pw@host/db?ssl=require",
            "postgresql+asyncpg://app:pw@host/db?ssl=require",
            id="already-normalised",
        ),
        pytest.param(
            "  postgresql://app:pw@host/db \n",
            "postgresql+asyncpg://app:pw@host/db",
            id="stray-whitespace",
        ),
        pytest.param(
            "sqlite+aiosqlite:///./dev.db",
            "sqlite+aiosqlite:///./dev.db",
            id="sqlite",
        ),
    ],
)
def test_pasted_connection_strings_become_asyncpg_urls(
    pasted: str, expected: str
) -> None:
    assert normalise_database_url(pasted) == expected


def test_an_explicit_ssl_setting_is_not_overridden_by_sslmode() -> None:
    url = "postgresql://app:pw@host/db?ssl=verify-full&sslmode=require"
    assert normalise_database_url(url) == "postgresql+asyncpg://app:pw@host/db?ssl=verify-full"


def test_normalisation_applies_to_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FORGE_DATABASE_URL", f"postgresql://app:pw@{NEON}/neondb?sslmode=require"
    )
    assert settings().database_url == f"postgresql+asyncpg://app:pw@{NEON}/neondb?ssl=require"


# --- CORS -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            "https://resumeforge.vercel.app",
            ["https://resumeforge.vercel.app"],
            id="one-bare-url",
        ),
        pytest.param(
            "https://resumeforge.vercel.app/",
            ["https://resumeforge.vercel.app"],
            id="trailing-slash",
        ),
        pytest.param(
            "https://a.vercel.app, https://b.example.com",
            ["https://a.vercel.app", "https://b.example.com"],
            id="comma-separated",
        ),
        pytest.param(
            '["https://a.vercel.app", "http://localhost:4200"]',
            ["https://a.vercel.app", "http://localhost:4200"],
            id="json-list",
        ),
        pytest.param("", [], id="blank"),
    ],
)
def test_cors_origins_accept_what_people_paste(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("FORGE_CORS_ORIGINS", raw)
    assert settings().cors_origins == expected


# --- production guards --------------------------------------------------------


def test_production_refuses_to_start_without_a_database_url() -> None:
    with pytest.raises(ValueError, match="FORGE_DATABASE_URL"):
        settings(**PRODUCTION)


def test_production_refuses_a_blank_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_DATABASE_URL", "")
    with pytest.raises(ValueError, match="FORGE_DATABASE_URL"):
        settings(**PRODUCTION)


def test_production_starts_with_a_real_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FORGE_DATABASE_URL", f"postgresql://app:pw@{NEON}/neondb?sslmode=require"
    )
    assert settings(**PRODUCTION).environment == "production"


# --- storage and compiler ----------------------------------------------------


def test_storage_is_off_unless_an_endpoint_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert settings().storage_configured is False
    monkeypatch.setenv("FORGE_S3_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")
    assert settings().storage_configured is True


def test_a_blank_storage_endpoint_counts_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # What a dashboard stores for an optional variable left empty.
    monkeypatch.setenv("FORGE_S3_ENDPOINT_URL", "")
    assert settings().storage_configured is False


def test_default_tectonic_path_matches_the_platform() -> None:
    expected = "tectonic.exe" if sys.platform == "win32" else "tectonic"
    assert settings().tectonic_path.endswith(expected)
