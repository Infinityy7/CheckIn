import '../styles/inventory.css'
import { useState } from 'react'
import { AlertTriangle, Check, ChevronDown, Clock3, Plane, RefreshCw, WifiOff } from 'lucide-react'
import { api, userErrorMessage } from '../services/api'
import type { FlightAvailability, FlightOffer, Money, Recommendation, TripCart } from '../types'
import { Button, Chip, Countdown, SourceBadge } from './UI'

function money(value: Money) {
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: value.currency, maximumFractionDigits: value.amount % 1 ? 2 : 0 }).format(value.amount)
}

function time(value: string) {
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : value
}

function duration(minutes: number) {
  if (!Number.isFinite(minutes) || minutes <= 0) return 'Duration unavailable'
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

function journeyPriceLabel(offer: FlightOffer) {
  if (offer.journeyType === 'round_trip') return 'return-trip total'
  if (offer.journeyType === 'one_way') return 'one-way total'
  return 'trip total'
}

/** Failure-closed source labeling: unconfigured providers are named, demo/test stays visibly non-live. */
function SourceLine({ data }: { data: FlightAvailability }) {
  return <span className="source-line">
    {data.sourceMode === 'unavailable'
      ? <Chip tone="muted" icon={<WifiOff aria-hidden />}>Inventory not configured</Chip>
      : <SourceBadge sourceMode={data.sourceMode} isLive={data.isLive} />}
    {data.source && <small>via {data.source}</small>}
  </span>
}

function Offer({ tripId, recommendationId, offer, onCartChange, onAdded }: {
  tripId: string
  recommendationId: string
  offer: FlightOffer
  onCartChange: (cart: TripCart) => void
  onAdded: () => void
}) {
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState(false)
  const [error, setError] = useState('')
  const unavailable = ['unavailable', 'expired', 'price_changed'].includes(offer.availabilityStatus)

  async function add() {
    setAdding(true); setError('')
    try {
      const cart = await api.addCartItem(tripId, recommendationId, offer.id, 'flight')
      onCartChange(cart); setAdded(true); onAdded()
    } catch (reason) { setError(userErrorMessage(reason, 'That flight could not be saved. Refresh its fare and try again.')) }
    finally { setAdding(false) }
  }

  return <article className={`flight-offer flight-offer--${offer.availabilityStatus}`}>
    <header><span><Plane /> {offer.carrier}{offer.flightNumber ? ` · ${offer.flightNumber}` : ''}</span><strong>{money(offer.total)}<small>{journeyPriceLabel(offer)}</small></strong></header>
    <div className="flight-route"><div><b>{offer.origin}</b><time dateTime={offer.departAt}>{time(offer.departAt)}</time></div><span><i />{duration(offer.durationMinutes)} · {offer.stops === 0 ? 'Direct' : `${offer.stops} ${offer.stops === 1 ? 'stop' : 'stops'}`}<i /></span><div><b>{offer.destination}</b><time dateTime={offer.arriveAt}>{time(offer.arriveAt)}</time></div></div>
    <div className="flight-offer__meta">
      <SourceBadge sourceMode={offer.sourceMode} isLive={offer.isLive} />
      <Countdown
        expiresAt={offer.holdExpiresAt ?? offer.quoteExpiresAt}
        label={offer.holdExpiresAt ? 'Hold expires in' : 'Quote expires in'}
        expiredLabel={offer.holdExpiresAt ? 'Hold expired — refresh fares' : 'Quote expired — refresh fares'}
      />
    </div>
    <Button variant={added ? 'secondary' : 'primary'} onClick={add} disabled={adding || added || unavailable}>{added ? <><Check /> Added</> : adding ? 'Adding…' : offer.holdExpiresAt ? 'Add held flight' : 'Save flight quote'}</Button>
    {error && <p className="flight-offer__error" role="alert"><AlertTriangle /> {error}</p>}
  </article>
}

export function FlightOffers({ tripId, recommendation, onCartChange, onAdded }: {
  tripId: string
  recommendation: Recommendation
  onCartChange: (cart: TripCart) => void
  onAdded: () => void
}) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<FlightAvailability | null>(null)
  const [error, setError] = useState('')
  const panelId = `flight-offers-${recommendation.id.replace(/[^a-zA-Z0-9_-]/g, '-')}`

  async function load() {
    setLoading(true); setError('')
    try { setData(await api.flightOffers(tripId, recommendation.id)) }
    catch (reason) { setError(userErrorMessage(reason, 'Live flight offers are unavailable right now.')) }
    finally { setLoading(false) }
  }

  function toggle() {
    const next = !open; setOpen(next)
    if (next && !data && !loading) void load()
  }

  return <section className="flight-inventory">
    <button className="flight-toggle" type="button" onClick={toggle} aria-expanded={open} aria-controls={panelId}><span><Plane /><b>Flight times & fares</b><small>Check bookable offers for your dates</small></span><ChevronDown aria-hidden="true" /></button>
    {open && <div className="flight-panel" id={panelId}>
      {loading && <p className="flight-loading" role="status"><Clock3 /> Checking live flight inventory…</p>}
      {!loading && error && <div className="flight-error" role="alert"><WifiOff /><div><strong>Flight search is temporarily unavailable</strong><p>{error}</p></div><Button variant="secondary" onClick={load}><RefreshCw /> Try again</Button></div>}
      {!loading && data && <><header><div><SourceLine data={data} /><p>Checked {time(data.checkedAt)}</p></div><button className="refresh-rates" type="button" onClick={load}><RefreshCw aria-hidden /> Refresh fares</button></header>
        {data.offers.length ? <div className="flight-offer-list">{data.offers.map((offer) => <Offer key={offer.id} tripId={tripId} recommendationId={recommendation.id} offer={offer} onCartChange={onCartChange} onAdded={onAdded} />)}</div> : <p className="flight-empty">No bookable flights remain for these dates. Nothing has been reserved.</p>}
      </>}
    </div>}
  </section>
}
