import { expect, test, type Page, type Route } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const shots = process.env.TRAVELBUDDY_E2E_ARTIFACTS
  ? path.resolve(process.env.TRAVELBUDDY_E2E_ARTIFACTS)
  : path.resolve(process.cwd(), '../screenshots')
fs.mkdirSync(shots, { recursive: true })
const me = { email: 'vedant@example.com', username: 'vedant', name: 'Vedant', phone: null, intake_complete: true, cotravellers: [] }
const profile = {
  id: 'character:e2e', version: 1,
  summary: 'Vedant is an energetic, moderately budget-conscious traveler who prioritizes memorable experiences over luxury. He follows local food, dramatic landscapes, unusual activities, and flexible schedules, preferring a few high-impact places over a rushed checklist.',
  characterMd: '# Character Sketch\n\nVedant follows local food, dramatic landscapes, and flexible schedules.',
  weights: { schemaVersion: 1, vibeWeights: { food: .35, culture: .25, nature: .22, adventure: .18 }, spontaneity: .7, chronotype: 'mid', splurgeCategory: 'food', saveCategory: 'transport', archetype: 'foodie_explorer', defaultParty: 'partner', foodAdventurousness: .86, dealBreakers: ['crowded_spots'], dietaryRequirements: [] },
  traits: { pace: 'balanced', budgetStyle: 'balanced', adventureLevel: .78, socialPreference: .58, comfortPreference: .55, spontaneity: .7, localVsTourist: .82, foodAdventurousness: .86, nightlifeInterest: .42, natureVsUrban: .62 },
  rawAnswers: { spontaneity: .7 }, createdAt: '2026-07-14T00:00:00Z', updatedAt: '2026-07-14T00:00:00Z',
}

const questionnaire = [
  { id: 'spontaneity', prompt: 'Your ideal trip day: every hour planned, or see where the day takes you?', type: 'slider', lowLabel: 'Planned', highLabel: 'Spontaneous' },
  { id: 'top_vibes', prompt: 'Pick your top 3 — what makes a trip unforgettable?', type: 'multi_choice', minSelections: 3, maxSelections: 3, options: ['adventure','culture','food','nightlife','relaxation','nature','shopping','history','romance','wellness'].map((value) => ({ value, label: value })) },
  { id: 'spend_preferences', prompt: 'You’d happily splurge on ___ but save on ___.', type: 'paired_choice', options: ['stay','experiences','food','shopping','transport'].map((value) => ({ value, label: value })) },
  { id: 'chronotype', prompt: 'On holiday you’re up and out by…', type: 'single_choice', options: [{ value: 'early', label: '8 AM' }, { value: 'mid', label: '9:30ish' }, { value: 'late', label: 'Whenever we wake up' }] },
  { id: 'archetype', prompt: 'Which traveler is most you?', type: 'single_choice', options: [{ value: 'foodie_explorer', label: 'Foodie Explorer' }, { value: 'culture_seeker', label: 'Culture Seeker' }] },
  { id: 'default_party', prompt: 'Who do you usually travel with?', type: 'single_choice', options: [{ value: 'solo', label: 'Solo' }, { value: 'partner', label: 'Partner' }] },
  { id: 'food_adventurousness', prompt: 'Food on trips: stick to what you know, or eat like a local dares you to?', type: 'slider', lowLabel: 'Familiar', highLabel: 'Anything' },
  { id: 'constraints', prompt: 'Any absolute no-gos?', type: 'multi_choice', minSelections: 0, options: [{ value: 'early_flights', label: 'Early flights' }, { value: 'vegetarian', label: 'Vegetarian' }] },
  { id: 'perfect_moment', prompt: 'In one line — describe your perfect travel moment.', type: 'free_text', optional: true },
]

