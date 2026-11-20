import { Activity, ArrowRight, BriefcaseBusiness, CheckCircle2, ClipboardCheck, Clock3, RefreshCw, Sparkles, UsersRound } from 'lucide-react'
import type { DashboardLoadState } from '../overviewDashboard'
import type { DashboardOverview } from '../types/dashboard'
import '../styles/overview-dashboard.css'

type Props = {
  state: DashboardLoadState
  greetingName: string
  onRetry: () => void
  onDiscoverSkills: () => void
  onViewWorkflows: () => void
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Date unavailable' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function statusLabel(status: string): string {
  return ({
    awaiting_human_review: 'Awaiting human review',
    revise_required: 'Revision required',
    stopped: 'Stopped',
    complete: 'Complete',
    pending: 'Pending',
    proceed: 'Proceed',
    revise: 'Revise',
    stop: 'Stop',
  } as Record<string, string>)[status] ?? 'Status unavailable'
}

function Metric({ label, value, detail, icon: Icon }: { label: string; value: number; detail: string; icon: typeof Activity }) {
  return <article className="overview-metric"><div className="overview-metric-head"><span>{label}</span><span className="overview-metric-icon"><Icon size={17} /></span></div><strong>{value.toLocaleString()}</strong><small>{detail}</small></article>
}

function Distribution({ title, items, kind }: { title: string; items: { status?: string; decision?: string; count: number }[]; kind: 'status' | 'decision' }) {
  const total = items.reduce((sum, item) => sum + item.count, 0)
  return <article className="panel overview-distribution">
    <div className="panel-heading"><div><h2>{title}</h2><p>Counted from saved account workflows</p></div></div>
    {items.length === 0 ? <p className="overview-muted">No workflow records to summarize.</p> : <div className="overview-bars" role="list" aria-label={title}>
      {items.map((item) => {
        const value = kind === 'status' ? item.status : item.decision
        const percentage = total ? Math.round(item.count / total * 100) : 0
        return <div className="overview-bar-row" role="listitem" key={value}>
          <div className="overview-bar-label"><span>{statusLabel(value ?? '')}</span><b>{item.count}</b></div>
          <div className="overview-bar-track" aria-label={`${statusLabel(value ?? '')}: ${item.count} of ${total}`}><i style={{ width: `${percentage}%` }} /></div>
        </div>
      })}
    </div>}
  </article>
}

function ActivityList({ data }: { data: DashboardOverview }) {
  return <article className="panel overview-activity">
    <div className="panel-heading"><div><h2>Recent activity</h2><p>Persisted workflow and review timestamps</p></div></div>
    {data.recent_activity.length === 0 ? <p className="overview-muted">No saved workflow activity yet.</p> : <ol className="overview-activity-list">
      {data.recent_activity.map((event, index) => <li key={`${event.workflow_id}-${event.event_type}-${event.occurred_at}-${index}`}>
        <span className="overview-activity-icon">{event.event_type === 'workflow_created' ? <BriefcaseBusiness size={15} /> : event.event_type === 'review_started' ? <Clock3 size={15} /> : <CheckCircle2 size={15} />}</span>
        <span className="overview-activity-copy"><b>{event.description}</b><small>{formatDate(event.occurred_at)} · {statusLabel(event.workflow_status)}</small></span>
      </li>)}
    </ol>}
  </article>
}

function RecentWorkflows({ data, onViewWorkflows }: { data: DashboardOverview; onViewWorkflows: () => void }) {
  return <section className="overview-recent">
    <div className="overview-section-heading"><div><h2>Recent workflows</h2><p>Saved job workflows for this account, newest first.</p></div><button type="button" className="text-link" onClick={onViewWorkflows}>View workflow history <ArrowRight size={14} /></button></div>
    {data.recent_workflows.length === 0 ? <div className="panel overview-empty"><div className="overview-empty-icon"><BriefcaseBusiness size={20} /></div><h3>No saved workflows yet</h3><p>Start a Career Orchestrator workflow to see its persisted status and review activity here.</p><button type="button" className="button-secondary" onClick={onViewWorkflows}>Open Career Orchestrator</button></div> : <div className="overview-workflow-list">
      {data.recent_workflows.map((workflow) => <article className="panel overview-workflow" key={workflow.run_id}>
        <div className="overview-workflow-main"><span className="overview-workflow-icon"><BriefcaseBusiness size={16} /></span><div><h3>{workflow.job_title}</h3><small>Created {formatDate(workflow.created_at)} · Updated {formatDate(workflow.updated_at)}</small></div></div>
        <div className="overview-workflow-status"><span>{statusLabel(workflow.workflow_status)}</span><small>Review: {statusLabel(workflow.review_decision)}</small></div>
      </article>)}
    </div>}
  </section>
}

export default function OverviewDashboardView({ state, greetingName, onRetry, onDiscoverSkills, onViewWorkflows }: Props) {
  return <div className="overview-page">
    <section className="welcome-row"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCOUNT OVERVIEW</div><h1>Good morning, {greetingName} <span>✦</span></h1><p>Saved analyses, workflows, and review activity from your account.</p></div><div className="welcome-actions"><button className="button-secondary" onClick={onRetry}><RefreshCw size={15} />Refresh data</button><button className="button-secondary" onClick={onViewWorkflows}><BriefcaseBusiness size={16} />Career workflows</button><button className="button-primary" onClick={onDiscoverSkills}><Sparkles size={17} />Discover skills</button></div></section>
    {state.status === 'loading' ? <div className="panel overview-state" role="status"><RefreshCw size={19} className="overview-spinner" /><h2>Loading your account overview</h2><p>Retrieving saved records for this account…</p></div> : state.status === 'error' ? <div className="panel overview-state overview-error" role="alert"><Activity size={20} /><h2>Overview is unavailable</h2><p>{state.message}</p><button type="button" className="button-secondary" onClick={onRetry}>Retry</button></div> : <>
      {state.data.summary.total_workflows === 0 && state.data.summary.saved_analyses === 0 ? <div className="panel overview-empty overview-first-run"><div className="overview-empty-icon"><Sparkles size={20} /></div><h2>Your account dashboard is ready</h2><p>No saved analyses or workflows are available yet. Create a Skills Discovery analysis or start a workflow to populate this dashboard.</p><button type="button" className="button-primary" onClick={onDiscoverSkills}>Discover skills</button></div> : null}
      <section className="metrics-grid" aria-label="Saved account metrics">
        <Metric label="Saved analyses" value={state.data.summary.saved_analyses} detail="Persisted analyses" icon={Activity} />
        <Metric label="Total workflows" value={state.data.summary.total_workflows} detail="Active, non-expired records" icon={UsersRound} />
        <Metric label="Pending reviews" value={state.data.summary.pending_reviews} detail="No decision recorded" icon={ClipboardCheck} />
        <Metric label="Completed reviews" value={state.data.summary.completed_reviews} detail="Decision recorded" icon={CheckCircle2} />
      </section>
      <section className="overview-panels"><Distribution title="Workflow status" items={state.data.workflow_status_distribution} kind="status" /><Distribution title="Review decisions" items={state.data.review_decision_distribution} kind="decision" /><ActivityList data={state.data} /></section>
      <RecentWorkflows data={state.data} onViewWorkflows={onViewWorkflows} />
      <p className="overview-data-note">Counts include only this signed-in account’s non-expired saved records. “Saved analyses” counts analysis records, not unique people. No candidate profiles, matching outcomes, or business impact are inferred.</p>
    </>}
  </div>
}
