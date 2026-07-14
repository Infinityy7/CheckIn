export type MascotState = 'neutral' | 'greeting' | 'thinking' | 'excited' | 'recommending' | 'confused' | 'celebrating' | 'idle'

export interface User {
  email: string
  intake_complete: boolean
  cotravellers: string[]
}

export interface CharacterTraits {
  pace: 'slow' | 'balanced' | 'fast'
  budgetStyle: 'strict' | 'balanced' | 'flexible'
  adventureLevel: number
  socialPreference: number
  comfortPreference: number
  spontaneity: number
  localVsTourist: number
  foodAdventurousness: number
  nightlifeInterest: number
  natureVsUrban: number
}

export interface CharacterProfile {
  id: string
  version: number
  summary: string
  traits: CharacterTraits
  rawAnswers: string[]
  createdAt: string
  updatedAt: string
}

export interface TripPreferences {
  destination: string
  origin: string
  start_date: string
  end_date: string
  budget_amount: number
  currency: string
  vibes: string[]
  group_type: 'solo' | 'couple' | 'friends' | 'family'
  num_travelers: number
  cotravellers: string[]
}

export interface Recommendation {
  id: string
  name: string
  category: 'hotel' | 'activity' | 'restaurant' | 'transport'
  description: string
  reasoning: string
  estimated_cost: string
  cost_min: number
  cost_max: number
  rating: number
  review_count: number
  location: string
  image_search_query: string
  metadata: Record<string, unknown>
  rank: number
  score: number
  score_breakdown: Record<string, number>
}

export interface AgentResult {
  agent_name: string
  recommendations: Recommendation[]
}

export interface ItineraryItem {
  time_slot: string
  title: string
  description: string
  category: string
  cost_estimate: string
  location: string
  tip?: string
}

export interface DayPlan {
  day_number: number
  date: string
  theme: string
  items: ItineraryItem[]
}

export interface Itinerary {
  trip_title: string
  trip_summary: string
  days: DayPlan[]
}

export interface TripState {
  trip_id: string
  preferences: TripPreferences
  context_brief?: string
  research_results?: AgentResult[]
  research_errors?: string[]
  selections?: string[]
  itinerary?: Itinerary
}

export interface StreamEvent {
  event: string
  agent?: string
  results?: Recommendation[]
  brief?: string
  error?: string
  itinerary?: Itinerary
}
