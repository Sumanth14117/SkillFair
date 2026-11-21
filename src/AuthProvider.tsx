import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AuthApiError, getCurrentUser, loginAccount, logoutAccount, registerAccount, type AuthUser } from './authClient'
import { AuthContext, type AuthStatus } from './authContext'
import { registerSessionExpiredHandler, resetSessionExpiredNotification } from './sessionExpiry'
import { publishAuthTabChange, subscribeToAuthTabChanges } from './authTabSync'
import { createSessionRevalidator } from './authSessionRevalidation'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [error, setError] = useState<string | null>(null)
  const authRequestVersion = useRef(0)
  const revalidator = useMemo(() => createSessionRevalidator({
    loadUser: (force) => getCurrentUser(fetch, force),
    onStart: () => {
      authRequestVersion.current += 1
      resetSessionExpiredNotification()
      setUser(null)
      setStatus('loading')
      setError(null)
    },
    onAuthenticated: (confirmedUser) => {
      resetSessionExpiredNotification()
      setUser(confirmedUser)
      setStatus('authenticated')
      setError(null)
    },
    onAnonymous: () => {
      setUser(null)
      setStatus('anonymous')
      setError('Your sign-in session has expired. Sign in again.')
    },
    onError: (reason) => {
      setUser(null)
      setStatus('error')
      setError(reason instanceof Error ? reason.message : 'Authentication is temporarily unavailable. Please try again.')
    },
    isUnauthorized: (reason) => reason instanceof AuthApiError && reason.status === 401,
  }), [])
  const revalidateCurrentUser = useCallback(() => revalidator.revalidate(), [revalidator])

  useEffect(() => registerSessionExpiredHandler(() => {
    authRequestVersion.current += 1
    revalidator.invalidate()
    setUser(null)
    setError('Your sign-in session has expired. Sign in again.')
    setStatus('anonymous')
    publishAuthTabChange()
  }), [revalidator])

  useEffect(() => {
    let active = true
    const version = ++authRequestVersion.current
    setStatus('loading')
    setError(null)
    getCurrentUser().then((currentUser) => {
      if (!active || authRequestVersion.current !== version) return
      resetSessionExpiredNotification()
      setUser(currentUser)
      setStatus('authenticated')
    }).catch((reason: unknown) => {
      if (!active || authRequestVersion.current !== version) return
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
  }, [])

  useEffect(() => subscribeToAuthTabChanges(() => { void revalidateCurrentUser() }), [revalidateCurrentUser])

  const authenticate = useCallback(async (action: (email: string, password: string) => Promise<AuthUser>, email: string, password: string) => {
    authRequestVersion.current += 1
    revalidator.invalidate()
    setError(null)
    try {
      const authenticatedUser = await action(email, password)
      resetSessionExpiredNotification()
      setUser(authenticatedUser)
      setStatus('authenticated')
      publishAuthTabChange()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Authentication failed. Please try again.'
      setError(message)
      throw new Error(message)
    }
  }, [revalidator])

  const login = useCallback((email: string, password: string) => authenticate(loginAccount, email, password), [authenticate])
  const register = useCallback(async (email: string, password: string) => {
    setError(null)
    try {
      await registerAccount(email, password)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Registration could not be completed. Please try again.'
      setError(message)
      throw new Error(message)
    }
  }, [])

  const logout = useCallback(async () => {
    authRequestVersion.current += 1
    revalidator.invalidate()
    setError(null)
    try {
      await logoutAccount()
      setUser(null)
      setStatus('anonymous')
      publishAuthTabChange()
    } catch (reason) {
      if (reason instanceof AuthApiError && reason.status === 401) {
        setUser(null)
        setStatus('anonymous')
        publishAuthTabChange()
        return
      }
      const message = reason instanceof Error ? reason.message : 'Sign out failed. Please try again.'
      setError(message)
      throw new Error(message)
    }
  }, [revalidator])

  const retryCurrentUser = revalidateCurrentUser
  const value = useMemo(() => ({ user, status, error, login, register, logout, retryCurrentUser }), [user, status, error, login, register, logout, retryCurrentUser])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
