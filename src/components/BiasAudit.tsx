import { useEffect, useRef, useState, type FormEvent } from 'react'
import { AlertCircle, AlertTriangle, Check, CircleHelp, ClipboardCheck, LoaderCircle, RotateCw, ShieldCheck, Sparkles, Trash2 } from 'lucide-react'
import { deleteSavedAudit, getSavedAudit, listSavedAudits, saveAudit, submitAudit, updateSavedAuditReview, type AuditRequest, type AuditResult, type Finding, type Importance, type ReviewState as SavedReviewState, type SavedAudit, type SavedAuditSummary } from '../jobAuditClient'
import { useAuth } from '../authContext'
import '../styles/bias-audit.css'

type ReviewState = 'needs_review' | 'accepted' | 'edited' | 'dismissed'

const REVIEW_STATES: { value: ReviewState; label: string }[] = [
  { value: 'needs_review', label: 'Needs review' }, { value: 'accepted', label: 'Accepted' },
  { value: 'edited', label: 'Edited' }, { value: 'dismissed', label: 'Dismissed' },
]
const CATEGORY_LABELS: Record<Finding['category'], string> = {
  credential_requirement: 'Credential requirement',
  potentially_exclusionary_wording: 'Potentially exclusionary wording',
  vague_requirement: 'Vague requirement',
  experience_threshold: 'Experience threshold',
}

function linesToRequirements(value: string, importance: Exclude<Importance, 'unspecified'>) {
  return value.split('\n').map((text) => text.trim()).filter(Boolean).map((text) => ({ text, importance }))
}

function FindingCard({ finding, review, onReview, saving }: { finding: Finding; review: ReviewState; onReview: (state: ReviewState) => void; saving: boolean }) {
  return <article className="audit-finding-card">
    <div className="audit-finding-top">
      <div className="audit-finding-labels"><span className="audit-category">{CATEGORY_LABELS[finding.category]}</span><span className={`audit-priority priority-${finding.priority}`}>{finding.priority} priority</span></div>
      <span className="audit-rule-id">{finding.rule_id}</span>
    </div>
    {finding.triggering_phrase && <blockquote className="audit-trigger">“{finding.triggering_phrase}”</blockquote>}
    <p className="audit-finding-explanation">{finding.explanation}</p>
    {finding.suggested_revision_or_action && <div className="audit-suggestion"><Sparkles size={15} /><div><b>Suggested action</b><p>{finding.suggested_revision_or_action}</p></div></div>}
    <div className="audit-review-row"><div><b>Recruiter review</b><span>{saving ? 'Saving selection…' : 'Selection is saved with this audit'}</span></div><div className="audit-review-options" role="group" aria-label={`Review status for ${finding.rule_id}`}>
      {REVIEW_STATES.map(({ value, label }) => <button key={value} type="button" className={review === value ? 'review-selected' : ''} aria-pressed={review === value} disabled={saving} onClick={() => onReview(value)}>{review === value && <Check size={13} />}{label}</button>)}
    </div></div>
  </article>
}

