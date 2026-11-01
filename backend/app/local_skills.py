"""Offline, exact-mention skill detection with conservative context requirements."""

import re
from dataclasses import dataclass

from .models import SkillEvidence


SKILL_VOCABULARY = (
    "Python", "Java", "C", "C++", "C#", "JavaScript", "TypeScript", "SQL", "HTML", "CSS",
    "React", "Node.js", "Express", "FastAPI", "Machine Learning", "Deep Learning",
    "Artificial Intelligence", "Data Analysis", "Statistics", "NumPy", "Pandas", "Scikit-learn",
    "TensorFlow", "PyTorch", "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Git", "Docker", "AWS",
    "Azure", "Excel",
)

_SECTION_HEADER = re.compile(
    r"^\s*(?P<header>programming\s+languages|front[\s-]?end|back[\s-]?end|databases?|"
    r"tools|core\s+skills|core\s+competencies|technical\s+skills?|skills\s*(?:&|and)\s*technologies|"
    r"skills|technologies|tech\s+stack|professional\s+experience|work\s+experience|"
    r"internship\s+experience|internships?|experience|back[\s-]?end(?:\s+development)?|"
    r"employment|(?:academic|personal|relevant)\s+projects?|projects?|certifications?|education|professional\s+summary|summary|profile|"
    r"certificates?|objective|awards?|publications?|volunteer(?:ing)?|languages|references|interests)\s*$",
    re.IGNORECASE,
)
_CANONICAL_SECTIONS = {
    "programming languages": "Programming Languages",
    "frontend": "Frontend", "front end": "Frontend", "front-end": "Frontend",
    "backend": "Backend", "back end": "Backend", "back-end": "Backend",
    "backend development": "Backend", "back end development": "Backend", "back-end development": "Backend",
    "database": "Databases", "databases": "Databases",
    "tools": "Tools",
    "core skills": "Core Skills", "core competencies": "Core Skills",
    "professional experience": "Professional Experience", "work experience": "Professional Experience",
    "internship experience": "Internship Experience", "internship": "Internship Experience",
    "internships": "Internship Experience", "experience": "Professional Experience", "employment": "Professional Experience",
    "project": "Projects", "projects": "Projects",
    "academic project": "Projects", "academic projects": "Projects",
    "personal project": "Projects", "personal projects": "Projects",
    "relevant project": "Projects", "relevant projects": "Projects",
    "certificate": "Certifications", "certificates": "Certifications",
    "certification": "Certifications", "certifications": "Certifications",
    "technical skill": "Technical Skills", "technical skills": "Technical Skills",
    "skills": "Skills", "skills & technologies": "Skills & Technologies",
    "skills and technologies": "Skills & Technologies", "technologies": "Technologies",
    "tech stack": "Tech Stack", "education": "Education",
    "professional summary": "Professional Summary", "summary": "Summary", "profile": "Profile",
    "objective": "Objective", "awards": "Awards", "publications": "Publications",
    "volunteer": "Volunteer", "volunteering": "Volunteer", "languages": "Languages",
    "references": "References", "interests": "Interests",
}
_LIST_SKILL_SECTIONS = {
    "Programming Languages", "Frontend", "Backend", "Databases", "Tools",
    "Core Skills", "Technical Skills", "Skills", "Skills & Technologies", "Technologies", "Tech Stack",
    "Certifications",
}
_INTERNSHIP_ENTRY = re.compile(r"\binternships?\b", re.IGNORECASE)
_BULLET_PREFIX = re.compile(r"^\s*[\u2022\u25cf\u25aa\u25ab\u25cb\u25e6\u2043\u2219\ufffd*]+\s*")
_USAGE_CUE = re.compile(
    r"\b(?:used|using|worked\s+with|experience\s+(?:using|with|in)|proficient\s+in|"
    r"built\s+with|built\s+using|developed\s+with|developed\s+using|implemented\s+with|"
    r"implemented\s+using|created\s+with|created\s+using|coded\s+in|programmed\s+in|"
    r"written\s+in|automated\s+with|deployed\s+with|trained\s+with|queried\s+with|"
    r"analyzed\s+with|analysed\s+with)\b",
    re.IGNORECASE,
)
_NEGATED_CUE = re.compile(r"\b(?:not|never|no)\s+(?:used|using|experience|proficient)\b", re.IGNORECASE)


