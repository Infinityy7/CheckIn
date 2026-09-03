import { scopeOf } from '../scope'
import type { TripPreferences } from '../types'

export const TRIP_DRAFT_KEY = 'travelbuddy.tripDraft.v1'

export interface TripDraft {
  form: TripPreferences
  guests: string[]
}

export function loadTripDraft(): TripDraft | null {
  try {
    const raw = localStorage.getItem(TRIP_DRAFT_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<TripDraft> | null
    if (!parsed || typeof parsed !== 'object' || !parsed.form || typeof parsed.form !== 'object') return null
    return { form: { ...parsed.form, scope: scopeOf(parsed.form) }, guests: Array.isArray(parsed.guests) ? parsed.guests.filter((guest): guest is string => typeof guest === 'string') : [] }
  } catch { return null }
}

export function saveTripDraft(draft: TripDraft) {
  try { localStorage.setItem(TRIP_DRAFT_KEY, JSON.stringify(draft)) } catch { /* storage unavailable */ }
}

export function clearTripDraft() {
  try { localStorage.removeItem(TRIP_DRAFT_KEY) } catch { /* storage unavailable */ }
}