const categories = ['hotel', 'activity', 'restaurant', 'transport'] as const
const agentNames = { hotel: 'Accommodation Agent', activity: 'Activities Agent', restaurant: 'Restaurant Agent', transport: 'Transport Agent' } as const
const names = {
  hotel: ['Ace Hotel Kyoto', 'Sowaka Gion', 'The Gate Hotel'],
  activity: ['Fushimi Dawn Walk', 'Uji Tea Atelier', 'Arashiyama Backroads'],
  restaurant: ['Giro Giro Hitoshina', 'Nishiki Market Trail', 'Monk Kyoto'],
  transport: ['Shinkansen + Local Rail', 'Private Arrival Transfer', 'Regional Rail Pass'],
}
const recommendations = categories.flatMap((category) => names[category].map((name, index) => ({
  id: `${category}-${index + 1}`, name, category, rank: index + 1, score: .94 - index * .07,
  description: `A carefully researched ${category} option with a strong sense of place, dependable quality, and enough character to make the trip memorable.`,
  reasoning: index === 0 ? 'It combines local texture, strong value, and the unhurried flexibility in your character profile.' : 'A thoughtful match for your comfort, budget, and appetite for distinctly local experiences.',
  estimated_cost: category === 'hotel' ? '$180–240 / night' : category === 'restaurant' ? '$35–65' : category === 'transport' ? '$80–120' : '$25–45',
  cost_min: 25, cost_max: 240, rating: 4.8 - index * .1, review_count: 1240 - index * 210,
  location: ['Gion, Kyoto', 'Higashiyama, Kyoto', 'Central Kyoto'][index], image_search_query: `${name} Kyoto`, metadata: {}, score_breakdown: { rating: .9, vibes: .94, budget: .83, taste: .96 },
})))
const researchResults = (items = recommendations) => categories.map((category) => ({
  agent_name: agentNames[category], recommendations: items.filter((item) => item.category === category),
}))

const preferences = { destination: 'Kyoto, Japan', origin: 'Mumbai, India', start_date: '2026-10-12', end_date: '2026-10-18', budget_amount: 3200, currency: 'USD', vibes: ['culture', 'food', 'nature'], group_type: 'couple', num_travelers: 2, cotravellers: [], cotraveller_usernames: [] }
const itinerary = {
  trip_title: 'Kyoto Between Lanterns & Cedar',
  trip_summary: 'A considered six-day route through temple mornings, local kitchens, and quieter edges of Kyoto. The pace leaves room for weather, wandering, and the discoveries that never appear on a checklist.',
  days: [1, 2, 3].map((day) => ({ day_number: day, date: `2026-10-${11 + day}`, theme: ['Gion at First Light', 'Tea Country & River Paths', 'Cedar, Craft & Quiet Temples'][day - 1], items: [
    { time_slot: '8:00 AM – 10:00 AM', title: ['Fushimi Before the Crowds', 'Local train to Uji', 'Northern Temple Walk'][day - 1], description: 'A deliberately early start with time to notice the details before the main visitor flow arrives.', category: day === 2 ? 'transport' : 'activity', cost_estimate: '$18', location: 'Kyoto', tip: 'Take the side path after the second gate.' },
    { time_slot: '12:00 PM – 1:30 PM', title: ['Seasonal Obanzai Lunch', 'Tea-house Tasting', 'Garden-side Soba'][day - 1], description: 'A compact, local lunch chosen for provenance, atmosphere, and an easy fit with the route.', category: 'restaurant', cost_estimate: '$42', location: 'Kyoto' },
    { time_slot: '3:00 PM – 5:00 PM', title: ['Canal-side Wandering', 'Ceramics Workshop', 'Free Time in Demachiyanagi'][day - 1], description: 'A flexible afternoon anchor with breathing room for shops, coffee, or a longer pause.', category: 'activity', cost_estimate: '$30', location: 'Kyoto' },
  ] })),
}

function healthBody(status: 'ok' | 'degraded' | 'unavailable' = 'ok') {
  return {
    status,
    account: { status: 'ready', code: null },
    gateway: { enabled: false, mode: 'direct' },
    research_cache: { enabled: true, hits: 3, misses: 5, stores: 5, errors: 0 },
    queue_timeouts: 0,
    routes: { primary: {
      attempts: 24, successes: status === 'ok' ? 24 : 20, failures: status === 'ok' ? 0 : 4,
      failover_attempts: 0, failover_successes: 0, short_circuits: 0, pause_continuations: 0, refusals: 0,
      input_tokens: 8200, output_tokens: 4100, cache_read_tokens: 0, in_flight: 0, average_latency_ms: 900,
      circuit: status === 'ok' ? 'closed' : 'open', consecutive_failures: status === 'ok' ? 0 : 3,
    } },
  }
}

