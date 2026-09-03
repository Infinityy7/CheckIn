import { useCallback, useEffect, useRef, useState } from 'react'
import { Compass, LogOut, Plus, Sparkles, UserRound } from 'lucide-react'
import './styles/shell.css'
import { api, ApiError, userErrorMessage } from './services/api'
import { clearTripDraft } from './services/tripDraft'
import { agentsForScope, scopeOf } from './scope'
import type { AgentHealth, CharacterProfile, FeasibilityReport, Itinerary, PendingCheckInTrip, Recommendation, StreamEvent, TripCreateResult, TripPreferences, TripState, User } from './types'
import { AuthView } from './components/AuthView'
import { Onboarding } from './components/Onboarding'
import { TripForm } from './components/TripForm'
import { ProfileDrawer } from './components/ProfileDrawer'
import { ItineraryView } from './components/ItineraryView'
import { HealthIndicator } from './components/HealthIndicator'
import { Workspace, type AgentStatus } from './components/Workspace'
import { Banner, Brand, Button, ErrorState, LoadingState, Stepper, ThemeToggle } from './components/UI'

type Screen = 'planner' | 'workspace' | 'itinerary'
const STAGES = ['Plan', 'Research', 'Itinerary'] as const
const heldWithoutReport: FeasibilityReport = { verdict: 'unrealistic', confidence: 0, reason: 'This request was held for review before research.', suggestion_text: '', suggested_changes: {} }

function streamErrorMessage(event: StreamEvent, fallback: string) {
  const message = event.error ?? fallback
  return event.request_id ? `${message} Reference: ${event.request_id.slice(0, 8)}.` : message
}

