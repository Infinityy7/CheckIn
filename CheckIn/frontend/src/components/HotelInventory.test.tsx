import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api } from '../services/api'
import type { HotelAvailability, Recommendation, TripCart } from '../types'
import { HotelInventory } from './HotelInventory'

const recommendation: Recommendation = {
  id: 'hotel-1', name: 'Gion House', category: 'hotel', description: 'A quiet stay.', reasoning: 'Fits your pace.',
  estimated_cost: '$250 / night', cost_min: 200, cost_max: 300, rating: 4.8, review_count: 920, location: 'Gion',
  image_search_query: 'Gion House', metadata: {}, rank: 1, score: .93, score_breakdown: {},
}

const availability: HotelAvailability = {
  hotelId: 'supplier-hotel-1', recommendationId: recommendation.id, source: 'Expedia Rapid Sandbox', sourceMode: 'demo', isLive: false,
  checkedAt: new Date().toISOString(), rooms: [{
    id: 'room-deluxe', name: 'Deluxe king', description: 'Garden-facing room', occupancy: { adults: 2, children: 1, maxGuests: 3 },
    beds: [{ type: 'king', count: 1 }], board: 'Breakfast included', ratePlans: [{
      id: 'rate-flex', label: 'Flexible breakfast rate', total: { amount: 640, currency: 'USD' }, nightly: { amount: 290, currency: 'USD' },
      taxesAndFees: { amount: 60, currency: 'USD' }, refundable: true, cancellationSummary: 'Free cancellation until 48 hours before arrival.',
      availabilityStatus: 'limited', roomsRemaining: 2, quoteExpiresAt: new Date(Date.now() + 20 * 60_000).toISOString(),
      source: 'Expedia Rapid Sandbox', sourceMode: 'demo', isLive: false,
    }],
  }],
}

const cart: TripCart = { tripId: 'trip-1', state: 'open', checkedAt: new Date().toISOString(), items: [] }

afterEach(() => vi.restoreAllMocks())

it('loads exact-date hotel rates only when the traveler opens the room panel', async () => {
  const rates = vi.spyOn(api, 'hotelRates').mockResolvedValue(availability)
  render(<HotelInventory tripId="trip-1" recommendation={recommendation} onCartChange={vi.fn()} />)

  expect(rates).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))

  expect(await screen.findByText('Deluxe king')).toBeInTheDocument()
  expect(screen.getByText('$640')).toBeInTheDocument()
  expect(screen.getByText('$290 / night')).toBeInTheDocument()
  expect(screen.getByText('$60 taxes & fees')).toBeInTheDocument()
  expect(screen.getByText(/2 left at this price/i)).toBeInTheDocument()
  expect(rates).toHaveBeenCalledWith('trip-1', 'hotel-1')
})

it('keeps demo inventory visibly labelled non-live at panel and rate level', async () => {
  vi.spyOn(api, 'hotelRates').mockResolvedValue(availability)
  render(<HotelInventory tripId="trip-1" recommendation={recommendation} onCartChange={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))

  const nonLive = await screen.findAllByText('Demo · non-live sample')
  expect(nonLive.length).toBeGreaterThanOrEqual(2)
  expect(screen.getByText(/via Expedia Rapid Sandbox/i)).toBeInTheDocument()
  expect(screen.queryByText('Live inventory')).not.toBeInTheDocument()
})

it('counts down the supplier quote expiry on each rate plan', async () => {
  vi.spyOn(api, 'hotelRates').mockResolvedValue(availability)
  render(<HotelInventory tripId="trip-1" recommendation={recommendation} onCartChange={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))

  expect(await screen.findByText(/Quote expires in/i)).toBeInTheDocument()
})

it('adds the selected rate to the cart and keeps itinerary selection in sync', async () => {
  vi.spyOn(api, 'hotelRates').mockResolvedValue(availability)
  const add = vi.spyOn(api, 'addCartItem').mockResolvedValue(cart)
  const onCartChange = vi.fn()
  const onAdded = vi.fn()
  render(<HotelInventory tripId="trip-1" recommendation={recommendation} onCartChange={onCartChange} onAdded={onAdded} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Save quoted rate/i }))

  await waitFor(() => expect(add).toHaveBeenCalledWith('trip-1', 'hotel-1', 'rate-flex', 'hotel'))
  expect(onCartChange).toHaveBeenCalledWith(cart)
  expect(onAdded).toHaveBeenCalledOnce()
  expect(screen.getByRole('button', { name: /Added/i })).toBeDisabled()
})

it('shows a real provider failure with a retry instead of invented availability', async () => {
  vi.spyOn(api, 'hotelRates').mockRejectedValue(new Error('Provider credentials are not configured.'))
  render(<HotelInventory tripId="trip-1" recommendation={recommendation} onCartChange={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Provider credentials are not configured.')
  expect(screen.queryByText('Deluxe king')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /Try again/i })).toBeInTheDocument()
})
