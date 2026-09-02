import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api } from '../services/api'
import type { HotelAvailability, HotelRatePlan, Recommendation, TripCart } from '../types'
import { HotelInventory } from './HotelInventory'

const recommendation: Recommendation = {
  id: 'hotel-1', name: 'Gion House', category: 'hotel', description: 'A quiet stay.', reasoning: 'Fits your pace.',
  estimated_cost: '$250 / night', cost_min: 200, cost_max: 300, rating: 4.8, review_count: 920, location: 'Gion',
  image_search_query: 'Gion House', metadata: {}, rank: 1, score: .93, score_breakdown: {},
}

const flexRate: HotelRatePlan = {
  id: 'rate-flex', label: 'Flexible breakfast rate', total: { amount: 640, currency: 'USD' }, nightly: { amount: 290, currency: 'USD' },
  taxesAndFees: { amount: 60, currency: 'USD' }, refundable: true, cancellationSummary: 'Free cancellation until 48 hours before arrival.',
  availabilityStatus: 'limited', roomsRemaining: 2, quoteExpiresAt: new Date(Date.now() + 20 * 60_000).toISOString(),
  source: 'Expedia Rapid Sandbox', sourceMode: 'demo', isLive: false,
}

const basicRate: HotelRatePlan = {
  ...flexRate, id: 'rate-basic', label: 'Room only', refundable: false, cancellationSummary: 'Non-refundable after booking.',
  total: { amount: 560, currency: 'USD' }, nightly: { amount: 250, currency: 'USD' }, availabilityStatus: 'available', roomsRemaining: 5,
}

const availability: HotelAvailability = {
  hotelId: 'supplier-hotel-1', recommendationId: recommendation.id, source: 'Expedia Rapid Sandbox', sourceMode: 'demo', isLive: false,
  checkedAt: new Date().toISOString(), rooms: [{
    id: 'room-deluxe', name: 'Deluxe king', description: 'Garden-facing room', occupancy: { adults: 2, children: 1, maxGuests: 3 },
    beds: [{ type: 'king', count: 1 }], board: 'Breakfast included', ratePlans: [flexRate],
  }],
}

const twoRates: HotelAvailability = { ...availability, rooms: [{ ...availability.rooms[0], ratePlans: [basicRate, flexRate] }] }

const emptyCart: TripCart = { tripId: 'trip-1', version: 1, state: 'open', checkedAt: new Date().toISOString(), items: [] }

function cartHolding(ratePlanId: string, recommendationId = recommendation.id): TripCart {
  return {
    ...emptyCart, version: 2, items: [{ id: `cart-${ratePlanId}`, recommendationId, ratePlanId, kind: 'hotel', title: 'Gion House', status: 'quoted' }],
  }
}

/** Mirrors Workspace: the cart prop is state that the add callback replaces. */
function Harness({ initial, onCartChange, onAdded }: { initial: TripCart | null; onCartChange?: (cart: TripCart) => void; onAdded?: () => void }) {
  const [cart, setCart] = useState(initial)
  return <HotelInventory tripId="trip-1" recommendation={recommendation} cart={cart} onCartChange={(next) => { setCart(next); onCartChange?.(next) }} onAdded={onAdded} />
}

afterEach(() => vi.restoreAllMocks())

it('loads exact-date hotel rates only when the traveler opens the room panel', async () => {
  const rates = vi.spyOn(api, 'hotelRates').mockResolvedValue(availability)
  render(<HotelInventory tripId="trip-1" recommendation={recommendation} cart={emptyCart} onCartChange={vi.fn()} />)

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
  render(<HotelInventory tripId="trip-1" recommendation={recommendation} cart={null} onCartChange={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))

  const nonLive = await screen.findAllByText('Demo · non-live sample')
  expect(nonLive.length).toBeGreaterThanOrEqual(2)
  expect(screen.getByText(/via Expedia Rapid Sandbox/i)).toBeInTheDocument()
  expect(screen.queryByText('Live inventory')).not.toBeInTheDocument()
})

it('counts down the supplier quote expiry on each rate plan', async () => {
  vi.spyOn(api, 'hotelRates').mockResolvedValue(availability)
  render(<HotelInventory tripId="trip-1" recommendation={recommendation} cart={emptyCart} onCartChange={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))

  expect(await screen.findByText(/Quote expires in/i)).toBeInTheDocument()
})

it('adds the selected rate to the cart and shows Added only once the cart holds it', async () => {
  vi.spyOn(api, 'hotelRates').mockResolvedValue(availability)
  const saved = cartHolding('rate-flex')
  const add = vi.spyOn(api, 'addCartItem').mockResolvedValue(saved)
  const onCartChange = vi.fn()
  const onAdded = vi.fn()
  render(<Harness initial={emptyCart} onCartChange={onCartChange} onAdded={onAdded} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Save quoted rate/i }))

  await waitFor(() => expect(add).toHaveBeenCalledWith('trip-1', 'hotel-1', 'rate-flex', 'hotel'))
  expect(onCartChange).toHaveBeenCalledWith(saved)
  expect(onAdded).toHaveBeenCalledOnce()
  expect(await screen.findByRole('button', { name: /Added/i })).toBeDisabled()
  expect(screen.getByText('One exact rate saved to your cart')).toBeInTheDocument()
})

it('marks only the exact rate the cart holds as Added and offers to replace it from another rate', async () => {
  vi.spyOn(api, 'hotelRates').mockResolvedValue(twoRates)
  render(<HotelInventory tripId="trip-1" recommendation={recommendation} cart={cartHolding('rate-flex')} onCartChange={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))
  await screen.findByText('Deluxe king')

  expect(screen.getAllByRole('button', { name: /^Added$/i })).toHaveLength(1)
  expect(screen.getByRole('button', { name: /Added/i })).toBeDisabled()
  expect(screen.getByRole('button', { name: /Replace saved rate/i })).toBeEnabled()
  expect(screen.queryByRole('button', { name: /Save quoted rate/i })).not.toBeInTheDocument()
})

it('does not show Added for a rate saved under a different hotel', async () => {
  vi.spyOn(api, 'hotelRates').mockResolvedValue(availability)
  render(<HotelInventory tripId="trip-1" recommendation={recommendation} cart={cartHolding('rate-flex', 'hotel-2')} onCartChange={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))
  await screen.findByText('Deluxe king')

  expect(screen.queryByRole('button', { name: /Added/i })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /Save quoted rate/i })).toBeEnabled()
})

it('keeps the rate unsaved and reports the failure when the cart rejects it', async () => {
  vi.spyOn(api, 'hotelRates').mockResolvedValue(availability)
  vi.spyOn(api, 'addCartItem').mockRejectedValue(new Error('That supplier price expired. Refresh prices before saving it.'))
  const onAdded = vi.fn()
  render(<Harness initial={emptyCart} onAdded={onAdded} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Save quoted rate/i }))

  expect(await screen.findByRole('alert')).toHaveTextContent('That supplier price expired.')
  expect(onAdded).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: /Save quoted rate/i })).toBeEnabled()
  expect(screen.queryByRole('button', { name: /Added/i })).not.toBeInTheDocument()
})

it('shows a real provider failure with a retry instead of invented availability', async () => {
  vi.spyOn(api, 'hotelRates').mockRejectedValue(new Error('Provider credentials are not configured.'))
  render(<HotelInventory tripId="trip-1" recommendation={recommendation} cart={emptyCart} onCartChange={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Provider credentials are not configured.')
  expect(screen.queryByText('Deluxe king')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /Try again/i })).toBeInTheDocument()
})
