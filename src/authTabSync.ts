const CHANNEL_NAME = 'skillfair-auth-state-v1'
const STORAGE_EVENT_KEY = 'skillfair.auth-change.v1'
const MESSAGE_TYPE = 'session-changed'

type AuthTabListener = () => void

const listeners = new Set<AuthTabListener>()
let channel: BroadcastChannel | null = null
let storageListenerActive = false
let eventCounter = 0

function notifyListeners(): void {
  for (const listener of [...listeners]) listener()
}

function onStorage(event: StorageEvent): void {
  if (event.key === STORAGE_EVENT_KEY && event.newValue !== null) notifyListeners()
}

function ensureTransport(): void {
  if (typeof window === 'undefined') return
  if (!storageListenerActive) {
    window.addEventListener('storage', onStorage)
    storageListenerActive = true
  }
  if (channel) return
  if (typeof BroadcastChannel !== 'undefined') {
    try {
      channel = new BroadcastChannel(CHANNEL_NAME)
      channel.addEventListener('message', (event: MessageEvent<unknown>) => {
        if (event.data === MESSAGE_TYPE) notifyListeners()
      })
      return
    } catch {
      channel = null
    }
  }
}

function stopTransportIfUnused(): void {
  if (listeners.size > 0 || typeof window === 'undefined') return
  channel?.close()
  channel = null
  if (storageListenerActive) window.removeEventListener('storage', onStorage)
  storageListenerActive = false
}

/** Cross-tab notifications carry no identity or credentials; receivers must revalidate with /api/auth/me. */
export function subscribeToAuthTabChanges(listener: AuthTabListener): () => void {
  listeners.add(listener)
  ensureTransport()
  return () => {
    listeners.delete(listener)
    stopTransportIfUnused()
  }
}

export function publishAuthTabChange(): void {
  ensureTransport()
  if (channel) {
    try {
      channel.postMessage(MESSAGE_TYPE)
      return
    } catch {
      // Use the non-sensitive storage event fallback if the channel is unavailable.
    }
  }
  if (typeof window === 'undefined') return
  try {
    eventCounter += 1
    window.localStorage.setItem(STORAGE_EVENT_KEY, `${Date.now()}:${eventCounter}`)
  } catch {
    // Cross-tab notification is best-effort; the server remains authoritative.
  }
}
