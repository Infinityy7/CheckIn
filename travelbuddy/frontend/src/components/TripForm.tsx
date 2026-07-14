import { useMemo, useState } from 'react'
import { ArrowRight, CalendarDays, CircleDollarSign, MapPin, Navigation, Sparkles, Users } from 'lucide-react'
import type { TripPreferences } from '../types'
import { Mascot } from './Mascot'
import { Button } from './UI'

const vibes = ['adventure', 'culture', 'food', 'nightlife', 'relaxation', 'nature', 'shopping', 'history', 'romance', 'family-friendly']

function isoDate(offset: number) {
  const date = new Date(); date.setDate(date.getDate() + offset); return date.toISOString().slice(0, 10)
}

export function TripForm({ onSubmit, busy }: { onSubmit: (preferences: TripPreferences) => void; busy: boolean }) {
  const [form, setForm] = useState<TripPreferences>({
    destination: '', origin: '', start_date: isoDate(30), end_date: isoDate(36), budget_amount: 2200,
    currency: 'USD', vibes: ['culture', 'food', 'nature'], group_type: 'couple', num_travelers: 2, cotravellers: [],
  })
  const duration = useMemo(() => Math.max(1, Math.round((new Date(form.end_date).getTime() - new Date(form.start_date).getTime()) / 86400000) + 1), [form.start_date, form.end_date])
  const set = <K extends keyof TripPreferences>(key: K, value: TripPreferences[K]) => setForm((current) => ({ ...current, [key]: value }))
  const toggleVibe = (vibe: string) => set('vibes', form.vibes.includes(vibe) ? form.vibes.filter((item) => item !== vibe) : [...form.vibes, vibe])

  return <main className="planner page-stage">
    <section className="planner-intro">
      <div><span className="eyebrow"><Sparkles /> New expedition</span><h1>Where should we<br /><em>point the compass?</em></h1><p>Give Tavi the practical edges. Your character profile handles the rest.</p></div>
      <div className="planner-mascot"><span className="route-kicker">YOUR PROFILE IS ACTIVE</span><Mascot state="recommending" size="hero" /><div className="mascot-note">I’ll look for the places that match your pace—not just the places everyone posts.</div></div>
    </section>
    <form className="trip-form" onSubmit={(event) => { event.preventDefault(); onSubmit(form) }}>
      <div className="trip-form__primary">
        <label className="field field--large"><MapPin /><span>Destination</span><input required value={form.destination} onChange={(e) => set('destination', e.target.value)} placeholder="Kyoto, Japan" /></label>
        <span className="route-arrow">→</span>
        <label className="field field--large"><Navigation /><span>Starting from</span><input required value={form.origin} onChange={(e) => set('origin', e.target.value)} placeholder="Mumbai, India" /></label>
      </div>
      <div className="trip-form__grid">
        <fieldset className="form-section date-section"><legend><CalendarDays /> When are you going?</legend><div className="field-row"><label><span>Depart</span><input type="date" min={isoDate(0)} value={form.start_date} onChange={(e) => set('start_date', e.target.value)} /></label><label><span>Return</span><input type="date" min={form.start_date} value={form.end_date} onChange={(e) => set('end_date', e.target.value)} /></label></div><strong>{duration} days to wander</strong></fieldset>
        <fieldset className="form-section"><legend><CircleDollarSign /> What’s the trip budget?</legend><div className="budget-control"><select value={form.currency} onChange={(e) => set('currency', e.target.value)}>{['USD','EUR','GBP','INR','JPY','AUD','CAD'].map((c) => <option key={c}>{c}</option>)}</select><input type="number" min="100" value={form.budget_amount} onChange={(e) => set('budget_amount', Number(e.target.value))} /></div><small>Total, for everyone. We’ll flag smart splurges.</small></fieldset>
        <fieldset className="form-section"><legend><Users /> Who’s coming?</legend><div className="segmented">{(['solo','couple','friends','family'] as const).map((type) => <button type="button" className={form.group_type === type ? 'is-active' : ''} key={type} onClick={() => set('group_type', type)}>{type}</button>)}</div><label className="inline-input"><span>Travelers</span><input type="number" min="1" max="50" value={form.num_travelers} onChange={(e) => set('num_travelers', Number(e.target.value))} /></label></fieldset>
        <fieldset className="form-section form-section--wide"><legend><Sparkles /> What should this trip feel like?</legend><div className="tag-cloud">{vibes.map((vibe) => <button type="button" key={vibe} className={form.vibes.includes(vibe) ? 'is-active' : ''} onClick={() => toggleVibe(vibe)}>{vibe}</button>)}</div></fieldset>
      </div>
      <footer className="trip-form__footer"><p><strong>Tavi’s read:</strong> {form.vibes.slice(0,3).join(', ') || 'open-ended'} · {form.group_type} · {duration} days</p><Button type="submit" disabled={busy || !form.vibes.length}>{busy ? 'Opening a workspace…' : 'Research my trip'} <ArrowRight /></Button></footer>
    </form>
  </main>
}
