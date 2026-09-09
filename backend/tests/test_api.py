"""API behaviour tests.

Runs against SQLite with the worker disabled, so these cover the HTTP contract
and the persistence rules rather than the agent pipeline (which needs an API
key and is exercised through the CLI).

The delete-independence tests are the important ones. "Base resumes and
generated versions are independent -- deleting one never affects the other" is
a promise about data the user may need months later, and it is exactly the
kind of guarantee that quietly breaks when someone adds a cascade.
"""

from __future__ import annotations

import pathlib

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import auth_router, gaps_router, generation_router, resume_router
from app.config import Settings, get_settings
from app.db import session as db_session
from app.db.models import Base, Generation, GenerationGap
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
RESUME = (FIXTURES / "jakes_resume.tex").read_text(encoding="utf-8")

JOB_TEXT = """
Senior Backend Engineer, Payments

We are looking for an engineer to work on our microservices platform.

Requirements:
- 5+ years building backend services in Go or Python
- Experience with Postgres at scale
- Familiarity with Kafka or similar event streaming
- Comfortable owning services end to end

Nice to have:
- Kubernetes experience
- Prior fintech or payments work
"""


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    """A test app with an isolated SQLite database and no worker."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"

    settings = Settings(
        database_url=db_url,
        environment="test",
        secret_key="test-secret-key-that-is-long-enough-for-hs256",
        anthropic_api_key=None,
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.config.get_settings", lambda: settings)

    engine = create_async_engine(db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_sessionmaker", maker)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(resume_router)
    app.include_router(generation_router)
    app.include_router(gaps_router)
    app.dependency_overrides[get_settings] = lambda: settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.maker = maker  # type: ignore[attr-defined]
        yield ac

    await engine.dispose()
    get_settings.cache_clear()


async def register(client: AsyncClient, email: str = "a@example.com") -> dict:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-battery", "display_name": "A"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


# --- auth -----------------------------------------------------------------


async def test_register_then_authenticate(client: AsyncClient) -> None:
    tokens = await register(client)
    response = await client.get("/api/auth/me", headers=auth(tokens))
    assert response.status_code == 200
    assert response.json()["email"] == "a@example.com"


async def test_duplicate_email_is_rejected(client: AsyncClient) -> None:
    await register(client)
    response = await client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "another-long-password"},
    )
    assert response.status_code == 409


async def test_login_failure_does_not_reveal_account_existence(
    client: AsyncClient,
) -> None:
    await register(client)
    wrong_password = await client.post(
        "/api/auth/login",
        json={"email": "a@example.com", "password": "wrong-password-here"},
    )
    no_account = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "wrong-password-here"},
    )
    assert wrong_password.status_code == no_account.status_code == 401
    assert wrong_password.json()["detail"] == no_account.json()["detail"]


async def test_refresh_rotates_and_revokes_the_old_token(client: AsyncClient) -> None:
    tokens = await register(client)
    first = tokens["refresh_token"]

    rotated = await client.post("/api/auth/refresh", json={"refresh_token": first})
    assert rotated.status_code == 200
    second = rotated.json()["refresh_token"]
    assert second != first

    # Presenting the consumed token again must fail -- that is what makes a
    # stolen refresh token detectable rather than silently usable.
    replayed = await client.post("/api/auth/refresh", json={"refresh_token": first})
    assert replayed.status_code == 401


async def test_logout_revokes_the_refresh_token(client: AsyncClient) -> None:
    tokens = await register(client)
    await client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    response = await client.post(
        "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 401


async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/resumes")).status_code == 401
    assert (await client.get("/api/generations")).status_code == 401
    assert (await client.get("/api/gaps/roadmap")).status_code == 401


# --- resumes --------------------------------------------------------------


async def test_upload_reports_what_was_parsed(client: AsyncClient) -> None:
    """The upload response must make a mis-parse visible before generation."""
    tokens = await register(client)
    response = await client.post(
        "/api/resumes",
        headers=auth(tokens),
        json={"name": "Backend", "tex_source": RESUME},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["template_profile"] == "jakes"
    assert body["stats"] == {"sections": 4, "entries": 4, "bullets": 10}
    titles = [s["title"] for s in body["sections"]]
    assert titles == ["Education", "Experience", "Projects", "Technical Skills"]


async def test_unparseable_upload_is_rejected(client: AsyncClient) -> None:
    tokens = await register(client)
    response = await client.post(
        "/api/resumes",
        headers=auth(tokens),
        json={"name": "Empty", "tex_source": "   " * 10},
    )
    assert response.status_code == 422


async def test_users_cannot_read_each_others_resumes(client: AsyncClient) -> None:
    alice = await register(client, "alice@example.com")
    bob = await register(client, "bob@example.com")

    created = await client.post(
        "/api/resumes", headers=auth(alice), json={"name": "A", "tex_source": RESUME}
    )
    resume_id = created.json()["id"]

    response = await client.get(f"/api/resumes/{resume_id}", headers=auth(bob))
    assert response.status_code == 404


async def test_soft_deleted_resume_disappears_from_listing(
    client: AsyncClient,
) -> None:
    tokens = await register(client)
    created = await client.post(
        "/api/resumes", headers=auth(tokens), json={"name": "A", "tex_source": RESUME}
    )
    resume_id = created.json()["id"]

    assert len((await client.get("/api/resumes", headers=auth(tokens))).json()) == 1
    assert (
        await client.delete(f"/api/resumes/{resume_id}", headers=auth(tokens))
    ).status_code == 204
    assert len((await client.get("/api/resumes", headers=auth(tokens))).json()) == 0
    assert (
        await client.get(f"/api/resumes/{resume_id}", headers=auth(tokens))
    ).status_code == 404


# --- generations and delete independence ----------------------------------


async def _make_generation(client: AsyncClient, tokens: dict) -> tuple[str, str]:
    created = await client.post(
        "/api/resumes", headers=auth(tokens), json={"name": "Backend", "tex_source": RESUME}
    )
    resume_id = created.json()["id"]
    response = await client.post(
        "/api/generations",
        headers=auth(tokens),
        json={
            "base_resume_id": resume_id,
            "job_text": JOB_TEXT,
            "company": "Northwind",
            "role": "Senior Backend Engineer",
        },
    )
    assert response.status_code == 202, response.text
    return resume_id, response.json()["id"]


async def test_generation_is_queued_with_a_source_snapshot(
    client: AsyncClient,
) -> None:
    tokens = await register(client)
    _, generation_id = await _make_generation(client, tokens)

    detail = await client.get(f"/api/generations/{generation_id}", headers=auth(tokens))
    body = detail.json()
    assert body["status"] == "queued"
    # The snapshot is taken at creation, which is what keeps the generation
    # independent of its base resume later on.
    assert body["tex_source"] == RESUME


async def test_deleting_the_base_resume_leaves_generations_intact(
    client: AsyncClient,
) -> None:
    """The independence guarantee, stated as a test."""
    tokens = await register(client)
    resume_id, generation_id = await _make_generation(client, tokens)

    await client.delete(f"/api/resumes/{resume_id}", headers=auth(tokens))

    detail = await client.get(f"/api/generations/{generation_id}", headers=auth(tokens))
    assert detail.status_code == 200
    assert detail.json()["tex_source"] == RESUME
    assert detail.json()["base_resume_name"] == "Backend"

    listing = await client.get("/api/generations", headers=auth(tokens))
    assert len(listing.json()) == 1


async def test_deleting_a_generation_leaves_the_base_resume_intact(
    client: AsyncClient,
) -> None:
    tokens = await register(client)
    resume_id, generation_id = await _make_generation(client, tokens)

    await client.delete(f"/api/generations/{generation_id}", headers=auth(tokens))

    assert (
        await client.get(f"/api/resumes/{resume_id}", headers=auth(tokens))
    ).status_code == 200
    assert len((await client.get("/api/generations", headers=auth(tokens))).json()) == 0


async def test_regeneration_increments_version_rather_than_overwriting(
    client: AsyncClient,
) -> None:
    tokens = await register(client)
    resume_id, first_id = await _make_generation(client, tokens)

    second = await client.post(
        "/api/generations",
        headers=auth(tokens),
        json={
            "base_resume_id": resume_id,
            "job_text": JOB_TEXT,
            "company": "Northwind",
            "role": "Senior Backend Engineer",
        },
    )
    assert second.json()["version_no"] == 2

    # Both versions survive; you need to know which one they are holding.
    listing = (await client.get("/api/generations", headers=auth(tokens))).json()
    assert sorted(g["version_no"] for g in listing) == [1, 2]


async def test_short_job_description_is_rejected(client: AsyncClient) -> None:
    tokens = await register(client)
    created = await client.post(
        "/api/resumes", headers=auth(tokens), json={"name": "A", "tex_source": RESUME}
    )
    response = await client.post(
        "/api/generations",
        headers=auth(tokens),
        json={"base_resume_id": created.json()["id"], "job_text": "backend dev"},
    )
    assert response.status_code == 422


# --- gap roadmap ----------------------------------------------------------


async def test_gap_roadmap_aggregates_across_applications(
    client: AsyncClient,
) -> None:
    """The sleeper feature: gaps become a roadmap once they accumulate."""
    tokens = await register(client)
    me = (await client.get("/api/auth/me", headers=auth(tokens))).json()

    # Seed three applications, two of which wanted Kafka.
    async with client.maker() as session:  # type: ignore[attr-defined]
        for index, skills in enumerate(
            [["Kafka", "Kubernetes"], ["Kafka"], ["Terraform"]]
        ):
            generation = Generation(
                user_id=me["id"],
                company=f"Company{index}",
                role="Backend Engineer",
                status="completed",
            )
            session.add(generation)
            await session.flush()
            for skill in skills:
                session.add(
                    GenerationGap(
                        generation_id=generation.id,
                        user_id=me["id"],
                        skill=skill,
                        normalized_skill=skill.lower(),
                        kind="required",
                        company=f"Company{index}",
                    )
                )
        await session.commit()

    roadmap = (await client.get("/api/gaps/roadmap", headers=auth(tokens))).json()
    assert roadmap["total_applications"] == 3
    top = roadmap["items"][0]
    assert top["skill"] == "Kafka"
    assert top["occurrences"] == 2
    assert sorted(top["companies"]) == ["Company0", "Company1"]
