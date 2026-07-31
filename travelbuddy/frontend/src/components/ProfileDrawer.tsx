import { useState } from 'react'
import { RotateCcw, Save, Sparkles } from 'lucide-react'
import { api, userErrorMessage } from '../services/api'
import type { CharacterProfile, CharacterTraits, DefaultParty, ProfileWeights, SpendCategory, TravelerArchetype, Vibe } from '../types'
import { Mascot } from './Mascot'
import { Button, Drawer } from './UI'

const vibes: Vibe[] = ['adventure', 'culture', 'food', 'nightlife', 'relaxation', 'nature', 'shopping', 'history', 'romance', 'wellness']
const spendCategories: SpendCategory[] = ['stay', 'experiences', 'food', 'shopping', 'transport']
const dealBreakers = ['early_flights', 'theme_parks', 'long_bus_rides', 'crowded_spots', 'heights', 'boats']
const dietaryRequirements = ['vegetarian', 'vegan', 'gluten_free', 'dairy_free', 'halal', 'kosher', 'nut_allergy', 'shellfish_allergy']

const traitLabels: Array<[keyof CharacterTraits, string, string, string]> = [
  ['adventureLevel', 'Adventure', 'Familiar', 'Bold'], ['socialPreference', 'Social energy', 'Private', 'Social'],
  ['comfortPreference', 'Comfort', 'Rugged', 'Polished'], ['spontaneity', 'Planning style', 'Planned', 'Spontaneous'],
  ['localVsTourist', 'Place style', 'Iconic', 'Local'], ['foodAdventurousness', 'Food curiosity', 'Classic', 'Curious'],
  ['nightlifeInterest', 'After dark', 'Early nights', 'Nightlife'], ['natureVsUrban', 'Setting', 'Urban', 'Nature'],
]

