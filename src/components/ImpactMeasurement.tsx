import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Activity, BarChart3, Clock3, LoaderCircle, MessageSquareText, RefreshCw, ShieldCheck } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { getDurationTrendEmptyState } from '../impactMeasurementChart'
import { skillFairFetch } from '../apiClient'
import { useAuth } from '../authContext'
import { ImpactRequestTracker, impactMetricValue, impactRequestErrorMessage, impactSummaryBelongsTo, impactSummaryUrl, isAwaitingImpactObservations, refreshImpactSummaryAfterSubmission } from '../impactMeasurementState'
import '../styles/impact-measurement.css'

type Availability = 'awaiting_data' | 'insufficient_sample' | 'measured' | 'unavailable'
type Category = 'all' | 'operations' | 'recruiter_feedback' | 'candidate_feedback' | 'baseline'
type Metric = { metric_id: string; name: string; category: string; value: number | null; unit: 'seconds' | 'count' | 'percent' | 'rating_1_to_5'; sample_size: number; period_start: string | null; period_end: string | null; data_availability: Availability; methodology: string; limitations: string[] }
type DistributionItem = { label: string; value: number | null; sample_size: number; data_availability: Availability }
type TrendPoint = { bucket_start: string; average_seconds: number | null; sample_size: number; data_availability: Availability }
type Summary = { generated_at: string; requested_start_date: string | null; requested_end_date: string | null; category: Category; pilot_period_label: string; retention_seconds: number; dropped_event_count: number; minimum_feedback_sample: number; metrics: Metric[]; decision_distribution: DistributionItem[]; review_duration_trend: TrendPoint[]; recruiter_rating_distribution: DistributionItem[]; candidate_rating_distribution: DistributionItem[]; baseline_comparison: { data_availability: Availability; baseline_average_seconds: number | null; pilot_average_seconds: number | null; absolute_difference_seconds: number | null; percentage_difference: number | null; baseline_sample_size: number; pilot_sample_size: number; baseline_period_start: string | null; baseline_period_end: string | null; pilot_period_start: string | null; pilot_period_end: string | null; limitations: string[] }; disclaimer: string }
type SubmitState = { kind: 'idle' | 'loading' | 'success' | 'error'; message: string }

const ratingLabels = ['1 · Strongly disagree', '2 · Disagree', '3 · Neither', '4 · Agree', '5 · Strongly agree']
const metricIcon = { review_duration_average: Clock3, recruiter_evidence_usefulness: ShieldCheck, candidate_experience_average: MessageSquareText, completed_reviews: Activity }
function displayAvailability(value: Availability) {
  return value.replaceAll('_', ' ')
}

function RatingQuestion({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (value: string) => void }) {
  return <label className="impact-rating-question" htmlFor={id}>{label}<select id={id} required value={value} onChange={(event) => onChange(event.target.value)}><option value="" disabled>Select a rating</option>{ratingLabels.map((item, index) => <option key={item} value={index + 1}>{item}</option>)}</select></label>
}

function MetricCard({ metric }: { metric: Metric | undefined }) {
  if (!metric) return null
  const Icon = metricIcon[metric.metric_id as keyof typeof metricIcon] ?? BarChart3
  return <article className="impact-live-card">
    <div className="impact-card-head"><span className="impact-icon"><Icon size={17} /></span><span className={`impact-pending impact-state-${metric.data_availability}`}>{displayAvailability(metric.data_availability)}</span></div>
    <h2>{metric.name}</h2>
    <strong>{impactMetricValue(metric.value, metric.unit, metric.data_availability)}</strong>
    <span className="impact-sample">Sample size · {metric.sample_size}</span>
    <details><summary>Methodology and limits</summary><p>{metric.methodology}</p>{metric.limitations.map((item) => <small key={item}>{item}</small>)}</details>
  </article>
}

