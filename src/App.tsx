import { useEffect, useRef, useState } from 'react'
import {
  Activity, ArrowLeftRight, ArrowRight, Bell, BriefcaseBusiness, ChartNoAxesCombined,
  Check, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, ClipboardCheck, Command, FileSearch,
  GraduationCap, LayoutDashboard, LogOut, Menu, MoreHorizontal, Plus, Search, ScanSearch, Settings2,
  SlidersHorizontal, Sparkles, UsersRound, X,
} from 'lucide-react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { activities, candidates, pipelineData, sections } from './data/demoData'
import SkillsDiscovery from './components/SkillsDiscovery'
import JobMatching from './components/JobMatching'
import BiasAudit from './components/BiasAudit'
import LearningPathways from './components/LearningPathways'
import MarketIntelligence from './components/MarketIntelligence'
import TransferableSkills from './components/TransferableSkills'
import InclusiveMatching from './components/InclusiveMatching'
import CareerOrchestrator, { type CareerWorkflowDraft, type CareerWorkflowRun } from './components/CareerOrchestrator'
import RecruiterReview from './components/RecruiterReview'
import ImpactMeasurement from './components/ImpactMeasurement'
import AuthPage from './components/AuthPage'
import { authDisplayName, type AuthUser } from './authClient'
import { skillFairFetch } from './apiClient'
import { useAuth, type AuthContextValue } from './authContext'
import type { SkillEvidence, SkillsDiscoveryResult } from './types/skills'
import { clearSkillFairSession, emptySkillFairSession, isValidSessionLocator, readSkillFairSession, writeSkillFairSession, type AppPage } from './sessionState'
import { restoreSavedWorkflow } from './workflowRestoration'

const iconMap = { LayoutDashboard, Sparkles, ChartNoAxesCombined, GraduationCap, UsersRound, ScanSearch, ClipboardCheck, ArrowLeftRight, BriefcaseBusiness, Activity }
type Page = AppPage

function isSkillEvidence(value: unknown): value is SkillEvidence {
  if (typeof value !== 'object' || value === null) return false
  const skill = value as Record<string, unknown>
  return typeof skill.skill_name === 'string'
    && (skill.evidence_type === 'explicit' || skill.evidence_type === 'inferred')
    && (skill.detection_method === 'ai' || skill.detection_method === 'local')
    && typeof skill.supporting_excerpt === 'string'
    && typeof skill.relevance_explanation === 'string'
    && (skill.validation_status === 'evidence_found' || skill.validation_status === 'inference_unverified')
}

