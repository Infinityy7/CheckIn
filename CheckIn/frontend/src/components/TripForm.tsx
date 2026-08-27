import '../styles/planner.css'
import { useCallback, useMemo, useState } from 'react'
import { ArrowRight, CalendarDays, CircleDollarSign, MapPin, Navigation, Sparkles, Users } from 'lucide-react'
import type { TripPreferences } from '../types'
import { Mascot } from './Mascot'
import { Button, SegmentedControl } from './UI'
import { CompanionManager } from './CompanionManager'

/** Full backend vibe list (schemas.ALLOWED_VIBES) — keep in sync. */
const VIBES = ['adventure', 'culture', 'food', 'nightlife', 'relaxation', 'nature', 'shopping', 'history', 'romance', 'wellness', 'family-friendly'] as const

const GROUPS = ['solo', 'couple', 'friends', 'family'] as const
const CURRENCIES = ['USD', 'EUR', 'GBP', 'INR', 'JPY', 'AUD', 'CAD'] as const

function isoDate(offset: number) {
  const date = new Date(); date.setDate(date.getDate() + offset); return date.toISOString().slice(0, 10)
}

export function TripForm({ onSubmit, busy }: { onSubmit: (preferences: TripPreferences) => void; busy: boolean }) {
  const [form, setForm] = useState<TripPreferences>({
    destination: '', origin: '', start_date: isoDate(30), end_date: isoDate(36), budget_amount: 2200,
    currency: 'USD', vibes: ['culture', 'food', 'nature'], group_type: 'couple', num_travelers: 2,
    cotravellers: [], cotraveller_usernames: [],
  })
  const [guests, setGuests] = useState<string[]>([])
  const [blockers, setBlockers] = useState<string[]>([])

  const usernames = form.cotraveller_usernames
  const partySize = guests.length + usernames.length + 1
  const solo = form.group_type === 'solo'
  const duration = useMemo(() => Math.max(1, Math.round((new Date(form.end_date).getTime() - new Date(form.start_date).getTime()) / 86400000) + 1), [form.start_date, form.end_date])
  const set = <K extends keyof TripPreferences>(key: K, value: TripPreferences[K]) => setForm((current) => ({ ...current, [key]: value }))
  const toggleVibe = (vibe: string) => set('vibes', form.vibes.includes(vibe) ? form.vibes.filter((item) => item !== vibe) : [...form.vibes, vibe])

  const setGroup = (group: TripPreferences['group_type']) => setForm((current) => ({
    ...current,
    group_type: group,
    num_travelers: group === 'solo' ? 1 : Math.max(current.num_travelers, guests.length + current.cotraveller_usernames.length + 1, 2),
  }))

  const handleGuestsChange = useCallback((next: string[]) => {
    setGuests(next)
    setForm((current) => ({ ...current, num_travelers: Math.max(current.num_travelers, next.length + current.cotraveller_usernames.length + 1) }))
  }, [])
  const handleUsernamesChange = useCallback((next: string[]) => {
    setForm((current) => ({
      ...current,
      cotraveller_usernames: next,
      num_travelers: Math.max(current.num_travelers, guests.length + next.length + 1),
    }))
  }, [guests.length])
  const handleBlockedChange = useCallback((names: string[]) => setBlockers(names), [])

  /** "2 linked members · 1 guest" — keeps the two party kinds legible. */
  const partyLabel = useMemo(() => [
    usernames.length ? `${usernames.length} linked member${usernames.length === 1 ? '' : 's'}` : null,
    guests.length ? `${guests.length} guest${guests.length === 1 ? '' : 's'}` : null,
  ].filter(Boolean).join(' · '), [usernames.length, guests.length])

  const gate = solo ? [] : blockers
  const blocked = busy || !form.vibes.length || gate.length > 0

  const submit = () => onSubmit({
    ...form,
    num_travelers: solo ? 1 : form.num_travelers,
    cotravellers: solo ? [] : guests,
    cotraveller_usernames: solo ? [] : usernames,
  })

  return <main className="planner page-stage">
    <section className="planner-intro">
      <div>
        <span className="eyebrow"><Sparkles aria-hidden /> New expedition</span>
        <h1>Where should we<br /><em>point the compass?</em></h1>
        <p>Give Tavi the practical edges. Your character profile handles the rest.</p>
      </div>
      <div className="planner-mascot">
        <Mascot state="recommending" size="hero" />
        <div className="mascot-note">I’ll look for the places that match your pace—not just the places everyone posts.</div>
      </div>
    </section>
    <form className="trip-form corner-tick" onSubmit={(event) => { event.preventDefault(); submit() }}>
      <div className="trip-form__primary">
        <label className="field field--large">
          <MapPin aria-hidden /><span>Destination</span>
          <input required value={form.destination} onChange={(e) => set('destination', e.target.value)} placeholder="Kyoto, Japan" />
        </label>
        <span className="route-arrow" aria-hidden>→</span>
        <label className="field field--large">
          <Navigation aria-hidden /><span>Starting from</span>
          <input required value={form.origin} onChange={(e) => set('origin', e.target.value)} placeholder="Mumbai, India" />
        </label>
      </div>
      <div className="trip-form__grid">
        <fieldset className="form-section">
          <legend><CalendarDays aria-hidden /> When are you going?</legend>
          <div className="field-row">
            <label><span>Depart</span><input type="date" min={isoDate(0)} value={form.start_date} onChange={(e) => set('start_date', e.target.value)} /></label>
            <label><span>Return</span><input type="date" min={form.start_date} value={form.end_date} onChange={(e) => set('end_date', e.target.value)} /></label>
          </div>
          <strong className="duration-note">{duration} days to wander</strong>
        </fieldset>
        <fieldset className="form-section">
          <legend><CircleDollarSign aria-hidden /> What’s the trip budget?</legend>
          <div className="budget-control">
            <label><span>Currency</span><select value={form.currency} onChange={(e) => set('currency', e.target.value)}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</select></label>
            <label><span>Amount</span><input type="number" min="100" value={form.budget_amount} onChange={(e) => set('budget_amount', Number(e.target.value))} /></label>
          </div>
          <small>Total, for everyone. We’ll flag smart splurges.</small>
        </fieldset>
        <fieldset className="form-section form-section--party">
          <legend><Users aria-hidden /> Who’s coming?</legend>
          <SegmentedControl options={GROUPS} value={form.group_type} onChange={setGroup} ariaLabel="Travel party" />
          <label className="inline-input">
            <span>Travelers</span>
            <input type="number" min={solo ? 1 : partySize} max="50" value={form.num_travelers} disabled={solo}
              onChange={(e) => set('num_travelers', Math.max(partySize, Number(e.target.value) || 1))} />
          </label>
          {!solo && partyLabel && <small className="party-count" role="status">You + {partyLabel}</small>}
          {solo
            ? <p className="party-hint">Flying solo — Tavi tunes everything to your own taste profile.</p>
            : <CompanionManager
              guests={guests}
              onGuestsChange={handleGuestsChange}
              usernames={usernames}
              onUsernamesChange={handleUsernamesChange}
              onBlockedChange={handleBlockedChange}
            />}
        </fieldset>
        <fieldset className="form-section form-section--wide">
          <legend><Sparkles aria-hidden /> What should this trip feel like?</legend>
          <div className="tag-cloud">
            {VIBES.map((vibe) => <button type="button" key={vibe} className={form.vibes.includes(vibe) ? 'is-active' : ''} aria-pressed={form.vibes.includes(vibe)} onClick={() => toggleVibe(vibe)}>{vibe}</button>)}
          </div>
        </fieldset>
      </div>
      <footer className="trip-form__footer">
        <div>
          <p><strong>Tavi’s read:</strong> {form.vibes.slice(0, 3).join(', ') || 'open-ended'} · {form.group_type} · {duration} days{!solo && partyLabel ? ` · ${partyLabel}` : ''}</p>
          {gate.length > 0 && <p className="gate-note" role="status">
            Waiting on taste profiles: {gate.join(' · ')}. Guests can be profiled right here with “Profile now” — linked members finish their own intake in their account.
          </p>}
        </div>
        <Button type="submit" disabled={blocked}>{busy ? 'Opening a workspace…' : 'Research my trip'} <ArrowRight aria-hidden /></Button>
      </footer>
    </form>
  </main>
}
