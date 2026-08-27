export type MascotState = 'neutral' | 'greeting' | 'thinking' | 'excited' | 'recommending' | 'confused' | 'celebrating' | 'idle'

export interface User {
  email: string
  username: string | null
  name?: string | null
  phone?: string | null
  intake_complete: boolean
  cotravellers: string[]
}

export interface RegisterPayload {
  email: string
  password: string
  username: string
  name: string
  phone?: string
}

export interface UserLookup {
  username: string
  name: string | null
  intake_complete: boolean
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
  cotraveller_usernames: string[]
}

export interface ScoreBreakdown {
  rating?: number
  vibes?: number
  budget?: number
  taste?: number
  total?: number
  matched?: string[]
  conflicts?: string[]
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
  vibe_tags?: string[]
  constraint_tags?: string[]
  dietary_tags?: string[]
  dietary_conflicts?: string[]
  rank: number
  score: number
  score_breakdown: ScoreBreakdown
}

/** Cache stamp read from recommendation.metadata (agents/base.py _from_cache). */
export interface RecommendationCacheInfo {
  ageSeconds: number
  similarity?: number
}

export function cacheInfo(item: Pick<Recommendation, 'metadata'>): RecommendationCacheInfo | null {
  const meta = item.metadata
  if (!meta || meta.cached !== true) return null
  const age = typeof meta.cache_age_seconds === 'number' ? meta.cache_age_seconds : 0
  const similarity = typeof meta.cache_similarity === 'number' ? meta.cache_similarity : undefined
  return { ageSeconds: age, similarity }
}

export type InventorySourceMode = 'live' | 'test' | 'demo' | 'unavailable'
export type AvailabilityStatus = 'available' | 'limited' | 'unavailable' | 'price_changed' | 'expired' | 'unknown'
export type CartItemStatus = 'saved' | 'quoted' | 'held' | 'revalidating' | 'booking' | 'booked' | 'confirmed' | 'price_changed' | 'unavailable' | 'expired' | 'error'

export interface Money {
  amount: number
  currency: string
}

export interface InventorySource {
  source: string
  sourceMode: InventorySourceMode
  isLive: boolean
}

export interface HotelRatePlan extends InventorySource {
  id: string
  label: string
  total: Money
  nightly: Money
  taxesAndFees: Money
  refundable: boolean
  cancellationSummary: string
  cancellation?: Record<string, unknown> | null
  availabilityStatus: AvailabilityStatus
  roomsRemaining?: number
  quoteExpiresAt?: string
  holdExpiresAt?: string
}

export interface HotelRoomType {
  id: string
  name: string
  description?: string
  occupancy: {
    adults: number
    children: number
    maxGuests: number
  }
  beds: Array<{ type: string; count: number }>
  board: string
  ratePlans: HotelRatePlan[]
}

export interface HotelAvailability extends InventorySource {
  hotelId: string
  recommendationId: string
  checkedAt: string
  rooms: HotelRoomType[]
}

export interface TripCartItem {
  id: string
  recommendationId: string
  ratePlanId?: string
  kind: 'hotel' | 'flight' | 'ride' | 'restaurant'
  title: string
  subtitle?: string
  status: CartItemStatus
  total?: Money
  source?: string
  sourceMode?: InventorySourceMode
  isLive?: boolean
  quoteExpiresAt?: string
  holdExpiresAt?: string
  addedAt?: string
  checkedAt?: string
  message?: string
}

export interface TripCart {
  tripId: string
  state: 'open' | 'revalidating' | 'ready' | 'checkout' | 'confirmed' | 'partial' | 'error'
  items: TripCartItem[]
  /** Saved-cart expiry is a UI/session lifetime only; it never promises supplier inventory. */
  savedExpiresAt?: string
  earliestHoldExpiresAt?: string
  checkedAt: string
}

export interface FlightOffer extends InventorySource {
  id: string
  carrier: string
  flightNumber?: string
  origin: string
  destination: string
  departAt: string
  arriveAt: string
  durationMinutes: number
  stops: number
  journeyType?: 'one_way' | 'round_trip'
  total: Money
  availabilityStatus: AvailabilityStatus
  quoteExpiresAt?: string
  holdExpiresAt?: string
}

export interface FlightAvailability extends InventorySource {
  recommendationId: string
  checkedAt: string
  offers: FlightOffer[]
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
  agents?: string[]
  resumed?: boolean
  results?: Recommendation[]
  brief?: string
  error?: string
  code?: string
  request_id?: string
  retryable?: boolean
  completed?: number
  failed?: number
  status?: 'complete' | 'partial' | 'failed'
  trip_id?: string
  available_categories?: number
  itinerary?: Itinerary
}

export interface ProfileOverview {
  sketch: string | null
  cotravellers: string[]
}

export type LlmHealthStatus = 'ok' | 'degraded' | 'unavailable'

export interface AgentRouteHealth {
  attempts: number
  successes: number
  failures: number
  failover_attempts: number
  failover_successes: number
  short_circuits: number
  pause_continuations: number
  refusals: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  in_flight: number
  average_latency_ms: number
  circuit: 'closed' | 'open' | 'half_open'
  consecutive_failures: number
}

export interface AgentHealth {
  status: LlmHealthStatus
  account: { status: 'ready' | 'blocked'; code: string | null }
  gateway: { enabled: boolean; mode: 'anthropic_passthrough' | 'direct' }
  research_cache: {
    enabled: boolean
    hits: number
    misses: number
    stores: number
    errors: number
  } | null
  queue_timeouts: number
  routes: Record<string, AgentRouteHealth>
}
