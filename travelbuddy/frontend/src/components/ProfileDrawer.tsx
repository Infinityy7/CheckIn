import { useState } from 'react'
import { RotateCcw, Save, Sparkles } from 'lucide-react'
import { api, userErrorMessage } from '../services/api'
import type { CharacterProfile, CharacterTraits } from '../types'
import { Mascot } from './Mascot'
import { Button, Drawer } from './UI'

const traitLabels: Array<[keyof CharacterTraits, string, string, string]> = [
  ['adventureLevel', 'Adventure', 'Familiar', 'Bold'], ['socialPreference', 'Social energy', 'Private', 'Social'],
  ['comfortPreference', 'Comfort', 'Rugged', 'Polished'], ['spontaneity', 'Planning style', 'Planned', 'Spontaneous'],
  ['localVsTourist', 'Place style', 'Iconic', 'Local'], ['foodAdventurousness', 'Food curiosity', 'Classic', 'Curious'],
  ['nightlifeInterest', 'After dark', 'Early nights', 'Nightlife'], ['natureVsUrban', 'Setting', 'Urban', 'Nature'],
]

export function ProfileDrawer({ open, profile, onClose, onUpdate, onRetake }: { open: boolean; profile: CharacterProfile | null; onClose: () => void; onUpdate: (profile: CharacterProfile) => void; onRetake: () => void | Promise<void> }) {
  const [draft, setDraft] = useState(profile)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  if (!draft) return <Drawer open={open} title="Your travel character" onClose={onClose}><p>No profile yet.</p></Drawer>
  const setTrait = (key: keyof CharacterTraits, value: string | number) => setDraft((current) => current ? ({ ...current, traits: { ...current.traits, [key]: value } }) : current)
  async function save() {
    if (!draft) return
    setSaving(true); setError('')
    try { const next = await api.updateProfile({ summary: draft.summary, traits: draft.traits }); onUpdate(next); onClose() }
    catch (reason) { setError(userErrorMessage(reason, 'Could not save the profile.')) }
    finally { setSaving(false) }
  }
  async function retake() {
    setSaving(true); setError('')
    try { await onRetake() }
    catch (reason) { setError(userErrorMessage(reason, 'Could not restart onboarding.')) }
    finally { setSaving(false) }
  }
  return <Drawer open={open} title="Your travel character" onClose={onClose}>
    <div className="drawer-profile-intro"><Mascot state="neutral" size="md" /><p>This is the lens the research crew uses to rank stays, meals, routes, and pacing. Change it anytime.</p></div>
    <label className="profile-summary"><span><Sparkles /> Character sketch</span><textarea value={draft.summary} onChange={(e) => setDraft({ ...draft, summary: e.target.value })} rows={8} /></label>
    <div className="profile-selects"><label>Pace<select value={draft.traits.pace} onChange={(e) => setTrait('pace', e.target.value)}><option value="slow">Slow</option><option value="balanced">Balanced</option><option value="fast">Fast</option></select></label><label>Budget style<select value={draft.traits.budgetStyle} onChange={(e) => setTrait('budgetStyle', e.target.value)}><option value="strict">Strict</option><option value="balanced">Balanced</option><option value="flexible">Flexible</option></select></label></div>
    <div className="trait-editor">{traitLabels.map(([key, label, low, high]) => <label key={key}><span><strong>{label}</strong><small>{low} ↔ {high}</small></span><input type="range" min="0" max="1" step="0.05" value={Number(draft.traits[key])} onChange={(e) => setTrait(key, Number(e.target.value))} /></label>)}</div>
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="profile-actions"><Button onClick={save} disabled={saving}><Save /> {saving ? 'Saving…' : 'Save profile'}</Button><Button variant="secondary" onClick={retake} disabled={saving}><RotateCcw /> Retake conversation</Button></div>
  </Drawer>
}
