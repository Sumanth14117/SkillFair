import { useState, type FormEvent } from 'react'
import { ArrowRight, CheckCircle2, Eye, EyeOff, LockKeyhole, Mail, ShieldCheck, Sparkles } from 'lucide-react'

type AuthMode = 'login' | 'register'
type Props = {
  onLogin: (email: string, password: string) => Promise<void>
  onRegister: (email: string, password: string) => Promise<void>
  serviceError?: string | null
}

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export default function AuthPage({ onLogin, onRegister, serviceError }: Props) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  function switchMode(next: AuthMode) {
    setMode(next)
    setError(null)
    setSuccess(null)
    setPassword('')
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSuccess(null)
    const normalizedEmail = email.trim()
    if (!EMAIL_PATTERN.test(normalizedEmail) || normalizedEmail.length > 320) {
      setError('Enter a valid email address.')
      return
    }
    if (mode === 'register' && (password.length < 12 || password.length > 128)) {
      setError('Use a password between 12 and 128 characters.')
      return
    }
    if (mode === 'login' && (!password || password.length > 128)) {
      setError('Enter your password.')
      return
    }
    setBusy(true)
    try {
      if (mode === 'register') {
        await onRegister(normalizedEmail, password)
        setSuccess('Your account is ready. Signing you in…')
      } else {
        await onLogin(normalizedEmail, password)
      }
      setPassword('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Authentication failed. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return <main className="auth-screen">
    <div className="auth-background-orb auth-orb-one" /><div className="auth-background-orb auth-orb-two" />
    <section className="auth-panel" aria-labelledby="auth-title">
      <header className="auth-brand"><div className="auth-brand-mark"><span /><span /><span /><span /></div><span>skillfair<span className="auth-brand-dot">.</span></span></header>
      <div className="auth-intro">
        <div className="auth-eyebrow"><Sparkles size={14} /> SKILLS-FIRST RECRUITMENT</div>
        <h1 id="auth-title">{mode === 'login' ? 'Welcome back' : 'Create your account'}</h1>
        <p>{mode === 'login' ? 'Sign in to continue to your SkillFair workspace.' : 'Start with a secure recruiter account for your SkillFair workspace.'}</p>
      </div>

      <div className="auth-mode-switch" role="tablist" aria-label="Account access">
        <button type="button" role="tab" aria-selected={mode === 'login'} className={mode === 'login' ? 'selected' : ''} onClick={() => switchMode('login')}>Sign in</button>
        <button type="button" role="tab" aria-selected={mode === 'register'} className={mode === 'register' ? 'selected' : ''} onClick={() => switchMode('register')}>Create account</button>
      </div>

      <form className="auth-form" onSubmit={submit} noValidate>
        <label htmlFor="auth-email">Email address</label>
        <div className="auth-input-wrap"><Mail size={17} /><input id="auth-email" type="email" autoComplete="email" inputMode="email" maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" required /></div>
        <div className="auth-password-label"><label htmlFor="auth-password">Password</label>{mode === 'register' && <span>12–128 characters</span>}</div>
        <div className="auth-input-wrap"><LockKeyhole size={17} /><input id="auth-password" type={showPassword ? 'text' : 'password'} autoComplete={mode === 'register' ? 'new-password' : 'current-password'} minLength={mode === 'register' ? 12 : undefined} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} placeholder={mode === 'register' ? 'Choose a strong password' : 'Enter your password'} required /><button type="button" className="auth-show-password" onClick={() => setShowPassword((visible) => !visible)} aria-label={showPassword ? 'Hide password' : 'Show password'}>{showPassword ? <EyeOff size={16} /> : <Eye size={16} />}</button></div>
        {(error || serviceError) && <div className="auth-message auth-error" role="alert">{error ?? serviceError}</div>}
        {success && <div className="auth-message auth-success" role="status"><CheckCircle2 size={16} />{success}</div>}
        <button type="submit" className="auth-submit" disabled={busy}>{busy ? <span className="auth-spinner" aria-hidden="true" /> : null}{busy ? (mode === 'login' ? 'Signing in…' : 'Creating account…') : (mode === 'login' ? 'Sign in to SkillFair' : 'Create secure account')} {!busy && <ArrowRight size={17} />}</button>
      </form>

      <div className="auth-privacy"><ShieldCheck size={16} /><span>Your password is protected with Argon2id hashing. SkillFair uses a secure, HTTP-only session cookie and does not store your password in this browser.</span></div>
      <footer className="auth-footer">Hiring on What You Can Do, Not Where You’ve Been.</footer>
    </section>
  </main>
}
