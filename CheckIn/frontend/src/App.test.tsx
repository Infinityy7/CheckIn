import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'
import { api, ApiError } from './services/api'
import { TRIP_DRAFT_KEY } from './services/tripDraft'
import type { AgentHealth, CharacterProfile, FeasibilityReport, Itinerary, Recommendation, StreamEvent, TripCreateResult, TripPreferences, TripState, User } from './types'

const user: User = { email: 'vedant@example.com', username: 'vedant', name: 'Vedant', phone: null, intake_complete: true, cotravellers: [] }
const profile: CharacterProfile = { id: 'character:test', version: 1, summary: 'Curious, food-led traveler.', rawAnswers: {}, createdAt: '2026-07-14T00:00:00Z', updatedAt: '2026-07-14T00:00:00Z' }
const health: AgentHealth = { status: 'ok', account: { status: 'ready', code: null }, gateway: { enabled: false, mode: 'direct' }, research_cache: null, queue_timeouts: 0, routes: {} }
const preferences: TripPreferences = {
  destination: 'Kyoto, Japan', origin: 'Mumbai, India', start_date: '2026-10-12', end_date: '2026-10-18', budget_amount: 3200,
  currency: 'USD', vibes: ['culture'], group_type: 'couple', num_travelers: 2, cotravellers: [], cotraveller_usernames: [],
}
const hotel: Recommendation = {
  id: 'hotel-1', name: 'A quiet Kyoto stay', category: 'hotel', description: 'A dependable local stay.', reasoning: 'Matches a balanced pace.',
  estimated_cost: '$180', cost_min: 150, cost_max: 200, rating: 4.7, review_count: 800, location: 'Gion', image_search_query: 'Kyoto hotel',
  metadata: {}, rank: 1, score: 0.9, score_breakdown: {},
}
const agentNames = ['Accommodation Agent', 'Activities Agent', 'Restaurant Agent', 'Transport Agent']
const allFailed = agentNames.map((name) => `${name} could not finish this search. You can retry safely.`)
const unrealistic: FeasibilityReport = {
  verdict: 'unrealistic', confidence: 0.9, reason: 'Flights alone would exceed this budget.',
  suggestion_text: 'Try at least 1800 USD for this route.', suggested_changes: { budget_amount: 1800, end_date: null, destination: null },
}
const received: TripCreateResult = { trip_id: 'trip-new', status: 'received', replayed: false }
const held: TripCreateResult = { trip_id: null, status: 'held', replayed: false, feasibility: unrealistic }

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function streamOf(events: StreamEvent[]) {
  return async (_id: string, onEvent: (event: StreamEvent) => void) => { for (const event of events) onEvent(event) }
}

function mockSession(tripState?: TripState) {
  localStorage.setItem('travelbuddy.session', 'test-token')
  vi.spyOn(api, 'me').mockResolvedValue(user)
  vi.spyOn(api, 'profile').mockResolvedValue(profile)
  vi.spyOn(api, 'pendingCheckIn').mockResolvedValue({ trip: null })
  vi.spyOn(api, 'agentHealth').mockResolvedValue(health)
  vi.spyOn(api, 'profileOverview').mockResolvedValue({ sketch: profile.summary, cotravellers: ['priya'] })
  vi.spyOn(api, 'cart').mockResolvedValue({ tripId: tripState?.trip_id ?? 'trip-new', state: 'open', items: [], checkedAt: '2026-09-01T00:00:00Z' })
  if (tripState) {
    localStorage.setItem('travelbuddy.lastTrip', tripState.trip_id)
    vi.spyOn(api, 'trip').mockResolvedValue(tripState)
  }
}

async function openPlanner() {
  render(<App />)
  expect(await screen.findByRole('heading', { name: /point the compass/i })).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByText(/checking saved companions/i)).not.toBeInTheDocument())
}

const submitButton = () => screen.getByRole('button', { name: /research my trip/i })

function fillTokyoTrip() {
  fireEvent.change(screen.getByLabelText(/destination/i), { target: { value: 'Tokyo, Japan' } })
  fireEvent.change(screen.getByLabelText(/starting from/i), { target: { value: 'Delhi, India' } })
  fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: '200' } })
}

