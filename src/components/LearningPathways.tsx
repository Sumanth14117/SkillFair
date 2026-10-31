import { useRef, useState, type FormEvent } from 'react'
import { AlertCircle, ArrowRight, BookOpenCheck, CheckCircle2, CircleHelp, Clock3, GraduationCap, LoaderCircle, ShieldCheck, Sparkles } from 'lucide-react'
import '../styles/learning-pathways.css'
import { skillFairFetch } from '../apiClient'

type EvidenceStatus = 'explicitly_evidenced' | 'inferred_unverified' | 'not_evidenced_in_analysis' | 'not_compared'
type ActivityType = 'practice_exercise' | 'project_prompt' | 'demonstration'
type LearningActivity = {
  activity_type: ActivityType
  title: string
  description: string
  relevance: string
}
type Pathway = {
  required_skill: string
  evidence_status: EvidenceStatus
  evidenced_skill: string | null
  evidence_type: 'explicit' | 'inferred' | null
  validation_status: 'evidence_found' | 'inference_unverified' | null
  detection_method: 'ai' | 'local' | null
  activities: LearningActivity[]
}
type LearningPathwaysResult = {
  job_title: string
  analysis_linked: boolean
  requirement_count: number
  pathways: Pathway[]
  disclaimer: string
}
type Props = { analysisId: string | null; onAnalysisExpired: () => void; onSkillsDiscovery: () => void }
type FailureKind = 'validation' | 'network' | 'expired' | 'service'

const STATUS_LABELS: Record<EvidenceStatus, string> = {
  explicitly_evidenced: 'Explicitly evidenced',
  inferred_unverified: 'Inferred · unverified',
  not_evidenced_in_analysis: 'Not evidenced in this analysis',
  not_compared: 'Not compared',
}
const STATUS_EXPLANATIONS: Record<EvidenceStatus, string> = {
  explicitly_evidenced: 'The linked analysis contains explicit supporting evidence for this skill.',
  inferred_unverified: 'The skill was inferred from context and has not been verified.',
  not_evidenced_in_analysis: 'The linked analysis did not establish this skill. This does not prove the candidate lacks it.',
  not_compared: 'No candidate analysis was linked, so candidate skills were not compared.',
}
const ACTIVITY_LABELS: Record<ActivityType, string> = {
  practice_exercise: 'Practice exercise',
  project_prompt: 'Project prompt',
  demonstration: 'Demonstration',
}

function parseSkills(value: string) {
  return value.split(/[\n,]+/).map((skill) => skill.trim()).filter(Boolean)
}

function validationMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((item) => item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string' ? item.msg : '')
      .filter(Boolean).join(' ')
  }
  return ''
}