function Sidebar({ page, setPage, collapsed, setCollapsed, mobileOpen, setMobileOpen, userEmail, onLogout, logoutBusy }: {
  page: Page; setPage: (page: Page) => void; collapsed: boolean; setCollapsed: (v: boolean) => void; mobileOpen: boolean; setMobileOpen: (v: boolean) => void; userEmail: string; onLogout: () => void; logoutBusy: boolean
}) {
  return <>
    {mobileOpen && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
    <aside className={`sidebar ${collapsed ? 'sidebar-collapsed' : ''} ${mobileOpen ? 'sidebar-open' : ''}`}>
      <div className="brand-row"><div className="brand-mark"><span /><span /><span /><span /></div>{!collapsed && <span className="brand-name">skillfair<span className="brand-dot">.</span></span>}{!collapsed && <button className="sidebar-collapse" onClick={() => setCollapsed(true)} aria-label="Collapse sidebar"><ChevronLeft size={16} /></button>}</div>
      {collapsed && <button className="expand-sidebar" onClick={() => setCollapsed(false)} aria-label="Expand sidebar"><ChevronRight size={16} /></button>}
      <div className="workspace-select"><div className="workspace-icon">N</div>{!collapsed && <><div className="workspace-text"><b>Northstar Studio</b><span>Recruiting team</span></div><ChevronDown size={15} className="muted-icon" /></>}</div>
      <div className="nav-label">WORKSPACE</div>
      <nav className="primary-nav" aria-label="Main navigation">{sections.map((item) => {
        const Icon = iconMap[item.icon as keyof typeof iconMap]
        return <button key={item.name} className={`nav-item ${page === item.name ? 'nav-active' : ''}`} onClick={() => { setPage(item.name); setMobileOpen(false) }} title={collapsed ? item.name : undefined} aria-current={page === item.name ? 'page' : undefined}><Icon size={18} strokeWidth={1.8} /><span>{item.name}</span></button>
      })}</nav>
      {!collapsed && <div className="sidebar-bottom"><div className="plan-card"><div className="plan-icon"><Sparkles size={15} /></div><b>Illustrative workspace</b><p>Dashboard figures are sample content, not live recruiting activity.</p><button onClick={() => setPage('Market Intelligence')}>Explore insights <ArrowRight size={14} /></button></div><button className="nav-item" onClick={() => setPage('Overview')}><CircleHelp size={18} /><span>Help center</span></button><button className="nav-item" onClick={() => setPage('Overview')}><Settings2 size={18} /><span>Settings</span></button></div>}
      {!collapsed && <div className="sidebar-footer"><div className="mini-avatar">{userEmail.slice(0, 1).toUpperCase()}</div><div className="footer-user"><b title={userEmail}>{userEmail}</b><span>Signed in</span></div><button className="sidebar-logout" onClick={onLogout} disabled={logoutBusy} aria-label="Sign out" title="Sign out"><LogOut size={17} /></button></div>}
    </aside>
  </>
}

function Topbar({ page, userEmail, onMenu, onNotice, onLogout, logoutBusy }: { page: Page; userEmail: string; onMenu: () => void; onNotice: () => void; onLogout: () => void; logoutBusy: boolean }) {
  return <header className="topbar"><button className="mobile-menu" onClick={onMenu} aria-label="Open navigation"><Menu size={20} /></button><div className="breadcrumb"><span>Workspace</span><ChevronRight size={14} /><b>{page}</b></div><div className="top-actions"><label className="search-box"><Search size={16} /><input aria-label="Search" placeholder="Search anything..." /><kbd><Command size={11} /> K</kbd></label><button className="icon-button notification-button" aria-label="Notifications" onClick={onNotice}><Bell size={18} /><i /></button><div className="top-divider" /><button className="profile-control" onClick={onLogout} disabled={logoutBusy} aria-label={`Sign out ${userEmail}`} title="Sign out"><div className="mini-avatar">{userEmail.slice(0, 1).toUpperCase()}</div><span>{userEmail}</span><LogOut size={15} className="muted-icon" /></button></div></header>
}

function MetricCard({ label, value, icon: Icon, tone }: { label: string; value: string; icon: typeof UsersRound; tone: string }) {
  return <article className="metric-card"><div className="metric-top"><span>Sample · {label}</span><div className={`metric-icon ${tone}`}><Icon size={17} /></div></div><div className="metric-bottom"><strong>{value}</strong><span className="metric-change">Demo</span></div><span className="metric-period">Illustrative data · not measured</span></article>
}

function PipelineChart() {
  return <div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><AreaChart data={pipelineData} margin={{ top: 12, right: 8, left: -20, bottom: 0 }}>
    <defs><linearGradient id="reviewFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#6786f5" stopOpacity={0.16} /><stop offset="100%" stopColor="#6786f5" stopOpacity={0} /></linearGradient><linearGradient id="matchFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#9475e8" stopOpacity={0.12} /><stop offset="100%" stopColor="#9475e8" stopOpacity={0} /></linearGradient></defs>
    <CartesianGrid stroke="#edf0f6" vertical={false} /><XAxis dataKey="week" axisLine={false} tickLine={false} tick={{ fill: '#949bb0', fontSize: 11 }} dy={10} /><YAxis axisLine={false} tickLine={false} tick={{ fill: '#949bb0', fontSize: 11 }} ticks={[0, 20, 40, 60]} /><Tooltip contentStyle={{ border: '1px solid #eceef5', borderRadius: 12, boxShadow: '0 8px 24px #17203b12', fontSize: 12 }} />
    <Area type="monotone" dataKey="reviewed" name="Sample candidates reviewed" stroke="#6886f5" strokeWidth={2.5} fill="url(#reviewFill)" activeDot={{ r: 5, strokeWidth: 0 }} /><Area type="monotone" dataKey="matched" name="Sample requirement matches" stroke="#9674e8" strokeWidth={2.5} fill="url(#matchFill)" activeDot={{ r: 5, strokeWidth: 0 }} />
  </AreaChart></ResponsiveContainer></div>
}

function ActivityFeed() {
  return <div className="activity-list">{activities.map((item) => <div className="activity-row" key={item.name}><div className={`avatar avatar-${item.tone}`}>{item.initials}</div><div className="activity-copy"><p><b>{item.name}</b> {item.action}</p><span>{item.time}</span></div><button className="dots-button" aria-label={`More activity for ${item.name}`}><MoreHorizontal size={17} /></button></div>)}</div>
}

function CandidateCard({ person }: { person: typeof candidates[number] }) {
  return <article className="candidate-card"><div className="candidate-head"><div className={`avatar avatar-${person.tone}`}>{person.initials}</div><div className="candidate-id"><b>{person.name}</b><span>{person.role} <i>·</i> {person.location}</span></div><button className="dots-button" aria-label={`More options for ${person.name}`}><MoreHorizontal size={18} /></button></div><div className="candidate-match"><div className="match-ring">DEMO</div><span>Illustrative profile</span><span className="verified-pill"><Check size={12} />Sample data</span></div><div className="skill-tags">{person.skills.map((skill) => <span key={skill}>{skill}</span>)}</div><button className="candidate-action">View profile <ArrowRight size={15} /></button></article>
}

function Overview({ setPage, greetingName }: { setPage: (page: Page) => void; greetingName: string }) {
  return <>
    <div className="demo-note overview-demo-banner" role="note">Illustrative demonstration · All Overview names, counts, chart values, activity and profiles below are sample data only. They are not live candidate records, verified outcomes, or impact measurements.</div>
    <section className="welcome-row"><div><div className="eyebrow"><span className="eyebrow-line" /> ILLUSTRATIVE DEMONSTRATION</div><h1>Good morning, {greetingName} <span>✦</span></h1><p>Sample view of a skills-first hiring workspace. No live recruiting activity is shown.</p></div><div className="welcome-actions"><button className="button-secondary" onClick={() => setPage('Market Intelligence')}><FileSearch size={16} />Analyze a job</button><button className="button-primary" onClick={() => setPage('Skills Discovery')}><Plus size={17} />Discover skills</button></div></section>
    <section className="metrics-grid" aria-label="Illustrative sample metrics"><MetricCard label="Candidates reviewed" value="1,284" icon={UsersRound} tone="metric-blue" /><MetricCard label="Potential matches" value="342" icon={Sparkles} tone="metric-violet" /><MetricCard label="Skills identified" value="2,891" icon={Activity} tone="metric-cyan" /><MetricCard label="Awaiting review" value="28" icon={ClipboardCheck} tone="metric-amber" /></section>
    <section className="insights-grid"><article className="panel pipeline-panel"><div className="panel-heading"><div><h2>Sample candidate pipeline</h2><p>Illustrative chart data · not measured activity</p></div><span className="select-button">Sample period</span></div><div className="chart-legend"><span><i className="legend-blue" />Sample reviewed <b>275</b></span><span><i className="legend-violet" />Sample requirement matches <b>178</b></span></div><PipelineChart /></article><article className="panel activity-panel"><div className="panel-heading"><div><h2>Sample recent activity</h2><p>Illustrative entries only</p></div><span className="text-link">Demo</span></div><ActivityFeed /><button className="activity-footer" onClick={() => setPage('Recruiter Review')}>Go to review queue <ArrowRight size={15} /></button></article></section>
    <section className="candidates-section"><div className="section-heading"><div><h2>Illustrative candidate profiles</h2><p>Sample skills only · no scores or hiring outcomes</p></div><div className="section-controls"><button className="filter-button"><SlidersHorizontal size={15} />Filters</button><button className="text-link" onClick={() => setPage('Recruiter Review')}>View all candidates <ArrowRight size={14} /></button></div></div><div className="candidate-grid">{candidates.map((person) => <CandidateCard key={person.name} person={person} />)}</div></section>
  </>
}

function ModulePage({ page }: { page: Page }) {
  const descriptions: Record<Page, string> = {
    Overview: '', 'Impact Measurement': 'Review the pilot measures SkillFair would need to evaluate its outcomes responsibly.', 'Skills Discovery': 'Explore capabilities from candidate experience and map them to your roles.',
    'Market Intelligence': 'Understand the skills landscape and discover where talent is growing.',
    'Transferable Skills': 'Explore evidence-aware capability connections between roles.',
    'Learning Pathways': 'See learning journeys that can help candidates build skills for their next role.',
    'Inclusive Matching': 'Review skills-based recommendations designed to make opportunity more accessible.',
    'Career Orchestrator': 'Coordinate evidence, role context, fairness review, and learning in one recruiter-led workflow.',
    'Bias Audit': 'Inspect hiring criteria and monitor fairness across your review process.',
    'Recruiter Review': 'Review candidate profiles and keep your hiring workflow moving.',
    'Job Matching': 'Compare candidate capabilities with the skills a role needs.',
  }
  const Icon = page === 'Job Matching' ? BriefcaseBusiness : iconMap[sections.find((item) => item.name === page)!.icon as keyof typeof iconMap]
  return <div className="module-page"><div className="module-icon"><Icon size={23} /></div><span className="eyebrow"><span className="eyebrow-line" /> SKILLFAIR WORKSPACE</span><h1>{page}</h1><p>{descriptions[page]}</p><div className="module-empty"><div className="empty-orbit"><Icon size={24} /></div><h2>Your {page.toLowerCase()} workspace is ready</h2><p>This presentation-ready preview uses illustrative content. Connect your workflow to bring this space to life.</p><button className="button-primary" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}><Sparkles size={16} />Explore workspace</button></div><div className="module-note">Frontend preview · No live records are shown here.</div></div>
}

