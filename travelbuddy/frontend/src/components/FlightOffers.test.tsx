import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api } from '../services/api'
import type { FlightAvailability, Recommendation, TripCart } from '../types'
import { FlightOffers } from './FlightOffers'

const recommendation: Recommendation = {
  id: 'transport-1', name: 'Fast route', category: 'transport', description: 'Door to door.', reasoning: 'Fits the trip.',
  estimated_cost: '$800', cost_min: 700, cost_max: 900, rating: 4.6, review_count: 400, location: 'BOM to KIX',
  image_search_query: 'flight', metadata: {}, rank: 1, score: .92, score_breakdown: {},
}

const flights: FlightAvailability = {
  recommendationId: recommendation.id, source: 'Duffel sandbox', sourceMode: 'demo', isLive: false, checkedAt: new Date().toISOString(),
  offers: [{ id: 'offer-1', carrier: 'Air Example', flightNumber: 'AE 42', origin: 'BOM', destination: 'KIX',
    departAt: '2026-10-12T03:30:00Z', arriveAt: '2026-10-12T15:10:00Z', durationMinutes: 700, stops: 1,
    journeyType: 'round_trip',
    total: { amount: 780, currency: 'USD' }, availabilityStatus: 'available', quoteExpiresAt: new Date(Date.now() + 15 * 60_000).toISOString(),
    source: 'Duffel sandbox', sourceMode: 'demo', isLive: false }],
}

afterEach(() => vi.restoreAllMocks())

it('lazily shows source-labelled flight offers with bookable details', async () => {
  const load = vi.spyOn(api, 'flightOffers').mockResolvedValue(flights)
  render(<FlightOffers tripId="trip-1" recommendation={recommendation} onCartChange={vi.fn()} onAdded={vi.fn()} />)

  expect(load).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))

  expect(await screen.findByText(/Demo flight offers · Duffel sandbox/i)).toBeInTheDocument()
  expect(screen.getByText(/Air Example · AE 42/i)).toBeInTheDocument()
  expect(screen.getByText('BOM')).toBeInTheDocument()
  expect(screen.getByText('KIX')).toBeInTheDocument()
  expect(screen.getByText(/11h 40m · 1 stop/i)).toBeInTheDocument()
  expect(screen.getByText('$780')).toBeInTheDocument()
  expect(screen.getByText('return-trip total')).toBeInTheDocument()
})

it('adds an exact flight offer and selects its transport recommendation only after success', async () => {
  vi.spyOn(api, 'flightOffers').mockResolvedValue(flights)
  const cart: TripCart = { tripId: 'trip-1', state: 'open', checkedAt: new Date().toISOString(), items: [] }
  const add = vi.spyOn(api, 'addCartItem').mockResolvedValue(cart)
  const onCartChange = vi.fn(); const onAdded = vi.fn()
  render(<FlightOffers tripId="trip-1" recommendation={recommendation} onCartChange={onCartChange} onAdded={onAdded} />)

  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Save flight quote/i }))

  await waitFor(() => expect(add).toHaveBeenCalledWith('trip-1', 'transport-1', 'offer-1', 'flight'))
  expect(onCartChange).toHaveBeenCalledWith(cart)
  expect(onAdded).toHaveBeenCalledOnce()
})

it('shows provider errors without inserting a fake flight', async () => {
  vi.spyOn(api, 'flightOffers').mockRejectedValue(new Error('Flight provider not configured.'))
  render(<FlightOffers tripId="trip-1" recommendation={recommendation} onCartChange={vi.fn()} onAdded={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Flight provider not configured.')
  expect(screen.queryByText('Air Example')).not.toBeInTheDocument()
})
