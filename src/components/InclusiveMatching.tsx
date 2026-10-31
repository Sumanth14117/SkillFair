import { useMemo, useRef, useState, type FormEvent } from 'react'
import { AlertCircle, AlertTriangle, Check, CircleHelp, LoaderCircle, ShieldCheck, Sparkles, UsersRound } from 'lucide-react'
import '../styles/inclusive-matching.css'
import { skillFairFetch } from '../apiClient'

type Importance = 'essential' | 'preferred'
type Evidence = {
  skill_name: string
  evidence_type: 'explicit' | 'inferred'
  detection_method: 'ai' | 'local'
  validation_status: 'evidence_found' | 'inference_unverified'
  supporting_excerpt: string
  relationship_type: 'exact_name_match' | 'curated_adjacent_capability'
  explanation: string
}
type Requirement = {
  skill: string
  importance: Importance
  status: 'explicitly_evidenced' | 'inferred_unverified' | 'potential_transfer' | 'not_evidenced'
  evidence: Evidence[]
  explanation: string
}
type AuditFinding = {
  rule_id: string
  category: 'credential_requirement' | 'potentially_exclusionary_wording' | 'vague_requirement' | 'experience_threshold'
  priority: 'low' | 'medium' | 'high'
  triggering_phrase: string
  criterion_importance: 'essential' | 'preferred' | 'unspecified'
  explanation: string
  suggested_revision_or_action: string
  status: 'potential_concern_for_human_review'
  human_review_required: true
}
type InclusiveResult = {
  job_title: string
  requirements: Requirement[]
  exact_explicit_count: number
  exact_inferred_count: number
  potential_transfer_count: number
  not_evidenced_count: number
  bias_audit_findings: AuditFinding[]
  human_review_prompts: string[]
  human_review_required: true
  disclaimer: string
}
type Props = { analysisId: string | null; onAnalysisExpired: () => void; onSkillsDiscovery: () => void }
type FailureKind = 'validation' | 'network' | 'expired' | 'service'

const STATUS: Record<Requirement['status'], string> = {
  explicitly_evidenced: 'Explicitly evidenced', inferred_unverified: 'Inferred · unverified',
  potential_transfer: 'Potential transfer', not_evidenced: 'Not evidenced in this analysis',
}
const CATEGORY: Record<AuditFinding['category'], string> = {
  credential_requirement: 'Credential requirement', potentially_exclusionary_wording: 'Potentially exclusionary wording',
  vague_requirement: 'Vague requirement', experience_threshold: 'Experience threshold',
}

function parseSkills(value: string) {
  return value.split(/[\n,]+/).map((skill) => skill.trim()).filter(Boolean)
}

function validationMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((item) => item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string' ? item.msg : '').filter(Boolean).join(' ')
  return ''
}

async function requestInclusiveMatch(payload: { analysis_id: string; job_title: string; job_description: string; essential_skills: string[]; preferred_skills: string[] }): Promise<InclusiveResult> {
  let response: Response
  try {
    response = await skillFairFetch('/api/jobs/inclusive-match', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    })
  } catch {
    throw Object.assign(new Error('Could not reach Inclusive Matching. Check that the backend is running and try again.'), { kind: 'network' as FailureKind })
  }
  let body: Partial<InclusiveResult> & { detail?: unknown }
  try { body = await response.json() as typeof body } catch {
    throw Object.assign(new Error('The service returned an unreadable response. Please try again.'), { kind: 'service' as FailureKind })
  }
  if (!response.ok) {
    if (response.status === 404) throw Object.assign(new Error('This Skills Discovery analysis is unknown or has expired. Run Skills Discovery again, then retry.'), { kind: 'expired' as FailureKind })
    const detail = validationMessage(body.detail)
    throw Object.assign(new Error(detail || 'Inclusive Matching could not be completed. Check the role details and try again.'), { kind: response.status === 422 ? 'validation' as FailureKind : 'service' as FailureKind })
  }
  if (typeof body.job_title !== 'string' || !Array.isArray(body.requirements) || typeof body.exact_explicit_count !== 'number' || typeof body.exact_inferred_count !== 'number' || typeof body.potential_transfer_count !== 'number' || typeof body.not_evidenced_count !== 'number' || !Array.isArray(body.bias_audit_findings) || !Array.isArray(body.human_review_prompts) || typeof body.disclaimer !== 'string' || body.human_review_required !== true) {
    throw Object.assign(new Error('The service returned an unsupported response. Please try again.'), { kind: 'service' as FailureKind })
  }
  return body as InclusiveResult
}

