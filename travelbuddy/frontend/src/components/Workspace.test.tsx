import { fireEvent, render, screen } from '@testing-library/react'
import { Workspace, type AgentStatus } from './Workspace'
import type { Recommendation, TripPreferences } from '../types'

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

it('keeps successful results visible and offers a retry for failed research', () => {
  const agents: AgentStatus = {
    'Accommodation Agent': 'complete',
    'Activities Agent': 'failed',
    'Restaurant Agent': 'complete',
    'Transport Agent': 'complete',
  }
  const retry = vi.fn()

  render(<Workspace
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

  expect(screen.getByText('Partial results ready')).toBeInTheDocument()
  expect(screen.getByText(/1 research category needs another try/)).toBeInTheDocument()
  expect(screen.getByText('A quiet Kyoto stay')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Retry research/i }))
  expect(retry).toHaveBeenCalledOnce()
})
