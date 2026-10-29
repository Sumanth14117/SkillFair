import { useState } from 'react'
import {
  Activity, ArrowDownRight, ArrowRight, ArrowUpRight, Bell, BriefcaseBusiness, ChartNoAxesCombined,
  Check, ChevronDown, ChevronLeft, ChevronRight, CircleHelp, ClipboardCheck, Command, FileSearch,
  GraduationCap, LayoutDashboard, Menu, MoreHorizontal, Plus, Search, ScanSearch, Settings2,
  SlidersHorizontal, Sparkles, UsersRound, X,
} from 'lucide-react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { activities, candidates, pipelineData, sections } from './data/demoData'
import SkillsDiscovery from './components/SkillsDiscovery'
import JobMatching from './components/JobMatching'
import type { SkillEvidence, SkillsDiscoveryResult } from './types/skills'

const iconMap = { LayoutDashboard, Sparkles, ChartNoAxesCombined, GraduationCap, UsersRound, ScanSearch, ClipboardCheck }
type Page = typeof sections[number]['name'] | 'Job Matching'

function Sidebar({ page, setPage, collapsed, setCollapsed, mobileOpen, setMobileOpen }: {
  page: Page; setPage: (page: Page) => void; collapsed: boolean; setCollapsed: (v: boolean) => void; mobileOpen: boolean; setMobileOpen: (v: boolean) => void
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
        return <button key={item.name} className={`nav-item ${page === item.name ? 'nav-active' : ''}`} onClick={() => { setPage(item.name); setMobileOpen(false) }} title={collapsed ? item.name : undefined} aria-current={page === item.name ? 'page' : undefined}><Icon size={18} strokeWidth={1.8} /><span>{item.name}</span>{item.name === 'Recruiter Review' && !collapsed && <span className="nav-count">8</span>}</button>
      })}</nav>
      {!collapsed && <div className="sidebar-bottom"><div className="plan-card"><div className="plan-icon"><Sparkles size={15} /></div><b>Your team is growing</b><p>You’ve reviewed 72% more candidates this month.</p><button onClick={() => setPage('Market Intelligence')}>Explore insights <ArrowRight size={14} /></button></div><button className="nav-item" onClick={() => setPage('Overview')}><CircleHelp size={18} /><span>Help center</span></button><button className="nav-item" onClick={() => setPage('Overview')}><Settings2 size={18} /><span>Settings</span></button></div>}
      {!collapsed && <div className="sidebar-footer"><div className="mini-avatar">AM</div><div className="footer-user"><b>Alex Morgan</b><span>Recruiter</span></div><MoreHorizontal size={19} className="muted-icon" /></div>}
    </aside>
  </>
}

function Topbar({ page, onMenu, onNotice }: { page: Page; onMenu: () => void; onNotice: () => void }) {
  return <header className="topbar"><button className="mobile-menu" onClick={onMenu} aria-label="Open navigation"><Menu size={20} /></button><div className="breadcrumb"><span>Workspace</span><ChevronRight size={14} /><b>{page}</b></div><div className="top-actions"><label className="search-box"><Search size={16} /><input aria-label="Search" placeholder="Search anything..." /><kbd><Command size={11} /> K</kbd></label><button className="icon-button notification-button" aria-label="Notifications" onClick={onNotice}><Bell size={18} /><i /></button><div className="top-divider" /><button className="profile-control"><div className="mini-avatar">AM</div><span>Alex Morgan</span><ChevronDown size={15} className="muted-icon" /></button></div></header>
}

function MetricCard({ label, value, change, icon: Icon, tone, trend = 'up' }: { label: string; value: string; change: string; icon: typeof UsersRound; tone: string; trend?: 'up' | 'down' }) {
  const Trend = trend === 'up' ? ArrowUpRight : ArrowDownRight
  return <article className="metric-card"><div className="metric-top"><span>{label}</span><div className={`metric-icon ${tone}`}><Icon size={17} /></div></div><div className="metric-bottom"><strong>{value}</strong><span className="metric-change"><Trend size={14} />{change}</span></div><span className="metric-period">vs. last month</span></article>
}

