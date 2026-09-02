import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { CompanionManager } from './CompanionManager'
import { api, ApiError } from '../services/api'
import type { CompanionLink, UserLookup } from '../types'

function Harness({ onBlocked }: { onBlocked: (blockers: string[]) => void }) {
  const [usernames, setUsernames] = useState<string[]>([])
  const [guests, setGuests] = useState<string[]>([])
  return <CompanionManager guests={guests} onGuestsChange={setGuests} usernames={usernames} onUsernamesChange={setUsernames} onBlockedChange={onBlocked} />
}

const sam = (link_status: UserLookup['link_status'], intake_complete = true): UserLookup => ({
  username: 'sam', name: 'Sam Iyer', intake_complete, link_status,
})
const pendingLink: CompanionLink = {
  link_id: 'link-1', username: 'sam', name: 'Sam Iyer', status: 'pending', created_at: '2026-09-01T10:00:00Z', responded_at: null,
}
const lastBlockers = (onBlocked: ReturnType<typeof vi.fn>) => onBlocked.mock.calls.at(-1)?.[0]

async function addSam() {
  await waitFor(() => expect(screen.queryByText(/checking saved companions/i)).not.toBeInTheDocument())
  fireEvent.change(screen.getByLabelText(/add by username/i), { target: { value: 'sam' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add' }))
}

beforeEach(() => vi.spyOn(api, 'profileOverview').mockResolvedValue({ sketch: null, cotravellers: [] }))
afterEach(() => vi.restoreAllMocks())

it('invites a linked member and keeps research blocked until they accept', async () => {
  const lookup = vi.spyOn(api, 'lookupUser').mockResolvedValue(sam('none'))
  const invite = vi.spyOn(api, 'inviteCompanion').mockResolvedValue(pendingLink)
  const onBlocked = vi.fn()
  render(<Harness onBlocked={onBlocked} />)
  await addSam()

  expect(await screen.findByText('@sam · not invited yet')).toBeInTheDocument()
  await waitFor(() => expect(lastBlockers(onBlocked)).toEqual(['@sam (needs an invitation)']))

  fireEvent.click(screen.getByRole('button', { name: 'Invite' }))
  expect(await screen.findByText('@sam · invitation pending')).toBeInTheDocument()
  expect(invite).toHaveBeenCalledWith('sam')
  await waitFor(() => expect(lastBlockers(onBlocked)).toEqual(['@sam (invitation pending)']))
  expect(screen.queryByRole('button', { name: 'Invite' })).not.toBeInTheDocument()

  lookup.mockResolvedValue(sam('accepted'))
  fireEvent.click(screen.getByRole('button', { name: /check again/i }))
  expect(await screen.findByText('@sam · Sam Iyer')).toBeInTheDocument()
  await waitFor(() => expect(lastBlockers(onBlocked)).toEqual([]))
  expect(screen.queryByRole('button', { name: /check again/i })).not.toBeInTheDocument()
})

it('offers to invite again after a decline', async () => {
  vi.spyOn(api, 'lookupUser').mockResolvedValue(sam('declined'))
  const invite = vi.spyOn(api, 'inviteCompanion').mockResolvedValue(pendingLink)
  const onBlocked = vi.fn()
  render(<Harness onBlocked={onBlocked} />)
  await addSam()

  expect(await screen.findByText('@sam · declined your invitation')).toBeInTheDocument()
  await waitFor(() => expect(lastBlockers(onBlocked)).toEqual(['@sam (declined your invitation)']))
  fireEvent.click(screen.getByRole('button', { name: /invite again/i }))
  expect(await screen.findByText('@sam · invitation pending')).toBeInTheDocument()
  expect(invite).toHaveBeenCalledWith('sam')
})

it('still blocks an accepted member whose own taste profile is unfinished', async () => {
  vi.spyOn(api, 'lookupUser').mockResolvedValue(sam('accepted', false))
  const invite = vi.spyOn(api, 'inviteCompanion')
  const onBlocked = vi.fn()
  render(<Harness onBlocked={onBlocked} />)
  await addSam()

  expect(await screen.findByText('@sam · hasn’t finished their taste profile')).toBeInTheDocument()
  await waitFor(() => expect(lastBlockers(onBlocked)).toEqual(['@sam (hasn’t finished their taste profile)']))
  expect(screen.queryByRole('button', { name: /invite/i })).not.toBeInTheDocument()
  expect(invite).not.toHaveBeenCalled()
})

it('reports a failed invitation without changing the member state', async () => {
  vi.spyOn(api, 'lookupUser').mockResolvedValue(sam('none'))
  vi.spyOn(api, 'inviteCompanion').mockRejectedValue(new ApiError('CheckIn could not reach the server. Check your connection and try again.', 0, 'NETWORK_ERROR', undefined, true))
  const onBlocked = vi.fn()
  render(<Harness onBlocked={onBlocked} />)
  await addSam()

  fireEvent.click(await screen.findByRole('button', { name: 'Invite' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/could not reach the server/i)
  expect(screen.getByText('@sam · not invited yet')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Invite' })).toBeInTheDocument()
  await waitFor(() => expect(lastBlockers(onBlocked)).toEqual(['@sam (needs an invitation)']))
})
