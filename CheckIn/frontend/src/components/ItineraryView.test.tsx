import { fireEvent, render, screen } from '@testing-library/react'
import { ItineraryView } from './ItineraryView'
import type { Itinerary, ItineraryItem, TripPreferences } from '../types'

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

const item = (title: string, timeSlot: string): ItineraryItem => ({
  time_slot: timeSlot,
  title,
  description: 'A quiet stop with time to linger.',
  category: 'activity',
  cost_estimate: '$20',
  location: 'Gion',
  tip: 'Arrive before nine.',
})

const itinerary: Itinerary = {
  trip_title: 'Kyoto, gently',
  trip_summary: 'Si slow days between temples, markets, and tea.',
  days: [
    { day_number: 1, date: '2026-10-12', theme: 'Arrival and Gion at dusk', items: [item('Check in at the ryokan', '15:00'), item('Evening walk in Gion', '18:00')] },
    { day_number: 2, date: '2026-10-13', theme: 'Temples before the crowds', items: [item('Kiyomizu-dera at open', '08:00'), item('Lunch at Nishiki Market', '12:30')] },
  ],
}

it('renders the travel document with both days and returns to the shortlist', () => {
  const back = vi.fn()
  render(<ItineraryView itinerary={itinerary} preferences={preferences} onBack={back} onRate={vi.fn().mockResolvedValue(undefined)} />)
  expect(screen.getByRole('heading', { name: 'Kyoto, gently' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Arrival and Gion at dusk' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Temples before the crowds' })).toBeInTheDocument()
  expect(screen.getByText('2 days')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /Back to shortlist/ }))
  expect(back).toHaveBeenCalledTimes(1)
})

it('reorders a stop within its day with the move controls', () => {
  const { container } = render(<ItineraryView itinerary={itinerary} preferences={preferences} onBack={vi.fn()} onRate={vi.fn().mockResolvedValue(undefined)} />)
  const titles = () => [...container.querySelectorAll('#day-1 .timeline-item h3')].map((node) => node.textContent)
  expect(titles()).toEqual(['Check in at the ryokan', 'Evening walk in Gion'])
  fireEvent.click(screen.getAllByRole('button', { name: 'Move later' })[0])
  expect(titles()).toEqual(['Evening walk in Gion', 'Check in at the ryokan'])
})