function SupportingMetric({ metric }: { metric: Metric }) {
  return <article className="impact-supporting-metric"><div><b>{metric.name}</b><span className={`impact-pending impact-state-${metric.data_availability}`}>{displayAvailability(metric.data_availability)}</span></div><strong>{impactMetricValue(metric.value, metric.unit, metric.data_availability)}</strong><small>Sample size · {metric.sample_size}</small><details><summary>Method and limitations</summary><p>{metric.methodology}</p>{metric.limitations.map((item) => <small key={item}>{item}</small>)}</details></article>
}

function EmptyChart({ availability, sampleSize }: { availability: Availability; sampleSize: number }) {
  return <div className="impact-chart-empty"><b>{availability === 'awaiting_data' ? 'Awaiting data' : availability === 'insufficient_sample' ? 'Insufficient sample' : 'No chart data available'}</b><span>Sample size · {sampleSize}. No fabricated chart values are shown.</span></div>
}

function DurationEmptyChart({ points, minimumSample, availability }: { points: TrendPoint[]; minimumSample: number; availability: Availability }) {
  const emptyState = getDurationTrendEmptyState(points, minimumSample, availability)
  return <div className="impact-chart-empty">
    <b>{emptyState.heading}</b>
    <span>{emptyState.detail}</span>
  </div>
}

