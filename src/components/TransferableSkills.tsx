import { useMemo, useRef, useState, type FormEvent } from 'react'
import { AlertCircle, ArrowLeftRight, Check, CircleHelp, Compass, LoaderCircle, ShieldCheck, Sparkles } from 'lucide-react'
import '../styles/transferable-skills.css'
import { skillFairFetch } from '../apiClient'

type Evidence = {
  skill_name: string
  evidence_type: 'explicit' | 'inferred'
  detection_method: 'ai' | 'local'
  validation_status: 'evidence_found' | 'inference_unverified'
  supporting_excerpt: string
  relationship_type: 'exact_name_match' | 'curated_adjacent_capability'
  explanation: string
}
type ResultItem = {
  target_requirement: string
  status: 'exact_match' | 'potential_transfer' | 'not_evidenced' | 'not_compared'
  candidate_evidence: Evidence[]
  explanation: string
  exploration_prompt: string | null
}
type TransferabilityResponse = {
  source_role: string
  target_role: string
  analysis_linked: boolean
  candidate_comparison_status: 'compared' | 'not_compared'
  results: ResultItem[]
  exploration_prompts: string[]
  disclaimer: string
}
type FailureKind = 'validation' | 'network' | 'expired' | 'service'
type Props = { analysisId: string | null; onAnalysisExpired: () => void }

const STATUS: Record<ResultItem['status'], string> = {
  exact_match: 'Exact name match', potential_transfer: 'Potential transfer',
  not_evidenced: 'Not evidenced in this analysis', not_compared: 'Not compared',
}

function parseRequirements(value: string) {
  return value.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean)
}

function validationMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((item) => item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string' ? item.msg : '').filter(Boolean).join(' ')
  return ''
}

async function requestTransferability(payload: { source_role: string; target_role: string; target_requirements: string[]; analysis_id?: string }): Promise<TransferabilityResponse> {
  let response: Response
  try {
    response = await skillFairFetch('/api/skills/transferability', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    })
  } catch {
    throw Object.assign(new Error('Could not reach the Transferable Skills service. Check that the backend is running and try again.'), { kind: 'network' as FailureKind })
  }
  let body: Partial<TransferabilityResponse> & { detail?: unknown }
  try { body = await response.json() as typeof body } catch {
    throw Object.assign(new Error('The service returned an unreadable response. Please try again.'), { kind: 'service' as FailureKind })
  }
  if (!response.ok) {
    if (response.status === 404 && payload.analysis_id) {
      throw Object.assign(new Error('This Skills Discovery analysis is unknown or has expired. Run Skills Discovery again, then retry.'), { kind: 'expired' as FailureKind })
    }
    const detail = validationMessage(body.detail)
    throw Object.assign(new Error(detail || 'Transferability could not be assessed. Check the role details and try again.'), { kind: response.status === 422 ? 'validation' as FailureKind : 'service' as FailureKind })
  }
  if (typeof body.source_role !== 'string' || typeof body.target_role !== 'string' || typeof body.analysis_linked !== 'boolean' || !Array.isArray(body.results) || !Array.isArray(body.exploration_prompts) || typeof body.disclaimer !== 'string') {
    throw Object.assign(new Error('The service returned an unsupported response. Please try again.'), { kind: 'service' as FailureKind })
  }
  return body as TransferabilityResponse
}

function EvidenceCard({ evidence }: { evidence: Evidence }) {
  const inferred = evidence.evidence_type === 'inferred'
  return <article className="transfer-evidence-card">
    <div className="transfer-evidence-heading"><b>{evidence.skill_name}</b><span className={`transfer-evidence-badge ${inferred ? 'inferred' : 'explicit'}`}>{inferred ? 'Inferred · unverified' : evidence.detection_method === 'local' ? 'Local mention · not proficiency proof' : 'Explicit evidence'}</span></div>
    <p>{evidence.explanation}</p>
    <details><summary>View supporting excerpt</summary><blockquote>“{evidence.supporting_excerpt}”</blockquote></details>
  </article>
}

function ResultGroup({ title, subtitle, items, tone }: { title: string; subtitle: string; items: ResultItem[]; tone: string }) {
  if (!items.length) return null
  return <section className={`transfer-result-group ${tone}`}>
    <div className="transfer-group-heading"><div><h3>{title}</h3><p>{subtitle}</p></div><span>{items.length}</span></div>
    <div className="transfer-result-list">{items.map((item, index) => <article className="transfer-result-card" key={`${item.target_requirement}-${index}`}>
      <header><div><span className="transfer-target-label">TARGET CAPABILITY</span><h4>{item.target_requirement}</h4></div><span className={`transfer-status status-${item.status}`}>{STATUS[item.status]}</span></header>
      <p className="transfer-explanation">{item.explanation}</p>
      {item.candidate_evidence.length > 0 && <div className="transfer-evidence-list">{item.candidate_evidence.map((evidence, evidenceIndex) => <EvidenceCard evidence={evidence} key={`${evidence.skill_name}-${evidenceIndex}`} />)}</div>}
      {item.exploration_prompt && <div className="transfer-prompt"><Compass size={15} /><span><b>Explore with evidence</b>{item.exploration_prompt}</span></div>}
    </article>)}</div>
  </section>
}

