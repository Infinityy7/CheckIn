import type { AgentHealth, CharacterProfile, CharacterTraits, FlightAvailability, HotelAvailability, IntakeAnswer, IntakeState, PendingCheckInTrip, PostTripFeedbackResponse, ProfileOverview, ProfileWeights, RegisterPayload, StreamEvent, TripCart, TripPreferences, TripState, User, UserLookup } from '../types'

const TOKEN_KEY = 'travelbuddy.session'
const JSON_TIMEOUT_MS = 20_000
// Endpoints that wait on a model turn. Adaptive thinking makes these
// legitimately slower than a database read, so they get their own budget.
const MODEL_TIMEOUT_MS = 90_000

interface ProblemBody {
  detail?: unknown
  error?: {
    code?: string
    message?: string
    request_id?: string
    retryable?: boolean
  }
}

interface CharacterProfileUpdate {
  summary: string
  expectedVersion?: number
  characterMd?: string
  weights?: ProfileWeights
  traits?: CharacterTraits
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code = 'REQUEST_FAILED',
    public requestId?: string,
    public retryable = false,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export function userErrorMessage(reason: unknown, fallback: string): string {
  if (!(reason instanceof Error)) return fallback
  if (reason instanceof ApiError && reason.requestId) {
    return `${reason.message} Reference: ${reason.requestId.slice(0, 8)}.`
  }
  return reason.message || fallback
}

async function responseError(response: Response, fallback: string): Promise<ApiError> {
  const body = await response.json().catch(() => ({})) as ProblemBody
  const detail = typeof body.detail === 'string' ? body.detail : undefined
  const requestId = body.error?.request_id ?? response.headers.get('X-Request-ID') ?? undefined
  return new ApiError(
    body.error?.message ?? detail ?? fallback,
    response.status,
    body.error?.code ?? 'REQUEST_FAILED',
    requestId,
    body.error?.retryable ?? response.status >= 500,
  )
}

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = JSON_TIMEOUT_MS): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY)
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(path, {
      ...init,
      signal: init.signal ?? controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    })
    if (!response.ok) throw await responseError(response, 'Something went off course.')
    return response.json() as Promise<T>
  } catch (reason) {
    if (reason instanceof ApiError) throw reason
    if (reason instanceof DOMException && reason.name === 'AbortError') {
      throw new ApiError('The server took too long to respond. It is safe to try again.', 0, 'REQUEST_TIMEOUT', undefined, true)
    }
    throw new ApiError('CheckIn could not reach the server. Check your connection and try again.', 0, 'NETWORK_ERROR', undefined, true)
  } finally {
    window.clearTimeout(timeout)
  }
}

function parseStreamEvent(line: string): StreamEvent {
  try {
    return JSON.parse(line.slice(6)) as StreamEvent
  } catch {
    throw new ApiError('CheckIn received an unreadable progress update. It is safe to retry.', 0, 'INVALID_STREAM', undefined, true)
  }
}

const STREAM_IDLE_TIMEOUT_MS = 45_000

async function readStreamChunk(reader: ReadableStreamDefaultReader<Uint8Array>) {
  let timeout = 0
  try {
    return await Promise.race([
      reader.read(),
      new Promise<never>((_, reject) => {
        timeout = window.setTimeout(() => reject(new ApiError(
          'The progress stream went quiet. Your completed work is safe to retry.',
          0, 'STREAM_TIMEOUT', undefined, true,
        )), STREAM_IDLE_TIMEOUT_MS)
      }),
    ])
  } catch (reason) {
    await reader.cancel().catch(() => undefined)
    throw reason
  } finally {
    window.clearTimeout(timeout)
  }
}

async function stream(
  path: string,
  onEvent: (event: StreamEvent) => void,
  terminalEvents: ReadonlySet<string>,
): Promise<void> {
  const token = localStorage.getItem(TOKEN_KEY)
  let response: Response
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
  } catch {
    throw new ApiError('CheckIn could not reach the research server. Check your connection and try again.', 0, 'NETWORK_ERROR', undefined, true)
  }
  if (!response.ok) throw await responseError(response, 'The research crew could not start.')
  if (!response.body) throw new ApiError('The server did not open a progress stream. It is safe to retry.', response.status, 'STREAM_UNAVAILABLE', response.headers.get('X-Request-ID') ?? undefined, true)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminalSeen = false
  const dispatch = (line: string) => {
    const event = parseStreamEvent(line)
    onEvent(event)
    if (terminalEvents.has(event.event)) terminalSeen = true
  }
  while (true) {
    const { done, value } = await readStreamChunk(reader)
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const line = chunk.split('\n').find((entry) => entry.startsWith('data: '))
      if (line) dispatch(line)
    }
  }
  buffer += decoder.decode()
  const finalLine = buffer.split('\n').find((entry) => entry.startsWith('data: '))
  if (finalLine) dispatch(finalLine)
  if (!terminalSeen) {
    throw new ApiError(
      'The progress stream ended early. Your completed work is safe to retry.',
      response.status, 'STREAM_INTERRUPTED', response.headers.get('X-Request-ID') ?? undefined, true,
    )
  }
}

