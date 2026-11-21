import { useEffect, useState } from 'react'
import { AlertCircle, Download, FileText, LoaderCircle, RefreshCw, Trash2 } from 'lucide-react'
import {
  accountDataError,
  accountDataLoading,
  confirmAndDeleteAccountRecord,
  downloadAccountExport,
  loadAccountData,
  visibleAccountData,
  type AccountDataState,
} from '../accountData'
import '../styles/data-management.css'

type Props = { ownerId: string }
type Notice = { kind: 'success' | 'error'; text: string } | null
const PAGE_SIZE = 25

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Date unavailable' : date.toLocaleString()
}

export default function DataManagement({ ownerId }: Props) {
  const [state, setState] = useState<AccountDataState>(() => accountDataLoading(ownerId))
  const [offset, setOffset] = useState(0)
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice>(null)

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setState(accountDataLoading(ownerId))
    loadAccountData(ownerId, offset, controller.signal).then((next) => {
      if (active) setState(next)
    }).catch((error: unknown) => {
      if (!active || controller.signal.aborted) return
      setState(accountDataError(ownerId, error instanceof Error ? error.message : 'Account data could not be loaded.'))
    })
    return () => { active = false; controller.abort() }
  }, [ownerId, offset, reload])

  const data = visibleAccountData(state, ownerId)

  async function exportData() {
    setBusy('export')
    setNotice(null)
    try {
      const result = await downloadAccountExport()
      const url = URL.createObjectURL(result.blob)
      const link = document.createElement('a')
      link.href = url
      link.download = result.filename
      link.click()
      URL.revokeObjectURL(url)
      setNotice({ kind: 'success', text: 'Your account export is ready.' })
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'The export could not be prepared.' })
    } finally {
      setBusy(null)
    }
  }

  async function deleteRecord(kind: 'analysis' | 'workflow', id: string) {
    setBusy(`${kind}:${id}`)
    setNotice(null)
    try {
      const deleted = await confirmAndDeleteAccountRecord(kind, id, (message) => window.confirm(message))
      if (!deleted) return
      setNotice({ kind: 'success', text: `The ${kind} was deleted.` })
      if (data && offset > 0 && ((kind === 'analysis' ? data.analyses : data.workflows).length === 1)) setOffset(Math.max(0, offset - PAGE_SIZE))
      else setReload((value) => value + 1)
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : `The ${kind} could not be deleted.` })
    } finally {
      setBusy(null)
    }
  }

  return <DataManagementView
    ownerId={ownerId} state={state} offset={offset} busy={busy} notice={notice}
    onExport={() => void exportData()}
    onRetry={() => setReload((value) => value + 1)}
    onDelete={(kind, id) => void deleteRecord(kind, id)}
    onPage={setOffset}
  />
}

type DataManagementViewProps = {
  ownerId: string
  state: AccountDataState
  offset: number
  busy: string | null
  notice: Notice
  onExport: () => void
  onRetry: () => void
  onDelete: (kind: 'analysis' | 'workflow', id: string) => void
  onPage: (offset: number) => void
}

