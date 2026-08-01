import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, BedDouble, Bike, Check, ChevronRight, Clock3, Coffee, HeartOff, Map, RefreshCw, Sparkles, Star, TrainFront, UtensilsCrossed } from 'lucide-react'
import type { CharacterProfile, Recommendation, TripCart as TripCartModel, TripPreferences } from '../types'
import { api, userErrorMessage } from '../services/api'
import { Mascot } from './Mascot'
import { Button, EmptyState } from './UI'
import { HotelInventory } from './HotelInventory'
import { TransportJourney } from './TransportJourney'
import { TripCart } from './TripCart'

export type AgentStatus = Record<string, 'waiting' | 'working' | 'complete' | 'failed'>
const categories = [
  { id: 'transport', label: 'Transportation', icon: TrainFront },
  { id: 'hotel', label: 'Stays', icon: BedDouble },
  { id: 'activity', label: 'Activities', icon: Bike },
  { id: 'restaurant', label: 'Food', icon: UtensilsCrossed },
] as const

function scoreLabel(score: number) { return `${Math.round(score * 100)}%` }

function RecommendationCard({ tripId, preferences, item, selected, onSelect, onInventorySelect, onDislike, onCartChange }: {
  tripId: string
  preferences: TripPreferences
  item: Recommendation
  selected: boolean
  onSelect: () => void | Promise<void>
  onInventorySelect: () => void
  onDislike: () => void
  onCartChange: (cart: TripCartModel) => void
}) {
  const [choosing, setChoosing] = useState(false)
  const [choiceError, setChoiceError] = useState('')

  async function choose() {
    setChoosing(true); setChoiceError('')
    try { await onSelect() }
    catch (reason) { setChoiceError(userErrorMessage(reason, 'That choice could not be saved.')) }
    finally { setChoosing(false) }
  }

  return <article className={`recommendation ${selected ? 'is-selected' : ''}`}>
    <div className="recommendation__visual">
      <span className="rank-badge"><b>#{item.rank}</b><small>MATCH</small></span>
      <div className="recommendation__map-motif"><Map /><span>{item.location}</span></div>
      <div className="score-orbit"><svg viewBox="0 0 46 46"><circle cx="23" cy="23" r="19" /><circle className="score-orbit__value" cx="23" cy="23" r="19" pathLength="100" strokeDasharray={`${Math.round(item.score * 100)} 100`} /></svg><b>{scoreLabel(item.score)}</b></div>
    </div>
    <div className="recommendation__body">
      <div className="recommendation__meta"><span><Star fill="currentColor" /> {item.rating.toFixed(1)} · {item.review_count.toLocaleString()} reviews</span><span>{item.estimated_cost}</span></div>
      <h3>{item.name}</h3><p>{item.description}</p>
      <div className="profile-match"><Sparkles /><p><strong>Why Tavi picked this</strong>{item.reasoning}</p></div>
      {item.category === 'transport' && <TransportJourney tripId={tripId} recommendation={item} preferences={preferences} onCartChange={onCartChange} onFlightAdded={() => { if (!selected) onInventorySelect() }} />}
      <div className="recommendation__footer"><button className="dislike" onClick={onDislike} aria-label={`Show fewer places like ${item.name}`}><HeartOff /> Not my thing</button><Button variant={selected ? 'secondary' : 'primary'} onClick={() => void choose()} disabled={choosing}>{choosing ? 'Saving…' : selected ? <><Check /> Selected</> : <>Choose this <ChevronRight /></>}</Button></div>
      {choiceError && <p className="recommendation__error" role="alert"><AlertTriangle /> {choiceError}</p>}
    </div>
    {item.category === 'hotel' && <HotelInventory tripId={tripId} recommendation={item} onCartChange={onCartChange} onAdded={() => { if (!selected) onInventorySelect() }} />}
  </article>
}

