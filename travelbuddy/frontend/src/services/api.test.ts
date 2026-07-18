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
})
