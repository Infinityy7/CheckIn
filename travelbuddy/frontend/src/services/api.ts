import type { CharacterProfile, Recommendation, StreamEvent, TripPreferences, TripState, User } from '../types'

const TOKEN_KEY = 'travelbuddy.session'
const JSON_TIMEOUT_MS = 20_000

interface ProblemBody {
  detail?: unknown
  error?: {
    code?: string
    message?: string
    request_id?: string
    retryable?: boolean
  }
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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY)
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), JSON_TIMEOUT_MS)
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
    throw new ApiError('TravelBuddy could not reach the server. Check your connection and try again.', 0, 'NETWORK_ERROR', undefined, true)
  } finally {
    window.clearTimeout(timeout)
  }
}

function parseStreamEvent(line: string): StreamEvent {
  try {
    return JSON.parse(line.slice(6)) as StreamEvent
  } catch {
    throw new ApiError('TravelBuddy received an unreadable progress update. It is safe to retry.', 0, 'INVALID_STREAM', undefined, true)
  }
}

async function stream(path: string, onEvent: (event: StreamEvent) => void): Promise<void> {
  const token = localStorage.getItem(TOKEN_KEY)
  let response: Response
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
  } catch {
    throw new ApiError('TravelBuddy could not reach the research server. Check your connection and try again.', 0, 'NETWORK_ERROR', undefined, true)
  }
  if (!response.ok) throw await responseError(response, 'The research crew could not start.')
  if (!response.body) throw new ApiError('The server did not open a progress stream. It is safe to retry.', response.status, 'STREAM_UNAVAILABLE', response.headers.get('X-Request-ID') ?? undefined, true)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const line = chunk.split('\n').find((entry) => entry.startsWith('data: '))
      if (line) onEvent(parseStreamEvent(line))
    }
  }
  buffer += decoder.decode()
  const finalLine = buffer.split('\n').find((entry) => entry.startsWith('data: '))
  if (finalLine) onEvent(parseStreamEvent(finalLine))
}

export const api = {
  hasSession: () => Boolean(localStorage.getItem(TOKEN_KEY)),
  auth: async (mode: 'login' | 'register', email: string, password: string) => {
    const result = await request<{ token: string }>(`/api/auth/${mode}`, {
      method: 'POST', body: JSON.stringify({ email, password }),
    })
    localStorage.setItem(TOKEN_KEY, result.token)
  },
  logout: async () => {
    await request('/api/auth/logout', { method: 'POST' }).catch(() => undefined)
    localStorage.removeItem(TOKEN_KEY)
  },
  me: () => request<User>('/api/auth/me'),
  profile: () => request<CharacterProfile>('/api/profile/character'),
  profileChat: (message = '') => request<{ reply: string; done: boolean }>('/api/profile/chat', {
    method: 'POST', body: JSON.stringify({ message }),
  }),
  updateProfile: (profile: Pick<CharacterProfile, 'summary' | 'traits'>) => request<CharacterProfile>('/api/profile/character', {
    method: 'PUT', body: JSON.stringify(profile),
  }),
  resetProfile: () => request('/api/profile/character/reset', { method: 'POST' }),
  feedback: (recommendation: Recommendation, sentiment: 'like' | 'dislike') => request<CharacterProfile>('/api/profile/character/feedback', {
    method: 'POST',
    body: JSON.stringify({ recommendation_name: recommendation.name, category: recommendation.category, sentiment }),
  }),
  createTrip: (preferences: TripPreferences) => request<{ trip_id: string }>('/api/trip/preferences', {
    method: 'POST', body: JSON.stringify(preferences),
  }),
  trip: (id: string) => request<TripState>(`/api/trip/${id}`),
  research: (id: string, onEvent: (event: StreamEvent) => void) => stream(`/api/trip/${id}/research`, onEvent),
  select: (id: string, selections: string[]) => request(`/api/trip/${id}/select`, {
    method: 'POST', body: JSON.stringify({ selections }),
  }),
  itinerary: (id: string, onEvent: (event: StreamEvent) => void) => stream(`/api/trip/${id}/itinerary`, onEvent),
}
