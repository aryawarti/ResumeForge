"""Request and response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

__all__ = [
    "RegisterRequest",
    "LoginRequest",
    "RefreshRequest",
    "TokenPair",
    "UserOut",
    "ResumeUploadRequest",
    "ResumeOut",
    "ResumeDetail",
    "GenerateRequest",
    "GenerationOut",
    "GenerationDetail",
    "GapRoadmapItem",
    "GapRoadmap",
]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str
    created_at: datetime


class ResumeUploadRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    tex_source: str = Field(min_length=20)
    profile: str | None = None


class ResumeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    template_profile: str
    page_count: int
    compiles: bool
    created_at: datetime


class ResumeDetail(ResumeOut):
    """Upload response. ``stats`` and ``warnings`` let the user confirm the
    parse before spending a generation on a document we read wrongly."""

    tex_source: str
    stats: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    base_resume_id: str
    job_text: str = Field(default="", max_length=60_000)
    job_url: str = Field(default="", max_length=1000)
    company: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=200)


class GenerationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    company: str
    role: str
    version_no: int
    base_resume_name: str
    page_count: int
    coverage_score: float
    created_at: datetime


class GenerationDetail(GenerationOut):
    tex_source: str
    error: str
    pdf_url: str | None = None
    coverage: dict[str, Any] = Field(default_factory=dict)
    change_report: dict[str, Any] = Field(default_factory=dict)
    gap_report: dict[str, Any] = Field(default_factory=dict)


class GapRoadmapItem(BaseModel):
    skill: str
    occurrences: int
    required_count: int
    preferred_count: int
    companies: list[str]


class GapRoadmap(BaseModel):
    """Gaps aggregated across applications -- the market-derived roadmap."""

    total_applications: int
    items: list[GapRoadmapItem]
