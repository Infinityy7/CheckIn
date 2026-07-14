import { useMemo, useState } from 'react'
import { ArrowRight, BedDouble, Bike, Check, ChevronRight, Clock3, Coffee, HeartOff, Map, RefreshCw, Sparkles, Star, TrainFront, UtensilsCrossed } from 'lucide-react'
import type { CharacterProfile, Recommendation, TripPreferences } from '../types'
import { Mascot } from './Mascot'
import { Button, EmptyState } from './UI'

export type AgentStatus = Record<string, 'waiting' | 'working' | 'complete' | 'failed'>
const categories = [
  { id: 'transport', label: 'Transportation', icon: TrainFront },
  { id: 'hotel', label: 'Stays', icon: BedDouble },
  { id: 'activity', label: 'Activities', icon: Bike },
  { id: 'restaurant', label: 'Food', icon: UtensilsCrossed },
] as const

function scoreLabel(score: number) { return `${Math.round(score * 100)}%` }

function RecommendationCard({ item, selected, onSelect, onDislike }: { item: Recommendation; selected: boolean; onSelect: () => void; onDislike: () => void }) {
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
      <div className="recommendation__footer"><button className="dislike" onClick={onDislike} aria-label={`Show fewer places like ${item.name}`}><HeartOff /> Not my thing</button><Button variant={selected ? 'secondary' : 'primary'} onClick={onSelect}>{selected ? <><Check /> Selected</> : <>Choose this <ChevronRight /></>}</Button></div>
    </div>
  </article>
}

export function Workspace({ destination, preferences, profile, recommendations, agents, researching, selections, onToggle, onAlternatives, onBuild }: {
  destination: string
  preferences: TripPreferences
  profile: CharacterProfile | null
  recommendations: Recommendation[]
  agents: AgentStatus
  researching: boolean
  selections: string[]
  onToggle: (id: string) => void
  onAlternatives: () => void
  onBuild: () => void
}) {
  const available = categories.filter((category) => recommendations.some((item) => item.category === category.id))
  const [category, setCategory] = useState<string>(available[0]?.id ?? 'hotel')
  const [feedback, setFeedback] = useState<string[]>([])
  const active = recommendations.filter((item) => item.category === category).sort((a,b) => a.rank - b.rank)
  const completed = Object.values(agents).filter((state) => state === 'complete').length
  const selectedItems = useMemo(() => recommendations.filter((item) => selections.includes(item.id)), [recommendations, selections])

  return <main className="workspace page-stage">
    <header className="workspace-hero">
      <div><span className="eyebrow">LIVE TRIP WORKSPACE · {preferences.start_date.slice(5).replace('-', '.')}—{preferences.end_date.slice(5).replace('-', '.')}</span><h1>{destination}</h1><p>{preferences.vibes.join(' · ')} · {preferences.num_travelers} travelers · {preferences.currency} {preferences.budget_amount.toLocaleString()}</p></div>
      <div className="workspace-stage"><span>{researching ? 'Research in motion' : 'Recommendations ready'}</span><b>{completed}/4 agents</b><div><i style={{ width: `${completed * 25}%` }} /></div></div>
    </header>

    <section className="agent-rail" aria-label="Research agents">
      {Object.entries(agents).map(([name, status], index) => <div className={`agent-node agent-node--${status}`} key={name}>
        <span>{status === 'complete' ? <Check /> : status === 'working' ? <span className="pulse-dot" /> : index + 1}</span>
        <div><small>{status.toUpperCase()}</small><strong>{name.replace('Agent','')}</strong><p>{status === 'working' ? 'Searching, comparing, and checking fit…' : status === 'complete' ? 'Top three ranked' : 'Queued with your character profile'}</p></div>
      </div>)}
      <div className="agent-tavi"><Mascot state={researching ? 'thinking' : 'excited'} size="sm" /><p>{researching ? 'I’m cross-checking value against your pace.' : 'Your shortlist is ready to shape.'}</p></div>
    </section>

    <div className="workspace-layout">
      <section className="recommendations-panel">
        <div className="section-heading"><div><span className="eyebrow">Ranked for your character</span><h2>The shortlist</h2></div><button className="alternative-button" onClick={onAlternatives} disabled={researching}><RefreshCw /> Show alternatives</button></div>
        <div className="category-tabs" role="tablist">{categories.map(({ id, label, icon: Icon }) => <button role="tab" aria-selected={category === id} className={category === id ? 'is-active' : ''} key={id} onClick={() => setCategory(id)}><Icon />{label}<span>{recommendations.filter((item) => item.category === id).length}</span></button>)}</div>
        {active.length ? <div className="recommendation-list">{active.map((item) => <RecommendationCard key={item.id} item={item} selected={selections.includes(item.id)} onSelect={() => onToggle(item.id)} onDislike={() => setFeedback((items) => [...items, item.id])} />)}</div> : <EmptyState title={researching ? 'This agent is still out exploring' : 'Nothing landed in this category'} detail={researching ? 'Results arrive independently, so you can browse while the others work.' : 'Run the research again to search a wider route.'} />}
        {feedback.length > 0 && <p className="feedback-note"><Check /> Preference noted. Tavi will use it when you refresh the shortlist.</p>}
      </section>
      <aside className="trip-docket">
        <div className="trip-docket__head"><span className="eyebrow">Your travel docket</span><strong>{selections.length} selected</strong></div>
        {selectedItems.length ? <div className="docket-items">{selectedItems.map((item) => <button key={item.id} onClick={() => onToggle(item.id)}><span>{item.category === 'restaurant' ? <Coffee /> : item.category === 'transport' ? <TrainFront /> : item.category === 'hotel' ? <BedDouble /> : <Bike />}</span><div><strong>{item.name}</strong><small>{item.estimated_cost}</small></div><span>×</span></button>)}</div> : <p className="docket-empty">Choose the options that feel right. One from each category is a good starting point.</p>}
        <div className="profile-influence"><Mascot state="recommending" size="sm" /><p><strong>Profile influence</strong>{profile ? `${Math.round(profile.traits.localVsTourist * 100)}% local-leaning · ${profile.traits.pace} pace` : 'Your profile is guiding every score.'}</p></div>
        <div className="docket-stats"><span><Clock3 /> Pace check<b>{selections.length > 6 ? 'Full' : 'Balanced'}</b></span><span><Sparkles /> Match quality<b>{recommendations.length ? scoreLabel(Math.max(...recommendations.map((item) => item.score))) : '—'}</b></span></div>
        <Button onClick={onBuild} disabled={!selections.length || researching}>Build my itinerary <ArrowRight /></Button>
      </aside>
    </div>
  </main>
}
