import { render, screen } from '@testing-library/react'
import type { Recommendation, TripPreferences } from '../types'
import { TransportJourney } from './TransportJourney'

const preferences: TripPreferences = {
  destination: 'Kyoto', origin: 'Mumbai', start_date: '2026-10-12', end_date: '2026-10-18', budget_amount: 3200,
  currency: 'USD', vibes: ['culture'], group_type: 'couple', num_travelers: 2, cotravellers: [],
}

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