function label(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function cloneProfile(profile: CharacterProfile | null): CharacterProfile | null {
  if (!profile) return null
  return {
    ...profile,
    weights: profile.weights ? {
      ...profile.weights,
      vibeWeights: { ...profile.weights.vibeWeights },
      dealBreakers: [...profile.weights.dealBreakers],
      dietaryRequirements: [...profile.weights.dietaryRequirements],
    } : undefined,
    traits: profile.traits ? { ...profile.traits } : undefined,
  }
}

function StructuredWeightsEditor({ weights, onChange }: { weights: ProfileWeights; onChange: (weights: ProfileWeights) => void }) {
  const set = <K extends keyof ProfileWeights>(key: K, value: ProfileWeights[K]) => onChange({ ...weights, [key]: value })
  const toggle = (key: 'dealBreakers' | 'dietaryRequirements', value: string) => {
    const values = weights[key]
    set(key, (values.includes(value) ? values.filter((item) => item !== value) : [...values, value]) as ProfileWeights[typeof key])
  }
  return <>
    <section className="profile-section">
      <div className="profile-section__head"><div><strong>Recommendation weights</strong><small>These guide the ranker and are normalized when saved.</small></div></div>
      <div className="trait-editor vibe-editor">{vibes.map((vibe) => <label key={vibe}><span><strong>{label(vibe)}</strong><small>{Math.round((weights.vibeWeights[vibe] ?? 0) * 100)}%</small></span><input aria-label={`${label(vibe)} weight`} type="range" min="0" max="1" step="0.01" value={weights.vibeWeights[vibe] ?? 0} onChange={(event) => set('vibeWeights', { ...weights.vibeWeights, [vibe]: Number(event.target.value) })} /></label>)}</div>
    </section>

    <section className="profile-section">
      <div className="profile-section__head"><div><strong>Trip rhythm</strong><small>Controls pacing, start times, and restaurant breadth.</small></div></div>
      <div className="trait-editor">
        <label><span><strong>Planning style</strong><small>Planned ↔ Spontaneous</small></span><input aria-label="Planning style" type="range" min="0" max="1" step="0.05" value={weights.spontaneity} onChange={(event) => set('spontaneity', Number(event.target.value))} /></label>
        <label><span><strong>Food curiosity</strong><small>Familiar ↔ Anything</small></span><input aria-label="Food curiosity" type="range" min="0" max="1" step="0.05" value={weights.foodAdventurousness} onChange={(event) => set('foodAdventurousness', Number(event.target.value))} /></label>
      </div>
      <div className="profile-selects">
        <label>Day starts<select value={weights.chronotype} onChange={(event) => set('chronotype', event.target.value as ProfileWeights['chronotype'])}><option value="early">8 AM</option><option value="mid">9:30ish</option><option value="late">Whenever</option></select></label>
        <label>Travel style<select value={weights.archetype} onChange={(event) => set('archetype', event.target.value as TravelerArchetype)}>{(['foodie_explorer','culture_seeker','adrenaline_chaser','slow_traveler','luxury_unwinder','social_butterfly'] as TravelerArchetype[]).map((item) => <option key={item} value={item}>{label(item)}</option>)}</select></label>
        <label>Usually with<select value={weights.defaultParty} onChange={(event) => set('defaultParty', event.target.value as DefaultParty)}>{(['solo','partner','friends','family_young_kids','multi_generation'] as DefaultParty[]).map((item) => <option key={item} value={item}>{label(item)}</option>)}</select></label>
        <label>Splurge on<select value={weights.splurgeCategory} onChange={(event) => set('splurgeCategory', event.target.value as SpendCategory)}>{spendCategories.map((item) => <option key={item} value={item} disabled={item === weights.saveCategory}>{label(item)}</option>)}</select></label>
        <label>Save on<select value={weights.saveCategory} onChange={(event) => set('saveCategory', event.target.value as SpendCategory)}>{spendCategories.map((item) => <option key={item} value={item} disabled={item === weights.splurgeCategory}>{label(item)}</option>)}</select></label>
      </div>
    </section>

    <section className="profile-section">
      <div className="profile-section__head"><div><strong>Hard boundaries</strong><small>These filter options before anything is ranked.</small></div></div>
      <div className="constraint-group"><span>Absolute no-gos</span><div className="profile-chips">{[...new Set([...dealBreakers, ...weights.dealBreakers])].map((item) => <button type="button" aria-pressed={weights.dealBreakers.includes(item)} className={weights.dealBreakers.includes(item) ? 'is-active' : ''} key={item} onClick={() => toggle('dealBreakers', item)}>{label(item)}</button>)}</div></div>
      <div className="constraint-group"><span>Dietary needs</span><div className="profile-chips">{[...new Set([...dietaryRequirements, ...weights.dietaryRequirements])].map((item) => <button type="button" aria-pressed={weights.dietaryRequirements.includes(item)} className={weights.dietaryRequirements.includes(item) ? 'is-active' : ''} key={item} onClick={() => toggle('dietaryRequirements', item)}>{label(item)}</button>)}</div></div>
    </section>
  </>
}

export function ProfileDrawer({ open, profile, onClose, onUpdate, onRetake }: { open: boolean; profile: CharacterProfile | null; onClose: () => void; onUpdate: (profile: CharacterProfile) => void; onRetake: () => void | Promise<void> }) {
  const [draft, setDraft] = useState(() => cloneProfile(profile))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  if (!draft) return <Drawer open={open} title="Your travel character" onClose={onClose}><p>No profile yet.</p></Drawer>

  const setTrait = (key: keyof CharacterTraits, value: string | number) => setDraft((current) => current?.traits ? ({ ...current, traits: { ...current.traits, [key]: value } }) : current)
  async function save() {
    if (!draft) return
    setSaving(true); setError('')
    try {
      const next = await api.updateProfile({ summary: draft.summary, weights: draft.weights, traits: draft.traits, expectedVersion: draft.version })
      onUpdate(next); onClose()
    } catch (reason) { setError(userErrorMessage(reason, 'Could not save the profile.')) }
    finally { setSaving(false) }
  }
  async function retake() {
    setSaving(true); setError('')
    try { await onRetake() }
    catch (reason) { setError(userErrorMessage(reason, 'Could not restart onboarding.')) }
    finally { setSaving(false) }
  }

  return <Drawer open={open} title="Your travel character" onClose={onClose}>
    <div className="drawer-profile-intro"><Mascot state="neutral" size="md" /><p>Your character sketch guides discovery. The structured controls below shape ranking and hard filters.</p></div>
    <label className="profile-summary"><span><Sparkles /> Character sketch</span><textarea value={draft.summary} onChange={(event) => setDraft({ ...draft, summary: event.target.value })} rows={8} /></label>
    {draft.weights ? <StructuredWeightsEditor weights={draft.weights} onChange={(weights) => setDraft({ ...draft, weights })} /> : draft.traits ? <>
      <div className="profile-selects"><label>Pace<select value={draft.traits.pace} onChange={(event) => setTrait('pace', event.target.value)}><option value="slow">Slow</option><option value="balanced">Balanced</option><option value="fast">Fast</option></select></label><label>Budget style<select value={draft.traits.budgetStyle} onChange={(event) => setTrait('budgetStyle', event.target.value)}><option value="strict">Strict</option><option value="balanced">Balanced</option><option value="flexible">Flexible</option></select></label></div>
      <div className="trait-editor">{traitLabels.map(([key, traitLabel, low, high]) => <label key={key}><span><strong>{traitLabel}</strong><small>{low} ↔ {high}</small></span><input type="range" min="0" max="1" step="0.05" value={Number(draft.traits?.[key])} onChange={(event) => setTrait(key, Number(event.target.value))} /></label>)}</div>
    </> : null}
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="profile-actions"><Button onClick={save} disabled={saving}><Save /> {saving ? 'Saving…' : 'Save profile'}</Button><Button variant="secondary" onClick={retake} disabled={saving}><RotateCcw /> Retake questionnaire</Button></div>
  </Drawer>
}
