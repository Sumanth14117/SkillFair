import re
import unicodedata
from dataclasses import dataclass

from .models import (
    AuditImportance,
    BiasAuditFinding,
    BiasAuditRequest,
    BiasAuditResponse,
)


@dataclass(frozen=True)
class _Criterion:
    text: str
    importance: AuditImportance


_PREFERRED_HEADING = re.compile(r"^(?:preferred|desired|nice\s+to\s+have)(?:\s+(?:qualifications?|requirements?|skills?))?\s*:?[\s—–-]*(.*)$", re.IGNORECASE)
_ESSENTIAL_HEADING = re.compile(r"^(?:essential|required|minimum|must\s+have)(?:\s+(?:qualifications?|requirements?|skills?))?\s*:?[\s—–-]*(.*)$", re.IGNORECASE)
_PREFERRED_CONTEXT = re.compile(r"\b(?:preferred|preferable|desirable|nice\s+to\s+have|bonus|advantageous)\b", re.IGNORECASE)
_ESSENTIAL_CONTEXT = re.compile(r"\b(?:essential|required|require(?:s|ment)?|mandatory|must\s+have|minimum\s+qualification|prerequisite)\b", re.IGNORECASE)
_MANDATORY = re.compile(r"\b(?:required|require(?:s|ment|ments)?|mandatory|must(?:\s+have)?|minimum(?:\s+of)?|at\s+least|prerequisite)\b", re.IGNORECASE)
_NEGATED_REQUIREMENT = re.compile(r"\b(?:not|never|no\s+longer)\s+(?:strictly\s+)?(?:required|mandatory|necessary|a\s+requirement)\b", re.IGNORECASE)
_CREDENTIAL = re.compile(
    r"\b(?:bachelor(?:['’]s)?(?:\s+degree)?|master(?:['’]s)?(?:\s+degree)?|"
    r"associate(?:['’]s)?(?:\s+degree)?|doctoral(?:\s+degree)?|ph\.?d\.?|degree|"
    r"diploma|certification|certificate|credential|professional\s+licen[cs]e|licen[cs]e)\b",
    re.IGNORECASE,
)
_LEGAL_CREDENTIAL_CONTEXT = re.compile(r"\b(?:required\s+by\s+(?:law|regulation|the\s+state)|legally\s+required|statutory|regulatory\s+requirement|licensure\s+requirement|mandated\s+by\s+(?:law|regulation))\b", re.IGNORECASE)
_EQUIVALENT_EXPERIENCE = re.compile(r"\b(?:or|and)\s+(?:equivalent(?:\s+(?:practical\s+)?experience)?|equivalent\s+qualification)\b", re.IGNORECASE)
_EXCLUSIONARY_WORDING = (
    ("native-speaker", re.compile(r"\bnative\s+(?:[a-z]+\s+)?speaker\b", re.IGNORECASE), "State the job-related language proficiency or communication tasks required, rather than native-speaker status."),
    ("digital-native", re.compile(r"\bdigital\s+native\b", re.IGNORECASE), "Name the specific tools or digital tasks the role requires."),
    ("youth-coded", re.compile(r"\byoung\s+(?:and\s+energetic|professional|candidate|graduate)\b", re.IGNORECASE), "Describe the role's actual responsibilities, schedule, and physical requirements where genuinely necessary."),
    ("recent-graduate-only", re.compile(r"\brecent\s+graduates?\s+only\b", re.IGNORECASE), "Describe any genuinely necessary experience or training directly, without restricting eligibility by graduation recency."),
    ("culture-fit", re.compile(r"\bculture\s+fit\b", re.IGNORECASE), "Replace culture-fit language with observable, job-related collaboration behaviors."),
    ("mother-tongue", re.compile(r"\b(?:mother\s+tongue|native\s+language)\b", re.IGNORECASE), "Specify the language proficiency level and tasks required for the role."),
)
_VAGUE_REQUIREMENTS = (
    re.compile(r"\b(?:excellent|strong|good|great)\s+(?:written\s+and\s+verbal\s+)?communication\s+skills?\b", re.IGNORECASE),
    re.compile(r"\bexcellent\s+interpersonal\s+skills?\b", re.IGNORECASE),
    re.compile(r"\bteam\s+players?\b", re.IGNORECASE),
    re.compile(r"\bself[ -]starter\b", re.IGNORECASE),
    re.compile(r"\bdetail[ -]oriented\b", re.IGNORECASE),
    re.compile(r"\b(?:good|great)\s+attitude\b", re.IGNORECASE),
    re.compile(r"\bstrong\s+work\s+ethic\b", re.IGNORECASE),
    re.compile(r"\bhighly\s+motivated\b", re.IGNORECASE),
    re.compile(r"\bstrong\s+problem[ -]solving\s+skills?\b", re.IGNORECASE),
    re.compile(r"\bproven\s+track\s+record\b", re.IGNORECASE),
)
_YEARS_PATTERNS = (
    re.compile(r"(?P<years>\d{1,3})\s*(?P<plus>\+)\s*years?['’]?\s+(?:of\s+)?(?:relevant\s+)?experience\b", re.IGNORECASE),
    re.compile(r"(?P<years>\d{1,3})\s+years?['’]?\s+(?:of\s+)?(?:relevant\s+)?experience\b", re.IGNORECASE),
    re.compile(r"experience\s+(?:of\s+)?(?:(?:at\s+least|minimum(?:\s+of)?)\s+)?(?P<years>\d{1,3})\s*(?P<plus>\+)?\s*years?\b", re.IGNORECASE),
)
_CONTINUOUS_EXPERIENCE = re.compile(
    r"\b(?:(?:minimum(?:\s+of)?|at\s+least)\s+)?\d{1,3}\s*\+?\s+years?\s+(?:of\s+)?"
    r"(?:continuous|consecutive|uninterrupted)\s+(?:professional\s+)?experience\b",
    re.IGNORECASE,
)
_EMPLOYMENT_CONTINUITY_RESTRICTIONS = (
    re.compile(r"\bno\s+(?:(?:any)\s+)?(?:employment|work|career)\s+gaps?\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:(?:any)\s+)?(?:career|employment|work)\s+breaks?\b", re.IGNORECASE),
    re.compile(r"\b(?:uninterrupted|continuous|consecutive)\s+(?:professional\s+)?(?:employment|work)\s+history\b", re.IGNORECASE),
    re.compile(r"\b(?:employment|work)\s+history\s+(?:must\s+be|is\s+required\s+to\s+be)\s+(?:uninterrupted|continuous|consecutive)\b", re.IGNORECASE),
    re.compile(r"\b(?:gap[ -]?free)\s+(?:employment|work|career)\s+(?:history|record)\b", re.IGNORECASE),
)
_CLAUSE_BOUNDARY = re.compile(r"[;\n!?]|(?<=[.!?])\s+(?=[A-Z0-9])")