function RequirementCard({ item }: { item: Requirement }) {
  return <article className="inclusive-requirement-card">
    <header><div><span className="inclusive-criterion-label">{item.importance.toUpperCase()} REQUIREMENT</span><h4>{item.skill}</h4></div><span className={`inclusive-status status-${item.status}`}>{STATUS[item.status]}</span></header>
    <p className="inclusive-explanation">{item.explanation}</p>
    {item.evidence.map((evidence, index) => <article className={`inclusive-evidence ${evidence.evidence_type}`} key={`${evidence.skill_name}-${index}`}>
      <div className="inclusive-evidence-heading"><div><b>{evidence.skill_name}</b><span className={`inclusive-evidence-type ${evidence.evidence_type}`}>{evidence.evidence_type === 'inferred' ? 'Inferred · unverified' : evidence.detection_method === 'local' ? 'Local mention · not proficiency proof' : 'Explicit evidence'}</span></div><span className="inclusive-relationship">{evidence.relationship_type === 'exact_name_match' ? 'Exact name match' : 'Adjacent capability'}</span></div>
      <p>{evidence.explanation}</p><details><summary>View supporting excerpt</summary><blockquote>“{evidence.supporting_excerpt}”</blockquote></details>
    </article>)}
  </article>
}

function RequirementsGroup({ title, subtitle, items }: { title: string; subtitle: string; items: Requirement[] }) {
  return <section className="inclusive-group"><div className="inclusive-group-heading"><div><h3>{title}</h3><p>{subtitle}</p></div><span>{items.length}</span></div>{items.length ? <div className="inclusive-requirements">{items.map((item, index) => <RequirementCard item={item} key={`${item.skill}-${index}`} />)}</div> : <div className="inclusive-no-criteria">No {title.toLowerCase()} were entered.</div>}</section>
}