export const api = {
  hasSession: () => Boolean(localStorage.getItem(TOKEN_KEY)),
  register: async (payload: RegisterPayload) => {
    const result = await request<{ token: string }>('/api/auth/register', {
      method: 'POST', body: JSON.stringify(payload),
    })
    localStorage.setItem(TOKEN_KEY, result.token)
  },
  login: async (identifier: string, password: string) => {
    const result = await request<{ token: string }>('/api/auth/login', {
      method: 'POST', body: JSON.stringify({ identifier, password }),
    })
    localStorage.setItem(TOKEN_KEY, result.token)
  },
  lookupUser: (username: string) => request<UserLookup>(`/api/users/lookup?username=${encodeURIComponent(username)}`),
  logout: async () => {
    await request('/api/auth/logout', { method: 'POST' }).catch(() => undefined)
    localStorage.removeItem(TOKEN_KEY)
  },
  me: () => request<User>('/api/auth/me'),
  profile: () => request<CharacterProfile>('/api/profile/character'),
  intake: () => request<IntakeState>('/api/profile/intake'),
  answerIntake: (questionId: string, value: IntakeAnswer) => request<IntakeState>(`/api/profile/intake/answers/${encodeURIComponent(questionId)}`, {
    method: 'PUT', body: JSON.stringify({ value }),
  }),
  completeIntake: () => request<CharacterProfile>('/api/profile/intake/complete', { method: 'POST' }, MODEL_TIMEOUT_MS),
  resetIntake: () => request('/api/profile/intake', { method: 'DELETE' }),
  /** Self-profile chat is legacy; with cotravellerName it is the canonical companion intake. */
  profileChat: (message = '', cotravellerName?: string) => request<{ reply: string; done: boolean }>('/api/profile/chat', {
    method: 'POST', body: JSON.stringify({ message, ...(cotravellerName ? { cotraveller_name: cotravellerName } : {}) }),
  }, MODEL_TIMEOUT_MS),
  profileOverview: () => request<ProfileOverview>('/api/profile'),
  agentHealth: () => request<AgentHealth>('/api/health/agents'),
  updateProfile: (profile: CharacterProfileUpdate) => request<CharacterProfile>('/api/profile/character', {
    method: 'PUT', body: JSON.stringify(profile),
  }),
  resetProfile: () => request('/api/profile/character/reset', { method: 'POST' }),
  feedback: (tripId: string, recommendationId: string, sentiment: 'like' | 'dislike') => request<CharacterProfile>('/api/profile/character/feedback', {
    method: 'POST',
    body: JSON.stringify({ trip_id: tripId, recommendation_id: recommendationId, sentiment }),
  }),
  createTrip: (preferences: TripPreferences) => request<{ trip_id: string }>('/api/trip/preferences', {
    method: 'POST', body: JSON.stringify(preferences),
  }),
  trip: (id: string) => request<TripState>(`/api/trip/${id}`),
  hotelRates: (tripId: string, recommendationId: string) => request<HotelAvailability>(
    `/api/trip/${encodeURIComponent(tripId)}/hotels/${encodeURIComponent(recommendationId)}/rates`,
  ),
  flightOffers: (tripId: string, recommendationId: string) => request<FlightAvailability>(
    `/api/trip/${encodeURIComponent(tripId)}/flights/${encodeURIComponent(recommendationId)}/offers`,
  ),
  cart: (tripId: string) => request<TripCart>(`/api/trip/${encodeURIComponent(tripId)}/cart`),
  addCartItem: (tripId: string, recommendationId: string, ratePlanId: string | undefined, kind: 'hotel' | 'flight' | 'ride' | 'restaurant') => request<TripCart>(
    `/api/trip/${encodeURIComponent(tripId)}/cart/items`, {
      method: 'POST', body: JSON.stringify({ recommendationId, ...(ratePlanId ? { ratePlanId } : {}), kind }),
    },
  ),
  removeCartItem: (tripId: string, itemId: string) => request<TripCart>(
    `/api/trip/${encodeURIComponent(tripId)}/cart/items/${encodeURIComponent(itemId)}`, { method: 'DELETE' },
  ),
  revalidateCart: (tripId: string) => request<TripCart>(`/api/trip/${encodeURIComponent(tripId)}/cart/revalidate`, {
    method: 'POST',
  }, 45_000),
  pendingCheckIn: () => request<{ trip: PendingCheckInTrip | null }>('/api/trips/pending-check-in'),
  submitPostTripFeedback: (id: string, overallRating: 1 | 2 | 3 | 4 | 5) => request<PostTripFeedbackResponse>(`/api/trip/${encodeURIComponent(id)}/post-trip-feedback`, {
    method: 'PUT', body: JSON.stringify({ overall_rating: overallRating }),
  }),
  research: (id: string, onEvent: (event: StreamEvent) => void) => stream(
    `/api/trip/${id}/research`, onEvent, new Set(['all_complete', 'error']),
  ),
  select: (id: string, selections: string[]) => request(`/api/trip/${id}/select`, {
    method: 'POST', body: JSON.stringify({ selections }),
  }),
  itinerary: (id: string, onEvent: (event: StreamEvent) => void) => stream(
    `/api/trip/${id}/itinerary`, onEvent, new Set(['itinerary_complete', 'itinerary_failed']),
  ),
}
