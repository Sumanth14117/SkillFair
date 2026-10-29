from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SkillEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str = Field(min_length=1, max_length=100)
    evidence_type: Literal["explicit", "inferred"]
    supporting_excerpt: str = Field(min_length=1, max_length=600)
    relevance_explanation: str = Field(min_length=1, max_length=500)
    validation_status: Literal["evidence_found", "inference_unverified"]


class SkillsDiscoverResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_mode: Literal["development", "ai"]
    message: str
    extracted_characters: int = Field(ge=0)
    extracted_text: str = Field(max_length=50_000)
    skills: list[SkillEvidence] = Field(max_length=60)


class JobMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str = Field(min_length=1, max_length=160)
    job_description: str = Field(min_length=1, max_length=10_000)
    required_skills: list[str] = Field(min_length=1, max_length=60)
    candidate_skills: list[SkillEvidence] = Field(max_length=60)

    @field_validator("job_title", "job_description")
    @classmethod
    def require_non_whitespace_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("required_skills")
    @classmethod
    def require_nonblank_skills(cls, value: list[str]) -> list[str]:
        cleaned = [skill.strip() for skill in value]
        if not cleaned or any(not skill for skill in cleaned):
            raise ValueError("required skills must not be blank")
        return cleaned


class SkillMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_skill: str
    candidate_skill: str
    evidence_type: Literal["explicit", "inferred"]
    validation_status: Literal["evidence_found", "inference_unverified"]
    supporting_excerpt: str
    relevance_explanation: str


class JobMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str
    total_required: int
    explicitly_supported: int
    inferred_unverified: int
    missing_count: int
    evidence_coverage_percent: int
    matched_skills: list[SkillMatch]
    missing_skills: list[str]
    calculation_method: str
