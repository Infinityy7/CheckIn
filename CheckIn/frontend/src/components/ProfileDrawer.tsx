import '../styles/identity.css'
import { useEffect, useState } from 'react'
import { Check, CircleCheck, RotateCcw, Save, Sparkles, Users, X } from 'lucide-react'
import { api, userErrorMessage } from '../services/api'
import type { CharacterProfile, CharacterTraits, CompanionLink, CompanionLinks, DefaultParty, ProfileWeights, SpendCategory, TravelerArchetype, Vibe } from '../types'
import { Mascot } from './Mascot'
import { Button, Chip, Drawer } from './UI'

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
    <section className="idn-section">
      <div className="idn-section__head"><div><strong>Recommendation weights</strong><small>These guide the ranker and are normalized when saved.</small></div></div>
      <div className="idn-sliders idn-sliders--vibes">{vibes.map((vibe) => <label key={vibe}><span><strong>{label(vibe)}</strong><small>{Math.round((weights.vibeWeights[vibe] ?? 0) * 100)}%</small></span><input aria-label={`${label(vibe)} weight`} type="range" min="0" max="1" step="0.01" value={weights.vibeWeights[vibe] ?? 0} onChange={(event) => set('vibeWeights', { ...weights.vibeWeights, [vibe]: Number(event.target.value) })} /></label>)}</div>
    </section>

    <section className="idn-section">
      <div className="idn-section__head"><div><strong>Trip rhythm</strong><small>Controls pacing, start times, and restaurant breadth.</small></div></div>
      <div className="idn-sliders">
        <label><span><strong>Planning style</strong><small>Planned ↔ Spontaneous</small></span><input aria-label="Planning style" type="range" min="0" max="1" step="0.05" value={weights.spontaneity} onChange={(event) => set('spontaneity', Number(event.target.value))} /></label>
        <label><span><strong>Food curiosity</strong><small>Familiar ↔ Anything</small></span><input aria-label="Food curiosity" type="range" min="0" max="1" step="0.05" value={weights.foodAdventurousness} onChange={(event) => set('foodAdventurousness', Number(event.target.value))} /></label>
      </div>
      <div className="idn-selects">
        <label>Day starts<select value={weights.chronotype} onChange={(event) => set('chronotype', event.target.value as ProfileWeights['chronotype'])}><option value="early">8 AM</option><option value="mid">9:30ish</option><option value="late">Whenever</option></select></label>
        <label>Travel style<select value={weights.archetype} onChange={(event) => set('archetype', event.target.value as TravelerArchetype)}>{(['foodie_explorer','culture_seeker','adrenaline_chaser','slow_traveler','luxury_unwinder','social_butterfly'] as TravelerArchetype[]).map((item) => <option key={item} value={item}>{label(item)}</option>)}</select></label>
        <label>Usually with<select value={weights.defaultParty} onChange={(event) => set('defaultParty', event.target.value as DefaultParty)}>{(['solo','partner','friends','family_young_kids','multi_generation'] as DefaultParty[]).map((item) => <option key={item} value={item}>{label(item)}</option>)}</select></label>
        <label>Splurge on<select value={weights.splurgeCategory} onChange={(event) => set('splurgeCategory', event.target.value as SpendCategory)}>{spendCategories.map((item) => <option key={item} value={item} disabled={item === weights.saveCategory}>{label(item)}</option>)}</select></label>
        <label>Save on<select value={weights.saveCategory} onChange={(event) => set('saveCategory', event.target.value as SpendCategory)}>{spendCategories.map((item) => <option key={item} value={item} disabled={item === weights.splurgeCategory}>{label(item)}</option>)}</select></label>
      </div>
    </section>

    <section className="idn-section">
      <div className="idn-section__head"><div><strong>Hard boundaries</strong><small>These filter options before anything is ranked.</small></div></div>
      <div className="idn-constraints"><span>Absolute no-gos</span><div className="idn-toggle-chips">{[...new Set([...dealBreakers, ...weights.dealBreakers])].map((item) => <button type="button" aria-pressed={weights.dealBreakers.includes(item)} className={weights.dealBreakers.includes(item) ? 'is-active' : ''} key={item} onClick={() => toggle('dealBreakers', item)}>{label(item)}</button>)}</div></div>
      <div className="idn-constraints"><span>Dietary needs</span><div className="idn-toggle-chips">{[...new Set([...dietaryRequirements, ...weights.dietaryRequirements])].map((item) => <button type="button" aria-pressed={weights.dietaryRequirements.includes(item)} className={weights.dietaryRequirements.includes(item) ? 'is-active' : ''} key={item} onClick={() => toggle('dietaryRequirements', item)}>{label(item)}</button>)}</div></div>
    </section>
  </>
}