function json(route: Route, body: unknown, status = 200) { return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }) }
function sse(route: Route, events: unknown[]) {
  return route.fulfill({ status: 200, contentType: 'text/event-stream', body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') })
}
async function session(page: Page, trip = false, final = false, eligible = false) {
  const calls = { profilePuts: 0, resets: 0, feedback: 0, postTrip: 0 }
  await page.addInitScript(({ trip }) => { localStorage.setItem('travelbuddy.session', 'e2e-token'); if (trip) localStorage.setItem('travelbuddy.lastTrip', 'trip-e2e') }, { trip })
  await page.route('**/api/auth/me', (route) => json(route, me))
  await page.route('**/api/profile', (route) => json(route, { sketch: profile.summary, cotravellers: [] }))
  await page.route('**/api/health/agents', (route) => json(route, healthBody('ok')))
  await page.route('**/api/profile/character/reset', (route) => { calls.resets += 1; return json(route, { status: 'reset', intake_complete: false }) })
  await page.route('**/api/profile/intake', (route) => { if (route.request().method() === 'DELETE') calls.resets += 1; return json(route, { status: 'not_started', currentIndex: 0, total: 9, answers: {}, currentQuestion: questionnaire[0] }) })
  await page.route('**/api/profile/character/feedback', (route) => { calls.feedback += 1; return json(route, profile) })
  await page.route('**/api/profile/chat', (route) => json(route, { reply: 'Let’s remap your travel style. What pace feels best?', done: false }))
  await page.route('**/api/profile/character', (route) => { if (route.request().method() === 'PUT') calls.profilePuts += 1; return json(route, profile) })
  await page.route('**/api/trips/pending-check-in', (route) => json(route, { trip: null }))
  await page.route('**/api/trip/trip-e2e/cart/items', (route) => json(route, {
    tripId: 'trip-e2e', state: 'open', savedExpiresAt: new Date(Date.now() + 60 * 60_000).toISOString(), checkedAt: new Date().toISOString(), items: [{
      id: 'cart-e2e', recommendationId: 'transport-1', ratePlanId: 'flight-e2e', kind: 'flight',
      title: 'Test flight offer', status: 'quoted', total: { amount: 740, currency: 'USD' },
      source: 'Controlled fixture', sourceMode: 'test', isLive: false,
    }],
  }))
  await page.route('**/api/trip/trip-e2e/cart/items/*', (route) => json(route, {
    tripId: 'trip-e2e', state: 'open', savedExpiresAt: new Date(Date.now() + 60 * 60_000).toISOString(),
    items: [], checkedAt: new Date().toISOString(),
  }))
  await page.route('**/api/trip/trip-e2e/cart', (route) => json(route, {
    tripId: 'trip-e2e', state: 'open', savedExpiresAt: new Date(Date.now() + 60 * 60_000).toISOString(),
    items: [], checkedAt: new Date().toISOString(),
  }))
  await page.route('**/api/trip/trip-e2e/post-trip-feedback', (route) => { calls.postTrip += 1; return json(route, { postTrip: { eligible: true, rating: 5, adjustments: [{ key: 'food', before: .35, after: .38, delta: .03 }] }, profile: { ...profile, version: 2 } }) })
  if (trip) await page.route('**/api/trip/trip-e2e', (route) => json(route, { trip_id: 'trip-e2e', preferences, research_results: researchResults(), selections: [], ...(final ? { itinerary } : {}), postTrip: { eligible } }))
  return calls
}

/** Post-auth bootstrap mocks for tests that start signed out and land in the planner. */
async function postAuthRoutes(page: Page, user = me) {
  await page.route('**/api/auth/me', (route) => json(route, user))
  await page.route('**/api/profile', (route) => json(route, { sketch: profile.summary, cotravellers: [] }))
  await page.route('**/api/profile/character', (route) => json(route, profile))
  await page.route('**/api/trips/pending-check-in', (route) => json(route, { trip: null }))
  await page.route('**/api/health/agents', (route) => json(route, healthBody('ok')))
}

test('landing is polished and free of console errors', async ({ page }) => {
  const errors: string[] = []; page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto('/'); await expect(page.getByRole('heading', { name: /Trips that feel/ })).toBeVisible()
  await page.screenshot({ path: `${shots}/01-landing-desktop.png`, fullPage: true })
  expect(errors).toEqual([])
})

test('creating an account claims a username and sends the full identity payload', async ({ page }) => {
  const lookups: string[] = []
  let registered: { email?: string; password?: string; username?: string; name?: string; phone?: string } | null = null
  await page.route('**/api/users/lookup*', (route) => {
    lookups.push(new URL(route.request().url()).searchParams.get('username') ?? '')
    return json(route, { detail: 'No CheckIn user with that username.' }, 404)
  })
  await page.route('**/api/auth/register', (route) => { registered = route.request().postDataJSON() as typeof registered; return json(route, { token: 'fresh-token' }) })
  await postAuthRoutes(page, { ...me, name: 'Vedant Sharma', phone: '+91 98765 43210' })

  await page.goto('/'); await expect(page.getByRole('heading', { name: 'Create your compass' })).toBeVisible()
  await page.getByLabel('Full name').fill('Vedant Sharma')
  await page.getByLabel('Username').fill('vedant')
  await page.getByLabel('Email').fill('vedant@example.com') // moving focus blurs the username, triggering the availability check
  await expect(page.getByText('Available')).toBeVisible()
  await page.getByLabel('Phone').fill('+91 98765 43210')
  await page.getByLabel('Password').fill('wander-more-88')
  await page.screenshot({ path: `${shots}/13-create-account.png`, fullPage: true })
  await page.getByRole('button', { name: /Meet Tavi/ }).click()
  await expect(page.getByRole('heading', { name: /point the compass/ })).toBeVisible()
  expect(lookups).toContain('vedant')
  expect(registered).toMatchObject({ email: 'vedant@example.com', password: 'wander-more-88', username: 'vedant', name: 'Vedant Sharma', phone: '+91 98765 43210' })
})

test('signing in accepts a username as the identifier', async ({ page }) => {
  let credentials: { identifier?: string; password?: string } | null = null
  await page.route('**/api/auth/login', (route) => { credentials = route.request().postDataJSON() as typeof credentials; return json(route, { token: 'returning-token' }) })
  await postAuthRoutes(page, { ...me, email: 'priya@example.com', username: 'priya_k', name: 'Priya Kapoor' })

  await page.goto('/'); await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByLabel('Email or username').fill('priya_k')
  await page.getByLabel('Password').fill('mapped-journeys-7')
  await page.getByRole('button', { name: /Open my workspace/ }).click()
  await expect(page.getByRole('heading', { name: /point the compass/ })).toBeVisible()
  expect(credentials).toEqual({ identifier: 'priya_k', password: 'mapped-journeys-7' })
})

test('first-time onboarding completes and persists the generated reveal', async ({ page }) => {
  let turns = 0
  const answers: Record<string, unknown> = {}
  await page.addInitScript(() => localStorage.setItem('travelbuddy.session', 'onboarding-token'))
  await page.route('**/api/auth/me', (route) => json(route, { email: 'new@example.com', username: 'newbie', name: 'New Explorer', phone: null, intake_complete: false, cotravellers: [] }))
  await page.route('**/api/profile/intake', (route) => json(route, { questionnaireVersion: 'personalisation-v1', status: 'in_progress', currentIndex: turns, total: 9, answers, currentQuestion: questionnaire[turns] }))
  await page.route('**/api/profile/intake/answers/*', async (route) => {
    const id = route.request().url().split('/').pop()!
    answers[id] = (route.request().postDataJSON() as { value: unknown }).value
    turns += 1
    return json(route, { questionnaireVersion: 'personalisation-v1', status: turns === 9 ? 'ready_to_complete' : 'in_progress', currentIndex: turns, total: 9, answers, currentQuestion: questionnaire[turns] ?? null })
  })
  await page.route('**/api/profile/intake/complete', (route) => json(route, profile))
  await page.route('**/api/profile/character', (route) => json(route, profile))
  await page.goto('/'); await expect(page.getByRole('heading', { name: /quick quiz/ })).toBeVisible()
  await page.screenshot({ path: `${shots}/02-onboarding-desktop.png`, fullPage: true })
  const next = page.getByRole('button', { name: /Next question/i })
  await next.click() // spontaneity slider keeps its default
  for (const vibe of ['adventure', 'culture', 'food']) await page.getByRole('button', { name: vibe, exact: true }).click()
  await next.click()
  await page.getByLabel(/splurge on/).selectOption('food'); await page.getByLabel(/save on/).selectOption('transport'); await next.click()
  await page.getByRole('radio', { name: '8 AM' }).click(); await next.click()
  await page.getByRole('radio', { name: 'Foodie Explorer' }).click(); await next.click()
  await page.getByRole('radio', { name: 'Solo' }).click(); await next.click()
  await next.click() // food_adventurousness slider keeps its default
  await page.getByRole('button', { name: 'Early flights' }).click(); await next.click()
  await page.getByRole('textbox').fill('A dawn walk followed by an unforgettable local breakfast.')
  await page.getByRole('button', { name: /Map my character/i }).click()
  await expect(page.getByRole('heading', { name: /I think I’ve got you/ })).toBeVisible()
  expect(turns).toBe(9)
  await page.screenshot({ path: `${shots}/03-profile-reveal.png`, fullPage: true })
})

test('trip creation workspace is responsive and profile editing is wired', async ({ page }) => {
  const calls = await session(page)
  await page.goto('/'); await expect(page.getByRole('heading', { name: /point the compass/ })).toBeVisible()
  await page.screenshot({ path: `${shots}/04-trip-creation-desktop.png`, fullPage: true })
  await page.getByRole('button', { name: /Character profile/i }).click(); await expect(page.getByRole('dialog')).toBeVisible()
  await page.screenshot({ path: `${shots}/05-character-profile.png`, fullPage: true })
  await page.getByLabel(/Character sketch/).fill('A revised travel character with more spontaneous local discoveries and slower mornings.')
  await page.getByRole('button', { name: /Save profile/ }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible(); expect(calls.profilePuts).toBe(1)
  await page.getByRole('button', { name: /Character profile/i }).click(); await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape'); await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.getByRole('button', { name: 'Minimize Tavi' }).click(); await expect(page.locator('.app-shell')).toHaveClass(/app-shell--tavi-hidden/)
  await page.getByRole('button', { name: /Character profile/i }).click(); await page.getByRole('button', { name: /Retake questionnaire/ }).click()
  await expect(page.getByRole('heading', { name: /quick quiz/ })).toBeVisible(); expect(calls.resets).toBe(1)
})

test('ranked recommendations select and generate a final itinerary', async ({ page }) => {
  const calls = await session(page, true)
  await page.route('**/api/trip/trip-e2e/select', (route) => json(route, { status: 'selections_saved', count: 1 }))
  await page.route('**/api/trip/trip-e2e/itinerary', (route) => sse(route, [{ event: 'itinerary_complete', itinerary }]))
  await page.goto('/'); await expect(page.getByRole('heading', { name: 'The shortlist' })).toBeVisible()
  await page.screenshot({ path: `${shots}/06-ranked-recommendations.png`, fullPage: true })
  await page.getByRole('button', { name: 'Switch to dark theme' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.screenshot({ path: `${shots}/12-workspace-dark.png`, fullPage: true })
  await page.getByRole('button', { name: 'Switch to light theme' }).click()
  await page.getByRole('button', { name: /^Not my thing:/ }).first().click()
  await expect(page.getByText('Noted — fewer like this')).toBeVisible(); expect(calls.feedback).toBe(1)
  await page.getByRole('button', { name: /^Choose this$/ }).first().click(); await expect(page.getByText('1 selected')).toBeVisible()
  await page.getByRole('button', { name: /Build my itinerary/ }).click(); await expect(page.getByRole('heading', { name: itinerary.trip_title })).toBeVisible()
  await page.screenshot({ path: `${shots}/07-final-itinerary.png`, fullPage: true })
})

test('partial retry keeps successful cards and merges the missing category', async ({ page }) => {
  await session(page, true)
  let refreshed = false
  let releaseResearch!: () => void
  const researchGate = new Promise<void>((resolve) => { releaseResearch = resolve })
  const partial = recommendations.filter((item) => item.category !== 'restaurant')
  const state = () => ({
    trip_id: 'trip-e2e', preferences,
    research_results: researchResults(refreshed ? recommendations : partial).filter((result) => result.recommendations.length > 0),
    research_errors: refreshed ? [] : ['Restaurant Agent could not finish this search. You can retry safely.'],
    selections: [],
  })
  await page.route('**/api/trip/trip-e2e/research', async (route) => {
    await researchGate
    refreshed = true
    return sse(route, [
      { event: 'research_started', resumed: true, agents: ['Restaurant Agent'] },
      { event: 'agent_started', agent: 'Restaurant Agent' },
      { event: 'agent_completed', agent: 'Restaurant Agent', results: recommendations.filter((item) => item.category === 'restaurant') },
      { event: 'all_complete', completed: 1, failed: 0, status: 'complete' },
    ])
  })
  await page.route('**/api/trip/trip-e2e', (route) => json(route, state()))

  await page.goto('/')
  await expect(page.getByText('Shinkansen + Local Rail')).toBeVisible()
  await page.getByRole('button', { name: 'Retry missing categories' }).click()
  await expect(page.getByText('Research in motion')).toBeVisible()
  await expect(page.getByText('Shinkansen + Local Rail')).toBeVisible()

  releaseResearch()
  await page.getByRole('tab', { name: /Food/ }).click()
  await expect(page.getByText('Monk Kyoto')).toBeVisible()
  await expect(page.getByText('Recommendations ready')).toBeVisible()
})

test('tablet and mobile layouts remain usable', async ({ page }) => {
  await session(page, true)
  await page.setViewportSize({ width: 820, height: 1180 }); await page.goto('/'); await expect(page.getByText('The shortlist')).toBeVisible(); await page.screenshot({ path: `${shots}/08-workspace-tablet.png`, fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 }); await page.reload(); await expect(page.getByText('The shortlist')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: `${shots}/09-workspace-mobile.png`, fullPage: true })
})

test('returning user opens the saved final itinerary', async ({ page }) => {
  await session(page, true, true)
  await page.goto('/'); await expect(page.getByRole('heading', { name: itinerary.trip_title })).toBeVisible()
  await expect(page.getByText(/Built around your character profile/)).toBeVisible()
})

test('an eligible completed trip accepts one post-trip rating', async ({ page }) => {
  const calls = await session(page, true, true, true)
  await page.goto('/'); await expect(page.getByRole('heading', { name: /How did this trip feel/ })).toBeVisible()
  await page.screenshot({ path: `${shots}/10-post-trip-checkin.png`, fullPage: true })
  await page.getByRole('radio', { name: '5 out of 5' }).check()
  await page.getByRole('button', { name: /Save my rating/ }).click()
  await expect(page.getByText(/profile learned from this trip/i)).toBeVisible()
  expect(calls.postTrip).toBe(1)
})

test('planner surfaces the newest pending trip without adding trip-history clutter', async ({ page }) => {
  await session(page)
  await page.route('**/api/trips/pending-check-in', (route) => json(route, { trip: { trip_id: 'trip-e2e', destination: 'Kyoto, Japan', end_date: '2026-10-18', trip_title: itinerary.trip_title } }))
  await page.route('**/api/trip/trip-e2e', (route) => json(route, { trip_id: 'trip-e2e', preferences, itinerary, research_results: [], selections: [], postTrip: { eligible: true } }))

  await page.goto('/'); await expect(page.getByText('How was Kyoto, Japan?')).toBeVisible()
  await page.getByRole('button', { name: /Rate this trip/i }).click()
  await expect(page.getByRole('heading', { name: itinerary.trip_title })).toBeVisible()
})

test('a couple trip gates research on the companion taste intake and submits cotravellers', async ({ page }) => {
  await session(page)
  const chat = { answered: 0, names: [] as string[] }
  await page.route('**/api/profile/chat', (route) => {
    const body = route.request().postDataJSON() as { message: string; cotraveller_name?: string }
    if (body.cotraveller_name) chat.names.push(body.cotraveller_name)
    if (body.message.trim()) chat.answered += 1
    const done = chat.answered >= 4
    return json(route, { reply: done ? 'That completes Priya’s sketch — rankings will balance you both.' : `Question ${chat.answered + 1}: how does Priya like to travel?`, done })
  })
  let submitted: { cotravellers?: string[]; cotraveller_usernames?: string[] } | null = null
  await page.route('**/api/trip/preferences', (route) => { submitted = route.request().postDataJSON() as typeof submitted; return json(route, { trip_id: 'trip-cotrav' }) })
  await page.route('**/api/trip/trip-cotrav/research', (route) => sse(route, [
    { event: 'research_started', agents: Object.values(agentNames) },
    ...categories.map((category) => ({ event: 'agent_completed', agent: agentNames[category], results: recommendations.filter((item) => item.category === category) })),
    { event: 'all_complete', completed: 4, failed: 0, status: 'complete' },
  ]))
  await page.route('**/api/trip/trip-cotrav/cart', (route) => json(route, { tripId: 'trip-cotrav', state: 'open', items: [], checkedAt: new Date().toISOString() }))
  await page.route('**/api/trip/trip-cotrav', (route) => json(route, { trip_id: 'trip-cotrav', preferences: { ...preferences, cotravellers: ['Priya'] }, research_results: researchResults(), selections: [] }))

  await page.goto('/'); await expect(page.getByRole('heading', { name: /point the compass/ })).toBeVisible()
  await page.getByLabel('Destination').fill('Kyoto, Japan')
  await page.getByLabel('Starting from').fill('Mumbai, India')
  await page.getByRole('button', { name: /Guest companion \(no account\)/ }).click()
  await page.getByLabel('Guest name').fill('Priya')
  await page.getByRole('button', { name: 'Add guest' }).click()

  await expect(page.getByText('Guest · needs taste intake')).toBeVisible()
  const submit = page.getByRole('button', { name: /Research my trip/ })
  await expect(submit).toBeDisabled()
  await expect(page.getByText(/Waiting on taste profiles: Priya \(guest — needs their taste intake\)/)).toBeVisible()

  await page.getByRole('button', { name: 'Profile now' }).click()
  const dialog = page.getByRole('dialog', { name: 'Taste intake · Priya' })
  await expect(dialog).toBeVisible()
  for (let turn = 1; turn <= 4; turn += 1) {
    await expect(dialog.getByText(`Question ${turn}:`, { exact: false })).toBeVisible()
    await dialog.getByLabel('Answer as Priya').fill(`Answer ${turn} about how Priya travels.`)
    await dialog.getByRole('button', { name: 'Send' }).click()
  }
  await expect(dialog.getByText('Priya is on the map')).toBeVisible()
  await dialog.getByRole('button', { name: 'Back to the trip' }).click()

  await expect(page.getByText('Guest · profiled')).toBeVisible()
  await expect(submit).toBeEnabled()
  await submit.click()
  await expect(page.getByRole('heading', { name: 'The shortlist' })).toBeVisible()
  expect(submitted?.cotravellers).toEqual(['Priya'])
  expect(submitted?.cotraveller_usernames).toEqual([])
  expect(chat.names).toContain('Priya')
  await expect(page.getByText('Balanced across 2 travellers').first()).toBeVisible()
})

test('a couple trip links members by username, blocks on unprofiled accounts, and submits their handles', async ({ page }) => {
  await session(page)
  await page.route('**/api/users/lookup*', (route) => {
    const username = new URL(route.request().url()).searchParams.get('username')?.toLowerCase() ?? ''
    if (username === 'priya_k') return json(route, { username: 'priya_k', name: 'Priya Kapoor', intake_complete: true })
    if (username === 'sam') return json(route, { username: 'sam', name: 'Sam Verma', intake_complete: false })
    return json(route, { detail: 'No CheckIn user with that username.' }, 404)
  })
  let submitted: { cotravellers?: string[]; cotraveller_usernames?: string[] } | null = null
  await page.route('**/api/trip/preferences', (route) => { submitted = route.request().postDataJSON() as typeof submitted; return json(route, { trip_id: 'trip-linked' }) })
  await page.route('**/api/trip/trip-linked/research', (route) => sse(route, [
    { event: 'research_started', agents: Object.values(agentNames) },
    ...categories.map((category) => ({ event: 'agent_completed', agent: agentNames[category], results: recommendations.filter((item) => item.category === category) })),
    { event: 'all_complete', completed: 4, failed: 0, status: 'complete' },
  ]))
  await page.route('**/api/trip/trip-linked/cart', (route) => json(route, { tripId: 'trip-linked', state: 'open', items: [], checkedAt: new Date().toISOString() }))
  await page.route('**/api/trip/trip-linked', (route) => json(route, { trip_id: 'trip-linked', preferences: { ...preferences, cotraveller_usernames: ['priya_k'] }, research_results: researchResults(), selections: [] }))

  await page.goto('/'); await expect(page.getByRole('heading', { name: /point the compass/ })).toBeVisible()
  await page.getByLabel('Destination').fill('Kyoto, Japan')
  await page.getByLabel('Starting from').fill('Mumbai, India')
  const usernameField = page.getByLabel('Add by username')
  const add = page.getByRole('button', { name: 'Add', exact: true })
  const submit = page.getByRole('button', { name: /Research my trip/ })

  await usernameField.fill('ghost')
  await add.click()
  await expect(page.getByText('No CheckIn user named @ghost')).toBeVisible()

  await usernameField.fill('sam')
  await add.click()
  await expect(page.getByText('@sam · hasn’t finished their taste profile')).toBeVisible()
  await expect(submit).toBeDisabled()
  await expect(page.getByText(/Waiting on taste profiles: @sam \(hasn’t finished their taste profile\)/)).toBeVisible()
  await page.getByRole('button', { name: 'Remove @sam from this trip' }).click()

  await usernameField.fill('priya_k')
  await add.click()
  await expect(page.getByText('@priya_k · Priya Kapoor')).toBeVisible()
  await expect(submit).toBeEnabled()
  await submit.click()
  await expect(page.getByRole('heading', { name: 'The shortlist' })).toBeVisible()
  expect(submitted?.cotraveller_usernames).toEqual(['priya_k'])
  expect(submitted?.cotravellers).toEqual([])
  await expect(page.getByText('Balanced across 2 travellers').first()).toBeVisible()
})

test('cached research results are labelled honestly and fresh cards stay clean', async ({ page }) => {
  await session(page, true)
  const withCache = recommendations.map((item) => item.id === 'transport-1'
    ? { ...item, metadata: { cached: true, cache_age_seconds: 7200, cache_similarity: 0.96 } }
    : item)
  await page.route('**/api/trip/trip-e2e', (route) => json(route, { trip_id: 'trip-e2e', preferences, research_results: researchResults(withCache), selections: [] }))

  await page.goto('/'); await expect(page.getByRole('heading', { name: 'The shortlist' })).toBeVisible()
  await expect(page.getByText('Cached 2h ago · 96% taste match')).toBeVisible()
  await expect(page.getByText('Some results were served from CheckIn\'s research cache')).toBeVisible()
  const freshCard = page.getByRole('article').filter({ hasText: 'Private Arrival Transfer' }).first()
  await expect(freshCard).toBeVisible()
  await expect(freshCard.getByText(/^Cached /)).toHaveCount(0)
})

test('the theme toggle flips dark mode and persists it across reload', async ({ page }) => {
  await session(page)
  await page.goto('/'); await expect(page.getByRole('heading', { name: /point the compass/ })).toBeVisible()
  await page.getByRole('button', { name: 'Switch to dark theme' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(await page.evaluate(() => localStorage.getItem('checkin.theme'))).toBe('dark')
  await page.screenshot({ path: `${shots}/11-planner-dark.png`, fullPage: true })
  await page.reload(); await expect(page.getByRole('heading', { name: /point the compass/ })).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('button', { name: 'Switch to light theme' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  expect(await page.evaluate(() => localStorage.getItem('checkin.theme'))).toBe('light')
})

test('degraded research health shows the nav indicator and banner until it recovers', async ({ page }) => {
  await session(page)
  let status: 'ok' | 'degraded' = 'degraded'
  await page.route('**/api/health/agents', (route) => json(route, healthBody(status)))

  await page.goto('/'); await expect(page.getByRole('heading', { name: /point the compass/ })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Research status: Research degraded' })).toBeVisible()
  await expect(page.getByText('Trip research is running degraded')).toBeVisible()

  status = 'ok'
  await page.getByRole('button', { name: 'Re-check' }).click()
  await expect(page.getByText('Trip research is running degraded')).not.toBeVisible()
  await expect(page.getByRole('button', { name: 'Research status: Research normal' })).toBeVisible()
})
