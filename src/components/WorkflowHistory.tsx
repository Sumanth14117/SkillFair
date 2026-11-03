import { Clock3, History, LoaderCircle, RefreshCw } from 'lucide-react'
import type { WorkflowHistoryItem, WorkflowHistoryState } from '../workflowHistory'
import '../styles/workflow-history.css'

export type WorkflowHistoryAction = { runId: string; status: 'loading' | 'error'; message?: string } | null

type Props = {
  state: WorkflowHistoryState
  action: WorkflowHistoryAction
  onRefresh: () => void
  onContinue: (item: WorkflowHistoryItem) => void
}

const STATUS_LABELS: Record<WorkflowHistoryItem['workflow_status'], string> = {
  awaiting_human_review: 'Pending human review',
  revise_required: 'Revised criteria needed',
  stopped: 'Stopped',
  complete: 'Review complete',
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

export default function WorkflowHistory({ state, action, onRefresh, onContinue }: Props) {
  return <section className="panel career-history" aria-labelledby="career-history-title">
    <header className="career-history-header">
      <div className="career-history-title-icon"><History size={17} /></div>
      <div><span className="career-stage-kicker">SAVED IN YOUR ACCOUNT</span><h2 id="career-history-title">Recent Workflows</h2><p>Open a specific saved workflow to review or continue it.</p></div>
      <button type="button" className="career-history-refresh" onClick={onRefresh} aria-label="Refresh workflow history" disabled={state.status === 'loading'}><RefreshCw size={14} />Refresh</button>
    </header>

    {state.status === 'loading' && <div className="career-history-message" role="status"><LoaderCircle size={16} className="spin-icon" />Loading saved workflows…</div>}
    {state.status === 'error' && <div className="career-history-message career-history-error" role="alert"><span>{state.message}</span><button type="button" className="button-secondary" onClick={onRefresh}>Retry</button></div>}
    {state.status === 'ready' && state.items.length === 0 && <div className="career-history-empty"><div><History size={18} /></div><b>No saved workflows yet</b><p>Workflows you create will appear here for this account.</p></div>}
    {state.status === 'ready' && state.items.length > 0 && <div className="career-history-list">
      {state.items.map((item) => {
        const itemAction = action?.runId === item.run_id ? action : null
        return <article className="career-history-item" key={item.run_id}>
          <div className="career-history-item-main">
            <div className="career-history-item-heading"><h3>{item.job_title}</h3><span className={`career-history-status status-${item.workflow_status}`}>{STATUS_LABELS[item.workflow_status]}</span></div>
            <div className="career-history-dates"><span><Clock3 size={12} />Created {formatDate(item.created_at)}</span><span>Updated {formatDate(item.updated_at)}</span></div>
            <small className="career-history-run-id">Workflow ID · {item.run_id}</small>
            {itemAction?.status === 'error' && <p className="career-history-item-error" role="alert">{itemAction.message}</p>}
          </div>
          <button type="button" className="button-secondary career-history-continue" onClick={() => onContinue(item)} disabled={itemAction?.status === 'loading'}>
            {itemAction?.status === 'loading' ? <><LoaderCircle size={13} className="spin-icon" />Opening…</> : itemAction?.status === 'error' ? 'Retry workflow' : 'Continue Workflow'}
          </button>
        </article>
      })}
    </div>}
  </section>
}