function expectTokyoValues() {
  expect(screen.getByLabelText(/destination/i)).toHaveValue('Tokyo, Japan')
  expect(screen.getByLabelText(/starting from/i)).toHaveValue('Delhi, India')
  expect(screen.getByLabelText(/amount/i)).toHaveValue(200)
}

async function addGuest(name: string) {
  fireEvent.click(screen.getByRole('button', { name: /guest companion/i }))
  fireEvent.change(screen.getByLabelText(/guest name/i), { target: { value: name } })
  fireEvent.click(screen.getByRole('button', { name: 'Add guest' }))
  expect(await screen.findByText('Guest · profiled')).toBeInTheDocument()
}

beforeEach(() => { document.documentElement.dataset.theme = 'light' })
afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

describe('trip creation keeps the form and its values', () => {
  it('stays mounted with every value while creation is pending, then shows the feasibility hold beside them', async () => {
    mockSession()
    const pending = deferred<TripCreateResult>()
    const createTrip = vi.spyOn(api, 'createTrip').mockReturnValueOnce(pending.promise)
    await openPlanner()
    fillTokyoTrip()
    await addGuest('Priya')

    fireEvent.click(submitButton())
    await waitFor(() => expect(createTrip).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: /opening a workspace/i })).toBeDisabled()
    expect(screen.getByText(/your details stay right here/i)).toBeInTheDocument()
    expect(screen.queryByText('Mapping the possibilities')).not.toBeInTheDocument()
    expectTokyoValues()
    expect(screen.getByText('Priya')).toBeInTheDocument()

    const [submitted, options] = createTrip.mock.calls[0]
    expect(submitted).toMatchObject({ destination: 'Tokyo, Japan', origin: 'Delhi, India', budget_amount: 200, cotravellers: ['Priya'], cotraveller_usernames: [] })
    expect(options).toEqual({ idempotencyKey: expect.stringMatching(/^[A-Za-z0-9._:-]{8,128}$/), acknowledgeFeasibility: false })

    await act(async () => pending.resolve(held))
    expect(await screen.findByText(/won’t fit its budget/i)).toBeInTheDocument()
    expect(screen.getByText(/Flights alone would exceed this budget.*Try at least 1800 USD/)).toBeInTheDocument()
    expectTokyoValues()
    expect(screen.getByText('Priya')).toBeInTheDocument()
    expect(submitButton()).toBeEnabled()
    expect(screen.queryByRole('heading', { name: 'The shortlist' })).not.toBeInTheDocument()
    expect(localStorage.getItem('travelbuddy.lastTrip')).toBeNull()
    expect(localStorage.getItem(TRIP_DRAFT_KEY)).toContain('Tokyo, Japan')

    createTrip.mockResolvedValueOnce(held)
    fireEvent.click(screen.getByRole('button', { name: /research anyway/i }))
    await waitFor(() => expect(createTrip).toHaveBeenCalledTimes(2))
    const [again, againOptions] = createTrip.mock.calls[1]
    expect(again).toEqual(submitted)
    expect(againOptions).toEqual({ idempotencyKey: options.idempotencyKey, acknowledgeFeasibility: true })
    expect(await screen.findByText(/won’t fit its budget/i)).toBeInTheDocument()
    expectTokyoValues()
  })

  it.each([
    ['a 422 validation error', new ApiError('Trips must start today or later.', 422, 'VALIDATION_ERROR')],
    ['a network failure', new ApiError('CheckIn could not reach the server. Check your connection and try again.', 0, 'NETWORK_ERROR', undefined, true)],
    ['a client timeout', new ApiError('The server took too long to respond. It is safe to try again.', 0, 'REQUEST_TIMEOUT', undefined, true)],
  ])('preserves every field and companion after %s and replays the same idempotency key on retry', async (_label, failure) => {
    mockSession()
    vi.spyOn(api, 'lookupUser').mockResolvedValue({ username: 'sam', name: 'Sam Iyer', intake_complete: true, link_status: 'accepted' })
    const createTrip = vi.spyOn(api, 'createTrip').mockRejectedValueOnce(failure)
    await openPlanner()
    fillTokyoTrip()
    fireEvent.change(screen.getByLabelText(/add by username/i), { target: { value: 'sam' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByText('@sam · Sam Iyer')).toBeInTheDocument()
    await addGuest('Priya')

    fireEvent.click(submitButton())
    expect(await screen.findByRole('alert')).toHaveTextContent(failure.message)
    expectTokyoValues()
    expect(screen.getByText('@sam · Sam Iyer')).toBeInTheDocument()
    expect(screen.getByText('Priya')).toBeInTheDocument()
    expect(screen.getByLabelText('Travelers')).toHaveValue(3)
    expect(submitButton()).toBeEnabled()
    expect(screen.queryByText('Mapping the possibilities')).not.toBeInTheDocument()

    createTrip.mockResolvedValueOnce(held)
    fireEvent.click(submitButton())
    await waitFor(() => expect(createTrip).toHaveBeenCalledTimes(2))
    expect(createTrip.mock.calls[1][0]).toEqual(createTrip.mock.calls[0][0])
    expect(createTrip.mock.calls[1][1]).toEqual({ idempotencyKey: createTrip.mock.calls[0][1].idempotencyKey, acknowledgeFeasibility: false })
  })

  it('opens the workspace after creation, clears the draft, and mints a new key when the values change', async () => {
    mockSession()
    const createTrip = vi.spyOn(api, 'createTrip')
      .mockRejectedValueOnce(new ApiError('Research is busy.', 503, 'SERVICE_UNAVAILABLE', undefined, true))
      .mockResolvedValueOnce(received)
    vi.spyOn(api, 'research').mockImplementation(streamOf([
      { event: 'agent_started', agent: 'Accommodation Agent' },
      { event: 'agent_completed', agent: 'Accommodation Agent', results: [hotel] },
      { event: 'all_complete', completed: 1, failed: 3, status: 'partial' },
    ]))
    vi.spyOn(api, 'trip').mockResolvedValue({ trip_id: 'trip-new', preferences: { ...preferences, destination: 'Tokyo, Japan' }, research_results: [{ agent_name: 'Accommodation Agent', recommendations: [hotel] }], selections: [] })
    await openPlanner()
    fillTokyoTrip()
    await waitFor(() => expect(localStorage.getItem(TRIP_DRAFT_KEY)).toContain('Tokyo, Japan'))

    fireEvent.click(submitButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('Research is busy.')
    expectTokyoValues()

    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: '2500' } })
    fireEvent.click(submitButton())
    expect(await screen.findByRole('heading', { name: 'The shortlist' })).toBeInTheDocument()
    expect(await screen.findByText('A quiet Kyoto stay')).toBeInTheDocument()
    expect(createTrip).toHaveBeenCalledTimes(2)
    expect(createTrip.mock.calls[1][0].budget_amount).toBe(2500)
    expect(createTrip.mock.calls[1][1].idempotencyKey).not.toBe(createTrip.mock.calls[0][1].idempotencyKey)
    expect(localStorage.getItem(TRIP_DRAFT_KEY)).toBeNull()
    expect(localStorage.getItem('travelbuddy.lastTrip')).toBe('trip-new')
  })

  it('applying the suggestion edits the form in place and submits the revised values with a fresh key', async () => {
    mockSession()
    const createTrip = vi.spyOn(api, 'createTrip').mockResolvedValueOnce(held).mockResolvedValueOnce(held)
    await openPlanner()
    fillTokyoTrip()
    fireEvent.click(submitButton())
    expect(await screen.findByText(/won’t fit its budget/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /apply suggestion/i }))
    expect(screen.queryByText(/won’t fit its budget/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText(/amount/i)).toHaveValue(1800)
    expect(screen.getByLabelText(/destination/i)).toHaveValue('Tokyo, Japan')

    fireEvent.click(submitButton())
    await waitFor(() => expect(createTrip).toHaveBeenCalledTimes(2))
    expect(createTrip.mock.calls[1][0]).toMatchObject({ destination: 'Tokyo, Japan', budget_amount: 1800 })
    expect(createTrip.mock.calls[1][1].idempotencyKey).not.toBe(createTrip.mock.calls[0][1].idempotencyKey)
  })

  it('starts a fresh form and clears the saved draft on New trip', async () => {
    mockSession()
    await openPlanner()
    fillTokyoTrip()
    await waitFor(() => expect(localStorage.getItem(TRIP_DRAFT_KEY)).toContain('Tokyo, Japan'))

    fireEvent.click(screen.getByRole('button', { name: /new trip/i }))
    expect(localStorage.getItem(TRIP_DRAFT_KEY)).toBeNull()
    expect(screen.getByLabelText(/destination/i)).toHaveValue('')
    expect(screen.getByLabelText(/amount/i)).toHaveValue(2200)
  })
})