function PipelineChart() {
  return <div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><AreaChart data={pipelineData} margin={{ top: 12, right: 8, left: -20, bottom: 0 }}>
    <defs><linearGradient id="reviewFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#6786f5" stopOpacity={0.16} /><stop offset="100%" stopColor="#6786f5" stopOpacity={0} /></linearGradient><linearGradient id="matchFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#9475e8" stopOpacity={0.12} /><stop offset="100%" stopColor="#9475e8" stopOpacity={0} /></linearGradient></defs>
    <CartesianGrid stroke="#edf0f6" vertical={false} /><XAxis dataKey="week" axisLine={false} tickLine={false} tick={{ fill: '#949bb0', fontSize: 11 }} dy={10} /><YAxis axisLine={false} tickLine={false} tick={{ fill: '#949bb0', fontSize: 11 }} ticks={[0, 20, 40, 60]} /><Tooltip contentStyle={{ border: '1px solid #eceef5', borderRadius: 12, boxShadow: '0 8px 24px #17203b12', fontSize: 12 }} />
    <Area type="monotone" dataKey="reviewed" name="Candidates reviewed" stroke="#6886f5" strokeWidth={2.5} fill="url(#reviewFill)" activeDot={{ r: 5, strokeWidth: 0 }} /><Area type="monotone" dataKey="matched" name="Potential matches" stroke="#9674e8" strokeWidth={2.5} fill="url(#matchFill)" activeDot={{ r: 5, strokeWidth: 0 }} />
  </AreaChart></ResponsiveContainer></div>
}

function ActivityFeed() {
  return <div className="activity-list">{activities.map((item) => <div className="activity-row" key={item.name}><div className={`avatar avatar-${item.tone}`}>{item.initials}</div><div className="activity-copy"><p><b>{item.name}</b> {item.action}</p><span>{item.time}</span></div><button className="dots-button" aria-label={`More activity for ${item.name}`}><MoreHorizontal size={17} /></button></div>)}</div>
}

function CandidateCard({ person }: { person: typeof candidates[number] }) {
  return <article className="candidate-card"><div className="candidate-head"><div className={`avatar avatar-${person.tone}`}>{person.initials}</div><div className="candidate-id"><b>{person.name}</b><span>{person.role} <i>·</i> {person.location}</span></div><button className="dots-button" aria-label={`More options for ${person.name}`}><MoreHorizontal size={18} /></button></div><div className="candidate-match"><div className="match-ring">{person.score}<small>%</small></div><span>{person.note}</span><span className="verified-pill"><Check size={12} />Skills verified</span></div><div className="skill-tags">{person.skills.map((skill) => <span key={skill}>{skill}</span>)}</div><button className="candidate-action">View profile <ArrowRight size={15} /></button></article>
}

function Overview({ setPage }: { setPage: (page: Page) => void }) {
  return <>
    <section className="welcome-row"><div><div className="eyebrow"><span className="eyebrow-line" /> TUESDAY, SEPTEMBER 17, 2024</div><h1>Good morning, Alex <span>✦</span></h1><p>Here’s what’s happening across your skills-first hiring pipeline.</p></div><div className="welcome-actions"><button className="button-secondary" onClick={() => setPage('Market Intelligence')}><FileSearch size={16} />Analyze a job</button><button className="button-primary" onClick={() => setPage('Skills Discovery')}><Plus size={17} />Discover skills</button></div></section>
    <section className="metrics-grid" aria-label="Recruiting summary"><MetricCard label="Candidates reviewed" value="1,284" change="18.6%" icon={UsersRound} tone="metric-blue" /><MetricCard label="Potential matches" value="342" change="12.4%" icon={Sparkles} tone="metric-violet" /><MetricCard label="Skills identified" value="2,891" change="24.8%" icon={Activity} tone="metric-cyan" /><MetricCard label="Awaiting review" value="28" change="6.2%" icon={ClipboardCheck} tone="metric-amber" trend="down" /></section>
    <section className="insights-grid"><article className="panel pipeline-panel"><div className="panel-heading"><div><h2>Candidate pipeline</h2><p>Skills-based activity over time</p></div><button className="select-button">Last 7 weeks <ChevronDown size={14} /></button></div><div className="chart-legend"><span><i className="legend-blue" />Candidates reviewed <b>275</b></span><span><i className="legend-violet" />Potential matches <b>178</b></span></div><PipelineChart /></article><article className="panel activity-panel"><div className="panel-heading"><div><h2>Recent activity</h2><p>Latest updates from your team</p></div><button className="text-link">View all <ArrowRight size={14} /></button></div><ActivityFeed /><button className="activity-footer" onClick={() => setPage('Recruiter Review')}>Go to review queue <ArrowRight size={15} /></button></article></section>
    <section className="candidates-section"><div className="section-heading"><div><h2>Top skill matches</h2><p>People matched on what they can do</p></div><div className="section-controls"><button className="filter-button"><SlidersHorizontal size={15} />Filters</button><button className="text-link" onClick={() => setPage('Recruiter Review')}>View all candidates <ArrowRight size={14} /></button></div></div><div className="candidate-grid">{candidates.map((person) => <CandidateCard key={person.name} person={person} />)}</div></section>
    <div className="demo-note">Illustrative demo data · No live candidate records or integrations are connected.</div>
  </>
}

