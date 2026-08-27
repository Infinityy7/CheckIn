import { act, type ComponentProps } from 'react'
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
  cotravellers: [], cotraveller_usernames: [],
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

const allComplete: AgentStatus = {
  'Accommodation Agent': 'complete',
  'Activities Agent': 'complete',
  'Restaurant Agent': 'complete',
  'Transport Agent': 'complete',
}

type WorkspaceProps = ComponentProps<typeof Workspace>

function renderWorkspace(overrides: Partial<WorkspaceProps> = {}) {
  const props: WorkspaceProps = {
    tripId: 'trip-1',
    destination: 'Kyoto',
    preferences,
    profile: null,
    recommendations: [recommendation],
    agents: allComplete,
    researching: false,
    selections: [],
    companions: [],
    onToggle: vi.fn(),
    onRetryMissing: vi.fn(),
    onFullRefresh: vi.fn(),
    onFeedback: vi.fn().mockResolvedValue(undefined),
    onBuild: vi.fn(),
    ...overrides,
  }
  render(<Workspace {...props} />)
  return props
}

const settle = () => act(async () => {})

beforeEach(() => {
  vi.spyOn(api, 'cart').mockResolvedValue({ tripId: 'trip-1', state: 'open', items: [], checkedAt: new Date().toISOString() })
})

afterEach(() => vi.restoreAllMocks())

it('marks cached recommendations with age and taste match and notes the cache at workspace level', async () => {
  const cachedRec: Recommendation = { ...recommendation, metadata: { cached: true, cache_age_seconds: 600, cache_similarity: 0.82 } }
  renderWorkspace({ recommendations: [cachedRec] })
  await settle()

  expect(screen.getByText(/Cached 10m ago · 82% taste match/)).toBeInTheDocument()
  expect(screen.getByText(/served from CheckIn's research cache/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /Refresh live/i })).toBeInTheDocument()
})

it('shows no cache affordances when nothing came from the cache', async () => {
  renderWorkspace()
  await settle()

  expect(screen.queryByText(/Cached \d/)).not.toBeInTheDocument()
  expect(screen.queryByText(/research cache/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Refresh live/i })).not.toBeInTheDocument()
})

it('runs a full refresh from the cached-results note only after the confirm step', async () => {
  const cachedRec: Recommendation = { ...recommendation, metadata: { cached: true, cache_age_seconds: 60 } }
  const { onFullRefresh } = renderWorkspace({ recommendations: [cachedRec] })
  await settle()

  fireEvent.click(screen.getByRole('button', { name: /Refresh live/i }))
  expect(onFullRefresh).not.toHaveBeenCalled()
  expect(screen.getByText(/selections are cleared/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Replace everything/i }))
  expect(onFullRefresh).toHaveBeenCalledOnce()
})

it('warns before a full refresh and lets the traveller back out', async () => {
  const { onFullRefresh } = renderWorkspace()
  await settle()

  fireEvent.click(screen.getByRole('button', { name: /Full refresh/i }))
  fireEvent.click(screen.getByRole('button', { name: /Keep my shortlist/i }))
  expect(onFullRefresh).not.toHaveBeenCalled()
  expect(screen.queryByText(/selections are cleared/)).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /Full refresh/i }))
  fireEvent.click(screen.getByRole('button', { name: /Replace everything/i }))
  expect(onFullRefresh).toHaveBeenCalledOnce()
})

it('keeps successful results, retries only missing categories, and never offers full refresh alongside failures', async () => {
  const agents: AgentStatus = { ...allComplete, 'Activities Agent': 'failed' }
  const { onRetryMissing } = renderWorkspace({ agents })
  await settle()

  expect(screen.getByText('Partial results ready')).toBeInTheDocument()
  expect(screen.getByText(/1 research category needs another try/)).toBeInTheDocument()
  expect(screen.getByText('A quiet Kyoto stay')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Full refresh/i })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Retry missing categories/i }))
  expect(onRetryMissing).toHaveBeenCalledOnce()
})

it('never offers a full refresh while research is running', async () => {
  const agents: AgentStatus = { ...allComplete, 'Activities Agent': 'working' }
  renderWorkspace({ agents, researching: true })
  await settle()

  expect(screen.queryByRole('button', { name: /Full refresh/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Retry missing categories/i })).not.toBeInTheDocument()
})

it('records a like and shows the per-card noted state', async () => {
  const { onFeedback } = renderWorkspace()
  await settle()

  fireEvent.click(screen.getByRole('button', { name: /More like this: A quiet Kyoto stay/i }))
  await waitFor(() => expect(onFeedback).toHaveBeenCalledWith(expect.objectContaining({ id: 'hotel-1' }), 'like'))
  expect(await screen.findByText(/Noted — more like this/)).toBeInTheDocument()
})

