import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Check, ChevronDown, FileText, LoaderCircle, LockKeyhole, ShieldCheck, Sparkles, UploadCloud, X } from 'lucide-react'
import type { SkillEvidence, SkillsDiscoveryResult } from '../types/skills'
import { uploadResume } from '../resumeUpload'

type Props = {
  onContinue: () => void
  onAnalysisReady: (analysisId: string, skillCount: number, mode: SkillsDiscoveryResult['analysis_mode'], skills: SkillEvidence[]) => void
  restoredAnalysis: { analysisId: string; analysisMode: SkillsDiscoveryResult['analysis_mode']; skills: SkillEvidence[] } | null
}
const MAX_BYTES = 10 * 1024 * 1024

type ProviderStatus = { provider: 'none' | 'gemini' | 'groq'; configured: boolean; message: string }

function providerLabel(provider: ProviderStatus['provider'] | undefined) {
  if (provider === 'gemini') return 'Google Gemini'
  if (provider === 'groq') return 'Groq'
  return 'an external AI provider'
}

function SkillCard({ skill }: { skill: SkillEvidence }) {
  const inferred = skill.evidence_type === 'inferred'
  return <article className="discovery-skill-card">
    <div className="discovery-skill-heading"><div><h3>{skill.skill_name}</h3><span className={`evidence-badge ${inferred ? 'inferred' : 'explicit'}`}>{inferred ? 'Inferred · unverified' : skill.detection_method === 'local' ? 'Local exact mention' : 'Explicit evidence'}</span></div><span className={`validation-mark ${inferred ? 'validation-inferred' : ''}`}>{inferred ? <Sparkles size={14} /> : <Check size={14} />}</span></div>
    <p className="skill-relevance">{skill.relevance_explanation}</p>
    <details className="evidence-details"><summary>View resume evidence <ChevronDown size={14} /></summary><blockquote>“{skill.supporting_excerpt}”</blockquote></details>
  </article>
}

