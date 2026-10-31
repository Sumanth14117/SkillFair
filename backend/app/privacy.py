"""Conservative PII minimization for validated skill evidence fields."""

import re

from .models import SkillEvidence


_EMAIL = re.compile(
    r"(?i)(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+"
)
_NANP_PHONE = re.compile(
    r"(?<!\w)(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}(?!\w)"
)
_INTERNATIONAL_PHONE = re.compile(r"(?<!\w)\+\d{1,3}(?:[ .()\-]*\d){6,12}(?!\w)")
_URL = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>\"']+|"
    r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>\"']*|\?[^\s<>\"']*)"
)
_IDENTIFYING_URL_PARAMETER = re.compile(
    r"(?i)(?:[?&]|%3[fF]|%26)(?:e-?mail|phone|mobile|contact|username|user[_-]?id|profile[_-]?id)="
)
_URL_TRAILING_PUNCTUATION = ".,;:!?)]}"


def redact_evidence_excerpt(excerpt: str) -> str:
    """Mask common contact details while preserving surrounding evidence text."""

    def redact_url(match: re.Match[str]) -> str:
        value = match.group(0)
        end = len(value)
        while end and value[end - 1] in _URL_TRAILING_PUNCTUATION:
            end -= 1
        url, punctuation = value[:end], value[end:]
        if (
            _EMAIL.search(url)
            or _NANP_PHONE.search(url)
            or _INTERNATIONAL_PHONE.search(url)
            or _IDENTIFYING_URL_PARAMETER.search(url)
            or re.search(r"(?i)\bmailto:", url)
        ):
            return "[REDACTED PERSONAL URL]" + punctuation
        return value

    excerpt = _URL.sub(redact_url, excerpt)
    excerpt = _EMAIL.sub("[REDACTED EMAIL]", excerpt)
    excerpt = _NANP_PHONE.sub("[REDACTED PHONE]", excerpt)
    excerpt = _INTERNATIONAL_PHONE.sub("[REDACTED PHONE]", excerpt)
    return excerpt


def is_safe_skill_name(skill_name: str) -> bool:
    """Skill labels cannot safely contain contact values or URLs; reject them."""
    return not (
        _EMAIL.search(skill_name)
        or _NANP_PHONE.search(skill_name)
        or _INTERNATIONAL_PHONE.search(skill_name)
        or _URL.search(skill_name)
    )


def redact_skill_evidence(skill: SkillEvidence) -> SkillEvidence:
    """Return a minimized copy; a suspicious label is not a usable skill record."""
    if not is_safe_skill_name(skill.skill_name):
        raise ValueError("skill name contains contact information or a URL")

    excerpt = redact_evidence_excerpt(skill.supporting_excerpt)
    explanation = redact_evidence_excerpt(skill.relevance_explanation)
    if excerpt == skill.supporting_excerpt and explanation == skill.relevance_explanation:
        return skill.model_copy(deep=True)
    return skill.model_copy(update={
        "supporting_excerpt": excerpt,
        "relevance_explanation": explanation,
    }, deep=True)
