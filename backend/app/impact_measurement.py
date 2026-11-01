"""Bounded, aggregate-only measurements for a controlled local SkillFair pilot."""

from __future__ import annotations

import hashlib
from collections import OrderedDict, Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from statistics import fmean, median
from threading import RLock
from time import monotonic
from typing import Callable, Literal
from uuid import UUID

from .models import (
    ImpactBaselineComparison,
    ImpactBaselineObservationRequest,
    ImpactCategory,
    ImpactDistributionItem,
    ImpactMetric,
    ImpactSummaryResponse,
    ImpactTrendPoint,
)


MAX_RETENTION_SECONDS = 30 * 60
DEFAULT_RETENTION_SECONDS = MAX_RETENTION_SECONDS
DEFAULT_MAX_RECORDS = 10_000
MAX_VALID_REVIEW_DURATION_SECONDS = 24 * 60 * 60
MINIMUM_FEEDBACK_SAMPLE = 5
_NO_AUTH_LIMITATION = "Submissions are unauthenticated and cannot be attributed to verified individuals."
_NO_CAUSATION_LIMITATION = "This descriptive comparison does not establish causation, fairness, or improved hiring outcomes."


@dataclass(frozen=True)
class _ReviewEvent:
    expires_at: float
    started_at: datetime | None = None
    completed_at: datetime | None = None
    decision: str | None = None
    learning_pathways_generated: bool = False


@dataclass(frozen=True)
class _FeedbackEvent:
    expires_at: float
    submitted_at: datetime
    cohort: Literal["recruiter", "candidate"]
    ratings: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _BaselineEvent:
    expires_at: float
    submitted_at: datetime
    duration_seconds: int


