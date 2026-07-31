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

export type Vibe = 'adventure' | 'culture' | 'food' | 'nightlife' | 'relaxation' | 'nature' | 'shopping' | 'history' | 'romance' | 'wellness'
export type SpendCategory = 'stay' | 'experiences' | 'food' | 'shopping' | 'transport'
export type TravelerArchetype = 'foodie_explorer' | 'culture_seeker' | 'adrenaline_chaser' | 'slow_traveler' | 'luxury_unwinder' | 'social_butterfly'
export type DefaultParty = 'solo' | 'partner' | 'friends' | 'family_young_kids' | 'multi_generation'

export interface ProfileWeights {
  schemaVersion: number
  vibeWeights: Partial<Record<Vibe, number>>
  spontaneity: number
  chronotype: 'early' | 'mid' | 'late'
  splurgeCategory: SpendCategory
  saveCategory: SpendCategory
  archetype: TravelerArchetype
  defaultParty: DefaultParty
  foodAdventurousness: number
  dealBreakers: string[]
  dietaryRequirements: string[]
}

export interface CharacterProfile {
  id: string
  version: number
  summary: string
  characterMd?: string
  weights?: ProfileWeights
  /** Legacy compatibility while stored profiles migrate to weights. */
  traits?: CharacterTraits
  rawAnswers: Record<string, IntakeAnswer> | string[]
  createdAt: string
  updatedAt: string
}

export type IntakeAnswer = string | string[] | number | { splurge: string; save: string }
export type IntakeQuestionType = 'slider' | 'single_choice' | 'multi_choice' | 'paired_choice' | 'free_text'

export interface IntakeQuestion {
  id: string
  prompt: string
  type: IntakeQuestionType
  options?: Array<{ value: string; label: string }>
  minSelections?: number
  maxSelections?: number
  optional?: boolean
  lowLabel?: string
  highLabel?: string
}

export interface IntakeState {
  questionnaireVersion: string
  status: 'not_started' | 'in_progress' | 'ready_to_complete' | 'completing' | 'completion_failed' | 'complete'
  currentIndex: number
  total: 9
  answers: Record<string, IntakeAnswer>
  currentQuestion: IntakeQuestion | null
  profile?: CharacterProfile
}

export interface WeightAdjustment {
  key: string
  before: number
  after: number
  delta: number
}

export interface PostTripState {
  eligible: boolean
  eligibleAt?: string
  rating?: 1 | 2 | 3 | 4 | 5
  submittedAt?: string
  adjustments?: WeightAdjustment[]
}

export interface PendingCheckInTrip {
  trip_id: string
  destination: string
  end_date: string
  trip_title: string
}

export interface PostTripFeedbackResponse {
  postTrip: PostTripState
  profile: CharacterProfile
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
  postTrip?: PostTripState
}

export interface StreamEvent {
  event: string
  agent?: string
  results?: Recommendation[]
  brief?: string
  error?: string
  code?: string
  request_id?: string
  retryable?: boolean
  completed?: number
  failed?: number
  itinerary?: Itinerary
}