export default function InclusiveMatching({ analysisId, onAnalysisExpired, onSkillsDiscovery }: Props) {
  const [jobTitle, setJobTitle] = useState('')
  const [jobDescription, setJobDescription] = useState('')
  const [essentialText, setEssentialText] = useState('')
  const [preferredText, setPreferredText] = useState('')
  const [result, setResult] = useState<InclusiveResult | null>(null)
  const [error, setError] = useState<{ kind: FailureKind; message: string } | null>(null)
  const [validation, setValidation] = useState('')
  const [loading, setLoading] = useState(false)
  const requestInFlight = useRef(false)
  const essentialSkills = useMemo(() => parseSkills(essentialText), [essentialText])
  const preferredSkills = useMemo(() => parseSkills(preferredText), [preferredText])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (requestInFlight.current || loading) return
    const title = jobTitle.trim()
    const description = jobDescription.trim()
    if (!title || title.length > 160) { setValidation('Enter a job title of 1 to 160 characters.'); return }
    if (!description || description.length > 10000) { setValidation('Enter a job description of 1 to 10,000 characters.'); return }
    if (essentialSkills.length > 60 || preferredSkills.length > 60 || essentialSkills.length + preferredSkills.length > 60) { setValidation('Enter no more than 60 essential and preferred requirements combined.'); return }
    if ([...essentialSkills, ...preferredSkills].some((skill) => skill.length > 100)) { setValidation('Each skill must be 100 characters or fewer.'); return }
    if (!analysisId) { setValidation('Complete Skills Discovery first to link validated candidate evidence.'); return }
    requestInFlight.current = true; setLoading(true); setValidation(''); setError(null); setResult(null)
    try {
      setResult(await requestInclusiveMatch({ analysis_id: analysisId, job_title: title, job_description: description, essential_skills: essentialSkills, preferred_skills: preferredSkills }))
    } catch (reason) {
      const failure = reason as Error & { kind?: FailureKind }
      if (failure.kind === 'expired') onAnalysisExpired()
      setError({ kind: failure.kind ?? 'service', message: failure.message || 'Inclusive Matching could not be completed. Please try again.' })
    } finally { requestInFlight.current = false; setLoading(false) }
  }

  const essentialResults = result?.requirements.filter((item) => item.importance === 'essential') ?? []
  const preferredResults = result?.requirements.filter((item) => item.importance === 'preferred') ?? []

  return <div className="inclusive-page">
    <header className="inclusive-heading"><div><span className="eyebrow"><span className="eyebrow-line" /> EVIDENCE-LED · RECRUITER-REVIEWED</span><h1>Inclusive Matching</h1><p>Review role requirements alongside validated candidate evidence and advisory criteria findings.</p></div><span className="inclusive-heading-pill"><UsersRound size={16} />No ranking · Human decision</span></header>
    <div className="inclusive-advisory" role="note"><div><ShieldCheck size={17} /></div><p><b>Support for human review, not an automated decision.</b> Missing evidence does not prove missing ability. Inferences are unverified; potential transfers do not establish skill equivalence. Bias Audit findings are advisory and do not establish fairness. Recruiters remain responsible for decisions. No automatic ranking or rejection is performed.</p></div>

    <div className="inclusive-layout">
      <form className="inclusive-form panel" onSubmit={submit} noValidate>
        <div className="inclusive-form-title"><span><Sparkles size={16} /></span><div><h2>Role criteria</h2><p>Set the job context and distinguish essential from preferred skills.</p></div></div>
        <label htmlFor="inclusive-job-title">Job title</label><input id="inclusive-job-title" value={jobTitle} onChange={(event) => { setJobTitle(event.target.value); setValidation('') }} maxLength={160} placeholder="e.g. Data Reporting Analyst" autoComplete="organization-title" />
        <label htmlFor="inclusive-job-description">Job description</label><textarea id="inclusive-job-description" value={jobDescription} onChange={(event) => { setJobDescription(event.target.value); setValidation('') }} rows={5} maxLength={10000} placeholder="Describe the role’s responsibilities and outcomes…" />
        <label htmlFor="inclusive-essential-skills">Essential skills <span>{essentialSkills.length}/60</span></label><textarea id="inclusive-essential-skills" value={essentialText} onChange={(event) => { setEssentialText(event.target.value); setValidation('') }} rows={4} placeholder={'One per line or comma-separated\nSQL\nData reporting'} />
        <label htmlFor="inclusive-preferred-skills">Preferred skills <span>{preferredSkills.length} entered</span></label><textarea id="inclusive-preferred-skills" value={preferredText} onChange={(event) => { setPreferredText(event.target.value); setValidation('') }} rows={4} placeholder={'One per line or comma-separated\nDashboard design\nExcel'} />
        <div className={`inclusive-source ${analysisId ? 'linked' : 'missing'}`}><span>{analysisId ? <Check size={15} /> : <AlertCircle size={15} />}</span><div><b>{analysisId ? 'Skills analysis is linked' : 'Skills Discovery required'}</b><p>{analysisId ? 'The backend resolves validated skills and excerpts from this analysis reference. Candidate evidence is not sent from this form.' : 'Run Skills Discovery first. This endpoint requires an analysis reference to access candidate evidence.'}</p></div>{!analysisId && <button type="button" onClick={onSkillsDiscovery}>Open Skills Discovery</button>}</div>
        {validation && <div className="inclusive-message validation" role="alert"><AlertCircle size={15} /><span>{validation}</span></div>}
        {error && <div className={`inclusive-message ${error.kind === 'expired' ? 'expired' : 'error'}`} role="alert"><AlertCircle size={15} /><span>{error.message}</span><button type="button" onClick={() => setError(null)}>Dismiss</button></div>}
        <div className="inclusive-submit-row"><span><ShieldCheck size={14} />Evidence stays server-side</span><button className="button-primary" type="submit" disabled={loading || !analysisId}>{loading ? <><LoaderCircle size={15} className="spin-icon" />Reviewing…</> : <>Review requirements <Sparkles size={15} /></>}</button></div>
      </form>

      <section className="inclusive-results" aria-live="polite" aria-busy={loading}>
        {loading ? <div className="panel inclusive-state"><div className="inclusive-state-icon busy"><LoaderCircle size={22} className="spin-icon" /></div><h2>Reviewing role evidence</h2><p>Comparing supplied criteria with validated analysis evidence and running the advisory Bias Audit checks.</p><div className="inclusive-progress"><i /></div></div>
          : error ? <div className="panel inclusive-state"><div className="inclusive-state-icon failed"><AlertCircle size={21} /></div><h2>{error.kind === 'validation' ? 'Check the role details' : error.kind === 'network' ? 'Inclusive Matching is unreachable' : error.kind === 'expired' ? 'Analysis unavailable' : 'Review could not be completed'}</h2><p>{error.message}</p><button className="button-secondary" type="button" onClick={() => setError(null)}>Return to the form</button></div>
            : result ? <>
              <div className="panel inclusive-summary"><div className="inclusive-summary-top"><div><span className="eyebrow"><span className="eyebrow-line" /> RECRUITER REVIEW</span><h2>{result.job_title}</h2></div><span className="inclusive-human-pill"><ShieldCheck size={14} />Human review required</span></div><div className="inclusive-counts"><div><b>{result.exact_explicit_count}</b><span>Explicit matches</span></div><div><b>{result.exact_inferred_count}</b><span>Inferred · unverified</span></div><div><b>{result.potential_transfer_count}</b><span>Potential transfers</span></div><div><b>{result.not_evidenced_count}</b><span>Not evidenced</span></div></div><p className="inclusive-summary-note">Counts describe evidence categories, not a candidate score or qualification measure.</p></div>
              <RequirementsGroup title="Essential requirements" subtitle="Review necessity and the supporting evidence for every criterion." items={essentialResults} />
              <RequirementsGroup title="Preferred requirements" subtitle="These remain separate from essential criteria in the backend response." items={preferredResults} />
              <section className="inclusive-group"><div className="inclusive-group-heading"><div><h3>Advisory Bias Audit findings</h3><p>Potential wording or criteria concerns for human review; not determinations.</p></div><span>{result.bias_audit_findings.length}</span></div>{result.bias_audit_findings.length ? <div className="inclusive-findings">{result.bias_audit_findings.map((finding, index) => <article className="inclusive-finding" key={`${finding.rule_id}-${index}`}><header><div><span className={`inclusive-priority priority-${finding.priority}`}>{finding.priority} priority</span><span className="inclusive-category">{CATEGORY[finding.category]}</span></div><span className="inclusive-importance">{finding.criterion_importance}</span></header>{finding.triggering_phrase && <blockquote>“{finding.triggering_phrase}”</blockquote>}<p>{finding.explanation}</p><div className="inclusive-action"><b>Suggested review</b><span>{finding.suggested_revision_or_action}</span></div><small><AlertTriangle size={12} />Advisory finding · Human review required</small></article>)}</div> : <div className="inclusive-no-criteria">No potential concerns were returned by the deterministic audit rules. This does not establish fairness or legal compliance.</div>}</section>
              <section className="panel inclusive-prompts"><div className="inclusive-group-heading"><div><h3>Human-review prompts</h3><p>Consider these questions alongside the evidence and role context.</p></div><CircleHelp size={17} /></div><ul>{result.human_review_prompts.map((prompt, index) => <li key={`${index}-${prompt}`}>{prompt}</li>)}</ul></section>
              <div className="inclusive-disclaimer"><ShieldCheck size={16} /><p>{result.disclaimer}</p></div>
            </>
            : <div className="panel inclusive-state inclusive-empty"><div className="inclusive-state-icon"><UsersRound size={21} /></div><span className="inclusive-empty-label">READY FOR HUMAN REVIEW</span><h2>Start with role requirements</h2><p>Compare essential and preferred skills against a linked Skills Discovery analysis. The service returns evidence and explanations without candidate scores or ranking.</p><div className="inclusive-empty-footnote"><ShieldCheck size={14} />Bias Audit feedback is advisory, not a fairness determination.</div></div>}
      </section>
    </div>
  </div>
}