def _importance_from_text(text: str, fallback: AuditImportance = "unspecified") -> AuditImportance:
    if _PREFERRED_CONTEXT.search(text):
        return "preferred"
    if _ESSENTIAL_CONTEXT.search(text):
        return "essential"
    return fallback


def _description_criteria(description: str) -> list[_Criterion]:
    criteria: list[_Criterion] = []
    section_importance: AuditImportance = "unspecified"
    for raw_line in description.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw_line).strip()
        if not line:
            continue
        heading = _PREFERRED_HEADING.match(line)
        if heading:
            section_importance = "preferred"
            line = heading.group(1).strip()
        else:
            heading = _ESSENTIAL_HEADING.match(line)
            if heading:
                section_importance = "essential"
                line = heading.group(1).strip()
        if not line:
            continue
        # Break ordinary prose into independently classified clauses. Keep punctuation in the evidence.
        clauses = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line)
        criteria.extend(
            _Criterion(clause.strip(), _importance_from_text(clause, section_importance))
            for clause in clauses
            if clause.strip()
        )
    return criteria


def _all_criteria(request: BiasAuditRequest) -> list[_Criterion]:
    candidates = [*_description_criteria(request.job_description)]
    candidates.extend(_Criterion(item.text, item.importance) for item in request.requirements)
    unique: list[_Criterion] = []
    seen: set[str] = set()
    for criterion in candidates:
        key = " ".join(unicodedata.normalize("NFKC", criterion.text).casefold().split())
        if key and key not in seen:
            seen.add(key)
            unique.append(criterion)
    return unique


def _clause_for_span(text: str, start: int, end: int) -> str:
    left = 0
    right = len(text)
    for boundary in _CLAUSE_BOUNDARY.finditer(text):
        if boundary.end() <= start:
            left = boundary.end()
        elif boundary.start() >= end:
            right = boundary.start()
            break
    return text[left:right].strip(" \t,:")


def _finding(
    *, rule_id: str, category: str, priority: str, phrase: str,
    importance: AuditImportance, explanation: str, suggestion: str,
) -> BiasAuditFinding:
    return BiasAuditFinding(
        rule_id=rule_id,
        category=category,
        priority=priority,
        triggering_phrase=phrase,
        criterion_importance=importance,
        explanation=explanation,
        suggested_revision_or_action=suggestion,
    )