describe('research failure recovery', () => {
  it('reopens the workspace with four failed agents and retries the same trip after every agent failed', async () => {
    mockSession({ trip_id: 'trip-failed', preferences, research_results: [], research_errors: allFailed, selections: [] })
    const createTrip = vi.spyOn(api, 'createTrip')
    const research = vi.spyOn(api, 'research').mockImplementation(streamOf([{ event: 'all_complete', completed: 0, failed: 4, status: 'failed' }]))
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'The shortlist' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /point the compass/i })).not.toBeInTheDocument()
    expect(screen.getAllByText('failed')).toHaveLength(4)
    expect(screen.getAllByText('Could not finish — retry available')).toHaveLength(4)
    expect(screen.getByText('4 research categories need another try')).toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: /retry missing categories/i })[0])
    await waitFor(() => expect(research).toHaveBeenCalledWith('trip-failed', expect.any(Function)))
    expect(createTrip).not.toHaveBeenCalled()
    expect(await screen.findByRole('heading', { name: 'The shortlist' })).toBeInTheDocument()
    expect(screen.getAllByText('failed')).toHaveLength(4)
  })

  it('keeps a fresh trip in the workspace with retry when every agent fails during the live stream', async () => {
    mockSession()
    vi.spyOn(api, 'createTrip').mockResolvedValueOnce(received)
    vi.spyOn(api, 'research').mockImplementation(streamOf([
      ...agentNames.flatMap((agent): StreamEvent[] => [{ event: 'agent_started', agent }, { event: 'agent_failed', agent }]),
      { event: 'all_complete', completed: 0, failed: 4, status: 'failed' },
    ]))
    vi.spyOn(api, 'trip').mockResolvedValue({ trip_id: 'trip-new', preferences, research_results: [], research_errors: allFailed, selections: [] })
    await openPlanner()
    fillTokyoTrip()

    fireEvent.click(submitButton())
    expect(await screen.findByText('4 research categories need another try')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'The shortlist' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /point the compass/i })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /retry missing categories/i }).length).toBeGreaterThan(0)
  })
})

