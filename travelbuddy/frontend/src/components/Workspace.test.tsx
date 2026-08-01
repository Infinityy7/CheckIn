import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Workspace, type AgentStatus } from './Workspace'
import type { Recommendation, TripPreferences } from '../types'
import { api } from '../services/api'

const preferences: TripPreferences = {
  destination: 'Kyoto',
  origin: 'Mumbai',
  start_date: '2026-10-12',
  end_date: '2026-10-18',
  budget_amount: 3200,
  currency: 'USD',
  vibes: ['culture'],
  group_type: 'couple',
  num_travelers: 2,
  cotravellers: [],
}

const recommendation: Recommendation = {
  id: 'hotel-1',
  name: 'A quiet Kyoto stay',
  category: 'hotel',
  description: 'A dependable local stay.',
  reasoning: 'Matches a balanced pace.',
  estimated_cost: '$180',
  cost_min: 150,
  cost_max: 200,
  rating: 4.7,
  review_count: 800,
  location: 'Gion',
  image_search_query: 'Kyoto hotel',
  metadata: {},
  rank: 1,
  score: 0.9,
  score_breakdown: {},
}

beforeEach(() => {
  vi.spyOn(api, 'cart').mockResolvedValue({ tripId: 'trip-1', state: 'open', items: [], checkedAt: new Date().toISOString() })
})

afterEach(() => vi.restoreAllMocks())

it('keeps successful results visible and offers a retry for failed research', async () => {
  const agents: AgentStatus = {
    'Accommodation Agent': 'complete',
    'Activities Agent': 'failed',
    'Restaurant Agent': 'complete',
    'Transport Agent': 'complete',
  }
  const retry = vi.fn()

  render(<Workspace
    tripId="trip-1"
    destination="Kyoto"
    preferences={preferences}
    profile={null}
    recommendations={[recommendation]}
    agents={agents}
    researching={false}
    selections={[]}
    onToggle={vi.fn()}
    onAlternatives={retry}
    onFeedback={vi.fn().mockResolvedValue(undefined)}
    onBuild={vi.fn()}
  />)

  await screen.findByText(/Open a hotel’s room prices/i)
  expect(screen.getByText('Partial results ready')).toBeInTheDocument()
  expect(screen.getByText(/1 research category needs another try/)).toBeInTheDocument()
  expect(screen.getByText('A quiet Kyoto stay')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Retry research/i }))
  expect(retry).toHaveBeenCalledOnce()
})

it('selects the matching hotel when its exact room rate is added to the cart', async () => {
  vi.spyOn(api, 'hotelRates').mockResolvedValue({
    hotelId: 'supplier-hotel', recommendationId: recommendation.id, source: 'Demo inventory', sourceMode: 'demo', isLive: false,
    checkedAt: new Date().toISOString(), rooms: [{ id: 'room-1', name: 'Garden room', occupancy: { adults: 2, children: 0, maxGuests: 2 }, beds: [{ type: 'king', count: 1 }], board: 'Room only', ratePlans: [{
      id: 'rate-1', label: 'Flexible', total: { amount: 500, currency: 'USD' }, nightly: { amount: 250, currency: 'USD' }, taxesAndFees: { amount: 50, currency: 'USD' },
      refundable: true, cancellationSummary: 'Free cancellation before arrival.', availabilityStatus: 'available', source: 'Demo inventory', sourceMode: 'demo', isLive: false,
    }] }],
  })
  vi.spyOn(api, 'addCartItem').mockResolvedValue({ tripId: 'trip-1', state: 'open', items: [], checkedAt: new Date().toISOString() })
  const toggle = vi.fn()
  const agents: AgentStatus = { 'Accommodation Agent': 'complete', 'Activities Agent': 'complete', 'Restaurant Agent': 'complete', 'Transport Agent': 'complete' }

  render(<Workspace tripId="trip-1" destination="Kyoto" preferences={preferences} profile={null} recommendations={[recommendation]} agents={agents} researching={false} selections={[]} onToggle={toggle} onAlternatives={vi.fn()} onFeedback={vi.fn().mockResolvedValue(undefined)} onBuild={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Save quoted rate/i }))

  await waitFor(() => expect(toggle).toHaveBeenCalledWith('hotel-1'))
})