def audit_job(request: BiasAuditRequest) -> BiasAuditResponse:
    findings: list[BiasAuditFinding] = []
    seen_findings: set[tuple[str, str, AuditImportance]] = set()

    def add(finding: BiasAuditFinding) -> None:
        key = (finding.rule_id, finding.triggering_phrase.casefold(), finding.criterion_importance)
        if key not in seen_findings:
            seen_findings.add(key)
            findings.append(finding)

    criteria = _all_criteria(request)
    for criterion in criteria:
        text = criterion.text
        importance = criterion.importance if criterion.importance != "unspecified" else _importance_from_text(text)

        for credential in _CREDENTIAL.finditer(text):
            clause = _clause_for_span(text, credential.start(), credential.end())
            if (
                not _MANDATORY.search(clause)
                or _NEGATED_REQUIREMENT.search(clause)
                or _LEGAL_CREDENTIAL_CONTEXT.search(clause)
                or _EQUIVALENT_EXPERIENCE.search(clause)
                or importance == "preferred"
            ):
                continue
            add(_finding(
                rule_id="BA-CREDENTIAL-001",
                category="credential_requirement",
                priority="medium",
                phrase=clause,
                importance=_importance_from_text(clause, importance),
                explanation="A degree or credential is stated as a hard requirement. Review whether it is necessary for the role or whether equivalent demonstrated skills or experience could satisfy the need.",
                suggestion="Document the job-related reason for the credential, or consider accepting equivalent skills, training, or experience where appropriate.",
            ))
            break

        for rule_name, pattern, suggestion in _EXCLUSIONARY_WORDING:
            for match in pattern.finditer(text):
                add(_finding(
                    rule_id=f"BA-WORDING-{rule_name.upper()}-001",
                    category="potentially_exclusionary_wording",
                    priority="medium",
                    phrase=match.group(0),
                    importance=importance,
                    explanation="This wording may act as an unnecessary eligibility signal or may be interpreted inconsistently. Its context and job-related necessity require human review.",
                    suggestion=suggestion,
                ))

        for pattern in _VAGUE_REQUIREMENTS:
            for match in pattern.finditer(text):
                add(_finding(
                    rule_id="BA-VAGUE-SKILL-001",
                    category="vague_requirement",
                    priority="low",
                    phrase=match.group(0),
                    importance=importance,
                    explanation="This skill wording is broad and does not describe an observable behavior or proficiency level, which can lead reviewers to interpret it differently.",
                    suggestion="Describe the work behavior, deliverable, or proficiency level that would demonstrate this skill.",
                ))

        continuity_match = _CONTINUOUS_EXPERIENCE.search(text)
        restrictive_match = next(
            (match for pattern in _EMPLOYMENT_CONTINUITY_RESTRICTIONS if (match := pattern.search(text))),
            None,
        )
        if continuity_match or restrictive_match:
            match = continuity_match or restrictive_match
            clause = _clause_for_span(text, match.start(), match.end())
            add(_finding(
                rule_id="BA-EXPERIENCE-CONTINUITY-001",
                category="experience_threshold",
                priority="medium",
                phrase=clause,
                importance=_importance_from_text(clause, importance),
                explanation=(
                    "A requirement for uninterrupted experience or an employment history without gaps may disadvantage "
                    "career-returning candidates even when they have the relevant skills. Review whether continuity is "
                    "necessary for the actual work rather than treating breaks as a proxy for capability."
                ),
                suggestion=(
                    "Evaluate the role-relevant skills, demonstrated experience, and ability to perform the work; remove "
                    "the continuity restriction unless it is demonstrably necessary."
                ),
            ))

        for pattern in _YEARS_PATTERNS:
            for match in pattern.finditer(text):
                years = int(match.group("years"))
                if years < 10 or importance == "preferred":
                    continue
                clause = _clause_for_span(text, match.start(), match.end())
                if not (match.groupdict().get("plus") or importance == "essential" or _MANDATORY.search(clause)):
                    continue
                if _NEGATED_REQUIREMENT.search(clause):
                    continue
                add(_finding(
                    rule_id="BA-EXPERIENCE-10Y-001",
                    category="experience_threshold",
                    priority="high",
                    phrase=clause,
                    importance=_importance_from_text(clause, importance),
                    explanation="A hard threshold of ten or more years may screen out capable applicants whose skills were developed through other paths. Review whether the threshold is necessary for the role's actual tasks.",
                    suggestion="Specify the capabilities or responsibilities needed and consider whether equivalent experience or demonstrated skills could meet them.",
                ))
                break

    return BiasAuditResponse(
        job_title=request.job_title or None,
        criteria_reviewed=len(criteria),
        finding_count=len(findings),
        findings=findings,
        human_review_required=True,
        disclaimer=(
            "Findings are potential wording or criteria concerns for human review, not determinations that a job is discriminatory, unfair, or unlawful. "
            "No findings do not establish fairness or legal compliance. This audit does not assess candidates or infer demographic characteristics."
        ),
    )
