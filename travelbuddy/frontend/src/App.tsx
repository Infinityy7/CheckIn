import { useEffect, useState } from 'react'
import { Compass, LogOut, Plus, Sparkles, UserRound } from 'lucide-react'
import { api, ApiError, userErrorMessage } from './services/api'
import type { CharacterProfile, Itinerary, PendingCheckInTrip, Recommendation, StreamEvent, TripPreferences, TripState, User } from './types'
import { AuthView } from './components/AuthView'
import { Onboarding } from './components/Onboarding'
import { TripForm } from './components/TripForm'
import { ProfileDrawer } from './components/ProfileDrawer'
import { ItineraryView } from './components/ItineraryView'
import { Workspace, type AgentStatus } from './components/Workspace'
import { Brand, Button, ErrorState, LoadingState } from './components/UI'

type Screen = 'planner' | 'workspace' | 'itinerary'
const initialAgents: AgentStatus = { 'Accommodation Agent': 'waiting', 'Activities Agent': 'waiting', 'Restaurant Agent': 'waiting', 'Transport Agent': 'waiting' }

function streamErrorMessage(event: StreamEvent, fallback: string) {
  const message = event.error ?? fallback
  return event.request_id ? `${message} Reference: ${event.request_id.slice(0, 8)}.` : message
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [profile, setProfile] = useState<CharacterProfile | null>(null)
  const [screen, setScreen] = useState<Screen>('planner')
  const [trip, setTrip] = useState<TripState | null>(null)
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [agents, setAgents] = useState<AgentStatus>(initialAgents)
  const [selections, setSelections] = useState<string[]>([])
  const [researching, setResearching] = useState(false)
  const [building, setBuilding] = useState(false)
  const [itinerary, setItinerary] = useState<Itinerary | null>(null)
  const [profileOpen, setProfileOpen] = useState(false)
  const [loading, setLoading] = useState(api.hasSession())
  const [error, setError] = useState('')
  const [pendingCheckIn, setPendingCheckIn] = useState<PendingCheckInTrip | null>(null)
  const [taviVisible, setTaviVisible] = useState(() => localStorage.getItem('travelbuddy.tavi') !== 'hidden')

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
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) { localStorage.removeItem('travelbuddy.session'); setUser(null) }
      else setError(userErrorMessage(reason, 'Could not open the workspace.'))
    } finally { setLoading(false) }
  }

  function hydrateTrip(state: TripState) {
    setTrip(state); setRecommendations(state.research_results?.flatMap((result) => result.recommendations) ?? []); setSelections(state.selections ?? [])
    setItinerary(state.itinerary ?? null); setScreen(state.itinerary ? 'itinerary' : state.research_results?.length ? 'workspace' : 'planner')
    const restored: AgentStatus = { ...initialAgents }
    for (const result of state.research_results ?? []) restored[result.agent_name] = 'complete'
    for (const failure of state.research_errors ?? []) {
      const agent = Object.keys(restored).find((name) => failure.startsWith(name))
      if (agent) restored[agent] = 'failed'
    }
    setAgents(restored)
  }

  async function createTrip(preferences: TripPreferences) {
    setLoading(true); setError('')
    try {
      const { trip_id } = await api.createTrip(preferences)
      localStorage.setItem('travelbuddy.lastTrip', trip_id)
      const state: TripState = { trip_id, preferences }
      setTrip(state); setRecommendations([]); setSelections([]); setItinerary(null); setAgents(initialAgents); setScreen('workspace')
      await runResearch(trip_id)
    } catch (reason) { setError(userErrorMessage(reason, 'Could not create that trip.')) }
    finally { setLoading(false) }
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
    finally { setResearching(false) }
  }

  async function buildItinerary() {
    if (!trip) return
    setBuilding(true); setError('')
    try {
      await api.select(trip.trip_id, selections)
      await api.itinerary(trip.trip_id, (event) => {
        if (event.event === 'itinerary_complete' && event.itinerary) { setItinerary(event.itinerary); setTrip((current) => current ? { ...current, itinerary: event.itinerary } : current); setScreen('itinerary') }
        if (event.event === 'itinerary_failed') setError(streamErrorMessage(event, 'The itinerary could not be generated.'))
      })
      const latest = await api.trip(trip.trip_id)
      if (latest.itinerary) hydrateTrip(latest)
    } catch (reason) { setError(userErrorMessage(reason, 'Could not assemble the itinerary.')) }
    finally { setBuilding(false) }
  }

  async function logout() { await api.logout(); setUser(null); setProfile(null); setTrip(null); setScreen('planner') }
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

  if (loading && !trip) return <div className="full-state"><LoadingState /></div>
  if (!user) return <AuthView onAuthenticated={bootstrap} />
  if (!user.intake_complete || !profile) return <Onboarding onComplete={(next) => { setProfile(next); setUser({ ...user, intake_complete: true }); setScreen('planner') }} />

  function toggleTavi() {
    setTaviVisible((visible) => {
      localStorage.setItem('travelbuddy.tavi', visible ? 'hidden' : 'visible')
      return !visible
    })
  }

  return <div className={`app-shell ${taviVisible ? '' : 'app-shell--tavi-hidden'}`}>
    <nav className="app-nav"><button className="brand-button" onClick={() => setScreen('planner')}><Brand /></button><div className="nav-route"><Compass /><span>{trip ? trip.preferences.destination : 'No active trip'}</span>{trip && <small>{screen}</small>}</div><div className="nav-actions"><Button variant="quiet" onClick={() => { setScreen('planner'); setTrip(null); localStorage.removeItem('travelbuddy.lastTrip') }}><Plus /> New trip</Button><button className="icon-button" onClick={toggleTavi} aria-label={taviVisible ? 'Minimize Tavi' : 'Show Tavi'} aria-pressed={!taviVisible}><Sparkles /></button><button className="profile-button" onClick={() => setProfileOpen(true)}><UserRound /><span>{user.email.split('@')[0]}<small>Character profile</small></span></button><button className="icon-button" onClick={logout} aria-label="Log out"><LogOut /></button></div></nav>
    {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
    {screen === 'planner' && pendingCheckIn && <aside className="pending-checkin" aria-label="Trip check-in"><div><Sparkles /><p><strong>How was {pendingCheckIn.destination}?</strong><span>A quick rating helps Tavi plan the next one better.</span></p></div><Button variant="secondary" onClick={openPendingTrip}>Rate this trip</Button></aside>}
    {screen === 'planner' && <TripForm onSubmit={createTrip} busy={loading} />}
    {screen === 'workspace' && trip && <Workspace tripId={trip.trip_id} destination={trip.preferences.destination} preferences={trip.preferences} profile={profile} recommendations={recommendations} agents={agents} researching={researching} selections={selections} onToggle={(id) => setSelections((items) => items.includes(id) ? items.filter((item) => item !== id) : [...items, id])} onAlternatives={() => runResearch()} onFeedback={async (item) => {
      try { const next = await api.feedback(trip.trip_id, item.id, 'dislike'); setProfile(next) }
      catch (reason) { setError(userErrorMessage(reason, 'Could not save that preference.')); throw reason }
    }} onBuild={buildItinerary} />}
    {screen === 'itinerary' && itinerary && trip && <ItineraryView itinerary={itinerary} preferences={trip.preferences} postTrip={trip.postTrip} onBack={() => setScreen('workspace')} onRate={submitPostTripRating} />}
    {building && <div className="build-overlay"><LoadingState title="Shaping your final route" detail="Balancing time, cost, geography, and your preferred pace…" /></div>}
    {!trip && screen !== 'planner' && <ErrorState message="This trip is no longer available. Start a fresh route." onRetry={() => setScreen('planner')} />}
    <ProfileDrawer key={profile.updatedAt} open={profileOpen} profile={profile} onClose={() => setProfileOpen(false)} onUpdate={setProfile} onRetake={retake} />
  </div>
}