describe('selections and itinerary', () => {
  const researched: TripState = { trip_id: 'trip-1', preferences, research_results: [{ agent_name: 'Accommodation Agent', recommendations: [hotel] }], selections: [] }

  it('persists every card toggle immediately and reverts a toggle the server rejects', async () => {
    mockSession(researched)
    const select = vi.spyOn(api, 'select')
      .mockResolvedValueOnce({ status: 'selections_saved', count: 1 })
      .mockRejectedValueOnce(new ApiError('Selections could not be saved.', 503, 'SERVICE_UNAVAILABLE', undefined, true))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'The shortlist' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /choose this/i }))
    await waitFor(() => expect(select).toHaveBeenCalledWith('trip-1', ['hotel-1']))
    expect(await screen.findByText('1 selected')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Selected' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Selected' }))
    await waitFor(() => expect(select).toHaveBeenCalledWith('trip-1', []))
    expect(await screen.findByRole('alert')).toHaveTextContent('Selections could not be saved.')
    expect(screen.getByText('1 selected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Selected' })).toBeInTheDocument()
    expect(select).toHaveBeenCalledTimes(2)
  })

  it('stays in the workspace when itinerary generation fails, even though the server still holds an old itinerary', async () => {
    const oldItinerary: Itinerary = { trip_title: 'Old Kyoto Route', trip_summary: 'A stale plan from an earlier selection set.', days: [] }
    const withSelection: TripState = { ...researched, selections: ['hotel-1'] }
    mockSession(withSelection)
    const trip = vi.spyOn(api, 'trip').mockResolvedValueOnce(withSelection).mockResolvedValue({ ...withSelection, itinerary: oldItinerary })
    vi.spyOn(api, 'itinerary').mockImplementation(streamOf([{ event: 'itinerary_failed', error: 'The itinerary could not be generated.', request_id: 'req-abcdef12' }]))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'The shortlist' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /build my itinerary/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The itinerary could not be generated. Reference: req-abcd.')
    expect(screen.getByRole('heading', { name: 'The shortlist' })).toBeInTheDocument()
    expect(screen.queryByText('Old Kyoto Route')).not.toBeInTheDocument()
    expect(screen.queryByText('Shaping your final route')).not.toBeInTheDocument()
    expect(trip).toHaveBeenCalledTimes(1)
  })

  it('moves to the itinerary only when the stream completes', async () => {
    const fresh: Itinerary = { trip_title: 'Kyoto Between Lanterns', trip_summary: 'A new plan.', days: [] }
    mockSession({ ...researched, selections: ['hotel-1'] })
    vi.spyOn(api, 'itinerary').mockImplementation(streamOf([{ event: 'itinerary_complete', itinerary: fresh }]))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'The shortlist' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /build my itinerary/i }))
    expect(await screen.findByRole('heading', { name: 'Kyoto Between Lanterns' })).toBeInTheDocument()
    expect(api.trip).toHaveBeenCalledTimes(1)
  })
})

