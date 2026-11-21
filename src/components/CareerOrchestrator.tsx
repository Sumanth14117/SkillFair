import { useEffect, useRef, useState, type FormEvent } from 'react'
import { AlertCircle, AlertTriangle, ArrowRight, Check, CheckCircle2, Compass, FileCheck2, Globe2, LoaderCircle, MapPin, ShieldCheck, Sparkles, UsersRound, Workflow } from 'lucide-react'
import { isValidSessionLocator } from '../sessionState'
import { getRecruiterReviewViewState } from '../recruiterReview'
import { skillFairFetch } from '../apiClient'
import { restoreSavedWorkflow } from '../workflowRestoration'
import { loadWorkflowHistory, type WorkflowHistoryItem, type WorkflowHistoryState } from '../workflowHistory'
import WorkflowHistory, { type WorkflowHistoryAction } from './WorkflowHistory'
import '../styles/career-orchestrator.css'

type StageStatus = 'complete' | 'unavailable' | 'needs_human_review' | 'blocked_pending_review' | 'stopped' | 'error'
type StageName = 'skills_discovery' | 'market_intelligence' | 'job_audit' | 'inclusive_matching' | 'human_review' | 'learning_pathways'
type Stage = { stage: StageName; status: StageStatus; summary: string }
type PassportSkill = { skill_name: string; evidence_type: 'explicit' | 'inferred'; validation_status: 'evidence_found' | 'inference_unverified'; detection_method: 'ai' | 'local' }
type Passport = { analysis_id: string; analysis_mode: 'ai' | 'local' | 'development'; skill_count: number; explicit_count: number; inferred_count: number; skills: PassportSkill[] }
type MarketSource = { source_id: string; source_name: string; source_type: string; verification_status: 'verified'; retrieved_at: string; published_at: string | null; geography: string | null; source_reference: string }
type MarketInsight = { insight_id: string; category: string; title: string; summary: string; value: number | null; unit: string | null; period: string | null; geography: string | null; source_id: string }
type MarketResult = { job_title: string; geography: string | null; data_availability: 'available' | 'unavailable'; sources: MarketSource[]; insights: MarketInsight[]; message: string }
type AuditFinding = { rule_id: string; category: 'credential_requirement' | 'potentially_exclusionary_wording' | 'vague_requirement' | 'experience_threshold'; priority: 'low' | 'medium' | 'high'; triggering_phrase: string; criterion_importance: 'essential' | 'preferred' | 'unspecified'; explanation: string; suggested_revision_or_action: string; status: 'potential_concern_for_human_review'; human_review_required: true }
type AuditResult = { job_title: string | null; criteria_reviewed: number; finding_count: number; findings: AuditFinding[]; human_review_required: true; disclaimer: string }
type MatchEvidence = { skill_name: string; evidence_type: 'explicit' | 'inferred'; detection_method: 'ai' | 'local'; validation_status: 'evidence_found' | 'inference_unverified'; supporting_excerpt: string; relationship_type: 'exact_name_match' | 'curated_adjacent_capability'; explanation: string }
type MatchRequirement = { skill: string; importance: 'essential' | 'preferred'; status: 'explicitly_evidenced' | 'inferred_unverified' | 'potential_transfer' | 'not_evidenced'; evidence: MatchEvidence[]; explanation: string }
type InclusiveResult = { job_title: string; requirements: MatchRequirement[]; exact_explicit_count: number; exact_inferred_count: number; potential_transfer_count: number; not_evidenced_count: number; bias_audit_findings: AuditFinding[]; human_review_prompts: string[]; human_review_required: true; disclaimer: string }
type Activity = { activity_type: 'practice_exercise' | 'project_prompt' | 'demonstration'; title: string; description: string; relevance: string }
type LearningPathway = { required_skill: string; evidence_status: 'explicitly_evidenced' | 'inferred_unverified' | 'not_evidenced_in_analysis' | 'not_compared'; evidenced_skill: string | null; evidence_type: 'explicit' | 'inferred' | null; validation_status: 'evidence_found' | 'inference_unverified' | null; detection_method: 'ai' | 'local' | null; activities: Activity[] }
type LearningResult = { job_title: string; analysis_linked: boolean; requirement_count: number; pathways: LearningPathway[]; disclaimer: string }
type HumanReview = { status: 'pending' | 'recorded'; decision: 'proceed' | 'revise' | 'stop' | null; reviewable_requirement_ids: string[]; reviewed_requirement_ids: string[]; review_notes: string | null; attested_at: string | null; reviewer_identity_verified: false }
type LearningStage = { status: 'blocked_pending_review' | 'complete' | 'stopped' | 'error'; message: string; result: LearningResult | null }
type ImpactTrackingState = 'idle' | 'recording' | 'recorded' | 'unavailable'
export type CareerWorkflowRun = { run_id: string; started_at: string; workflow_status: 'awaiting_human_review' | 'revise_required' | 'stopped' | 'complete'; stages: Stage[]; skill_passport: Passport; market_intelligence: MarketResult; job_audit: AuditResult; inclusive_matching: InclusiveResult; human_review: HumanReview; learning_pathways: LearningStage; workflow_summary: string; disclaimers: string[] }
export type CareerWorkflowDraft = { jobTitle: string; jobDescription: string; geography: string; essentialSkills: string; preferredSkills: string }
export type CareerOrchestratorProps = { ownerId: string; analysisId: string | null; run: CareerWorkflowRun | null; runId: string | null; onRunChange: (run: CareerWorkflowRun | null) => void; onRunExpired: (runId: string) => void; onAnalysisExpired: () => void; draft: CareerWorkflowDraft; onDraftChange: (draft: CareerWorkflowDraft) => void; onSkillsDiscovery: () => void; mode?: 'orchestrator' | 'review'; onOpenOrchestrator?: () => void }
type ErrorKind = 'validation' | 'network' | 'analysis_expired' | 'run_expired' | 'service' | 'conflict'

