import { useRef, useState } from 'react'
import { ArrowRight, Check, ChevronDown, FileText, LoaderCircle, LockKeyhole, Sparkles, UploadCloud, X } from 'lucide-react'
import type { SkillEvidence, SkillsDiscoveryResult } from '../types/skills'

type Props = { onContinue: (skills: SkillEvidence[], mode: SkillsDiscoveryResult['analysis_mode']) => void }
const MAX_BYTES = 10 * 1024 * 1024

function discover(file: File, onProgress: (value: number) => void): Promise<SkillsDiscoveryResult> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('POST', '/api/skills/discover')
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    request.upload.onload = () => onProgress(100)
    request.onerror = () => reject(new Error('Could not reach the Skills Discovery service. Check that the backend is running.'))
    request.onload = () => {
      let body: { detail?: string } & Partial<SkillsDiscoveryResult>
      try { body = JSON.parse(request.responseText) as typeof body } catch { reject(new Error('The service returned an unreadable response.')); return }
      if (request.status < 200 || request.status >= 300) { reject(new Error(body.detail ?? 'Resume analysis failed. Please try again.')); return }
      resolve(body as SkillsDiscoveryResult)
    }
    const form = new FormData()
    form.append('file', file)
    request.send(form)
  })
}

function SkillCard({ skill }: { skill: SkillEvidence }) {
  const inferred = skill.evidence_type === 'inferred'
  return <article className="discovery-skill-card">
    <div className="discovery-skill-heading"><div><h3>{skill.skill_name}</h3><span className={`evidence-badge ${inferred ? 'inferred' : 'explicit'}`}>{inferred ? 'Inferred · unverified' : 'Explicit evidence'}</span></div><span className={`validation-mark ${inferred ? 'validation-inferred' : ''}`}>{inferred ? <Sparkles size={14} /> : <Check size={14} />}</span></div>
    <p className="skill-relevance">{skill.relevance_explanation}</p>
    <details className="evidence-details"><summary>View resume evidence <ChevronDown size={14} /></summary><blockquote>“{skill.supporting_excerpt}”</blockquote></details>
  </article>
}

