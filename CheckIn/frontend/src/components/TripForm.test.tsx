import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { TripForm } from './TripForm'
import { api, ApiError } from '../services/api'
import { TRIP_DRAFT_KEY } from '../services/tripDraft'

function mockOverview(cotravellers: string[]) {
  return vi.spyOn(api, 'profileOverview').mockResolvedValue({ sketch: null, cotravellers })
}

function mockLookup(users: Record<string, { name: string | null; intake_complete: boolean }>) {
  return vi.spyOn(api, 'lookupUser').mockImplementation((username) => {
    const found = users[username]
    if (!found) return Promise.reject(new ApiError('No such user.', 404, 'NOT_FOUND'))
    return Promise.resolve({ username, link_status: 'accepted' as const, ...found })
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

const planGroup = () => within(screen.getByRole('group', { name: /what should we plan/i }))
const SCOPE_LABELS = ['Flights & transport', 'Stays', 'Activities', 'Food']

beforeEach(() => localStorage.clear())
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
  // the blocker list lifts to the parent one commit after the status text renders
  await waitFor(() => expect(screen.getByRole('button', { name: /research my trip/i })).toBeDisabled())
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
  vi.spyOn(api, 'lookupUser').mockImplementation((username) => Promise.resolve({ username, name: null, intake_complete: true, link_status: 'accepted' as const }))
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

const feasibilityReport = {
  verdict: 'unrealistic' as const,
  confidence: 0.9,
  reason: 'Flights alone would exceed this budget.',
  suggestion_text: 'Try at least 1800 USD for this route.',
  suggested_changes: { budget_amount: 1800, end_date: null, destination: null },
}

it('shows the feasibility warning with the suggested change', async () => {
  mockOverview([])
  render(<TripForm onSubmit={vi.fn()} busy={false} feasibility={feasibilityReport} onProceedAnyway={vi.fn()} onDismissWarning={vi.fn()} />)

  expect(screen.getByText(/won’t fit its budget/i)).toBeInTheDocument()
  expect(screen.getByText(/Flights alone would exceed this budget.*Try at least 1800 USD/)).toBeInTheDocument()
  await settled()
})

it('applies the suggested change to the form and dismisses the warning', async () => {
  mockOverview([])
  const onDismiss = vi.fn()
  render(<TripForm onSubmit={vi.fn()} busy={false} feasibility={feasibilityReport} onProceedAnyway={vi.fn()} onDismissWarning={onDismiss} />)

  fireEvent.click(screen.getByRole('button', { name: /apply suggestion/i }))

  expect(screen.getByLabelText(/amount/i)).toHaveValue(1800)
  expect(onDismiss).toHaveBeenCalledTimes(1)
  await settled()
})

it('lets the traveler research anyway with the values currently on the form', async () => {
  mockOverview([])
  const onProceed = vi.fn()
  render(<TripForm onSubmit={vi.fn()} busy={false} feasibility={feasibilityReport} onProceedAnyway={onProceed} onDismissWarning={vi.fn()} />)

  fillRoute()
  fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: '200' } })
  fireEvent.click(screen.getByRole('button', { name: /research anyway/i }))
  expect(onProceed).toHaveBeenCalledTimes(1)
  expect(onProceed).toHaveBeenCalledWith(expect.objectContaining({ destination: 'Kyoto, Japan', origin: 'Mumbai, India', budget_amount: 200, cotravellers: [] }))
  await settled()
})

it('shows an in-place status and disables every submit path while creation is pending', async () => {
  mockOverview([])
  render(<TripForm onSubmit={vi.fn()} busy feasibility={feasibilityReport} onProceedAnyway={vi.fn()} onDismissWarning={vi.fn()} />)

  expect(screen.getByRole('button', { name: /opening a workspace/i })).toBeDisabled()
  expect(screen.getByRole('button', { name: /research anyway/i })).toBeDisabled()
  expect(screen.getByText(/your details stay right here/i)).toBeInTheDocument()
  await settled()
})

it('persists an unfinished draft under the versioned key and restores it on remount', async () => {
  mockOverview([])
  const first = render(<TripForm onSubmit={vi.fn()} busy={false} />)
  await settled()
  expect(localStorage.getItem(TRIP_DRAFT_KEY)).toBeNull()

  fillRoute()
  fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: '200' } })
  openGuestSection()
  addGuest('Maya')
  expect(await screen.findByText('Guest · needs taste intake')).toBeInTheDocument()
  await waitFor(() => expect(localStorage.getItem(TRIP_DRAFT_KEY)).toContain('Kyoto, Japan'))
  expect(JSON.parse(localStorage.getItem(TRIP_DRAFT_KEY)!)).toMatchObject({ form: { origin: 'Mumbai, India', budget_amount: 200 }, guests: ['Maya'] })
  first.unmount()

  render(<TripForm onSubmit={vi.fn()} busy={false} />)
  expect(screen.getByLabelText(/destination/i)).toHaveValue('Kyoto, Japan')
  expect(screen.getByLabelText(/starting from/i)).toHaveValue('Mumbai, India')
  expect(screen.getByLabelText(/amount/i)).toHaveValue(200)
  expect(screen.getByText('Maya')).toBeInTheDocument()
  await settled()
})

