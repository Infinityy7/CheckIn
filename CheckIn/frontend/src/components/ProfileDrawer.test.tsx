import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ProfileDrawer } from './ProfileDrawer'
import { api, ApiError } from '../services/api'
import type { CharacterProfile } from '../types'

const profile: CharacterProfile = {
  id: 'character:test', version: 3, summary: 'A thoughtful food traveler who values culture and quiet discoveries.',
  weights: {
    schemaVersion: 1, vibeWeights: { food: .5, culture: .3, nature: .2 }, spontaneity: .6, chronotype: 'mid',
    splurgeCategory: 'food', saveCategory: 'transport', archetype: 'foodie_explorer', defaultParty: 'partner',
    foodAdventurousness: .8, dealBreakers: [], dietaryRequirements: [],
  }, rawAnswers: {}, createdAt: '2026-07-01T00:00:00Z', updatedAt: '2026-07-02T00:00:00Z',
}

afterEach(() => vi.restoreAllMocks())

it('edits structured ranking weights and hard boundaries without exposing JSON', async () => {
  vi.spyOn(api, 'profileOverview').mockResolvedValue({ sketch: null, cotravellers: [] })
  const update = vi.spyOn(api, 'updateProfile').mockResolvedValue(profile)
  render(<ProfileDrawer open profile={profile} onClose={vi.fn()} onUpdate={vi.fn()} onRetake={vi.fn()} />)
  await screen.findByText('No companions saved yet.')

  fireEvent.change(screen.getByRole('slider', { name: 'Food weight' }), { target: { value: '.7' } })
  fireEvent.click(screen.getByRole('button', { name: 'Theme Parks' }))
  fireEvent.click(screen.getByRole('button', { name: /Save profile/i }))

  await waitFor(() => expect(update).toHaveBeenCalled())
  expect(update.mock.calls[0][0]).toMatchObject({ expectedVersion: 3, weights: { vibeWeights: { food: .7 }, dealBreakers: ['theme_parks'] } })
  expect(screen.queryByText(/schemaVersion/)).not.toBeInTheDocument()
})

it('surfaces the version conflict message from a 409 save', async () => {
  vi.spyOn(api, 'profileOverview').mockResolvedValue({ sketch: null, cotravellers: [] })
  vi.spyOn(api, 'updateProfile').mockRejectedValue(new ApiError('This profile changed in another session. Close and reopen to edit the latest version.', 409, 'VERSION_CONFLICT'))
  render(<ProfileDrawer open profile={profile} onClose={vi.fn()} onUpdate={vi.fn()} onRetake={vi.fn()} />)
  await screen.findByText('No companions saved yet.')

  fireEvent.click(screen.getByRole('button', { name: /Save profile/i }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/changed in another session/)
})

it('lists saved travel companions from the profile overview', async () => {
  vi.spyOn(api, 'profileOverview').mockResolvedValue({ sketch: null, cotravellers: ['maya', 'sam_lee'] })
  render(<ProfileDrawer open profile={profile} onClose={vi.fn()} onUpdate={vi.fn()} onRetake={vi.fn()} />)

  expect(await screen.findByText('Maya')).toBeInTheDocument()
  expect(screen.getByText('Sam Lee')).toBeInTheDocument()
  expect(screen.getByText('Add or profile companions from the trip planner.')).toBeInTheDocument()
})

it('shows the companions empty state when none are saved', async () => {
  vi.spyOn(api, 'profileOverview').mockResolvedValue({ sketch: null, cotravellers: [] })
  render(<ProfileDrawer open profile={profile} onClose={vi.fn()} onUpdate={vi.fn()} onRetake={vi.fn()} />)

  expect(await screen.findByText('No companions saved yet.')).toBeInTheDocument()
})
