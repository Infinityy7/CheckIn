import { expect, test, type Page, type Route } from '@playwright/test'
import path from 'node:path'

const shots = path.resolve(process.cwd(), '../screenshots')
const profile = {
  id: 'character:e2e', version: 1,
  summary: 'Vedant is an energetic, moderately budget-conscious traveler who prioritizes memorable experiences over luxury. He follows local food, dramatic landscapes, unusual activities, and flexible schedules, preferring a few high-impact places over a rushed checklist.',
  traits: { pace: 'balanced', budgetStyle: 'balanced', adventureLevel: .78, socialPreference: .58, comfortPreference: .55, spontaneity: .7, localVsTourist: .82, foodAdventurousness: .86, nightlifeInterest: .42, natureVsUrban: .62 },
  rawAnswers: ['Balanced and flexible'], createdAt: '2026-07-14T00:00:00Z', updatedAt: '2026-07-14T00:00:00Z',
}

const categories = ['hotel', 'activity', 'restaurant', 'transport'] as const
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

const preferences = { destination: 'Kyoto, Japan', origin: 'Mumbai, India', start_date: '2026-10-12', end_date: '2026-10-18', budget_amount: 3200, currency: 'USD', vibes: ['culture', 'food', 'nature'], group_type: 'couple', num_travelers: 2, cotravellers: [] }
const itinerary = {
  trip_title: 'Kyoto Between Lanterns & Cedar',
  trip_summary: 'A considered six-day route through temple mornings, local kitchens, and quieter edges of Kyoto. The pace leaves room for weather, wandering, and the discoveries that never appear on a checklist.',
  days: [1, 2, 3].map((day) => ({ day_number: day, date: `2026-10-${11 + day}`, theme: ['Gion at First Light', 'Tea Country & River Paths', 'Cedar, Craft & Quiet Temples'][day - 1], items: [
    { time_slot: '8:00 AM – 10:00 AM', title: ['Fushimi Before the Crowds', 'Local train to Uji', 'Northern Temple Walk'][day - 1], description: 'A deliberately early start with time to notice the details before the main visitor flow arrives.', category: day === 2 ? 'transport' : 'activity', cost_estimate: '$18', location: 'Kyoto', tip: 'Take the side path after the second gate.' },
    { time_slot: '12:00 PM – 1:30 PM', title: ['Seasonal Obanzai Lunch', 'Tea-house Tasting', 'Garden-side Soba'][day - 1], description: 'A compact, local lunch chosen for provenance, atmosphere, and an easy fit with the route.', category: 'restaurant', cost_estimate: '$42', location: 'Kyoto' },
    { time_slot: '3:00 PM – 5:00 PM', title: ['Canal-side Wandering', 'Ceramics Workshop', 'Free Time in Demachiyanagi'][day - 1], description: 'A flexible afternoon anchor with breathing room for shops, coffee, or a longer pause.', category: 'activity', cost_estimate: '$30', location: 'Kyoto' },
  ] })),
}

function json(route: Route, body: unknown, status = 200) { return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }) }
async function session(page: Page, trip = false, final = false) {
  await page.addInitScript(({ trip }) => { localStorage.setItem('travelbuddy.session', 'e2e-token'); if (trip) localStorage.setItem('travelbuddy.lastTrip', 'trip-e2e') }, { trip })
  await page.route('**/api/auth/me', (route) => json(route, { email: 'vedant@example.com', intake_complete: true, cotravellers: [] }))
  await page.route('**/api/profile/character', (route) => route.request().method() === 'PUT' ? json(route, profile) : json(route, profile))
  if (trip) await page.route('**/api/trip/trip-e2e', (route) => json(route, { trip_id: 'trip-e2e', preferences, research_results: categories.map((category) => ({ agent_name: `${category} Agent`, recommendations: recommendations.filter((item) => item.category === category) })), selections: [], ...(final ? { itinerary } : {}) }))
}

test('landing is polished and free of console errors', async ({ page }) => {
  const errors: string[] = []; page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto('/'); await expect(page.getByRole('heading', { name: /Trips that feel/ })).toBeVisible()
  await page.screenshot({ path: `${shots}/01-landing-desktop.png`, fullPage: true })
  expect(errors).toEqual([])
})

test('first-time onboarding completes and persists the generated reveal', async ({ page }) => {
  let turns = 0
  await page.addInitScript(() => localStorage.setItem('travelbuddy.session', 'onboarding-token'))
  await page.route('**/api/auth/me', (route) => json(route, { email: 'new@example.com', intake_complete: false, cotravellers: [] }))
  await page.route('**/api/profile/chat', async (route) => {
    const body = route.request().postDataJSON() as { message: string }
    if (body.message) turns += 1
    return json(route, { reply: turns >= 6 ? "That's everything I needed — I've got a solid picture now." : `Question ${turns + 1}: tell me which option feels most like you?`, done: turns >= 6 })
  })
  await page.route('**/api/profile/character', (route) => json(route, profile))
  await page.goto('/'); await expect(page.getByRole('heading', { name: /quick chat/ })).toBeVisible()
  await page.screenshot({ path: `${shots}/02-onboarding-desktop.png`, fullPage: true })
  for (let i = 0; i < 6; i += 1) { await page.locator('.quick-replies button').first().click(); if (i < 5) await expect(page.getByText(`Question ${i + 2}: tell me which option feels most like you?`)).toBeVisible() }
  await expect(page.getByRole('heading', { name: /I think I’ve got you/ })).toBeVisible()
  await page.screenshot({ path: `${shots}/03-profile-reveal.png`, fullPage: true })
})

test('trip creation workspace is responsive and profile editing is wired', async ({ page }) => {
  await session(page)
  await page.goto('/'); await expect(page.getByRole('heading', { name: /point the compass/ })).toBeVisible()
  await page.screenshot({ path: `${shots}/04-trip-creation-desktop.png`, fullPage: true })
  await page.getByRole('button', { name: /Character profile/i }).click(); await expect(page.getByRole('dialog')).toBeVisible()
  await page.screenshot({ path: `${shots}/05-character-profile.png`, fullPage: true })
})

test('ranked recommendations select and generate a final itinerary', async ({ page }) => {
  await session(page, true)
  await page.route('**/api/trip/trip-e2e/select', (route) => json(route, { status: 'selections_saved', count: 1 }))
  await page.route('**/api/trip/trip-e2e/itinerary', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: `data: ${JSON.stringify({ event: 'itinerary_complete', itinerary })}\n\n` }))
  await page.goto('/'); await expect(page.getByRole('heading', { name: 'The shortlist' })).toBeVisible()
  await page.screenshot({ path: `${shots}/06-ranked-recommendations.png`, fullPage: true })
  await page.getByRole('button', { name: /Choose this/ }).first().click(); await expect(page.getByText('1 selected')).toBeVisible()
  await page.getByRole('button', { name: /Build my itinerary/ }).click(); await expect(page.getByRole('heading', { name: itinerary.trip_title })).toBeVisible()
  await page.screenshot({ path: `${shots}/07-final-itinerary.png`, fullPage: true })
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