async function requestPathways(payload: { analysis_id?: string; job_title: string; required_skills: string[] }): Promise<LearningPathwaysResult> {
  let response: Response
  try {
    response = await skillFairFetch('/api/learning/pathways', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch {
    throw Object.assign(new Error('Could not reach the Learning Pathways service. Check that the backend is running and try again.'), { kind: 'network' as FailureKind })
  }

  let body: Partial<LearningPathwaysResult> & { detail?: unknown }
  try {
    body = await response.json() as typeof body
  } catch {
    throw Object.assign(new Error('The service returned an unreadable response. Please try again.'), { kind: 'service' as FailureKind })
  }
  if (!response.ok) {
    if (response.status === 404 && payload.analysis_id) {
      throw Object.assign(new Error('This Skills Discovery analysis is unknown or has expired. Run Skills Discovery again, then return here and retry.'), { kind: 'expired' as FailureKind })
    }
    const detail = validationMessage(body.detail)
    throw Object.assign(new Error(detail || 'Learning Pathways could not be generated. Check the fields and try again.'), {
      kind: response.status === 422 ? 'validation' as FailureKind : 'service' as FailureKind,
    })
  }
  if (!Array.isArray(body.pathways) || typeof body.requirement_count !== 'number' || typeof body.analysis_linked !== 'boolean' || typeof body.disclaimer !== 'string') {
    throw Object.assign(new Error('The service returned an unsupported response. Please try again.'), { kind: 'service' as FailureKind })
  }
  return body as LearningPathwaysResult
}

function PathwayCard({ pathway }: { pathway: Pathway }) {
  const activityCount = pathway.activities.length
  return <article className="pathway-skill-card">
    <header className="pathway-skill-header"><div><span className="pathway-section-label">REQUIRED SKILL</span><h3>{pathway.required_skill}</h3></div><span className={`pathway-evidence-status status-${pathway.evidence_status}`}><i />{STATUS_LABELS[pathway.evidence_status]}</span></header>
    <p className="pathway-status-explanation">{STATUS_EXPLANATIONS[pathway.evidence_status]}</p>
    {pathway.evidenced_skill && <div className={`pathway-evidence-link ${pathway.evidence_type === 'inferred' ? 'inferred' : ''}`}><ShieldCheck size={14} /><span>Analysis skill: <b>{pathway.evidenced_skill}</b></span><small>{pathway.detection_method === 'local' ? 'Local mention · not proficiency proof' : pathway.validation_status === 'inference_unverified' ? 'Unverified' : 'Evidence found'}</small></div>}
    <div className="pathway-activities-heading"><div><h4>Suggested activities</h4><p>Optional ways to practice and demonstrate this skill</p></div><span>{activityCount}</span></div>
    {activityCount ? <div className="pathway-activities">{pathway.activities.map((activity, index) => <section className="pathway-activity" key={`${activity.activity_type}-${index}`}>
      <div className="pathway-activity-top"><span className="pathway-activity-icon">{activity.activity_type === 'demonstration' ? <CheckCircle2 size={15} /> : activity.activity_type === 'project_prompt' ? <BookOpenCheck size={15} /> : <Clock3 size={15} />}</span><span className="pathway-activity-type">{ACTIVITY_LABELS[activity.activity_type]}</span></div>
      <h5>{activity.title}</h5><p className="pathway-activity-description">{activity.description}</p>
      <div className="pathway-relevance"><CircleHelp size={14} /><p><b>Why it’s relevant</b>{activity.relevance}</p></div>
    </section>)}</div> : <div className="pathway-no-activities"><CheckCircle2 size={15} />No additional activities were returned for this skill.</div>}
  </article>
}

export default function LearningPathways({ analysisId, onAnalysisExpired, onSkillsDiscovery }: Props) {
  const [jobTitle, setJobTitle] = useState('')
  const [skillsText, setSkillsText] = useState('')
  const [result, setResult] = useState<LearningPathwaysResult | null>(null)
  const [error, setError] = useState<{ kind: FailureKind; message: string } | null>(null)
  const [validation, setValidation] = useState('')
  const [loading, setLoading] = useState(false)
  const requestInFlight = useRef(false)
  const skills = parseSkills(skillsText)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (requestInFlight.current || loading) return
    const title = jobTitle.trim()
    const requestedSkills = parseSkills(skillsText)
    if (!title) { setValidation('Enter a target job title.'); return }
    if (title.length > 160) { setValidation('Job title must be 160 characters or fewer.'); return }
    if (!requestedSkills.length) { setValidation('Enter at least one essential skill.'); return }
    if (requestedSkills.length > 60) { setValidation('Enter no more than 60 required skills.'); return }
    if (requestedSkills.some((skill) => skill.length > 100)) { setValidation('Each skill must be 100 characters or fewer.'); return }

    requestInFlight.current = true
    setLoading(true)
    setError(null)
    setValidation('')
    setResult(null)
    try {
      const payload = {
        ...(analysisId ? { analysis_id: analysisId } : {}),
        job_title: title,
        required_skills: requestedSkills,
      }
      setResult(await requestPathways(payload))
    } catch (reason) {
      const failure = reason as Error & { kind?: FailureKind }
      if (failure.kind === 'expired') onAnalysisExpired()
      setError({ kind: failure.kind ?? 'service', message: failure.message || 'Learning Pathways could not be generated. Please try again.' })
    } finally {
      requestInFlight.current = false
      setLoading(false)
    }
  }

  return <div className="learning-page">
    <header className="learning-heading"><div><span className="eyebrow"><span className="eyebrow-line" /> SKILL DEVELOPMENT PLANNER</span><h1>Learning Pathways</h1><p>Find practical ways to build and demonstrate skills for a target role.</p></div><span className="learning-heading-pill"><GraduationCap size={16} />Deterministic suggestions</span></header>
    <aside className="learning-advisory" role="note"><div><ShieldCheck size={18} /></div><p><b>Suggestions are advisory.</b> Activities are practice prompts, not verified courses or credentials. They do not prove competence, guarantee employability, or confirm that training is completed.</p></aside>

    <div className="learning-layout">
      <form className="panel learning-form" onSubmit={handleSubmit} noValidate aria-busy={loading}>
        <div className="learning-form-title"><span>01</span><div><h2>Choose a target role</h2><p>Enter the skills that matter for this opportunity.</p></div></div>
        <label htmlFor="learning-job-title">Target job title</label>
        <input id="learning-job-title" name="job_title" value={jobTitle} onChange={(event) => { setJobTitle(event.target.value); setValidation('') }} maxLength={160} placeholder="e.g. Data Engineer" autoComplete="organization-title" />
        <label htmlFor="learning-required-skills">Essential skills <span>Comma or line separated</span></label>
        <textarea id="learning-required-skills" name="required_skills" value={skillsText} onChange={(event) => { setSkillsText(event.target.value); setValidation('') }} rows={7} placeholder={'e.g.\nPython\nData modeling\nCloud platforms'} aria-describedby="learning-skills-hint" />
        <div className="learning-skills-hint" id="learning-skills-hint"><CircleHelp size={14} />{skills.length} of 60 skills · Each skill is limited to 100 characters.</div>
        <div className={`learning-analysis-source ${analysisId ? 'linked' : 'general'}`}><span>{analysisId ? <ShieldCheck size={16} /> : <Sparkles size={16} />}</span><div><b>{analysisId ? 'Candidate analysis will be used' : 'General recommendations'}</b><p>{analysisId ? 'Validated skills are resolved privately by the backend. Resume text and skill evidence are not sent from this page.' : 'No candidate analysis is attached. Suggestions will not be compared with candidate skills.'}</p></div></div>
        {validation && <div className="learning-message validation" role="alert"><AlertCircle size={16} />{validation}</div>}
        {error && <div className={`learning-message ${error.kind === 'expired' ? 'expired' : 'error'}`} role="alert"><AlertCircle size={16} /><span>{error.message}</span>{error.kind === 'expired' && <button type="button" onClick={onSkillsDiscovery}>Go to Skills Discovery <ArrowRight size={13} /></button>}</div>}
        <div className="learning-submit-row"><span><ShieldCheck size={14} />No resume is uploaded</span><button type="submit" className="button-primary" disabled={loading}>{loading ? <><LoaderCircle size={15} className="spin-icon" />Building pathways…</> : <><Sparkles size={15} />Generate pathways</>}</button></div>
      </form>

      <section className="learning-results" aria-live="polite" aria-busy={loading}>
        {loading ? <div className="panel learning-state"><div className="learning-state-icon busy"><LoaderCircle size={21} className="spin-icon" /></div><h2>Building your pathways</h2><p>Creating deterministic activities for each required skill.</p><div className="learning-progress"><i /></div></div> : error ? <div className="panel learning-state"><div className="learning-state-icon failed"><AlertCircle size={21} /></div><h2>{error.kind === 'expired' ? 'Analysis needs refreshing' : error.kind === 'validation' ? 'Check the role details' : 'Pathways unavailable'}</h2><p>{error.message}</p>{error.kind === 'expired' && <button type="button" className="button-secondary" onClick={onSkillsDiscovery}>Run Skills Discovery again</button>}</div> : result ? result.pathways.length ? <>
          <div className="learning-result-summary panel"><div><span className="eyebrow"><span className="eyebrow-line" /> PATHWAY OVERVIEW</span><h2>{result.job_title}</h2></div><div className="learning-summary-count"><b>{result.requirement_count}</b><span>essential skills</span></div><div className={`learning-result-source ${result.analysis_linked ? 'linked' : 'general'}`}><span>{result.analysis_linked ? <ShieldCheck size={14} /> : <Sparkles size={14} />}</span>{result.analysis_linked ? 'Compared with linked analysis' : 'Not compared with candidate analysis'}</div><p className="learning-result-disclaimer">{result.disclaimer}</p></div>
          <div className="learning-pathway-list">{result.pathways.map((pathway, index) => <PathwayCard key={`${pathway.required_skill}-${index}`} pathway={pathway} />)}</div>
        </> : <div className="panel learning-state"><div className="learning-state-icon"><CircleHelp size={21} /></div><h2>No pathways returned</h2><p>No required skills were returned. Review the role requirements and generate pathways again.</p></div> : <div className="panel learning-state learning-empty"><div className="learning-state-icon"><GraduationCap size={22} /></div><span className="learning-empty-label">READY WHEN YOU ARE</span><h2>Start with the skills to build</h2><p>Enter a role and its essential skills. SkillFair will create focused practice, project, and demonstration ideas for each one.</p><div className="learning-empty-tags"><span>Practice exercises</span><span>Project prompts</span><span>Ways to demonstrate</span></div><small>Recommendations are generated from deterministic templates.</small></div>}
      </section>
    </div>
  </div>
}