export default function SkillsDiscovery({ onContinue, onAnalysisReady, restoredAnalysis }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<SkillsDiscoveryResult | null>(null)
  const [dismissRestored, setDismissRestored] = useState(false)
  const [error, setError] = useState('')
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'analyzing'>('idle')
  const [progress, setProgress] = useState(0)
  const [analysisMode, setAnalysisMode] = useState<'local' | 'ai'>('local')
  const [providerConsent, setProviderConsent] = useState(false)
  const [providerStatus, setProviderStatus] = useState<ProviderStatus | null>(null)
  const uploadController = useRef<AbortController | null>(null)
  const uploadRequestId = useRef(0)

  useEffect(() => () => {
    uploadRequestId.current += 1
    uploadController.current?.abort()
    uploadController.current = null
  }, [])

  useEffect(() => {
    let active = true
    fetch('/api/skills/provider')
      .then(async (response) => {
        if (!response.ok) throw new Error('Provider status unavailable')
        return response.json() as Promise<ProviderStatus>
      })
      .then((status) => { if (active) setProviderStatus(status) })
      .catch(() => {
        if (active) setProviderStatus({ provider: 'none', configured: false, message: 'AI provider status could not be checked. Local analysis is still available.' })
      })
    return () => { active = false }
  }, [])

  const selectedProviderLabel = providerLabel(providerStatus?.provider)

  function selectFile(next: File | undefined) {
    setError('')
    setResult(null)
    setDismissRestored(true)
    if (!next) return
    const extension = next.name.toLowerCase().split('.').pop()
    if (!['pdf', 'docx'].includes(extension ?? '')) { setFile(null); setError('Choose a PDF or DOCX file.'); return }
    if (next.size > MAX_BYTES) { setFile(null); setError('This file exceeds the 10 MB limit.'); return }
    if (!next.size) { setFile(null); setError('This file is empty. Choose another resume.'); return }
    setFile(next)
  }

  async function submit() {
    if (!file || phase !== 'idle') return
    if (analysisMode === 'ai' && (!providerStatus?.configured || !providerConsent)) return
    setError('')
    setResult(null)
    setProgress(0)
    setPhase('uploading')
    const requestId = ++uploadRequestId.current
    const controller = new AbortController()
    uploadController.current = controller
    const isCurrent = () => uploadRequestId.current === requestId && !controller.signal.aborted
    try {
      const consentedProvider = providerStatus?.provider === 'gemini' || providerStatus?.provider === 'groq' ? providerStatus.provider : undefined
      const response = await uploadResume(file, analysisMode, analysisMode === 'ai' && providerConsent, consentedProvider, (value) => {
        if (!isCurrent()) return
        setProgress(value)
        if (value >= 100) setPhase('analyzing')
      }, controller.signal)
      if (!isCurrent()) return
      onAnalysisReady(response.analysis_id, response.skills.length, response.analysis_mode, response.skills)
      setResult(response)
      setProgress(100)
    } catch (reason) {
      if (isCurrent()) setError(reason instanceof Error ? reason.message : 'Resume analysis failed. Please try again.')
    } finally {
      if (uploadRequestId.current === requestId) {
        uploadController.current = null
        setPhase('idle')
        setProviderConsent(false)
      }
    }
  }

  function retryAnalysis() {
    if (!file || phase !== 'idle') return
    setResult(null)
    setError('')
    void submit()
  }

  const restoredResult = !result && !dismissRestored && restoredAnalysis ? {
    analysis_id: restoredAnalysis.analysisId,
    analysis_mode: restoredAnalysis.analysisMode,
    skills: restoredAnalysis.skills,
  } : null
  const displayedResult = result ?? restoredResult
  const displayedExplicit = displayedResult?.skills.filter((skill) => skill.evidence_type === 'explicit') ?? []
  const displayedInferred = displayedResult?.skills.filter((skill) => skill.evidence_type === 'inferred') ?? []

  return <div className="discovery-page">
    <div className="discovery-heading"><div><span className="eyebrow"><span className="eyebrow-line" /> SKILLS-FIRST CANDIDATE REVIEW</span><h1>Skills Discovery</h1><p>Uncover the capabilities behind every candidate’s experience.</p></div><div className="privacy-note"><LockKeyhole size={15} />Resume processed temporarily</div></div>
    {!displayedResult && <section className="discovery-upload-panel">
      <fieldset className="analysis-choice">
        <legend>Choose how to analyse this resume</legend>
        <label><input type="radio" name="analysis-mode" value="local" checked={analysisMode === 'local'} disabled={phase !== 'idle'} onChange={() => { setAnalysisMode('local'); setProviderConsent(false) }} /> <span><b>Local deterministic analysis</b><small>Exact vocabulary matching; resume text stays within SkillFair.</small></span></label>
        <label><input type="radio" name="analysis-mode" value="ai" checked={analysisMode === 'ai'} disabled={phase !== 'idle' || !providerStatus?.configured} onChange={() => { setAnalysisMode('ai'); setProviderConsent(false) }} /> <span><b>{providerStatus?.configured ? `${selectedProviderLabel} AI-assisted analysis` : 'External AI analysis unavailable'}</b><small>{providerStatus?.configured ? 'Requires fresh consent each time. Account and model access are checked when you submit.' : providerStatus?.message ?? 'Checking configured provider…'}</small></span></label>
      </fieldset>
      {analysisMode === 'ai' && providerStatus?.configured && <div className="gemini-disclosure" role="group" aria-labelledby="provider-disclosure-title">
        <b id="provider-disclosure-title">Before you choose {selectedProviderLabel}</b>
        <p>The full extracted resume text will be sent to {selectedProviderLabel} for AI-assisted skill analysis. Resume content may contain personal information. Local deterministic analysis is available without sending resume text to an external AI provider. SkillFair’s code does not control {selectedProviderLabel}’s data retention or handling.</p>
        <label><input type="checkbox" checked={providerConsent} disabled={phase !== 'idle'} onChange={(event) => setProviderConsent(event.target.checked)} /> I consent to sending the full extracted resume text to {selectedProviderLabel} for this analysis.</label>
      </div>}
      <div className={`drop-zone ${dragging ? 'drop-zone-active' : ''} ${error ? 'drop-zone-error' : ''}`} onDragEnter={(event) => { event.preventDefault(); setDragging(true) }} onDragOver={(event) => event.preventDefault()} onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false) }} onDrop={(event) => { event.preventDefault(); setDragging(false); selectFile(event.dataTransfer.files[0]) }}>
        <div className="upload-art"><UploadCloud size={25} /></div><h2>Upload a candidate resume</h2><p>Drop a file here or browse from your device</p><button className="button-secondary browse-button" onClick={() => inputRef.current?.click()} disabled={phase !== 'idle'}>Browse files</button><input ref={inputRef} type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" hidden onChange={(event) => { selectFile(event.target.files?.[0]); event.target.value = '' }} /><span className="upload-guidance">PDF or DOCX · Up to 10 MB · Text-based documents</span>
      </div>
      {file && <div className="selected-file"><div className="file-icon"><FileText size={18} /></div><div className="selected-file-info"><b>{file.name}</b><span>{(file.size / 1024 / 1024).toFixed(2)} MB · Ready to process</span></div><button className="remove-file" onClick={() => { setFile(null); setResult(null) }} aria-label="Remove selected resume" disabled={phase !== 'idle'}><X size={17} /></button></div>}
      {phase !== 'idle' && <div className="upload-progress" role="status" aria-live="polite"><div className="progress-heading"><span><LoaderCircle className="spin-icon" size={16} />{phase === 'uploading' ? 'Uploading resume' : analysisMode === 'ai' ? `Extracting text for ${selectedProviderLabel} analysis` : 'Extracting text for local analysis'}</span><b>{phase === 'uploading' ? `${progress}%` : 'In progress'}</b></div><div className="progress-track"><div style={{ width: `${phase === 'analyzing' ? 100 : progress}%` }} /></div></div>}
      {error && <div className="discovery-error" role="alert"><X size={16} />{error}<button onClick={submit} disabled={!file || phase !== 'idle' || (analysisMode === 'ai' && (!providerStatus?.configured || !providerConsent))}>Try again</button></div>}
      {file && phase === 'idle' && <div className="upload-submit-row"><span><LockKeyhole size={13} />Files are processed in memory and not saved.</span><button className="button-primary" onClick={submit} disabled={analysisMode === 'ai' && (!providerStatus?.configured || !providerConsent)}><Sparkles size={15} />{analysisMode === 'ai' ? `Consent & analyse with ${providerStatus?.configured ? selectedProviderLabel : 'AI'}` : 'Discover skills locally'}</button></div>}
    </section>}

    {displayedResult && <section className="discovery-results">
      <div className="result-file-row"><div className="selected-file"><div className="file-icon"><FileText size={18} /></div><div className="selected-file-info"><b>{file?.name ?? 'Saved Skill Passport'}</b><span>{result ? `${result.extracted_characters.toLocaleString()} characters extracted` : 'Validated skill evidence restored from your account; resume text is not retained'}</span></div></div><button className="button-secondary" onClick={() => { setResult(null); setFile(null); setError(''); setDismissRestored(true) }}>Upload another resume</button></div>
      {restoredResult && <div className="account-restore-banner" role="status">Showing the latest saved analysis and evidence from your account.</div>}
      <div className={`analysis-mode-banner ${displayedResult.analysis_mode}`}><div className="mode-icon">{displayedResult.analysis_mode === 'ai' ? <Sparkles size={17} /> : <FileText size={17} />}</div><div><b>{displayedResult.analysis_mode === 'ai' ? restoredResult ? 'AI-assisted analysis · restored' : `AI analysis complete via ${selectedProviderLabel}` : displayedResult.analysis_mode === 'local' ? restoredResult ? 'Local deterministic analysis · restored' : 'Local deterministic analysis complete' : 'Text extraction only · no skills analysis'}</b><p>{result?.message ?? 'Saved skill records and evidence were restored from the authenticated account. Resume files and full resume text are not retained.'}</p></div><span>{displayedResult.analysis_mode === 'ai' ? 'AI-assisted analysis' : displayedResult.analysis_mode === 'local' ? 'Exact-term scan' : 'Extraction only'}</span></div>
      {displayedResult.analysis_mode === 'local' && <div className="discovery-empty" role="note"><ShieldCheck size={18} /><b>Local mentions are not proof of proficiency</b><span>Only exact vocabulary matches in a skills section, project entry, or direct usage phrase are shown. No skills are inferred.</span></div>}
      {(displayedResult.analysis_mode === 'ai' || displayedResult.analysis_mode === 'local') && <>
        <div className="results-title"><div><h2>{displayedResult.analysis_mode === 'local' ? 'Explicit skill mentions found' : 'Skills found in this resume'}</h2><p>{displayedResult.analysis_mode === 'local' ? 'Each mention includes the exact resume line that triggered the deterministic match.' : 'Each skill includes its supporting evidence. Inferred items remain unverified.'}</p></div><span className="result-count">{displayedResult.skills.length} skills</span></div>
        {displayedResult.skills.length === 0 ? <div className="discovery-empty" role="status"><Sparkles size={19} /><b>{displayedResult.analysis_mode === 'local' ? 'No skills met the local matching rules' : 'No skills were returned for this resume'}</b><span>{displayedResult.analysis_mode === 'local' ? 'This does not mean the resume contains no skills. Try the scan again or review the resume manually.' : 'Review the resume manually or try the analysis again.'}</span>{result?.analysis_mode === 'local' && <button type="button" className="button-secondary" onClick={retryAnalysis} disabled={phase !== 'idle'}>Retry local scan</button>}</div> : <>
          {displayedExplicit.length > 0 && <div className="skill-group"><div className="skill-group-title"><h2>{displayedResult.analysis_mode === 'local' ? 'Local explicit mentions' : 'Explicit evidence'}</h2><span>{displayedExplicit.length} · directly stated in resume</span></div><div className="discovered-skills-grid">{displayedExplicit.map((skill, index) => <SkillCard key={`${skill.skill_name}-${index}`} skill={skill} />)}</div></div>}
          {displayedInferred.length > 0 && <div className="skill-group inferred-group"><div className="skill-group-title"><h2>Inferred · unverified</h2><span>{displayedInferred.length} · suggested by context, not confirmed</span></div><div className="discovered-skills-grid">{displayedInferred.map((skill, index) => <SkillCard key={`${skill.skill_name}-${index}`} skill={skill} />)}</div></div>}
        </>}
      </>}
      {displayedResult.analysis_mode === 'development' ? <div className="continue-row"><span><LockKeyhole size={13} />No skill analysis is available for this result.</span><button className="button-primary" disabled>Run skill analysis first</button></div> : <div className="continue-row"><span><LockKeyhole size={13} />Review mentions and evidence before making hiring decisions.</span><button className="button-primary" onClick={onContinue}>Continue to job matching <ArrowRight size={15} /></button></div>}
    </section>}
    {!file && !displayedResult && !error && <div className="discovery-empty-hint"><Sparkles size={16} /><span>Local deterministic analysis is available without external transfer. Local mentions include evidence and are not proof of proficiency.</span></div>}
  </div>
}