const STAGE_LABELS: Record<StageName, string> = {
  skills_discovery: 'Skill Passport', market_intelligence: 'Market Intelligence', job_audit: 'Job Audit',
  inclusive_matching: 'Inclusive Matching', human_review: 'Human Review', learning_pathways: 'Learning Pathways',
}
const STATUS_LABELS: Record<StageStatus, string> = {
  complete: 'Complete', unavailable: 'Unavailable', needs_human_review: 'Review required',
  blocked_pending_review: 'Blocked pending review', stopped: 'Stopped', error: 'Provider error',
}
const FINDING_CATEGORY: Record<AuditFinding['category'], string> = {
  credential_requirement: 'Credential requirement', potentially_exclusionary_wording: 'Potentially exclusionary wording',
  vague_requirement: 'Vague requirement', experience_threshold: 'Experience threshold',
}
const LEARNING_STATUS: Record<LearningPathway['evidence_status'], string> = {
  explicitly_evidenced: 'Explicitly evidenced', inferred_unverified: 'Inferred · unverified',
  not_evidenced_in_analysis: 'Not evidenced in this analysis', not_compared: 'Not compared',
}
const ACTIVITY_LABEL: Record<Activity['activity_type'], string> = {
  practice_exercise: 'Practice exercise', project_prompt: 'Project prompt', demonstration: 'Demonstration',
}

function parseList(value: string) { return value.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean) }
function validationDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((item) => item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string' ? item.msg : '').filter(Boolean).join(' ')
  return ''
}
function errorFor(status: number, detail: unknown, action: 'create' | 'review'): { kind: ErrorKind; message: string } {
  if (status === 404) return action === 'create'
    ? { kind: 'analysis_expired', message: 'This Skills Discovery analysis is unknown or has expired. Run Skills Discovery again, then start a new workflow.' }
    : { kind: 'run_expired', message: 'This workflow run is unknown or has expired. Start a new workflow to continue.' }
  if (status === 409) return { kind: 'conflict', message: validationDetail(detail) || 'A review decision has already been recorded or the review details do not match this run.' }
  if (status === 422) return { kind: 'validation', message: validationDetail(detail) || 'Check the submitted role details and try again.' }
  return { kind: 'service', message: validationDetail(detail) || 'The workflow service could not complete this request. Please try again.' }
}

async function postJson<T>(url: string, payload: unknown, action: 'create' | 'review'): Promise<T> {
  let response: Response
  try {
    response = await skillFairFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
  } catch {
    throw Object.assign(new Error('Could not reach the Career Orchestrator. Check that the backend is running and try again.'), { kind: 'network' as ErrorKind })
  }
  let body: T & { detail?: unknown }
  try { body = await response.json() as T & { detail?: unknown } } catch {
    throw Object.assign(new Error('The workflow service returned an unreadable response. Please try again.'), { kind: 'service' as ErrorKind })
  }
  if (!response.ok) {
    const failure = errorFor(response.status, body.detail, action)
    throw Object.assign(new Error(failure.message), { kind: failure.kind })
  }
  return body
}

export function verifyRun(value: CareerWorkflowRun): CareerWorkflowRun {
  if (!isValidSessionLocator(value.run_id) || !Array.isArray(value.stages) || !value.skill_passport || !value.market_intelligence || !value.job_audit || !value.inclusive_matching || !value.human_review || !value.learning_pathways || typeof value.workflow_summary !== 'string' || !Array.isArray(value.disclaimers)) {
    throw Object.assign(new Error('The service returned an unsupported workflow response. Please try again.'), { kind: 'service' as ErrorKind })
  }
  if (!['pending', 'recorded'].includes(value.human_review.status) || ![null, 'proceed', 'revise', 'stop'].includes(value.human_review.decision) || !['blocked_pending_review', 'complete', 'stopped', 'error'].includes(value.learning_pathways.status)) throw Object.assign(new Error('The service returned invalid workflow review state.'), { kind: 'service' as ErrorKind })
  const stage = (name: StageName) => value.stages.find((item) => item.stage === name)
  if (!stage('learning_pathways') || !stage('human_review')) throw Object.assign(new Error('The service returned an incomplete workflow response. Please try again.'), { kind: 'service' as ErrorKind })
  const pathways = value.learning_pathways.result
  const validPathways = !!pathways && typeof pathways.job_title === 'string' && typeof pathways.analysis_linked === 'boolean' && Number.isInteger(pathways.requirement_count) && pathways.requirement_count > 0 && Array.isArray(pathways.pathways) && pathways.pathways.length === pathways.requirement_count && pathways.pathways.every((item) => typeof item.required_skill === 'string' && Array.isArray(item.activities) && item.activities.every((activity) => typeof activity.title === 'string' && typeof activity.description === 'string' && typeof activity.relevance === 'string')) && typeof pathways.disclaimer === 'string'
  const learningIsComplete = value.workflow_status === 'complete' && value.human_review.status === 'recorded' && value.human_review.decision === 'proceed' && value.learning_pathways.status === 'complete' && validPathways && stage('learning_pathways')?.status === 'complete'
  if (value.learning_pathways.status === 'complete' && !learningIsComplete) throw Object.assign(new Error('The workflow response did not confirm a recorded Proceed decision and completed pathways.'), { kind: 'service' as ErrorKind })
  if (value.human_review.status === 'pending' && (value.learning_pathways.status !== 'blocked_pending_review' || value.learning_pathways.result !== null)) throw Object.assign(new Error('The workflow returned pathways before the required human review.'), { kind: 'service' as ErrorKind })
  if (value.human_review.status === 'recorded' && value.human_review.decision !== 'proceed' && value.learning_pathways.result !== null) throw Object.assign(new Error('The workflow returned learning activities without a Proceed decision.'), { kind: 'service' as ErrorKind })
  return value
}

