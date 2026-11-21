import type { AuthUser } from './authClient'

export type SessionRevalidationHandlers = {
  loadUser: (force: boolean) => Promise<AuthUser>
  onStart: () => void
  onAuthenticated: (user: AuthUser) => void
  onAnonymous: () => void
  onError: (reason: unknown) => void
  isUnauthorized: (reason: unknown) => boolean
}

export type SessionRevalidator = {
  revalidate: () => Promise<void>
  invalidate: () => void
}

/** Coalesces tab notifications and commits only the last server-confirmed identity. */
export function createSessionRevalidator(handlers: SessionRevalidationHandlers): SessionRevalidator {
  let generation = 0
  let pending: Promise<void> | null = null
  let pendingGeneration = -1
  let queued = false

  function invalidate(): void {
    generation += 1
    pending = null
    pendingGeneration = -1
    queued = false
  }

  function revalidate(): Promise<void> {
    if (pending) {
      queued = true
      return pending
    }

    const requestGeneration = ++generation
    pendingGeneration = requestGeneration
    handlers.onStart()
    const operation = (async () => {
      let force = true
      while (generation === requestGeneration) {
        queued = false
        try {
          const confirmedUser = await handlers.loadUser(force)
          if (generation !== requestGeneration) return
          if (queued) {
            force = true
            continue
          }
          handlers.onAuthenticated(confirmedUser)
          return
        } catch (reason: unknown) {
          if (generation !== requestGeneration) return
          if (queued) {
            force = true
            continue
          }
          if (handlers.isUnauthorized(reason)) handlers.onAnonymous()
          else handlers.onError(reason)
          return
        }
      }
    })().finally(() => {
      if (pendingGeneration === requestGeneration) {
        pending = null
        pendingGeneration = -1
      }
    })
    pending = operation
    return operation
  }

  return { revalidate, invalidate }
}
