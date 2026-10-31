import { useRef, useState, type FormEvent } from 'react'
import { AlertCircle, AlertTriangle, Check, CircleHelp, ClipboardCheck, LoaderCircle, RotateCw, ShieldCheck, Sparkles } from 'lucide-react'
import '../styles/bias-audit.css'

type Importance = 'essential' | 'preferred' | 'unspecified'
type ReviewState = 'Needs review' | 'Accepted' | 'Edited' | 'Dismissed'
type AuditRequest = {
  job_title?: string
  job_description: string
  requirements: { text: string; importance: Importance }[]
}
type Finding = {
  rule_id: string
  category: 'credential_requirement' | 'potentially_exclusionary_wording' | 'vague_requirement' | 'experience_threshold'
  priority: 'low' | 'medium' | 'high'
  triggering_phrase: string
  criterion_importance: Importance
  explanation: string
  suggested_revision_or_action: string
  status: 'potential_concern_for_human_review'
  human_review_required: true
}
type AuditResult = {
  job_title: string | null
  criteria_reviewed: number
  finding_count: number
  findings: Finding[]
  human_review_required: true
  disclaimer: string
}

const REVIEW_STATES: ReviewState[] = ['Needs review', 'Accepted', 'Edited', 'Dismissed']
const CATEGORY_LABELS: Record<Finding['category'], string> = {
  credential_requirement: 'Credential requirement',
  potentially_exclusionary_wording: 'Potentially exclusionary wording',
  vague_requirement: 'Vague requirement',
  experience_threshold: 'Experience threshold',
}

function linesToRequirements(value: string, importance: Exclude<Importance, 'unspecified'>) {
  return value.split('\n').map((text) => text.trim()).filter(Boolean).map((text) => ({ text, importance }))
}

