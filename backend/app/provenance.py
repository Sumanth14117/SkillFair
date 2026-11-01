from collections import OrderedDict
from dataclasses import dataclass
from secrets import token_urlsafe
from threading import RLock
from time import monotonic
from typing import Callable

from .models import SkillEvidence
from .privacy import redact_skill_evidence


@dataclass(frozen=True)
class _AnalysisRecord:
    expires_at: float
    skills: tuple[SkillEvidence, ...]
    analysis_mode: str


class AnalysisStore:
    """Bounded process-local storage for validated skill evidence, never resume text."""

    def __init__(
        self,
        ttl_seconds: int = 30 * 60,
        max_entries: int = 500,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("ttl_seconds and max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._records: OrderedDict[str, _AnalysisRecord] = OrderedDict()
        self._lock = RLock()

    def _remove_expired(self, now: float) -> None:
        expired = [key for key, record in self._records.items() if record.expires_at <= now]
        for key in expired:
            self._records.pop(key, None)

    def create(self, skills: list[SkillEvidence], analysis_mode: str = "ai") -> str:
        if analysis_mode not in {"ai", "local", "development"}:
            raise ValueError("unsupported analysis mode")
        now = self._clock()
        safe_skills = tuple(redact_skill_evidence(skill) for skill in skills)
        with self._lock:
            self._remove_expired(now)
            analysis_id = token_urlsafe(32)
            while analysis_id in self._records:
                analysis_id = token_urlsafe(32)
            self._records[analysis_id] = _AnalysisRecord(now + self._ttl_seconds, safe_skills, analysis_mode)
            while len(self._records) > self._max_entries:
                self._records.popitem(last=False)
        return analysis_id

    def resolve(self, analysis_id: str) -> list[SkillEvidence] | None:
        resolved = self.resolve_with_mode(analysis_id)
        return resolved[0] if resolved is not None else None

    def resolve_with_mode(self, analysis_id: str) -> tuple[list[SkillEvidence], str] | None:
        now = self._clock()
        with self._lock:
            self._remove_expired(now)
            record = self._records.get(analysis_id)
            if record is None:
                return None
            self._records.move_to_end(analysis_id)
            return [skill.model_copy(deep=True) for skill in record.skills], record.analysis_mode


analysis_store = AnalysisStore()
