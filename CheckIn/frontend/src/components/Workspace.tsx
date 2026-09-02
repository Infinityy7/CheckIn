import '../styles/workspace.css'
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, BedDouble, Bike, Check, ChevronRight, Clock3, Coffee, Map, RefreshCw, Sparkles, Star, ThumbsDown, ThumbsUp, TrainFront, Users, UtensilsCrossed } from 'lucide-react'
import type { CharacterProfile, Recommendation, TripCart as TripCartModel, TripPreferences } from '../types'
import { cacheInfo } from '../types'
import { ApiError, api, userErrorMessage } from '../services/api'
import { Mascot } from './Mascot'
import { Banner, Button, CachedBadge, Chip, EmptyState, Meter, Modal } from './UI'
import { HotelInventory } from './HotelInventory'
import { TransportJourney } from './TransportJourney'
import { CART_REFRESHED_NOTICE, TripCart } from './TripCart'

export type AgentStatus = Record<string, 'waiting' | 'working' | 'complete' | 'failed'>
type Sentiment = 'like' | 'dislike'

const categories = [
  { id: 'transport', label: 'Transportation', icon: TrainFront },
  { id: 'hotel', label: 'Stays', icon: BedDouble },
  { id: 'activity', label: 'Activities', icon: Bike },
  { id: 'restaurant', label: 'Food', icon: UtensilsCrossed },
] as const

const rankedSignals = [
  ['budget', 'Budget'],
  ['taste', 'Taste'],
  ['rating', 'Rating'],
  ['vibes', 'Vibes'],
] as const

/** Categories whose selection is mirrored by a cart item; removing that item deselects the card. */
const cartBackedCategories = new Set<Recommendation['category']>(['transport', 'restaurant'])

function scoreLabel(score: number) { return `${Math.round(score * 100)}%` }

