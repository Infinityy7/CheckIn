import type { CharacterProfile, StreamEvent, TripPreferences, TripState, User } from '../types'

const TOKEN_KEY = 'travelbuddy.session'

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY)
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: 'Something went off course.' }))
    throw new ApiError(body.detail ?? 'Something went off course.', response.status)
  }
  return response.json() as Promise<T>
}

async function stream(path: string, onEvent: (event: StreamEvent) => void): Promise<void> {
  const token = localStorage.getItem(TOKEN_KEY)
  const response = await fetch(path, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok || !response.body) throw new ApiError('The research crew could not start.', response.status)
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
      if (line) onEvent(JSON.parse(line.slice(6)) as StreamEvent)
    }
  }
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