def _skill_pattern(skill: str) -> re.Pattern[str]:
    if skill == "C":
        return re.compile(r"(?<![\w+#])C(?![\w+#])", re.IGNORECASE)
    if skill == "C++":
        return re.compile(r"(?<![\w+#])C\s*\+\s*\+(?![\w+#])", re.IGNORECASE)
    if skill == "C#":
        return re.compile(r"(?<![\w+#])C\s*#(?![\w+#])", re.IGNORECASE)
    if skill == "Node.js":
        term = r"Node\s*\.\s*js"
    elif skill == "Scikit-learn":
        term = r"Scikit[\s-]+learn"
    elif skill in {"Machine Learning", "Deep Learning", "Artificial Intelligence", "Data Analysis"}:
        term = re.escape(skill).replace(r"\ ", r"[\s-]+")
    else:
        term = re.escape(skill)
    return re.compile(rf"(?<!\w){term}(?!\w)", re.IGNORECASE)


_PATTERNS = {skill: _skill_pattern(skill) for skill in SKILL_VOCABULARY}
_PROJECT_ACTION = re.compile(
    r"^\s*(?:designed|built|developed|implemented|created|deployed|maintained|"
    r"analyzed|analysed|tested|automated|integrated|programmed|engineered|"
    r"documented|visualized|visualised|trained|queried|launched|delivered)\b",
    re.IGNORECASE,
)
_PROJECT_STACK_LABEL = re.compile(r"^\s*(?:technologies|technology|tech\s+stack|stack|tools)\s*[:：]\s*", re.IGNORECASE)


@dataclass(frozen=True)
class _Context:
    start: int
    end: int
    kind: str
    section_name: str | None
    line_start: int
    line_end: int


def _line_spans(text: str):
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")
        yield offset, offset + len(content), content
        offset += len(line)


def _section_header(line: str) -> tuple[str, str | None] | None:
    """Recognize standalone headings or explicit `Heading: inline content` forms."""
    candidate = _BULLET_PREFIX.sub("", line).strip()
    if not candidate:
        return None

    inline: str | None = None
    if ":" in candidate or "：" in candidate:
        separator = ":" if ":" in candidate else "："
        label, remainder = candidate.split(separator, 1)
        if _SECTION_HEADER.fullmatch(label.strip()):
            candidate = label.strip()
            inline = remainder.strip() or None
    else:
        # Some exported resumes decorate a heading with a trailing dash or colon.
        candidate = re.sub(r"[\s:：\-–—\u2022\u25cf\u25aa\u25ab\u25cb\u25e6\u2043\u2219\ufffd*]+$", "", candidate)

    match = _SECTION_HEADER.fullmatch(candidate)
    if not match:
        return None
    raw_header = re.sub(r"\s+", " ", match.group(0).strip().lower())
    raw_header = re.sub(r"[\-–—]+", " ", raw_header)
    raw_header = re.sub(r"\s+", " ", raw_header)
    return raw_header, inline


def _is_bullet_line(line: str) -> bool:
    return bool(_BULLET_PREFIX.match(line))


def _is_skill_list_line(line: str) -> bool:
    """Avoid treating a plain vocabulary item as an unknown section heading."""
    candidate = _BULLET_PREFIX.sub("", line).strip()
    if not candidate:
        return False
    parts = re.split(r"\s*(?:[,;/|]|\band\b)\s*", candidate, flags=re.IGNORECASE)
    if len(parts) > 1:
        return all(any(pattern.fullmatch(part.strip()) for pattern in _PATTERNS.values()) for part in parts)
    return any(pattern.fullmatch(candidate) for pattern in _PATTERNS.values())


