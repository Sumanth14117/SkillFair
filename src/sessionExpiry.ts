type SessionExpiredHandler = () => void
type SessionEpochListener = (epoch: number) => void

let sessionExpiredHandler: SessionExpiredHandler | null = null
let notificationSent = false
let sessionEpoch = 0
const epochListeners = new Set<SessionEpochListener>()

export function registerSessionExpiredHandler(handler: SessionExpiredHandler): () => void {
  sessionExpiredHandler = handler
  return () => {
    if (sessionExpiredHandler === handler) sessionExpiredHandler = null
  }
}

export function currentSessionEpoch(): number {
  return sessionEpoch
}

export function subscribeSessionEpoch(listener: SessionEpochListener): () => void {
  epochListeners.add(listener)
  return () => epochListeners.delete(listener)
}

export function notifySessionExpired(requestEpoch: number): void {
  if (requestEpoch !== sessionEpoch || notificationSent) return
  notificationSent = true
  sessionExpiredHandler?.()
}

export function resetSessionExpiredNotification(): void {
  sessionEpoch += 1
  notificationSent = false
  for (const listener of [...epochListeners]) listener(sessionEpoch)
}