function newIdempotencyKey() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}-${Math.random().toString(36).slice(2, 14)}`
}

function preferencesFingerprint(preferences: TripPreferences) {
  return JSON.stringify(preferences, Object.keys(preferences).sort())
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [profile, setProfile] = useState<CharacterProfile | null>(null)
  const [screen, setScreen] = useState<Screen>('planner')
  const [trip, setTrip] = useState<TripState | null>(null)
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [agents, setAgents] = useState<AgentStatus>({})
  const [selections, setSelections] = useState<string[]>([])
  const [researching, setResearching] = useState(false)
  const [building, setBuilding] = useState(false)
  const [itinerary, setItinerary] = useState<Itinerary | null>(null)
  const [profileOpen, setProfileOpen] = useState(false)
  const [loading, setLoading] = useState(api.hasSession())
  const [creatingTrip, setCreatingTrip] = useState(false)
  const [error, setError] = useState('')
  const [pendingCheckIn, setPendingCheckIn] = useState<PendingCheckInTrip | null>(null)
  const [health, setHealth] = useState<AgentHealth | null>(null)
  const [taviVisible, setTaviVisible] = useState(() => localStorage.getItem('travelbuddy.tavi') !== 'hidden')
  const [feasibility, setFeasibility] = useState<FeasibilityReport | null>(null)
  const [formKey, setFormKey] = useState(0)
  const submission = useRef<{ fingerprint: string; key: string } | null>(null)

  const refreshHealth = useCallback(() => {
    api.agentHealth().then(setHealth).catch(() => setHealth(null))
  }, [])

  // Session bootstrap runs exactly once; subsequent refreshes are explicit user actions.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (api.hasSession()) void bootstrap() }, [])

  async function bootstrap() {
    setLoading(true); setError('')
    try {
      const me = await api.me(); setUser(me)
      if (me.intake_complete) setProfile(await api.profile())
      const lastTrip = localStorage.getItem('travelbuddy.lastTrip')
      if (me.intake_complete && lastTrip) {
        const state = await api.trip(lastTrip).catch(() => null)
        if (state) hydrateTrip(state)
      }
      if (me.intake_complete) {
        const pending = await api.pendingCheckIn().catch(() => ({ trip: null }))
        setPendingCheckIn(pending.trip)
        refreshHealth()
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) { localStorage.removeItem('travelbuddy.session'); setUser(null) }
      else setError(userErrorMessage(reason, 'Could not open the workspace.'))
    } finally { setLoading(false) }
  }

  function hydrateTrip(state: TripState) {
    const restored = agentsForScope(scopeOf(state.preferences))
    const results = (state.research_results ?? []).filter((result) => result.agent_name in restored)
    const failures = (state.research_errors ?? []).flatMap((failure) => Object.keys(restored).filter((name) => failure.startsWith(name)))
    const researched = results.length > 0 || failures.length > 0
    setTrip(state); setRecommendations(results.flatMap((result) => result.recommendations)); setSelections(state.selections ?? [])
    setItinerary(state.itinerary ?? null); setScreen(state.itinerary ? 'itinerary' : researched ? 'workspace' : 'planner')
    for (const result of results) restored[result.agent_name] = 'complete'
    for (const agent of failures) restored[agent] = 'failed'
    setAgents(restored)
  }

  /** One key per submitted value-set: retries and "Research anyway" replay it, edits mint a new one. */
  function idempotencyKeyFor(preferences: TripPreferences) {
    const fingerprint = preferencesFingerprint(preferences)
    if (submission.current?.fingerprint !== fingerprint) submission.current = { fingerprint, key: newIdempotencyKey() }
    return submission.current.key
  }

  async function createTrip(preferences: TripPreferences, acknowledgeFeasibility = false) {
    const idempotencyKey = idempotencyKeyFor(preferences)
    setCreatingTrip(true); setError(''); setFeasibility(null)
    let created: TripCreateResult
    try { created = await api.createTrip(preferences, { idempotencyKey, acknowledgeFeasibility }) }
    catch (reason) { setError(userErrorMessage(reason, 'Could not create that trip.')); return }
    finally { setCreatingTrip(false) }
    if (created.status === 'held' || !created.trip_id) { setFeasibility(created.feasibility ?? heldWithoutReport); return }
    submission.current = null
    clearTripDraft()
    localStorage.setItem('travelbuddy.lastTrip', created.trip_id)
    await beginResearch(created.trip_id, preferences)
  }

  async function beginResearch(tripId: string, preferences: TripPreferences) {
    const state: TripState = { trip_id: tripId, preferences }
    setTrip(state); setRecommendations([]); setSelections([]); setItinerary(null); setAgents(agentsForScope(scopeOf(preferences))); setScreen('workspace')
    await runResearch(tripId)
  }

  async function runResearch(tripId = trip?.trip_id) {
    if (!tripId) return
    const retryingPartial = recommendations.length > 0 && Object.values(agents).some((status) => status === 'failed')
    setResearching(true); setError('')
    // Keep last-known-good cards visible while replacements are in flight.
    // Missing-category retries keep existing recommendation IDs intact.
    if (!retryingPartial) { setSelections([]); setItinerary(null) }
    try {
      await api.research(tripId, (event: StreamEvent) => {
        if (event.event === 'agent_started' && event.agent) setAgents((current) => ({ ...current, [event.agent!]: 'working' }))
        if (event.event === 'agent_completed' && event.agent) { setAgents((current) => ({ ...current, [event.agent!]: 'complete' })); setRecommendations((items) => [...items.filter((item) => item.category !== event.results?.[0]?.category), ...(event.results ?? [])]) }
        if (event.event === 'agent_failed' && event.agent) setAgents((current) => ({ ...current, [event.agent!]: 'failed' }))
        if (event.event === 'error') setError(streamErrorMessage(event, 'Research failed.'))
      })
      hydrateTrip(await api.trip(tripId))
    } catch (reason) { setError(userErrorMessage(reason, 'The research crew got interrupted.')) }
    finally { setResearching(false); refreshHealth() }
  }

  async function toggleSelection(id: string) {
    if (!trip) return
    const flip = (items: string[]) => items.includes(id) ? items.filter((item) => item !== id) : [...items, id]
    const next = flip(selections)
    setSelections(next)
    try { await api.select(trip.trip_id, next) }
    catch (reason) { setSelections(flip); setError(userErrorMessage(reason, 'Could not save that choice.')) }
  }

  async function buildItinerary() {
    if (!trip) return
    setBuilding(true); setError('')
    try {
      await api.itinerary(trip.trip_id, (event) => {
        if (event.event === 'itinerary_complete' && event.itinerary) { setItinerary(event.itinerary); setTrip((current) => current ? { ...current, itinerary: event.itinerary } : current); setScreen('itinerary') }
        if (event.event === 'itinerary_failed') setError(streamErrorMessage(event, 'The itinerary could not be generated.'))
      })
    } catch (reason) { setError(userErrorMessage(reason, 'Could not assemble the itinerary.')) }
    finally { setBuilding(false) }
  }

  function startNewTrip() {
    setScreen('planner'); setTrip(null); setFeasibility(null); setError('')
    submission.current = null; clearTripDraft(); localStorage.removeItem('travelbuddy.lastTrip')
    setFormKey((key) => key + 1)
  }

  async function logout() { await api.logout(); clearTripDraft(); setUser(null); setProfile(null); setTrip(null); setFeasibility(null); setScreen('planner') }
  async function retake() {
    try { await api.resetIntake() }
    catch (reason) { if (!(reason instanceof ApiError) || reason.status !== 404) throw reason; await api.resetProfile() }
    setProfile(null); setUser((current) => current ? { ...current, intake_complete: false } : current); setProfileOpen(false)
  }

  async function openPendingTrip() {
    if (!pendingCheckIn) return
    setLoading(true); setError('')
    try {
      const state = await api.trip(pendingCheckIn.trip_id)
      localStorage.setItem('travelbuddy.lastTrip', state.trip_id)
      hydrateTrip(state)
    } catch (reason) { setError(userErrorMessage(reason, 'Could not open that trip.')) }
    finally { setLoading(false) }
  }

  async function submitPostTripRating(rating: 1 | 2 | 3 | 4 | 5) {
    if (!trip) return
    const result = await api.submitPostTripFeedback(trip.trip_id, rating)
    setProfile(result.profile)
    setTrip((current) => current ? { ...current, postTrip: result.postTrip } : current)
    setPendingCheckIn((current) => current?.trip_id === trip.trip_id ? null : current)
  }

  // Full-screen loading is reserved for session bootstrap and opening an existing trip;
  // trip creation keeps TripForm mounted so nothing the traveler typed is lost.
  if (loading && !trip) return <div className="full-state"><LoadingState /></div>
  if (!user) return <AuthView onAuthenticated={bootstrap} />
  if (!user.intake_complete || !profile) return <Onboarding onComplete={(next) => { setProfile(next); setUser({ ...user, intake_complete: true }); setScreen('planner') }} />

  function toggleTavi() {
    setTaviVisible((visible) => {
      localStorage.setItem('travelbuddy.tavi', visible ? 'hidden' : 'visible')
      return !visible
    })
  }

  const stage = screen === 'planner' ? 0 : screen === 'workspace' ? 1 : 2
  const impaired = health !== null && health.status !== 'ok'

  return <div className={`app-shell ${taviVisible ? '' : 'app-shell--tavi-hidden'}`}>
    <a className="skip-link" href="#main">Skip to content</a>
    <nav className="app-nav" aria-label="Primary">
      <button className="brand-button" onClick={() => setScreen('planner')}><Brand /></button>
      <div className="nav-center">
        <Stepper steps={STAGES} current={stage} />
        <div className="nav-route"><Compass aria-hidden /><span>{trip ? trip.preferences.destination : 'No active trip'}</span></div>
      </div>
      <div className="nav-actions">
        <Button variant="quiet" onClick={startNewTrip}><Plus /> New trip</Button>
        <HealthIndicator health={health} onRefresh={refreshHealth} />
        <ThemeToggle />
        <button className="icon-button" onClick={toggleTavi} aria-label={taviVisible ? 'Minimize Tavi' : 'Show Tavi'} aria-pressed={!taviVisible}><Sparkles /></button>
        <button className="profile-button" onClick={() => setProfileOpen(true)}><UserRound /><span>{user.email.split('@')[0]}<small>Character profile</small></span></button>
        <button className="icon-button" onClick={logout} aria-label="Log out"><LogOut /></button>
      </div>
    </nav>
    {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
    {impaired && <div className="ops-banner"><Banner
      tone={health.status === 'unavailable' ? 'danger' : 'warn'}
      title={health.status === 'unavailable' ? 'Trip research is unavailable right now' : 'Trip research is running degraded'}
      detail={health.status === 'unavailable' ? 'The AI research service is not reachable. Existing results stay available; retry in a little while.' : 'Some research routes are recovering. Searches may be slower or fall back to a different model.'}
      action={<Button variant="secondary" onClick={refreshHealth}>Re-check</Button>}
    /></div>}
    <div id="main">
      {screen === 'planner' && pendingCheckIn && <aside className="pending-checkin" aria-label="Trip check-in"><div><Sparkles /><p><strong>How was {pendingCheckIn.destination}?</strong><span>A quick rating helps Tavi plan the next one better.</span></p></div><Button variant="secondary" onClick={openPendingTrip}>Rate this trip</Button></aside>}
      {screen === 'planner' && <TripForm key={formKey} onSubmit={(preferences) => createTrip(preferences)} busy={creatingTrip} feasibility={feasibility} onProceedAnyway={(preferences) => createTrip(preferences, true)} onDismissWarning={() => setFeasibility(null)} />}
      {screen === 'workspace' && trip && <Workspace tripId={trip.trip_id} destination={trip.preferences.destination} preferences={trip.preferences} profile={profile} recommendations={recommendations} agents={agents} researching={researching} selections={selections} companions={[...trip.preferences.cotravellers, ...(trip.preferences.cotraveller_usernames ?? []).map((username) => `@${username}`)]} onToggle={toggleSelection} onRetryMissing={() => runResearch()} onFullRefresh={() => runResearch()} onFeedback={async (item, sentiment) => {
        try { const next = await api.feedback(trip.trip_id, item.id, sentiment); setProfile(next) }
        catch (reason) { setError(userErrorMessage(reason, 'Could not save that preference.')); throw reason }
      }} onBuild={buildItinerary} />}
      {screen === 'itinerary' && itinerary && trip && <ItineraryView itinerary={itinerary} preferences={trip.preferences} postTrip={trip.postTrip} onBack={() => setScreen('workspace')} onRate={submitPostTripRating} />}
      {building && <div className="build-overlay"><LoadingState title="Shaping your final route" detail="Balancing time, cost, geography, and your preferred pace…" /></div>}
      {!trip && screen !== 'planner' && <ErrorState message="This trip is no longer available. Start a fresh route." onRetry={() => setScreen('planner')} />}
    </div>
    <ProfileDrawer key={profile.updatedAt} open={profileOpen} profile={profile} onClose={() => setProfileOpen(false)} onUpdate={setProfile} onRetake={retake} />
  </div>
}
