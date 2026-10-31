import { createContext, useContext } from 'react'
import type { AuthUser } from './authClient'

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous' | 'error'
export type AuthContextValue = {
  user: AuthUser | null
  status: AuthStatus
  error: string | null
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  retryCurrentUser: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}