it('ignores a corrupt draft and past dates from an old draft', async () => {
  mockOverview([])
  localStorage.setItem(TRIP_DRAFT_KEY, 'not json')
  const first = render(<TripForm onSubmit={vi.fn()} busy={false} />)
  expect(screen.getByLabelText(/destination/i)).toHaveValue('')
  await settled()
  first.unmount()

  localStorage.setItem(TRIP_DRAFT_KEY, JSON.stringify({ form: { destination: 'Lisbon', start_date: '2020-01-01', end_date: '2020-01-05' }, guests: 'nope' }))
  render(<TripForm onSubmit={vi.fn()} busy={false} />)
  expect(screen.getByLabelText(/destination/i)).toHaveValue('Lisbon')
  expect((screen.getByLabelText('Depart') as HTMLInputElement).value >= new Date().toISOString().slice(0, 10)).toBe(true)
  await settled()
})

it('renders no feasibility warning by default', async () => {
  mockOverview([])
  render(<TripForm onSubmit={vi.fn()} busy={false} />)
  expect(screen.queryByText(/won’t fit its budget/i)).not.toBeInTheDocument()
  await settled()
})

it('plans the full trip by default and submits every scope in canonical order', async () => {
  mockOverview([])
  const onSubmit = vi.fn()
  render(<TripForm onSubmit={onSubmit} busy={false} />)
  await settled()

  const plan = planGroup()
  expect(plan.getByRole('button', { name: 'Full trip' })).toHaveAttribute('aria-pressed', 'true')
  for (const label of SCOPE_LABELS) expect(plan.getByRole('button', { name: label })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.queryByText(/ only$/)).not.toBeInTheDocument()

  fillRoute()
  fireEvent.click(screen.getByRole('button', { name: /research my trip/i }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  expect(onSubmit.mock.calls[0][0].scope).toEqual(['transport', 'hotel', 'activity', 'restaurant'])
})

it('submits only the parts left selected and summarises them in Tavi’s read', async () => {
  mockOverview([])
  const onSubmit = vi.fn()
  render(<TripForm onSubmit={onSubmit} busy={false} />)
  await settled()

  const plan = planGroup()
  fireEvent.click(plan.getByRole('button', { name: 'Food' }))
  fireEvent.click(plan.getByRole('button', { name: 'Activities' }))
  expect(plan.getByRole('button', { name: 'Full trip' })).toHaveAttribute('aria-pressed', 'false')
  expect(plan.getByRole('button', { name: 'Food' })).toHaveAttribute('aria-pressed', 'false')
  expect(screen.getByText(/flights & stays only/)).toBeInTheDocument()

  fillRoute()
  fireEvent.click(screen.getByRole('button', { name: /research my trip/i }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  expect(onSubmit.mock.calls[0][0].scope).toEqual(['transport', 'hotel'])
})

it('blocks research until at least one part is picked, keeps canonical order, and Full trip restores all four', async () => {
  mockOverview([])
  const onSubmit = vi.fn()
  render(<TripForm onSubmit={onSubmit} busy={false} />)
  await settled()
  fillRoute()

  const plan = planGroup()
  for (const label of SCOPE_LABELS) fireEvent.click(plan.getByRole('button', { name: label }))
  expect(screen.getByRole('button', { name: /research my trip/i })).toBeDisabled()
  expect(screen.getByText('Pick at least one thing to plan.')).toBeInTheDocument()

  fireEvent.click(plan.getByRole('button', { name: 'Food' }))
  fireEvent.click(plan.getByRole('button', { name: 'Flights & transport' }))
  expect(screen.getByRole('button', { name: /research my trip/i })).toBeEnabled()
  expect(screen.queryByText('Pick at least one thing to plan.')).not.toBeInTheDocument()
  expect(screen.getByText(/flights & food only/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /research my trip/i }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
  expect(onSubmit.mock.calls[0][0].scope).toEqual(['transport', 'restaurant'])

  fireEvent.click(plan.getByRole('button', { name: 'Full trip' }))
  expect(plan.getByRole('button', { name: 'Full trip' })).toHaveAttribute('aria-pressed', 'true')
  for (const label of SCOPE_LABELS) expect(plan.getByRole('button', { name: label })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: /research my trip/i }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2))
  expect(onSubmit.mock.calls[1][0].scope).toEqual(['transport', 'hotel', 'activity', 'restaurant'])
})

it('restores a saved scope from the draft and fills a missing one with the full trip', async () => {
  mockOverview([])
  localStorage.setItem(TRIP_DRAFT_KEY, JSON.stringify({ form: { destination: 'Lisbon' }, guests: [] }))
  const first = render(<TripForm onSubmit={vi.fn()} busy={false} />)
  expect(planGroup().getByRole('button', { name: 'Full trip' })).toHaveAttribute('aria-pressed', 'true')
  await settled()
  first.unmount()

  localStorage.setItem(TRIP_DRAFT_KEY, JSON.stringify({ form: { destination: 'Lisbon', scope: ['restaurant', 'hotel', 'bogus'] }, guests: [] }))
  render(<TripForm onSubmit={vi.fn()} busy={false} />)
  const plan = planGroup()
  expect(plan.getByRole('button', { name: 'Full trip' })).toHaveAttribute('aria-pressed', 'false')
  expect(plan.getByRole('button', { name: 'Stays' })).toHaveAttribute('aria-pressed', 'true')
  expect(plan.getByRole('button', { name: 'Food' })).toHaveAttribute('aria-pressed', 'true')
  expect(plan.getByRole('button', { name: 'Flights & transport' })).toHaveAttribute('aria-pressed', 'false')
  expect(screen.getByText(/stays & food only/)).toBeInTheDocument()
  await settled()
})
