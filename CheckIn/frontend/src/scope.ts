import type { AgentStatus } from './components/Workspace'
import type { TripScope } from './types'

/** Canonical order, mirrored by the backend's SCOPE_CATEGORIES. */
export const SCOPE_OPTIONS: ReadonlyArray<{ id: TripScope; label: string; agent: string; read: string }> = [
  { id: 'transport', label: 'Flights & transport', agent: 'Transport Agent', read: 'flights' },
  { id: 'hotel', label: 'Stays', agent: 'Accommodation Agent', read: 'stays' },
  { id: 'activity', label: 'Activities', agent: 'Activities Agent', read: 'activities' },
  { id: 'restaurant', label: 'Food', agent: 'Restaurant Agent', read: 'food' },
]

export const ALL_SCOPES: readonly TripScope[] = SCOPE_OPTIONS.map((option) => option.id)

export function canonicalScope(ids: ReadonlyArray<string> | null | undefined): TripScope[] {
  const chosen = new Set(Array.isArray(ids) ? ids : [])
  return ALL_SCOPES.filter((id) => chosen.has(id))
}

export function scopeOf(prefs: { scope?: ReadonlyArray<string> | null }): TripScope[] {
  const scoped = canonicalScope(prefs.scope)
  return scoped.length ? scoped : [...ALL_SCOPES]
}

export function agentsForScope(scope: readonly TripScope[]): AgentStatus {
  return Object.fromEntries(SCOPE_OPTIONS.filter((option) => scope.includes(option.id)).map((option) => [option.agent, 'waiting' as const]))
}
