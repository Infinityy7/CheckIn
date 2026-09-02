import { api, ApiError, TRIP_CREATE_TIMEOUT_MS, userErrorMessage } from './api'
import type { TripPreferences } from '../types'

const preferences: TripPreferences = {
  destination: 'Tokyo, Japan', origin: 'Delhi, India', start_date: '2026-10-12', end_date: '2026-10-18', budget_amount: 200,
  currency: 'USD', vibes: ['culture'], group_type: 'couple', num_travelers: 2, cotravellers: [], cotraveller_usernames: [],
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('createTrip', () => {
  it('sends the idempotency key header and the feasibility acknowledgement flag', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ trip_id: null, status: 'held', replayed: false, feasibility: { verdict: 'unrealistic' } }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await api.createTrip(preferences, { idempotencyKey: 'a1b2c3d4-e5f6-7890', acknowledgeFeasibility: true })

    expect(result).toMatchObject({ trip_id: null, status: 'held', replayed: false })
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/trip/preferences')
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({ 'Idempotency-Key': 'a1b2c3d4-e5f6-7890', 'Content-Type': 'application/json' })
    expect(JSON.parse(init.body as string)).toEqual({ ...preferences, feasibility_acknowledged: true })
  })

  it('does not acknowledge feasibility unless asked', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ trip_id: 'trip-1', status: 'received', replayed: false }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await api.createTrip(preferences, { idempotencyKey: 'a1b2c3d4-e5f6-7890' })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(init.body as string).feasibility_acknowledged).toBe(false)
  })

  it('outlasts the 25s backend feasibility deadline instead of using the 20s JSON budget', async () => {
    expect(TRIP_CREATE_TIMEOUT_MS).toBe(45_000)
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_path: string, init?: RequestInit) => new Promise<Response>((_, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })))
    const outcome = vi.fn()
    api.createTrip(preferences, { idempotencyKey: 'a1b2c3d4-e5f6-7890' }).then(() => outcome('resolved'), (reason: ApiError) => outcome(reason.code))

    await vi.advanceTimersByTimeAsync(30_000)
    expect(outcome).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(TRIP_CREATE_TIMEOUT_MS - 30_000)
    expect(outcome).toHaveBeenCalledWith('REQUEST_TIMEOUT')
  })

  it('keeps the shorter JSON budget for ordinary reads', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_path: string, init?: RequestInit) => new Promise<Response>((_, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })))
    const outcome = vi.fn()
    api.me().then(() => outcome('resolved'), (reason: ApiError) => outcome(reason.code))

    await vi.advanceTimersByTimeAsync(20_000)
    expect(outcome).toHaveBeenCalledWith('REQUEST_TIMEOUT')
  })
})

describe('ApiError', () => {
  it('keeps the response status for UI recovery decisions', () => {
    const error = new ApiError('Sign in again', 401)
    expect(error.message).toBe('Sign in again')
    expect(error.status).toBe(401)
  })

  it('reads the stable backend problem contract', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: 'Research is busy.',
      error: { code: 'CONFLICT', message: 'Research is busy.', request_id: 'request-abcdef12', retryable: true },
    }), { status: 409, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'request-abcdef12' } })))

    await expect(api.me()).rejects.toMatchObject({
      status: 409,
      code: 'CONFLICT',
      requestId: 'request-abcdef12',
      retryable: true,
    })
  })

  it('turns a network failure into a useful retryable error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(api.me()).rejects.toMatchObject({
      code: 'NETWORK_ERROR',
      retryable: true,
    })
  })

  it('adds a short support reference to user-facing messages', () => {
    const error = new ApiError('Could not finish.', 503, 'SERVICE_UNAVAILABLE', 'abcdef123456', true)
    expect(userErrorMessage(error, 'Fallback')).toBe('Could not finish. Reference: abcdef12.')
  })

  it('persists deterministic intake answers using the question id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      questionnaireVersion: 'personalisation-v1', status: 'in_progress', currentIndex: 3, total: 9, answers: {}, currentQuestion: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await api.answerIntake('spend_preferences', { splurge: 'food', save: 'transport' })

    expect(fetchMock).toHaveBeenCalledWith('/api/profile/intake/answers/spend_preferences', expect.objectContaining({
      method: 'PUT', body: JSON.stringify({ value: { splurge: 'food', save: 'transport' } }),
    }))
  })

  it('submits post-trip ratings through an idempotent PUT contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ postTrip: { eligible: true, rating: 5 }, profile: {} }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await api.submitPostTripFeedback('trip/unsafe id', 5)

    expect(fetchMock).toHaveBeenCalledWith('/api/trip/trip%2Funsafe%20id/post-trip-feedback', expect.objectContaining({
      method: 'PUT', body: JSON.stringify({ overall_rating: 5 }),
    }))
  })

  it('sends stable trip and recommendation ids for explicit feedback', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({}), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await api.feedback('trip-42', 'restaurant-7', 'dislike')

    expect(fetchMock).toHaveBeenCalledWith('/api/profile/character/feedback', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ trip_id: 'trip-42', recommendation_id: 'restaurant-7', sentiment: 'dislike' }),
    }))
  })

  it('rejects a progress stream that closes without a terminal event', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      'data: {"event":"agent_started","agent":"Activities Agent"}\n\n',
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )))

    await expect(api.research('trip-42', () => undefined)).rejects.toMatchObject({
      code: 'STREAM_INTERRUPTED',
      retryable: true,
    })
  })

  it('accepts a stream only after its terminal event arrives', async () => {
    const events: string[] = []
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      'data: {"event":"heartbeat"}\n\ndata: {"event":"all_complete"}\n\n',
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )))

    await expect(api.research('trip-42', (event) => events.push(event.event))).resolves.toBeUndefined()
    expect(events).toEqual(['heartbeat', 'all_complete'])
  })
})
