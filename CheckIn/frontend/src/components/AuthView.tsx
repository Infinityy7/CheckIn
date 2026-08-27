import '../styles/identity.css'
import { useState } from 'react'
import type { FormEvent, InputHTMLAttributes, ReactNode } from 'react'
import { ArrowRight, Compass, Map, Sparkles } from 'lucide-react'
import { api, ApiError, userErrorMessage } from '../services/api'
import type { RegisterPayload } from '../types'
import { Mascot } from './Mascot'
import { Brand, Button, Chip, SegmentedControl, ThemeToggle } from './UI'

const authModes = ['register', 'login'] as const
type AuthMode = (typeof authModes)[number]

const USERNAME_RE = /^[a-z0-9](?:[a-z0-9_-]{1,28}[a-z0-9])?$/
const USERNAME_HINT = '2-30 chars · letters, numbers, - or _'

const modeCopy: Record<AuthMode, { eyebrow: string; title: string; action: string; segment: string }> = {
  register: { eyebrow: 'Begin your travel profile', title: 'Create your compass', action: 'Meet Tavi', segment: 'Create account' },
  login: { eyebrow: 'Welcome back, explorer', title: 'Continue your journey', action: 'Open my workspace', segment: 'Sign in' },
}

interface Availability {
  username: string
  state: 'checking' | 'available' | 'taken'
}

function Field({ id, label, hint, hintTone = 'muted', status, adornment, ...input }: {
  id: string
  label: ReactNode
  hint?: string
  hintTone?: 'muted' | 'danger'
  status?: ReactNode
  adornment?: string
} & InputHTMLAttributes<HTMLInputElement>) {
  const hintId = hint ? `${id}-hint` : undefined
  const control = <input id={id} aria-describedby={hintId} aria-invalid={hintTone === 'danger' || undefined} {...input} />
  return <div className="idn-field">
    <span className="idn-auth__label-row">
      <label htmlFor={id}>{label}</label>
      {status}
    </span>
    {adornment
      ? <span className="idn-auth__adorned"><span className="idn-auth__adornment" aria-hidden="true">{adornment}</span>{control}</span>
      : control}
    {hint && <small id={hintId} className={hintTone === 'danger' ? 'idn-auth__hint idn-auth__hint--danger' : 'idn-auth__hint'}>{hint}</small>}
  </div>
}

export function AuthView({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [mode, setMode] = useState<AuthMode>('register')
  const [name, setName] = useState('')
  const [username, setUsername] = useState('')
  const [availability, setAvailability] = useState<Availability | null>(null)
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function checkUsername() {
    const candidate = username
    if (!USERNAME_RE.test(candidate)) return
    if (availability?.username === candidate && availability.state !== 'checking') return
    setAvailability({ username: candidate, state: 'checking' })
    const settle = (state: Availability['state'] | null) => setAvailability((prev) =>
      prev?.username === candidate ? (state ? { username: candidate, state } : null) : prev)
    try {
      await api.lookupUser(candidate)
      settle('taken')
    } catch (reason) {
      // A 404 means the handle is unclaimed; anything else stays silent.
      settle(reason instanceof ApiError && reason.status === 404 ? 'available' : null)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setError('')
    if (mode === 'register' && !USERNAME_RE.test(username)) {
      setError(`Pick a username first — ${USERNAME_HINT}.`)
      return
    }
    setBusy(true)
    try {
      if (mode === 'register') {
        const payload: RegisterPayload = {
          email: email.trim(),
          password,
          username,
          name: name.trim(),
          ...(phone.trim() ? { phone: phone.trim() } : {}),
        }
        await api.register(payload)
      } else {
        await api.login(identifier.trim(), password)
      }
      onAuthenticated()
    } catch (reason) {
      setError(userErrorMessage(reason, 'Could not sign you in.'))
    } finally {
      setBusy(false)
    }
  }

  const copy = modeCopy[mode]
  const usernameInvalid = username !== '' && !USERNAME_RE.test(username)
  const availabilityStatus = <span className="idn-auth__availability" role="status">
    {availability && availability.username === username && (
      availability.state === 'checking' ? <Chip tone="muted">Checking…</Chip>
        : availability.state === 'available' ? <Chip tone="ok">Available</Chip>
          : <Chip tone="warn">Taken</Chip>
    )}
  </span>

  return <main className="idn-auth">
    <nav className="idn-auth__bar" aria-label="CheckIn">
      <Brand />
      <span className="idn-auth__note">AI travel, personally mapped</span>
      <ThemeToggle />
    </nav>
    <section className="idn-auth__split">
      <aside className="idn-auth__hero">
        <span className="eyebrow"><Sparkles aria-hidden /> Your personal travel intelligence</span>
        <h1>Trips that feel<br /><em>like you.</em></h1>
        <p>Meet Tavi—part compass, part curious co-pilot. Together you’ll research, compare, and shape a trip around the way you actually like to move through the world.</p>
        <div className="idn-auth__proof">
          <span><Compass aria-hidden /> Four specialist agents</span>
          <span><Map aria-hidden /> One coherent itinerary</span>
        </div>
        <div className="idn-auth__scene" aria-hidden="true">
          <span className="idn-waypoint idn-waypoint--a">RESEARCH</span>
          <span className="idn-waypoint idn-waypoint--b">RANK</span>
          <Mascot state="greeting" size="hero" />
        </div>
      </aside>
      <div className="idn-auth__form-side">
        <form className="idn-auth__card corner-tick" onSubmit={submit}>
          <SegmentedControl
            options={authModes}
            value={mode}
            onChange={(next) => {
              setMode(next)
              setError('')
              if (next === 'login' && !identifier) setIdentifier(email)
            }}
            ariaLabel="Create an account or sign in"
            format={(option) => modeCopy[option].segment}
          />
          <span className="eyebrow">{copy.eyebrow}</span>
          <h2>{copy.title}</h2>
          {mode === 'register' ? <>
            <Field id="auth-name" label="Full name" value={name} onChange={(e) => setName(e.target.value)}
              autoComplete="name" placeholder="Priya Sharma" required />
            <Field id="auth-username" label="Username" adornment="@" value={username}
              onChange={(e) => setUsername(e.target.value.toLowerCase())}
              onBlur={() => { void checkUsername() }}
              autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false}
              placeholder="trailmix" required
              hint={USERNAME_HINT} hintTone={usernameInvalid ? 'danger' : 'muted'}
              status={availabilityStatus} />
            <Field id="auth-email" label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              autoComplete="email" placeholder="you@example.com" required />
            <Field id="auth-phone" label="Phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)}
              autoComplete="tel" placeholder="+91 98765 43210"
              status={<span className="idn-auth__optional">Optional</span>} />
            <Field id="auth-password" label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password" minLength={8} required hint="At least 8 characters." />
          </> : <>
            <Field id="auth-identifier" label="Email or username" value={identifier} onChange={(e) => setIdentifier(e.target.value)}
              autoComplete="username" placeholder="you@example.com" required />
            <Field id="auth-login-password" label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password" required />
          </>}
          {error && <p className="form-error" role="alert">{error}</p>}
          <Button disabled={busy} type="submit">{busy ? 'Opening the map…' : copy.action} <ArrowRight aria-hidden /></Button>
        </form>
      </div>
    </section>
  </main>
}
