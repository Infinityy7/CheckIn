import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { TripForm } from './TripForm'
import { api, ApiError } from '../services/api'

function mockOverview(cotravellers: string[]) {
  return vi.spyOn(api, 'profileOverview').mockResolvedValue({ sketch: null, cotravellers })
}

function mockLookup(users: Record<string, { name: string | null; intake_complete: boolean }>) {
  return vi.spyOn(api, 'lookupUser').mockImplementation((username) => {
    const found = users[username]
    if (!found) return Promise.reject(new ApiError('No such user.', 404, 'NOT_FOUND'))
    return Promise.resolve({ username, ...found })
  })
}

async function settled() {
  await waitFor(() => expect(screen.queryByText(/checking saved companions/i)).not.toBeInTheDocument())
}

function addUsername(username: string) {
  fireEvent.change(screen.getByLabelText(/add by username/i), { target: { value: username } })
  fireEvent.click(screen.getByRole('button', { name: 'Add' }))
}

function openGuestSection() {
  fireEvent.click(screen.getByRole('button', { name: /guest companion/i }))
}

function addGuest(name: string) {
  fireEvent.change(screen.getByLabelText(/guest name/i), { target: { value: name } })
  fireEvent.click(screen.getByRole('button', { name: 'Add guest' }))
}

function fillRoute() {
  fireEvent.change(screen.getByLabelText(/destination/i), { target: { value: 'Kyoto, Japan' } })
  fireEvent.change(screen.getByLabelText(/starting from/i), { target: { value: 'Mumbai, India' } })
}

afterEach(() => vi.restoreAllMocks())

it('hides the companion manager for solo trips and submits empty companion lists', async () => {
  mockOverview([])
  const onSubmit = vi.fn()
  render(<TripForm onSubmit={onSubmit} busy={false} />)

  expect(await screen.findByLabelText(/add by username/i)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'solo' }))
  expect(screen.queryByLabelText(/add by username/i)).not.toBeInTheDocument()
  expect(screen.getByLabelText('Travelers')).toHaveValue(1)

  fillRoute()
  fireEvent.click(screen.getByRole('button', { name: /research my trip/i }))

  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    group_type: 'solo', num_travelers: 1, cotravellers: [], cotraveller_usernames: [],
  }))
})

it('adds a linked member by username and includes them in the payload', async () => {
  mockOverview([])
  mockLookup({ sam: { name: 'Sam Iyer', intake_complete: true } })
  const onSubmit = vi.fn()
  render(<TripForm onSubmit={onSubmit} busy={false} />)
  await settled()

  addUsername('sam')
  expect(await screen.findByText('@sam · Sam Iyer')).toBeInTheDocument()
  const submit = screen.getByRole('button', { name: /research my trip/i })
  expect(submit).toBeEnabled()
  expect(screen.queryByText(/waiting on taste profiles/i)).not.toBeInTheDocument()

  fillRoute()
  fireEvent.click(submit)
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    cotravellers: [], cotraveller_usernames: ['sam'], num_travelers: 2,
  }))
})

it('blocks research while a linked member has not finished their taste profile', async () => {
  mockOverview([])
  mockLookup({ kai: { name: null, intake_complete: false } })
  render(<TripForm onSubmit={vi.fn()} busy={false} />)
  await settled()

  addUsername('kai')
  expect(await screen.findByText(/@kai · hasn.t finished their taste profile/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /research my trip/i })).toBeDisabled()
  expect(screen.getByText(/waiting on taste profiles/i)).toHaveTextContent('@kai')
})

it('shows an inline error when no CheckIn user has that username', async () => {
  mockOverview([])
  mockLookup({})
  render(<TripForm onSubmit={vi.fn()} busy={false} />)
  await settled()

  addUsername('ghost')
  expect(await screen.findByText('No CheckIn user named @ghost')).toBeInTheDocument()
  expect(screen.queryByText('@ghost · verifying…')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /research my trip/i })).toBeEnabled()
})