export function DataManagementView({ ownerId, state, offset, busy, notice, onExport, onRetry, onDelete, onPage }: DataManagementViewProps) {
  const data = visibleAccountData(state, ownerId)
  return <section className="data-management" aria-labelledby="data-management-title">
    <header className="data-management-header">
      <div><span className="eyebrow"><span className="eyebrow-line" /> ACCOUNT CONTROLS</span><h1 id="data-management-title">Data Management</h1><p>Review, export, or remove the SkillFair records saved to your account.</p></div>
      <button type="button" className="data-export-button" onClick={onExport} disabled={busy !== null}><Download size={16} />{busy === 'export' ? 'Preparing export…' : 'Export account data'}</button>
    </header>

    <div className="data-privacy-note"><b>Your privacy</b><p>Original resume files and complete extracted resume text are not stored. The export includes saved skill results and their PII-minimized evidence excerpts, workflow inputs and decisions, and stored account-linked measurements. It excludes password hashes, session and CSRF token material, internal database IDs, and free-text review notes. Automated PII detection is limited.</p></div>

    {notice && <div className={`data-notice ${notice.kind}`} role={notice.kind === 'error' ? 'alert' : 'status'}>{notice.kind === 'error' ? <AlertCircle size={16} /> : <FileText size={16} />}{notice.text}</div>}
    {state.ownerId === ownerId && state.status === 'loading' && <div className="data-state" role="status"><LoaderCircle size={18} className="spin-icon" />Loading your saved account data…</div>}
    {state.ownerId === ownerId && state.status === 'error' && <div className="data-state data-state-error" role="alert"><AlertCircle size={19} /><div><b>Account data is unavailable</b><p>{state.message}</p><button type="button" onClick={onRetry}><RefreshCw size={14} />Retry</button></div></div>}

    {data && <>
      <section className="data-record-section" aria-labelledby="saved-analyses-heading"><header><div><h2 id="saved-analyses-heading">Saved analyses</h2><p>{data.analysis_total} saved record{data.analysis_total === 1 ? '' : 's'} · evidence excerpts are hidden in this overview</p></div></header>
        {data.analysis_total === 0 ? <div className="data-empty">No saved analyses are associated with this account.</div> : data.analyses.length === 0 ? <div className="data-empty">No analyses on this page.</div> : <div className="data-record-list">{data.analyses.map((item) => <article className="data-record" key={item.analysis_id}><div className="data-record-icon"><FileText size={17} /></div><div className="data-record-main"><b>{item.skill_count} detected skill{item.skill_count === 1 ? '' : 's'} · {item.analysis_mode} analysis</b><span>Created {formatDate(item.created_at)} · {item.status === 'available' ? 'Available' : item.status === 'expired' ? 'Expired' : 'Marked deleted'}{item.linked_workflow_count ? ` · ${item.linked_workflow_count} linked workflow${item.linked_workflow_count === 1 ? '' : 's'} must be deleted first` : ''}</span><code>{item.analysis_id}</code></div><button type="button" className="data-delete-button" onClick={() => onDelete('analysis', item.analysis_id)} disabled={busy !== null} aria-label={`Delete analysis created ${formatDate(item.created_at)}`}><Trash2 size={15} />Delete</button></article>)}</div>}
      </section>

      <section className="data-record-section" aria-labelledby="saved-workflows-heading"><header><div><h2 id="saved-workflows-heading">Saved workflows</h2><p>{data.workflow_total} saved record{data.workflow_total === 1 ? '' : 's'} · deleting a workflow also removes its linked review and workflow-level measurements</p></div></header>
        {data.workflow_total === 0 ? <div className="data-empty">No saved workflows are associated with this account.</div> : data.workflows.length === 0 ? <div className="data-empty">No workflows on this page.</div> : <div className="data-record-list">{data.workflows.map((item) => <article className="data-record" key={item.run_id}><div className="data-record-icon"><FileText size={17} /></div><div className="data-record-main"><b>{item.job_title}</b><span>{item.workflow_status.replaceAll('_', ' ')}{item.review_decision ? ` · ${item.review_decision} decision` : ''} · Updated {formatDate(item.updated_at)} · {item.status === 'expired' ? 'Expired' : 'Available'}</span><code>{item.run_id}</code></div><button type="button" className="data-delete-button" onClick={() => onDelete('workflow', item.run_id)} disabled={busy !== null} aria-label={`Delete workflow ${item.job_title}`}><Trash2 size={15} />Delete</button></article>)}</div>}
      </section>

      {(data.analysis_total > PAGE_SIZE || data.workflow_total > PAGE_SIZE) && <nav className="data-pagination" aria-label="Account data pages"><button type="button" onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0 || busy !== null}>Previous</button><span>Records {offset + 1}–{offset + Math.max(data.analyses.length, data.workflows.length)} of your account data</span><button type="button" onClick={() => onPage(offset + PAGE_SIZE)} disabled={offset + PAGE_SIZE >= Math.max(data.analysis_total, data.workflow_total) || busy !== null}>Next</button></nav>}
    </>}
  </section>
}