function StageStatusPill({ status }: { status: StageStatus }) {
  return <span className={`career-stage-status stage-status-${status}`}><i />{STATUS_LABELS[status]}</span>
}

function StageRail({ run }: { run: CareerWorkflowRun }) {
  return <nav className="career-stage-rail" aria-label="Career workflow stages">{run.stages.map((stage, index) => <a href={`#career-${stage.stage}`} className="career-rail-item" key={stage.stage}><span className={`career-rail-marker rail-${stage.status}`}>{stage.status === 'complete' ? <Check size={12} /> : index + 1}</span><span><b>{STAGE_LABELS[stage.stage]}</b><small>{STATUS_LABELS[stage.status]}</small></span></a>)}<a href="#career-summary" className="career-rail-item"><span className="career-rail-marker rail-summary"><FileCheck2 size={12} /></span><span><b>Explainable Summary</b><small>Review workflow</small></span></a></nav>
}

function StageHeader({ id, label, stage }: { id: string; label: string; stage: Stage }) {
  return <div className="career-stage-header" id={id}><div><span className="career-stage-kicker">WORKFLOW STAGE</span><h2>{label}</h2><p>{stage.summary}</p></div><StageStatusPill status={stage.status} /></div>
}

function SkillPassportStage({ run, stage }: { run: CareerWorkflowRun; stage: Stage }) {
  const passport = run.skill_passport
  const passportSummary = passport.analysis_mode === 'local'
    ? `${passport.skill_count} locally detected mentions · not proof of proficiency`
    : passport.analysis_mode === 'development'
      ? 'Text extraction only · no skills analysis was performed'
      : `${passport.skill_count} AI analyzed records · review supporting evidence`
  return <section className="panel career-stage-card"><StageHeader id="career-skills_discovery" label="Skill Passport" stage={stage} />
    <div className="career-passport-summary"><div className="career-passport-symbol"><ShieldCheck size={20} /></div><div><b>{passportSummary}</b><span>{passport.explicit_count} explicit · {passport.inferred_count} inferred and unverified</span></div><small>Analysis reference · {passport.analysis_id.slice(0, 8)}…</small></div>
    {passport.skills.length ? <div className="career-passport-skills">{passport.skills.map((skill, index) => <span key={`${skill.skill_name}-${index}`} className={`passport-skill ${skill.evidence_type}`}><b>{skill.skill_name}</b><small>{skill.detection_method === 'local' ? 'Local exact mention · not proficiency proof' : skill.evidence_type === 'explicit' ? 'AI · explicit evidence' : 'AI · inferred, unverified'}</small></span>)}</div> : <div className="career-inline-empty">{passport.analysis_mode === 'local' ? 'The local scan found no vocabulary terms that met its context rules. This does not mean the resume contains no skills.' : passport.analysis_mode === 'development' ? 'Text was extracted, but no skill analysis was performed.' : 'No skill records were returned for this analysis.'}</div>}
    <p className="career-small-note">This summary omits resume text and excerpts. Evidence excerpts appear only beside matching requirements where returned by the backend.</p>
  </section>
}

function MarketStage({ run, stage }: { run: CareerWorkflowRun; stage: Stage }) {
  const market = run.market_intelligence
  return <section className="panel career-stage-card"><StageHeader id="career-market_intelligence" label="Market Intelligence" stage={stage} />
    <div className={`career-market-availability ${market.data_availability}`}><Globe2 size={17} /><div><b>{market.data_availability === 'available' ? 'Verified market information returned' : 'Market data unavailable'}</b><span>{market.message}</span></div>{market.geography && <small><MapPin size={12} />{market.geography}</small>}</div>
    {market.data_availability === 'available' && market.insights.length > 0 ? <div className="career-market-insights">{market.insights.map((insight) => { const source = market.sources.find((item) => item.source_id === insight.source_id); return <article key={insight.insight_id}><span>{insight.category}</span><h3>{insight.title}</h3><p>{insight.summary}</p>{insight.value !== null && <b className="career-market-value">{insight.value}{insight.unit ? ` ${insight.unit}` : ''}</b>}{source && <small>Source: {source.source_name} · Retrieved {new Date(source.retrieved_at).toLocaleString()}</small>}</article> })}</div> : <div className="career-inline-empty">No market statistics or trends are shown because no verified insights were returned.</div>}
    {market.sources.length > 0 && <details className="career-market-sources"><summary>View source provenance ({market.sources.length})</summary>{market.sources.map((source) => <div key={source.source_id}><b>{source.source_name}</b><span>{source.source_type} · {source.verification_status}</span><span>Retrieved {new Date(source.retrieved_at).toLocaleString()}{source.published_at ? ` · Published ${new Date(source.published_at).toLocaleDateString()}` : ''}</span><span>Source reference: {source.source_reference}</span></div>)}</details>}
  </section>
}

function AuditStage({ run, stage }: { run: CareerWorkflowRun; stage: Stage }) {
  const audit = run.job_audit
  return <section className="panel career-stage-card"><StageHeader id="career-job_audit" label="Job Audit" stage={stage} />
    <div className="career-stage-count"><b>{audit.finding_count}</b><span>advisory finding{audit.finding_count === 1 ? '' : 's'} · {audit.criteria_reviewed} criteria reviewed</span></div>
    {audit.findings.length ? <div className="career-finding-list">{audit.findings.map((finding, index) => <article key={`${finding.rule_id}-${index}`}><header><span className={`career-priority priority-${finding.priority}`}>{finding.priority}</span><b>{FINDING_CATEGORY[finding.category]}</b><small>{finding.criterion_importance}</small></header>{finding.triggering_phrase && <blockquote>“{finding.triggering_phrase}”</blockquote>}<p>{finding.explanation}</p><div><b>Suggested action</b><span>{finding.suggested_revision_or_action}</span></div><small><AlertTriangle size={12} />Potential concern · Human review required</small></article>)}</div> : <div className="career-inline-empty">No potential concerns were returned by the deterministic audit rules. This does not establish fairness or legal compliance.</div>}
    <p className="career-disclaimer-inline">{audit.disclaimer}</p>
  </section>
}

