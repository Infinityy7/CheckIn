import { useState } from 'react'
import { ArrowRight, Compass, Map, Sparkles } from 'lucide-react'
import { api } from '../services/api'
import { Mascot } from './Mascot'
import { Brand, Button } from './UI'

export function AuthView({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('register')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try { await api.auth(mode, email, password); onAuthenticated() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not sign you in.') }
    finally { setBusy(false) }
  }

  return <main className="auth-screen">
    <nav className="auth-nav"><Brand /><span className="nav-note">AI travel, personally mapped</span></nav>
    <section className="auth-editorial">
      <div className="auth-copy">
        <span className="eyebrow"><Sparkles /> Your personal travel intelligence</span>
        <h1>Trips that feel<br /><em>like you.</em></h1>
        <p>Meet Tavi—part compass, part curious co-pilot. Together you’ll research, compare, and shape a trip around the way you actually like to move through the world.</p>
        <div className="auth-proof">
          <span><Compass /> Four specialist agents</span>
          <span><Map /> One coherent itinerary</span>
        </div>
      </div>
      <div className="auth-mascot-scene" aria-hidden="true">
        <div className="map-ring map-ring--one" /><div className="map-ring map-ring--two" />
        <span className="route-label route-label--a">RESEARCH</span><span className="route-label route-label--b">RANK</span>
        <Mascot state="greeting" size="hero" />
      </div>
      <form className="auth-card" onSubmit={submit}>
        <span className="eyebrow">{mode === 'register' ? 'Begin your travel profile' : 'Welcome back, explorer'}</span>
        <h2>{mode === 'register' ? 'Create your compass' : 'Continue your journey'}</h2>
        <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" required /></label>
        <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" minLength={8} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required /></label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <Button disabled={busy} type="submit">{busy ? 'Opening the map…' : mode === 'register' ? 'Meet Tavi' : 'Open my workspace'} <ArrowRight /></Button>
        <button type="button" className="text-button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}>
          {mode === 'login' ? 'New here? Create an account' : 'Already have a profile? Sign in'}
        </button>
      </form>
    </section>
  </main>
}