export function Workspace({ tripId, destination, preferences, profile, recommendations, agents, researching, selections, onToggle, onAlternatives, onFeedback, onBuild }: {
  tripId: string
  destination: string
  preferences: TripPreferences
  profile: CharacterProfile | null
  recommendations: Recommendation[]
  agents: AgentStatus
  researching: boolean
  selections: string[]
  onToggle: (id: string) => void
  onAlternatives: () => void
  onFeedback: (item: Recommendation) => Promise<void>
  onBuild: () => void
}) {
  const available = categories.filter((category) => recommendations.some((item) => item.category === category.id))
  const [category, setCategory] = useState<string>(available[0]?.id ?? 'hotel')
  const [feedback, setFeedback] = useState<string[]>([])
  const [cart, setCart] = useState<TripCartModel | null>(null)
  const [cartLoading, setCartLoading] = useState(true)
  const [cartError, setCartError] = useState('')
  const active = recommendations.filter((item) => item.category === category).sort((a,b) => a.rank - b.rank)
  const completed = Object.values(agents).filter((state) => state === 'complete').length
  const failed = Object.values(agents).filter((state) => state === 'failed').length
  const selectedItems = useMemo(() => recommendations.filter((item) => selections.includes(item.id)), [recommendations, selections])

  useEffect(() => {
    let active = true
    api.cart(tripId).then((next) => { if (active) { setCart(next); setCartError('') } }).catch((reason) => {
      if (active) setCartError(userErrorMessage(reason, 'Your booking cart is unavailable right now.'))
    }).finally(() => { if (active) setCartLoading(false) })
    return () => { active = false }
  }, [tripId])

  async function recordFeedback(item: Recommendation) {
    try {
      await onFeedback(item)
      setFeedback((items) => [...items, item.id])
    } catch {
      // App owns the global error notice; avoid an unhandled rejected promise here.
    }
  }

  async function toggleChoice(item: Recommendation) {
    if (item.category === 'hotel' || item.category === 'activity') { onToggle(item.id); return }
    const existing = cart?.items.find((cartItem) => cartItem.recommendationId === item.id)
    if (selections.includes(item.id)) {
      if (existing) setCart(await api.removeCartItem(tripId, existing.id))
      onToggle(item.id)
      return
    }
    if (!existing) setCart(await api.addCartItem(tripId, item.id, undefined, item.category === 'restaurant' ? 'restaurant' : 'ride'))
    onToggle(item.id)
  }

  return <main className="workspace page-stage">
    <header className="workspace-hero">
      <div><span className="eyebrow">LIVE TRIP WORKSPACE · {preferences.start_date.slice(5).replace('-', '.')}—{preferences.end_date.slice(5).replace('-', '.')}</span><h1>{destination}</h1><p>{preferences.vibes.join(' · ')} · {preferences.num_travelers} travelers · {preferences.currency} {preferences.budget_amount.toLocaleString()}</p></div>
      <div className="workspace-stage"><span>{researching ? 'Research in motion' : failed ? 'Partial results ready' : 'Recommendations ready'}</span><b>{completed}/4 agents</b><div><i style={{ width: `${completed * 25}%` }} /></div></div>
    </header>

    <section className="agent-rail" aria-label="Research agents">
      {Object.entries(agents).map(([name, status], index) => <div className={`agent-node agent-node--${status}`} key={name}>
        <span>{status === 'complete' ? <Check /> : status === 'failed' ? <AlertTriangle /> : status === 'working' ? <span className="pulse-dot" /> : index + 1}</span>
        <div><small>{status.toUpperCase()}</small><strong>{name.replace('Agent','')}</strong><p>{status === 'working' ? 'Searching, comparing, and checking fit…' : status === 'complete' ? 'Top three ranked' : status === 'failed' ? 'Could not finish — retry available' : 'Queued with your character profile'}</p></div>
      </div>)}
      <div className="agent-tavi"><Mascot state={researching ? 'thinking' : failed ? 'confused' : 'excited'} size="sm" /><p>{researching ? 'I’m cross-checking value against your pace.' : failed ? 'I kept the successful results. We can retry the missing route.' : 'Your shortlist is ready to shape.'}</p></div>
    </section>

    {failed > 0 && !researching && <section className="partial-results" role="status"><AlertTriangle /><div><strong>{failed} research {failed === 1 ? 'category needs' : 'categories need'} another try</strong><p>Your completed recommendations are still here. Retrying reruns only the missing categories.</p></div><Button variant="secondary" onClick={onAlternatives}><RefreshCw /> Retry research</Button></section>}

    <div className="workspace-layout">
      <section className="recommendations-panel">
        <div className="section-heading"><div><span className="eyebrow">Ranked for your character</span><h2>The shortlist</h2></div><button className="alternative-button" onClick={onAlternatives} disabled={researching}><RefreshCw /> Show alternatives</button></div>
        <div className="category-tabs" role="tablist">{categories.map(({ id, label, icon: Icon }) => <button role="tab" aria-selected={category === id} className={category === id ? 'is-active' : ''} key={id} onClick={() => setCategory(id)}><Icon />{label}<span>{recommendations.filter((item) => item.category === id).length}</span></button>)}</div>
        {active.length ? <div className="recommendation-list">{active.map((item) => <RecommendationCard key={item.id} tripId={tripId} preferences={preferences} item={item} selected={selections.includes(item.id)} onSelect={() => toggleChoice(item)} onInventorySelect={() => { if (!selections.includes(item.id)) onToggle(item.id) }} onDislike={() => void recordFeedback(item)} onCartChange={(next) => { setCart(next); setCartError('') }} />)}</div> : <EmptyState title={researching ? 'This agent is still out exploring' : 'Nothing landed in this category'} detail={researching ? 'Results arrive independently, so you can browse while the others work.' : 'Run the research again to search a wider route.'} action={!researching ? <Button variant="secondary" onClick={onAlternatives}><RefreshCw /> Retry research</Button> : undefined} />}
        {feedback.length > 0 && <p className="feedback-note"><Check /> Preference noted. Tavi will use it when you refresh the shortlist.</p>}
      </section>
      <aside className="trip-docket">
        <div className="trip-docket__head"><span className="eyebrow">Your travel docket</span><strong>{selections.length} selected</strong></div>
        {selectedItems.length ? <div className="docket-items">{selectedItems.map((item) => <button key={item.id} onClick={() => void toggleChoice(item).catch((reason) => setCartError(userErrorMessage(reason, 'That choice could not be removed.')))}><span>{item.category === 'restaurant' ? <Coffee /> : item.category === 'transport' ? <TrainFront /> : item.category === 'hotel' ? <BedDouble /> : <Bike />}</span><div><strong>{item.name}</strong><small>{item.estimated_cost}</small></div><span>×</span></button>)}</div> : <p className="docket-empty">Choose the options that feel right. One from each category is a good starting point.</p>}
        <TripCart tripId={tripId} cart={cart} loading={cartLoading} error={cartError} onCartChange={(next) => { setCart(next); setCartError('') }} onError={setCartError} />
        <div className="profile-influence"><Mascot state="recommending" size="sm" /><p><strong>Profile influence</strong>{profile?.weights ? `${Math.round(profile.weights.spontaneity * 100)}% spontaneous · ${Object.entries(profile.weights.vibeWeights).sort((a, b) => Number(b[1]) - Number(a[1]))[0]?.[0] ?? 'personal'}-leaning` : profile?.traits ? `${Math.round(profile.traits.localVsTourist * 100)}% local-leaning · ${profile.traits.pace} pace` : 'Your profile is guiding every score.'}</p></div>
        <div className="docket-stats"><span><Clock3 /> Pace check<b>{selections.length > 6 ? 'Full' : 'Balanced'}</b></span><span><Sparkles /> Match quality<b>{recommendations.length ? scoreLabel(Math.max(...recommendations.map((item) => item.score))) : '—'}</b></span></div>
        <Button onClick={onBuild} disabled={!selections.length || researching}>Build my itinerary <ArrowRight /></Button>
      </aside>
    </div>
  </main>
}