class ImpactMeasurementStore:
    """Stores only short-lived operational events and anonymous rating aggregates' inputs."""

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_RETENTION_SECONDS,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_review_duration_seconds: int = MAX_VALID_REVIEW_DURATION_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not 1 <= ttl_seconds <= MAX_RETENTION_SECONDS or max_records < 1 or max_review_duration_seconds < 1:
            raise ValueError("impact storage limits are outside the permitted bounds")
        self.ttl_seconds = ttl_seconds
        self.max_records = max_records
        self.max_review_duration_seconds = max_review_duration_seconds
        self._clock = clock
        self._lock = RLock()
        self._reviews: OrderedDict[str, _ReviewEvent] = OrderedDict()
        self._feedback: OrderedDict[tuple[str, str], _FeedbackEvent] = OrderedDict()
        self._baseline: OrderedDict[str, _BaselineEvent] = OrderedDict()
        self._dropped_event_count = 0

    @staticmethod
    def _run_key(run_id: str) -> str:
        return hashlib.sha256(run_id.encode("utf-8")).hexdigest()

    def _purge(self, now: float) -> None:
        for store in (self._reviews, self._feedback, self._baseline):
            expired = [key for key, item in store.items() if item.expires_at <= now]
            for key in expired:
                store.pop(key, None)

    def _size(self) -> int:
        return len(self._reviews) + len(self._feedback) + len(self._baseline)

    def _available(self, key_exists: bool = False) -> bool:
        return key_exists or self._size() < self.max_records

    def _record_drop(self) -> None:
        # Keep the diagnostic counter bounded too; the summary will still flag
        # incomplete aggregates after the store reaches capacity.
        self._dropped_event_count = min(self.max_records, self._dropped_event_count + 1)

    def record_review_start(self, run_id: str, started_at: datetime) -> bool | None:
        key = self._run_key(run_id)
        now = self._clock()
        with self._lock:
            self._purge(now)
            existing = self._reviews.get(key)
            if existing is not None and existing.started_at is not None:
                return False
            if not self._available(existing is not None):
                self._record_drop()
                return None
            expiry = existing.expires_at if existing is not None else now + self.ttl_seconds
            self._reviews[key] = _ReviewEvent(
                expires_at=expiry,
                started_at=started_at.astimezone(timezone.utc),
                completed_at=existing.completed_at if existing else None,
                decision=existing.decision if existing else None,
                learning_pathways_generated=existing.learning_pathways_generated if existing else False,
            )
            return True

    def record_review_completion(
        self,
        run_id: str,
        decision: str,
        completed_at: datetime,
        learning_pathways_generated: bool,
    ) -> bool | None:
        if decision not in {"proceed", "revise", "stop"}:
            raise ValueError("unsupported workflow decision")
        key = self._run_key(run_id)
        now = self._clock()
        with self._lock:
            self._purge(now)
            existing = self._reviews.get(key)
            if existing is not None and existing.decision is not None:
                if existing.decision != decision:
                    raise ValueError("conflicting impact decision for workflow")
                return False
            if not self._available(existing is not None):
                self._record_drop()
                return None
            expiry = existing.expires_at if existing is not None else now + self.ttl_seconds
            self._reviews[key] = _ReviewEvent(
                expires_at=expiry,
                started_at=existing.started_at if existing else None,
                completed_at=completed_at.astimezone(timezone.utc),
                decision=decision,
                learning_pathways_generated=learning_pathways_generated,
            )
            return True

    def add_feedback(
        self,
        cohort: Literal["recruiter", "candidate"],
        submission_id: UUID,
        ratings: dict[str, int],
        submitted_at: datetime,
    ) -> bool | None:
        key = (cohort, str(submission_id))
        now = self._clock()
        with self._lock:
            self._purge(now)
            if key in self._feedback:
                return False
            if not self._available():
                self._record_drop()
                return None
            self._feedback[key] = _FeedbackEvent(
                expires_at=now + self.ttl_seconds,
                submitted_at=submitted_at.astimezone(timezone.utc),
                cohort=cohort,
                ratings=tuple(sorted(ratings.items())),
            )
            return True

    def add_baseline(self, request: ImpactBaselineObservationRequest, submitted_at: datetime) -> bool | None:
        key = str(request.submission_id)
        now = self._clock()
        with self._lock:
            self._purge(now)
            if key in self._baseline:
                return False
            if not self._available():
                self._record_drop()
                return None
            self._baseline[key] = _BaselineEvent(
                expires_at=now + self.ttl_seconds,
                submitted_at=submitted_at.astimezone(timezone.utc),
                duration_seconds=request.review_duration_seconds,
            )
            return True

    def snapshot(self) -> tuple[list[_ReviewEvent], list[_FeedbackEvent], list[_BaselineEvent]]:
        now = self._clock()
        with self._lock:
            self._purge(now)
            return list(self._reviews.values()), list(self._feedback.values()), list(self._baseline.values())

    @property
    def dropped_event_count(self) -> int:
        with self._lock:
            return self._dropped_event_count

    @property
    def record_count(self) -> int:
        with self._lock:
            self._purge(self._clock())
            return self._size()

    def summary(
        self,
        *,
        category: ImpactCategory = "all",
        start_date: date | None = None,
        end_date: date | None = None,
        now: datetime | None = None,
    ) -> ImpactSummaryResponse:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if start_date and end_date and start_date > end_date:
            raise ValueError("start date must not be after end date")
        start = datetime.combine(start_date, time.min, tzinfo=timezone.utc) if start_date else None
        end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc) if end_date else None
        reviews, feedback, baseline = self.snapshot()
        in_period = lambda stamp: stamp is not None and (start is None or stamp >= start) and (end is None or stamp < end)

        selected_reviews = [r for r in reviews if in_period(r.started_at) or in_period(r.completed_at)]
        starts = [r for r in selected_reviews if in_period(r.started_at)]
        completed = [r for r in selected_reviews if r.decision is not None and in_period(r.completed_at)]
        incomplete = [r for r in starts if r.decision is None]
        valid_durations: list[tuple[datetime, float]] = []
        invalid_duration_count = 0
        for record in completed:
            if record.started_at is None or record.completed_at is None:
                invalid_duration_count += 1
                continue
            seconds = (record.completed_at - record.started_at).total_seconds()
            if 0 < seconds <= self.max_review_duration_seconds:
                valid_durations.append((record.completed_at, seconds))
            else:
                invalid_duration_count += 1

        selected_feedback = [item for item in feedback if in_period(item.submitted_at) and (category == "all" or category == f"{item.cohort}_feedback")]
        recruiter = [item for item in selected_feedback if item.cohort == "recruiter"]
        candidate = [item for item in selected_feedback if item.cohort == "candidate"]
        selected_baseline = [item for item in baseline if in_period(item.submitted_at)] if category in {"all", "baseline", "operations"} else []

        metrics: list[ImpactMetric] = []
        if category in {"all", "operations"}:
            duration_values = [seconds for _, seconds in valid_durations]
            metrics.extend([
                self._metric("review_duration_average", "Average recruiter review time", "operations", fmean(duration_values) if len(duration_values) >= MINIMUM_FEEDBACK_SAMPLE else None, "seconds", len(duration_values), start, end, "Arithmetic mean of valid server-timestamped review intervals.", invalid_duration_count, [stamp for stamp, _ in valid_durations]),
                self._metric("review_duration_median", "Median recruiter review time", "operations", median(duration_values) if len(duration_values) >= MINIMUM_FEEDBACK_SAMPLE else None, "seconds", len(duration_values), start, end, "Median of valid server-timestamped review intervals.", invalid_duration_count, [stamp for stamp, _ in valid_durations]),
                self._metric("completed_reviews", "Completed reviews", "operations", len(completed) if len(starts) >= MINIMUM_FEEDBACK_SAMPLE else None, "count", len(starts), start, end, "Unique workflow runs with a committed Proceed, Revise, or Stop decision; sample size is the number of unique review starts.", 0, [r.completed_at for r in completed if r.completed_at]),
                self._metric("incomplete_reviews", "Incomplete reviews", "operations", len(incomplete) if len(starts) >= MINIMUM_FEEDBACK_SAMPLE else None, "count", len(starts), start, end, "Unique review starts without a committed decision at the time of this snapshot.", 0, [r.started_at for r in incomplete if r.started_at]),
                self._metric("workflow_completion_rate", "Workflow review completion rate", "operations", len([r for r in starts if r.decision is not None and in_period(r.completed_at)]) / len(starts) * 100 if len(starts) >= MINIMUM_FEEDBACK_SAMPLE else None, "percent", len(starts), start, end, "Started reviews completed with a committed human decision divided by unique started reviews.", 0, [r.started_at for r in starts if r.started_at]),
                self._metric("learning_pathways_generated", "Learning Pathways generated", "operations", sum(r.learning_pathways_generated for r in completed) if len(starts) >= MINIMUM_FEEDBACK_SAMPLE else None, "count", len(starts), start, end, "Unique committed Proceed decisions whose backend response confirmed a completed Learning Pathways result; sample size is the number of unique review starts.", 0, [r.completed_at for r in completed if r.completed_at]),
            ])

        if category in {"all", "recruiter_feedback"}:
            metrics.extend(self._rating_metrics(recruiter, "recruiter", start, end))
            recruiter_values = [value for event in recruiter for _, value in event.ratings]
            metrics.append(self._metric(
                "recruiter_experience_average", "Recruiter experience average", "recruiter_feedback",
                fmean(recruiter_values) if len(recruiter) >= MINIMUM_FEEDBACK_SAMPLE else None,
                "rating_1_to_5", len(recruiter), start, end,
                "Arithmetic mean across the three required recruiter rating questions; each completed questionnaire is included once per question.", 0,
                [event.submitted_at for event in recruiter],
            ))
        if category in {"all", "candidate_feedback"}:
            metrics.extend(self._rating_metrics(candidate, "candidate", start, end))
            candidate_values = [value for event in candidate for _, value in event.ratings]
            metrics.append(self._metric(
                "candidate_experience_average", "Candidate experience average", "candidate_feedback",
                fmean(candidate_values) if len(candidate) >= MINIMUM_FEEDBACK_SAMPLE else None,
                "rating_1_to_5", len(candidate), start, end,
                "Arithmetic mean across the four required candidate rating questions; each completed questionnaire is included once per question.", 0,
                [event.submitted_at for event in candidate],
            ))

        decisions = Counter(item.decision for item in completed if item.decision)
        decision_distribution = [
            ImpactDistributionItem(label=decision, value=decisions[decision] if len(starts) >= MINIMUM_FEEDBACK_SAMPLE else None, sample_size=len(starts), data_availability=self._availability(len(starts)))
            for decision in ("proceed", "revise", "stop")
        ] if category in {"all", "operations"} else []

        trend = self._duration_trend(valid_durations) if category in {"all", "operations"} else []
        recruiter_distribution = self._rating_distribution(recruiter) if category in {"all", "recruiter_feedback"} else []
        candidate_distribution = self._rating_distribution(candidate) if category in {"all", "candidate_feedback"} else []
        comparison = self._baseline_comparison(valid_durations, selected_baseline, start, end)
        if category not in {"all", "baseline", "operations"}:
            comparison = self._unavailable_comparison()

        dropped = self.dropped_event_count
        if dropped:
            loss_note = f"{dropped} event(s) could not be stored after the in-memory capacity limit was reached; displayed samples may be incomplete."
            metrics = [metric.model_copy(update={"value": None, "data_availability": "unavailable", "limitations": [*metric.limitations, loss_note]}) for metric in metrics]
            decision_distribution = [item.model_copy(update={"value": None, "data_availability": "unavailable"}) for item in decision_distribution]
            trend = [item.model_copy(update={"average_seconds": None, "data_availability": "unavailable"}) for item in trend]
            recruiter_distribution = [item.model_copy(update={"value": None, "data_availability": "unavailable"}) for item in recruiter_distribution]
            candidate_distribution = [item.model_copy(update={"value": None, "data_availability": "unavailable"}) for item in candidate_distribution]
            comparison = comparison.model_copy(update={
                "data_availability": "unavailable", "baseline_average_seconds": None,
                "pilot_average_seconds": None, "absolute_difference_seconds": None,
                "percentage_difference": None,
                "limitations": [*comparison.limitations, loss_note],
            })

        return ImpactSummaryResponse(
            generated_at=current,
            requested_start_date=start_date.isoformat() if start_date else None,
            requested_end_date=end_date.isoformat() if end_date else None,
            category=category,
            pilot_period_label="Current local pilot · records expire within 30 minutes or on backend restart",
            retention_seconds=self.ttl_seconds,
            dropped_event_count=dropped,
            minimum_feedback_sample=MINIMUM_FEEDBACK_SAMPLE,
            metrics=metrics,
            decision_distribution=decision_distribution,
            review_duration_trend=trend,
            recruiter_rating_distribution=recruiter_distribution,
            candidate_rating_distribution=candidate_distribution,
            baseline_comparison=comparison,
            disclaimer=(
                "Measurements describe workflow activity and voluntary ratings only. They do not prove fairness, "
                "causation, candidate competence, or improved hiring outcomes. " + _NO_AUTH_LIMITATION
                + (f" {dropped} event(s) were dropped because in-memory capacity was reached." if dropped else "")
            ),
        )

    @staticmethod
    def _availability(sample_size: int, *, available: bool = True) -> str:
        if not available:
            return "unavailable"
        if sample_size == 0:
            return "awaiting_data"
        if sample_size < MINIMUM_FEEDBACK_SAMPLE:
            return "insufficient_sample"
        return "measured"

    @classmethod
    def _metric(cls, metric_id: str, name: str, category: str, value: float | int | None, unit: str, sample_size: int, start: datetime | None, end: datetime | None, methodology: str, invalid_count: int, observation_times: list[datetime] | None = None) -> ImpactMetric:
        availability = cls._availability(sample_size)
        limitations = [_NO_AUTH_LIMITATION, "This is a process-local demonstration sample retained for at most 30 minutes."]
        if availability == "awaiting_data":
            limitations.append("No eligible observations are available in the selected period; unavailable is not the same as zero.")
        elif availability == "insufficient_sample":
            limitations.append(f"At least {MINIMUM_FEEDBACK_SAMPLE} eligible observations are required before displaying a calculated value.")
        if invalid_count:
            limitations.append(f"{invalid_count} invalid or non-positive duration observation(s) were excluded.")
        timestamps = observation_times or []
        return ImpactMetric(metric_id=metric_id, name=name, category=category, value=value, unit=unit, sample_size=sample_size, period_start=min(timestamps) if timestamps else None, period_end=max(timestamps) if timestamps else None, data_availability=availability, methodology=methodology, limitations=limitations)

    @classmethod
    def _rating_metrics(cls, events: list[_FeedbackEvent], cohort: str, start: datetime | None, end: datetime | None) -> list[ImpactMetric]:
        names = {
            "recruiter": {"evidence_usefulness": "Evidence usefulness", "explanation_clarity": "Skill explanation clarity", "review_ease": "Ease of review"},
            "candidate": {"skill_representation_clarity": "Clarity of skill representation", "understanding_of_skills": "Understanding of identified skills", "perceived_transparency": "Perceived transparency", "overall_experience": "Overall candidate experience"},
        }[cohort]
        metrics = []
        values = [dict(event.ratings) for event in events]
        for field, label in names.items():
            ratings = [item[field] for item in values if field in item]
            metrics.append(cls._metric(f"{cohort}_{field}", label, f"{cohort}_feedback", fmean(ratings) if len(ratings) >= MINIMUM_FEEDBACK_SAMPLE else None, "rating_1_to_5", len(ratings), start, end, f"Arithmetic mean of voluntary {cohort} ratings on a 1–5 scale; each submission is counted once.", 0, [event.submitted_at for event in events]))
        return metrics

    @classmethod
    def _rating_distribution(cls, events: list[_FeedbackEvent]) -> list[ImpactDistributionItem]:
        ratings = [rating for event in events for _, rating in event.ratings]
        sample_size = len(events)
        counts = Counter(ratings)
        availability = cls._availability(sample_size)
        return [ImpactDistributionItem(label=str(value), value=counts[value] if sample_size >= MINIMUM_FEEDBACK_SAMPLE else None, sample_size=sample_size, data_availability=availability) for value in range(1, 6)]

    @classmethod
    def _duration_trend(cls, durations: list[tuple[datetime, float]]) -> list[ImpactTrendPoint]:
        buckets: dict[datetime, list[float]] = defaultdict(list)
        for stamp, seconds in durations:
            bucket_minute = stamp.minute - (stamp.minute % 5)
            bucket = stamp.replace(minute=bucket_minute, second=0, microsecond=0)
            buckets[bucket].append(seconds)
        return [ImpactTrendPoint(bucket_start=bucket, average_seconds=fmean(values) if len(values) >= MINIMUM_FEEDBACK_SAMPLE else None, sample_size=len(values), data_availability=cls._availability(len(values))) for bucket, values in sorted(buckets.items())]

    @classmethod
    def _baseline_comparison(cls, pilot: list[tuple[datetime, float]], baseline: list[_BaselineEvent], start: datetime | None, end: datetime | None) -> ImpactBaselineComparison:
        baseline_sizes = len(baseline)
        pilot_sizes = len(pilot)
        availability = cls._availability(min(baseline_sizes, pilot_sizes))
        baseline_average = fmean(item.duration_seconds for item in baseline) if availability == "measured" else None
        pilot_average = fmean(value for _, value in pilot) if availability == "measured" else None
        difference = pilot_average - baseline_average if pilot_average is not None and baseline_average is not None else None
        percent = difference / baseline_average * 100 if difference is not None and baseline_average and baseline_average > 0 else None
        baseline_dates = [item.submitted_at for item in baseline]
        pilot_dates = [stamp for stamp, _ in pilot]
        limitations = [_NO_CAUSATION_LIMITATION, _NO_AUTH_LIMITATION, "Baseline observations are voluntarily entered and are not independently verified."]
        if availability == "awaiting_data":
            limitations.append("Both baseline and SkillFair-assisted observations are required.")
        elif availability == "insufficient_sample":
            limitations.append(f"At least {MINIMUM_FEEDBACK_SAMPLE} valid observations in each group are required before comparison.")
        if baseline_average == 0:
            limitations.append("Percentage difference is unavailable when the baseline average is zero.")
        return ImpactBaselineComparison(data_availability=availability, baseline_average_seconds=baseline_average, pilot_average_seconds=pilot_average, absolute_difference_seconds=difference, percentage_difference=percent, baseline_sample_size=baseline_sizes, pilot_sample_size=pilot_sizes, baseline_period_start=min(baseline_dates) if baseline_dates else None, baseline_period_end=max(baseline_dates) if baseline_dates else None, pilot_period_start=min(pilot_dates) if pilot_dates else None, pilot_period_end=max(pilot_dates) if pilot_dates else None, limitations=limitations)

    @classmethod
    def _unavailable_comparison(cls) -> ImpactBaselineComparison:
        return ImpactBaselineComparison(data_availability="unavailable", baseline_average_seconds=None, pilot_average_seconds=None, absolute_difference_seconds=None, percentage_difference=None, baseline_sample_size=0, pilot_sample_size=0, baseline_period_start=None, baseline_period_end=None, pilot_period_start=None, pilot_period_end=None, limitations=["Baseline comparison is not part of the selected category.", _NO_CAUSATION_LIMITATION])


impact_measurement_store = ImpactMeasurementStore()