async function submitAudit(payload: AuditRequest): Promise<AuditResult> {
  let response: Response
  try {
    response = await fetch('/api/jobs/audit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch {
    throw new Error('Could not reach the Bias Audit service. Check that the backend is running and try again.')
  }

  let body: Partial<AuditResult> & { detail?: string | { msg?: string }[] }
  try {
    body = await response.json() as typeof body
  } catch {
    throw new Error('The audit service returned an unreadable response. Please try again.')
  }
  if (!response.ok) {
    const detail = typeof body.detail === 'string'
      ? body.detail
      : Array.isArray(body.detail)
        ? body.detail.map((item) => item.msg).filter((message): message is string => Boolean(message)).join(' ')
        : ''
    throw new Error(detail || 'The audit could not be completed. Check the fields and try again.')
  }
  if (!Array.isArray(body.findings) || typeof body.finding_count !== 'number' || typeof body.criteria_reviewed !== 'number' || typeof body.disclaimer !== 'string') {
    throw new Error('The audit service returned an unsupported response. Please try again.')
  }
  return body as AuditResult
}

function FindingCard({ finding, review, onReview }: { finding: Finding; review: ReviewState; onReview: (state: ReviewState) => void }) {
  return <article className="audit-finding-card">
    <div className="audit-finding-top">
      <div className="audit-finding-labels"><span className="audit-category">{CATEGORY_LABELS[finding.category]}</span><span className={`audit-priority priority-${finding.priority}`}>{finding.priority} priority</span></div>
      <span className="audit-rule-id">{finding.rule_id}</span>
    </div>
    {finding.triggering_phrase && <blockquote className="audit-trigger">“{finding.triggering_phrase}”</blockquote>}
    <p className="audit-finding-explanation">{finding.explanation}</p>
    {finding.suggested_revision_or_action && <div className="audit-suggestion"><Sparkles size={15} /><div><b>Suggested action</b><p>{finding.suggested_revision_or_action}</p></div></div>}
    <div className="audit-review-row"><div><b>Recruiter review</b><span>Temporary page state · not saved</span></div><div className="audit-review-options" role="group" aria-label={`Review status for ${finding.rule_id}`}>
      {REVIEW_STATES.map((state) => <button key={state} type="button" className={review === state ? 'review-selected' : ''} aria-pressed={review === state} onClick={() => onReview(state)}>{review === state && <Check size={13} />}{state}</button>)}
    </div></div>
  </article>
}

export default function BiasAudit() {
  const [jobTitle, setJobTitle] = useState('')
  const [jobDescription, setJobDescription] = useState('')
  const [essential, setEssential] = useState('')
  const [preferred, setPreferred] = useState('')
  const [result, setResult] = useState<AuditResult | null>(null)
  const [reviews, setReviews] = useState<Record<string, ReviewState>>({})
  const [error, setError] = useState('')
  const [validation, setValidation] = useState('')
  const [loading, setLoading] = useState(false)
  const requestInFlight = useRef(false)
  const requirementCount = linesToRequirements(essential, 'essential').length + linesToRequirements(preferred, 'preferred').length

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (requestInFlight.current || loading) return
    const description = jobDescription.trim()
    const requirements = [
      ...linesToRequirements(essential, 'essential'),
      ...linesToRequirements(preferred, 'preferred'),
    ]
    if (!description && requirements.length === 0) {
      setValidation('Add a job description or at least one qualification to audit.')
      return
    }
    if (requirements.length > 60) {
      setValidation('The backend accepts up to 60 qualifications. Combine or remove some items before continuing.')
      return
    }
    const tooLong = requirements.find((item) => item.text.length > 1000)
    if (tooLong) {
      setValidation('Each qualification must be 1,000 characters or fewer.')
      return
    }

    const payload: AuditRequest = {
      ...(jobTitle.trim() ? { job_title: jobTitle.trim() } : {}),
      job_description: description,
      requirements,
    }
    requestInFlight.current = true
    setLoading(true)
    setError('')
    setValidation('')
    try {
      const audit = await submitAudit(payload)
      setResult(audit)
      setReviews(Object.fromEntries(audit.findings.map((finding) => [`${finding.rule_id}:${finding.criterion_importance}:${finding.triggering_phrase}`, 'Needs review'])))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The audit could not be completed. Please try again.')
    } finally {
      requestInFlight.current = false
      setLoading(false)
    }
  }

  function updateReview(key: string, review: ReviewState) {
    setReviews((current) => ({ ...current, [key]: review }))
  }

  const hasInput = Boolean(jobDescription.trim() || requirementCount)
  return <div className="audit-page">
    <header className="audit-page-heading"><div><span className="eyebrow"><span className="eyebrow-line" /> HUMAN-LED CRITERIA REVIEW</span><h1>Bias Audit</h1><p>Surface job criteria and wording that may benefit from a closer look.</p></div><div className="audit-heading-note"><ShieldCheck size={16} />Deterministic · Advisory only</div></header>
    <div className="audit-advisory" role="note"><div className="audit-advisory-icon"><AlertTriangle size={18} /></div><p><b>Human review is essential.</b> Findings are potential concerns, not proof of discrimination or unfairness, and do not establish legal compliance. The audit does not assess candidates or infer demographic characteristics.</p></div>

    <div className="audit-layout">
      <form className="panel audit-form" onSubmit={handleSubmit} noValidate aria-busy={loading}>
        <div className="audit-form-heading"><div><span className="audit-step">01</span><div><h2>Role details</h2><p>Enter the role criteria you want to review.</p></div></div><ClipboardCheck size={19} /></div>
        <label htmlFor="audit-job-title">Job title <span>Optional</span></label>
        <input id="audit-job-title" name="job_title" type="text" maxLength={160} value={jobTitle} onChange={(event) => setJobTitle(event.target.value)} placeholder="e.g. Senior Data Analyst" />
        <label htmlFor="audit-job-description">Job description <span>Up to 10,000 characters</span></label>
        <textarea id="audit-job-description" name="job_description" maxLength={10000} rows={8} value={jobDescription} onChange={(event) => { setJobDescription(event.target.value); setValidation('') }} placeholder="Paste the role description, responsibilities, and qualifications…" />
        <div className="audit-char-count" aria-live="polite">{jobDescription.length.toLocaleString()} / 10,000</div>
        <div className="audit-field-divider"><span>OPTIONAL STRUCTURED QUALIFICATIONS</span><i /></div>
        <label htmlFor="audit-essential">Essential requirements <span>One per line</span></label>
        <textarea id="audit-essential" name="essential_requirements" rows={4} value={essential} onChange={(event) => { setEssential(event.target.value); setValidation('') }} placeholder={'e.g.\nExperience building data pipelines\nSQL query development'} />
        <label htmlFor="audit-preferred">Preferred qualifications <span>One per line</span></label>
        <textarea id="audit-preferred" name="preferred_qualifications" rows={3} value={preferred} onChange={(event) => { setPreferred(event.target.value); setValidation('') }} placeholder={'e.g.\nExperience with dbt\nCloud data platform exposure'} />
        <div className="audit-input-hint"><CircleHelp size={14} />{requirementCount} of 60 structured qualifications · Your description and structured requirements are sent to the audit endpoint.</div>
        {validation && <div className="audit-message audit-validation" role="alert"><AlertCircle size={16} />{validation}</div>}
        {error && <div className="audit-message audit-error" role="alert"><AlertCircle size={16} /><span>{error}</span><button type="button" onClick={() => setError('')}>Dismiss</button></div>}
        <div className="audit-submit-row"><span><ShieldCheck size={14} />No candidate data is used</span><button className="button-primary" type="submit" disabled={loading}>{loading ? <><LoaderCircle size={15} className="spin-icon" />Auditing criteria…</> : <>{result ? <RotateCw size={15} /> : <Sparkles size={15} />}{result ? 'Run audit again' : 'Run Bias Audit'}</>}</button></div>
      </form>

      <section className="audit-results" aria-live="polite" aria-busy={loading}>
        {loading ? <div className="panel audit-state-card"><div className="audit-state-icon audit-loading-icon"><LoaderCircle className="spin-icon" size={22} /></div><h2>Reviewing role criteria</h2><p>Checking the supplied wording against deterministic audit rules. No AI provider is used.</p><div className="audit-loading-bar"><span /></div></div> : error && !result ? <div className="panel audit-state-card"><div className="audit-state-icon audit-error-icon"><AlertCircle size={21} /></div><h2>Audit could not be completed</h2><p>{error}</p><button className="button-secondary" type="button" onClick={() => setError('')}>Return to form</button></div> : result ? <>
          <div className="audit-summary panel"><div className="audit-summary-top"><div><span className="eyebrow"><span className="eyebrow-line" /> AUDIT SUMMARY</span><h2>{result.job_title || jobTitle.trim() || 'Role criteria review'}</h2></div><span className="audit-method-pill"><ShieldCheck size={13} />Rule-based review</span></div><div className="audit-summary-stats"><div><b>{result.criteria_reviewed}</b><span>Criteria reviewed</span></div><div><b>{result.finding_count}</b><span>Potential concerns</span></div><div><b>{Object.values(reviews).filter((state) => state !== 'Needs review').length}</b><span>Reviewed by you</span></div></div><p className="audit-result-disclaimer">{result.disclaimer}</p></div>
          {result.findings.length === 0 ? <div className="panel audit-state-card audit-no-findings"><div className="audit-state-icon"><Check size={21} /></div><span className="audit-no-findings-label">No rule-based concerns surfaced</span><h2>No findings from these checks</h2><p>This result is limited to the wording patterns covered by the deterministic rules. It does not establish that the role is fair or legally compliant.</p></div> : <div className="audit-findings"><div className="audit-findings-heading"><div><h2>Findings for human review</h2><p>{result.finding_count} potential {result.finding_count === 1 ? 'concern' : 'concerns'} · No automated conclusions</p></div><span>{result.finding_count} findings</span></div>{result.findings.map((finding, index) => {
            const key = `${finding.rule_id}:${finding.criterion_importance}:${finding.triggering_phrase}`
            return <FindingCard key={`${key}-${index}`} finding={finding} review={reviews[key] ?? 'Needs review'} onReview={(state) => updateReview(key, state)} />
          })}</div>}
          <div className="audit-temp-note"><AlertCircle size={14} />Review choices are temporary and will be lost when this page is refreshed.</div>
        </> : <div className="panel audit-state-card audit-empty-state"><div className="audit-state-icon"><ClipboardCheck size={21} /></div><span className="audit-empty-label">READY WHEN YOU ARE</span><h2>Start with the role criteria</h2><p>Paste a job description and optionally separate essential requirements from preferred qualifications. The audit will return specific wording for your team to consider.</p><div className="audit-empty-chips"><span>Credential requirements</span><span>Restrictive wording</span><span>Vague skills</span><span>Experience thresholds</span></div><span className="audit-empty-footnote">Up to 10,000 description characters · 60 structured qualifications</span></div>}
      </section>
    </div>
    {!hasInput && !result && <p className="audit-footer-note">Your input stays in the current session. The backend evaluates job criteria only; it does not use Gemini or candidate information.</p>}
  </div>
}