it('saves restaurant choices in the unified cart before selecting them', async () => {
  const restaurant: Recommendation = { ...recommendation, id: 'restaurant-1', name: 'Kappo Sora', category: 'restaurant' }
  const cart = { tripId: 'trip-1', state: 'open' as const, checkedAt: new Date().toISOString(), items: [{ id: 'cart-restaurant-1', recommendationId: restaurant.id, kind: 'restaurant' as const, title: restaurant.name, status: 'saved' as const }] }
  const add = vi.spyOn(api, 'addCartItem').mockResolvedValue(cart)
  const toggle = vi.fn()
  const agents: AgentStatus = { 'Accommodation Agent': 'complete', 'Activities Agent': 'complete', 'Restaurant Agent': 'complete', 'Transport Agent': 'complete' }
  render(<Workspace tripId="trip-1" destination="Kyoto" preferences={preferences} profile={null} recommendations={[recommendation, restaurant]} agents={agents} researching={false} selections={[]} onToggle={toggle} onAlternatives={vi.fn()} onFeedback={vi.fn().mockResolvedValue(undefined)} onBuild={vi.fn()} />)

  fireEvent.click(screen.getByRole('tab', { name: /Food/i }))
  fireEvent.click(screen.getByRole('button', { name: /Choose this/i }))

  await waitFor(() => expect(add).toHaveBeenCalledWith('trip-1', 'restaurant-1', undefined, 'restaurant'))
  expect(toggle).toHaveBeenCalledWith('restaurant-1')
})

it('selects an exact flight without also creating a generic transport cart item', async () => {
  const transport: Recommendation = { ...recommendation, id: 'transport-1', name: 'Complete journey', category: 'transport' }
  vi.spyOn(api, 'flightOffers').mockResolvedValue({
    recommendationId: transport.id, source: 'Controlled flights', sourceMode: 'test', isLive: false,
    checkedAt: new Date().toISOString(), offers: [{
      id: 'offer-1', carrier: 'Quality Air', flightNumber: 'QA101', origin: 'BOM', destination: 'KIX',
      departAt: '2026-10-12T08:00:00Z', arriveAt: '2026-10-12T17:00:00Z', durationMinutes: 540,
      stops: 0, journeyType: 'round_trip', total: { amount: 740, currency: 'USD' }, availabilityStatus: 'available',
      source: 'Controlled flights', sourceMode: 'test', isLive: false,
    }],
  })
  const cart = { tripId: 'trip-1', state: 'ready' as const, checkedAt: new Date().toISOString(), items: [{ id: 'flight-item', recommendationId: transport.id, ratePlanId: 'offer-1', kind: 'flight' as const, title: 'Quality Air QA101', status: 'quoted' as const }] }
  const add = vi.spyOn(api, 'addCartItem').mockResolvedValue(cart)
  const toggle = vi.fn()
  const agents: AgentStatus = { 'Accommodation Agent': 'complete', 'Activities Agent': 'complete', 'Restaurant Agent': 'complete', 'Transport Agent': 'complete' }
  render(<Workspace tripId="trip-1" destination="Kyoto" preferences={preferences} profile={null} recommendations={[transport]} agents={agents} researching={false} selections={[]} onToggle={toggle} onAlternatives={vi.fn()} onFeedback={vi.fn().mockResolvedValue(undefined)} onBuild={vi.fn()} />)

  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Save flight quote/i }))

  await waitFor(() => expect(add).toHaveBeenCalledTimes(1))
  expect(add).toHaveBeenCalledWith('trip-1', 'transport-1', 'offer-1', 'flight')
  expect(toggle).toHaveBeenCalledWith('transport-1')
})