export function ProfileDrawer({ open, profile, onClose, onUpdate, onRetake }: { open: boolean; profile: CharacterProfile | null; onClose: () => void; onUpdate: (profile: CharacterProfile) => void; onRetake: () => void | Promise<void> }) {
  const [draft, setDraft] = useState(() => cloneProfile(profile))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [companions, setCompanions] = useState<string[] | null>(null)
  const [companionsError, setCompanionsError] = useState('')
  const [links, setLinks] = useState<CompanionLinks | null>(null)
  const [linksError, setLinksError] = useState('')
  const [linkBusy, setLinkBusy] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    api.profileOverview()
      .then((overview) => { if (!cancelled) { setCompanions(overview.cotravellers); setCompanionsError('') } })
      .catch((reason: unknown) => { if (!cancelled) setCompanionsError(userErrorMessage(reason, 'Could not load your travel companions.')) })
    api.companionLinks()
      .then((rows) => { if (!cancelled) { setLinks(rows); setLinksError('') } })
      .catch((reason: unknown) => { if (!cancelled) setLinksError(userErrorMessage(reason, 'Could not load your travel invitations.')) })
    return () => { cancelled = true }
  }, [open])

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

  const replaceLink = (updated: CompanionLink) => setLinks((current) => current ? {
    incoming: current.incoming.map((row) => row.link_id === updated.link_id ? updated : row),
    outgoing: current.outgoing.map((row) => row.link_id === updated.link_id ? updated : row),
  } : current)
  async function respondToInvitation(link: CompanionLink, action: 'accept' | 'decline') {
    setLinkBusy(link.link_id); setLinksError('')
    try { replaceLink(await api.respondCompanionLink(link.link_id, action)) }
    catch (reason) { setLinksError(userErrorMessage(reason, 'Could not update that invitation.')) }
    finally { setLinkBusy(null) }
  }
  async function stopSharing(link: CompanionLink) {
    setLinkBusy(link.link_id); setLinksError('')
    try { replaceLink(await api.removeCompanionLink(link.link_id)) }
    catch (reason) { setLinksError(userErrorMessage(reason, 'Could not remove that companion.')) }
    finally { setLinkBusy(null) }
  }

  const invitations = links?.incoming.filter((row) => row.status === 'pending') ?? []
  const sharingWith = links?.incoming.filter((row) => row.status === 'accepted') ?? []
  const sharedWithMe = links?.outgoing.filter((row) => row.status === 'accepted') ?? []
  const awaiting = links?.outgoing.filter((row) => row.status === 'pending') ?? []
  const linkedCount = invitations.length + sharingWith.length + sharedWithMe.length + awaiting.length

  return <Drawer open={open} title="Your travel character" onClose={onClose}>
    <div className="idn-drawer-intro"><Mascot state="neutral" size="md" /><p>Your character sketch guides discovery. The structured controls below shape ranking and hard filters.</p></div>
    <label className="idn-summary"><span><Sparkles aria-hidden /> Character sketch</span><textarea value={draft.summary} onChange={(event) => setDraft({ ...draft, summary: event.target.value })} rows={8} /></label>
    {draft.weights ? <StructuredWeightsEditor weights={draft.weights} onChange={(weights) => setDraft({ ...draft, weights })} /> : draft.traits ? <>
      <div className="idn-selects"><label>Pace<select value={draft.traits.pace} onChange={(event) => setTrait('pace', event.target.value)}><option value="slow">Slow</option><option value="balanced">Balanced</option><option value="fast">Fast</option></select></label><label>Budget style<select value={draft.traits.budgetStyle} onChange={(event) => setTrait('budgetStyle', event.target.value)}><option value="strict">Strict</option><option value="balanced">Balanced</option><option value="flexible">Flexible</option></select></label></div>
      <div className="idn-sliders idn-section">{traitLabels.map(([key, traitLabel, low, high]) => <label key={key}><span><strong>{traitLabel}</strong><small>{low} ↔ {high}</small></span><input type="range" min="0" max="1" step="0.05" aria-label={traitLabel} value={Number(draft.traits?.[key])} onChange={(event) => setTrait(key, Number(event.target.value))} /></label>)}</div>
    </> : null}

    <section className="idn-section" aria-label="Travel companions">
      <div className="idn-section__head"><div><strong><Users aria-hidden size={15} /> Travel companions</strong><small>Invitations decide whose taste profile a trip may use. Accepting shares your compiled taste with that organizer’s research — never your sketch text.</small></div></div>
      {linksError && <p className="form-error" role="alert">{linksError}</p>}
      {links === null && !linksError
        ? <p className="idn-companions-empty" role="status">Loading invitations…</p>
        : links && <>
          {invitations.length > 0 && <ul className="idn-invites" aria-label="Invitations waiting for you">
            {invitations.map((link) => <li key={link.link_id} className="idn-invite">
              <span className="idn-invite__who"><strong>{`@${link.username}`}</strong><small>{link.name ? `${link.name} · ` : ''}wants to plan trips with your taste profile</small></span>
              <span className="idn-invite__actions">
                <Button type="button" disabled={linkBusy === link.link_id} onClick={() => void respondToInvitation(link, 'accept')}><Check aria-hidden /> Accept</Button>
                <Button type="button" variant="quiet" disabled={linkBusy === link.link_id} onClick={() => void respondToInvitation(link, 'decline')}>Decline</Button>
              </span>
            </li>)}
          </ul>}
          {sharingWith.length > 0 && <div className="idn-companions__group"><span>Can plan with your profile</span><div className="idn-companions">{sharingWith.map((link) => <span key={link.link_id} className="idn-linked">
            <Chip tone="ok" icon={<CircleCheck aria-hidden />}>{`@${link.username}`}</Chip>
            <button type="button" className="icon-button idn-linked__remove" aria-label={`Stop sharing your profile with @${link.username}`} disabled={linkBusy === link.link_id} onClick={() => void stopSharing(link)}><X aria-hidden /></button>
          </span>)}</div></div>}
          {sharedWithMe.length > 0 && <div className="idn-companions__group"><span>Share their profile with you</span><div className="idn-companions">{sharedWithMe.map((link) => <Chip key={link.link_id} tone="ok" icon={<CircleCheck aria-hidden />}>{`@${link.username}`}</Chip>)}</div></div>}
          {awaiting.length > 0 && <div className="idn-companions__group"><span>Waiting on their answer</span><div className="idn-companions">{awaiting.map((link) => <Chip key={link.link_id} tone="muted">{`@${link.username} · invitation pending`}</Chip>)}</div></div>}
          {linkedCount === 0 && <p className="idn-companions-empty">No linked companions yet — invite them from the trip planner.</p>}
        </>}
      <div className="idn-companions__group">
        <span>Guests you’ve profiled</span>
        {companionsError
          ? <p className="form-error" role="alert">{companionsError}</p>
          : companions === null
            ? <p className="idn-companions-empty" role="status">Loading companions…</p>
            : companions.length === 0
              ? <p className="idn-companions-empty">No companions saved yet.</p>
              : <div className="idn-companions">{companions.map((name) => <Chip key={name} tone="ok">{label(name)}</Chip>)}</div>}
      </div>
      <p className="idn-note">Add or profile companions from the trip planner.</p>
    </section>

    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="idn-drawer-actions"><Button onClick={save} disabled={saving}><Save aria-hidden /> {saving ? 'Saving…' : 'Save profile'}</Button><Button variant="secondary" onClick={retake} disabled={saving}><RotateCcw aria-hidden /> Retake questionnaire</Button></div>
  </Drawer>
}
