import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertCircle, ChevronRight, CircleCheck, Clock, Plus, RefreshCw, Send, X } from 'lucide-react'
import type { UserLookup } from '../types'
import { api, ApiError, userErrorMessage } from '../services/api'
import { Button, Chip } from './UI'
import { CompanionIntake } from './CompanionIntake'

export const MAX_COMPANIONS = 8

/** Mirror of the backend cotraveller slug rule: lowercase, non-alphanumeric runs → '-', trimmed. */
export function slugifyName(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

/** null = lookup failed (unverified); undefined/missing = still verifying. */
type LookupMap = Record<string, UserLookup | null>

/** Why a linked member cannot join research yet; null once they can. Mirrors the backend 403/400 order. */
function linkedBlocker(username: string, info: UserLookup | null | undefined): string | null {
  if (info === undefined) return `@${username} (still verifying)`
  if (info === null) return `@${username} (couldn’t be verified)`
  if (info.link_status === 'pending') return `@${username} (invitation pending)`
  if (info.link_status === 'declined') return `@${username} (declined your invitation)`
  if (info.link_status !== 'accepted') return `@${username} (needs an invitation)`
  return info.intake_complete ? null : `@${username} (hasn’t finished their taste profile)`
}

/**
 * Trip travel-party manager. Two kinds of co-traveller:
 * - linked members, added by CheckIn username (primary) — their own account's
 *   taste profile rides along only after they accept an invitation; only they
 *   can finish their profile, so a pending invitation or an unfinished profile
 *   blocks research (mirrors the backend 403/400);
 * - guests without an account (secondary) — the organizer answers the
 *   4-question taste intake for them right here.
 * Removing anyone only takes them off this trip. Every blocker is lifted up
 * so TripForm can hold research until the whole party has a usable profile.
 */
export function CompanionManager({ guests, onGuestsChange, usernames, onUsernamesChange, onBlockedChange }: {
  guests: string[]
  onGuestsChange: (guests: string[]) => void
  usernames: string[]
  onUsernamesChange: (usernames: string[]) => void
  onBlockedChange: (blockers: string[]) => void
}) {
  const [profiled, setProfiled] = useState<string[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [guestDraft, setGuestDraft] = useState('')
  const [intakeFor, setIntakeFor] = useState<string | null>(null)
  const [guestOpen, setGuestOpen] = useState(false)

  const [usernameDraft, setUsernameDraft] = useState('')
  const [lookupError, setLookupError] = useState<string | null>(null)
  const [looking, setLooking] = useState(false)
  const [lookups, setLookups] = useState<LookupMap>({})
  const inFlightLookups = useRef(new Set<string>())
  const [inviteBusy, setInviteBusy] = useState<string | null>(null)
  const [inviteError, setInviteError] = useState<string | null>(null)

  const [reloadKey, setReloadKey] = useState(0)
  const load = useCallback(() => setReloadKey((key) => key + 1), [])
  useEffect(() => {
    let cancelled = false
    api.profileOverview()
      .then((overview) => { if (!cancelled) { setProfiled(overview.cotravellers.map(slugifyName)); setLoadError(null) } })
      .catch((reason: unknown) => { if (!cancelled) setLoadError(userErrorMessage(reason, 'Could not load saved companions.')) })
    return () => { cancelled = true }
  }, [reloadKey])

  // Usernames can outlive this component (solo round-trip keeps them in the
  // form), so verify any we have no lookup for instead of trusting them.
  useEffect(() => {
    for (const username of usernames) {
      const key = username.toLowerCase()
      if (lookups[key] !== undefined || inFlightLookups.current.has(key)) continue
      inFlightLookups.current.add(key)
      api.lookupUser(username)
        .then((found) => setLookups((previous) => ({ ...previous, [key]: found })))
        .catch(() => setLookups((previous) => ({ ...previous, [key]: null })))
        .finally(() => inFlightLookups.current.delete(key))
    }
  }, [usernames, lookups])

  const profiledSet = useMemo(() => new Set(profiled ?? []), [profiled])
  const total = guests.length + usernames.length
  const full = total >= MAX_COMPANIONS

  const blockers = useMemo(() => [
    ...usernames.map((username) => linkedBlocker(username, lookups[username.toLowerCase()])),
    ...guests.map((name) => profiledSet.has(slugifyName(name)) ? null : `${name} (guest — needs their taste intake)`),
  ].filter((entry): entry is string => entry !== null), [usernames, lookups, guests, profiledSet])
  useEffect(() => { onBlockedChange(blockers) }, [blockers, onBlockedChange])

  const suggestions = useMemo(
    () => (profiled ?? []).filter((slug) => slug && !guests.some((name) => slugifyName(name) === slug)),
    [profiled, guests],
  )

  const addUsername = async () => {
    const username = usernameDraft.trim().replace(/^@+/, '')
    if (!username || full || looking) return
    if (usernames.some((existing) => existing.toLowerCase() === username.toLowerCase())) {
      setLookupError(`@${username} is already in the party.`)
      return
    }
    setLooking(true)
    setLookupError(null)
    try {
      const found = await api.lookupUser(username)
      setLookups((previous) => ({ ...previous, [found.username.toLowerCase()]: found }))
      onUsernamesChange([...usernames, found.username])
      setUsernameDraft('')
    } catch (reason) {
      setLookupError(reason instanceof ApiError && reason.status === 404
        ? `No CheckIn user named @${username}`
        : userErrorMessage(reason, 'Could not verify that username. It is safe to try again.'))
    } finally {
      setLooking(false)
    }
  }

  const invite = async (username: string) => {
    const key = username.toLowerCase()
    setInviteBusy(key)
    setInviteError(null)
    try {
      const link = await api.inviteCompanion(username)
      setLookups((previous) => {
        const current = previous[key]
        return current ? { ...previous, [key]: { ...current, link_status: link.status } } : previous
      })
    } catch (reason) {
      setInviteError(userErrorMessage(reason, `Could not invite @${username}. It is safe to try again.`))
    } finally {
      setInviteBusy(null)
    }
  }

  const recheck = async (username: string) => {
    const key = username.toLowerCase()
    if (inFlightLookups.current.has(key)) return
    inFlightLookups.current.add(key)
    try {
      const found = await api.lookupUser(username)
      setLookups((previous) => ({ ...previous, [key]: found }))
    } catch {
      setLookups((previous) => ({ ...previous, [key]: null }))
    } finally {
      inFlightLookups.current.delete(key)
    }
  }

  const addGuest = (name: string) => {
    const trimmed = name.trim()
    if (!trimmed || full) return
    if (guests.some((existing) => slugifyName(existing) === slugifyName(trimmed))) { setGuestDraft(''); return }
    onGuestsChange([...guests, trimmed])
    setGuestDraft('')
  }

  const memberChip = (username: string, info: UserLookup | null | undefined) => {
    if (info === undefined) return <Chip tone="muted">{`@${username} · verifying…`}</Chip>
    if (info === null) return <Chip tone="warn" icon={<AlertCircle aria-hidden />} title="CheckIn couldn’t confirm this account — remove and re-add them to retry.">{`@${username} · couldn’t verify this account`}</Chip>
    if (info.link_status === 'accepted') {
      return info.intake_complete
        ? <Chip tone="ok" icon={<CircleCheck aria-hidden />}>{`@${username}${info.name ? ` · ${info.name}` : ''}`}</Chip>
        : <Chip tone="warn" icon={<AlertCircle aria-hidden />} title="Only they can finish their own taste profile — ask them to open CheckIn and complete the intake.">{`@${username} · hasn’t finished their taste profile`}</Chip>
    }
    if (info.link_status === 'pending') return <Chip tone="muted" icon={<Clock aria-hidden />} title="They can accept from the Travel companions section of their profile.">{`@${username} · invitation pending`}</Chip>
    if (info.link_status === 'declined') return <Chip tone="warn" icon={<AlertCircle aria-hidden />}>{`@${username} · declined your invitation`}</Chip>
    return <Chip tone="muted">{`@${username} · not invited yet`}</Chip>
  }

  return <div className="party">
    <p className="party-hint">Add co-travellers by their CheckIn username and send an invitation — once they accept, their own taste profile travels with them, so rankings balance the whole group.</p>
    {loadError
      ? <p className="party-status party-status--error" role="alert">
        {loadError} <Button type="button" variant="quiet" onClick={load}>Retry</Button>
      </p>
      : profiled === null && <p className="party-status" role="status">Checking saved companions…</p>}
    <div className="party-add">
      <label htmlFor="companion-username">Add by username</label>
      <div className="party-add__row">
        <span className="username-field">
          <span className="username-field__at" aria-hidden>@</span>
          <input
            id="companion-username"
            value={usernameDraft}
            maxLength={80}
            autoComplete="off"
            placeholder="their-username"
            disabled={full}
            onChange={(event) => setUsernameDraft(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void addUsername() } }}
          />
        </span>
        <Button type="button" variant="secondary" onClick={() => void addUsername()} disabled={!usernameDraft.trim() || full || looking}>
          <Plus aria-hidden /> {looking ? 'Checking…' : 'Add'}
        </Button>
      </div>
      {lookupError && <p className="party-status party-status--error" role="alert">{lookupError}</p>}
    </div>
    {usernames.length > 0 && <ul className="party-list" aria-label="Linked members">
      {usernames.map((username) => {
        const key = username.toLowerCase()
        const info = lookups[key]
        const canInvite = info != null && info.link_status !== 'accepted' && info.link_status !== 'pending'
        return <li key={key} className="party-row">
          {memberChip(username, info)}
          <span className="party-row__actions">
            {canInvite && <Button type="button" variant="secondary" disabled={inviteBusy === key} onClick={() => void invite(info.username)}>
              <Send aria-hidden /> {inviteBusy === key ? 'Inviting…' : info.link_status === 'none' ? 'Invite' : 'Invite again'}
            </Button>}
            {info?.link_status === 'pending' && <Button type="button" variant="quiet" onClick={() => void recheck(username)} title="Refresh once they have accepted">
              <RefreshCw aria-hidden /> Check again
            </Button>}
            <button
              type="button"
              className="icon-button party-remove"
              aria-label={`Remove @${username} from this trip`}
              title="Removes them from this trip only — their account and any invitation are untouched"
              onClick={() => onUsernamesChange(usernames.filter((other) => other !== username))}
            ><X aria-hidden /></button>
          </span>
        </li>
      })}
    </ul>}
    {inviteError && <p className="party-status party-status--error" role="alert">{inviteError}</p>}
    {guests.length > 0 && <ul className="party-list" aria-label="Guests">
      {guests.map((name) => {
        const isProfiled = profiledSet.has(slugifyName(name))
        return <li key={slugifyName(name)} className="party-row">
          <span className="party-name">{name}</span>
          {isProfiled
            ? <Chip tone="ok" icon={<CircleCheck aria-hidden />}>Guest · profiled</Chip>
            : <Chip tone="warn" icon={<AlertCircle aria-hidden />}>Guest · needs taste intake</Chip>}
          <span className="party-row__actions">
            {!isProfiled && <Button type="button" variant="secondary" onClick={() => setIntakeFor(name)}>Profile now</Button>}
            <button
              type="button"
              className="icon-button party-remove"
              aria-label={`Remove ${name} from this trip`}
              title="Removes them from this trip only — their saved taste profile stays"
              onClick={() => onGuestsChange(guests.filter((other) => other !== name))}
            ><X aria-hidden /></button>
          </span>
        </li>
      })}
    </ul>}
    <div className="party-guest">
      <button type="button" className="party-guest__toggle" aria-expanded={guestOpen} onClick={() => setGuestOpen((open) => !open)}>
        <ChevronRight aria-hidden /> Guest companion (no account)
      </button>
      <p className="party-guest__hint">For companions without a CheckIn account — answer 4 quick taste questions for them.</p>
      {guestOpen && <div className="party-guest__body">
        <div className="party-add">
          <label htmlFor="companion-name">Guest name</label>
          <div className="party-add__row">
            <input
              id="companion-name"
              value={guestDraft}
              maxLength={80}
              autoComplete="off"
              placeholder="e.g. Priya"
              disabled={full}
              onChange={(event) => setGuestDraft(event.target.value)}
              onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addGuest(guestDraft) } }}
            />
            <Button type="button" variant="secondary" onClick={() => addGuest(guestDraft)} disabled={!guestDraft.trim() || full}>
              <Plus aria-hidden /> Add guest
            </Button>
          </div>
        </div>
        {suggestions.length > 0 && !full && <div className="party-saved">
          <span className="party-saved__label">Profiled before:</span>
          {suggestions.map((slug) => <button type="button" key={slug} className="party-suggestion" onClick={() => addGuest(slug)}>
            <Plus aria-hidden /> {slug}
          </button>)}
        </div>}
      </div>}
    </div>
    {full && <small className="party-cap" role="status">Trips carry up to {MAX_COMPANIONS} co-travellers — linked members and guests combined.</small>}
    {intakeFor !== null && createPortal(
      <CompanionIntake
        name={intakeFor}
        onProfiled={(profiledName) => setProfiled((previous) => [...(previous ?? []), slugifyName(profiledName)])}
        onClose={() => setIntakeFor(null)}
      />,
      document.body,
    )}
  </div>
}
