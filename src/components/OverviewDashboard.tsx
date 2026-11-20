import { useCallback, useEffect, useRef, useState } from 'react'
import { skillFairFetch } from '../apiClient'
import { dashboardStateForOwner, loadDashboardOverview, type DashboardLoadState } from '../overviewDashboard'
import OverviewDashboardView from './OverviewDashboardView'

type Props = {
  ownerId: string
  greetingName: string
  onDiscoverSkills: () => void
  onViewWorkflows: () => void
}

export default function OverviewDashboard({ ownerId, greetingName, onDiscoverSkills, onViewWorkflows }: Props) {
  const ownerRef = useRef(ownerId)
  ownerRef.current = ownerId
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<DashboardLoadState>({ status: 'loading', ownerId })
  const retry = useCallback(() => setAttempt((current) => current + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setState({ status: 'loading', ownerId })
    loadDashboardOverview(ownerId, () => ownerRef.current, skillFairFetch, controller.signal).then((data) => {
      if (!active || controller.signal.aborted) return
      if (data) setState({ status: 'ready', ownerId, data })
    }).catch((error: unknown) => {
      if (!active || controller.signal.aborted) return
      setState({ status: 'error', ownerId, message: error instanceof Error ? error.message : 'Overview data is temporarily unavailable. Please retry.' })
    })
    return () => { active = false; controller.abort() }
  }, [ownerId, attempt])

  const visibleState = dashboardStateForOwner(state, ownerId)
  return <OverviewDashboardView state={visibleState} greetingName={greetingName} onRetry={retry} onDiscoverSkills={onDiscoverSkills} onViewWorkflows={onViewWorkflows} />
}
