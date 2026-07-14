import { useEffect, useState } from 'react'
import { Compass, LogOut, Plus, UserRound } from 'lucide-react'
import { api, ApiError } from './services/api'
import type { CharacterProfile, Itinerary, Recommendation, StreamEvent, TripPreferences, TripState, User } from './types'
import { AuthView } from './components/AuthView'
import { Onboarding } from './components/Onboarding'
import { TripForm } from './components/TripForm'
import { ProfileDrawer } from './components/ProfileDrawer'
import { ItineraryView } from './components/ItineraryView'
import { Workspace, type AgentStatus } from './components/Workspace'
import { Brand, Button, ErrorState, LoadingState } from './components/UI'

type Screen = 'planner' | 'workspace' | 'itinerary'
const initialAgents: AgentStatus = { 'Accommodation Agent': 'waiting', 'Activities Agent': 'waiting', 'Restaurant Agent': 'waiting', 'Transport Agent': 'waiting' }

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
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) { localStorage.removeItem('travelbuddy.session'); setUser(null) }
      else setError(reason instanceof Error ? reason.message : 'Could not open the workspace.')
    } finally { setLoading(false) }
  }

  function hydrateTrip(state: TripState) {
    setTrip(state); setRecommendations(state.research_results?.flatMap((result) => result.recommendations) ?? []); setSelections(state.selections ?? [])
    setItinerary(state.itinerary ?? null); setScreen(state.itinerary ? 'itinerary' : state.research_results?.length ? 'workspace' : 'planner')
    if (state.research_results?.length) setAgents(Object.fromEntries(state.research_results.map((result) => [result.agent_name, 'complete'])) as AgentStatus)
  }

  async function createTrip(preferences: TripPreferences) {
    setLoading(true); setError('')
    try {
      const { trip_id } = await api.createTrip(preferences)
      localStorage.setItem('travelbuddy.lastTrip', trip_id)
      const state: TripState = { trip_id, preferences }
      setTrip(state); setRecommendations([]); setSelections([]); setItinerary(null); setAgents(initialAgents); setScreen('workspace')
      await runResearch(trip_id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not create that trip.') }
    finally { setLoading(false) }
  }

  async function runResearch(tripId = trip?.trip_id) {
    if (!tripId) return
    setResearching(true); setError(''); setRecommendations([]); setAgents(initialAgents)
    try {
      await api.research(tripId, (event: StreamEvent) => {
        if (event.event === 'agent_started' && event.agent) setAgents((current) => ({ ...current, [event.agent!]: 'working' }))
        if (event.event === 'agent_completed' && event.agent) { setAgents((current) => ({ ...current, [event.agent!]: 'complete' })); setRecommendations((items) => [...items.filter((item) => item.category !== event.results?.[0]?.category), ...(event.results ?? [])]) }
        if (event.event === 'agent_failed' && event.agent) setAgents((current) => ({ ...current, [event.agent!]: 'failed' }))
        if (event.event === 'error') setError(event.error ?? 'Research failed.')
      })
      setTrip(await api.trip(tripId))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'The research crew got interrupted.') }
    finally { setResearching(false) }
  }

  async function buildItinerary() {
    if (!trip) return
    setBuilding(true); setError('')
    try {
      await api.select(trip.trip_id, selections)
      await api.itinerary(trip.trip_id, (event) => {
        if (event.event === 'itinerary_complete' && event.itinerary) { setItinerary(event.itinerary); setScreen('itinerary') }
        if (event.event === 'itinerary_failed') setError(event.error ?? 'The itinerary could not be generated.')
      })
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not assemble the itinerary.') }
    finally { setBuilding(false) }
  }

  async function logout() { await api.logout(); setUser(null); setProfile(null); setTrip(null); setScreen('planner') }
  async function retake() { await api.resetProfile(); setProfile(null); setUser((current) => current ? { ...current, intake_complete: false } : current); setProfileOpen(false) }

  if (loading && !trip) return <div className="full-state"><LoadingState /></div>
  if (!user) return <AuthView onAuthenticated={bootstrap} />
  if (!user.intake_complete || !profile) return <Onboarding onComplete={(next) => { setProfile(next); setUser({ ...user, intake_complete: true }); setScreen('planner') }} />

  return <div className="app-shell">
    <nav className="app-nav"><button className="brand-button" onClick={() => setScreen('planner')}><Brand /></button><div className="nav-route"><Compass /><span>{trip ? trip.preferences.destination : 'No active trip'}</span>{trip && <small>{screen}</small>}</div><div className="nav-actions"><Button variant="quiet" onClick={() => { setScreen('planner'); setTrip(null); localStorage.removeItem('travelbuddy.lastTrip') }}><Plus /> New trip</Button><button className="profile-button" onClick={() => setProfileOpen(true)}><UserRound /><span>{user.email.split('@')[0]}<small>Character profile</small></span></button><button className="icon-button" onClick={logout} aria-label="Log out"><LogOut /></button></div></nav>
    {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
    {screen === 'planner' && <TripForm onSubmit={createTrip} busy={loading} />}
    {screen === 'workspace' && trip && <Workspace destination={trip.preferences.destination} preferences={trip.preferences} profile={profile} recommendations={recommendations} agents={agents} researching={researching} selections={selections} onToggle={(id) => setSelections((items) => items.includes(id) ? items.filter((item) => item !== id) : [...items, id])} onAlternatives={() => runResearch()} onBuild={buildItinerary} />}
    {screen === 'itinerary' && itinerary && trip && <ItineraryView itinerary={itinerary} preferences={trip.preferences} onBack={() => setScreen('workspace')} />}
    {building && <div className="build-overlay"><LoadingState title="Shaping your final route" detail="Balancing time, cost, geography, and your preferred pace…" /></div>}
    {!trip && screen !== 'planner' && <ErrorState message="This trip is no longer available. Start a fresh route." onRetry={() => setScreen('planner')} />}
    <ProfileDrawer key={profile.updatedAt} open={profileOpen} profile={profile} onClose={() => setProfileOpen(false)} onUpdate={setProfile} onRetake={retake} />
  </div>
}