export default function BiasAudit({ initialAuditId = null }: { initialAuditId?: string | null }) {
  const auth = useAuth()
  const ownerId = auth.user?.id ?? ''
  const [jobTitle, setJobTitle] = useState('')
  const [jobDescription, setJobDescription] = useState('')
  const [essential, setEssential] = useState('')
  const [preferred, setPreferred] = useState('')
  const [result, setResult] = useState<AuditResult | null>(null)
  const [reviews, setReviews] = useState<Record<string, ReviewState>>({})
  const [auditId, setAuditId] = useState<string | null>(null)
  const [savedAudits, setSavedAudits] = useState<SavedAuditSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState('')
  const [saveLoading, setSaveLoading] = useState(false)
  const [reviewSaving, setReviewSaving] = useState<number | null>(null)
  const [historyOpenError, setHistoryOpenError] = useState('')
  const [error, setError] = useState('')
  const [validation, setValidation] = useState('')
  const [loading, setLoading] = useState(false)
  const requestInFlight = useRef(false)
  const requestId = useRef(0)
  const requestController = useRef<AbortController | null>(null)
  const historyController = useRef<AbortController | null>(null)
  const savedActionController = useRef<AbortController | null>(null)
  const savedActionId = useRef(0)
  const lastPayload = useRef<AuditRequest | null>(null)
  const requirementCount = linesToRequirements(essential, 'essential').length + linesToRequirements(preferred, 'preferred').length

  useEffect(() => () => {
    requestId.current += 1
    requestController.current?.abort()
    requestController.current = null
  }, [])

  useEffect(() => {
    if (!ownerId) return
    savedActionController.current?.abort()
    setAuditId(null)
    setResult(null)
    setReviews({})
    setSavedAudits([])
    lastPayload.current = null
    historyController.current?.abort()
    const controller = new AbortController()
    historyController.current = controller
    let active = true
    setHistoryLoading(true)
    setHistoryError('')
    const restoreAction = savedActionId.current
    void listSavedAudits(undefined, controller.signal).then(async (response) => {
      if (!active) return
      setSavedAudits(response.audits)
      const latest = response.audits[0]
      if (latest && savedActionId.current === restoreAction) {
        const saved = await getSavedAudit(latest.audit_id, undefined, controller.signal)
        if (active && savedActionId.current === restoreAction && !controller.signal.aborted) applySavedAudit(saved)
      }
    }).catch((reason: unknown) => {
      if (active && !controller.signal.aborted) setHistoryError(reason instanceof Error ? reason.message : 'Saved audit history could not be loaded.')
    }).finally(() => { if (active) setHistoryLoading(false) })
    return () => { active = false; controller.abort() }
  }, [ownerId])

  useEffect(() => {
    if (!ownerId || !initialAuditId) return
    savedActionController.current?.abort()
    const controller = new AbortController()
    savedActionController.current = controller
    const action = ++savedActionId.current
    setHistoryOpenError('')
    void getSavedAudit(initialAuditId, undefined, controller.signal).then((saved) => {
      if (savedActionId.current === action && !controller.signal.aborted) applySavedAudit(saved)
    }).catch((reason: unknown) => {
      if (savedActionId.current === action && !controller.signal.aborted) setHistoryOpenError(reason instanceof Error ? reason.message : 'The saved audit could not be opened.')
    })
    return () => { controller.abort() }
  }, [ownerId, initialAuditId])

  useEffect(() => () => {
    historyController.current?.abort()
    savedActionController.current?.abort()
  }, [])

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
    lastPayload.current = payload
    requestInFlight.current = true
    const thisRequest = ++requestId.current
    const controller = new AbortController()
    requestController.current = controller
    const isCurrent = () => requestId.current === thisRequest && !controller.signal.aborted
    setLoading(true)
    setError('')
    setValidation('')
    try {
      const audit = await submitAudit(payload, undefined, controller.signal)
      if (!isCurrent()) return
      setResult(audit)
      setAuditId(null)
      setReviews(Object.fromEntries(audit.findings.map((_finding, index) => [String(index), 'needs_review'])))
    } catch (reason) {
      if (isCurrent()) setError(reason instanceof Error ? reason.message : 'The audit could not be completed. Please try again.')
    } finally {
      if (requestId.current === thisRequest) {
        requestInFlight.current = false
        requestController.current = null
        setLoading(false)
      }
    }
  }

  async function saveCurrentAudit() {
    if (!lastPayload.current || saveLoading) return
    savedActionController.current?.abort()
    const controller = new AbortController()
    savedActionController.current = controller
    const action = ++savedActionId.current
    setSaveLoading(true)
    setError('')
    try {
      const saved = await saveAudit(lastPayload.current, undefined, controller.signal)
      if (savedActionId.current !== action || controller.signal.aborted) return
      applySavedAudit(saved)
      setSavedAudits((current) => [saved, ...current.filter((item) => item.audit_id !== saved.audit_id)].slice(0, 20))
    } catch (reason) {
      if (savedActionId.current === action && !controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'The audit could not be saved.')
    } finally {
      if (savedActionId.current === action) setSaveLoading(false)
    }
  }

  function applySavedAudit(saved: SavedAudit) {
    setAuditId(saved.audit_id)
    setResult(saved.result)
    setJobTitle(saved.job_title ?? '')
    setJobDescription('')
    setEssential('')
    setPreferred('')
    lastPayload.current = null
    setReviews(Object.fromEntries(saved.review_selections.map((item) => [String(item.finding_index), item.review_state])))
  }

  async function openSavedAudit(summary: SavedAuditSummary) {
    savedActionController.current?.abort()
    const controller = new AbortController()
    savedActionController.current = controller
    const action = ++savedActionId.current
    setHistoryOpenError('')
    try {
      const saved = await getSavedAudit(summary.audit_id, undefined, controller.signal)
      if (savedActionId.current === action && !controller.signal.aborted) applySavedAudit(saved)
    } catch (reason) {
      if (savedActionId.current === action && !controller.signal.aborted) setHistoryOpenError(reason instanceof Error ? reason.message : 'The saved audit could not be opened.')
    }
  }

  async function updateReview(findingIndex: number, review: ReviewState) {
    if (!auditId) {
      setReviews((current) => ({ ...current, [String(findingIndex)]: review }))
      return
    }
    if (reviewSaving !== null) return
    setReviewSaving(findingIndex)
    setError('')
    const controller = new AbortController()
    savedActionController.current?.abort()
    savedActionController.current = controller
    const action = ++savedActionId.current
    try {
      const saved = await updateSavedAuditReview(auditId, findingIndex, review as SavedReviewState, undefined, controller.signal)
      if (savedActionId.current === action && !controller.signal.aborted) applySavedAudit(saved)
    } catch (reason) {
      if (savedActionId.current === action && !controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'The review selection could not be saved.')
    } finally {
      if (savedActionId.current === action) setReviewSaving(null)
    }
  }

  async function removeSavedAudit(summary: SavedAuditSummary) {
    savedActionController.current?.abort()
    const controller = new AbortController()
    savedActionController.current = controller
    const action = ++savedActionId.current
    setHistoryOpenError('')
    try {
      await deleteSavedAudit(summary.audit_id, undefined, controller.signal)
      if (savedActionId.current !== action || controller.signal.aborted) return
      setSavedAudits((current) => current.filter((item) => item.audit_id !== summary.audit_id))
      if (auditId === summary.audit_id) {
        setAuditId(null)
        setResult(null)
        setReviews({})
        lastPayload.current = null
      }
    } catch (reason) {
      if (savedActionId.current === action && !controller.signal.aborted) setHistoryOpenError(reason instanceof Error ? reason.message : 'The saved audit could not be deleted.')
    }
  }

  const hasInput = Boolean(jobDescription.trim() || requirementCount)
  return <div className="audit-page">
    <header className="audit-page-heading"><div><span className="eyebrow"><span className="eyebrow-line" /> HUMAN-LED CRITERIA REVIEW</span><h1>Bias Audit</h1><p>Surface job criteria and wording that may benefit from a closer look.</p></div><div className="audit-heading-note"><ShieldCheck size={16} />Deterministic · Advisory only</div></header>
    <div className="audit-advisory" role="note"><div className="audit-advisory-icon"><AlertTriangle size={18} /></div><p><b>Human review is essential.</b> Findings are potential concerns, not proof of discrimination or unfairness, and do not establish legal compliance. The audit does not assess candidates or infer demographic characteristics.</p></div>

    <section className="panel audit-history" aria-labelledby="audit-history-heading" aria-busy={historyLoading}>
      <div className="audit-history-heading"><div><h2 id="audit-history-heading">Saved audits</h2><p>Only deterministic results and your per-finding review selections are stored with this account. The full job description is not retained.</p></div>{historyLoading && <span role="status">Loading history…</span>}</div>
      {historyError && <p className="audit-message audit-error" role="alert">{historyError}<button type="button" onClick={() => { setHistoryError(''); setHistoryLoading(true); historyController.current?.abort(); const controller = new AbortController(); historyController.current = controller; void listSavedAudits(undefined, controller.signal).then((data) => setSavedAudits(data.audits)).catch((reason: unknown) => setHistoryError(reason instanceof Error ? reason.message : 'Saved audit history could not be loaded.')).finally(() => setHistoryLoading(false)) }}>Retry</button></p>}
      {historyOpenError && <p className="audit-message audit-error" role="alert">{historyOpenError}</p>}
      {!historyLoading && !historyError && savedAudits.length === 0 && <p className="audit-history-empty">No saved audits yet. Run an audit, then save it to revisit the result later.</p>}
      {savedAudits.length > 0 && <ul className="audit-history-list">{savedAudits.map((item) => <li key={item.audit_id} className={auditId === item.audit_id ? 'audit-history-active' : ''}><button type="button" className="audit-history-open" onClick={() => void openSavedAudit(item)}><b>{item.job_title || 'Role criteria review'}</b><span>{new Date(item.created_at).toLocaleString()} · {item.finding_count} findings · {item.reviewed_finding_count} reviewed</span></button><button type="button" className="audit-history-delete" aria-label={`Delete saved audit ${item.job_title ?? 'role criteria review'}`} onClick={() => void removeSavedAudit(item)}><Trash2 size={15} /></button></li>)}</ul>}
    </section>

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
        <div className="audit-submit-row"><span><ShieldCheck size={14} />No candidate data is used</span><div>{result && !auditId && <button className="button-secondary" type="button" onClick={() => void saveCurrentAudit()} disabled={saveLoading || loading}>{saveLoading ? 'Saving…' : 'Save audit'}</button>}<button className="button-primary" type="submit" disabled={loading}>{loading ? <><LoaderCircle size={15} className="spin-icon" />Auditing criteria…</> : <>{result ? <RotateCw size={15} /> : <Sparkles size={15} />}{result ? 'Run audit again' : 'Run Bias Audit'}</>}</button></div></div>
      </form>

      <section className="audit-results" aria-live="polite" aria-busy={loading}>
        {loading ? <div className="panel audit-state-card"><div className="audit-state-icon audit-loading-icon"><LoaderCircle className="spin-icon" size={22} /></div><h2>Reviewing role criteria</h2><p>Checking the supplied wording against deterministic audit rules. No AI provider is used.</p><div className="audit-loading-bar"><span /></div></div> : error && !result ? <div className="panel audit-state-card"><div className="audit-state-icon audit-error-icon"><AlertCircle size={21} /></div><h2>Audit could not be completed</h2><p>{error}</p><button className="button-secondary" type="button" onClick={() => setError('')}>Return to form</button></div> : result ? <>
          <div className="audit-summary panel"><div className="audit-summary-top"><div><span className="eyebrow"><span className="eyebrow-line" /> AUDIT SUMMARY</span><h2>{result.job_title || jobTitle.trim() || 'Role criteria review'}</h2></div><span className="audit-method-pill"><ShieldCheck size={13} />Rule-based review</span></div><div className="audit-summary-stats"><div><b>{result.criteria_reviewed}</b><span>Criteria reviewed</span></div><div><b>{result.finding_count}</b><span>Potential concerns</span></div><div><b>{Object.values(reviews).filter((state) => state !== 'needs_review').length}</b><span>Reviewed by you</span></div></div><p className="audit-result-disclaimer">{result.disclaimer}</p></div>
          {result.findings.length === 0 ? <div className="panel audit-state-card audit-no-findings"><div className="audit-state-icon"><Check size={21} /></div><span className="audit-no-findings-label">No rule-based concerns surfaced</span><h2>No findings from these checks</h2><p>This result is limited to the wording patterns covered by the deterministic rules. It does not establish that the role is fair or legally compliant.</p></div> : <div className="audit-findings"><div className="audit-findings-heading"><div><h2>Findings for human review</h2><p>{result.finding_count} potential {result.finding_count === 1 ? 'concern' : 'concerns'} · No automated conclusions</p></div><span>{result.finding_count} findings</span></div>{result.findings.map((finding, index) => <FindingCard key={`${finding.rule_id}-${index}`} finding={finding} review={reviews[String(index)] ?? 'needs_review'} saving={reviewSaving === index} onReview={(state) => void updateReview(index, state)} />)}</div>}
          {auditId ? <div className="audit-temp-note"><Check size={14} />Saved to this account · updated {new Date(savedAudits.find((item) => item.audit_id === auditId)?.updated_at ?? '').toLocaleString()}</div> : <div className="audit-temp-note"><AlertCircle size={14} />Unsaved draft · review selections are stored only after you save this audit.</div>}
        </> : <div className="panel audit-state-card audit-empty-state"><div className="audit-state-icon"><ClipboardCheck size={21} /></div><span className="audit-empty-label">READY WHEN YOU ARE</span><h2>Start with the role criteria</h2><p>Paste a job description and optionally separate essential requirements from preferred qualifications. The audit will return specific wording for your team to consider.</p><div className="audit-empty-chips"><span>Credential requirements</span><span>Restrictive wording</span><span>Vague skills</span><span>Experience thresholds</span></div><span className="audit-empty-footnote">Up to 10,000 description characters · 60 structured qualifications</span></div>}
      </section>
    </div>
    {!hasInput && !result && <p className="audit-footer-note">Your input stays in the current session. The backend evaluates job criteria only; it does not use Gemini or candidate information.</p>}
  </div>
}