export default function ImpactMeasurement() {
  const auth = useAuth()
  const retryCurrentUser = auth.retryCurrentUser
  const accountId = auth.user?.id ?? ''
  const [summary, setSummary] = useState<Summary | null>(null)
  const [summaryOwnerId, setSummaryOwnerId] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [category, setCategory] = useState<Category>('all')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [appliedFilters, setAppliedFilters] = useState<{ category: Category; startDate: string; endDate: string }>({ category: 'all', startDate: '', endDate: '' })
  const [recruiterRatings, setRecruiterRatings] = useState({ evidence_usefulness: '', explanation_clarity: '', review_ease: '' })
  const [recruiterConsent, setRecruiterConsent] = useState(false)
  const [recruiterState, setRecruiterState] = useState<SubmitState>({ kind: 'idle', message: '' })
  const [baselineSeconds, setBaselineSeconds] = useState('')
  const [baselineState, setBaselineState] = useState<SubmitState>({ kind: 'idle', message: '' })
  const pendingSubmissionIds = useRef<Record<string, string>>({})
  const submissionInProgress = useRef<Record<string, boolean>>({})
  const requestTracker = useRef(new ImpactRequestTracker())
  const summaryController = useRef<AbortController | null>(null)
  const previousAccountId = useRef(accountId)

  const loadSummary = useCallback(async (filters: typeof appliedFilters) => {
    summaryController.current?.abort()
    const controller = new AbortController()
    summaryController.current = controller
    const request = requestTracker.current.begin(accountId)
    setLoading(true)
    setLoadError('')
    setSummary(null)
    setSummaryOwnerId('')
    try {
      const response = await skillFairFetch(impactSummaryUrl(filters), { signal: controller.signal })
      const body = await response.json().catch(() => ({})) as Summary & { detail?: unknown }
      if (!response.ok) {
        const message = impactRequestErrorMessage(response.status, body.detail, 'The measurement summary could not be loaded.')
        if (response.status === 401) {
          if (requestTracker.current.isCurrent(request, accountId)) {
            setLoadError(message)
            retryCurrentUser()
          }
          return
        }
        throw new Error(message)
      }
      if (!requestTracker.current.isCurrent(request, accountId)) return
      setSummary(body)
      setSummaryOwnerId(accountId)
    } catch (reason) {
      if (controller.signal.aborted || !requestTracker.current.isCurrent(request, accountId)) return
      setSummary(null)
      setSummaryOwnerId('')
      setLoadError(reason instanceof TypeError ? 'The measurement service could not be reached. Check your connection and try again.' : reason instanceof Error ? reason.message : 'The measurement service could not be reached.')
    } finally {
      if (requestTracker.current.isCurrent(request, accountId)) setLoading(false)
    }
  }, [accountId, retryCurrentUser])

  useEffect(() => {
    const tracker = requestTracker.current
    if (previousAccountId.current !== accountId) {
      previousAccountId.current = accountId
      setSummary(null)
      setSummaryOwnerId('')
      setLoadError('')
      setRecruiterRatings({ evidence_usefulness: '', explanation_clarity: '', review_ease: '' })
      setRecruiterConsent(false)
      setRecruiterState({ kind: 'idle', message: '' })
      setBaselineSeconds('')
      setBaselineState({ kind: 'idle', message: '' })
      pendingSubmissionIds.current = {}
      submissionInProgress.current = {}
    }
    void loadSummary(appliedFilters)
    return () => {
      summaryController.current?.abort()
      tracker.invalidate()
    }
  }, [accountId, appliedFilters, loadSummary])

  function handleExpiredSession() {
    setSummary(null)
    setSummaryOwnerId('')
    setLoadError('Your sign-in session has expired. Sign in again to view account-scoped measurements.')
    auth.retryCurrentUser()
  }

  async function submitRating(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submissionInProgress.current.recruiter) return
    submissionInProgress.current.recruiter = true
    const values = Object.fromEntries(Object.entries(recruiterRatings).map(([key, value]) => [key, Number(value)]))
    setRecruiterState({ kind: 'loading', message: 'Submitting voluntary ratings…' })
    try {
      const submissionId = pendingSubmissionIds.current.recruiter ?? crypto.randomUUID()
      pendingSubmissionIds.current.recruiter = submissionId
      const response = await skillFairFetch('/api/impact/feedback/recruiter', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ submission_id: submissionId, consent_to_aggregate: true, ...values }),
      })
      const body = await response.json().catch(() => ({})) as { accepted?: boolean; duplicate?: boolean; message?: string; detail?: unknown }
      if (!response.ok) {
        if (response.status === 401) {
          handleExpiredSession()
          return
        }
        throw new Error(impactRequestErrorMessage(response.status, body.detail, 'The rating could not be submitted.'))
      }
      pendingSubmissionIds.current.recruiter = ''
      setRecruiterState({ kind: 'success', message: body.message ?? (body.duplicate ? 'This submission was already counted.' : 'Thank you. Your ratings are included in account-level aggregates for up to 90 days.') })
      setRecruiterRatings({ evidence_usefulness: '', explanation_clarity: '', review_ease: '' })
      setRecruiterConsent(false)
      await refreshImpactSummaryAfterSubmission(response.ok, () => loadSummary(appliedFilters))
    } catch (reason) {
      setRecruiterState({ kind: 'error', message: reason instanceof Error ? reason.message : 'The feedback service could not be reached.' })
    } finally {
      submissionInProgress.current.recruiter = false
    }
  }

  async function submitBaseline(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submissionInProgress.current.baseline) return
    submissionInProgress.current.baseline = true
    setBaselineState({ kind: 'loading', message: 'Adding baseline observation…' })
    try {
      const submissionId = pendingSubmissionIds.current.baseline ?? crypto.randomUUID()
      pendingSubmissionIds.current.baseline = submissionId
      const response = await skillFairFetch('/api/impact/baseline-observations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ submission_id: submissionId, consent_to_aggregate: true, review_duration_seconds: Number(baselineSeconds) }),
      })
      const body = await response.json().catch(() => ({})) as { duplicate?: boolean; detail?: unknown }
      if (!response.ok) {
        if (response.status === 401) {
          handleExpiredSession()
          return
        }
        throw new Error(impactRequestErrorMessage(response.status, body.detail, 'The baseline observation could not be submitted.'))
      }
      pendingSubmissionIds.current.baseline = ''
      setBaselineState({ kind: 'success', message: body.duplicate ? 'This observation was already counted.' : 'Baseline observation stored with this account for up to 90 days. It is not independently verified.' })
      setBaselineSeconds('')
      await refreshImpactSummaryAfterSubmission(response.ok, () => loadSummary(appliedFilters))
    } catch (reason) {
      setBaselineState({ kind: 'error', message: reason instanceof Error ? reason.message : 'The baseline service could not be reached.' })
    } finally {
      submissionInProgress.current.baseline = false
    }
  }

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (startDate && endDate && startDate > endDate) {
      setLoadError('Start date must be on or before end date.')
      return
    }
    setAppliedFilters({ category, startDate, endDate })
  }

  const visibleSummary = impactSummaryBelongsTo(summaryOwnerId, accountId) ? summary : null
  const metricMap = useMemo(() => new Map((visibleSummary?.metrics ?? []).map((metric) => [metric.metric_id, metric])), [visibleSummary])
  const chartsHaveData = (values: DistributionItem[]) => values.some((item) => item.value !== null)
  const averageMetric = metricMap.get('review_duration_average')

  return <div className="impact-page">
    <header className="discovery-heading">
      <div><span className="eyebrow"><span className="eyebrow-line" /> RESPONSIBLE EVALUATION</span><h1>Impact Measurement</h1><p>Observe workflow activity and voluntary feedback; do not treat metrics as proof of fair or successful hiring.</p></div>
      <span className="impact-status"><Activity size={15} />{visibleSummary?.pilot_period_label ?? 'Account-scoped pilot measurements'}</span>
    </header>

    <section className="impact-notice" role="note"><b>Account-scoped pilot measurements</b><p>Review activity, recruiter ratings and baseline observations are scoped to your signed-in account. Recruiter ratings and baselines are reportable for up to 90 days; expired entries are excluded from summaries. No resumes, evidence excerpts or free-text comments are stored here. Persistent candidate feedback is not available until a candidate identity or separately reviewed anonymous-submission design exists. These measurements do not demonstrate fairness, causation or improved hiring outcomes.</p></section>

    <form className="impact-filter-bar" onSubmit={applyFilters}>
      <label>From<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
      <label>To<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
      <label>Measurement category<select value={category} onChange={(event) => setCategory(event.target.value as Category)}><option value="all">All measures</option><option value="operations">Workflow operations</option><option value="recruiter_feedback">Recruiter feedback</option><option value="candidate_feedback">Candidate feedback</option><option value="baseline">Baseline comparison</option></select></label>
      <label>Pilot period<select value="current" disabled aria-label="Pilot period"><option value="current">Current local pilot only</option></select></label>
      <button type="submit" className="button-secondary" disabled={loading}>Apply filters</button>
      <button type="button" className="impact-refresh" aria-label="Refresh measurements" onClick={() => void loadSummary(appliedFilters)} disabled={loading}><RefreshCw size={15} /></button>
    </form>
    {loadError && <div className="impact-error" role="alert">{loadError}<button type="button" onClick={() => void loadSummary(appliedFilters)}>Retry</button></div>}
    {loading && <div className="impact-loading" role="status"><LoaderCircle size={18} className="spin-icon" />Loading aggregate measurements…</div>}
    {!loading && !loadError && visibleSummary && <>
      {visibleSummary.dropped_event_count > 0 && <div className="impact-error" role="alert">Some measurement events could not be stored. Affected aggregates are marked unavailable.</div>}
      <section className="impact-kpis" aria-label="Impact measurement summary">
        {['review_duration_average', 'recruiter_evidence_usefulness', 'candidate_experience_average', 'completed_reviews'].map((id) => <MetricCard key={id} metric={metricMap.get(id)} />)}
      </section>
      {(appliedFilters.category === 'all' || appliedFilters.category === 'operations' || appliedFilters.category === 'recruiter_feedback' || appliedFilters.category === 'candidate_feedback') && <section className="impact-supporting-section"><div className="section-heading"><div><h2>Additional metrics</h2><p>Values remain unavailable or suppressed until there are enough valid observations.</p></div></div><div className="impact-supporting-grid">{visibleSummary.metrics.filter((metric) => !['review_duration_average', 'recruiter_evidence_usefulness', 'candidate_experience_average', 'completed_reviews'].includes(metric.metric_id)).map((metric) => <SupportingMetric key={metric.metric_id} metric={metric} />)}</div></section>}
      {visibleSummary.dropped_event_count === 0 && isAwaitingImpactObservations(visibleSummary.metrics) && <div className="impact-empty" role="status"><BarChart3 size={20} /><div><b>Awaiting pilot observations</b><span>Open a pending Human Review workflow to start timing, record a reviewed decision, or submit voluntary recruiter ratings below. No zero values or sample charts are invented when no records exist.</span></div></div>}

      <section className="impact-chart-grid">
        <article className="panel impact-chart-card"><header><div><h2>Recruiter review duration</h2><p>Average of valid server-timestamped intervals · minimum sample {visibleSummary.minimum_feedback_sample} per 5-minute bucket</p></div></header>{visibleSummary.review_duration_trend.some((point) => point.average_seconds !== null) ? <div className="impact-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={visibleSummary.review_duration_trend}><CartesianGrid stroke="#edf0f6" vertical={false} /><XAxis dataKey="bucket_start" tickFormatter={(value) => new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} /><YAxis unit="s" /><Tooltip labelFormatter={(value) => new Date(value).toLocaleString()} formatter={(value) => [`${Number(value).toFixed(0)} sec`, 'Average duration']} /><Line dataKey="average_seconds" name="Average seconds" stroke="#6886f5" connectNulls={false} /></LineChart></ResponsiveContainer></div> : <DurationEmptyChart points={visibleSummary.review_duration_trend} minimumSample={visibleSummary.minimum_feedback_sample} availability={visibleSummary.review_duration_trend[0]?.data_availability ?? averageMetric?.data_availability ?? 'awaiting_data'} />}</article>
        <article className="panel impact-chart-card"><header><div><h2>Human Review decisions</h2><p>Operational events only · not hiring outcomes</p></div></header>{chartsHaveData(visibleSummary.decision_distribution) ? <div className="impact-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={visibleSummary.decision_distribution}><CartesianGrid stroke="#edf0f6" vertical={false} /><XAxis dataKey="label" /><YAxis allowDecimals={false} /><Tooltip formatter={(value) => [value, 'Committed decisions']} /><Bar dataKey="value" fill="#8175d5" radius={[5, 5, 0, 0]} /></BarChart></ResponsiveContainer></div> : <EmptyChart availability={visibleSummary.decision_distribution[0]?.data_availability ?? 'awaiting_data'} sampleSize={visibleSummary.decision_distribution[0]?.sample_size ?? 0} />}</article>
        <article className="panel impact-chart-card"><header><div><h2>Recruiter feedback ratings</h2><p>Combined voluntary ratings · scale 1–5 · no comments collected</p></div></header>{chartsHaveData(visibleSummary.recruiter_rating_distribution) ? <div className="impact-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={visibleSummary.recruiter_rating_distribution}><CartesianGrid stroke="#edf0f6" vertical={false} /><XAxis dataKey="label" /><YAxis allowDecimals={false} /><Tooltip formatter={(value) => [value, 'Ratings']} /><Bar dataKey="value" fill="#6886f5" radius={[5, 5, 0, 0]} /></BarChart></ResponsiveContainer></div> : <EmptyChart availability={visibleSummary.recruiter_rating_distribution[0]?.data_availability ?? metricMap.get('recruiter_evidence_usefulness')?.data_availability ?? 'awaiting_data'} sampleSize={visibleSummary.recruiter_rating_distribution[0]?.sample_size ?? metricMap.get('recruiter_evidence_usefulness')?.sample_size ?? 0} />}</article>
        <article className="panel impact-chart-card"><header><div><h2>Candidate feedback</h2><p>Persistent collection is deferred; no candidate submissions are attributed to recruiter accounts</p></div></header><EmptyChart availability="unavailable" sampleSize={0} /></article>
      </section>

      <section className="impact-feedback-grid" aria-label="Voluntary feedback">
        <form className="panel impact-form" onSubmit={(event) => void submitRating(event)}>
          <div className="impact-form-heading"><div className="impact-icon"><MessageSquareText size={17} /></div><div><h2>Recruiter feedback</h2><p>Voluntary · linked to your signed-in account · aggregate only</p></div></div>
          <RatingQuestion id="impact-rec-evidence" label="How useful was the supporting evidence?" value={recruiterRatings.evidence_usefulness} onChange={(value) => setRecruiterRatings((current) => ({ ...current, evidence_usefulness: value }))} />
          <RatingQuestion id="impact-rec-clarity" label="How clear were the skill explanations?" value={recruiterRatings.explanation_clarity} onChange={(value) => setRecruiterRatings((current) => ({ ...current, explanation_clarity: value }))} />
          <RatingQuestion id="impact-rec-ease" label="How easy was it to review the information?" value={recruiterRatings.review_ease} onChange={(value) => setRecruiterRatings((current) => ({ ...current, review_ease: value }))} />
          <label className="impact-consent"><input type="checkbox" required checked={recruiterConsent} onChange={(event) => setRecruiterConsent(event.target.checked)} />I voluntarily agree that these ratings may be associated with my signed-in account and included in aggregate pilot measurements for up to 90 days.</label>
          <div className="impact-form-foot"><span>Comments are not collected or stored.</span><button type="submit" className="button-primary" disabled={recruiterState.kind === 'loading' || !recruiterConsent}>{recruiterState.kind === 'loading' ? 'Submitting…' : 'Submit recruiter ratings'}</button></div>
          {recruiterState.message && <p className={`impact-submit-message ${recruiterState.kind}`} role={recruiterState.kind === 'error' ? 'alert' : 'status'}>{recruiterState.message}</p>}
        </form>

        <section className="panel impact-form" aria-labelledby="candidate-feedback-heading">
          <div className="impact-form-heading"><div className="impact-icon candidate"><ShieldCheck size={17} /></div><div><h2 id="candidate-feedback-heading">Candidate feedback</h2><p>Collection is temporarily unavailable</p></div></div>
          <p>SkillFair does not yet have a separate candidate identity or reviewed anonymous-submission design. Candidate feedback will not be attributed to a recruiter account or collected through this page.</p>
          <p>Candidate feedback metrics are marked unavailable, not zero. Any future collection will remain separate from hiring decisions.</p>
        </section>
      </section>

      <section className="panel impact-baseline">
        <div><h2>Optional baseline comparison</h2><p>Enter an observed duration from a baseline process. This submission is associated with your signed-in account, retained for up to 90 days, and not independently verified.</p></div>
        <form onSubmit={(event) => void submitBaseline(event)}><label htmlFor="impact-baseline-seconds">Baseline review duration in seconds<input id="impact-baseline-seconds" type="number" min="1" max="14400" step="1" required value={baselineSeconds} onChange={(event) => setBaselineSeconds(event.target.value)} /></label><button className="button-secondary" type="submit" disabled={baselineState.kind === 'loading'}>{baselineState.kind === 'loading' ? 'Submitting…' : 'Add baseline observation'}</button></form>
        {baselineState.message && <p className={`impact-submit-message ${baselineState.kind}`} role={baselineState.kind === 'error' ? 'alert' : 'status'}>{baselineState.message}</p>}
        <div className="impact-comparison"><b>Baseline versus SkillFair-assisted review</b><span>Status: {displayAvailability(visibleSummary.baseline_comparison.data_availability)} · baseline n={visibleSummary.baseline_comparison.baseline_sample_size} · pilot n={visibleSummary.baseline_comparison.pilot_sample_size}</span><span>{visibleSummary.baseline_comparison.absolute_difference_seconds === null ? 'Difference unavailable until both groups meet the sample threshold.' : `Absolute difference ${visibleSummary.baseline_comparison.absolute_difference_seconds.toFixed(1)} seconds${visibleSummary.baseline_comparison.percentage_difference === null ? '' : ` · ${visibleSummary.baseline_comparison.percentage_difference.toFixed(1)}%`}`}</span><small>This descriptive before-and-after comparison does not establish causation or improved fairness.</small></div>
      </section>
      <p className="impact-disclaimer">{visibleSummary.disclaimer}</p>
    </>}
  </div>
}
