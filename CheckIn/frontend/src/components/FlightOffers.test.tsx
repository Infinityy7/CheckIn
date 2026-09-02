import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api } from '../services/api'
import type { FlightAvailability, FlightOffer, Recommendation, TripCart } from '../types'
import { FlightOffers } from './FlightOffers'

const recommendation: Recommendation = {
  id: 'transport-1', name: 'Fast route', category: 'transport', description: 'Door to door.', reasoning: 'Fits the trip.',
  estimated_cost: '$800', cost_min: 700, cost_max: 900, rating: 4.6, review_count: 400, location: 'BOM to KIX',
  image_search_query: 'flight', metadata: {}, rank: 1, score: .92, score_breakdown: {},
}

const offerOne: FlightOffer = {
  id: 'offer-1', carrier: 'Air Example', flightNumber: 'AE 42', origin: 'BOM', destination: 'KIX',
  departAt: '2026-10-12T03:30:00Z', arriveAt: '2026-10-12T15:10:00Z', durationMinutes: 700, stops: 1,
  journeyType: 'round_trip',
  total: { amount: 780, currency: 'USD' }, availabilityStatus: 'available', quoteExpiresAt: new Date(Date.now() + 15 * 60_000).toISOString(),
  source: 'Duffel sandbox', sourceMode: 'demo', isLive: false,
}

const offerTwo: FlightOffer = { ...offerOne, id: 'offer-2', carrier: 'Northstar Demo', flightNumber: 'NS 7', stops: 0, total: { amount: 910, currency: 'USD' } }

const flights: FlightAvailability = {
  recommendationId: recommendation.id, source: 'Duffel sandbox', sourceMode: 'demo', isLive: false, checkedAt: new Date().toISOString(),
  offers: [offerOne],
}

const emptyCart: TripCart = { tripId: 'trip-1', version: 1, state: 'open', checkedAt: new Date().toISOString(), items: [] }

function cartHolding(offerId: string): TripCart {
  return { ...emptyCart, version: 2, state: 'ready', items: [{ id: `cart-${offerId}`, recommendationId: recommendation.id, ratePlanId: offerId, kind: 'flight', title: 'Air Example AE 42', status: 'quoted' }] }
}

function Harness({ initial, onCartChange, onAdded }: { initial: TripCart | null; onCartChange?: (cart: TripCart) => void; onAdded: () => void }) {
  const [cart, setCart] = useState(initial)
  return <FlightOffers tripId="trip-1" recommendation={recommendation} cart={cart} onCartChange={(next) => { setCart(next); onCartChange?.(next) }} onAdded={onAdded} />
}

afterEach(() => vi.restoreAllMocks())

it('lazily shows source-labelled flight offers with bookable details', async () => {
  const load = vi.spyOn(api, 'flightOffers').mockResolvedValue(flights)
  render(<FlightOffers tripId="trip-1" recommendation={recommendation} cart={emptyCart} onCartChange={vi.fn()} onAdded={vi.fn()} />)

  expect(load).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))

  expect(await screen.findByText(/via Duffel sandbox/i)).toBeInTheDocument()
  expect(screen.getByText(/Air Example · AE 42/i)).toBeInTheDocument()
  expect(screen.getByText('BOM')).toBeInTheDocument()
  expect(screen.getByText('KIX')).toBeInTheDocument()
  expect(screen.getByText(/11h 40m · 1 stop/i)).toBeInTheDocument()
  expect(screen.getByText('$780')).toBeInTheDocument()
  expect(screen.getByText('return-trip total')).toBeInTheDocument()
  expect(screen.getByText(/Quote expires in/i)).toBeInTheDocument()
})

it('marks demo offers with the shared non-live source badge, never as live inventory', async () => {
  vi.spyOn(api, 'flightOffers').mockResolvedValue(flights)
  render(<FlightOffers tripId="trip-1" recommendation={recommendation} cart={null} onCartChange={vi.fn()} onAdded={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))

  const nonLive = await screen.findAllByText('Demo · non-live sample')
  expect(nonLive.length).toBeGreaterThanOrEqual(2)
  expect(screen.queryByText('Live inventory')).not.toBeInTheDocument()
})

it('adds an exact flight offer, selects its transport recommendation only after success, and derives Added from the cart', async () => {
  vi.spyOn(api, 'flightOffers').mockResolvedValue(flights)
  const saved = cartHolding('offer-1')
  const add = vi.spyOn(api, 'addCartItem').mockResolvedValue(saved)
  const onCartChange = vi.fn(); const onAdded = vi.fn()
  render(<Harness initial={emptyCart} onCartChange={onCartChange} onAdded={onAdded} />)

  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Save flight quote/i }))

  await waitFor(() => expect(add).toHaveBeenCalledWith('trip-1', 'transport-1', 'offer-1', 'flight'))
  expect(onCartChange).toHaveBeenCalledWith(saved)
  expect(onAdded).toHaveBeenCalledOnce()
  expect(await screen.findByRole('button', { name: /Added/i })).toBeDisabled()
})

it('shows exactly one Added when two offers exist and the cart holds one, with a replace action on the other', async () => {
  vi.spyOn(api, 'flightOffers').mockResolvedValue({ ...flights, offers: [offerOne, offerTwo] })
  render(<FlightOffers tripId="trip-1" recommendation={recommendation} cart={cartHolding('offer-2')} onCartChange={vi.fn()} onAdded={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))
  await screen.findByText(/Northstar Demo · NS 7/i)

  expect(screen.getAllByRole('button', { name: /^Added$/i })).toHaveLength(1)
  expect(screen.getByRole('button', { name: /Replace saved flight/i })).toBeEnabled()
  expect(screen.queryByRole('button', { name: /Save flight quote/i })).not.toBeInTheDocument()
})

it('does not select the recommendation when saving the offer fails', async () => {
  vi.spyOn(api, 'flightOffers').mockResolvedValue(flights)
  vi.spyOn(api, 'addCartItem').mockRejectedValue(new Error('That flight is not part of this trip’s latest supplier results.'))
  const onAdded = vi.fn()
  render(<Harness initial={emptyCart} onAdded={onAdded} />)

  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Save flight quote/i }))

  expect(await screen.findByRole('alert')).toHaveTextContent('latest supplier results')
  expect(onAdded).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: /Added/i })).not.toBeInTheDocument()
})

it('shows provider errors without inserting a fake flight', async () => {
  vi.spyOn(api, 'flightOffers').mockRejectedValue(new Error('Flight provider not configured.'))
  render(<FlightOffers tripId="trip-1" recommendation={recommendation} cart={emptyCart} onCartChange={vi.fn()} onAdded={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Flight provider not configured.')
  expect(screen.queryByText('Air Example')).not.toBeInTheDocument()
})
