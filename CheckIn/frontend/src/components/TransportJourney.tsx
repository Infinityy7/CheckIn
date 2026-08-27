import '../styles/inventory.css'
import { Building2, CarFront, House, MapPinned, Plane, Route } from 'lucide-react'
import type { Recommendation, TripCart, TripPreferences } from '../types'
import { FlightOffers } from './FlightOffers'

type Metadata = Record<string, unknown>
type TransportLeg = { mode?: string; route?: string; timing?: string; duration?: string; estimated_cost?: string }

function text(metadata: Metadata, ...keys: string[]) {
  for (const key of keys) if (typeof metadata[key] === 'string' && metadata[key]) return metadata[key] as string
  return ''
}

function group(metadata: Metadata, key: string): Metadata {
  const value = metadata[key]
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Metadata : {}
}

function leg(metadata: Metadata, key: string): TransportLeg | null {
  const value = metadata[key]
  return value && typeof value === 'object' && !Array.isArray(value) ? value as TransportLeg : null
}

function legDetail(value: TransportLeg | null, legacy: string, pending: string) {
  if (!value) return legacy || pending
  return [value.mode, value.timing, value.duration, value.estimated_cost].filter(Boolean).join(' · ') || pending
}

function list(metadata: Metadata, key: string) {
  const value = metadata[key]
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string').join(', ') : ''
}

function Leg({ number, icon: Icon, title, route, detail, pending }: {
  number: number
  icon: typeof CarFront
  title: string
  route: string
  detail: string
  pending?: boolean
}) {
  return <li className={pending ? 'journey-leg journey-leg--pending' : 'journey-leg'}>
    <span className="journey-leg__number">{number}</span><span className="journey-leg__icon"><Icon /></span>
    <div><small>{title}</small><strong>{route}</strong><p>{detail}</p></div>
  </li>
}

export function TransportJourney({ tripId, recommendation, preferences, onCartChange, onFlightAdded }: {
  tripId?: string
  recommendation: Recommendation
  preferences: TripPreferences
  onCartChange?: (cart: TripCart) => void
  onFlightAdded?: () => void
}) {
  const metadata = recommendation.metadata ?? {}
  const outbound = group(metadata, 'outbound')
  const inbound = group(metadata, 'return')
  const outboundPickup = leg(outbound, 'home_to_airport')
  const outboundFlight = leg(outbound, 'flight')
  const arrivalRide = leg(outbound, 'airport_to_hotel')
  const returnRide = leg(inbound, 'hotel_to_airport')
  const returnFlight = leg(inbound, 'flight')
  const homeRide = leg(inbound, 'airport_to_home')
  const daily = text(metadata, 'daily_transport')

  return <section className="transport-journey" aria-label={`Door-to-door transport for ${recommendation.name}`}>
    <header><span><Route /> Complete journey</span><small>Each leg is priced and confirmed separately</small></header>
    <div className="journey-groups">
      <section><h4>Outbound</h4><ol>
        <Leg number={1} icon={House} title="Pickup" route={outboundPickup?.route || `Home → ${preferences.origin} airport`} detail={legDetail(outboundPickup, text(metadata, 'home_to_airport', 'departure_transfer'), 'Add your pickup address after choosing a flight to see ride options.')} pending={!outboundPickup && !text(metadata, 'home_to_airport', 'departure_transfer')} />
        <Leg number={2} icon={Plane} title="Main journey" route={outboundFlight?.route || `${preferences.origin} → ${preferences.destination}`} detail={legDetail(outboundFlight, text(metadata, 'outbound_flight', 'flight', 'getting_there'), 'Live flight times and fares will appear here when connected.')} pending={!outboundFlight && !text(metadata, 'outbound_flight', 'flight', 'getting_there')} />
        <Leg number={3} icon={Building2} title="Arrival ride" route={arrivalRide?.route || 'Airport → selected hotel'} detail={legDetail(arrivalRide, text(metadata, 'airport_to_hotel', 'arrival_transfer'), 'Ride options unlock after your flight and hotel are selected.')} pending={!arrivalRide && !text(metadata, 'airport_to_hotel', 'arrival_transfer')} />
      </ol></section>
      <section><h4>Return</h4><ol>
        <Leg number={1} icon={Building2} title="Hotel pickup" route={returnRide?.route || 'Selected hotel → airport'} detail={legDetail(returnRide, text(metadata, 'hotel_to_airport', 'return_departure_transfer'), 'Timed from your selected return flight.')} pending={!returnRide && !text(metadata, 'hotel_to_airport', 'return_departure_transfer')} />
        <Leg number={2} icon={Plane} title="Flight home" route={returnFlight?.route || `${preferences.destination} → ${preferences.origin}`} detail={legDetail(returnFlight, text(metadata, 'return_flight'), 'Return availability is confirmed with the flight provider.')} pending={!returnFlight && !text(metadata, 'return_flight')} />
        <Leg number={3} icon={CarFront} title="Final ride" route={homeRide?.route || `${preferences.origin} airport → home`} detail={legDetail(homeRide, text(metadata, 'airport_to_home', 'return_arrival_transfer'), 'Prepared after your arrival time is confirmed.')} pending={!homeRide && !text(metadata, 'airport_to_home', 'return_arrival_transfer')} />
      </ol></section>
    </div>
    <div className="daily-mobility"><MapPinned /><div><small>Daily mobility</small><strong>{daily || 'Local transport plan pending'}</strong><p>{text(metadata, 'passes_or_cards') || list(metadata, 'apps_to_download') || 'Kept separate from airport and flight bookings.'}</p></div></div>
    {tripId && onCartChange && onFlightAdded && <FlightOffers tripId={tripId} recommendation={recommendation} onCartChange={onCartChange} onAdded={onFlightAdded} />}
  </section>
}