def _is_unknown_heading(line: str) -> bool:
    """Recognize conservative heading-like boundaries, including trailing colons."""
    if _is_bullet_line(line) or _is_skill_list_line(line):
        return False
    candidate = _BULLET_PREFIX.sub("", line).strip()
    if not candidate or len(candidate) > 50:
        return False
    if any(char in candidate for char in ",;.!?\ufffd\u00b7\u2013\u2014"):
        return False
    explicit_heading = candidate.endswith((":", "："))
    candidate = candidate.rstrip(" :：\t")
    if len(candidate) < 2:
        return False
    if explicit_heading or candidate.isupper():
        return True
    # Title-like short labels are plausible headings; sentence-like text is not.
    if candidate.istitle():
        return True
    return False


def _project_entry_segment(line: str) -> str | None:
    """Return text eligible for exact matching inside a Projects section.

    Project titles are not treated as evidence. Accept a technology list, an
    explicitly labelled stack, the descriptive side of a title separator, or
    a line that reads like a project description. The original whole line is
    still retained as the supporting excerpt.
    """
    candidate = _BULLET_PREFIX.sub("", line).strip()
    if not candidate:
        return None

    stack_label = _PROJECT_STACK_LABEL.match(candidate)
    if stack_label:
        return candidate[stack_label.end():]
    if _is_unknown_heading(line):
        return None

    # A comma/and separated line containing only vocabulary terms is an
    # explicit technology list, not an inferred claim about a project title.
    parts = re.split(r"\s*(?:[,;/|]|\band\b)\s*", candidate, flags=re.IGNORECASE)
    if len(parts) > 1 and all(any(pattern.fullmatch(part.strip()) for pattern in _PATTERNS.values()) for part in parts):
        return candidate

    # In `Project title — stack or description`, exclude the title side from
    # detection while keeping the complete source line as evidence.
    separator = re.search(r"\s*(?:—|–|\||:)\s*", candidate)
    if separator and candidate[separator.end():].strip():
        return candidate[separator.end():]

    # Plain project prose is eligible only when it has a clear descriptive
    # action at the start. This avoids scanning title-only lines such as
    # "Python Inventory Dashboard".
    if _PROJECT_ACTION.match(candidate):
        return candidate

    return None


def _contexts(text: str) -> list[_Context]:
    contexts: list[_Context] = []
    section_name: str | None = None
    allow_skill_list = False
    previous_line_continues = False
    for start, end, line in _line_spans(text):
        heading = _section_header(line)
        if heading:
            raw_header, inline = heading
            section_name = _CANONICAL_SECTIONS.get(raw_header)
            allow_skill_list = section_name in _LIST_SKILL_SECTIONS
            previous_line_continues = False
            if inline:
                inline_start = start + line.find(inline)
                if allow_skill_list:
                    contexts.append(_Context(inline_start, inline_start + len(inline), "skills_section", section_name, start, end))
                else:
                    _append_usage_contexts(contexts, line, start, section_name, start, end)
            continue
        if section_name == "Projects" and _is_unknown_heading(line):
            candidate = _BULLET_PREFIX.sub("", line).strip()
            if candidate.istitle() and any(pattern.search(candidate) for pattern in _PATTERNS.values()):
                # A title-cased project title can itself contain technology
                # words. Skip that title as evidence while keeping the Projects
                # section active for its subsequent description/list lines.
                previous_line_continues = False
                continue
        if _is_unknown_heading(line):
            if previous_line_continues:
                # PDF text extraction can wrap a credential's issuer onto the next
                # line after a trailing dash. Keep the active section but never
                # merge the issuer line into a skill excerpt.
                previous_line_continues = False
                continue
            # An unfamiliar heading is still a boundary; do not carry the prior
            # section label into it or attribute it to a guessed category.
            section_name = None
            allow_skill_list = False
            previous_line_continues = False
            continue
        if not line.strip():
            previous_line_continues = False
            continue
        if section_name == "Projects":
            project_segment = _project_entry_segment(line)
            if project_segment:
                segment_start = start + line.find(project_segment)
                contexts.append(_Context(segment_start, segment_start + len(project_segment), "project_entry", "Projects", start, end))
            previous_line_continues = False
            continue
        if _INTERNSHIP_ENTRY.search(line):
            contexts.append(_Context(start, end, "experience_title", "Internship Experience", start, end))
            previous_line_continues = bool(re.search(r"[-–—]\s*$", line))
            continue
        if allow_skill_list and re.match(r"^\s*(?:experience\s+(?:using|with|in)|worked\b|built\b|developed\b|implemented\b|created\b)", line, re.IGNORECASE):
            # A narrative sentence inside or after a list is not another list item.
            # Drop the old section attribution before applying usage-context rules.
            section_name = None
            allow_skill_list = False
        if allow_skill_list:
            contexts.append(_Context(start, end, "skills_section", section_name, start, end))
            previous_line_continues = bool(re.search(r"[-–—]\s*$", line))
            continue

        # Outside a skills section, only accept a term in the short phrase governed by
        # a direct usage/experience cue. General mentions in prose or boilerplate are skipped.
        _append_usage_contexts(contexts, line, start, section_name, start, end)
        previous_line_continues = bool(re.search(r"[-–—]\s*$", line))
    return contexts