function InclusiveStage({ run, stage }: { run: CareerWorkflowRun; stage: Stage }) {
  const matching = run.inclusive_matching
  const essential = matching.requirements.filter((item) => item.importance === 'essential')
  const preferred = matching.requirements.filter((item) => item.importance === 'preferred')
  function requirementCard(item: MatchRequirement, index: number) {
    return <article className="career-requirement" key={`${item.skill}-${index}`}><header><div><span>{item.importance.toUpperCase()} REQUIREMENT</span><h3>{item.skill}</h3></div><span className={`career-evidence-status evidence-${item.status}`}>{item.status === 'explicitly_evidenced' ? 'Explicitly evidenced' : item.status === 'inferred_unverified' ? 'Inferred · unverified' : item.status === 'potential_transfer' ? 'Potential transfer' : 'Not evidenced'}</span></header><p>{item.explanation}</p>{item.evidence.map((evidence, eIndex) => <div className={`career-match-evidence ${evidence.evidence_type}`} key={`${evidence.skill_name}-${eIndex}`}><div><b>{evidence.skill_name}</b><span>{evidence.evidence_type === 'inferred' ? 'Inferred · unverified' : evidence.detection_method === 'local' ? 'Local mention · not proficiency proof' : 'AI · explicit evidence'} · {evidence.relationship_type === 'exact_name_match' ? 'Exact name match' : 'Adjacent capability'}</span></div><p>{evidence.explanation}</p><details><summary>View supporting excerpt</summary><blockquote>“{evidence.supporting_excerpt}”</blockquote></details></div>)}</article>
  }
  return <section className="panel career-stage-card"><StageHeader id="career-inclusive_matching" label="Inclusive Matching" stage={stage} />
    <div className="career-counts"><div><b>{matching.exact_explicit_count}</b><span>Explicit</span></div><div><b>{matching.exact_inferred_count}</b><span>Inferred · unverified</span></div><div><b>{matching.potential_transfer_count}</b><span>Potential transfers</span></div><div><b>{matching.not_evidenced_count}</b><span>Not evidenced</span></div></div>
    <div className="career-requirement-group"><h3>Essential skills <span>{essential.length}</span></h3>{essential.length ? essential.map(requirementCard) : <div className="career-inline-empty">No essential skill requirements returned.</div>}</div>
    <div className="career-requirement-group"><h3>Preferred skills <span>{preferred.length}</span></h3>{preferred.length ? preferred.map(requirementCard) : <div className="career-inline-empty">No preferred skill requirements returned.</div>}</div>
    {matching.human_review_prompts.length > 0 && <div className="career-review-prompts"><h3>Human-review prompts</h3><ul>{matching.human_review_prompts.map((prompt, index) => <li key={`${index}-${prompt}`}>{prompt}</li>)}</ul></div>}
    <p className="career-disclaimer-inline">{matching.disclaimer}</p>
  </section>
}

function HumanReviewStage({ run, onSubmit, loading, selected, onToggle, notes, onNotesChange, error, impactTracking }: {
  run: CareerWorkflowRun; onSubmit: (decision: 'proceed' | 'revise' | 'stop') => void; loading: boolean; selected: string[]; onToggle: (id: string) => void; notes: string; onNotesChange: (notes: string) => void; error: string; impactTracking: ImpactTrackingState
}) {
  const pending = run.human_review.status === 'pending'
  const allReviewed = run.human_review.reviewable_requirement_ids.length > 0 && selected.length === run.human_review.reviewable_requirement_ids.length
  const requirements = run.inclusive_matching.requirements
  return <section className="panel career-stage-card career-human-stage"><StageHeader id="career-human_review" label="Human Review" stage={run.stages.find((item) => item.stage === 'human_review')!} />
    <div className="career-attestation"><ShieldCheck size={17} /><p><b>Review is a required decision gate.</b> Your action records an attestation that you reviewed the criteria; it does not verify your recruiter identity. SkillFair does not approve candidates or prove a process is fair.</p></div>
    <p className="career-impact-tracking" role="status" aria-live="polite">{impactTracking === 'recorded' ? 'Review start is recorded with this account’s workflow for aggregate measurement. Sign-in is not independent recruiter identity verification.' : impactTracking === 'recording' ? 'Recording the review start with the persistent workflow…' : impactTracking === 'unavailable' ? 'Review can continue, but its start timestamp may not be available for duration measurement.' : 'Review timing is derived from persistent workflow timestamps for account-level reporting.'}</p>
    <h3 className="career-review-list-title">Reviewed requirement identifiers</h3><p className="career-review-help">Review each requirement and mark its identifier before choosing Proceed, Revise, or Stop.</p>
    <div className="career-review-items">{run.human_review.reviewable_requirement_ids.map((id, index) => { const item = requirements[index]; const checked = pending ? selected.includes(id) : run.human_review.reviewed_requirement_ids.includes(id); return <label className={`career-review-item ${checked ? 'checked' : ''}`} key={id}><input type="checkbox" checked={checked} disabled={!pending || loading} onChange={() => onToggle(id)} /><span className="career-review-id">{id}</span><span className="career-review-requirement">{item ? item.skill : 'Requirement'}<small>{item?.importance ?? 'role criteria'} · {item ? (item.status === 'not_evidenced' ? 'missing evidence is inconclusive' : item.status.replaceAll('_', ' ')) : ''}</small></span>{checked && <CheckCircle2 size={16} />}</label> })}</div>
    {pending ? <><label className="career-review-notes-label" htmlFor="career-review-notes">Review notes <span>Optional · not persisted beyond this workflow run</span></label><textarea id="career-review-notes" rows={3} maxLength={1000} value={notes} onChange={(event) => onNotesChange(event.target.value)} placeholder="Add concise notes about the role criteria or evidence reviewed…" />{error && <div className="career-error-inline" role="alert"><AlertCircle size={15} />{error}</div>}<div className="career-review-actions"><span>{selected.length} of {run.human_review.reviewable_requirement_ids.length} requirements reviewed</span><div><button type="button" className="button-secondary" disabled={loading || !allReviewed} onClick={() => onSubmit('revise')}> {loading ? 'Saving…' : 'Revise'}</button><button type="button" className="career-stop-button" disabled={loading || !allReviewed} onClick={() => onSubmit('stop')}>{loading ? 'Saving…' : 'Stop'}</button><button type="button" className="button-primary" disabled={loading || !allReviewed} onClick={() => onSubmit('proceed')}>{loading ? <><LoaderCircle size={14} className="spin-icon" />Recording…</> : <>Proceed to Learning Pathways <ArrowRight size={14} /></>}</button></div></div></> : <div className="career-review-recorded"><CheckCircle2 size={16} /><span>Decision recorded: <b>{run.human_review.decision}</b>{run.human_review.attested_at ? ` · ${new Date(run.human_review.attested_at).toLocaleString()}` : ''}{run.human_review.review_notes ? <small>{run.human_review.review_notes}</small> : null}</span></div>}
    <p className="career-disclaimer-inline">Review attestation is not recruiter identity verification or a hiring decision.</p>
  </section>
}

