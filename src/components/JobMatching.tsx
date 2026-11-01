import { useMemo, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, BriefcaseBusiness, Check, CircleHelp, LoaderCircle, ShieldCheck, Sparkles } from 'lucide-react'
import type { SkillsDiscoveryResult } from '../types/skills'
import { skillFairFetch } from '../apiClient'
import '../styles/job-matching.css'

type MatchItem = {
  required_skill: string
  candidate_skill: string
  evidence_type: 'explicit' | 'inferred'
  detection_method: 'ai' | 'local'
  validation_status: 'evidence_found' | 'inference_unverified'
  supporting_excerpt: string
  relevance_explanation: string
}
type MatchResult = {
  job_title: string
  total_required: number
  explicitly_supported: number
  inferred_unverified: number
  missing_count: number
  evidence_coverage_percent: number
  matched_skills: MatchItem[]
  missing_skills: string[]
  calculation_method: string
}
type Props = {
  analysisId: string | null
  skillCount: number
  analysisMode: SkillsDiscoveryResult['analysis_mode'] | null
  onAnalysisExpired: () => void
  onBack: () => void
}

async function requestMatch(payload: object): Promise<MatchResult> {
  const response = await skillFairFetch('/api/jobs/match', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  let body: MatchResult | { detail?: string }
  try { body = await response.json() as typeof body } catch { throw new Error('The matching service returned an unreadable response.') }
  if (!response.ok) throw Object.assign(new Error(response.status === 404 ? 'This Skills Discovery analysis is unknown or has expired. Upload and analyse the resume again.' : 'detail' in body && body.detail ? body.detail : 'Job matching failed. Please try again.'), { status: response.status })
  return body as MatchResult
}

export default function JobMatching({ analysisId, skillCount, analysisMode, onAnalysisExpired, onBack }: Props) {
  const [jobTitle, setJobTitle] = useState('')
  const [jobDescription, setJobDescription] = useState('')
  const [skillsText, setSkillsText] = useState('')
  const [result, setResult] = useState<MatchResult | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState(false)
  const requestInFlight = useRef(false)
  const requirements = useMemo(() => skillsText.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean), [skillsText])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (requestInFlight.current || loading || requirements.length === 0) return
    requestInFlight.current = true
    setError('')
    setResult(null)
    setLoading(true)
    try {
      if (!analysisId) throw new Error('Skills analysis is missing or expired. Run Skills Discovery again.')
      const matched = await requestMatch({ analysis_id: analysisId, job_title: jobTitle, job_description: jobDescription, required_skills: requirements })
      setResult(matched)
      setEditing(false)
    } catch (reason) {
      if ((reason as Error & { status?: number }).status === 404) onAnalysisExpired()
      setError(reason instanceof Error ? reason.message : 'Job matching failed. Please try again.')
    } finally {
      requestInFlight.current = false
      setLoading(false)
    }
  }

  return <div className="job-matching-page">
    <div className="discovery-heading"><div><button className="matching-back" onClick={onBack}><ArrowLeft size={14} />Back to Skills Discovery</button><span className="eyebrow"><span className="eyebrow-line" /> EVIDENCE-LED ROLE COMPARISON</span><h1>Job Matching</h1><p>Compare the discovered skills with a role’s requirements.</p></div><div className="privacy-note"><ShieldCheck size={15} />Deterministic · No candidate ranking</div></div>

    {!skillCount ? <section className="matching-empty" role="status"><div className="empty-orbit"><BriefcaseBusiness size={23} /></div><h2>{error ? 'Analysis needs refreshing' : 'No candidate skills to compare yet'}</h2><p>{error || 'Return to Skills Discovery and complete an analysis first. Job Matching uses stored skill evidence and does not upload or process the resume again.'}</p><button className="button-primary" onClick={onBack}>Return to Skills Discovery</button></section> : <>
    <div className="matching-source"><Sparkles size={15} /><span><b>{skillCount} skills in the saved analysis</b><small>The backend checks whether this temporary analysis reference is still available when you compare.</small></span><span className="source-badge">{analysisMode === 'ai' ? 'AI analysis' : analysisMode === 'local' ? 'Local exact mentions' : 'Text extraction only'}</span></div>
      {(!result || editing) && <form className="matching-form panel" onSubmit={submit}>
        <div className="matching-form-heading"><div><h2>Role requirements</h2><p>Enter the role criteria you want to compare.</p></div><span><BriefcaseBusiness size={17} /></span></div>
        <label>Job title<input required maxLength={160} value={jobTitle} onChange={(event) => setJobTitle(event.target.value)} placeholder="e.g. Data Analyst" /></label>
        <label>Job description<textarea required maxLength={10000} rows={4} value={jobDescription} onChange={(event) => setJobDescription(event.target.value)} placeholder="Describe the role and its responsibilities…" /></label>
        <label>Required skills <span className="label-hint">One per line or separated by commas</span><textarea required rows={4} value={skillsText} onChange={(event) => setSkillsText(event.target.value)} placeholder={'SQL\nData visualization\nStakeholder communication'} /></label>
        <div className="matching-form-footer"><span>{requirements.length} requirements · Exact normalized skill-name matching</span><button className="button-primary" type="submit" disabled={loading || requirements.length === 0}>{loading ? <><LoaderCircle className="spin-icon" size={15} />Comparing evidence…</> : <>Compare skills <Sparkles size={15} /></>}</button></div>
        {error && <div className="matching-error" role="alert">{error}</div>}
      </form>}

      {result && !editing && <section className="matching-results" aria-live="polite">
        <div className="matching-results-heading"><div><span className="eyebrow"><span className="eyebrow-line" /> MATCH REVIEW</span><h2>{result.job_title}</h2><p>{result.total_required} unique requirements · Evidence comparison, not a competence assessment</p></div><div className="matching-result-actions"><button className="button-secondary" onClick={() => setEditing(true)}>Edit requirements</button><div className="coverage-card"><strong>{result.evidence_coverage_percent}<small>%</small></strong><span>evidence coverage</span></div></div></div>
        <div className="coverage-explainer"><CircleHelp size={16} /><span><b>How to read this:</b> {result.calculation_method}</span></div>
        <div className="matching-counts"><div><span className="count-dot explicit-dot" />Explicitly supported <b>{result.explicitly_supported}</b></div><div><span className="count-dot inferred-dot" />Inferred · unverified <b>{result.inferred_unverified}</b></div><div><span className="count-dot missing-dot" />Not found <b>{result.missing_count}</b></div></div>
        {result.matched_skills.length > 0 && <div className="match-group"><div className="match-group-heading"><h3><Check size={15} />Skills with evidence</h3><span>Review every excerpt</span></div><div className="match-cards">{result.matched_skills.map((item, index) => <article className="match-evidence-card" key={`${item.required_skill}-${index}`}><div className="match-evidence-title"><b>{item.required_skill}</b><span className={`evidence-badge ${item.evidence_type}`}>{item.evidence_type === 'inferred' ? 'Inferred · unverified' : item.detection_method === 'local' ? 'Local mention · not proficiency proof' : 'Explicit evidence'}</span></div><p>{item.relevance_explanation}</p><blockquote>“{item.supporting_excerpt}”</blockquote></article>)}</div></div>}
        <div className="match-group"><div className="match-group-heading missing-heading"><h3>Requirements not found in the discovered skills</h3><span>Absence from extracted evidence does not establish inability</span></div>{result.missing_skills.length ? <div className="missing-tags">{result.missing_skills.map((skill, index) => <span key={`${skill}-${index}`}>{skill}</span>)}</div> : <div className="match-clear"><Check size={15} />Every unique requirement has a name match. Review the evidence and inferred items.</div>}</div>
        <div className="matching-human-note"><ShieldCheck size={16} /><span>Recruiter review required. This comparison uses only candidate skill names and evidence plus recruiter-entered requirements; it does not use demographic information, rank candidates, or decide suitability.</span></div>
      </section>}
    </>}
  </div>
}