describe('planning scope', () => {
  const journey: Recommendation = { ...hotel, id: 'transport-1', name: 'Complete journey', category: 'transport' }
  const rail = () => within(screen.getByRole('region', { name: 'Research agents' }))

  it('hydrates a transport-only trip with just the Transport Agent and ignores out-of-scope results', async () => {
    mockSession({
      trip_id: 'trip-flights', preferences: { ...preferences, scope: ['transport'] }, selections: [],
      research_results: [{ agent_name: 'Transport Agent', recommendations: [journey] }, { agent_name: 'Accommodation Agent', recommendations: [hotel] }],
      research_errors: ['Restaurant Agent could not finish this search. You can retry safely.'],
    })
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'The shortlist' })).toBeInTheDocument()
    expect(screen.getByText('1/1 agents')).toBeInTheDocument()
    expect(rail().getByText('Transport')).toBeInTheDocument()
    expect(rail().getByText('complete')).toBeInTheDocument()
    expect(rail().queryByText('Accommodation')).not.toBeInTheDocument()
    expect(rail().queryByText('Restaurant')).not.toBeInTheDocument()
    expect(screen.queryByText(/needs another try/)).not.toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(1)
    expect(screen.getByRole('tab', { name: /transportation/i })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: 'Complete journey' })).toBeInTheDocument()
    expect(screen.queryByText('A quiet Kyoto stay')).not.toBeInTheDocument()
  })

  it('begins research with only the scoped agents and sends the scope in canonical order', async () => {
    mockSession()
    const createTrip = vi.spyOn(api, 'createTrip').mockResolvedValueOnce(received)
    const stream = deferred<void>()
    vi.spyOn(api, 'research').mockImplementation((_id, onEvent) => { onEvent({ event: 'agent_started', agent: 'Transport Agent' }); return stream.promise })
    vi.spyOn(api, 'trip').mockResolvedValue({ trip_id: 'trip-new', preferences: { ...preferences, scope: ['transport', 'hotel'] }, research_results: [{ agent_name: 'Transport Agent', recommendations: [journey] }], selections: [] })
    await openPlanner()
    fillTokyoTrip()
    const plan = within(screen.getByRole('group', { name: /what should we plan/i }))
    fireEvent.click(plan.getByRole('button', { name: 'Food' }))
    fireEvent.click(plan.getByRole('button', { name: 'Activities' }))

    fireEvent.click(submitButton())
    expect(await screen.findByRole('heading', { name: 'The shortlist' })).toBeInTheDocument()
    expect(createTrip.mock.calls[0][0].scope).toEqual(['transport', 'hotel'])
    expect(screen.getByText('0/2 agents')).toBeInTheDocument()
    expect(rail().getByText('working')).toBeInTheDocument()
    expect(rail().getByText('Transport')).toBeInTheDocument()
    expect(rail().getByText('Accommodation')).toBeInTheDocument()
    expect(rail().queryByText('Activities')).not.toBeInTheDocument()
    expect(rail().queryByText('Restaurant')).not.toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(2)

    await act(async () => stream.resolve())
    expect(await screen.findByText('1/2 agents')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Complete journey' })).toBeInTheDocument()
  })
})