export default function App() {
  const auth = useAuth()
  useEffect(() => {
    if (auth.status === 'anonymous') clearSkillFairSession()
  }, [auth.status])

  if (auth.status === 'loading') return <main className="auth-status-screen" role="status"><div className="auth-status-mark"><span /><span /><span /><span /></div><strong>Restoring your SkillFair session…</strong><span>Checking your secure account session.</span></main>
  if (auth.status === 'error') return <main className="auth-status-screen"><div className="auth-status-mark"><span /><span /><span /><span /></div><strong>SkillFair is temporarily unavailable</strong><span>{auth.error}</span><button className="auth-submit" onClick={auth.retryCurrentUser}>Retry connection <ArrowRight size={16} /></button></main>
  if (auth.status === 'anonymous' || !auth.user) return <AuthPage onLogin={auth.login} onRegister={auth.register} serviceError={auth.error} />

  return <AuthenticatedApp key={auth.user.id} auth={auth} user={auth.user} />
}

function AuthenticatedApp({ auth, user }: { auth: AuthContextValue; user: AuthUser }) {
  const [initialSession] = useState(() => readSkillFairSession(auth.user!.id))
  const [page, setPage] = useState<Page>(initialSession.page)
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [notice, setNotice] = useState(false)
  // Analysis and workflow locators are restored from authenticated backend records;
  // sessionStorage retains navigation/drafts only and is never the source of truth.
  const [analysisId, setAnalysisId] = useState<string | null>(null)
  const [skillCount, setSkillCount] = useState(0)
  const [analysisMode, setAnalysisMode] = useState<SkillsDiscoveryResult['analysis_mode'] | null>(null)
  const [analysisSkills, setAnalysisSkills] = useState<SkillEvidence[]>([])
  const [careerRun, setCareerRun] = useState<CareerWorkflowRun | null>(null)
  const [careerRunId, setCareerRunId] = useState<string | null>(null)
  const restoreRunId = useRef<string | null>(initialSession.runId)
  const [careerDraft, setCareerDraft] = useState<CareerWorkflowDraft>(initialSession.careerDraft)
  const [accountRestore, setAccountRestore] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [accountRestoreError, setAccountRestoreError] = useState('')
  const [restoreAttempt, setRestoreAttempt] = useState(0)
  const [logoutBusy, setLogoutBusy] = useState(false)
  const [authActionError, setAuthActionError] = useState<string | null>(null)
  useEffect(() => {
    if (auth.status !== 'authenticated') return
    let active = true
    const restoreController = new AbortController()
    setAccountRestore('loading')
    setAccountRestoreError('')
    setAnalysisId(null)
    setSkillCount(0)
    setAnalysisMode(null)
    setAnalysisSkills([])
    setCareerRun(null)
    setCareerRunId(null)
    const restore = async () => {
      try {
        const [analysisResponse, workflowResult] = await Promise.all([
          skillFairFetch('/api/skills/analyses/latest', { signal: restoreController.signal }),
          restoreSavedWorkflow(restoreRunId.current, skillFairFetch, restoreController.signal),
        ])
        if (!analysisResponse.ok) throw new Error('Saved account data could not be restored. Retry when the service is available.')
        const analysis = await analysisResponse.json() as { analysis_id?: unknown; analysis_mode?: unknown; skills?: unknown[] } | null
        if (analysis !== null && (typeof analysis.analysis_id !== 'string' || !Array.isArray(analysis.skills) || !analysis.skills.every(isSkillEvidence) || !['ai', 'local', 'development'].includes(String(analysis.analysis_mode)))) {
          throw new Error('The saved Skills Discovery record had an unsupported format.')
        }
        if (active && analysis) {
          setAnalysisId(analysis.analysis_id as string)
          setSkillCount((analysis.skills as unknown[]).length)
          setAnalysisMode(analysis.analysis_mode as SkillsDiscoveryResult['analysis_mode'])
          setAnalysisSkills(analysis.skills as SkillEvidence[])
        }
        if (active && workflowResult.kind === 'restored') {
          setCareerRun(workflowResult.run)
          setCareerRunId(workflowResult.run.run_id)
        } else if (active && workflowResult.kind === 'missing') {
          restoreRunId.current = null
          setCareerRun(null)
          setCareerRunId(null)
          writeSkillFairSession({ ...readSkillFairSession(auth.user!.id), runId: null })
          setAccountRestoreError('The saved workflow is no longer available to this account. Its reference was cleared; start a new workflow to continue.')
          setAccountRestore('error')
          return
        }
        if (active) setAccountRestore('ready')
      } catch (reason) {
        if (!active) return
        setAccountRestoreError(reason instanceof Error ? reason.message : 'Saved account data could not be restored.')
        setAccountRestore('error')
      }
    }
    void restore()
    return () => { active = false; restoreController.abort() }
  }, [auth.status, auth.user, restoreAttempt])
  useEffect(() => {
    if (auth.status !== 'authenticated') return
    writeSkillFairSession({ ownerId: auth.user!.id, analysisId, skillCount, analysisMode, page, runId: careerRunId, careerDraft })
  }, [auth.status, auth.user, analysisId, skillCount, analysisMode, page, careerRunId, careerDraft])
  function rememberAnalysis(id: string, count: number, mode: SkillsDiscoveryResult['analysis_mode'], skills: SkillEvidence[]) {
    writeSkillFairSession({ ownerId: auth.user!.id, analysisId: id, skillCount: count, analysisMode: mode, page, runId: careerRunId, careerDraft })
    setAnalysisId(id)
    setSkillCount(count)
    setAnalysisMode(mode)
    setAnalysisSkills(skills)
  }
  function handleRunChange(run: CareerWorkflowRun | null) {
    const nextRunId = run === null ? null : run.run_id
    if (run !== null && !isValidSessionLocator(nextRunId)) {
      throw new Error('The workflow service returned an invalid run reference. The result was not saved.')
    }
    writeSkillFairSession({ ownerId: auth.user!.id, analysisId, skillCount, analysisMode, page, runId: nextRunId, careerDraft })
    restoreRunId.current = nextRunId
    setCareerRun(run)
    setCareerRunId(nextRunId)
  }
  function invalidateAnalysis() {
    setAnalysisId(null)
    setSkillCount(0)
    setAnalysisMode(null)
    setAnalysisSkills([])
  }
  function handleRunExpired(runId: string) {
    if (careerRunId === runId) {
      restoreRunId.current = null
      setCareerRun(null)
      setCareerRunId(null)
    }
  }
  function continueToMatching() {
    handleRunChange(null)
    setPage('Job Matching')
  }
  async function handleLogout() {
    setLogoutBusy(true)
    setAuthActionError(null)
    try {
      await auth.logout()
      const empty = emptySkillFairSession(auth.user!.id)
      writeSkillFairSession(empty)
      restoreRunId.current = null
      setAnalysisId(null)
      setSkillCount(0)
      setAnalysisMode(null)
      setAnalysisSkills([])
      setCareerRun(null)
      setCareerRunId(null)
      setCareerDraft(empty.careerDraft)
      setPage('Overview')
    } catch (error) {
      setAuthActionError(error instanceof Error ? error.message : 'Sign out failed. Please try again.')
    } finally {
      setLogoutBusy(false)
    }
  }

  const displayName = authDisplayName(user)
  return <div className="app-shell"><Sidebar page={page === 'Job Matching' ? 'Overview' : page} setPage={setPage} collapsed={collapsed} setCollapsed={setCollapsed} mobileOpen={mobileOpen} setMobileOpen={setMobileOpen} userEmail={user.email} onLogout={handleLogout} logoutBusy={logoutBusy} /><main className="main-area"><Topbar page={page} userEmail={user.email} onMenu={() => setMobileOpen(true)} onNotice={() => setNotice(true)} onLogout={handleLogout} logoutBusy={logoutBusy} />{accountRestore === 'loading' && <div className="account-restore-banner" role="status">Restoring your saved analyses and workflows…</div>}{accountRestore === 'error' && <div className="account-restore-banner account-restore-error" role="alert">{accountRestoreError}<button type="button" onClick={() => setRestoreAttempt((attempt) => attempt + 1)}>Retry restoration</button></div>}<div className="content-wrap">{page === 'Overview' ? <Overview setPage={setPage} greetingName={displayName} /> : page === 'Impact Measurement' ? <ImpactMeasurement /> : page === 'Skills Discovery' ? <SkillsDiscovery restoredAnalysis={analysisId && analysisMode ? { analysisId, analysisMode, skills: analysisSkills } : null} onAnalysisReady={rememberAnalysis} onContinue={continueToMatching} /> : page === 'Job Matching' ? <JobMatching analysisId={analysisId} skillCount={skillCount} analysisMode={analysisMode} onAnalysisExpired={invalidateAnalysis} onBack={() => setPage('Skills Discovery')} /> : page === 'Bias Audit' ? <BiasAudit /> : page === 'Learning Pathways' ? <LearningPathways analysisId={analysisId} onAnalysisExpired={invalidateAnalysis} onSkillsDiscovery={() => setPage('Skills Discovery')} /> : page === 'Market Intelligence' ? <MarketIntelligence /> : page === 'Transferable Skills' ? <TransferableSkills analysisId={analysisId} onAnalysisExpired={invalidateAnalysis} /> : page === 'Inclusive Matching' ? <InclusiveMatching analysisId={analysisId} onAnalysisExpired={invalidateAnalysis} onSkillsDiscovery={() => setPage('Skills Discovery')} /> : page === 'Career Orchestrator' ? <CareerOrchestrator ownerId={user.id} analysisId={analysisId} run={careerRun} runId={careerRunId} onRunChange={handleRunChange} onRunExpired={handleRunExpired} onAnalysisExpired={invalidateAnalysis} draft={careerDraft} onDraftChange={setCareerDraft} onSkillsDiscovery={() => setPage('Skills Discovery')} /> : page === 'Recruiter Review' ? <RecruiterReview ownerId={user.id} analysisId={analysisId} run={careerRun} runId={careerRunId} onRunChange={handleRunChange} onRunExpired={handleRunExpired} onAnalysisExpired={invalidateAnalysis} draft={careerDraft} onDraftChange={setCareerDraft} onSkillsDiscovery={() => setPage('Skills Discovery')} onOpenOrchestrator={() => setPage('Career Orchestrator')} /> : <ModulePage page={page} />}</div></main>{(notice || authActionError) && <div className={`toast ${authActionError ? 'toast-error' : ''}`} role={authActionError ? 'alert' : 'status'}>{authActionError ? authActionError : <><Check size={16} />You’re all caught up</>}<button onClick={() => { setNotice(false); setAuthActionError(null) }} aria-label="Dismiss"><X size={15} /></button></div>}</div>
}