it('keeps the guest companion flow: intake gating with Profile now', async () => {
  mockOverview(['priya'])
  render(<TripForm onSubmit={vi.fn()} busy={false} />)
  await settled()

  openGuestSection()
  addGuest('Priya!')
  expect(await screen.findByText('Guest · profiled')).toBeInTheDocument()
  const submit = screen.getByRole('button', { name: /research my trip/i })
  expect(submit).toBeEnabled()
  expect(screen.queryByText(/waiting on taste profiles/i)).not.toBeInTheDocument()

  addGuest('Maya')
  expect(await screen.findByText('Guest · needs taste intake')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Profile now' })).toBeInTheDocument()
  expect(submit).toBeDisabled()
  expect(screen.getByText(/waiting on taste profiles/i)).toHaveTextContent('Maya')
})

it('offers the full backend vibe list including wellness', async () => {
  mockOverview([])
  render(<TripForm onSubmit={vi.fn()} busy={false} />)
  for (const vibe of ['adventure', 'culture', 'food', 'nightlife', 'relaxation', 'nature', 'shopping', 'history', 'romance', 'wellness', 'family-friendly']) {
    expect(screen.getByRole('button', { name: vibe })).toBeInTheDocument()
  }
  await settled()
})

it('auto-bumps num_travelers across linked members and guests without lowering it', async () => {
  mockOverview([])
  mockLookup({ ana: { name: null, intake_complete: true }, ben: { name: null, intake_complete: true } })
  render(<TripForm onSubmit={vi.fn()} busy={false} />)
  await settled()
  const travelers = screen.getByLabelText('Travelers')
  expect(travelers).toHaveValue(2)

  fireEvent.change(travelers, { target: { value: '4' } })
  expect(travelers).toHaveValue(4)

  addUsername('ana')
  expect(await screen.findByText('@ana')).toBeInTheDocument()
  expect(travelers).toHaveValue(4)

  fireEvent.change(travelers, { target: { value: '2' } })
  addUsername('ben')
  expect(await screen.findByText('@ben')).toBeInTheDocument()
  openGuestSection()
  addGuest('Chio')
  expect(travelers).toHaveValue(4)
})

it('submits both companion lists and labels the two party kinds', async () => {
  mockOverview(['ana'])
  mockLookup({ sam: { name: 'Sam', intake_complete: true }, rio: { name: null, intake_complete: true } })
  const onSubmit = vi.fn()
  render(<TripForm onSubmit={onSubmit} busy={false} />)
  await settled()

  addUsername('sam')
  expect(await screen.findByText('@sam · Sam')).toBeInTheDocument()
  addUsername('rio')
  expect(await screen.findByText('@rio')).toBeInTheDocument()
  openGuestSection()
  addGuest('Ana')
  expect(await screen.findByText('Guest · profiled')).toBeInTheDocument()

  expect(screen.getAllByText(/2 linked members · 1 guest/).length).toBeGreaterThan(0)
  expect(screen.getByLabelText('Travelers')).toHaveValue(4)

  fillRoute()
  const submit = screen.getByRole('button', { name: /research my trip/i })
  expect(submit).toBeEnabled()
  fireEvent.click(submit)
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    cotravellers: ['Ana'], cotraveller_usernames: ['sam', 'rio'], num_travelers: 4,
  }))
})

it('caps linked members plus guests at 8 companions combined', async () => {
  mockOverview([])
  vi.spyOn(api, 'lookupUser').mockImplementation((username) => Promise.resolve({ username, name: null, intake_complete: true }))
  render(<TripForm onSubmit={vi.fn()} busy={false} />)
  await settled()

  for (let index = 1; index <= 7; index += 1) {
    addUsername(`u${index}`)
    expect(await screen.findByText(`@u${index}`)).toBeInTheDocument()
  }
  openGuestSection()
  addGuest('Ana')
  expect(await screen.findByText('Guest · needs taste intake')).toBeInTheDocument()

  expect(screen.getByText(/up to 8 co-travellers/i)).toBeInTheDocument()
  expect(screen.getByLabelText(/add by username/i)).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
  expect(screen.getByLabelText(/guest name/i)).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Add guest' })).toBeDisabled()
})
