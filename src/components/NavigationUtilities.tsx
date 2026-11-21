import { useCallback, useEffect, useRef, useState } from 'react'
import { Bell, Check, LoaderCircle, RefreshCw, Search, X } from 'lucide-react'
import { listNotifications, markNotificationRead, searchAccount, type NotificationItem, type SearchResult } from '../navigationClient'
import '../styles/navigation-utilities.css'

export function GlobalSearch({ onSelect }: { onSelect: (result: SearchResult) => void }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [retryCount, setRetryCount] = useState(0)
  const [state, setState] = useState<{ status: 'idle' | 'loading' | 'ready' | 'error'; results: SearchResult[]; error?: string }>({ status: 'idle', results: [] })
  const controller = useRef<AbortController | null>(null)
  useEffect(() => {
    controller.current?.abort()
    if (!open || query.trim().length < 2) {
      setState({ status: 'idle', results: [] })
      return
    }
    const current = new AbortController()
    controller.current = current
    const timer = window.setTimeout(() => {
      setState({ status: 'loading', results: [] })
      void searchAccount(query.trim(), current.signal).then((result) => {
        if (!current.signal.aborted) setState({ status: 'ready', results: result.results })
      }).catch((reason: unknown) => {
        if (!current.signal.aborted) setState({ status: 'error', results: [], error: reason instanceof Error ? reason.message : 'Search is unavailable.' })
      })
    }, 250)
    return () => { window.clearTimeout(timer); current.abort() }
  }, [open, query, retryCount])
  useEffect(() => () => controller.current?.abort(), [])
  return <div className="utility-control global-search">
    <button className="utility-icon-button" type="button" aria-label="Search saved SkillFair records" aria-expanded={open} onClick={() => { setOpen((value) => !value); setQuery('') }}><Search size={17} /></button>
    {open && <section className="utility-popover search-popover" aria-label="Global search">
      <div className="popover-heading"><b>Search saved records</b><button type="button" onClick={() => setOpen(false)} aria-label="Close search"><X size={15} /></button></div>
      <input autoFocus value={query} maxLength={80} onChange={(event) => setQuery(event.target.value)} placeholder="Skill, workflow, or audit title" aria-label="Search query" />
      {query.trim().length < 2 && <p className="utility-hint">Enter at least 2 characters. Resume excerpts and job descriptions are not searched.</p>}
      {state.status === 'loading' && <p role="status"><LoaderCircle className="utility-spin" size={15} /> Searching your saved records…</p>}
      {state.status === 'error' && <p className="utility-error" role="alert">{state.error}<button type="button" onClick={() => setRetryCount((value) => value + 1)}>Retry</button></p>}
      {state.status === 'ready' && state.results.length === 0 && <p className="utility-hint">No saved records matched that search.</p>}
      {state.status === 'ready' && state.results.length > 0 && <ul className="utility-results">{state.results.map((result) => <li key={`${result.record_type}:${result.record_id}`}><button type="button" onClick={() => { onSelect(result); setOpen(false); setQuery('') }}><span><b>{result.title}</b><small>{result.detail} · {result.page}</small></span><time>{new Date(result.updated_at).toLocaleDateString()}</time></button></li>)}</ul>}
    </section>}
  </div>
}

export function NotificationsMenu({ onOpenWorkflow }: { onOpenWorkflow: (workflowId: string) => void }) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<NotificationItem[]>([])
  const [unread, setUnread] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const controller = useRef<AbortController | null>(null)
  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true); setError('')
    try {
      const data = await listNotifications(signal)
      if (!signal?.aborted) { setItems(data.notifications); setUnread(data.unread_count) }
    } catch (reason) {
      if (!signal?.aborted) setError(reason instanceof Error ? reason.message : 'Notifications could not be loaded.')
    } finally { if (!signal?.aborted) setLoading(false) }
  }, [])
  useEffect(() => {
    const current = new AbortController(); controller.current = current
    void refresh(current.signal)
    return () => current.abort()
  }, [refresh])
  useEffect(() => {
    if (!open) return
    const current = new AbortController(); controller.current = current
    void refresh(current.signal)
    return () => current.abort()
  }, [open, refresh])
  useEffect(() => () => controller.current?.abort(), [])
  async function openNotification(item: NotificationItem) {
    try {
      if (!item.read_at) {
        const updated = await markNotificationRead(item.notification_id)
        setItems((current) => current.map((candidate) => candidate.notification_id === updated.notification_id ? updated : candidate))
        setUnread((count) => Math.max(0, count - 1))
      }
      if (item.workflow_id) { setOpen(false); onOpenWorkflow(item.workflow_id) }
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'The notification could not be opened.') }
  }
  return <div className="utility-control notifications-control">
    <button className="utility-icon-button" type="button" aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`} aria-expanded={open} onClick={() => setOpen((value) => !value)}><Bell size={17} />{unread > 0 && <span className="notification-badge">{unread > 99 ? '99+' : unread}</span>}</button>
    {open && <section className="utility-popover notifications-popover" aria-label="Notifications">
      <div className="popover-heading"><span><b>Notifications</b><small>{unread} unread</small></span><button type="button" onClick={() => void refresh()} aria-label="Refresh notifications"><RefreshCw size={14} /></button></div>
      {loading && <p role="status"><LoaderCircle className="utility-spin" size={15} /> Loading notifications…</p>}
      {error && <p className="utility-error" role="alert">{error}<button type="button" onClick={() => void refresh()}>Retry</button></p>}
      {!loading && !error && items.length === 0 && <p className="utility-hint">No workflow notifications yet. A notice appears after a review decision is saved.</p>}
      {!loading && items.length > 0 && <ul className="utility-results notification-list">{items.map((item) => <li key={item.notification_id}><button type="button" onClick={() => void openNotification(item)}><span><b>{item.title}{item.read_at ? '' : <i aria-label="Unread"> •</i>}</b><small>{item.message}</small></span><time>{new Date(item.created_at).toLocaleDateString()}</time>{item.read_at && <Check size={14} aria-label="Read" />}</button></li>)}</ul>}
    </section>}
  </div>
}
