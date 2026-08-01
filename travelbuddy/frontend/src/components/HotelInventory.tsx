import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, BedSingle, Check, ChevronDown, Clock3, RefreshCw, ShieldCheck, UsersRound, WifiOff } from 'lucide-react'
import { api, userErrorMessage } from '../services/api'
import type { HotelAvailability, HotelRatePlan, Money, Recommendation, TripCart } from '../types'
import { Button } from './UI'

function money(value?: Money) {
  if (!value || !Number.isFinite(value.amount)) return 'Price unavailable'
  return new Intl.NumberFormat(undefined, {
    style: 'currency', currency: value.currency, maximumFractionDigits: value.amount % 1 ? 2 : 0,
  }).format(value.amount)
}

function remaining(expiresAt: string | undefined, now: number) {
  if (!expiresAt) return null
  const difference = new Date(expiresAt).getTime() - now
  if (!Number.isFinite(difference) || difference <= 0) return 'Expired — refresh required'
  const minutes = Math.floor(difference / 60_000)
  const seconds = Math.floor((difference % 60_000) / 1_000)
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m remaining`
  return `${minutes}m ${String(seconds).padStart(2, '0')}s remaining`
}

export function ExpiryCountdown({ expiresAt, kind = 'quote' }: { expiresAt?: string; kind?: 'quote' | 'hold' | 'saved' }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!expiresAt) return
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [expiresAt])
  const label = remaining(expiresAt, now)
  if (!label) return <span className="expiry-note expiry-note--saved"><Clock3 /> Saved choice · no inventory hold</span>
  const expired = label.startsWith('Expired')
  const expiry = new Date(expiresAt!)
  const exact = Number.isFinite(expiry.getTime()) ? expiry.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : expiresAt
  return <span className={`expiry-note ${expired ? 'expiry-note--expired' : ''}`} role={expired ? 'alert' : 'status'}>
    <Clock3 /> {kind === 'hold' ? 'Supplier hold' : kind === 'saved' ? 'Saved cart · not reserved' : 'Price quote'} · {label} · <time dateTime={expiresAt}>{exact}</time>{kind === 'saved' ? ' · does not reserve inventory' : ''}
  </span>
}

function SourceBadge({ availability }: { availability: Pick<HotelAvailability, 'source' | 'sourceMode' | 'isLive'> }) {
  const label = availability.isLive && availability.sourceMode === 'live' ? 'Live supplier rates' : availability.sourceMode === 'test' ? 'Test rates' : availability.sourceMode === 'demo' ? 'Demo rates' : 'Provider unavailable'
  return <span className={`inventory-source inventory-source--${availability.sourceMode}`}>
    <i aria-hidden="true" /> {label} · {availability.source}
  </span>
}

function availabilityCopy(rate: HotelRatePlan) {
  if (rate.availabilityStatus === 'price_changed') return 'Price changed — refresh before adding'
  if (rate.availabilityStatus === 'expired') return 'Quote expired — refresh required'
  if (rate.availabilityStatus === 'unavailable') return 'No longer available'
  if (rate.roomsRemaining && rate.roomsRemaining <= 3) return `${rate.roomsRemaining} left at this price`
  return 'Available for your dates'
}

function RatePlan({ tripId, recommendationId, rate, onCartChange, onAdded }: {
  tripId: string
  recommendationId: string
  rate: HotelRatePlan
  onCartChange: (cart: TripCart) => void
  onAdded?: () => void
}) {
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState(false)
  const [error, setError] = useState('')
  const unavailable = ['unavailable', 'expired', 'price_changed'].includes(rate.availabilityStatus)

  async function add() {
    setAdding(true); setError('')
    try {
      const cart = await api.addCartItem(tripId, recommendationId, rate.id, 'hotel')
      onCartChange(cart); setAdded(true); onAdded?.()
    } catch (reason) {
      setError(userErrorMessage(reason, 'That room could not be added. Refresh its price and try again.'))
    } finally { setAdding(false) }
  }

  return <article className={`rate-plan rate-plan--${rate.availabilityStatus}`} aria-label={`${rate.label} rate`}>
    <div className="rate-plan__copy">
      <div className="rate-plan__title"><strong>{rate.label}</strong>{rate.refundable ? <span className="refundable"><ShieldCheck /> Refundable</span> : <span>Non-refundable</span>}</div>
      <p>{rate.cancellationSummary || (rate.refundable ? 'Review the cancellation window before payment.' : 'This price cannot be refunded after booking.')}</p>
      <span className={rate.availabilityStatus === 'available' || rate.availabilityStatus === 'limited' ? 'availability-ok' : 'availability-bad'}>{availabilityCopy(rate)}</span>
      <ExpiryCountdown expiresAt={rate.holdExpiresAt ?? rate.quoteExpiresAt} kind={rate.holdExpiresAt ? 'hold' : 'quote'} />
    </div>
    <div className="rate-plan__price">
      <strong>{money(rate.total)}</strong><span>trip total</span>
      <small>{money(rate.nightly)} / night</small>
      <small>{money(rate.taxesAndFees)} taxes & fees</small>
    </div>
    <div className="rate-plan__action">
      <Button variant={added ? 'secondary' : 'primary'} onClick={add} disabled={unavailable || adding || added}>
        {added ? <><Check /> Added</> : adding ? 'Adding…' : rate.holdExpiresAt ? 'Add held room' : 'Save quoted rate'}
      </Button>
      <small>{rate.holdExpiresAt ? 'Supplier-confirmed hold' : 'Availability is rechecked before booking'}</small>
    </div>
    {error && <p className="rate-plan__error" role="alert"><AlertTriangle /> {error}</p>}
  </article>
}

export function HotelInventory({ tripId, recommendation, onCartChange, onAdded }: {
  tripId: string
  recommendation: Recommendation
  onCartChange: (cart: TripCart) => void
  onAdded?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [availability, setAvailability] = useState<HotelAvailability | null>(null)
  const [error, setError] = useState('')
  const panelId = `hotel-rates-${recommendation.id.replace(/[^a-zA-Z0-9_-]/g, '-')}`

  async function load() {
    setLoading(true); setError('')
    try { setAvailability(await api.hotelRates(tripId, recommendation.id)) }
    catch (reason) { setError(userErrorMessage(reason, 'Live rooms are not available for this hotel right now.')) }
    finally { setLoading(false) }
  }

  function toggle() {
    const next = !open
    setOpen(next)
    if (next && !availability && !loading) void load()
  }

  const rateCount = useMemo(() => availability?.rooms.reduce((count, room) => count + room.ratePlans.length, 0) ?? 0, [availability])

  return <section className="hotel-inventory">
    <button className="inventory-toggle" type="button" aria-expanded={open} aria-controls={panelId} onClick={toggle}>
      <span><BedSingle /><b>Rooms & booking prices</b><small>Check availability for your exact dates</small></span>
      <ChevronDown aria-hidden="true" />
    </button>
    {open && <div className="inventory-panel" id={panelId}>
      {loading && <div className="inventory-loading" role="status"><span className="inventory-skeleton" /><span><b>Checking room inventory…</b><small>Confirming prices, policies, and remaining rooms.</small></span></div>}
      {!loading && error && <div className="inventory-error" role="alert"><WifiOff /><div><strong>Room search is temporarily unavailable</strong><p>{error}</p></div><Button variant="secondary" onClick={load}><RefreshCw /> Try again</Button></div>}
      {!loading && availability && <>
        <header className="inventory-panel__head"><div><SourceBadge availability={availability} /><p>{rateCount} rate {rateCount === 1 ? 'plan' : 'plans'} checked {new Date(availability.checkedAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</p></div><button className="refresh-rates" onClick={load}><RefreshCw /> Refresh prices</button></header>
        {availability.rooms.length ? <div className="room-list">{availability.rooms.map((room) => <section className="room-type" key={room.id}>
          <header><div><span className="eyebrow">Room type</span><h4>{room.name}</h4><p>{room.description}</p></div><div className="room-facts"><span><UsersRound /> Up to {room.occupancy.maxGuests}</span><span><BedSingle /> {room.beds.map((bed) => `${bed.count} ${bed.type}`).join(' · ') || 'Bed details on request'}</span><span>{room.board || 'Room only'}</span></div></header>
          <div className="rate-list">{room.ratePlans.map((rate) => <RatePlan key={rate.id} tripId={tripId} recommendationId={recommendation.id} rate={rate} onCartChange={onCartChange} onAdded={onAdded} />)}</div>
        </section>)}</div> : <div className="inventory-empty"><AlertTriangle /><strong>No rooms remain for these dates.</strong><p>Try alternatives or adjust your dates. Nothing has been reserved.</p></div>}
      </>}
    </div>}
  </section>
}