it('records a dislike through the thumbs-down control', async () => {
  const { onFeedback } = renderWorkspace()
  await settle()

  fireEvent.click(screen.getByRole('button', { name: /Not my thing: A quiet Kyoto stay/i }))
  await waitFor(() => expect(onFeedback).toHaveBeenCalledWith(expect.objectContaining({ id: 'hotel-1' }), 'dislike'))
  expect(await screen.findByText(/Noted — fewer like this/)).toBeInTheDocument()
})

it('discloses score meters and matched taste tags under why-this-ranked', async () => {
  const scored: Recommendation = { ...recommendation, score_breakdown: { budget: 0.8, taste: 0.9, rating: 0.7, vibes: 0.6, matched: ['izakaya', 'tea houses'] } }
  renderWorkspace({ recommendations: [scored] })
  await settle()

  fireEvent.click(screen.getByText('Why this ranked'))
  expect(screen.getByRole('meter', { name: 'Budget' })).toHaveAttribute('aria-valuenow', '80')
  expect(screen.getByRole('meter', { name: 'Taste' })).toHaveAttribute('aria-valuenow', '90')
  expect(screen.getByRole('meter', { name: 'Rating' })).toBeInTheDocument()
  expect(screen.getByRole('meter', { name: 'Vibes' })).toBeInTheDocument()
  expect(screen.getByText('izakaya')).toBeInTheDocument()
  expect(screen.getByText('tea houses')).toBeInTheDocument()
})

it('shows the group-fit chip and veto note when companions travel along', async () => {
  renderWorkspace({ companions: ['Asha', 'Ravi'] })
  await settle()

  expect(screen.getAllByText('Balanced across 3 travellers').length).toBeGreaterThan(0)
  expect(screen.getByText(/clashed with anyone's no-gos or diets were removed before ranking/)).toBeInTheDocument()
})

it('shows no group-fit chip for solo travellers', async () => {
  renderWorkspace({ companions: [] })
  await settle()

  expect(screen.queryByText(/Balanced across/)).not.toBeInTheDocument()
  expect(screen.queryByText(/no-gos or diets/)).not.toBeInTheDocument()
})

it('selects the matching hotel when its eact room rate is added to the cart', async () => {
  vi.spyOn(api, 'hotelRates').mockResolvedValue({
    hotelId: 'supplier-hotel', recommendationId: recommendation.id, source: 'Demo inventory', sourceMode: 'demo', isLive: false,
    checkedAt: new Date().toISOString(), rooms: [{ id: 'room-1', name: 'Garden room', occupancy: { adults: 2, children: 0, maxGuests: 2 }, beds: [{ type: 'king', count: 1 }], board: 'Room only', ratePlans: [{
      id: 'rate-1', label: 'Fleible', total: { amount: 500, currency: 'USD' }, nightly: { amount: 250, currency: 'USD' }, taxesAndFees: { amount: 50, currency: 'USD' },
      refundable: true, cancellationSummary: 'Free cancellation before arrival.', availabilityStatus: 'available', source: 'Demo inventory', sourceMode: 'demo', isLive: false,
    }] }],
  })
  vi.spyOn(api, 'addCartItem').mockResolvedValue({ tripId: 'trip-1', state: 'open', items: [], checkedAt: new Date().toISOString() })
  const { onToggle } = renderWorkspace()
  await settle()

  fireEvent.click(screen.getByRole('button', { name: /Rooms & booking prices/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Save quoted rate/i }))

  await waitFor(() => expect(onToggle).toHaveBeenCalledWith('hotel-1'))
})

it('saves restaurant choices in the unified cart before selecting them', async () => {
  const restaurant: Recommendation = { ...recommendation, id: 'restaurant-1', name: 'Kappo Sora', category: 'restaurant' }
  const cart = { tripId: 'trip-1', state: 'open' as const, checkedAt: new Date().toISOString(), items: [{ id: 'cart-restaurant-1', recommendationId: restaurant.id, kind: 'restaurant' as const, title: restaurant.name, status: 'saved' as const }] }
  const add = vi.spyOn(api, 'addCartItem').mockResolvedValue(cart)
  const { onToggle } = renderWorkspace({ recommendations: [recommendation, restaurant] })
  await settle()

  fireEvent.click(screen.getByRole('tab', { name: /Food/i }))
  fireEvent.click(screen.getByRole('button', { name: /Choose this/i }))

  await waitFor(() => expect(add).toHaveBeenCalledWith('trip-1', 'restaurant-1', undefined, 'restaurant'))
  expect(onToggle).toHaveBeenCalledWith('restaurant-1')
})

it('selects an eact flight without also creating a generic transport cart item', async () => {
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
  const { onToggle } = renderWorkspace({ recommendations: [transport] })
  await settle()

  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Save flight quote/i }))

  await waitFor(() => expect(add).toHaveBeenCalledTimes(1))
  expect(add).toHaveBeenCalledWith('trip-1', 'transport-1', 'offer-1', 'flight')
  expect(onToggle).toHaveBeenCalledWith('transport-1')
})