def _append_usage_contexts(
    contexts: list[_Context], line: str, line_start: int, section_name: str | None,
    original_line_start: int, original_line_end: int,
) -> None:
    for cue in _USAGE_CUE.finditer(line):
        phrase_end = re.search(r"[.!?;]", line[cue.end():])
        end_in_line = cue.end() + phrase_end.start() if phrase_end else len(line)
        if end_in_line - cue.end() > 140:
            end_in_line = cue.end() + 140
        phrase = line[cue.end():end_in_line]
        if _NEGATED_CUE.search(line[max(0, cue.start() - 24):cue.end()]):
            continue
        phrase_start = line_start + cue.end()
        contexts.append(_Context(
            phrase_start, phrase_start + len(phrase), "usage", section_name,
            original_line_start, original_line_end,
        ))


def _excerpt(text: str, context: _Context) -> str:
    # The stored source offsets preserve the whole original line, including its
    # original punctuation and spacing, without trying to reconstruct line breaks.
    return text[context.line_start:context.line_end]


def extract_local_skills(resume_text: str) -> list[SkillEvidence]:
    """Return only exact vocabulary mentions in a skills section or usage phrase."""
    contexts = _contexts(resume_text)
    found: dict[str, tuple[int, int, int, _Context]] = {}
    for context in contexts:
        # The API evidence field is deliberately bounded. Do not crop long lines:
        # omitting them is safer than returning a partial or rewritten excerpt.
        if context.line_end - context.line_start > 600:
            continue
        segment = resume_text[context.start:context.end]
        for skill, pattern in _PATTERNS.items():
            match = pattern.search(segment)
            if not match:
                continue
            absolute_start = context.start + match.start()
            absolute_end = context.start + match.end()
            previous = found.get(skill)
            rank = 0 if context.kind == "skills_section" else 1
            if previous is None or (rank, absolute_start) < (previous[0], previous[1]):
                found[skill] = (rank, absolute_start, absolute_end, context)

    results: list[SkillEvidence] = []
    for skill in SKILL_VOCABULARY:
        record = found.get(skill)
        if record is None:
            continue
        _, _, _, context = record
        source_excerpt = _excerpt(resume_text, context)
        if not source_excerpt or source_excerpt not in resume_text:
            continue
        section = context.section_name or "Resume text"
        explanation = (
            f"The exact term appears in an internship entry in the resume's {section} section. "
            "A local mention is not proof of proficiency."
            if context.kind == "experience_title" else
            f"The exact term appears in the resume's {section} section. A local mention is not proof of proficiency."
            if context.section_name else
            "The exact term appears in resume text; its section could not be confidently identified. "
            "A local mention is not proof of proficiency."
        )
        results.append(SkillEvidence(
            skill_name=skill,
            evidence_type="explicit",
            supporting_excerpt=source_excerpt,
            relevance_explanation=explanation,
            validation_status="evidence_found",
            detection_method="local",
        ))
    return results[:60]
