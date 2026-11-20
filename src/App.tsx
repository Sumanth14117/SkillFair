import { useEffect, useRef, useState } from 'react'
import {
  Activity, ArrowLeftRight, ArrowRight, BriefcaseBusiness, ChartNoAxesCombined,
  ChevronLeft, ChevronRight, ClipboardCheck, Database,
  CircleHelp, GraduationCap, LayoutDashboard, LogOut, Menu, ScanSearch,
  Settings, Sparkles, UsersRound, X,
} from 'lucide-react'
import { sections } from './data/demoData'
import OverviewDashboard from './components/OverviewDashboard'
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
import DataManagement from './components/DataManagement'
import HelpCenter from './components/HelpCenter'
import SettingsPage from './components/SettingsPage'
import { GlobalSearch, NotificationsMenu } from './components/NavigationUtilities'
import AuthPage from './components/AuthPage'
import { authDisplayName, type AuthUser } from './authClient'
import { skillFairFetch } from './apiClient'
import { useAuth, type AuthContextValue } from './authContext'
import type { SkillEvidence, SkillsDiscoveryResult } from './types/skills'
import { clearSkillFairSession, emptySkillFairSession, isValidSessionLocator, readSkillFairSession, writeSkillFairSession, type AppPage } from './sessionState'
import { restoreSavedWorkflow } from './workflowRestoration'
import type { SearchResult } from './navigationClient'
import { readUserPreferences } from './userPreferences'

const iconMap = { LayoutDashboard, Sparkles, ChartNoAxesCombined, GraduationCap, UsersRound, ScanSearch, ClipboardCheck, ArrowLeftRight, BriefcaseBusiness, Activity, Database, Settings, CircleHelp }
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

