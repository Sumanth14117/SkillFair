import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { AuthApiError, getCurrentUser, loginAccount, logoutAccount, registerAccount, type AuthUser } from './authClient'
import { AuthContext, type AuthStatus } from './authContext'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [error, setError] = useState<string | null>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)

  useEffect(() => {
    let active = true
    setStatus('loading')
    setError(null)
    getCurrentUser().then((currentUser) => {
      if (!active) return
      setUser(currentUser)
      setStatus('authenticated')
    }).catch((reason: unknown) => {
      if (!active) return
      if (reason instanceof AuthApiError && reason.status === 401) {
        setUser(null)
        setStatus('anonymous')
        return
      }
      setUser(null)
      setStatus('error')
      setError(reason instanceof Error ? reason.message : 'Authentication is temporarily unavailable. Please try again.')
    })
    return () => { active = false }
  }, [loadAttempt])

  const authenticate = useCallback(async (action: (email: string, password: string) => Promise<AuthUser>, email: string, password: string) => {
    setError(null)
    try {
      const authenticatedUser = await action(email, password)
      setUser(authenticatedUser)
      setStatus('authenticated')
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Authentication failed. Please try again.'
      setError(message)
      throw new Error(message)
    }
  }, [])

  const login = useCallback((email: string, password: string) => authenticate(loginAccount, email, password), [authenticate])
  const register = useCallback((email: string, password: string) => authenticate(registerAccount, email, password), [authenticate])

  const logout = useCallback(async () => {
    setError(null)
    try {
      await logoutAccount()
      setUser(null)
      setStatus('anonymous')
    } catch (reason) {
      if (reason instanceof AuthApiError && reason.status === 401) {
        setUser(null)
        setStatus('anonymous')
        return
      }
      const message = reason instanceof Error ? reason.message : 'Sign out failed. Please try again.'
      setError(message)
      throw new Error(message)
    }
  }, [])

  const retryCurrentUser = useCallback(() => setLoadAttempt((attempt) => attempt + 1), [])
  const value = useMemo(() => ({ user, status, error, login, register, logout, retryCurrentUser }), [user, status, error, login, register, logout, retryCurrentUser])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