function RecommendationCard({ tripId, preferences, item, selected, companions, noted, cart, onSelect, onInventorySelect, onFeedback, onCartChange }: {
  tripId: string
  preferences: TripPreferences
  item: Recommendation
  selected: boolean
  companions: string[]
  noted: Sentiment | null
  cart: TripCartModel | null
  onSelect: () => void | Promise<void>
  onInventorySelect: () => void
  onFeedback: (sentiment: Sentiment) => Promise<void>
  onCartChange: (cart: TripCartModel) => void
}) {
  const [choosing, setChoosing] = useState(false)
  const [choiceError, setChoiceError] = useState('')
  const [sending, setSending] = useState<Sentiment | null>(null)
  const cached = cacheInfo(item)
  const breakdown = item.score_breakdown
  const signals = rankedSignals.filter(([key]) => typeof breakdown[key] === 'number')
  const matched = breakdown.matched ?? []
  const hasRankedDetail = signals.length > 0 || matched.length > 0 || companions.length > 0

  async function choose() {
    setChoosing(true); setChoiceError('')
    try { await onSelect() }
    catch (reason) { setChoiceError(userErrorMessage(reason, 'That choice could not be saved.')) }
    finally { setChoosing(false) }
  }

  async function sendFeedback(sentiment: Sentiment) {
    setSending(sentiment)
    try { await onFeedback(sentiment) }
    finally { setSending(null) }
  }

  return <article className={`wk-card corner-tick ${selected ? 'is-selected' : ''} ${cached ? 'wk-card--cached' : ''}`}>
    <div className="wk-card__visual">
      <span className="wk-card__rank"><b>#{item.rank}</b><small>match</small></span>
      <div className="wk-card__map"><Map aria-hidden /><span>{item.location}</span></div>
      <div className="wk-score" role="img" aria-label={`Match score ${scoreLabel(item.score)}`}>
        <svg viewBox="0 0 46 46" aria-hidden><circle cx="23" cy="23" r="19" /><circle className="wk-score__value" cx="23" cy="23" r="19" pathLength="100" strokeDasharray={`${Math.round(item.score * 100)} 100`} /></svg>
        <b>{scoreLabel(item.score)}</b>
      </div>
    </div>
    <div className="wk-card__body">
      {(cached || companions.length > 0) && <div className="wk-card__chips">
        {cached && <CachedBadge ageSeconds={cached.ageSeconds} similarity={cached.similarity} />}
        {companions.length > 0 && <Chip tone="brand" icon={<Users aria-hidden />}>Balanced across {companions.length + 1} travellers</Chip>}
      </div>}
      <div className="wk-card__meta"><span><Star aria-hidden fill="currentColor" /> {item.rating.toFixed(1)} · {item.review_count.toLocaleString()} reviews</span><span>{item.estimated_cost}</span></div>
      <h3>{item.name}</h3>
      <p className="wk-card__blurb">{item.description}</p>
      <div className="wk-card__reason"><Sparkles aria-hidden /><p><strong>Why Tavi picked this</strong>{item.reasoning}</p></div>
      {hasRankedDetail && <details className="wk-ranked">
        <summary>Why this ranked</summary>
        <div className="wk-ranked__body">
          {signals.map(([key, label]) => <Meter key={key} label={label} value={breakdown[key] as number} />)}
          {matched.length > 0 && <div className="wk-ranked__tags" aria-label="Matched taste tags">{matched.map((tag) => <Chip key={tag} tone="brand">{tag}</Chip>)}</div>}
          {companions.length > 0 && <p className="wk-ranked__note">Options that clashed with anyone's no-gos or diets were removed before ranking.</p>}
        </div>
      </details>}
      {item.category === 'transport' && <TransportJourney tripId={tripId} recommendation={item} preferences={preferences} cart={cart} onCartChange={onCartChange} onFlightAdded={() => { if (!selected) onInventorySelect() }} />}
      <div className="wk-card__footer">
        <div className="wk-feedback" role="group" aria-label={`Feedback on ${item.name}`}>
          <button type="button" className={`wk-feedback__button ${noted === 'like' ? 'is-noted' : ''}`} aria-pressed={noted === 'like'} aria-label={`More like this: ${item.name}`} title="More like this" disabled={sending !== null} onClick={() => void sendFeedback('like')}><ThumbsUp aria-hidden /></button>
          <button type="button" className={`wk-feedback__button ${noted === 'dislike' ? 'is-noted' : ''}`} aria-pressed={noted === 'dislike'} aria-label={`Not my thing: ${item.name}`} title="Not my thing" disabled={sending !== null} onClick={() => void sendFeedback('dislike')}><ThumbsDown aria-hidden /></button>
          {noted && <span className="wk-feedback__noted" role="status"><Check aria-hidden /> {noted === 'like' ? 'Noted — more like this' : 'Noted — fewer like this'}</span>}
        </div>
        <Button variant={selected ? 'secondary' : 'primary'} onClick={() => void choose()} disabled={choosing}>{choosing ? 'Saving…' : selected ? <><Check aria-hidden /> Selected</> : <>Choose this <ChevronRight aria-hidden /></>}</Button>
      </div>
      {choiceError && <p className="wk-card__error" role="alert"><AlertTriangle aria-hidden /> {choiceError}</p>}
    </div>
    {item.category === 'hotel' && <HotelInventory tripId={tripId} recommendation={item} cart={cart} onCartChange={onCartChange} onAdded={() => { if (!selected) onInventorySelect() }} />}
  </article>
}

export function Workspace({ tripId, destination, preferences, profile, recommendations, agents, researching, selections, companions, onToggle, onRetryMissing, onFullRefresh, onFeedback, onBuild }: {
  tripId: string
  destination: string
  preferences: TripPreferences
  profile: CharacterProfile | null
  recommendations: Recommendation[]
  agents: AgentStatus
  researching: boolean
  selections: string[]
  companions: string[]
  onToggle: (id: string) => void | Promise<void>
  onRetryMissing: () => void
  onFullRefresh: () => void
  onFeedback: (item: Recommendation, sentiment: Sentiment) => Promise<void>
  onBuild: () => void
}) {
  const available = categories.filter((category) => recommendations.some((item) => item.category === category.id))
  const [category, setCategory] = useState<string>(available[0]?.id ?? 'hotel')
  const [noted, setNoted] = useState<Record<string, Sentiment>>({})
  const [confirmingRefresh, setConfirmingRefresh] = useState(false)
  const [cart, setCart] = useState<TripCartModel | null>(null)
  const [cartLoading, setCartLoading] = useState(true)
  const [cartError, setCartError] = useState('')

  const active = recommendations.filter((item) => item.category === category).sort((a, b) => a.rank - b.rank)
  const agentEntries = Object.entries(agents)
  const completed = agentEntries.filter(([, status]) => status === 'complete').length
  const failed = agentEntries.filter(([, status]) => status === 'failed').length
  const allComplete = agentEntries.length > 0 && completed === agentEntries.length
  /* Full refresh is only offered when the backend would actually run one: nothing mid-flight,
     no failed categories (those get a partial retry instead). */
  const canFullRefresh = allComplete && !researching
  const anyCached = recommendations.some((item) => cacheInfo(item) !== null)
  const selectedItems = useMemo(() => recommendations.filter((item) => selections.includes(item.id)), [recommendations, selections])

  useEffect(() => {
    let alive = true
    api.cart(tripId).then((next) => { if (alive) { setCart(next); setCartError('') } }).catch((reason) => {
      if (alive) setCartError(userErrorMessage(reason, 'Your booking cart is unavailable right now.'))
    }).finally(() => { if (alive) setCartLoading(false) })
    return () => { alive = false }
  }, [tripId])

  async function recordFeedback(item: Recommendation, sentiment: Sentiment) {
    try {
      await onFeedback(item, sentiment)
      setNoted((prev) => ({ ...prev, [item.id]: sentiment }))
    } catch {
      // App owns the global error notice; avoid an unhandled rejected promise here.
    }
  }

  /** App persists the toggle and reverts on failure; the cart panel just needs the reason. */
  function requestToggle(id: string) {
    void Promise.resolve(onToggle(id)).catch((reason) => setCartError(userErrorMessage(reason, 'That choice could not be saved.')))
  }

  /* A removed cart item is the only way a transport or restaurant choice leaves the cart, so the
     card must follow it. Hotels stay selected: a hotel can be chosen without an exact rate. */
  function reconcileSelections(previous: TripCartModel | null, next: TripCartModel) {
    const remaining = new Set(next.items.map((item) => item.recommendationId))
    const dropped = new Set((previous?.items ?? []).map((item) => item.recommendationId).filter((id) => !remaining.has(id)))
    for (const id of dropped) {
      const item = recommendations.find((entry) => entry.id === id)
      if (item && cartBackedCategories.has(item.category) && selections.includes(id)) requestToggle(id)
    }
  }

  function applyCart(next: TripCartModel) {
    reconcileSelections(cart, next); setCart(next); setCartError('')
  }

  async function toggleChoice(item: Recommendation) {
    if (!cartBackedCategories.has(item.category)) { await onToggle(item.id); return }
    const existing = cart?.items.find((cartItem) => cartItem.recommendationId === item.id)
    try {
      if (selections.includes(item.id)) {
        if (existing) setCart(await api.removeCartItem(tripId, existing.id, cart?.version))
      } else if (!existing) {
        setCart(await api.addCartItem(tripId, item.id, undefined, item.category === 'restaurant' ? 'restaurant' : 'ride'))
      }
      setCartError('')
    } catch (reason) {
      if (reason instanceof ApiError && reason.code === 'CART_VERSION_CONFLICT') {
        setCart(await api.cart(tripId).catch(() => cart))
        setCartError(`${CART_REFRESHED_NOTICE} Try that choice again.`)
      } else {
        setCartError(userErrorMessage(reason, 'Your cart could not be updated, so that choice was left unchanged.'))
      }
      throw reason
    }
    await onToggle(item.id)
  }

  return <main className="wk-workspace">
    <header className="wk-hero">
      <div>
        <span className="eyebrow">Trip workspace · {preferences.start_date.slice(5).replace('-', '.')}—{preferences.end_date.slice(5).replace('-', '.')}</span>
        <h1>{destination}</h1>
        <p>{preferences.vibes.join(' · ')} · {preferences.num_travelers} travelers · {preferences.currency} {preferences.budget_amount.toLocaleString()}</p>
      </div>
      <div className="wk-stage">
        <span role="status">{researching ? 'Research in motion' : failed ? 'Partial results ready' : 'Recommendations ready'}</span>
        <b>{completed}/4 agents</b>
        <div className="wk-stage__track"><i style={{ width: `${completed * 25}%` }} /></div>
      </div>
    </header>

    <section className="wk-rail" aria-label="Research agents">
      {agentEntries.map(([name, status], index) => <div className={`wk-agent wk-agent--${status}`} key={name}>
        <span className="wk-agent__marker" aria-hidden>{status === 'complete' ? <Check /> : status === 'failed' ? <AlertTriangle /> : status === 'working' ? <i className="wk-pulse" /> : index + 1}</span>
        <div>
          <small>{status}</small>
          <strong>{name.replace('Agent', '').trim()}</strong>
          <p>{status === 'working' ? 'Searching, comparing, and checking fit…' : status === 'complete' ? 'Top three ranked' : status === 'failed' ? 'Could not finish — retry available' : 'Queued with your character profile'}</p>
        </div>
      </div>)}
      <div className="wk-rail__tavi"><Mascot state={researching ? 'thinking' : failed ? 'confused' : 'excited'} size="sm" /><p>{researching ? 'I’m cross-checking value against your pace.' : failed ? 'I kept the successful results. We can retry the missing categories.' : 'Your shortlist is ready to shape.'}</p></div>
    </section>

    {(anyCached || (failed > 0 && !researching)) && <div className="wk-notices">
      {failed > 0 && !researching && <Banner tone="warn"
        title={`${failed} research ${failed === 1 ? 'category needs' : 'categories need'} another try`}
        detail="Your completed recommendations are kept and their IDs stay stable. Retrying reruns only the missing categories."
        action={<Button variant="secondary" onClick={onRetryMissing}><RefreshCw aria-hidden /> Retry missing categories</Button>} />}
      {anyCached && <Banner tone="info"
        title="Some results were served from CheckIn's research cache"
        detail="Cached picks show their age and taste match — they are not fresh live quotes."
        action={canFullRefresh ? <Button variant="secondary" onClick={() => setConfirmingRefresh(true)}><RefreshCw aria-hidden /> Refresh live</Button> : undefined} />}
    </div>}

    <div className="wk-layout">
      <section className="wk-shortlist">
        <div className="wk-heading">
          <div><span className="eyebrow">Ranked for your character</span><h2>The shortlist</h2></div>
          {canFullRefresh && <Button variant="quiet" className="wk-refresh" onClick={() => setConfirmingRefresh(true)}><RefreshCw aria-hidden /> Full refresh</Button>}
        </div>
        <div className="wk-tabs" role="tablist" aria-label="Recommendation categories">{categories.map(({ id, label, icon: Icon }) => <button type="button" role="tab" aria-selected={category === id} className={category === id ? 'is-active' : ''} key={id} onClick={() => setCategory(id)}><Icon aria-hidden />{label}<span>{recommendations.filter((item) => item.category === id).length}</span></button>)}</div>
        {active.length ? <div className="wk-list">{active.map((item) => <RecommendationCard
          key={item.id}
          tripId={tripId}
          preferences={preferences}
          item={item}
          selected={selections.includes(item.id)}
          companions={companions}
          noted={noted[item.id] ?? null}
          cart={cart}
          onSelect={() => toggleChoice(item)}
          onInventorySelect={() => { if (!selections.includes(item.id)) requestToggle(item.id) }}
          onFeedback={(sentiment) => recordFeedback(item, sentiment)}
          onCartChange={applyCart}
        />)}</div> : <EmptyState
          title={researching ? 'This agent is still out exploring' : 'Nothing landed in this category'}
          detail={researching ? 'Results arrive independently, so you can browse while the others work.' : failed > 0 ? 'Retry the missing categories — completed picks stay where they are.' : 'Run a full refresh to search a wider route.'}
          action={researching ? undefined : failed > 0 ? <Button variant="secondary" onClick={onRetryMissing}><RefreshCw aria-hidden /> Retry missing categories</Button> : canFullRefresh ? <Button variant="secondary" onClick={() => setConfirmingRefresh(true)}><RefreshCw aria-hidden /> Full refresh</Button> : undefined} />}
      </section>

      <aside className="wk-docket">
        <div className="wk-docket__head"><span className="eyebrow">Your travel docket</span><strong>{selections.length} selected</strong></div>
        {companions.length > 0 && <div className="wk-docket__group">
          <Chip tone="brand" icon={<Users aria-hidden />}>Balanced across {companions.length + 1} travellers</Chip>
          <p>Ranked for you and {companions.join(', ')} together.</p>
        </div>}
        {selectedItems.length ? <div className="wk-docket__items">{selectedItems.map((item) => <button type="button" key={item.id} aria-label={`Remove ${item.name} from your docket`} onClick={() => void toggleChoice(item).catch(() => undefined)}>
          <span className="wk-docket__icon" aria-hidden>{item.category === 'restaurant' ? <Coffee /> : item.category === 'transport' ? <TrainFront /> : item.category === 'hotel' ? <BedDouble /> : <Bike />}</span>
          <div><strong>{item.name}</strong><small>{item.estimated_cost}</small></div>
          <span aria-hidden>×</span>
        </button>)}</div> : <p className="wk-docket__empty">Choose the options that feel right. One from each category is a good starting point.</p>}
        <TripCart tripId={tripId} cart={cart} loading={cartLoading} error={cartError} onCartChange={applyCart} onError={setCartError} />
        <div className="wk-docket__profile"><Mascot state="recommending" size="sm" /><p><strong>Profile influence</strong>{profile?.weights ? `${Math.round(profile.weights.spontaneity * 100)}% spontaneous · ${Object.entries(profile.weights.vibeWeights).sort((a, b) => Number(b[1]) - Number(a[1]))[0]?.[0] ?? 'personal'}-leaning` : profile?.traits ? `${Math.round(profile.traits.localVsTourist * 100)}% local-leaning · ${profile.traits.pace} pace` : 'Your profile is guiding every score.'}</p></div>
        <div className="wk-docket__stats">
          <span><Clock3 aria-hidden /> Pace check<b>{selections.length > 6 ? 'Full' : 'Balanced'}</b></span>
          <span><Sparkles aria-hidden /> Match quality<b>{recommendations.length ? scoreLabel(Math.max(...recommendations.map((item) => item.score))) : '—'}</b></span>
        </div>
        <Button onClick={onBuild} disabled={!selections.length || researching}>Build my itinerary <ArrowRight aria-hidden /></Button>
      </aside>
    </div>

    <Modal open={confirmingRefresh && canFullRefresh} title="Refresh the whole shortlist?" eyebrow="Full refresh" onClose={() => setConfirmingRefresh(false)}>
      <p className="wk-confirm__copy">This reruns research for all four categories against live sources. Every current recommendation is replaced and your current selections are cleared.</p>
      <div className="wk-confirm__actions">
        <Button variant="secondary" onClick={() => setConfirmingRefresh(false)}>Keep my shortlist</Button>
        <Button onClick={() => { setConfirmingRefresh(false); onFullRefresh() }}>Replace everything</Button>
      </div>
    </Modal>
  </main>
}
