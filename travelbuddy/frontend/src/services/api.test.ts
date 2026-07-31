import { api, ApiError, userErrorMessage } from './api'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
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
})