function ModulePage({ page }: { page: Page }) {
  const descriptions: Record<Page, string> = {
    Overview: '', 'Skills Discovery': 'Explore capabilities from candidate experience and map them to your roles.',
    'Market Intelligence': 'Understand the skills landscape and discover where talent is growing.',
    'Learning Pathways': 'See learning journeys that can help candidates build skills for their next role.',
    'Inclusive Matching': 'Review skills-based recommendations designed to make opportunity more accessible.',
    'Bias Audit': 'Inspect hiring criteria and monitor fairness across your review process.',
    'Recruiter Review': 'Review candidate profiles and keep your hiring workflow moving.',
    'Job Matching': 'Compare candidate capabilities with the skills a role needs.',
  }
  const Icon = page === 'Job Matching' ? BriefcaseBusiness : iconMap[sections.find((item) => item.name === page)!.icon as keyof typeof iconMap]
  return <div className="module-page"><div className="module-icon"><Icon size={23} /></div><span className="eyebrow"><span className="eyebrow-line" /> SKILLFAIR WORKSPACE</span><h1>{page}</h1><p>{descriptions[page]}</p><div className="module-empty"><div className="empty-orbit"><Icon size={24} /></div><h2>Your {page.toLowerCase()} workspace is ready</h2><p>This presentation-ready preview uses illustrative content. Connect your workflow to bring this space to life.</p><button className="button-primary" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}><Sparkles size={16} />Explore workspace</button></div><div className="module-note">Frontend preview · No AI agents or SAP integrations are connected.</div></div>
}

export default function App() {
  const [page, setPage] = useState<Page>('Overview')
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [notice, setNotice] = useState(false)
  const [candidateSkills, setCandidateSkills] = useState<SkillEvidence[]>([])
  const [analysisMode, setAnalysisMode] = useState<SkillsDiscoveryResult['analysis_mode'] | null>(null)
  function continueToMatching(skills: SkillEvidence[], mode: SkillsDiscoveryResult['analysis_mode']) {
    setCandidateSkills(skills)
    setAnalysisMode(mode)
    setPage('Job Matching')
  }
  return <div className="app-shell"><Sidebar page={page === 'Job Matching' ? 'Overview' : page} setPage={setPage} collapsed={collapsed} setCollapsed={setCollapsed} mobileOpen={mobileOpen} setMobileOpen={setMobileOpen} /><main className="main-area"><Topbar page={page} onMenu={() => setMobileOpen(true)} onNotice={() => setNotice(true)} /><div className="content-wrap">{page === 'Overview' ? <Overview setPage={setPage} /> : page === 'Skills Discovery' ? <SkillsDiscovery onContinue={continueToMatching} /> : page === 'Job Matching' ? <JobMatching candidateSkills={candidateSkills} analysisMode={analysisMode} onBack={() => setPage('Skills Discovery')} /> : <ModulePage page={page} />}</div></main>{notice && <div className="toast" role="status"><Check size={16} />You’re all caught up<button onClick={() => setNotice(false)} aria-label="Dismiss"><X size={15} /></button></div>}</div>
}