export default function TransferableSkills({ analysisId, onAnalysisExpired }: Props) {
  const [sourceRole, setSourceRole] = useState('')
  const [targetRole, setTargetRole] = useState('')
  const [requirementsText, setRequirementsText] = useState('')
  const [result, setResult] = useState<TransferabilityResponse | null>(null)
  const [error, setError] = useState<{ kind: FailureKind; message: string } | null>(null)
  const [validation, setValidation] = useState('')
  const [loading, setLoading] = useState(false)
  const requestInFlight = useRef(false)
  const requirements = useMemo(() => parseRequirements(requirementsText), [requirementsText])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (requestInFlight.current || loading) return
    const source = sourceRole.trim()
    const target = targetRole.trim()
    if (!source || !target) { setValidation('Enter both a source role and a target role.'); return }
    if (source.length > 160 || target.length > 160) { setValidation('Role names must be 160 characters or fewer.'); return }
    if (requirements.length > 60) { setValidation('Enter no more than 60 target requirements.'); return }
    if (requirements.some((item) => item.length > 100)) { setValidation('Each target requirement must be 100 characters or fewer.'); return }
    requestInFlight.current = true
    setLoading(true); setValidation(''); setError(null); setResult(null)
    const payload = { source_role: source, target_role: target, target_requirements: requirements, ...(analysisId ? { analysis_id: analysisId } : {}) }
    try { setResult(await requestTransferability(payload)) }
    catch (reason) { const failure = reason as Error & { kind?: FailureKind }; if (failure.kind === 'expired') onAnalysisExpired(); setError({ kind: failure.kind ?? 'service', message: failure.message || 'Transferability could not be assessed. Please try again.' }) }
    finally { requestInFlight.current = false; setLoading(false) }
  }

  const exact = result?.results.filter((item) => item.status === 'exact_match') ?? []
  const potential = result?.results.filter((item) => item.status === 'potential_transfer') ?? []
  const unresolved = result?.results.filter((item) => item.status === 'not_evidenced' || item.status === 'not_compared') ?? []

  return <div className="transfer-page">
    <header className="transfer-heading"><div><span className="eyebrow"><span className="eyebrow-line" /> SKILLS-FIRST CAREER EXPLORATION</span><h1>Transferable Skills</h1><p>Explore evidence-aware connections between experience and a target role.</p></div><span className="transfer-heading-pill"><ArrowLeftRight size={16} />Deterministic · Human reviewed</span></header>

    <div className="transfer-advisory" role="note"><div><ShieldCheck size={17} /></div><p><b>Advisory exploration, not a qualification decision.</b> A potential transfer does not mean skills are equivalent or prove role qualification. Missing evidence does not prove missing ability. Assess context and evidence with the candidate.</p></div>

    <div className="transfer-layout">
      <form className="transfer-form panel" onSubmit={submit} noValidate>
        <div className="transfer-form-title"><span><ArrowLeftRight size={16} /></span><div><h2>Compare role capabilities</h2><p>Describe the career move you want to explore.</p></div></div>
        <label htmlFor="transfer-source-role">Source / current role</label>
        <input id="transfer-source-role" value={sourceRole} onChange={(event) => { setSourceRole(event.target.value); setValidation('') }} maxLength={160} placeholder="e.g. Operations Analyst" autoComplete="organization-title" aria-describedby="transfer-role-limit" />
        <label htmlFor="transfer-target-role">Target role</label>
        <input id="transfer-target-role" value={targetRole} onChange={(event) => { setTargetRole(event.target.value); setValidation('') }} maxLength={160} placeholder="e.g. Data Reporting Analyst" autoComplete="organization-title" />
        <label htmlFor="transfer-requirements">Target requirements <span>Optional</span></label>
        <textarea id="transfer-requirements" value={requirementsText} onChange={(event) => { setRequirementsText(event.target.value); setValidation('') }} rows={6} placeholder={'One per line or comma-separated\nData reporting\nSQL'} aria-describedby="transfer-requirements-hint" />
        <div className="transfer-field-hint" id="transfer-role-limit">Role titles up to 160 characters. Requirements up to 60 items, 100 characters each.</div>
        <div className={`transfer-analysis-source ${analysisId ? 'linked' : 'general'}`}><span>{analysisId ? <ShieldCheck size={15} /> : <Sparkles size={15} />}</span><div><b>{analysisId ? 'Skills analysis linked' : 'General role exploration'}</b><p>{analysisId ? 'The backend will resolve validated skills and excerpts by analysis reference. No resume or candidate evidence is sent from this page.' : 'No analysis is attached, so the result will contain general prompts only and make no candidate-specific claims.'}</p></div></div>
        <div id="transfer-requirements-hint" className="transfer-field-hint transfer-hint-bottom">{requirements.length} requirement{requirements.length === 1 ? '' : 's'} · Exact labels and a narrow curated relationship set</div>
        {validation && <div className="transfer-message validation" role="alert"><AlertCircle size={15} /><span>{validation}</span></div>}
        {error && <div className={`transfer-message ${error.kind === 'expired' ? 'expired' : 'error'}`} role="alert"><AlertCircle size={15} /><span>{error.message}</span><button type="button" onClick={() => setError(null)}>Dismiss</button></div>}
        <div className="transfer-submit-row"><span><ShieldCheck size={14} />No ranking or demographic inference</span><button className="button-primary" type="submit" disabled={loading}>{loading ? <><LoaderCircle size={15} className="spin-icon" />Exploring…</> : <>Explore transferability <ArrowLeftRight size={15} /></>}</button></div>
      </form>

      <section className="transfer-results" aria-live="polite" aria-busy={loading}>
        {loading ? <div className="panel transfer-state"><div className="transfer-state-icon busy"><LoaderCircle size={22} className="spin-icon" /></div><h2>Reviewing capability relationships</h2><p>Applying deterministic name and curated relationship checks to the submitted role requirements.</p><div className="transfer-progress"><i /></div></div>
          : error ? <div className="panel transfer-state"><div className="transfer-state-icon failed"><AlertCircle size={21} /></div><h2>{error.kind === 'validation' ? 'Check the role details' : error.kind === 'network' ? 'Transferable Skills is unreachable' : error.kind === 'expired' ? 'Analysis unavailable' : 'Transferability could not be assessed'}</h2><p>{error.message}</p><button type="button" className="button-secondary" onClick={() => setError(null)}>Return to the form</button></div>
            : result ? <>
              <div className="panel transfer-summary"><div><span className="eyebrow"><span className="eyebrow-line" /> ROLE EXPLORATION</span><h2>{result.source_role}<span> → </span>{result.target_role}</h2></div><span className={`transfer-summary-source ${result.analysis_linked ? 'linked' : 'general'}`}>{result.analysis_linked ? 'Analysis linked' : 'General exploration'}</span><p>{result.analysis_linked ? 'Candidate-specific comparison uses validated server-side analysis evidence.' : 'No candidate analysis was linked; no candidate-specific comparison was made.'}</p></div>
              {exact.length === 0 && potential.length === 0 && unresolved.length === 0 ? <div className="panel transfer-state transfer-empty"><div className="transfer-state-icon"><Compass size={21} /></div><span className="transfer-empty-label">NO REQUIREMENTS PROVIDED</span><h2>Explore the role relationship</h2><p>The backend returned general prompts for discussing how responsibilities, tools, and work products may relate across these roles.</p></div> : <>
                <ResultGroup title="Exact name matches" subtitle="A shared normalized skill label, not a proficiency assessment." items={exact} tone="exact" />
                <ResultGroup title="Potential transfers" subtitle="Curated adjacent capabilities that require context and human assessment." items={potential} tone="potential" />
                <ResultGroup title={result.analysis_linked ? 'Not evidenced in this analysis' : 'Not compared'} subtitle={result.analysis_linked ? 'The available analysis did not support a match; this is not evidence of inability.' : 'Without an analysis, candidate skills are not assessed.'} items={unresolved} tone="unresolved" />
              </>}
              {result.exploration_prompts.length > 0 && <div className="panel transfer-prompts"><div className="transfer-prompts-heading"><div className="transfer-state-icon"><Compass size={17} /></div><div><h3>Questions for human review</h3><p>Use role responsibilities and candidate examples to explore context.</p></div></div><ul>{result.exploration_prompts.map((prompt, index) => <li key={`${index}-${prompt}`}>{prompt}</li>)}</ul></div>}
              <div className="transfer-disclaimer"><CircleHelp size={16} /><p>{result.disclaimer}</p></div>
            </>
            : <div className="panel transfer-state transfer-empty"><div className="transfer-state-icon"><Compass size={21} /></div><span className="transfer-empty-label">READY WHEN YOU ARE</span><h2>Start with two roles</h2><p>Explore requirements against validated analysis evidence, or compare roles generally without making candidate-specific claims.</p><div className="transfer-empty-footnote"><Check size={14} />Only backend-returned relationships and explanations will be shown.</div></div>}
      </section>
    </div>
  </div>
}
