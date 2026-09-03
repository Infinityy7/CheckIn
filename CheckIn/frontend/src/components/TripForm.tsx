import '../styles/planner.css'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowRight, CalendarDays, CircleDollarSign, ListChecks, LoaderCircle, MapPin, Navigation, Sparkles, Users } from 'lucide-react'
import type { FeasibilityReport, TripPreferences, TripScope } from '../types'
import { loadTripDraft, saveTripDraft } from '../services/tripDraft'
import { ALL_SCOPES, canonicalScope, SCOPE_OPTIONS } from '../scope'
import { Mascot } from './Mascot'
import { Banner, Button, SegmentedControl } from './UI'
import { CompanionManager } from './CompanionManager'

/** Full backend vibe list (schemas.ALLOWED_VIBES) — keep in sync. */
const VIBES = ['adventure', 'culture', 'food', 'nightlife', 'relaxation', 'nature', 'shopping', 'history', 'romance', 'wellness', 'family-friendly'] as const

const GROUPS = ['solo', 'couple', 'friends', 'family'] as const
const CURRENCIES = ['USD', 'EUR', 'GBP', 'INR', 'JPY', 'AUD', 'CAD'] as const
const DRAFT_SAVE_DELAY_MS = 300

function isoDate(offset: number) {
  const date = new Date(); date.setDate(date.getDate() + offset); return date.toISOString().slice(0, 10)
}

function defaultForm(): TripPreferences {
  return {
    destination: '', origin: '', start_date: isoDate(30), end_date: isoDate(36), budget_amount: 2200,
    currency: 'USD', vibes: ['culture', 'food', 'nature'], group_type: 'couple', num_travelers: 2,
    cotravellers: [], cotraveller_usernames: [], scope: [...ALL_SCOPES],
  }
}

function initialState(): { form: TripPreferences; guests: string[] } {
  const base = defaultForm()
  const draft = loadTripDraft()
  if (!draft) return { form: base, guests: [] }
  const form = { ...base, ...draft.form }
  if (form.start_date < isoDate(0)) { form.start_date = base.start_date; form.end_date = base.end_date }
  return { form, guests: draft.guests }
}

export function TripForm({ onSubmit, busy, feasibility, onProceedAnyway, onDismissWarning }: {
  onSubmit: (preferences: TripPreferences) => void | Promise<void>
  busy: boolean
  feasibility?: FeasibilityReport | null
  onProceedAnyway?: (preferences: TripPreferences) => void | Promise<void>
  onDismissWarning?: () => void
}) {
  const [initial] = useState(initialState)
  const [form, setForm] = useState<TripPreferences>(initial.form)
  const [guests, setGuests] = useState<string[]>(initial.guests)
  const [blockers, setBlockers] = useState<string[]>([])

  useEffect(() => {
    if (form === initial.form && guests === initial.guests) return
    const timer = window.setTimeout(() => saveTripDraft({ form, guests }), DRAFT_SAVE_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [form, guests, initial])

  const usernames = form.cotraveller_usernames
  const partySize = guests.length + usernames.length + 1
  const solo = form.group_type === 'solo'
  const duration = useMemo(() => Math.max(1, Math.round((new Date(form.end_date).getTime() - new Date(form.start_date).getTime()) / 86400000) + 1), [form.start_date, form.end_date])
  const set = <K extends keyof TripPreferences>(key: K, value: TripPreferences[K]) => setForm((current) => ({ ...current, [key]: value }))
  const toggleVibe = (vibe: string) => set('vibes', form.vibes.includes(vibe) ? form.vibes.filter((item) => item !== vibe) : [...form.vibes, vibe])
  const scope = form.scope ?? ALL_SCOPES
  const fullTrip = ALL_SCOPES.every((id) => scope.includes(id))
  const toggleScope = (id: TripScope) => set('scope', ALL_SCOPES.filter((item) => item === id ? !scope.includes(id) : scope.includes(item)))
  const scopeRead = fullTrip || !scope.length ? '' : ` · ${SCOPE_OPTIONS.filter((option) => scope.includes(option.id)).map((option) => option.read).join(' & ')} only`

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
  const blocked = busy || !form.vibes.length || !scope.length || gate.length > 0

  const hasSuggestion = Boolean(
    feasibility?.suggested_changes.budget_amount
    || feasibility?.suggested_changes.end_date
    || feasibility?.suggested_changes.destination,
  )
  const applySuggestion = () => {
    const changes = feasibility?.suggested_changes
    if (!changes) return
    setForm((current) => ({
      ...current,
      ...(changes.budget_amount ? { budget_amount: changes.budget_amount } : {}),
      ...(changes.end_date ? { end_date: changes.end_date } : {}),
      ...(changes.destination ? { destination: changes.destination } : {}),
    }))
    onDismissWarning?.()
  }

  const payload = (): TripPreferences => ({
    ...form,
    num_travelers: solo ? 1 : form.num_travelers,
    cotravellers: solo ? [] : guests,
    cotraveller_usernames: solo ? [] : usernames,
    scope: canonicalScope(scope),
  })
  const submit = (send: (preferences: TripPreferences) => void | Promise<void>) => {
    saveTripDraft({ form, guests })
    void send(payload())
  }

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
    {feasibility && <div className="feasibility-banner"><Banner
      tone="warn"
      title="This trip probably won’t fit its budget"
      detail={[feasibility.reason, feasibility.suggestion_text].filter(Boolean).join(' ')}
      action={<div className="feasibility-actions">
        {hasSuggestion && <Button variant="secondary" onClick={applySuggestion} disabled={busy}>Apply suggestion</Button>}
        <Button variant="secondary" onClick={() => { if (onProceedAnyway) submit(onProceedAnyway) }} disabled={blocked}>Research anyway</Button>
      </div>}
    /></div>}
    <form className="trip-form corner-tick" aria-busy={busy} onSubmit={(event) => { event.preventDefault(); submit(onSubmit) }}>
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
          <legend><ListChecks aria-hidden /> What should we plan?</legend>
          <div className="tag-cloud">
            <button type="button" className={fullTrip ? 'is-active' : ''} aria-pressed={fullTrip} onClick={() => set('scope', [...ALL_SCOPES])}>Full trip</button>
            {SCOPE_OPTIONS.map(({ id, label }) => <button type="button" key={id} className={scope.includes(id) ? 'is-active' : ''} aria-pressed={scope.includes(id)} onClick={() => toggleScope(id)}>{label}</button>)}
          </div>
          <small>Tavi researches only what you pick here — you arrange the rest.</small>
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
          <p><strong>Tavi’s read:</strong> {form.vibes.slice(0, 3).join(', ') || 'open-ended'} · {form.group_type} · {duration} days{scopeRead}{!solo && partyLabel ? ` · ${partyLabel}` : ''}</p>
          {!scope.length && <p className="gate-note" role="status">Pick at least one thing to plan.</p>}
          {gate.length > 0 && <p className="gate-note" role="status">
            Waiting on taste profiles: {gate.join(' · ')}. Guests can be profiled right here with “Profile now” — linked members finish their own intake in their account.
          </p>}
          {busy && <p className="submit-status" role="status"><LoaderCircle className="spin" aria-hidden /> Checking this route’s feasibility — your details stay right here.</p>}
        </div>
        <Button type="submit" disabled={blocked}>{busy ? 'Opening a workspace…' : 'Research my trip'} <ArrowRight aria-hidden /></Button>
      </footer>
    </form>
  </main>
}