function Sidebar({ page, setPage, collapsed, setCollapsed, mobileOpen, setMobileOpen, userEmail, displayName, onLogout, logoutBusy }: {
  page: Page; setPage: (page: Page) => void; collapsed: boolean; setCollapsed: (v: boolean) => void; mobileOpen: boolean; setMobileOpen: (v: boolean) => void; userEmail: string; displayName: string; onLogout: () => void; logoutBusy: boolean
}) {
  return <>
    {mobileOpen && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
    <aside className={`sidebar ${collapsed ? 'sidebar-collapsed' : ''} ${mobileOpen ? 'sidebar-open' : ''}`}>
      <div className="brand-row"><div className="brand-mark"><span /><span /><span /><span /></div>{!collapsed && <span className="brand-name">skillfair<span className="brand-dot">.</span></span>}{!collapsed && <button className="sidebar-collapse" onClick={() => setCollapsed(true)} aria-label="Collapse sidebar"><ChevronLeft size={16} /></button>}</div>
      {collapsed && <button className="expand-sidebar" onClick={() => setCollapsed(false)} aria-label="Expand sidebar"><ChevronRight size={16} /></button>}
      <div className="workspace-select" aria-label="Single-account workspace"><div className="workspace-icon">S</div>{!collapsed && <div className="workspace-text"><b>Personal workspace</b><span>One signed-in account · no organization switching</span></div>}</div>
      <div className="nav-label">WORKSPACE</div>
      <nav className="primary-nav" aria-label="Main navigation">{sections.map((item) => {
        const Icon = iconMap[item.icon as keyof typeof iconMap]
        return <button key={item.name} className={`nav-item ${page === item.name ? 'nav-active' : ''}`} onClick={() => { setPage(item.name); setMobileOpen(false) }} title={collapsed ? item.name : undefined} aria-current={page === item.name ? 'page' : undefined}><Icon size={18} strokeWidth={1.8} /><span>{item.name}</span></button>
      })}</nav>
      {!collapsed && <div className="sidebar-bottom"><div className="plan-card"><div className="plan-icon"><Sparkles size={15} /></div><b>Account workspace</b><p>Overview uses saved records from your account.</p><button onClick={() => setPage('Market Intelligence')}>Open Market Intelligence <ArrowRight size={14} /></button></div></div>}
      {!collapsed && <div className="sidebar-footer"><div className="mini-avatar">{displayName.slice(0, 1).toUpperCase()}</div><div className="footer-user"><b title={displayName}>{displayName}</b><span title={userEmail}>{userEmail}</span></div><button className="sidebar-logout" onClick={onLogout} disabled={logoutBusy} aria-label="Sign out" title="Sign out"><LogOut size={17} /></button></div>}
    </aside>
  </>
}

function Topbar({ page, userEmail, displayName, onMenu, onLogout, logoutBusy, onSearchSelect, onOpenWorkflow }: { page: Page; userEmail: string; displayName: string; onMenu: () => void; onLogout: () => void; logoutBusy: boolean; onSearchSelect: (result: SearchResult) => void; onOpenWorkflow: (workflowId: string) => void }) {
  return <header className="topbar"><button className="mobile-menu" onClick={onMenu} aria-label="Open navigation"><Menu size={20} /></button><div className="breadcrumb"><span>Workspace</span><ChevronRight size={14} /><b>{page}</b></div><div className="top-actions"><GlobalSearch onSelect={onSearchSelect} /><NotificationsMenu onOpenWorkflow={onOpenWorkflow} /><button className="profile-control" onClick={onLogout} disabled={logoutBusy} aria-label={`Sign out ${displayName}`} title={`Signed in as ${userEmail}`}><div className="mini-avatar">{displayName.slice(0, 1).toUpperCase()}</div><span>{displayName}</span><LogOut size={15} className="muted-icon" /></button></div></header>
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
    'Job Matching': 'Compare candidate capabilities with the skills a role needs.', 'Data Management': 'Review, export, and remove records saved to your account.', Settings: '', 'Help Center': '',
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
  const [displayName, setDisplayName] = useState(() => readUserPreferences(user.id).displayName || authDisplayName(user))
  const [pendingAuditId, setPendingAuditId] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
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
  const searchController = useRef<AbortController | null>(null)
  useEffect(() => () => searchController.current?.abort(), [])
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

  async function openSearchResult(result: SearchResult) {
    if (result.record_type === 'workflow') {
      restoreRunId.current = result.record_id
      setCareerRun(null); setCareerRunId(null)
      writeSkillFairSession({ ...readSkillFairSession(user.id), runId: result.record_id, page: 'Career Orchestrator' })
      setPage('Career Orchestrator')
      setRestoreAttempt((attempt) => attempt + 1)
      return
    }
    if (result.record_type === 'bias_audit') { setPendingAuditId(result.record_id); setPage('Bias Audit'); return }
    searchController.current?.abort()
    const controller = new AbortController(); searchController.current = controller
    try {
      const response = await skillFairFetch(`/api/skills/analyses/${encodeURIComponent(result.record_id)}`, { signal: controller.signal })
      if (!response.ok) throw new Error(response.status === 404 ? 'That saved analysis is no longer available to this account.' : 'The saved analysis could not be opened.')
      const analysis = await response.json() as { analysis_id?: unknown; analysis_mode?: unknown; skills?: unknown[] }
      if (controller.signal.aborted || typeof analysis.analysis_id !== 'string' || !Array.isArray(analysis.skills) || !analysis.skills.every(isSkillEvidence) || !['ai', 'local', 'development'].includes(String(analysis.analysis_mode))) return
      rememberAnalysis(analysis.analysis_id, analysis.skills.length, analysis.analysis_mode as SkillsDiscoveryResult['analysis_mode'], analysis.skills)
      setPage('Skills Discovery')
    } catch (reason) {
      if (!controller.signal.aborted) setAuthActionError(reason instanceof Error ? reason.message : 'The saved analysis could not be opened.')
    }
  }
  function openWorkflowNotification(workflowId: string) {
    restoreRunId.current = workflowId
    setCareerRun(null); setCareerRunId(null)
    writeSkillFairSession({ ...readSkillFairSession(user.id), runId: workflowId, page: 'Career Orchestrator' })
    setPage('Career Orchestrator')
    setRestoreAttempt((attempt) => attempt + 1)
  }

  return <div className="app-shell"><Sidebar page={page === 'Job Matching' ? 'Overview' : page} setPage={setPage} collapsed={collapsed} setCollapsed={setCollapsed} mobileOpen={mobileOpen} setMobileOpen={setMobileOpen} userEmail={user.email} displayName={displayName} onLogout={handleLogout} logoutBusy={logoutBusy} /><main className="main-area"><Topbar page={page} userEmail={user.email} displayName={displayName} onMenu={() => setMobileOpen(true)} onLogout={handleLogout} logoutBusy={logoutBusy} onSearchSelect={(result) => void openSearchResult(result)} onOpenWorkflow={openWorkflowNotification} />{accountRestore === 'loading' && <div className="account-restore-banner" role="status">Restoring your saved analyses and workflows…</div>}{accountRestore === 'error' && <div className="account-restore-banner account-restore-error" role="alert">{accountRestoreError}<button type="button" onClick={() => setRestoreAttempt((attempt) => attempt + 1)}>Retry restoration</button></div>}<div className="content-wrap">{page === 'Overview' ? <OverviewDashboard ownerId={user.id} greetingName={displayName} onDiscoverSkills={() => setPage('Skills Discovery')} onViewWorkflows={() => setPage('Career Orchestrator')} /> : page === 'Settings' ? <SettingsPage key={user.id} ownerId={user.id} email={user.email} onDisplayNameChange={setDisplayName} /> : page === 'Help Center' ? <HelpCenter /> : page === 'Data Management' ? <DataManagement key={user.id} ownerId={user.id} /> : page === 'Impact Measurement' ? <ImpactMeasurement /> : page === 'Skills Discovery' ? <SkillsDiscovery restoredAnalysis={analysisId && analysisMode ? { analysisId, analysisMode, skills: analysisSkills } : null} onAnalysisReady={rememberAnalysis} onContinue={continueToMatching} /> : page === 'Job Matching' ? <JobMatching analysisId={analysisId} skillCount={skillCount} analysisMode={analysisMode} onAnalysisExpired={invalidateAnalysis} onBack={() => setPage('Skills Discovery')} /> : page === 'Bias Audit' ? <BiasAudit key={user.id} initialAuditId={pendingAuditId} /> : page === 'Learning Pathways' ? <LearningPathways analysisId={analysisId} onAnalysisExpired={invalidateAnalysis} onSkillsDiscovery={() => setPage('Skills Discovery')} /> : page === 'Market Intelligence' ? <MarketIntelligence /> : page === 'Transferable Skills' ? <TransferableSkills analysisId={analysisId} onAnalysisExpired={invalidateAnalysis} /> : page === 'Inclusive Matching' ? <InclusiveMatching analysisId={analysisId} onAnalysisExpired={invalidateAnalysis} onSkillsDiscovery={() => setPage('Skills Discovery')} /> : page === 'Career Orchestrator' ? <CareerOrchestrator ownerId={user.id} analysisId={analysisId} run={careerRun} runId={careerRunId} onRunChange={handleRunChange} onRunExpired={handleRunExpired} onAnalysisExpired={invalidateAnalysis} draft={careerDraft} onDraftChange={setCareerDraft} onSkillsDiscovery={() => setPage('Skills Discovery')} /> : page === 'Recruiter Review' ? <RecruiterReview ownerId={user.id} analysisId={analysisId} run={careerRun} runId={careerRunId} onRunChange={handleRunChange} onRunExpired={handleRunExpired} onAnalysisExpired={invalidateAnalysis} draft={careerDraft} onDraftChange={setCareerDraft} onSkillsDiscovery={() => setPage('Skills Discovery')} onOpenOrchestrator={() => setPage('Career Orchestrator')} /> : <ModulePage page={page} />}</div></main>{authActionError && <div className="toast toast-error" role="alert">{authActionError}<button onClick={() => setAuthActionError(null)} aria-label="Dismiss"><X size={15} /></button></div>}</div>
}
