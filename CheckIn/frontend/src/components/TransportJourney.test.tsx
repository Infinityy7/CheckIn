import { fireEvent, render, screen } from '@testing-library/react'
import { api } from '../services/api'
import type { Recommendation, TripCart, TripPreferences } from '../types'
import { TransportJourney } from './TransportJourney'

const preferences: TripPreferences = {
  destination: 'Kyoto', origin: 'Mumbai', start_date: '2026-10-12', end_date: '2026-10-18', budget_amount: 3200,
  currency: 'USD', vibes: ['culture'], group_type: 'couple', num_travelers: 2, cotravellers: [], cotraveller_usernames: [],
}

afterEach(() => vi.restoreAllMocks())

it('presents outbound, return, and daily mobility from structured transport metadata', () => {
  const recommendation = {
    id: 'transport-1', name: 'Door-to-door route', category: 'transport', description: 'Complete journey.', reasoning: 'Fastest fit.',
    estimated_cost: '$900', cost_min: 800, cost_max: 1000, rating: 4.5, review_count: 300, location: 'Mumbai to Kyoto',
    image_search_query: 'route', rank: 1, score: .9, score_breakdown: {}, metadata: {
      outbound: {
        home_to_airport: { mode: 'UberXL', route: 'Bandra → BOM', timing: 'Leave 04:30', duration: '45 min', estimated_cost: '$18' },
        flight: { mode: 'Flight', route: 'BOM → KIX', timing: '07:20', duration: '11h 10m', estimated_cost: '$720' },
        airport_to_hotel: { mode: 'Ride', route: 'KIX → Gion', timing: 'After landing', duration: '90 min', estimated_cost: '$65' },
      },
      return: {
        hotel_to_airport: { mode: 'Ride', route: 'Gion → KIX', timing: 'Leave 3h early', duration: '90 min', estimated_cost: '$65' },
        flight: { mode: 'Flight', route: 'KIX → BOM', timing: 'Evening', duration: '12h', estimated_cost: '$700' },
        airport_to_home: { mode: 'UberXL', route: 'BOM → Bandra', timing: 'After baggage', duration: '45 min', estimated_cost: '$18' },
      },
      daily_transport: 'ICOCA + local rail', passes_or_cards: 'Buy ICOCA at KIX',
    },
  } satisfies Recommendation

  render(<TransportJourney recommendation={recommendation} preferences={preferences} />)

  expect(screen.getByText('Bandra → BOM')).toBeInTheDocument()
  expect(screen.getByText('BOM → KIX')).toBeInTheDocument()
  expect(screen.getByText('KIX → Gion')).toBeInTheDocument()
  expect(screen.getByText('Gion → KIX')).toBeInTheDocument()
  expect(screen.getByText('KIX → BOM')).toBeInTheDocument()
  expect(screen.getByText('BOM → Bandra')).toBeInTheDocument()
  expect(screen.getByText('ICOCA + local rail')).toBeInTheDocument()
})

it('labels missing ride and flight details as pending rather than fabricating them', () => {
  const recommendation = {
    id: 'transport-2', name: 'Pending route', category: 'transport', description: 'Planning.', reasoning: 'Potential match.', estimated_cost: '$0',
    cost_min: 0, cost_max: 0, rating: 0, review_count: 0, location: 'Mumbai to Kyoto', image_search_query: 'route', metadata: {}, rank: 2, score: .5, score_breakdown: {},
  } satisfies Recommendation
  render(<TransportJourney recommendation={recommendation} preferences={preferences} />)
  expect(screen.getByText(/Live flight times and fares will appear here when connected/i)).toBeInTheDocument()
  expect(screen.getByText(/Ride options unlock after your flight and hotel are selected/i)).toBeInTheDocument()
})

it('passes the cart through so the saved flight offer reads as Added', async () => {
  const recommendation = {
    id: 'transport-1', name: 'Complete journey', category: 'transport', description: 'Complete journey.', reasoning: 'Fastest fit.',
    estimated_cost: '$900', cost_min: 800, cost_max: 1000, rating: 4.5, review_count: 300, location: 'Mumbai to Kyoto',
    image_search_query: 'route', rank: 1, score: .9, score_breakdown: {}, metadata: {},
  } satisfies Recommendation
  vi.spyOn(api, 'flightOffers').mockResolvedValue({
    recommendationId: recommendation.id, source: 'Controlled flights', sourceMode: 'test', isLive: false, checkedAt: new Date().toISOString(),
    offers: [
      { id: 'offer-1', carrier: 'Quality Air', flightNumber: 'QA101', origin: 'BOM', destination: 'KIX', departAt: '2026-10-12T08:00:00Z', arriveAt: '2026-10-12T17:00:00Z', durationMinutes: 540, stops: 0, journeyType: 'round_trip', total: { amount: 740, currency: 'USD' }, availabilityStatus: 'available', source: 'Controlled flights', sourceMode: 'test', isLive: false },
      { id: 'offer-2', carrier: 'Northstar', flightNumber: 'NS7', origin: 'BOM', destination: 'KIX', departAt: '2026-10-12T11:00:00Z', arriveAt: '2026-10-12T21:00:00Z', durationMinutes: 600, stops: 1, journeyType: 'round_trip', total: { amount: 690, currency: 'USD' }, availabilityStatus: 'available', source: 'Controlled flights', sourceMode: 'test', isLive: false },
    ],
  })
  const cart: TripCart = { tripId: 'trip-1', version: 3, state: 'ready', checkedAt: new Date().toISOString(), items: [{ id: 'flight-item', recommendationId: 'transport-1', ratePlanId: 'offer-2', kind: 'flight', title: 'Northstar NS7', status: 'quoted' }] }

  render(<TransportJourney tripId="trip-1" recommendation={recommendation} preferences={preferences} cart={cart} onCartChange={vi.fn()} onFlightAdded={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: /Flight times & fares/i }))
  await screen.findByText(/Northstar · NS7/i)

  expect(screen.getAllByRole('button', { name: /^Added$/i })).toHaveLength(1)
  expect(screen.getByRole('button', { name: /Replace saved flight/i })).toBeInTheDocument()
})