function LearningStage({ stage }: { stage: LearningStage }) {
  return <section className="panel career-stage-card"><div className="career-stage-header" id="career-learning_pathways"><div><span className="career-stage-kicker">WORKFLOW STAGE</span><h2>Learning Pathways</h2><p>{stage.message}</p></div><span className={`career-stage-status stage-status-${stage.status}`}><i />{stage.status === 'complete' ? 'Complete' : stage.status === 'stopped' ? 'Stopped' : stage.status === 'error' ? 'Error' : 'Blocked pending review'}</span></div>
    {stage.status === 'complete' && stage.result ? <><div className="career-learning-summary"><b>{stage.result.job_title}</b><span>{stage.result.requirement_count} requirements · Activities are advisory, not qualifications</span></div><div className="career-learning-pathways">{stage.result.pathways.map((pathway, index) => <article key={`${pathway.required_skill}-${index}`}><header><div><span>REQUIREMENT</span><h3>{pathway.required_skill}</h3></div><span className={`career-learning-evidence learning-${pathway.evidence_status}`}>{LEARNING_STATUS[pathway.evidence_status]}</span></header>{pathway.evidenced_skill && <p className="career-learning-linked">Analysis skill: <b>{pathway.evidenced_skill}</b> · {pathway.detection_method === 'local' ? 'local mention, not proficiency proof' : pathway.validation_status === 'inference_unverified' ? 'unverified inference' : 'evidence found'}</p>}{pathway.activities.map((activity, aIndex) => <div className="career-learning-activity" key={`${activity.title}-${aIndex}`}><span>{ACTIVITY_LABEL[activity.activity_type]}</span><h4>{activity.title}</h4><p>{activity.description}</p><small>{activity.relevance}</small></div>)}</article>)}</div><p className="career-disclaimer-inline">{stage.result.disclaimer}</p></> : <div className={`career-learning-locked ${stage.status === 'stopped' ? 'stopped' : ''}`}><div>{stage.status === 'stopped' ? <CheckCircle2 size={18} /> : <ShieldCheck size={18} />}</div><b>{stage.status === 'stopped' ? 'Workflow stopped' : stage.status === 'error' ? 'Learning Pathways could not be generated' : 'Learning activities are held for human review'}</b><p>{stage.message}</p></div>}
  </section>
}

function WorkflowRunView({ run, selected, onToggle, onReview, loading, notes, onNotesChange, error, onStartNew, onEditCriteria, reviewMode, impactTracking }: {
  run: CareerWorkflowRun; selected: string[]; onToggle: (id: string) => void; onReview: (decision: 'proceed' | 'revise' | 'stop') => void; loading: boolean; notes: string; onNotesChange: (notes: string) => void; error: string; onStartNew: () => void; onEditCriteria: () => void; reviewMode: boolean; impactTracking: ImpactTrackingState
}) {
  const stage = (name: StageName) => run.stages.find((item) => item.stage === name)!
  return <div className="career-run-view"><StageRail run={run} />
    <div className="career-run-content"><div className="career-run-meta"><span>Run started {new Date(run.started_at).toLocaleString()}</span><span className={`career-workflow-state state-${run.workflow_status}`}>{run.workflow_status.replaceAll('_', ' ')}</span></div>
      <SkillPassportStage run={run} stage={stage('skills_discovery')} />
      <MarketStage run={run} stage={stage('market_intelligence')} />
      <AuditStage run={run} stage={stage('job_audit')} />
      <InclusiveStage run={run} stage={stage('inclusive_matching')} />
      <HumanReviewStage run={run} onSubmit={onReview} loading={loading} selected={selected} onToggle={onToggle} notes={notes} onNotesChange={onNotesChange} error={error} impactTracking={impactTracking} />
      <LearningStage stage={run.learning_pathways} />
      <section className="panel career-explainable-summary" id="career-summary"><div className="career-summary-title"><div><FileCheck2 size={18} /></div><span><small>FINAL WORKFLOW SUMMARY</small><h2>Explainable Summary</h2></span></div><p>{run.workflow_summary}</p><ul>{run.disclaimers.map((disclaimer, index) => <li key={`${index}-${disclaimer}`}>{disclaimer}</li>)}</ul><div className="career-summary-footer"><span>Workflow ID · {run.run_id}</span>{!reviewMode && <button type="button" className="button-secondary" onClick={onStartNew}>Start a new workflow</button>}{run.workflow_status === 'revise_required' && <button type="button" className="button-primary" onClick={onEditCriteria}>{reviewMode ? 'Revise in Career Orchestrator' : 'Revise role criteria'}</button>}</div></section>
    </div>
  </div>
}