export default function SkillsDiscovery({ onContinue }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<SkillsDiscoveryResult | null>(null)
  const [error, setError] = useState('')
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'analyzing'>('idle')
  const [progress, setProgress] = useState(0)

  function selectFile(next: File | undefined) {
    setError('')
    setResult(null)
    if (!next) return
    const extension = next.name.toLowerCase().split('.').pop()
    if (!['pdf', 'docx'].includes(extension ?? '')) { setFile(null); setError('Choose a PDF or DOCX file.'); return }
    if (next.size > MAX_BYTES) { setFile(null); setError('This file exceeds the 10 MB limit.'); return }
    if (!next.size) { setFile(null); setError('This file is empty. Choose another resume.'); return }
    setFile(next)
  }

  async function submit() {
    if (!file || phase !== 'idle') return
    setError('')
    setResult(null)
    setProgress(0)
    setPhase('uploading')
    try {
      const response = await discover(file, (value) => {
        setProgress(value)
        if (value >= 100) setPhase('analyzing')
      })
      setResult(response)
      setProgress(100)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Resume analysis failed. Please try again.')
    } finally {
      setPhase('idle')
    }
  }

  const explicit = result?.skills.filter((skill) => skill.evidence_type === 'explicit') ?? []
  const inferred = result?.skills.filter((skill) => skill.evidence_type === 'inferred') ?? []

  return <div className="discovery-page">
    <div className="discovery-heading"><div><span className="eyebrow"><span className="eyebrow-line" /> SKILLS-FIRST CANDIDATE REVIEW</span><h1>Skills Discovery</h1><p>Uncover the capabilities behind every candidate’s experience.</p></div><div className="privacy-note"><LockKeyhole size={15} />Resume processed temporarily</div></div>
    {!result && <section className="discovery-upload-panel">
      <div className={`drop-zone ${dragging ? 'drop-zone-active' : ''} ${error ? 'drop-zone-error' : ''}`} onDragEnter={(event) => { event.preventDefault(); setDragging(true) }} onDragOver={(event) => event.preventDefault()} onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false) }} onDrop={(event) => { event.preventDefault(); setDragging(false); selectFile(event.dataTransfer.files[0]) }}>
        <div className="upload-art"><UploadCloud size={25} /></div><h2>Upload a candidate resume</h2><p>Drop a file here or browse from your device</p><button className="button-secondary browse-button" onClick={() => inputRef.current?.click()} disabled={phase !== 'idle'}>Browse files</button><input ref={inputRef} type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" hidden onChange={(event) => { selectFile(event.target.files?.[0]); event.target.value = '' }} /><span className="upload-guidance">PDF or DOCX · Up to 10 MB · Text-based documents</span>
      </div>
      {file && <div className="selected-file"><div className="file-icon"><FileText size={18} /></div><div className="selected-file-info"><b>{file.name}</b><span>{(file.size / 1024 / 1024).toFixed(2)} MB · Ready to process</span></div><button className="remove-file" onClick={() => { setFile(null); setResult(null) }} aria-label="Remove selected resume" disabled={phase !== 'idle'}><X size={17} /></button></div>}
      {phase !== 'idle' && <div className="upload-progress" role="status" aria-live="polite"><div className="progress-heading"><span><LoaderCircle className="spin-icon" size={16} />{phase === 'uploading' ? 'Uploading resume' : 'Extracting text and checking for configured AI analysis'}</span><b>{phase === 'uploading' ? `${progress}%` : 'In progress'}</b></div><div className="progress-track"><div style={{ width: `${phase === 'analyzing' ? 100 : progress}%` }} /></div></div>}
      {error && <div className="discovery-error" role="alert"><X size={16} />{error}<button onClick={submit} disabled={!file || phase !== 'idle'}>Try again</button></div>}
      {file && phase === 'idle' && <div className="upload-submit-row"><span><LockKeyhole size={13} />Files are processed in memory and not saved.</span><button className="button-primary" onClick={submit}><Sparkles size={15} />Discover skills</button></div>}
    </section>}

    {result && <section className="discovery-results">
      <div className="result-file-row"><div className="selected-file"><div className="file-icon"><FileText size={18} /></div><div className="selected-file-info"><b>{file?.name}</b><span>{result.extracted_characters.toLocaleString()} characters extracted</span></div></div><button className="button-secondary" onClick={() => { setResult(null); setFile(null); setError('') }}>Upload another resume</button></div>
      <div className={`analysis-mode-banner ${result.analysis_mode}`}><div className="mode-icon">{result.analysis_mode === 'ai' ? <Sparkles size={17} /> : <FileText size={17} />}</div><div><b>{result.analysis_mode === 'ai' ? 'AI analysis complete' : 'Development mode · Text extraction only'}</b><p>{result.message}</p></div><span>{result.analysis_mode === 'ai' ? 'AI analysis' : 'No AI configured'}</span></div>
      {result.analysis_mode === 'development' && <details className="extracted-preview"><summary>Preview extracted resume text <ChevronDown size={14} /></summary><pre>{result.extracted_text}</pre></details>}
      {result.analysis_mode === 'ai' && <>
        <div className="results-title"><div><h2>Skills found in this resume</h2><p>Each skill includes its supporting evidence. Inferred items remain unverified.</p></div><span className="result-count">{result.skills.length} skills</span></div>
        {result.skills.length === 0 ? <div className="discovery-empty"><Sparkles size={19} /><b>No skills were returned for this resume</b><span>Try another document or review the resume text.</span></div> : <>
          {explicit.length > 0 && <div className="skill-group"><div className="skill-group-title"><h2>Explicit evidence</h2><span>{explicit.length} · directly stated in resume</span></div><div className="discovered-skills-grid">{explicit.map((skill, index) => <SkillCard key={`${skill.skill_name}-${index}`} skill={skill} />)}</div></div>}
          {inferred.length > 0 && <div className="skill-group inferred-group"><div className="skill-group-title"><h2>Inferred · unverified</h2><span>{inferred.length} · suggested by context, not confirmed</span></div><div className="discovered-skills-grid">{inferred.map((skill, index) => <SkillCard key={`${skill.skill_name}-${index}`} skill={skill} />)}</div></div>}
        </>}
      </>}
      <div className="continue-row"><span><LockKeyhole size={13} />Review evidence before making hiring decisions.</span><button className="button-primary" onClick={() => onContinue(result.skills, result.analysis_mode)}>Continue to job matching <ArrowRight size={15} /></button></div>
    </section>}
    {!file && !result && !error && <div className="discovery-empty-hint"><Sparkles size={16} /><span>Skills are only returned when a configured AI provider has analyzed the resume. Development mode exercises secure text extraction without generating sample skills.</span></div>}
  </div>
}
