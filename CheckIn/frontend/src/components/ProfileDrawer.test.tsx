import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ProfileDrawer } from './ProfileDrawer'
import { api, ApiError } from '../services/api'
import type { CharacterProfile, CompanionLink } from '../types'

const profile: CharacterProfile = {
  id: 'character:test', version: 3, summary: 'A thoughtful food traveler who values culture and quiet discoveries.',
  weights: {
    schemaVersion: 1, vibeWeights: { food: .5, culture: .3, nature: .2 }, spontaneity: .6, chronotype: 'mid',
    splurgeCategory: 'food', saveCategory: 'transport', archetype: 'foodie_explorer', defaultParty: 'partner',
    foodAdventurousness: .8, dealBreakers: [], dietaryRequirements: [],
  }, rawAnswers: {}, createdAt: '2026-07-01T00:00:00Z', updatedAt: '2026-07-02T00:00:00Z',
}

const noLinks = { incoming: [], outgoing: [] }
const invitation: CompanionLink = {
  link_id: 'link-1', username: 'ora', name: 'Ora Ganizer', status: 'pending', created_at: '2026-09-01T10:00:00Z', responded_at: null,
}
const sharedByMember: CompanionLink = {
  link_id: 'link-2', username: 'sam', name: null, status: 'accepted', created_at: '2026-08-30T10:00:00Z', responded_at: '2026-08-31T10:00:00Z',
}

beforeEach(() => vi.spyOn(api, 'companionLinks').mockResolvedValue(noLinks))
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
  expect(await screen.findByText(/No linked companions yet/)).toBeInTheDocument()
})

it('accepts an incoming travel invitation from the companions section', async () => {
  vi.spyOn(api, 'profileOverview').mockResolvedValue({ sketch: null, cotravellers: [] })
  vi.spyOn(api, 'companionLinks').mockResolvedValue({ incoming: [invitation], outgoing: [] })
  const respond = vi.spyOn(api, 'respondCompanionLink').mockResolvedValue({ ...invitation, status: 'accepted', responded_at: '2026-09-02T09:00:00Z' })
  render(<ProfileDrawer open profile={profile} onClose={vi.fn()} onUpdate={vi.fn()} onRetake={vi.fn()} />)

  expect(await screen.findByText('@ora')).toBeInTheDocument()
  expect(screen.getByText(/Ora Ganizer · wants to plan trips with your taste profile/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /accept/i }))

  await waitFor(() => expect(respond).toHaveBeenCalledWith('link-1', 'accept'))
  expect(await screen.findByText('Can plan with your profile')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /accept/i })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Stop sharing your profile with @ora' })).toBeInTheDocument()
})

it('declines an invitation and lists companions who share their profile with you', async () => {
  vi.spyOn(api, 'profileOverview').mockResolvedValue({ sketch: null, cotravellers: [] })
  vi.spyOn(api, 'companionLinks').mockResolvedValue({ incoming: [invitation], outgoing: [sharedByMember] })
  const respond = vi.spyOn(api, 'respondCompanionLink').mockResolvedValue({ ...invitation, status: 'declined', responded_at: '2026-09-02T09:00:00Z' })
  render(<ProfileDrawer open profile={profile} onClose={vi.fn()} onUpdate={vi.fn()} onRetake={vi.fn()} />)

  expect(await screen.findByText('Share their profile with you')).toBeInTheDocument()
  expect(screen.getByText('@sam')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /decline/i }))

  await waitFor(() => expect(respond).toHaveBeenCalledWith('link-1', 'decline'))
  await waitFor(() => expect(screen.queryByText('@ora')).not.toBeInTheDocument())
  expect(screen.getByText('@sam')).toBeInTheDocument()
  expect(screen.queryByText(/No linked companions yet/)).not.toBeInTheDocument()
})

it('reports a failed invitation response and keeps the invitation actionable', async () => {
  vi.spyOn(api, 'profileOverview').mockResolvedValue({ sketch: null, cotravellers: [] })
  vi.spyOn(api, 'companionLinks').mockResolvedValue({ incoming: [invitation], outgoing: [] })
  vi.spyOn(api, 'respondCompanionLink').mockRejectedValue(new ApiError('Only the invited traveler can respond to this invitation', 403, 'FORBIDDEN'))
  render(<ProfileDrawer open profile={profile} onClose={vi.fn()} onUpdate={vi.fn()} onRetake={vi.fn()} />)

  fireEvent.click(await screen.findByRole('button', { name: /accept/i }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/Only the invited traveler/)
  expect(screen.getByRole('button', { name: /accept/i })).toBeInTheDocument()
})