export default function CareerOrchestrator({ ownerId, analysisId, run, runId, onRunChange, onRunExpired, onAnalysisExpired, draft, onDraftChange, onSkillsDiscovery, mode = 'orchestrator', onOpenOrchestrator }: CareerOrchestratorProps) {
  const [editing, setEditing] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [reviewNotes, setReviewNotes] = useState('')
  const [loading, setLoading] = useState<'create' | 'proceed' | 'revise' | 'stop' | null>(null)
  const [error, setError] = useState<{ kind: ErrorKind; message: string } | null>(null)
  const [impactTracking, setImpactTracking] = useState<ImpactTrackingState>('idle')
  const [restoreState, setRestoreState] = useState<'idle' | 'loading' | 'error'>(runId ? 'loading' : 'idle')
  const inFlight = useRef(false)
  const ownerIdRef = useRef(ownerId)
  ownerIdRef.current = ownerId
  const [historyState, setHistoryState] = useState<WorkflowHistoryState>({ status: 'loading' })
  const [historyRefresh, setHistoryRefresh] = useState(0)
  const [historyAction, setHistoryAction] = useState<WorkflowHistoryAction>(null)
  const historyActionController = useRef<AbortController | null>(null)
  const essentialSkills = parseList(draft.essentialSkills)
  const preferredSkills = parseList(draft.preferredSkills)
  const reviewMode = mode === 'review'
  const reviewViewState = getRecruiterReviewViewState(run, runId, restoreState)
  const showForm = !reviewMode && (!run || editing)
  const workflowVisible = (reviewMode && reviewViewState === 'workflow') || (!reviewMode && Boolean(run) && restoreState === 'idle' && (!runId || run?.run_id === runId))
  const currentRunId = run?.run_id
  const currentReviewStatus = run?.human_review.status

  useEffect(() => {
    if (!workflowVisible || !currentRunId || currentReviewStatus !== 'pending') return
    const controller = new AbortController()
    setImpactTracking('recording')
    skillFairFetch(`/api/impact/reviews/${encodeURIComponent(currentRunId)}/start`, { method: 'POST', signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('measurement unavailable')
        setImpactTracking('recorded')
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setImpactTracking('unavailable')
      })
    return () => controller.abort()
  }, [workflowVisible, currentRunId, currentReviewStatus])

  async function restoreRun(id: string) {
    setRestoreState('loading')
    setError(null)
    try {
      let response: Response
      try { response = await skillFairFetch(`/api/career/orchestrator/runs/${encodeURIComponent(id)}`) }
      catch { throw Object.assign(new Error('Could not reach the Career Orchestrator. Your saved workflow reference is retained; retry when the backend is available.'), { kind: 'network' as ErrorKind }) }
      let body: CareerWorkflowRun & { detail?: unknown }
      try { body = await response.json() as typeof body }
      catch { throw Object.assign(new Error('The workflow service returned an unreadable response. Please retry restoration.'), { kind: 'service' as ErrorKind }) }
      if (response.status === 404) {
        onRunExpired(id)
        onRunChange(null)
        setRestoreState('error')
        setError({ kind: 'run_expired', message: 'This workflow is no longer available. It may have expired, been evicted, or the backend may have restarted. Your role draft is preserved; start a new workflow after confirming the analysis is available.' })
        return
      }
      if (!response.ok) {
        const failure = errorFor(response.status, body.detail, 'review')
        throw Object.assign(new Error(failure.message), { kind: failure.kind })
      }
      const verified = verifyRun(body)
      if (verified.run_id !== id) throw Object.assign(new Error('The workflow service returned a different run reference.'), { kind: 'service' as ErrorKind })
      onRunChange(verified)
      setSelected([])
      setReviewNotes('')
      setRestoreState('idle')
    } catch (reason) {
      const failure = reason as Error & { kind?: ErrorKind }
      setRestoreState('error')
      setError({ kind: failure.kind ?? 'service', message: failure.message || 'Workflow restoration failed. Please retry.' })
    }
  }

  useEffect(() => {
    if (!runId) {
      setRestoreState('idle')
      return
    }
    if (run?.run_id === runId) {
      setRestoreState('idle')
      setSelected([])
      setReviewNotes('')
      return
    }
    void restoreRun(runId)
    // Restore only from the server; the browser stores the locator, never a workflow body.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, run?.run_id])

  useEffect(() => {
    if (reviewMode) return
    const expectedOwnerId = ownerId
    const controller = new AbortController()
    let active = true
    setHistoryState({ status: 'loading' })
    void loadWorkflowHistory(expectedOwnerId, () => ownerIdRef.current, skillFairFetch, controller.signal)
      .then((result) => { if (active && result) setHistoryState(result) })
      .catch(() => {
        if (active && !controller.signal.aborted && ownerIdRef.current === expectedOwnerId) {
          setHistoryState({ status: 'error', message: 'Saved workflows could not be loaded. Check your connection and retry.' })
        }
      })
    return () => { active = false; controller.abort() }
  }, [ownerId, reviewMode, historyRefresh])

  useEffect(() => () => { historyActionController.current?.abort() }, [])

  function updateDraft(key: keyof CareerWorkflowDraft, value: string) {
    onDraftChange({ ...draft, [key]: value })
    setError(null)
  }

  async function createRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (inFlight.current || loading) return
    if (!analysisId) { setError({ kind: 'analysis_expired', message: 'Complete Skills Discovery first; the orchestrator requires its analysis reference.' }); return }
    if (!draft.jobTitle.trim() || draft.jobTitle.trim().length > 160) { setError({ kind: 'validation', message: 'Enter a job title of 1 to 160 characters.' }); return }
    if (!draft.jobDescription.trim() || draft.jobDescription.trim().length > 10000) { setError({ kind: 'validation', message: 'Enter a job description of 1 to 10,000 characters.' }); return }
    if (draft.geography.trim().length > 120) { setError({ kind: 'validation', message: 'Geography must be 120 characters or fewer.' }); return }
    if (!essentialSkills.length && !preferredSkills.length) { setError({ kind: 'validation', message: 'Provide at least one essential or preferred skill.' }); return }
    if (essentialSkills.length + preferredSkills.length > 60) { setError({ kind: 'validation', message: 'Essential and preferred skills must contain no more than 60 items combined.' }); return }
    if ([...essentialSkills, ...preferredSkills].some((skill) => skill.length > 100)) { setError({ kind: 'validation', message: 'Each skill must be 100 characters or fewer.' }); return }
    inFlight.current = true; setLoading('create'); setError(null)
    const payload = { analysis_id: analysisId, job_title: draft.jobTitle.trim(), job_description: draft.jobDescription.trim(), ...(draft.geography.trim() ? { geography: draft.geography.trim() } : {}), essential_skills: essentialSkills, preferred_skills: preferredSkills }
    try {
      const response = await postJson<CareerWorkflowRun>('/api/career/orchestrator/runs', payload, 'create')
      const verified = verifyRun(response)
      onRunChange(verified); setSelected([]); setReviewNotes(''); setEditing(false); setRestoreState('idle'); setHistoryRefresh((value) => value + 1)
    } catch (reason) { const failure = reason as Error & { kind?: ErrorKind }; if (failure.kind === 'analysis_expired') onAnalysisExpired(); setError({ kind: failure.kind ?? 'service', message: failure.message || 'The workflow could not be created.' }) }
    finally { inFlight.current = false; setLoading(null) }
  }

  async function submitReview(decision: 'proceed' | 'revise' | 'stop') {
    if (!run || inFlight.current || loading) return
    inFlight.current = true; setLoading(decision); setError(null)
    try {
      const payload = { decision, reviewed_requirement_ids: selected, ...(reviewNotes.trim() ? { review_notes: reviewNotes.trim() } : {}) }
      const response = await postJson<CareerWorkflowRun>(`/api/career/orchestrator/runs/${encodeURIComponent(run.run_id)}/review`, payload, 'review')
      const verified = verifyRun(response)
      onRunChange(verified)
      setHistoryRefresh((value) => value + 1)
      if (decision === 'revise') setEditing(true)
    } catch (reason) { const failure = reason as Error & { kind?: ErrorKind }; setError({ kind: failure.kind ?? 'service', message: failure.message || 'The review decision could not be recorded.' }) }
    finally { inFlight.current = false; setLoading(null) }
  }

  function startNew() { onRunChange(null); setSelected([]); setReviewNotes(''); setError(null); setEditing(false) }
  function toggleRequirement(id: string) { setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]); setError(null) }

  async function continueSavedWorkflow(item: WorkflowHistoryItem) {
    if (historyAction?.status === 'loading') return
    historyActionController.current?.abort()
    const controller = new AbortController()
    historyActionController.current = controller
    const expectedOwnerId = ownerId
    setHistoryAction({ runId: item.run_id, status: 'loading' })
    try {
      const result = await restoreSavedWorkflow(item.run_id, skillFairFetch, controller.signal)
      if (controller.signal.aborted || ownerIdRef.current !== expectedOwnerId) return
      if (result.kind !== 'restored') {
        if (item.run_id === runId) {
          onRunExpired(item.run_id)
          onRunChange(null)
          setRestoreState('idle')
        }
        setHistoryAction({ runId: item.run_id, status: 'error', message: 'This workflow is no longer available to this account. It was not replaced with another workflow.' })
        return
      }
      onRunChange(result.run)
      onDraftChange({
        jobTitle: result.workflowInputs.job_title,
        jobDescription: result.workflowInputs.job_description,
        geography: result.workflowInputs.geography ?? '',
        essentialSkills: result.workflowInputs.essential_skills.join('\n'),
        preferredSkills: result.workflowInputs.preferred_skills.join('\n'),
      })
      setSelected([])
      setReviewNotes('')
      setEditing(false)
      setError(null)
      setRestoreState('idle')
      setHistoryAction(null)
    } catch {
      if (controller.signal.aborted || ownerIdRef.current !== expectedOwnerId) return
      setHistoryAction({ runId: item.run_id, status: 'error', message: 'Could not retrieve this workflow. Retry this same workflow when the service is available.' })
    }
  }

  return <div className="career-page">
    <header className="career-heading"><div><span className="eyebrow"><span className="eyebrow-line" /> {reviewMode ? 'SKILLFAIR HUMAN REVIEW' : 'SKILLFAIR CAREER WORKFLOW'}</span><h1>{reviewMode ? 'Review workspace' : 'Career Orchestrator'}</h1><p>{reviewMode ? 'Inspect the current workflow, supporting evidence, and human review state.' : 'Coordinate a skills-first review from candidate evidence to human-guided learning.'}</p></div><span className="career-heading-pill"><Workflow size={16} />Evidence · context · oversight</span></header>
    <div className="career-principles"><span><ShieldCheck size={15} />Evidence-backed</span><span><Globe2 size={15} />Source-aware</span><span><UsersRound size={15} />Recruiter-led</span><p>No candidate ranking or automatic hiring decisions.</p></div>

    {!reviewMode && <WorkflowHistory state={historyState} action={historyAction} onRefresh={() => setHistoryRefresh((value) => value + 1)} onContinue={(item) => void continueSavedWorkflow(item)} />}

    {!reviewMode && !analysisId && <div className="career-missing-analysis" role="status"><div><AlertCircle size={19} /></div><span><b>Skills Discovery is required first</b><small>The orchestrator needs an analysis ID to retrieve validated evidence. It does not accept skill records or resume excerpts from this form.</small></span><button type="button" className="button-primary" onClick={onSkillsDiscovery}>Open Skills Discovery <ArrowRight size={14} /></button></div>}
    <div className="career-workspace">
      {((runId && (run?.run_id !== runId || restoreState !== 'idle')) || (reviewMode && restoreState === 'error')) && <div className="panel career-create-loading" role="status" aria-live="polite"><LoaderCircle size={20} className={restoreState === 'loading' ? 'spin-icon' : ''} /><div><b>{restoreState === 'loading' ? 'Restoring workflow' : 'Workflow restoration needs attention'}</b><span>{restoreState === 'loading' ? 'Fetching the authoritative workflow state from the backend. Review selections and notes start empty.' : error?.message ?? 'The workflow could not be restored.'}</span></div>{restoreState === 'error' && runId && error?.kind !== 'run_expired' && <button type="button" className="button-secondary" onClick={() => void restoreRun(runId)}>Retry</button>}</div>}
      {(!runId || run?.run_id === runId || restoreState === 'error') && showForm && <form className="panel career-start-form" onSubmit={createRun} noValidate>
        <div className="career-form-header"><div className="career-form-icon"><Sparkles size={18} /></div><div><span className="career-stage-kicker">START A COORDINATED REVIEW</span><h2>{run?.workflow_status === 'revise_required' ? 'Revise role criteria' : 'Define the target role'}</h2><p>Role context is recruiter supplied. Skills and evidence are resolved by the backend analysis reference.</p></div></div>
        <div className="career-form-grid"><label htmlFor="career-job-title">Job title<input id="career-job-title" value={draft.jobTitle} onChange={(event) => updateDraft('jobTitle', event.target.value)} maxLength={160} placeholder="e.g. Data Reporting Analyst" /></label><label htmlFor="career-geography">Geography <span>Optional</span><input id="career-geography" value={draft.geography} onChange={(event) => updateDraft('geography', event.target.value)} maxLength={120} placeholder="e.g. Toronto, Canada" /></label><label className="career-wide" htmlFor="career-job-description">Job description<textarea id="career-job-description" value={draft.jobDescription} onChange={(event) => updateDraft('jobDescription', event.target.value)} maxLength={10000} rows={5} placeholder="Describe responsibilities, outcomes, and role context…" /></label><label htmlFor="career-essential-skills">Essential skills <span>{essentialSkills.length} · max 60 total</span><textarea id="career-essential-skills" value={draft.essentialSkills} onChange={(event) => updateDraft('essentialSkills', event.target.value)} rows={4} placeholder={'One per line or comma-separated\nSQL\nStakeholder communication'} /></label><label htmlFor="career-preferred-skills">Preferred skills <span>{preferredSkills.length}</span><textarea id="career-preferred-skills" value={draft.preferredSkills} onChange={(event) => updateDraft('preferredSkills', event.target.value)} rows={4} placeholder={'One per line or comma-separated\nDashboard design\nExcel'} /></label></div>
        {error && <div className={`career-global-error ${error.kind}`} role="alert"><AlertCircle size={16} /><span><b>{error.kind === 'analysis_expired' ? 'Analysis unavailable' : error.kind === 'validation' ? 'Check the role details' : error.kind === 'network' ? 'Service unreachable' : 'Workflow error'}</b>{error.message}</span><button type="button" onClick={() => setError(null)}>Dismiss</button></div>}
        <div className="career-form-footer"><span><ShieldCheck size={14} />No candidate evidence is sent from this form</span><div>{run && <button type="button" className="button-secondary" onClick={() => setEditing(false)}>Cancel</button>}<button type="submit" className="button-primary" disabled={loading !== null || !analysisId}>{loading === 'create' ? <><LoaderCircle size={15} className="spin-icon" />Creating workflow…</> : <>Start career workflow <ArrowRight size={15} /></>}</button></div></div>
      </form>}

      {loading === 'create' && <div className="panel career-create-loading" role="status" aria-live="polite"><LoaderCircle size={20} className="spin-icon" /><div><b>Coordinating workflow stages</b><span>Resolving the Skill Passport, market availability, Job Audit, and Inclusive Matching.</span></div></div>}
      {((reviewMode && reviewViewState === 'workflow') || (!reviewMode && run && restoreState === 'idle' && (!runId || run.run_id === runId))) && run && <WorkflowRunView run={run} selected={selected} onToggle={toggleRequirement} onReview={submitReview} loading={loading !== null} notes={reviewNotes} onNotesChange={setReviewNotes} error={error?.message ?? ''} onStartNew={startNew} onEditCriteria={() => { if (reviewMode) onOpenOrchestrator?.(); else { setEditing(true); setError(null) } }} reviewMode={reviewMode} impactTracking={impactTracking} />}
      {reviewMode && (reviewViewState === 'empty' || reviewViewState === 'error') && <section className="panel career-empty recruiter-review-empty" aria-live="polite"><div><Compass size={22} /></div><h2>{reviewViewState === 'error' ? 'No current workflow is available' : 'No workflow is ready for review'}</h2><p>{reviewViewState === 'error' ? 'The saved run could not be restored. Start a new workflow after completing or confirming Skills Discovery.' : 'Recruiter Review shows real Career Orchestrator workflows only. Illustrative Overview profiles are not candidate records.'}</p><div className="recruiter-review-empty-actions"><button type="button" className="button-secondary" onClick={onSkillsDiscovery}>Go to Skills Discovery</button><button type="button" className="button-primary" onClick={onOpenOrchestrator}>Open Career Orchestrator</button></div></section>}
      {!reviewMode && !run && !showForm && !loading && <div className="panel career-empty"><div><Compass size={22} /></div><h2>Start a recruiter-led workflow</h2><p>Coordinate validated candidate evidence, available market context, job criteria review, inclusive matching, a human decision, and then learning activities.</p></div>}
    </div>
  </div>
}
